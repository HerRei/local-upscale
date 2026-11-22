//! Host-owned registry of filesystem paths the user or the host itself authorized.
//!
//! The web view only ever names paths for media, output folders, custom models, or
//! benchmark exports that the host obtained from a native dialog, a drag-and-drop
//! event, a launch / Open With event, the app's own default and profile folders, or
//! previously persisted media and settings.
//!
//! Known limits: a check resolves a path once, and the later read, write or
//! directory creation uses the path again, so another program that swaps a folder for
//! a link in between can still redirect that use. Tauri adds natively dropped files and
//! folders to its asset scope by itself, and removing them from the list does not take
//! that asset access back. Windows has no link-count check, so a result replaced by a
//! hard link to another file on the same volume still passes as that result. Saved
//! settings and recipes keep the path as chosen, so a saved folder or checkpoint that
//! another program later turns into a link grants the link's new target on the next
//! reconcile or restart.
use std::{
    collections::HashSet,
    fs,
    io::ErrorKind,
    path::{Component, Path, PathBuf},
    sync::Mutex,
};

use crate::{
    error::{AppError, AppResult},
    paths::AppPaths,
    types::{CustomEditModel, MediaItem, Recipe, UiSettings},
};

const MEDIA_REFUSAL: &str =
    "LocalSR can only open media files or folders that you chose in its dialog, dropped onto the window, or opened with the app.";
const OUTPUT_REFUSAL: &str =
    "LocalSR can only write to its default output folder or a folder you chose. Choose the output folder again.";
const MODEL_REFUSAL: &str =
    "LocalSR can only use a checkpoint that you chose in its file dialog. Choose the checkpoint again.";
const MODEL_FOLDER_REFUSAL: &str =
    "LocalSR can only store models in its default folder or a folder you chose. Choose the model folder again.";
const EXPORT_REFUSAL: &str =
    "LocalSR can only export to a destination you chose in its save dialog. Choose the destination again.";

#[derive(Default)]
pub struct GrantRegistry {
    inner: Mutex<RegistryState>,
}

#[derive(Default)]
struct RegistryState {
    app_owned_roots: Vec<PathBuf>,
    default_output: Option<PathBuf>,
    default_model_root: Option<PathBuf>,
    media_files: HashSet<PathBuf>,
    media_folders: HashSet<PathBuf>,
    revoked_media: HashSet<PathBuf>,
    output_folders: HashSet<PathBuf>,
    persisted_outputs: HashSet<PathBuf>,
    model_folders: HashSet<PathBuf>,
    persisted_model_folders: HashSet<PathBuf>,
    model_files: HashSet<PathBuf>,
    persisted_models: HashSet<PathBuf>,
    export_files: HashSet<PathBuf>,
}

impl GrantRegistry {
    pub fn new(
        paths: &AppPaths,
        media: &[MediaItem],
        settings: &UiSettings,
        recipes: &[Recipe],
    ) -> Self {
        let mut state = RegistryState::default();
        for root in [&paths.next_root, &paths.model_root, &paths.work_root] {
            if let Ok(resolved) = resolve_path(root) {
                if !state.app_owned_roots.contains(&resolved) {
                    state.app_owned_roots.push(resolved);
                }
            }
        }
        state.default_output = resolve_path(&paths.default_output).ok();
        state.default_model_root = resolve_path(&paths.model_root).ok();
        seed_model_root(&mut state, &settings.model_directory);
        for item in media {
            if let Ok(resolved) = resolve_path(Path::new(&item.path)) {
                state.media_files.insert(resolved);
            }
        }
        seed_outputs(&mut state, &settings.output_directory);
        seed_models(
            &mut state,
            &settings.custom_model_path,
            &settings.custom_edit_model,
            recipes,
        );
        Self {
            inner: Mutex::new(state),
        }
    }

    pub fn grant_media_file(&self, path: &Path) {
        let Ok(resolved) = resolve_path(path) else {
            return;
        };
        if let Ok(mut state) = self.inner.lock() {
            state.revoked_media.remove(&resolved);
            state.media_files.insert(resolved);
        }
    }

    pub fn grant_media_files<P: AsRef<Path>>(&self, paths: &[P]) {
        for path in paths {
            self.grant_media_file(path.as_ref());
        }
    }

    pub fn grant_media_folder(&self, path: &Path) {
        let Ok(resolved) = resolve_path(path) else {
            return;
        };
        if let Ok(mut state) = self.inner.lock() {
            state
                .revoked_media
                .retain(|entry| !entry.starts_with(&resolved));
            state.media_folders.insert(resolved);
        }
    }

    pub fn grant_dropped_media(&self, paths: &[PathBuf]) {
        for path in paths {
            if path.is_dir() {
                self.grant_media_folder(path);
            } else {
                self.grant_media_file(path);
            }
        }
    }

    pub fn grant_launch_files(&self, files: &[String]) {
        for file in files {
            let path = Path::new(file);
            if path.is_dir() {
                self.grant_media_folder(path);
            } else {
                self.grant_media_file(path);
            }
        }
    }

    pub fn revoke_media_file(&self, path: &Path) {
        let Ok(resolved) = resolve_path(path) else {
            return;
        };
        if let Ok(mut state) = self.inner.lock() {
            state.media_files.remove(&resolved);
            if state
                .media_folders
                .iter()
                .any(|folder| resolved.starts_with(folder))
            {
                state.revoked_media.insert(resolved);
            }
        }
    }

    pub fn replace_media_files<P: AsRef<Path>>(&self, paths: &[P]) {
        let resolved: Vec<PathBuf> = paths
            .iter()
            .filter_map(|path| resolve_path(path.as_ref()).ok())
            .collect();
        if let Ok(mut state) = self.inner.lock() {
            state.media_files.clear();
            state.media_folders.clear();
            state.revoked_media.clear();
            for path in resolved {
                state.media_files.insert(path);
            }
        }
    }

    pub fn clear_media(&self) {
        if let Ok(mut state) = self.inner.lock() {
            state.media_files.clear();
            state.media_folders.clear();
            state.revoked_media.clear();
        }
    }

    pub fn grant_output_folder(&self, path: &Path) {
        let Ok(resolved) = resolve_path(path) else {
            return;
        };
        if let Ok(mut state) = self.inner.lock() {
            state.output_folders.insert(resolved);
        }
    }

    /// Grant the results folder a chosen media folder gets, but only while it really
    /// lies inside that folder: a `LocalSR Results` link to elsewhere grants nothing.
    pub fn grant_results_folder(&self, media_folder: &Path, results: &Path) {
        let (Ok(folder), Ok(resolved)) = (resolve_path(media_folder), resolve_path(results)) else {
            return;
        };
        if resolved.starts_with(&folder) && resolved != folder {
            if let Ok(mut state) = self.inner.lock() {
                state.output_folders.insert(resolved);
            }
        }
    }

    pub fn reconcile_outputs(&self, output_directory: &str) {
        if let Ok(mut state) = self.inner.lock() {
            seed_outputs(&mut state, output_directory);
        }
    }

    pub fn grant_model_file(&self, path: &Path) {
        let Ok(resolved) = resolve_path(path) else {
            return;
        };
        if let Ok(mut state) = self.inner.lock() {
            state.model_files.insert(resolved);
        }
    }

    pub fn reconcile_models(
        &self,
        custom_model_path: &str,
        custom_edit_model: &CustomEditModel,
        recipes: &[Recipe],
    ) {
        if let Ok(mut state) = self.inner.lock() {
            seed_models(&mut state, custom_model_path, custom_edit_model, recipes);
        }
    }

    pub fn grant_model_folder(&self, path: &Path) {
        let Ok(resolved) = resolve_path(path) else {
            return;
        };
        if let Ok(mut state) = self.inner.lock() {
            state.model_folders.insert(resolved);
        }
    }

    pub fn check_model_folder(&self, path: &Path) -> AppResult<PathBuf> {
        let resolved = self.resolve_requested(path, MODEL_FOLDER_REFUSAL)?;
        let allowed = self
            .inner
            .lock()
            .map(|state| {
                state.default_model_root.as_ref() == Some(&resolved)
                    || state.model_folders.contains(&resolved)
            })
            .unwrap_or(false);
        if allowed {
            Ok(resolved)
        } else {
            Err(AppError::Validation(MODEL_FOLDER_REFUSAL.into()))
        }
    }

    pub fn reconcile_model_root(&self, model_directory: &str) {
        if let Ok(mut state) = self.inner.lock() {
            seed_model_root(&mut state, model_directory);
        }
    }

    pub fn grant_export_file(&self, path: &Path) {
        let Ok(resolved) = resolve_path(path) else {
            return;
        };
        if let Ok(mut state) = self.inner.lock() {
            state.export_files.insert(resolved);
        }
    }

    pub fn consume_export_file(&self, path: &Path) {
        let Ok(resolved) = resolve_path(path) else {
            return;
        };
        if let Ok(mut state) = self.inner.lock() {
            state.export_files.remove(&resolved);
        }
    }

    pub fn check_media_input(&self, path: &Path) -> AppResult<PathBuf> {
        let resolved = self.resolve_requested(path, MEDIA_REFUSAL)?;
        let allowed = self
            .inner
            .lock()
            .map(|state| {
                !state.revoked_media.contains(&resolved)
                    && (state.media_files.contains(&resolved)
                        || state
                            .media_folders
                            .iter()
                            .any(|folder| resolved.starts_with(folder)))
            })
            .unwrap_or(false);
        if allowed {
            Ok(resolved)
        } else {
            Err(AppError::Validation(MEDIA_REFUSAL.into()))
        }
    }

    pub fn check_media_folder(&self, path: &Path) -> AppResult<PathBuf> {
        let resolved = self.resolve_requested(path, MEDIA_REFUSAL)?;
        let allowed = self
            .inner
            .lock()
            .map(|state| {
                state
                    .media_folders
                    .iter()
                    .any(|folder| resolved.starts_with(folder))
            })
            .unwrap_or(false);
        if allowed {
            Ok(resolved)
        } else {
            Err(AppError::Validation(MEDIA_REFUSAL.into()))
        }
    }

    pub fn check_output_folder(&self, path: &Path) -> AppResult<PathBuf> {
        let resolved = self.resolve_requested(path, OUTPUT_REFUSAL)?;
        let allowed = self
            .inner
            .lock()
            .map(|state| {
                state.default_output.as_ref() == Some(&resolved)
                    || state
                        .output_folders
                        .iter()
                        .any(|folder| resolved.starts_with(folder))
            })
            .unwrap_or(false);
        if allowed {
            Ok(resolved)
        } else {
            Err(AppError::Validation(OUTPUT_REFUSAL.into()))
        }
    }

    pub fn check_model_file(&self, path: &Path) -> AppResult<PathBuf> {
        let resolved = self.resolve_requested(path, MODEL_REFUSAL)?;
        let allowed = self
            .inner
            .lock()
            .map(|state| state.model_files.contains(&resolved))
            .unwrap_or(false);
        if allowed {
            Ok(resolved)
        } else {
            Err(AppError::Validation(MODEL_REFUSAL.into()))
        }
    }

    pub fn check_export_file(&self, path: &Path) -> AppResult<PathBuf> {
        let resolved = self.resolve_requested(path, EXPORT_REFUSAL)?;
        let allowed = self
            .inner
            .lock()
            .map(|state| state.export_files.contains(&resolved))
            .unwrap_or(false);
        if allowed {
            Ok(resolved)
        } else {
            Err(AppError::Validation(EXPORT_REFUSAL.into()))
        }
    }

    /// Resolve a path the web view sent. A network share or device path is refused from
    /// its text alone, before any filesystem call, unless a granted path names that share.
    fn resolve_requested(&self, path: &Path, refusal: &str) -> AppResult<PathBuf> {
        if !self.lexically_allowed(path) {
            return Err(AppError::Validation(refusal.into()));
        }
        resolve_path(path).map_err(|_| AppError::Validation(refusal.into()))
    }

    fn lexically_allowed(&self, path: &Path) -> bool {
        self.inner
            .lock()
            .map(|state| {
                let roots = state
                    .app_owned_roots
                    .iter()
                    .chain(&state.default_output)
                    .chain(&state.media_files)
                    .chain(&state.media_folders)
                    .chain(&state.output_folders)
                    .chain(&state.model_folders)
                    .chain(&state.model_files)
                    .chain(&state.export_files)
                    .map(|root| root.to_string_lossy().into_owned());
                local_or_on_share_of(&path.to_string_lossy(), roots)
            })
            .unwrap_or(false)
    }

    #[cfg(test)]
    pub fn is_app_owned(&self, path: &Path) -> AppResult<bool> {
        let resolved = resolve_path(path)?;
        Ok(self
            .inner
            .lock()
            .map(|state| {
                state.default_output.as_ref() == Some(&resolved)
                    || state
                        .app_owned_roots
                        .iter()
                        .any(|root| resolved.starts_with(root))
            })
            .unwrap_or(false))
    }
}

fn seed_model_root(state: &mut RegistryState, model_directory: &str) {
    let mut next_persisted = HashSet::new();
    if !model_directory.trim().is_empty() {
        if let Ok(resolved) = resolve_path(Path::new(model_directory)) {
            if state.default_model_root.as_ref() != Some(&resolved) {
                next_persisted.insert(resolved);
            }
        }
    }
    for old in &state.persisted_model_folders {
        if !next_persisted.contains(old) {
            state.model_folders.remove(old);
            if !state.app_owned_roots.iter().take(3).any(|core| core == old) {
                state.app_owned_roots.retain(|entry| entry != old);
            }
        }
    }
    for current in &next_persisted {
        state.model_folders.insert(current.clone());
        if !state.app_owned_roots.contains(current) {
            state.app_owned_roots.push(current.clone());
        }
    }
    state.persisted_model_folders = next_persisted;
}

fn seed_outputs(state: &mut RegistryState, output_directory: &str) {
    let mut next_persisted = HashSet::new();
    // The exact string that was checked when it was saved: a trimmed copy could name
    // another folder.
    if !output_directory.is_empty() {
        if let Ok(resolved) = resolve_path(Path::new(output_directory)) {
            if state.default_output.as_ref() != Some(&resolved) {
                next_persisted.insert(resolved);
            }
        }
    }
    for old in &state.persisted_outputs {
        if !next_persisted.contains(old) {
            state.output_folders.remove(old);
        }
    }
    for current in &next_persisted {
        state.output_folders.insert(current.clone());
    }
    state.persisted_outputs = next_persisted;
}

fn seed_models(
    state: &mut RegistryState,
    custom_model_path: &str,
    custom_edit_model: &CustomEditModel,
    recipes: &[Recipe],
) {
    let mut next_persisted = HashSet::new();
    for raw in std::iter::once(custom_model_path)
        .chain(custom_edit_model.non_empty_paths())
        .chain(
            recipes
                .iter()
                .map(|recipe| recipe.custom_model_path.as_str()),
        )
    {
        if !raw.is_empty() {
            if let Ok(resolved) = resolve_path(Path::new(raw)) {
                next_persisted.insert(resolved);
            }
        }
    }
    for old in &state.persisted_models {
        if !next_persisted.contains(old) {
            state.model_files.remove(old);
        }
    }
    for current in &next_persisted {
        state.model_files.insert(current.clone());
    }
    state.persisted_models = next_persisted;
}

#[derive(Debug, PartialEq, Eq)]
enum Remote {
    /// `server\share`, lower-cased, of a UNC path in either spelling.
    Share(String),
    /// A `\\.\` device path or a verbatim path that is not a drive or a share.
    Device,
}

/// True for a path whose text names no network share or device.
pub fn is_local(text: &str) -> bool {
    remote_prefix(text).is_none()
}

/// True for a local path, or one on the same share as one of `roots`; never for a
/// device path. Read from the text alone, so nothing reaches the filesystem.
pub fn local_or_on_share_of<I: IntoIterator<Item = String>>(text: &str, roots: I) -> bool {
    match remote_prefix(text) {
        None => true,
        Some(Remote::Device) => false,
        Some(share) => roots
            .into_iter()
            .any(|root| remote_prefix(&root).as_ref() == Some(&share)),
    }
}

/// What a Windows path names before its first folder, read from the text alone:
/// a network share, a device, or (None) a local or non-Windows path.
fn remote_prefix(text: &str) -> Option<Remote> {
    let text = text.replace('/', "\\");
    let rest = if let Some(rest) = text.strip_prefix(r"\\?\") {
        let bytes = rest.as_bytes();
        if bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' {
            return None;
        }
        match rest.get(..4) {
            Some(unc) if unc.eq_ignore_ascii_case(r"UNC\") => &rest[4..],
            _ => return Some(Remote::Device),
        }
    } else if text.starts_with(r"\\.\") {
        return Some(Remote::Device);
    } else {
        text.strip_prefix(r"\\")?
    };
    let mut parts = rest.split('\\');
    let server = parts.next().unwrap_or_default();
    let share = parts.next().unwrap_or_default();
    if server.is_empty() || share.is_empty() {
        return Some(Remote::Device);
    }
    Some(Remote::Share(format!("{server}\\{share}").to_lowercase()))
}

/// Resolve an absolute path cleanly across existing and not-yet-created suffixes.
/// Symlinks on any existing prefix (including dangling symlinks) are resolved or rejected,
/// and `..` in any non-existent suffix is rejected so no folder grant can be escaped.
pub fn resolve_path(path: &Path) -> AppResult<PathBuf> {
    let text = path.to_string_lossy();
    if text.trim().is_empty() || text.contains('\0') {
        return Err(AppError::Validation("path cannot be empty".into()));
    }
    if !path.is_absolute() {
        return Err(AppError::Validation("path must be absolute".into()));
    }
    if let Ok(canonical) = fs::canonicalize(path) {
        return Ok(canonical);
    }

    for ancestor in path.ancestors() {
        match fs::symlink_metadata(ancestor) {
            Ok(_) => {
                // Canonicalizing the deepest existing ancestor resolves any symlinks
                // (and rejects dangling symlinks, whose target does not exist).
                let mut resolved = fs::canonicalize(ancestor).map_err(|_| {
                    AppError::Validation("path ancestor could not be resolved".into())
                })?;
                let suffix = path
                    .strip_prefix(ancestor)
                    .map_err(|_| AppError::Validation("path could not be normalized".into()))?;
                for component in suffix.components() {
                    match component {
                        Component::Normal(part) => resolved.push(part),
                        Component::CurDir => {}
                        Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                            return Err(AppError::Validation(
                                "path contains invalid traversal components".into(),
                            ));
                        }
                    }
                }
                return Ok(resolved);
            }
            Err(error) if error.kind() == ErrorKind::NotFound => continue,
            Err(_) => {
                return Err(AppError::Validation("path ancestor is unavailable".into()));
            }
        }
    }

    Err(AppError::Validation("path root is unavailable".into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_registry(root: &Path) -> GrantRegistry {
        let paths = AppPaths::under(root);
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::create_dir_all(&paths.model_root).unwrap();
        fs::create_dir_all(&paths.work_root).unwrap();
        fs::create_dir_all(&paths.default_output).unwrap();
        GrantRegistry::new(&paths, &[], &UiSettings::default(), &[])
    }

    #[test]
    fn granted_file_folder_and_child_are_accepted_while_ungranted_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let registry = test_registry(dir.path());
        let file = dir.path().join("photo.png");
        let other = dir.path().join("other.png");
        let folder = dir.path().join("album");
        let nested = folder.join("sub").join("shot.jpg");
        let sibling = dir.path().join("album_evil").join("shot.jpg");
        fs::create_dir_all(nested.parent().unwrap()).unwrap();
        fs::create_dir_all(sibling.parent().unwrap()).unwrap();
        fs::write(&file, b"a").unwrap();
        fs::write(&other, b"b").unwrap();
        fs::write(&nested, b"c").unwrap();
        fs::write(&sibling, b"d").unwrap();

        assert!(registry.check_media_input(&file).is_err());
        registry.grant_media_file(&file);
        assert!(registry.check_media_input(&file).is_ok());
        assert!(registry.check_media_input(&other).is_err());

        registry.grant_media_folder(&folder);
        assert!(registry.check_media_folder(&folder).is_ok());
        assert!(registry.check_media_input(&folder).is_ok());
        assert!(registry.check_media_input(&nested).is_ok());
        // Sibling directory sharing a string prefix must not match.
        assert!(registry.check_media_input(&sibling).is_err());

        // Cross-scope isolation: a granted media file/folder is not an output folder, model, or export.
        assert!(registry.check_output_folder(&folder).is_err());
        assert!(registry.check_model_file(&file).is_err());
        assert!(registry.check_export_file(&file).is_err());
    }

    #[test]
    fn parent_traversal_and_symlink_escapes_are_refused() {
        let dir = tempfile::tempdir().unwrap();
        let registry = test_registry(dir.path());
        let granted = dir.path().join("granted");
        let outside = dir.path().join("outside");
        fs::create_dir_all(granted.join("sub")).unwrap();
        fs::create_dir_all(&outside).unwrap();
        let secret = outside.join("secret.png");
        fs::write(&secret, b"secret").unwrap();

        registry.grant_media_folder(&granted);
        registry.grant_output_folder(&granted);

        // `..` escaping the granted folder (existing and non-existent tails).
        assert!(registry
            .check_media_input(&granted.join("..").join("outside").join("secret.png"))
            .is_err());
        assert!(registry
            .check_output_folder(&granted.join("sub").join("..").join("..").join("outside"))
            .is_err());
        assert!(registry
            .check_output_folder(
                &granted
                    .join("new_dir")
                    .join("..")
                    .join("..")
                    .join("outside")
            )
            .is_err());

        #[cfg(unix)]
        {
            use std::os::unix::fs::symlink;
            let file_link = granted.join("escape.png");
            symlink(&secret, &file_link).unwrap();
            assert!(registry.check_media_input(&file_link).is_err());

            let dir_link = granted.join("escape_dir");
            symlink(&outside, &dir_link).unwrap();
            assert!(registry
                .check_media_input(&dir_link.join("secret.png"))
                .is_err());
            assert!(registry.check_output_folder(&dir_link).is_err());
            assert!(registry
                .check_output_folder(&dir_link.join("new_sub"))
                .is_err());

            let dangling = granted.join("dangling");
            symlink(outside.join("missing"), &dangling).unwrap();
            assert!(registry.check_output_folder(&dangling).is_err());
            assert!(registry
                .check_output_folder(&dangling.join("child"))
                .is_err());
        }
    }

    #[test]
    fn app_owned_and_default_output_paths_are_allowed() {
        let dir = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(dir.path());
        let registry = test_registry(dir.path());

        assert!(registry.is_app_owned(&paths.next_root).unwrap());
        assert!(registry.is_app_owned(&paths.model_root).unwrap());
        assert!(registry
            .is_app_owned(&paths.work_root.join("cache.webm"))
            .unwrap());
        assert!(registry.is_app_owned(&paths.default_output).unwrap());
        assert!(!registry
            .is_app_owned(&dir.path().join("unrelated"))
            .unwrap());

        // Default output folder is allowed as output, but arbitrary subdirectories under it are not.
        assert!(registry.check_output_folder(&paths.default_output).is_ok());
        assert!(registry
            .check_output_folder(&paths.default_output.join("arbitrary_sub"))
            .is_err());
    }

    #[test]
    fn non_existent_child_under_granted_output_folder_is_accepted() {
        let dir = tempfile::tempdir().unwrap();
        let registry = test_registry(dir.path());
        let chosen = dir.path().join("photos");
        fs::create_dir_all(&chosen).unwrap();
        registry.grant_output_folder(&chosen);

        let results = chosen.join("LocalSR Results");
        assert!(!results.exists());
        assert!(registry.check_output_folder(&results).is_ok());
    }

    #[test]
    fn a_results_folder_only_inside_the_media_folder_is_granted() {
        let dir = tempfile::tempdir().unwrap();
        let registry = test_registry(dir.path());
        let photos = dir.path().join("photos");
        fs::create_dir_all(&photos).unwrap();
        let results = photos.join("LocalSR Results");
        registry.grant_results_folder(&photos, &results);
        assert!(registry.check_output_folder(&results).is_ok());
        assert!(registry.check_output_folder(&photos).is_err());

        #[cfg(unix)]
        {
            let album = dir.path().join("album");
            let private = dir.path().join("private");
            fs::create_dir_all(&album).unwrap();
            fs::create_dir_all(&private).unwrap();
            let link = album.join("LocalSR Results");
            std::os::unix::fs::symlink(&private, &link).unwrap();
            registry.grant_results_folder(&album, &link);
            assert!(registry.check_output_folder(&link).is_err());
            assert!(registry.check_output_folder(&private).is_err());
        }
    }

    #[test]
    fn persisted_paths_are_granted_exactly_as_saved() {
        let dir = tempfile::tempdir().unwrap();
        let registry = test_registry(dir.path());
        let export = dir.path().join("export");
        let model = dir.path().join("model.pth");
        fs::create_dir_all(&export).unwrap();
        fs::write(&model, b"m").unwrap();
        let spaced = format!("{} ", export.to_string_lossy());
        registry.reconcile_outputs(&spaced);
        // Windows drops trailing spaces from path names, so there both spellings name the
        // folder that was chosen; elsewhere they are two different folders.
        #[cfg(not(windows))]
        assert!(registry.check_output_folder(&export).is_err());
        assert!(registry.check_output_folder(Path::new(&spaced)).is_ok());
        registry.reconcile_models(
            &format!(" {}", model.to_string_lossy()),
            &CustomEditModel::default(),
            &[],
        );
        assert!(registry.check_model_file(&model).is_err());
    }

    #[test]
    fn share_and_device_paths_are_read_from_their_text() {
        let share = |text: &str| Some(Remote::Share(text.into()));
        assert_eq!(
            remote_prefix(r"\\Server\Share\a.png"),
            share(r"server\share")
        );
        assert_eq!(
            remote_prefix("//server/share/a.png"),
            share(r"server\share")
        );
        assert_eq!(
            remote_prefix(r"\\?\UNC\server\share\a"),
            share(r"server\share")
        );
        assert_eq!(remote_prefix(r"\\.\pipe\x"), Some(Remote::Device));
        assert_eq!(remote_prefix(r"\\?\GLOBALROOT\x"), Some(Remote::Device));
        assert_eq!(remote_prefix(r"\\server"), Some(Remote::Device));
        assert_eq!(remote_prefix(r"\\?\C:\photos"), None);
        assert_eq!(remote_prefix(r"C:\photos"), None);
        assert_eq!(remote_prefix("/Users/me/photos"), None);
    }

    #[test]
    fn share_paths_and_relative_paths_are_refused_before_the_filesystem() {
        let dir = tempfile::tempdir().unwrap();
        let registry = test_registry(dir.path());
        registry
            .inner
            .lock()
            .unwrap()
            .media_folders
            .insert(PathBuf::from(r"\\?\UNC\nas\photos\album"));
        assert!(registry.lexically_allowed(Path::new(r"\\NAS\photos\album\a.png")));
        assert!(!registry.lexically_allowed(Path::new(r"\\attacker\share\a.png")));
        assert!(!registry.lexically_allowed(Path::new(r"\\.\pipe\x")));
        assert!(registry.lexically_allowed(&dir.path().join("local.png")));
        // A relative path is refused before it is resolved against the working folder.
        assert!(resolve_path(Path::new("relative/a.png")).is_err());
        assert!(registry
            .check_media_input(Path::new(r"\\attacker\share\a.png"))
            .is_err());
    }
}
