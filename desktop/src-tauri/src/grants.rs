//! Host-owned registry of filesystem paths the user or the host itself authorized.
//!
//! The web view only ever names paths for media, output folders, custom models, or
//! benchmark exports that the host obtained from a native dialog, a drag-and-drop
//! event, a launch / Open With event, the app's own default and profile folders, or
//! previously persisted media and settings.
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
    types::{MediaItem, Recipe, UiSettings},
};

const MEDIA_REFUSAL: &str =
    "LocalSR can only open media files or folders that you chose in its dialog, dropped onto the window, or opened with the app.";
const OUTPUT_REFUSAL: &str =
    "LocalSR can only write to its default output folder or a folder you chose. Choose the output folder again.";
const MODEL_REFUSAL: &str =
    "LocalSR can only use a checkpoint that you chose in its file dialog. Choose the checkpoint again.";
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
    media_files: HashSet<PathBuf>,
    media_folders: HashSet<PathBuf>,
    revoked_media: HashSet<PathBuf>,
    output_folders: HashSet<PathBuf>,
    persisted_outputs: HashSet<PathBuf>,
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
        for item in media {
            if let Ok(resolved) = resolve_path(Path::new(&item.path)) {
                state.media_files.insert(resolved);
            }
        }
        seed_outputs(&mut state, &settings.output_directory);
        seed_models(&mut state, &settings.custom_model_path, recipes);
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

    pub fn reconcile_models(&self, custom_model_path: &str, recipes: &[Recipe]) {
        if let Ok(mut state) = self.inner.lock() {
            seed_models(&mut state, custom_model_path, recipes);
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
        let resolved =
            resolve_path(path).map_err(|_| AppError::Validation(MEDIA_REFUSAL.into()))?;
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
        let resolved =
            resolve_path(path).map_err(|_| AppError::Validation(MEDIA_REFUSAL.into()))?;
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
        let resolved =
            resolve_path(path).map_err(|_| AppError::Validation(OUTPUT_REFUSAL.into()))?;
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
        let resolved =
            resolve_path(path).map_err(|_| AppError::Validation(MODEL_REFUSAL.into()))?;
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
        let resolved =
            resolve_path(path).map_err(|_| AppError::Validation(EXPORT_REFUSAL.into()))?;
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

fn seed_outputs(state: &mut RegistryState, output_directory: &str) {
    let mut next_persisted = HashSet::new();
    let trimmed = output_directory.trim();
    if !trimmed.is_empty() {
        if let Ok(resolved) = resolve_path(Path::new(trimmed)) {
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

fn seed_models(state: &mut RegistryState, custom_model_path: &str, recipes: &[Recipe]) {
    let mut next_persisted = HashSet::new();
    for raw in std::iter::once(custom_model_path).chain(
        recipes
            .iter()
            .map(|recipe| recipe.custom_model_path.as_str()),
    ) {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            if let Ok(resolved) = resolve_path(Path::new(trimmed)) {
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
}
