//! Relocate installed catalog models and bundles when the user changes the model folder.

use std::{
    collections::HashSet,
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::atomic::Ordering,
};

use serde::Serialize;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter};
use uuid::Uuid;

use crate::{
    catalog::{safe_model_filename, sha256_file},
    disk_room::format_size,
    error::{AppError, AppResult},
    state::{emit_state_changed, lock, AppState},
};

const COPY_CHUNK_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, Serialize)]
pub struct ModelMoveProgress {
    pub moved_bytes: u64,
    pub total_bytes: u64,
    pub moved_files: usize,
    pub total_files: usize,
    pub progress: f64,
    pub current_file: String,
}

#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct MoveEnv {
    #[cfg(test)]
    pub force_copy: bool,
    #[cfg(test)]
    pub available_space_override: Option<u64>,
    #[cfg(test)]
    pub fail_after_files: Option<usize>,
}

impl MoveEnv {
    fn force_copy_path(self) -> bool {
        #[cfg(test)]
        {
            self.force_copy
        }
        #[cfg(not(test))]
        {
            false
        }
    }

    fn available_space(self, path: &Path) -> AppResult<u64> {
        #[cfg(test)]
        if let Some(bytes) = self.available_space_override {
            return Ok(bytes);
        }
        Ok(fs2::available_space(path)?)
    }

    fn should_fail_after(self, completed_count: usize) -> bool {
        #[cfg(test)]
        if let Some(limit) = self.fail_after_files {
            return completed_count >= limit;
        }
        #[cfg(not(test))]
        let _ = completed_count;
        false
    }
}

#[derive(Clone, Debug)]
struct MoveEntry {
    relative: PathBuf,
    source: PathBuf,
    destination: PathBuf,
    size_bytes: u64,
    expected_sha256: Option<String>,
    bundle_family: Option<String>,
}

struct MovingGuard<'a> {
    state: &'a AppState,
}

impl<'a> MovingGuard<'a> {
    fn acquire(state: &'a AppState) -> AppResult<Self> {
        if state.moving_models.swap(true, Ordering::SeqCst) {
            return Err(AppError::Validation(
                "A model folder move is already in progress.".into(),
            ));
        }
        Ok(Self { state })
    }
}

impl Drop for MovingGuard<'_> {
    fn drop(&mut self) {
        self.state.moving_models.store(false, Ordering::SeqCst);
    }
}

/// Change the active model folder (or reset to default when `target` is `None`).
pub async fn change_model_directory<R: tauri::Runtime>(
    state: &AppState,
    app: Option<&AppHandle<R>>,
    target: Option<&str>,
) -> AppResult<()> {
    change_model_directory_with_env(state, app, target, MoveEnv::default()).await
}

pub(crate) async fn change_model_directory_with_env<R: tauri::Runtime>(
    state: &AppState,
    app: Option<&AppHandle<R>>,
    target: Option<&str>,
    env: MoveEnv,
) -> AppResult<()> {
    crate::updates::ensure_not_installing(state)?;
    ensure_idle_for_move(state)?;
    let _guard = MovingGuard::acquire(state)?;
    // Re-check after acquiring the move flag so no job or download slipped in.
    ensure_idle_for_move(state)?;

    let (setting_value, dest_root) = resolve_destination(state, target)?;
    let old_root = state.model_root();

    if old_root.is_dir() && same_canonical_path(&old_root, &dest_root) {
        apply_setting_and_refresh(state, setting_value, &[])?;
        if old_root != dest_root {
            crate::worker::restart_for_model_directory(state);
        }
        if let Some(handle) = app {
            emit_state_changed(handle);
        }
        return Ok(());
    }

    if !old_root.is_dir() {
        apply_setting_and_refresh(state, setting_value, &[])?;
        crate::worker::restart_for_model_directory(state);
        if let Some(handle) = app {
            emit_state_changed(handle);
        }
        return Ok(());
    }

    let entries = collect_entries(state, &old_root, &dest_root)?;
    if entries.is_empty() {
        apply_setting_and_refresh(state, setting_value, &[])?;
        crate::worker::restart_for_model_directory(state);
        if let Some(handle) = app {
            emit_state_changed(handle);
        }
        return Ok(());
    }

    let mut mappings: Vec<_> = entries
        .iter()
        .map(|entry| {
            (
                entry.source.clone(),
                fs::canonicalize(&entry.source).ok(),
                entry.destination.clone(),
            )
        })
        .collect();
    let references: Vec<String> = {
        let settings = lock(&state.settings)?;
        let recipes = lock(&state.recipes)?;
        std::iter::once(settings.custom_model_path.clone())
            .chain(
                settings
                    .custom_models
                    .iter()
                    .map(|model| model.path.clone()),
            )
            .chain(
                settings
                    .custom_edit_model
                    .non_empty_paths()
                    .map(str::to_owned),
            )
            .chain(
                recipes
                    .iter()
                    .map(|recipe| recipe.custom_model_path.clone()),
            )
            .collect()
    };
    for reference in references {
        let Ok(resolved) = fs::canonicalize(&reference) else {
            continue;
        };
        if let Some((_, _, destination)) = mappings
            .iter()
            .find(|(_, canonical, _)| canonical.as_ref() == Some(&resolved))
        {
            mappings.push((PathBuf::from(reference), None, destination.clone()));
        }
    }
    let total_bytes: u64 = entries.iter().map(|entry| entry.size_bytes).sum();
    let total_files = entries.len();
    let use_copy = env.force_copy_path() || !same_volume(&old_root, &dest_root);

    let previous_status = save_runtime_status(state);
    set_runtime_move_status(
        state,
        format!("Preparing to move {total_files} model file(s)…"),
        0.0,
    );
    if let Some(handle) = app {
        emit_state_changed(handle);
    }

    let app_for_task = app.cloned();
    let dest_root_for_task = dest_root.clone();
    let entries_for_task = entries.clone();
    let move_outcome = tokio::task::spawn_blocking(move || {
        execute_move(
            &dest_root_for_task,
            &entries_for_task,
            use_copy,
            total_bytes,
            env,
            |progress| {
                if let Some(handle) = app_for_task.as_ref() {
                    let _ = handle.emit("model-move-progress", progress);
                }
            },
        )
    })
    .await
    .map_err(|error| AppError::Config(format!("model move task failed: {error}")))?;

    let execution = match move_outcome {
        Ok(execution) => execution,
        Err(error) => {
            restore_runtime_status(state, previous_status);
            if let Some(handle) = app {
                emit_state_changed(handle);
            }
            return Err(error);
        }
    };

    if let Err(error) = apply_setting_and_refresh(state, setting_value, &mappings) {
        // Persist failed before old files were removed (copy path) or while they can
        // still be put back (rename path).
        execution.rollback(&old_root);
        restore_runtime_status(state, previous_status);
        if let Some(handle) = app {
            emit_state_changed(handle);
        }
        return Err(error);
    }

    execution.finalize_old_files(&old_root);
    restore_runtime_status(state, previous_status);
    crate::worker::restart_for_model_directory(state);
    if let Some(handle) = app {
        emit_state_changed(handle);
    }
    Ok(())
}

fn ensure_idle_for_move(state: &AppState) -> AppResult<()> {
    if !lock(&state.runtime)?.active_job_id.is_empty() {
        return Err(AppError::Validation(
            "Finish or cancel the current job before changing the model folder.".into(),
        ));
    }
    if lock(&state.database)?.has_inflight_jobs()? {
        return Err(AppError::Validation(
            "Finish or cancel queued jobs before changing the model folder.".into(),
        ));
    }
    if !lock(&state.downloads)?.is_empty() {
        return Err(AppError::Validation(
            "Wait for active downloads to finish before changing the model folder.".into(),
        ));
    }
    Ok(())
}

fn resolve_destination(state: &AppState, target: Option<&str>) -> AppResult<(String, PathBuf)> {
    let default_root = &state.paths.model_root;
    match target {
        None => {
            fs::create_dir_all(default_root)?;
            Ok((String::new(), default_root.clone()))
        }
        Some(raw) if raw.trim().is_empty() => {
            fs::create_dir_all(default_root)?;
            Ok((String::new(), default_root.clone()))
        }
        Some(raw) => {
            let requested = Path::new(raw);
            state.grants.check_model_folder(requested)?;
            if !requested.is_dir() {
                return Err(AppError::Validation(
                    "The chosen model folder is unavailable.".into(),
                ));
            }
            fs::create_dir_all(default_root).ok();
            if same_canonical_path(requested, default_root) {
                Ok((String::new(), default_root.clone()))
            } else {
                Ok((raw.to_owned(), requested.to_path_buf()))
            }
        }
    }
}

fn same_canonical_path(left: &Path, right: &Path) -> bool {
    match (fs::canonicalize(left), fs::canonicalize(right)) {
        (Ok(a), Ok(b)) => a == b,
        _ => left == right,
    }
}

fn collect_entries(
    state: &AppState,
    old_root: &Path,
    dest_root: &Path,
) -> AppResult<Vec<MoveEntry>> {
    let mut seen = HashSet::new();
    let mut entries = Vec::new();

    {
        let catalog = lock(&state.catalog)?;
        for model in &catalog.models {
            let filename = safe_model_filename(&model.filename)?;
            let rel = PathBuf::from(filename);
            if !seen.insert(rel.clone()) {
                continue;
            }
            let source = old_root.join(&rel);
            if source.is_file() {
                let size_bytes = source.metadata()?.len();
                entries.push(MoveEntry {
                    destination: dest_root.join(&rel),
                    relative: rel,
                    source,
                    size_bytes,
                    expected_sha256: (!model.sha256.is_empty()).then(|| model.sha256.clone()),
                    bundle_family: None,
                });
            }
        }

        for bundle in catalog
            .video_models
            .iter()
            .chain(&catalog.interpolation_models)
            .chain(&catalog.audio_models)
        {
            let family = safe_model_filename(&bundle.family)?;
            for file in &bundle.files {
                let filename = safe_model_filename(&file.filename)?;
                let rel = Path::new(family).join(filename);
                if !seen.insert(rel.clone()) {
                    continue;
                }
                let source = old_root.join(&rel);
                if source.is_file() {
                    let size_bytes = source.metadata()?.len();
                    entries.push(MoveEntry {
                        destination: dest_root.join(&rel),
                        relative: rel,
                        source,
                        size_bytes,
                        expected_sha256: (!file.sha256.is_empty()).then(|| file.sha256.clone()),
                        bundle_family: Some(family.to_owned()),
                    });
                }
            }
        }
    }

    for bundle in crate::editing::catalog_in(old_root)? {
        let family = safe_model_filename(&bundle.family)?;
        for file in &bundle.files {
            let filename = safe_model_filename(&file.filename)?;
            let rel = Path::new(family).join(filename);
            if !seen.insert(rel.clone()) {
                continue;
            }
            let source = old_root.join(&rel);
            if source.is_file() {
                let size_bytes = source.metadata()?.len();
                entries.push(MoveEntry {
                    destination: dest_root.join(&rel),
                    relative: rel,
                    source,
                    size_bytes,
                    expected_sha256: (!file.sha256.is_empty()).then(|| file.sha256.clone()),
                    bundle_family: Some(family.to_owned()),
                });
            }
        }
    }

    Ok(entries)
}

enum MoveExecution {
    Renamed {
        moved_pairs: Vec<(PathBuf, PathBuf)>,
        source_files: Vec<PathBuf>,
        created_dirs: Vec<PathBuf>,
        families: HashSet<String>,
    },
    Copied {
        created_files: Vec<PathBuf>,
        created_dirs: Vec<PathBuf>,
        source_files: Vec<PathBuf>,
        families: HashSet<String>,
    },
}

impl MoveExecution {
    fn rollback(self, _old_root: &Path) {
        match self {
            Self::Renamed {
                moved_pairs,
                created_dirs,
                ..
            } => {
                for (old_path, new_path) in moved_pairs.into_iter().rev() {
                    if let Some(parent) = old_path.parent() {
                        let _ = fs::create_dir_all(parent);
                    }
                    let _ = fs::rename(new_path, old_path);
                }
                for dir in created_dirs.into_iter().rev() {
                    let _ = fs::remove_dir(dir);
                }
            }
            Self::Copied {
                created_files,
                created_dirs,
                ..
            } => {
                for file in created_files {
                    let _ = fs::remove_file(file);
                }
                for dir in created_dirs.into_iter().rev() {
                    let _ = fs::remove_dir(dir);
                }
            }
        }
    }

    fn finalize_old_files(self, old_root: &Path) {
        let families = match self {
            Self::Renamed {
                source_files,
                families,
                ..
            }
            | Self::Copied {
                source_files,
                families,
                ..
            } => {
                for source in source_files {
                    let _ = fs::remove_file(source);
                }
                families
            }
        };
        for family in families {
            let _ = fs::remove_dir(old_root.join(family));
        }
    }
}

fn execute_move(
    dest_root: &Path,
    entries: &[MoveEntry],
    use_copy: bool,
    total_bytes: u64,
    env: MoveEnv,
    mut on_progress: impl FnMut(&ModelMoveProgress),
) -> AppResult<MoveExecution> {
    let families: HashSet<String> = entries
        .iter()
        .filter_map(|entry| entry.bundle_family.clone())
        .collect();

    // Moving a relative file symlink can change its target.
    for entry in entries {
        if fs::symlink_metadata(&entry.source)?
            .file_type()
            .is_symlink()
        {
            return Err(AppError::Validation(format!(
                "The model file is a symlink: {}. Replace it with the file before moving.",
                entry.relative.display()
            )));
        }
    }

    // A destination that resolves to a source would disappear during cleanup.
    let sources: HashSet<PathBuf> = entries
        .iter()
        .map(|entry| fs::canonicalize(&entry.source))
        .collect::<Result<_, _>>()?;
    for entry in entries {
        if entry.destination.exists() && sources.contains(&fs::canonicalize(&entry.destination)?) {
            return Err(AppError::Validation(format!(
                "The destination aliases a source file: {}",
                entry.relative.display()
            )));
        }
    }

    // Check existing destinations before changing anything on disk.
    let mut already_present = vec![false; entries.len()];
    let mut copy_bytes_needed = 0_u64;
    for (index, entry) in entries.iter().enumerate() {
        if entry.destination.exists() {
            if destination_matches_source(entry)? {
                already_present[index] = true;
            } else {
                return Err(AppError::Validation(format!(
                    "The destination already has a different file for \"{}\". Remove it manually or choose another folder.",
                    entry.relative.display()
                )));
            }
        } else {
            copy_bytes_needed = copy_bytes_needed.saturating_add(entry.size_bytes);
        }
    }

    if !use_copy {
        match try_rename_all(
            entries,
            &already_present,
            total_bytes,
            env,
            &families,
            &mut on_progress,
        ) {
            Ok(exec) => return Ok(exec),
            Err(RenameOutcome::CrossDevice) => {
                // Fall through to the verified copy path below.
            }
            Err(RenameOutcome::Failed(error)) => return Err(error),
        }
    }

    check_free_space(dest_root, copy_bytes_needed, env)?;
    copy_and_verify_all(
        entries,
        &already_present,
        total_bytes,
        env,
        &families,
        &mut on_progress,
    )
}

enum RenameOutcome {
    CrossDevice,
    Failed(AppError),
}

fn try_rename_all(
    entries: &[MoveEntry],
    already_present: &[bool],
    total_bytes: u64,
    env: MoveEnv,
    families: &HashSet<String>,
    on_progress: &mut impl FnMut(&ModelMoveProgress),
) -> Result<MoveExecution, RenameOutcome> {
    let mut moved_pairs: Vec<(PathBuf, PathBuf)> = Vec::new();
    let mut created_dirs: Vec<PathBuf> = Vec::new();
    let mut moved_bytes = 0_u64;
    let total_files = entries.len();

    let rollback = |pairs: Vec<(PathBuf, PathBuf)>, dirs: Vec<PathBuf>| {
        for (old_path, new_path) in pairs.into_iter().rev() {
            let _ = fs::rename(new_path, old_path);
        }
        for dir in dirs.into_iter().rev() {
            let _ = fs::remove_dir(dir);
        }
    };

    for (index, entry) in entries.iter().enumerate() {
        if already_present[index] {
            moved_bytes = moved_bytes.saturating_add(entry.size_bytes);
            emit_progress(
                on_progress,
                moved_bytes,
                total_bytes,
                index + 1,
                total_files,
                &entry.relative,
            );
            continue;
        }
        if let Some(parent) = entry.destination.parent() {
            if !parent.exists() {
                if let Err(error) = fs::create_dir_all(parent) {
                    rollback(moved_pairs, created_dirs);
                    return Err(RenameOutcome::Failed(error.into()));
                }
                created_dirs.push(parent.to_path_buf());
            }
        }
        match fs::rename(&entry.source, &entry.destination) {
            Ok(()) => {
                moved_pairs.push((entry.source.clone(), entry.destination.clone()));
            }
            Err(error) if is_cross_device_error(&error) => {
                rollback(moved_pairs, created_dirs);
                return Err(RenameOutcome::CrossDevice);
            }
            Err(error) => {
                rollback(moved_pairs, created_dirs);
                return Err(RenameOutcome::Failed(error.into()));
            }
        }
        if env.should_fail_after(index + 1) {
            rollback(moved_pairs, created_dirs);
            return Err(RenameOutcome::Failed(AppError::Io(std::io::Error::other(
                "simulated move failure",
            ))));
        }
        moved_bytes = moved_bytes.saturating_add(entry.size_bytes);
        emit_progress(
            on_progress,
            moved_bytes,
            total_bytes,
            index + 1,
            total_files,
            &entry.relative,
        );
    }

    // For renamed entries, old files that were already present at destination still need cleanup.
    let source_to_remove: Vec<PathBuf> = entries
        .iter()
        .enumerate()
        .filter(|(index, _)| already_present[*index])
        .map(|(_, entry)| entry.source.clone())
        .collect();
    Ok(MoveExecution::Renamed {
        moved_pairs,
        source_files: source_to_remove,
        created_dirs,
        families: families.clone(),
    })
}

fn copy_and_verify_all(
    entries: &[MoveEntry],
    already_present: &[bool],
    total_bytes: u64,
    env: MoveEnv,
    families: &HashSet<String>,
    on_progress: &mut impl FnMut(&ModelMoveProgress),
) -> AppResult<MoveExecution> {
    let mut created_files: Vec<PathBuf> = Vec::new();
    let mut created_dirs: Vec<PathBuf> = Vec::new();
    let mut moved_bytes = 0_u64;
    let total_files = entries.len();

    let cleanup_copied = |files: Vec<PathBuf>, dirs: Vec<PathBuf>, temp: Option<&Path>| {
        if let Some(tmp) = temp {
            let _ = fs::remove_file(tmp);
        }
        for file in files {
            let _ = fs::remove_file(file);
        }
        for dir in dirs.into_iter().rev() {
            let _ = fs::remove_dir(dir);
        }
    };

    for (index, entry) in entries.iter().enumerate() {
        if already_present[index] {
            moved_bytes = moved_bytes.saturating_add(entry.size_bytes);
            emit_progress(
                on_progress,
                moved_bytes,
                total_bytes,
                index + 1,
                total_files,
                &entry.relative,
            );
            continue;
        }
        let parent = entry
            .destination
            .parent()
            .ok_or_else(|| AppError::Validation("destination path has no parent".into()))?;
        if !parent.exists() {
            if let Err(error) = fs::create_dir_all(parent) {
                cleanup_copied(created_files, created_dirs, None);
                return Err(error.into());
            }
            created_dirs.push(parent.to_path_buf());
        }
        let temp = parent.join(format!(".move-{}.tmp", Uuid::new_v4()));
        let expected_hash = match entry.expected_sha256.clone() {
            Some(hash) => hash,
            None => match sha256_file(&entry.source) {
                Ok(hash) => hash,
                Err(error) => {
                    cleanup_copied(created_files, created_dirs, Some(&temp));
                    return Err(error);
                }
            },
        };
        let copy_result = copy_and_hash(
            &entry.source,
            &temp,
            entry.size_bytes,
            &expected_hash,
            moved_bytes,
            total_bytes,
            index,
            total_files,
            &entry.relative,
            on_progress,
        );
        if let Err(error) = copy_result {
            cleanup_copied(created_files, created_dirs, Some(&temp));
            return Err(error);
        }
        if let Err(error) = fs::rename(&temp, &entry.destination) {
            cleanup_copied(created_files, created_dirs, Some(&temp));
            return Err(error.into());
        }
        created_files.push(entry.destination.clone());

        if env.should_fail_after(index + 1) {
            cleanup_copied(created_files, created_dirs, None);
            return Err(AppError::Io(std::io::Error::other(
                "simulated cross-volume failure",
            )));
        }
        moved_bytes = moved_bytes.saturating_add(entry.size_bytes);
        emit_progress(
            on_progress,
            moved_bytes,
            total_bytes,
            index + 1,
            total_files,
            &entry.relative,
        );
    }

    Ok(MoveExecution::Copied {
        created_files,
        created_dirs,
        source_files: entries.iter().map(|entry| entry.source.clone()).collect(),
        families: families.clone(),
    })
}

#[allow(clippy::too_many_arguments)]
fn copy_and_hash(
    source: &Path,
    temp: &Path,
    expected_size: u64,
    expected_sha256: &str,
    completed_before: u64,
    total_bytes: u64,
    file_index: usize,
    total_files: usize,
    relative: &Path,
    on_progress: &mut impl FnMut(&ModelMoveProgress),
) -> AppResult<()> {
    let mut reader = File::open(source)?;
    let mut writer = File::create(temp)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; COPY_CHUNK_BYTES];
    let mut copied = 0_u64;
    let mut last_reported = 0_u64;

    loop {
        let count = reader.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        writer.write_all(&buffer[..count])?;
        hasher.update(&buffer[..count]);
        copied = copied.saturating_add(count as u64);
        if copied.saturating_sub(last_reported) >= 4 * 1024 * 1024 {
            last_reported = copied;
            emit_progress(
                on_progress,
                completed_before.saturating_add(copied),
                total_bytes,
                file_index,
                total_files,
                relative,
            );
        }
    }
    writer.flush()?;
    writer.sync_all()?;

    let actual_size = temp.metadata()?.len();
    if actual_size != expected_size {
        return Err(AppError::Validation(format!(
            "Moved file size mismatch for \"{}\": expected {expected_size} bytes, got {actual_size}.",
            relative.display()
        )));
    }
    let digest = encode_hex(&hasher.finalize());
    if !digest.eq_ignore_ascii_case(expected_sha256) {
        return Err(AppError::Validation(format!(
            "Moved file checksum mismatch for \"{}\". The original folder was kept untouched.",
            relative.display()
        )));
    }
    Ok(())
}

fn encode_hex(bytes: &[u8]) -> String {
    use std::fmt::Write as _;
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        let _ = write!(&mut encoded, "{byte:02x}");
    }
    encoded
}

fn destination_matches_source(entry: &MoveEntry) -> AppResult<bool> {
    let Ok(meta) = entry.destination.metadata() else {
        return Ok(false);
    };
    if !meta.is_file() || meta.len() != entry.size_bytes {
        return Ok(false);
    }
    let dest_hash = sha256_file(&entry.destination)?;
    let expected = sha256_file(&entry.source)?;
    Ok(dest_hash.eq_ignore_ascii_case(&expected))
}

fn check_free_space(dest: &Path, bytes_needed: u64, env: MoveEnv) -> AppResult<()> {
    if bytes_needed == 0 {
        return Ok(());
    }
    let available = env.available_space(dest)?;
    if available < bytes_needed {
        return Err(AppError::Validation(format!(
            "Not enough free space in \"{}\": {} needed, {} available.",
            dest.display(),
            format_size(bytes_needed),
            format_size(available),
        )));
    }
    Ok(())
}

fn emit_progress(
    on_progress: &mut impl FnMut(&ModelMoveProgress),
    moved_bytes: u64,
    total_bytes: u64,
    moved_files: usize,
    total_files: usize,
    relative: &Path,
) {
    let progress = if total_bytes > 0 {
        (moved_bytes as f64 / total_bytes as f64 * 100.0).clamp(0.0, 100.0)
    } else if total_files > 0 {
        (moved_files as f64 / total_files as f64 * 100.0).clamp(0.0, 100.0)
    } else {
        100.0
    };
    on_progress(&ModelMoveProgress {
        moved_bytes,
        total_bytes,
        moved_files,
        total_files,
        progress,
        current_file: relative.to_string_lossy().into_owned(),
    });
}

fn apply_setting_and_refresh(
    state: &AppState,
    new_model_directory: String,
    mappings: &[(PathBuf, Option<PathBuf>, PathBuf)],
) -> AppResult<()> {
    let rewrite = |path: &mut String| {
        if let Some((_, _, destination)) = mappings.iter().find(|(source, canonical, _)| {
            Path::new(path) == source || canonical.as_deref() == Some(Path::new(path))
        }) {
            *path = destination.to_string_lossy().into_owned();
        }
    };
    {
        let _io = lock(&state.settings_io)?;
        let previous_settings = lock(&state.settings)?.clone();
        let previous_recipes = lock(&state.recipes)?.clone();
        {
            let mut settings = lock(&state.settings)?;
            settings.model_directory = new_model_directory.clone();
            rewrite(&mut settings.custom_model_path);
            for model in &mut settings.custom_models {
                rewrite(&mut model.path);
            }
            let edit = &mut settings.custom_edit_model;
            rewrite(&mut edit.diffusion_path);
            rewrite(&mut edit.text_encoder_path);
            rewrite(&mut edit.vision_path);
            rewrite(&mut edit.vae_path);
        }
        for recipe in lock(&state.recipes)?.iter_mut() {
            rewrite(&mut recipe.custom_model_path);
        }
        if let Err(error) = state.write_settings() {
            *lock(&state.settings)? = previous_settings;
            *lock(&state.recipes)? = previous_recipes;
            return Err(error);
        }
    }
    state.grants.reconcile_model_root(&new_model_directory);
    let settings = lock(&state.settings)?;
    let recipes = lock(&state.recipes)?;
    state.grants.reconcile_models(
        &settings.custom_model_path,
        &settings.custom_models,
        &settings.custom_edit_model,
        &recipes,
    );
    drop(recipes);
    drop(settings);
    state.refresh_catalog()?;
    Ok(())
}

fn save_runtime_status(state: &AppState) -> Option<(String, String, f64)> {
    state.runtime.lock().ok().map(|runtime| {
        (
            runtime.status_title.clone(),
            runtime.status_detail.clone(),
            runtime.progress,
        )
    })
}

fn set_runtime_move_status(state: &AppState, detail: String, progress: f64) {
    if let Ok(mut runtime) = state.runtime.lock() {
        runtime.status_title = "Moving models".into();
        runtime.status_detail = detail;
        runtime.progress = progress;
    }
}

fn restore_runtime_status(state: &AppState, previous: Option<(String, String, f64)>) {
    if let Some((title, detail, progress)) = previous {
        if let Ok(mut runtime) = state.runtime.lock() {
            runtime.status_title = title;
            runtime.status_detail = detail;
            runtime.progress = progress;
        }
    }
}

fn is_cross_device_error(error: &std::io::Error) -> bool {
    #[cfg(unix)]
    {
        error.raw_os_error() == Some(18) // EXDEV
    }
    #[cfg(windows)]
    {
        error.raw_os_error() == Some(17) // ERROR_NOT_SAME_DEVICE
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = error;
        false
    }
}

#[cfg(unix)]
fn same_volume(left: &Path, right: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    match (fs::metadata(left), fs::metadata(right)) {
        (Ok(a), Ok(b)) => a.dev() == b.dev(),
        _ => false,
    }
}

#[cfg(windows)]
fn same_volume(left: &Path, right: &Path) -> bool {
    fn volume_path(path: &Path) -> Option<String> {
        use std::os::windows::ffi::OsStrExt;
        #[link(name = "kernel32")]
        extern "system" {
            fn GetVolumePathNameW(
                lpsz_file_name: *const u16,
                lpsz_volume_path_name: *mut u16,
                cch_buffer_length: u32,
            ) -> i32;
        }
        let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut buffer = vec![0_u16; 1024];
        // SAFETY: null-terminated UTF-16 path and bounded output buffer.
        let ok =
            unsafe { GetVolumePathNameW(wide.as_ptr(), buffer.as_mut_ptr(), buffer.len() as u32) };
        if ok == 0 {
            return None;
        }
        let end = buffer.iter().position(|&c| c == 0).unwrap_or(buffer.len());
        Some(String::from_utf16_lossy(&buffer[..end]).to_lowercase())
    }
    match (volume_path(left), volume_path(right)) {
        (Some(a), Some(b)) => a == b,
        _ => false,
    }
}

#[cfg(not(any(unix, windows)))]
fn same_volume(_left: &Path, _right: &Path) -> bool {
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        catalog::bundled_catalog,
        paths::AppPaths,
        types::{CatalogModel, CatalogVideoModel, ModelFile, UiSettings},
    };
    use std::sync::Arc;

    fn sha256_hex(bytes: &[u8]) -> String {
        encode_hex(&Sha256::digest(bytes))
    }

    fn test_state_with_catalog(root: &Path) -> (Arc<AppState>, Vec<u8>, Vec<u8>, Vec<u8>) {
        let paths = AppPaths::under(root);
        fs::create_dir_all(&paths.model_root).unwrap();
        let state = Arc::new(AppState::from_paths(paths).unwrap());

        let img_bytes = b"image-checkpoint-payload".to_vec();
        let bundle_a = b"bundle-component-a".to_vec();
        let bundle_b = b"bundle-component-b".to_vec();

        {
            let mut catalog = state.catalog.lock().unwrap();
            let base_model = bundled_catalog().unwrap().models[0].clone();
            catalog.models = vec![CatalogModel {
                model_id: "test_img".into(),
                name: "Test Image".into(),
                filename: "test_img.pth".into(),
                size_bytes: img_bytes.len() as u64,
                sha256: sha256_hex(&img_bytes),
                ..base_model
            }];
            let base_video = bundled_catalog().unwrap().interpolation_models[0].clone();
            catalog.interpolation_models = vec![CatalogVideoModel {
                model_id: "test_bundle".into(),
                name: "Test Bundle".into(),
                family: "test_family".into(),
                files: vec![
                    ModelFile {
                        role: "weights".into(),
                        filename: "weights.bin".into(),
                        size_bytes: bundle_a.len() as u64,
                        sha256: sha256_hex(&bundle_a),
                        download_url: "https://example.com/a".into(),
                    },
                    ModelFile {
                        role: "config".into(),
                        filename: "config.json".into(),
                        size_bytes: bundle_b.len() as u64,
                        sha256: sha256_hex(&bundle_b),
                        download_url: "https://example.com/b".into(),
                    },
                ],
                ..base_video
            }];
            catalog.video_models.clear();
            catalog.audio_models.clear();
        }

        fs::write(state.paths.model_root.join("test_img.pth"), &img_bytes).unwrap();
        let bundle_dir = state.paths.model_root.join("test_family");
        fs::create_dir_all(&bundle_dir).unwrap();
        fs::write(bundle_dir.join("weights.bin"), &bundle_a).unwrap();
        fs::write(bundle_dir.join("config.json"), &bundle_b).unwrap();
        fs::write(
            state.paths.model_root.join("unrelated-user-file.txt"),
            b"keep me",
        )
        .unwrap();
        state.refresh_catalog().unwrap();

        (state, img_bytes, bundle_a, bundle_b)
    }

    #[cfg(unix)]
    #[test]
    fn move_rejects_destination_symlink_to_source_family() {
        let dir = tempfile::tempdir().unwrap();
        let (state, _, ba, _) = test_state_with_catalog(dir.path());
        let dest = dir.path().join("dest");
        fs::create_dir_all(&dest).unwrap();
        std::os::unix::fs::symlink(
            state.paths.model_root.join("test_family"),
            dest.join("test_family"),
        )
        .unwrap();
        let entries = collect_entries(&state, &state.paths.model_root, &dest).unwrap();
        assert!(execute_move(&dest, &entries, false, 0, MoveEnv::default(), |_| {}).is_err());
        assert_eq!(fs::read(dest.join("test_family/weights.bin")).unwrap(), ba);
    }

    #[cfg(unix)]
    #[test]
    fn move_rejects_relative_source_file_symlink() {
        let dir = tempfile::tempdir().unwrap();
        let (state, _, _, _) = test_state_with_catalog(dir.path());
        let source = state.paths.model_root.join("test_img.pth");
        let shared = state.paths.model_root.parent().unwrap().join("shared");
        fs::create_dir_all(&shared).unwrap();
        fs::rename(&source, shared.join("weights")).unwrap();
        std::os::unix::fs::symlink("../shared/weights", &source).unwrap();
        let dest = state.paths.model_root.join("new");
        fs::create_dir_all(&dest).unwrap();
        let entries = collect_entries(&state, &state.paths.model_root, &dest).unwrap();
        assert!(execute_move(&dest, &entries, false, 0, MoveEnv::default(), |_| {}).is_err());
        assert!(source.is_file());
    }

    #[cfg(unix)]
    #[test]
    fn changing_from_model_alias_to_real_path_restarts_worker() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let state = Arc::new(AppState::from_paths(AppPaths::under(dir.path())).unwrap());
            let real = dir.path().join("real");
            let alias = dir.path().join("alias");
            fs::create_dir_all(&real).unwrap();
            std::os::unix::fs::symlink(&real, &alias).unwrap();
            state.settings.lock().unwrap().model_directory = alias.to_string_lossy().into_owned();
            state.grants.grant_model_folder(&real);
            let (sender, receiver) = std::sync::mpsc::channel();
            *state.worker.sender.lock().unwrap() = Some(sender);
            state.runtime.lock().unwrap().worker = "ready".into();
            change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&real.to_string_lossy()),
                MoveEnv::default(),
            )
            .await
            .unwrap();
            assert!(receiver.try_recv().unwrap().contains("shutdown_request"));
        });
    }

    #[test]
    fn case_variant_model_path_updates_worker_when_filesystem_ignores_case() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let state = Arc::new(AppState::from_paths(AppPaths::under(dir.path())).unwrap());
            let real = dir.path().join("ModelFolder");
            let variant = dir.path().join("modelfolder");
            fs::create_dir_all(&real).unwrap();
            if !variant.is_dir() {
                assert!(!same_canonical_path(&real, &variant));
                return;
            }
            state.settings.lock().unwrap().model_directory = variant.to_string_lossy().into_owned();
            state.grants.grant_model_folder(&real);
            let (sender, receiver) = std::sync::mpsc::channel();
            *state.worker.sender.lock().unwrap() = Some(sender);
            state.runtime.lock().unwrap().worker = "ready".into();
            change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&real.to_string_lossy()),
                MoveEnv::default(),
            )
            .await
            .unwrap();
            assert!(receiver.try_recv().unwrap().contains("shutdown_request"));
        });
    }

    #[test]
    fn move_rejects_catalog_destination_when_source_differs() {
        let dir = tempfile::tempdir().unwrap();
        let (state, img, _, _) = test_state_with_catalog(dir.path());
        let dest = dir.path().join("dest");
        fs::create_dir_all(&dest).unwrap();
        fs::write(dest.join("test_img.pth"), &img).unwrap();
        let modified = vec![b'x'; img.len()];
        fs::write(state.paths.model_root.join("test_img.pth"), &modified).unwrap();
        let entries = collect_entries(&state, &state.paths.model_root, &dest).unwrap();
        assert!(execute_move(&dest, &entries, false, 0, MoveEnv::default(), |_| {}).is_err());
        assert_eq!(
            fs::read(state.paths.model_root.join("test_img.pth")).unwrap(),
            modified
        );
    }

    #[test]
    fn rename_rollback_preserves_duplicate_source() {
        let dir = tempfile::tempdir().unwrap();
        let (state, img, _, _) = test_state_with_catalog(dir.path());
        let dest = dir.path().join("dest");
        fs::create_dir_all(&dest).unwrap();
        fs::write(dest.join("test_img.pth"), &img).unwrap();
        let entries = collect_entries(&state, &state.paths.model_root, &dest).unwrap();
        let execution =
            execute_move(&dest, &entries, false, 0, MoveEnv::default(), |_| {}).unwrap();
        execution.rollback(&state.paths.model_root);
        assert_eq!(
            fs::read(state.paths.model_root.join("test_img.pth")).unwrap(),
            img
        );
    }

    #[test]
    fn empty_or_missing_model_folder_change_restarts_worker() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            for missing in [false, true] {
                let dir = tempfile::tempdir().unwrap();
                let paths = AppPaths::under(dir.path());
                let state = Arc::new(AppState::from_paths(paths).unwrap());
                if missing {
                    state.settings.lock().unwrap().model_directory = dir
                        .path()
                        .join("missing/models")
                        .to_string_lossy()
                        .into_owned();
                } else {
                    fs::create_dir_all(&state.paths.model_root).unwrap();
                }
                let dest = dir.path().join("dest");
                fs::create_dir_all(&dest).unwrap();
                state.grants.grant_model_folder(&dest);
                let (sender, receiver) = std::sync::mpsc::channel();
                *state.worker.sender.lock().unwrap() = Some(sender);
                state.runtime.lock().unwrap().worker = "ready".into();
                change_model_directory_with_env::<tauri::Wry>(
                    &state,
                    None,
                    Some(&dest.to_string_lossy()),
                    MoveEnv::default(),
                )
                .await
                .unwrap();
                assert!(receiver.try_recv().unwrap().contains("shutdown_request"));
            }
        });
    }

    #[test]
    fn move_rewrites_custom_references_to_moved_catalog_files() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let (state, _, _, _) = test_state_with_catalog(dir.path());
            let source = state.paths.model_root.join("test_family/weights.bin");
            let dest = dir.path().join("dest");
            fs::create_dir_all(&dest).unwrap();
            state.grants.grant_model_folder(&dest);
            state
                .settings
                .lock()
                .unwrap()
                .custom_edit_model
                .text_encoder_path = source.to_string_lossy().into_owned();
            state.settings.lock().unwrap().custom_model_path =
                source.to_string_lossy().into_owned();
            let recipe: crate::types::Recipe = serde_json::from_value(serde_json::json!({
                "id":"r", "name":"r", "task":"upscale", "model_id":"m", "output_scale":2,
                "tile_size":256, "halo":16, "precision":"fp32", "safe_memory":false,
                "custom_model_path": source.to_string_lossy()
            }))
            .unwrap();
            state.recipes.lock().unwrap().push(recipe);
            change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&dest.to_string_lossy()),
                MoveEnv::default(),
            )
            .await
            .unwrap();
            let expected = dest
                .join("test_family")
                .join("weights.bin")
                .to_string_lossy()
                .into_owned();
            assert_eq!(
                state
                    .settings
                    .lock()
                    .unwrap()
                    .custom_edit_model
                    .text_encoder_path,
                expected
            );
            assert_eq!(state.settings.lock().unwrap().custom_model_path, expected);
            assert_eq!(state.recipes.lock().unwrap()[0].custom_model_path, expected);
            assert!(state.grants.check_model_file(Path::new(&expected)).is_ok());
            let (saved, recipes) = crate::settings::load(&state.paths);
            assert_eq!(saved.custom_edit_model.text_encoder_path, expected);
            assert_eq!(recipes[0].custom_model_path, expected);
        });
    }

    #[cfg(unix)]
    #[test]
    fn move_rewrites_custom_reference_through_symlink() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let (state, _, _, _) = test_state_with_catalog(dir.path());
            let alias = dir.path().join("alias");
            std::os::unix::fs::symlink(state.paths.model_root.join("test_family"), &alias).unwrap();
            state
                .settings
                .lock()
                .unwrap()
                .custom_edit_model
                .text_encoder_path = alias.join("weights.bin").to_string_lossy().into_owned();
            let dest = dir.path().join("dest");
            fs::create_dir_all(&dest).unwrap();
            state.grants.grant_model_folder(&dest);
            change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&dest.to_string_lossy()),
                MoveEnv::default(),
            )
            .await
            .unwrap();
            assert_eq!(
                state
                    .settings
                    .lock()
                    .unwrap()
                    .custom_edit_model
                    .text_encoder_path,
                dest.join("test_family/weights.bin").to_string_lossy()
            );
        });
    }

    #[test]
    fn failed_settings_save_restores_files_and_custom_references() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let (state, img, _, _) = test_state_with_catalog(dir.path());
            let source = state.paths.model_root.join("test_img.pth");
            let dest = dir.path().join("dest");
            fs::create_dir_all(&dest).unwrap();
            fs::write(dest.join("test_img.pth"), &img).unwrap();
            state.grants.grant_model_folder(&dest);
            state.settings.lock().unwrap().custom_model_path =
                source.to_string_lossy().into_owned();
            fs::write(
                &state.paths.settings,
                r#"{"schema_version":999,"ui":{},"recipes":[]}"#,
            )
            .unwrap();
            assert!(change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&dest.to_string_lossy()),
                MoveEnv::default()
            )
            .await
            .is_err());
            assert_eq!(fs::read(&source).unwrap(), img);
            assert_eq!(
                state.settings.lock().unwrap().custom_model_path,
                source.to_string_lossy()
            );
            assert!(state.settings.lock().unwrap().model_directory.is_empty());
            assert!(state
                .paths
                .model_root
                .join("test_family/weights.bin")
                .is_file());
        });
    }

    #[test]
    fn same_volume_move_relocates_models_and_bundles_and_updates_settings() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let (state, img, ba, bb) = test_state_with_catalog(dir.path());
            let dest = dir.path().join("custom-models");
            fs::create_dir_all(&dest).unwrap();
            state.grants.grant_model_folder(&dest);

            let dest_str = dest.to_string_lossy().into_owned();
            change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&dest_str),
                MoveEnv::default(),
            )
            .await
            .unwrap();

            assert_eq!(state.model_root(), dest);
            assert_eq!(state.settings.lock().unwrap().model_directory, dest_str);
            assert_eq!(fs::read(dest.join("test_img.pth")).unwrap(), img);
            assert_eq!(fs::read(dest.join("test_family/weights.bin")).unwrap(), ba);
            assert_eq!(fs::read(dest.join("test_family/config.json")).unwrap(), bb);
            assert!(!state.paths.model_root.join("test_img.pth").exists());
            assert!(!state.paths.model_root.join("test_family").exists());
            assert_eq!(
                fs::read(state.paths.model_root.join("unrelated-user-file.txt")).unwrap(),
                b"keep me"
            );
            assert!(state.catalog.lock().unwrap().models[0].installed);
            assert!(state.catalog.lock().unwrap().interpolation_models[0].installed);
        });
    }

    #[test]
    fn cross_volume_forced_copy_verifies_sha256_and_deletes_old_files() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let (state, img, ba, bb) = test_state_with_catalog(dir.path());
            let dest = dir.path().join("other-drive-models");
            fs::create_dir_all(&dest).unwrap();
            state.grants.grant_model_folder(&dest);

            let dest_str = dest.to_string_lossy().into_owned();
            let env = MoveEnv {
                force_copy: true,
                available_space_override: Some(10_000_000),
                fail_after_files: None,
            };
            change_model_directory_with_env::<tauri::Wry>(&state, None, Some(&dest_str), env)
                .await
                .unwrap();

            assert_eq!(fs::read(dest.join("test_img.pth")).unwrap(), img);
            assert_eq!(fs::read(dest.join("test_family/weights.bin")).unwrap(), ba);
            assert_eq!(fs::read(dest.join("test_family/config.json")).unwrap(), bb);
            assert!(!state.paths.model_root.join("test_img.pth").exists());
            assert!(!state.paths.model_root.join("test_family").exists());
            assert_eq!(state.settings.lock().unwrap().model_directory, dest_str);
            assert!(state.catalog.lock().unwrap().models[0].installed);
        });
    }

    #[test]
    fn cross_volume_move_refuses_when_free_space_is_insufficient() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let (state, img, _, _) = test_state_with_catalog(dir.path());
            let dest = dir.path().join("small-drive");
            fs::create_dir_all(&dest).unwrap();
            state.grants.grant_model_folder(&dest);

            let env = MoveEnv {
                force_copy: true,
                available_space_override: Some(10),
                fail_after_files: None,
            };
            let err = change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&dest.to_string_lossy()),
                env,
            )
            .await
            .unwrap_err();
            assert!(err.to_string().contains("Not enough free space"));
            assert_eq!(
                fs::read(state.paths.model_root.join("test_img.pth")).unwrap(),
                img
            );
            assert!(!dest.join("test_img.pth").exists());
            assert!(state.settings.lock().unwrap().model_directory.is_empty());
        });
    }

    #[test]
    fn failure_halfway_leaves_old_folder_intact_on_copy_and_rename() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let (state, img, ba, bb) = test_state_with_catalog(dir.path());
            let dest = dir.path().join("dest-fail");
            fs::create_dir_all(&dest).unwrap();
            state.grants.grant_model_folder(&dest);

            // Cross-volume copy failing after the first file.
            let copy_env = MoveEnv {
                force_copy: true,
                available_space_override: Some(10_000_000),
                fail_after_files: Some(1),
            };
            assert!(change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&dest.to_string_lossy()),
                copy_env,
            )
            .await
            .is_err());

            assert_eq!(
                fs::read(state.paths.model_root.join("test_img.pth")).unwrap(),
                img
            );
            assert_eq!(
                fs::read(state.paths.model_root.join("test_family/weights.bin")).unwrap(),
                ba
            );
            assert_eq!(
                fs::read(state.paths.model_root.join("test_family/config.json")).unwrap(),
                bb
            );
            assert!(!dest.join("test_img.pth").exists());
            assert!(!dest.join("test_family").exists());
            assert!(state.settings.lock().unwrap().model_directory.is_empty());

            // Same-volume rename failing after the first file rolls back cleanly.
            let rename_env = MoveEnv {
                force_copy: false,
                available_space_override: None,
                fail_after_files: Some(1),
            };
            assert!(change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&dest.to_string_lossy()),
                rename_env,
            )
            .await
            .is_err());

            assert_eq!(
                fs::read(state.paths.model_root.join("test_img.pth")).unwrap(),
                img
            );
            assert_eq!(
                fs::read(state.paths.model_root.join("test_family/weights.bin")).unwrap(),
                ba
            );
            assert!(!dest.join("test_img.pth").exists());
            assert!(state.settings.lock().unwrap().model_directory.is_empty());
        });
    }

    #[test]
    fn move_refuses_while_job_or_download_is_active() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let (state, _, _, _) = test_state_with_catalog(dir.path());
            let dest = dir.path().join("dest-busy");
            fs::create_dir_all(&dest).unwrap();
            state.grants.grant_model_folder(&dest);

            // Active job refusal.
            state.runtime.lock().unwrap().active_job_id = "job-1".into();
            let err = change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&dest.to_string_lossy()),
                MoveEnv::default(),
            )
            .await
            .unwrap_err();
            assert!(err.to_string().contains("current job"));
            state.runtime.lock().unwrap().active_job_id.clear();

            // Active download refusal.
            state.downloads.lock().unwrap().insert(
                "test_img".into(),
                crate::downloads::DownloadEntry {
                    order: 1,
                    status: crate::downloads::DownloadPhase::Downloading,
                    progress: 50.0,
                    downloaded_bytes: 10,
                    total_bytes: 20,
                    planned_files: Vec::new(),
                    cancellation: Arc::new(std::sync::atomic::AtomicBool::new(false)),
                    cancel_notify: Arc::new(tokio::sync::Notify::new()),
                },
            );
            let err = change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&dest.to_string_lossy()),
                MoveEnv::default(),
            )
            .await
            .unwrap_err();
            assert!(err.to_string().contains("downloads"));
        });
    }

    #[test]
    fn missing_model_folder_keeps_app_working_and_can_reselect_or_reset() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let dir = tempfile::tempdir().unwrap();
            let paths = AppPaths::under(dir.path());
            let missing_drive = dir.path().join("unplugged-drive").join("models");
            let saved = UiSettings {
                model_directory: missing_drive.to_string_lossy().into_owned(),
                ..UiSettings::default()
            };
            crate::settings::save(&paths, &saved, &[]).unwrap();

            let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
            assert!(state.model_root_missing());
            let snapshot = state.snapshot().unwrap();
            assert!(snapshot.model_directory_missing);
            assert_eq!(
                snapshot.default_model_directory,
                paths.model_root.to_string_lossy()
            );
            assert!(snapshot.catalog.models.iter().all(|m| !m.installed));

            // Re-picking an existing folder where models live restores discovery.
            let recovered = dir.path().join("reconnected-drive");
            fs::create_dir_all(&recovered).unwrap();
            let model = bundled_catalog().unwrap().models[0].clone();
            fs::File::create(recovered.join(&model.filename))
                .unwrap()
                .set_len(model.size_bytes)
                .unwrap();
            state.grants.grant_model_folder(&recovered);
            change_model_directory_with_env::<tauri::Wry>(
                &state,
                None,
                Some(&recovered.to_string_lossy()),
                MoveEnv::default(),
            )
            .await
            .unwrap();
            assert!(!state.model_root_missing());
            assert!(state.catalog.lock().unwrap().models[0].installed);

            // Resetting to default moves models back and clears model_directory.
            change_model_directory_with_env::<tauri::Wry>(&state, None, None, MoveEnv::default())
                .await
                .unwrap();
            assert!(state.settings.lock().unwrap().model_directory.is_empty());
            assert!(!state.model_root_missing());
            assert!(paths.model_root.join(&model.filename).is_file());
            assert!(state.catalog.lock().unwrap().models[0].installed);
        });
    }
}
