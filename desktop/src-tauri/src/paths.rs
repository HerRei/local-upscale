use std::path::{Path, PathBuf};

use directories::{BaseDirs, UserDirs};

use crate::error::{AppError, AppResult};

#[derive(Clone, Debug)]
pub struct AppPaths {
    pub next_root: PathBuf,
    pub model_root: PathBuf,
    pub database: PathBuf,
    pub settings: PathBuf,
    pub benchmark: PathBuf,
    pub work_root: PathBuf,
    pub legacy_settings: PathBuf,
    pub default_output: PathBuf,
    #[cfg(debug_assertions)]
    pub repo_root: PathBuf,
}

impl AppPaths {
    pub fn discover() -> AppResult<Self> {
        let base =
            BaseDirs::new().ok_or_else(|| AppError::Config("home directory unavailable".into()))?;
        let shared_root = base.data_local_dir().join("LocalSR");
        let next_root = shared_root.join("next");
        let model_root = shared_root.join("models");
        std::fs::create_dir_all(&next_root)?;
        std::fs::create_dir_all(&model_root)?;
        let work_root = next_root.join("work");
        std::fs::create_dir_all(&work_root)?;

        let default_output = UserDirs::new()
            .and_then(|dirs| dirs.desktop_dir().map(Path::to_path_buf))
            .unwrap_or_else(|| base.home_dir().to_path_buf());
        #[cfg(debug_assertions)]
        let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(Path::parent)
            .ok_or_else(|| AppError::Config("repository path could not be resolved".into()))?
            .to_path_buf();

        Ok(Self {
            database: next_root.join("queue.sqlite3"),
            settings: next_root.join("settings.json"),
            benchmark: next_root.join("benchmark-latest.json"),
            work_root,
            legacy_settings: shared_root.join("settings.json"),
            next_root,
            model_root,
            default_output,
            #[cfg(debug_assertions)]
            repo_root,
        })
    }
}

#[cfg(test)]
impl AppPaths {
    pub fn under(root: &Path) -> Self {
        let shared_root = root.join("LocalSR");
        let next_root = shared_root.join("next");
        Self {
            database: next_root.join("queue.sqlite3"),
            settings: next_root.join("settings.json"),
            benchmark: next_root.join("benchmark-latest.json"),
            work_root: next_root.join("work"),
            legacy_settings: shared_root.join("settings.json"),
            model_root: shared_root.join("models"),
            default_output: root.join("output"),
            #[cfg(debug_assertions)]
            repo_root: root.to_path_buf(),
            next_root,
        }
    }
}
