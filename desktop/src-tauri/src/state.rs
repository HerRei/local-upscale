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
    /// Starts in a row after which the engine stopped by itself, before it ran long
    /// enough or finished a job.
    pub restart_count: AtomicU32,
    /// Whether the running engine has finished a job; with a long run it shows the
    /// engine works, so its stopping later is not a failing start.
    pub finished_job: AtomicBool,
    pub shutting_down: AtomicBool,
    /// The process ID of an engine that ended on its own with a failure, for its
    /// replacement: the engine then knows which GPU step crashed it.
    pub crashed_engine: AtomicU32,
}

impl Default for WorkerControl {
    fn default() -> Self {
        Self {
            cancellation: Mutex::new(crate::cancellation::Cancellation::default()),
            sender: Mutex::new(None),
            generation: AtomicU64::new(0),
            pid: AtomicU32::new(0),
            restart_count: AtomicU32::new(0),
            finished_job: AtomicBool::new(false),
            shutting_down: AtomicBool::new(false),
            crashed_engine: AtomicU32::new(0),
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
    /// The native editing executable found at startup, or empty.
    pub edit_runtime: Mutex<String>,
    pub worker: WorkerControl,
    pub scheduler: Mutex<()>,
    // One writer of the settings file at a time, from taking the copy to writing it, so an
    // older copy can never land after a newer one.
    pub settings_io: Mutex<()>,
    pub downloads: Mutex<HashMap<String, Arc<AtomicBool>>>,
    pub launch_intents: Mutex<Vec<LaunchIntent>>,
    /// Set when no settings file existed at launch: the first device report
    /// then replaces the CPU default with the computer's GPU.
    pub choose_device: AtomicBool,
    /// What the user has to be told once at start-up, for example that a damaged
    /// queue file was moved aside. Shown and emptied by `take_startup_notices`.
    startup_notices: Mutex<Vec<String>>,
    /// The programs the user's choices and the host's own searches have named.
    pub programs: crate::programs::GrantedPrograms,
    /// The editing runtimes the user picked in the host's own file dialog.
    pub edit_programs: crate::programs::GrantedPrograms,
}

impl AppState {
    pub fn new() -> AppResult<Self> {
        Self::from_paths(AppPaths::discover()?)
    }

    pub(crate) fn from_paths(paths: AppPaths) -> AppResult<Self> {
        let mut notices = Vec::new();
        // The safety copy is a convenience: a full disk or a damaged queue must not keep
        // the app from starting. No update step depends on it.
        if let Err(error) =
            crate::update_storage::before_version(&paths, &crate::distribution::profile_version())
        {
            eprintln!("LocalSR could not copy its data before the new version: {error}");
            notices.push(format!(
                "LocalSR could not make its safety copy of your settings and queue ({error})."
            ));
        }
        let (mut database, set_aside) = Database::open_or_set_aside(&paths.database)?;
        crate::cancellation::sweep_scratch(&database.take_interrupted_outputs());
        if let Some(moved) = set_aside {
            notices.push(format!(
                "The queue and history file was damaged. LocalSR moved it to {} and started a new, empty queue. Your settings and models were not touched.",
                moved.display()
            ));
        }
        let catalog = load_catalog(&paths)?;
        let first_run = !paths.settings.exists() && !paths.legacy_settings.exists();
        let (settings, recipes) = settings::load(&paths);
        let latest_benchmark = settings::load_benchmark(&paths);
        let programs = crate::programs::GrantedPrograms::default();
        programs.grant(&settings.external_ffmpeg_path);
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
            edit_runtime: Mutex::new(String::new()),
            worker: WorkerControl::default(),
            scheduler: Mutex::new(()),
            settings_io: Mutex::new(()),
            downloads: Mutex::new(HashMap::new()),
            launch_intents: Mutex::new(Vec::new()),
            choose_device: AtomicBool::new(first_run),
            startup_notices: Mutex::new(notices),
            programs,
            edit_programs: crate::programs::GrantedPrograms::default(),
        })
    }

    /// The start-up notices, once: a second call returns nothing.
    pub fn take_startup_notices(&self) -> Vec<String> {
        self.startup_notices
            .lock()
            .map(|mut notices| std::mem::take(&mut *notices))
            .unwrap_or_default()
    }

    pub fn snapshot(&self) -> AppResult<AppSnapshot> {
        // Keep every guard in a short, explicit scope. In particular, do not
        // lock `database` twice inside one struct literal: Rust extends those
        // temporary guards to the end of the statement and a non-reentrant
        // mutex would deadlock the first frontend bootstrap.
        let catalog = lock(&self.catalog)?.clone();
        let (media, mut jobs, runtime) = {
            let database = lock(&self.database)?;
            let mut runtime = lock(&self.runtime)?;
            crate::compare::refresh(&database, &mut runtime.compare)?;
            (
                database.list_media()?,
                database.list_jobs()?,
                runtime.clone(),
            )
        };
        // The queue row says which model runs ("Running SwinIR · 2 queued").
        for job in &mut jobs {
            if let Some(label) = crate::results::catalog_label(&catalog, &job.model_id) {
                job.model_label = label;
            }
        }
        let settings = lock(&self.settings)?.clone();
        let recipes = lock(&self.recipes)?.clone();
        let capabilities = lock(&self.capabilities)?.clone();
        let engine = lock(&self.engine)?.clone();
        let latest_benchmark = lock(&self.latest_benchmark)?.clone();
        let edit = crate::editing::EditSetup {
            models: crate::editing::catalog(&self.paths)?,
            runtime_path: lock(&self.edit_runtime)?.clone(),
        };

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
            edit,
        })
    }

    pub fn persist_settings(&self) -> AppResult<()> {
        let _io = lock(&self.settings_io)?;
        self.write_settings()
    }

    /// Write the settings as they are now. The caller holds `settings_io`.
    pub fn write_settings(&self) -> AppResult<()> {
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
    fn the_scratch_of_a_job_a_stopped_host_left_running_is_removed_at_the_next_start() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        let output = directory.path().join("out").join("clip.mp4");
        let scratch = directory
            .path()
            .join("out")
            .join(format!(".localsr-job-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&scratch).unwrap();
        std::fs::write(scratch.join("frame"), b"partial").unwrap();
        {
            let state = AppState::from_paths(paths.clone()).unwrap();
            let database = state.database.lock().unwrap();
            database
                .insert_media("m", "/in/clip.mov", "clip", "video")
                .unwrap();
            let request = serde_json::json!({"type": "video_job_request",
                "data": {"output_video_path": output}});
            database.insert_job("j", "m", &request.to_string()).unwrap();
            database.set_job_status("j", "running").unwrap();
            // The host stops here without cleaning up.
            assert!(scratch.is_dir());
        }
        let state = AppState::from_paths(paths).unwrap();
        assert!(!scratch.exists());
        let jobs = state.database.lock().unwrap().list_jobs().unwrap();
        assert_eq!(jobs[0].status, "interrupted");
    }

    #[test]
    fn the_program_saved_by_an_earlier_run_is_known_at_start() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        let saved = UiSettings {
            external_ffmpeg_path: "/opt/tools/ffmpeg".into(),
            ..UiSettings::default()
        };
        crate::settings::save(&paths, &saved, &[]).unwrap();
        let state = AppState::from_paths(paths).unwrap();
        assert!(state.programs.check("/opt/tools/ffmpeg").is_ok());
        assert!(state.programs.check("/opt/tools/other").is_err());
    }

    fn paths_with_queue(directory: &std::path::Path, content: &[u8]) -> AppPaths {
        let paths = AppPaths::under(directory);
        std::fs::create_dir_all(&paths.next_root).unwrap();
        std::fs::write(&paths.database, content).unwrap();
        paths
    }

    #[test]
    fn a_damaged_queue_does_not_keep_the_app_from_starting() {
        let directory = tempfile::tempdir().unwrap();
        let paths = paths_with_queue(directory.path(), b"not a database\n");
        std::fs::write(&paths.settings, r#"{"schema_version":2}"#).unwrap();
        let state = AppState::from_paths(paths.clone()).unwrap();
        assert!(state.snapshot().unwrap().media.is_empty());
        let notices = state.take_startup_notices();
        let moved = std::fs::read_dir(&paths.next_root)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .find(|path| path.to_string_lossy().contains("queue.damaged-"))
            .unwrap();
        assert_eq!(std::fs::read(&moved).unwrap(), b"not a database\n");
        assert!(notices
            .iter()
            .any(|notice| notice.contains(moved.to_str().unwrap())));
        assert!(state.take_startup_notices().is_empty());
        assert_eq!(
            std::fs::read_to_string(&paths.settings).unwrap(),
            r#"{"schema_version":2}"#
        );
    }

    #[test]
    fn a_failed_safety_copy_is_reported_and_does_not_keep_the_app_from_starting() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        std::fs::create_dir_all(&paths.next_root).unwrap();
        drop(crate::database::Database::open(&paths.database).unwrap());
        std::fs::write(paths.next_root.join("data-version.json"), "0.0.1").unwrap();
        std::fs::write(paths.next_root.join("backups"), b"a file, not a folder").unwrap();
        let state = AppState::from_paths(paths).unwrap();
        let notices = state.take_startup_notices();
        assert_eq!(notices.len(), 1);
        assert!(notices[0].contains("safety copy"));
    }

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
