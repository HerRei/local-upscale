use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicBool, AtomicU32, AtomicU64},
        Arc, Mutex, MutexGuard,
    },
};

use tauri::{AppHandle, Emitter};

use crate::{
    catalog::{load_catalog, refresh_install_state},
    database::Database,
    error::{AppError, AppResult},
    launch::LaunchIntent,
    paths::AppPaths,
    settings,
    types::{
        AppSnapshot, BenchmarkResult, CapabilityInfo, CatalogManifest, EngineInfo, Recipe,
        RuntimeStatus, UiSettings, APP_VERSION, PROTOCOL_VERSION,
    },
};

pub struct WorkerControl {
    pub cancellation: Mutex<crate::cancellation::Cancellation>,
    pub sender: Mutex<Option<std::sync::mpsc::Sender<String>>>,
    pub generation: AtomicU64,
    pub pid: AtomicU32,
    pub restart_count: AtomicU32,
    pub shutting_down: AtomicBool,
}

impl Default for WorkerControl {
    fn default() -> Self {
        Self {
            cancellation: Mutex::new(crate::cancellation::Cancellation::default()),
            sender: Mutex::new(None),
            generation: AtomicU64::new(0),
            pid: AtomicU32::new(0),
            restart_count: AtomicU32::new(0),
            shutting_down: AtomicBool::new(false),
        }
    }
}

pub struct AppState {
    pub video_preview: crate::video_preview::VideoPreviewControl,
    pub updates: crate::updates::UpdateControl,
    #[cfg(target_os = "linux")]
    pub media_server: Mutex<Option<crate::media_server::MediaServer>>,
    pub paths: AppPaths,
    pub database: Mutex<Database>,
    pub catalog: Mutex<CatalogManifest>,
    pub settings: Mutex<UiSettings>,
    pub recipes: Mutex<Vec<Recipe>>,
    pub capabilities: Mutex<CapabilityInfo>,
    pub engine: Mutex<Option<EngineInfo>>,
    pub runtime: Mutex<RuntimeStatus>,
    pub latest_benchmark: Mutex<Option<BenchmarkResult>>,
    pub worker: WorkerControl,
    pub scheduler: Mutex<()>,
    pub downloads: Mutex<HashMap<String, Arc<AtomicBool>>>,
    pub launch_intents: Mutex<Vec<LaunchIntent>>,
}

impl AppState {
    pub fn new() -> AppResult<Self> {
        Self::from_paths(AppPaths::discover()?)
    }

    pub(crate) fn from_paths(paths: AppPaths) -> AppResult<Self> {
        crate::update_storage::before_version(&paths, &crate::distribution::profile_version())?;
        let database = Database::open(&paths.database)?;
        let catalog = load_catalog(&paths)?;
        let (settings, recipes) = settings::load(&paths);
        let latest_benchmark = settings::load_benchmark(&paths);
        Ok(Self {
            video_preview: crate::video_preview::VideoPreviewControl::default(),
            updates: crate::updates::UpdateControl::default(),
            #[cfg(target_os = "linux")]
            media_server: Mutex::new(None),
            paths,
            database: Mutex::new(database),
            catalog: Mutex::new(catalog),
            settings: Mutex::new(settings),
            recipes: Mutex::new(recipes),
            capabilities: Mutex::new(CapabilityInfo::detecting()),
            engine: Mutex::new(None),
            runtime: Mutex::new(RuntimeStatus::default()),
            latest_benchmark: Mutex::new(latest_benchmark),
            worker: WorkerControl::default(),
            scheduler: Mutex::new(()),
            downloads: Mutex::new(HashMap::new()),
            launch_intents: Mutex::new(Vec::new()),
        })
    }

    pub fn snapshot(&self) -> AppResult<AppSnapshot> {
        // Keep every guard in a short, explicit scope. In particular, do not
        // lock `database` twice inside one struct literal: Rust extends those
        // temporary guards to the end of the statement and a non-reentrant
        // mutex would deadlock the first frontend bootstrap.
        let catalog = lock(&self.catalog)?.clone();
        let (media, jobs) = {
            let database = lock(&self.database)?;
            (database.list_media()?, database.list_jobs()?)
        };
        let settings = lock(&self.settings)?.clone();
        let recipes = lock(&self.recipes)?.clone();
        let capabilities = lock(&self.capabilities)?.clone();
        let engine = lock(&self.engine)?.clone();
        let runtime = lock(&self.runtime)?.clone();
        let latest_benchmark = lock(&self.latest_benchmark)?.clone();

        Ok(AppSnapshot {
            app_version: APP_VERSION.into(),
            protocol_version: PROTOCOL_VERSION,
            catalog,
            media,
            jobs,
            settings,
            recipes,
            capabilities,
            engine,
            runtime,
            latest_benchmark,
        })
    }

    pub fn persist_settings(&self) -> AppResult<()> {
        let ui = lock(&self.settings)?.clone();
        let recipes = lock(&self.recipes)?.clone();
        settings::save(&self.paths, &ui, &recipes)
    }

    pub fn refresh_catalog(&self) -> AppResult<()> {
        let mut catalog = lock(&self.catalog)?;
        refresh_install_state(&mut catalog, &self.paths);
        Ok(())
    }
}

pub fn lock<T>(mutex: &Mutex<T>) -> AppResult<MutexGuard<'_, T>> {
    mutex
        .lock()
        .map_err(|_| AppError::Config("internal state lock was poisoned".into()))
}

pub fn emit_state_changed(app: &AppHandle) {
    let _ = app.emit("state-changed", ());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_reads_media_and_jobs_without_relocking_database() {
        let directory = tempfile::tempdir().unwrap();
        let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();

        let snapshot = state.snapshot().unwrap();

        assert!(snapshot.media.is_empty());
        assert!(snapshot.jobs.is_empty());
        assert_eq!(snapshot.app_version, APP_VERSION);
    }
}
