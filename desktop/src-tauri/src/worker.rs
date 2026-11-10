use std::{
    env,
    io::{BufReader, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU32, Ordering},
        mpsc, Arc,
    },
    thread,
    time::{Duration, Instant},
};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_notification::NotificationExt;

use crate::{
    error::{AppError, AppResult},
    pipe_lines::{read_frame, skip_line, Frame},
    process_tree::{lead_own_group, ProcessTree},
    settings,
    state::{emit_state_changed, lock, AppState},
    types::{
        BenchmarkResult, CapabilityInfo, DeviceInfo, EngineInfo, RuntimeStatus, WorkerEnvelope,
        PROTOCOL_VERSION,
    },
};

struct WorkerCommand {
    program: PathBuf,
    arguments: Vec<String>,
    working_directory: Option<PathBuf>,
    python_path: Option<PathBuf>,
}

#[derive(Default)]
struct WorkerEventGate {
    job_id: String,
    last_progress: Option<Instant>,
    last_tile_completed: Option<Instant>,
    last_video_started: Option<Instant>,
    last_video_progress: Option<Instant>,
    last_video_preview: Option<Instant>,
    last_benchmark_progress: Option<Instant>,
}

impl WorkerEventGate {
    fn should_forward(&mut self, envelope: &WorkerEnvelope, now: Instant) -> bool {
        let data = &envelope.data;
        let job_id = string(data, "job_id");
        if !job_id.is_empty() && job_id != self.job_id {
            self.reset_for(job_id);
        }

        match envelope.message_type.as_str() {
            // The following regular progress envelope carries the same bounded
            // percentage and ETA. Keep the typed stage event in the protocol,
            // but do not duplicate a high-frequency webview event.
            "stage_progress" => false,
            "benchmark_stage_progress" => false,
            "progress" => sampled(
                &mut self.last_progress,
                now,
                is_last_item(data, "completed_tiles", "total_tiles"),
                Duration::from_millis(100),
            ),
            "tile_update" => match string(data, "phase").as_str() {
                "reset" => true,
                // Position and completion metadata must stay paired even for
                // fast models. Only expensive JPEG payloads are sampled.
                "started" => true,
                "completed" if string(data, "jpeg_base64").is_empty() => true,
                "completed" => sampled(
                    &mut self.last_tile_completed,
                    now,
                    is_last_item(data, "completed_tiles", "total_tiles"),
                    Duration::from_millis(80),
                ),
                _ => false,
            },
            "video_frame_started" => sampled(
                &mut self.last_video_started,
                now,
                false,
                Duration::from_millis(120),
            ),
            "video_frame_completed" if !string(data, "jpeg_base64").is_empty() => sampled(
                &mut self.last_video_preview,
                now,
                false,
                Duration::from_millis(150),
            ),
            "video_frame_completed" | "video_tile_progress" => sampled(
                &mut self.last_video_progress,
                now,
                is_last_item(data, "frames_processed", "total_frames"),
                Duration::from_millis(100),
            ),
            "live_preview_frame" => true,
            "benchmark_progress" => sampled(
                &mut self.last_benchmark_progress,
                now,
                is_last_item(data, "completed_frames", "total_frames"),
                Duration::from_millis(100),
            ),
            "job_completed"
            | "video_job_completed"
            | "job_cancelled"
            | "job_failed"
            | "benchmark_completed"
            | "benchmark_cancelled"
            | "benchmark_failed" => {
                self.job_id.clear();
                true
            }
            _ => true,
        }
    }

    fn reset_for(&mut self, job_id: String) {
        self.job_id = job_id;
        self.last_progress = None;
        self.last_tile_completed = None;
        self.last_video_started = None;
        self.last_video_progress = None;
        self.last_video_preview = None;
        self.last_benchmark_progress = None;
    }
}

fn sampled(last: &mut Option<Instant>, now: Instant, force: bool, interval: Duration) -> bool {
    let interval_elapsed = match last {
        Some(previous) => now.duration_since(*previous) >= interval,
        None => true,
    };
    if force || interval_elapsed {
        *last = Some(now);
        true
    } else {
        false
    }
}

fn is_last_item(data: &Value, completed_key: &str, total_key: &str) -> bool {
    let completed = integer(data, completed_key);
    let total = integer(data, total_key);
    total > 0 && completed >= total
}

pub fn start_worker(state: Arc<AppState>, app: AppHandle) -> AppResult<()> {
    if state.worker.shutting_down.load(Ordering::SeqCst) {
        return Ok(());
    }
    if lock(&state.worker.sender)?.is_some() {
        return Ok(());
    }

    let specification = resolve_worker_command(&state, &app)?;
    let mut command = Command::new(&specification.program);
    command.args(&specification.arguments);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;

        // Keep the JSON-lines worker attached to pipes without flashing a
        // console window behind the desktop application.
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // The engine starts FFmpeg and the system media helper. Stopping the engine has
    // to stop them too, so they stay in its process group (or Job Object).
    lead_own_group(&mut command);
    if let Some(directory) = specification.working_directory {
        command.current_dir(directory);
    }
    if let Some(path) = specification.python_path {
        command.env("PYTHONPATH", path);
        command.env("PYTHONUTF8", "1");
    }
    command.env("PYTHONUNBUFFERED", "1");
    let crashed_engine = state.worker.crashed_engine.swap(0, Ordering::SeqCst);
    if crashed_engine != 0 {
        command.env("LOCALSR_CRASHED_ENGINE", crashed_engine.to_string());
    }

    let mut child = command.spawn().map_err(|error| {
        // The next attempt still has to tell its engine which one crashed.
        if crashed_engine != 0 {
            state
                .worker
                .crashed_engine
                .store(crashed_engine, Ordering::SeqCst);
        }
        AppError::Worker(format!(
            "could not start the LocalSR inference engine: {error}"
        ))
    })?;
    state.worker.pid.store(child.id(), Ordering::SeqCst);
    let tree = ProcessTree::new(&child);
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| AppError::Worker("worker stdin was not available".into()))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| AppError::Worker("worker stdout was not available".into()))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| AppError::Worker("worker stderr was not available".into()))?;

    let generation = state.worker.generation.fetch_add(1, Ordering::SeqCst) + 1;
    state.worker.finished_job.store(false, Ordering::SeqCst);
    let (sender, receiver) = mpsc::channel::<String>();
    *lock(&state.worker.sender)? = Some(sender);
    {
        let mut runtime = lock(&state.runtime)?;
        runtime.worker = "starting".into();
        runtime.status_title = "Starting engine".into();
        runtime.status_detail = "The inference process is loading.".into();
        // A new engine has run nothing yet.
        runtime.hardware_note.clear();
        runtime.engine_retry = false;
    }
    emit_state_changed(&app);

    thread::spawn(move || {
        let mut writer = stdin;
        for message in receiver {
            if writeln!(writer, "{message}")
                .and_then(|_| writer.flush())
                .is_err()
            {
                break;
            }
        }
    });

    let reader_state = state.clone();
    let reader_app = app.clone();
    let drained = Arc::new(AtomicBool::new(false));
    let reader_drained = drained.clone();
    let overflowed = Arc::new(AtomicBool::new(false));
    let reader_overflowed = overflowed.clone();
    thread::spawn(move || {
        let mut event_gate = WorkerEventGate::default();
        let mut reader = BufReader::new(stdout);
        let mut line = Vec::new();
        loop {
            match read_frame(&mut reader, MAX_ENGINE_LINE_BYTES, &mut line) {
                Ok(Frame::Line) => {}
                Ok(Frame::End) | Err(_) => break,
                Ok(Frame::TooLong) => {
                    // An engine that sends a message this long is faulty. The
                    // supervisor stops it and the usual restart takes over.
                    eprintln!("LocalSR stopped the inference engine: a message passed {MAX_ENGINE_LINE_BYTES} bytes");
                    reader_overflowed.store(true, Ordering::SeqCst);
                    break;
                }
            }
            match serde_json::from_slice::<WorkerEnvelope>(&line) {
                Ok(envelope) => {
                    if reader_state.worker.generation.load(Ordering::SeqCst) != generation {
                        break;
                    }
                    if event_gate.should_forward(&envelope, Instant::now()) {
                        handle_worker_envelope(&reader_state, &reader_app, envelope, generation);
                    }
                }
                Err(_) => {
                    let warning = WorkerEnvelope {
                        message_type: "warning".into(),
                        data: json!({"message": "The inference engine sent an invalid response."}),
                    };
                    let _ = reader_app.emit("worker-message", warning);
                }
            }
            // One large preview must not keep its buffer for the rest of the session.
            line.shrink_to(READ_BUFFER_KEEP_BYTES);
        }
        reader_drained.store(true, Ordering::SeqCst);
    });

    thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        let mut line = Vec::new();
        loop {
            match read_frame(&mut reader, MAX_ENGINE_STDERR_LINE_BYTES, &mut line) {
                Ok(Frame::Line) => {}
                Ok(Frame::End) | Err(_) => break,
                // Only the start of a long line is kept; the rest is read and dropped
                // so the engine never blocks on a full pipe.
                Ok(Frame::TooLong) => {
                    if skip_line(&mut reader).is_err() {
                        break;
                    }
                }
            }
            if cfg!(debug_assertions) {
                eprintln!("LocalSR engine: {}", String::from_utf8_lossy(&line));
            }
        }
    });

    thread::spawn(move || {
        // The jobs a cancellation covered when it had to stop the engine.
        let mut forced_jobs: Vec<String> = Vec::new();
        let mut restart_requested: Option<Instant> = None;
        let engine_pid = child.id();
        let engine_started = Instant::now();
        let mut stopped_by_app = false;
        let status = loop {
            match tree.try_wait(&mut child) {
                Ok(Some(status)) => break Ok(status),
                Err(error) => break Err(error),
                Ok(None) => {}
            }
            if state.worker.shutting_down.load(Ordering::SeqCst) {
                stopped_by_app = true;
                tree.terminate();
                let _ = child.kill();
                break tree.wait(&mut child);
            }
            if overflowed.load(Ordering::SeqCst) {
                // Not an app-requested stop: the engine failed, and the restart
                // counter says so.
                tree.terminate();
                let _ = child.kill();
                break tree.wait(&mut child);
            }
            let restarting = state
                .runtime
                .lock()
                .map(|runtime| runtime.worker == "restarting")
                .unwrap_or(false);
            if restart_overdue(restarting, &mut restart_requested, Instant::now()) {
                // The engine asked for a restart but has not exited, for example because
                // freeing a lost GPU hangs; the queue waits for its replacement.
                stopped_by_app = true;
                tree.terminate();
                let _ = child.kill();
                break tree.wait(&mut child);
            }
            if let Ok(mut cancellation) = state.worker.cancellation.lock() {
                if cancellation.expired(Instant::now()) {
                    // This lock also protects terminal envelopes: a completion
                    // cannot dispatch another job while its process is killed.
                    cancellation.resetting = true;
                    forced_jobs = cancellation.job_ids.clone();
                    if let Ok(mut runtime) = state.runtime.lock() {
                        runtime.status_title = "Cancelling".into();
                        runtime.status_detail =
                            "Stopping the inference engine and releasing memory…".into();
                    }
                    emit_state_changed(&app);
                    stopped_by_app = true;
                    tree.terminate();
                    let _ = child.kill();
                    break tree.wait(&mut child);
                }
            }
            thread::sleep(Duration::from_millis(100));
        };
        // Whatever ended the engine, nothing it started may outlive it: a helper
        // would keep writing into scratch the host removes next.
        tree.terminate();
        if !stopped_by_app && engine_crashed(&status) {
            state
                .worker
                .crashed_engine
                .store(engine_pid, Ordering::SeqCst);
        }
        if state.worker.generation.load(Ordering::SeqCst) == generation {
            if let Ok(mut runtime) = state.runtime.lock() {
                mark_exited(&mut runtime);
            }
        }
        if forced_jobs.is_empty() && !state.worker.shutting_down.load(Ordering::SeqCst) {
            // Read what the engine wrote before it exited, such as the failure that asks
            // for this restart, before its messages stop counting. Bounded: a helper
            // process that still holds the pipe must not hold up recovery.
            let deadline = Instant::now() + OUTPUT_DRAIN_LIMIT;
            while !drained.load(Ordering::SeqCst) && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(20));
            }
        }
        if supervisor_should_return(&state.worker, generation) {
            state.worker.pid.store(0, Ordering::SeqCst);
            return;
        }
        state.worker.pid.store(0, Ordering::SeqCst);
        state.worker.generation.fetch_add(1, Ordering::SeqCst);
        if let Ok(mut sender) = state.worker.sender.lock() {
            *sender = None;
        }
        let cleanup_error = lock(&state.worker.cancellation)
            .and_then(|mut cancellation| cancellation.finish())
            .err();
        let mut requested_restart = false;
        if let Ok(mut runtime) = state.runtime.lock() {
            // The killed or crashed engine's job ends here, its route note with it.
            let active = runtime.take_active_job();
            // A batch's unfinished photos, or the one active job.
            let mut active_ids = std::mem::take(&mut runtime.active_batch_job_ids);
            if active_ids.is_empty() && !active.is_empty() {
                active_ids.push(active);
            }
            requested_restart = runtime.worker == "restarting" && forced_jobs.is_empty();
            let last_confirmed_stage = runtime.status_title.clone();
            let exit_summary = match &status {
                Ok(exit) => format!("The inference engine exited with {exit}"),
                Err(error) => {
                    format!("The inference engine could not report its exit status: {error}")
                }
            };
            let interruption = worker_interruption_detail(&exit_summary, &last_confirmed_stage);
            runtime.worker = "unavailable".into();
            if requested_restart {
                // Keep the failure that asked for the restart on screen; it already says
                // that the engine restarts.
            } else {
                runtime.status_title = if forced_jobs.is_empty() {
                    "Engine stopped"
                } else {
                    "Cancelled"
                }
                .into();
            }
            runtime.status_detail = if requested_restart {
                std::mem::take(&mut runtime.status_detail)
            } else if forced_jobs.is_empty() {
                format!("{interruption} Restarting…")
            } else if let Some(error) = cleanup_error {
                format!("Work stopped. Could not remove its temporary directory: {error}. Restarting engine…")
            } else {
                "Work stopped; temporary output removed. Restarting engine…".into()
            };
            runtime.estimated_remaining_seconds = 0.0;
            if let Ok(database) = state.database.lock() {
                settle_stopped_jobs(&database, &active_ids, &forced_jobs, &interruption);
            }
        }
        emit_state_changed(&app);

        let failed = forced_jobs.is_empty() && !requested_restart;
        let finished_job = state.worker.finished_job.swap(false, Ordering::SeqCst);
        if let Some(delay) = restart_delay(
            &state.worker.restart_count,
            engine_started.elapsed(),
            finished_job,
            failed,
        ) {
            thread::sleep(delay);
            if let Err(error) = start_worker(state.clone(), app.clone()) {
                give_up_on_engine(&state, &app, error.to_string());
            }
        } else {
            give_up_on_engine(
                &state,
                &app,
                "The inference engine repeatedly stopped. Try again, or restart LocalSR.".into(),
            );
        }
    });

    Ok(())
}

pub fn send(state: &AppState, message: &Value) -> AppResult<()> {
    let encoded = serde_json::to_string(message)?;
    let guard = lock(&state.worker.sender)?;
    let sender = guard
        .as_ref()
        .ok_or_else(|| AppError::Worker("the inference engine is not ready".into()))?;
    sender
        .send(encoded)
        .map_err(|_| AppError::Worker("the inference engine connection closed".into()))
}

/// The most queued photos one image batch request carries (the engine's limit too).
const MAX_BATCH_PHOTOS: usize = 64;
/// Catalog architectures the Neural Engine runs (SPAN, RRDBNet, FBCNN). The engine
/// checks again and runs any photo it cannot batch as an ordinary job.
const BATCH_ARCHITECTURES: [&str; 3] = ["SPAN", "RealESRGAN", "FBCNN"];

/// The recipe the photos of one batch share: the whole request apart from each
/// photo's identity and paths. None for anything but an image on Apple's GPU with
/// the Neural Engine on, without a fix before the upscaler and without Faces.
fn image_batch_key(message: &Value) -> Option<Value> {
    let data = &message["data"];
    let face = data["face_model_path"]
        .as_str()
        .is_some_and(|path| !path.is_empty());
    let stages = data["stages"].as_array();
    if message["type"] != "job_request"
        || data["device"] != "mps"
        || data["neural_engine"] == false
        || data["run_alone"] == true
        || face
        || stages.is_some_and(|stages| stages.len() != 1 || stages[0]["kind"] == "face_restore")
    {
        return None;
    }
    let mut key = data.as_object()?.clone();
    for field in [
        "job_id",
        "image_path",
        "output_path",
        "scratch_directory",
        "output_temporary_directory",
    ] {
        key.remove(field);
    }
    Some(Value::Object(key))
}

/// What to send next: the first queued job, alone or with the queued photos right
/// behind it that share its recipe; a batch never reaches past a different job.
fn next_dispatch(
    window: &[crate::database::PendingJob],
    batch_model: impl Fn(&str) -> bool,
) -> AppResult<Option<(Vec<String>, Value)>> {
    let Some(first) = window.first() else {
        return Ok(None);
    };
    let message: Value = serde_json::from_str(&first.request_json)?;
    let mut ids = vec![first.id.clone()];
    let mut jobs = vec![message["data"].clone()];
    if let Some(key) = image_batch_key(&message)
        .filter(|_| batch_model(message["data"]["model_path"].as_str().unwrap_or_default()))
    {
        for job in window.iter().skip(1).take(MAX_BATCH_PHOTOS - 1) {
            let Ok(next) = serde_json::from_str::<Value>(&job.request_json) else {
                break;
            };
            if image_batch_key(&next).as_ref() != Some(&key) {
                break;
            }
            ids.push(job.id.clone());
            jobs.push(next["data"].clone());
        }
    }
    if ids.len() == 1 {
        let mut message = message;
        if let Some(data) = message["data"].as_object_mut() {
            data.remove("run_alone");
        }
        return Ok(Some((ids, message)));
    }
    let batch = json!({"type": "image_batch_request", "data": {"jobs": jobs}});
    Ok(Some((ids, batch)))
}

/// Installed model paths that may run as a photo batch with this engine.
fn batch_model_paths(state: &AppState, platform: bool) -> AppResult<Vec<String>> {
    let batches = platform
        && lock(&state.engine)?.as_ref().is_some_and(|engine| {
            engine
                .features
                .iter()
                .any(|feature| feature == "image_batch_v1")
        });
    if !batches {
        return Ok(Vec::new());
    }
    Ok(lock(&state.catalog)?
        .models
        .iter()
        .filter(|model| BATCH_ARCHITECTURES.contains(&model.architecture.as_str()))
        .filter_map(|model| model.installed_path.clone())
        .collect())
}

pub fn dispatch_next(state: &Arc<AppState>, app: &AppHandle) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    dispatch_next_locked(state, app)
}

pub fn dispatch_next_locked(state: &Arc<AppState>, app: &AppHandle) -> AppResult<()> {
    if begin_dispatch(state, cfg!(target_os = "macos"))? {
        emit_state_changed(app);
    }
    Ok(())
}

/// Send the next job or photo batch (batches only where `batches` holds: on a Mac);
/// true when something was sent or a job was failed, so the window needs to know.
fn begin_dispatch(state: &Arc<AppState>, batches: bool) -> AppResult<bool> {
    let mut changed = false;
    loop {
        match dispatch_once(state, batches)? {
            Dispatch::Sent => return Ok(true),
            Dispatch::Idle => return Ok(changed),
            // The job at the head of the queue failed; the next one is looked at.
            Dispatch::Failed => changed = true,
        }
    }
}

enum Dispatch {
    Sent,
    Idle,
    Failed,
}

fn dispatch_once(state: &Arc<AppState>, batches: bool) -> AppResult<Dispatch> {
    if !lock(&state.runtime)?.active_job_id.is_empty() {
        return Ok(Dispatch::Idle);
    }
    let Some(pending) = lock(&state.database)?.next_queued_job()? else {
        return Ok(Dispatch::Idle);
    };
    if lock(&state.runtime)?.worker != "ready" {
        return Ok(Dispatch::Idle);
    }
    let paths = batch_model_paths(state, batches)?;
    let mut window = Vec::new();
    if !paths.is_empty() {
        window = lock(&state.database)?.queued_job_window(MAX_BATCH_PHOTOS)?;
    }
    if window.first().map(|job| &job.id) != Some(&pending.id) {
        window = vec![pending];
    }
    let Some((ids, mut message)) = next_dispatch(&window, |path| paths.iter().any(|p| p == path))?
    else {
        return Ok(Dispatch::Idle);
    };
    let first = ids[0].clone();
    let prepared = lock(&state.worker.cancellation)?.prepare(&first, &mut message);
    if let Err(error) = prepared {
        // The first job's output folder cannot be used (an ejected drive, a folder
        // that is read-only): that job fails on its own and must not hold up the queue.
        lock(&state.database)?.finish_job(&first, "failed", "", &error.to_string())?;
        return Ok(Dispatch::Failed);
    }
    if let Err(error) = record_scratch_folders(state) {
        // Without the record a crash would leave the folder behind for good.
        lock(&state.worker.cancellation)?.finish()?;
        lock(&state.database)?.finish_job(&first, "failed", "", &error.to_string())?;
        return Ok(Dispatch::Failed);
    }
    // Preparing can end a batch early, before a photo whose folder cannot be used.
    let ids: Vec<String> = match message["data"]["jobs"].as_array() {
        Some(jobs) if message["type"] == "image_batch_request" => {
            jobs.iter().map(|job| string(job, "job_id")).collect()
        }
        _ => vec![first.clone()],
    };
    {
        let mut runtime = lock(&state.runtime)?;
        if runtime.worker != "ready" {
            // The engine stopped meanwhile. Nothing was sent, so the folders made for it go.
            drop(runtime);
            lock(&state.worker.cancellation)?.finish()?;
            return Ok(Dispatch::Idle);
        }
        runtime.begin_active_job(first.clone());
        runtime.active_batch_job_ids = if ids.len() > 1 { ids } else { Vec::new() };
        runtime.status_title = "Queued".into();
        runtime.status_detail = "Sending the next item to the inference engine.".into();
        runtime.progress = 0.0;
        runtime.result_preview_data_url.clear();
    }
    // The other photos of a batch stay queued until the engine starts each one.
    lock(&state.database)?.set_job_status(&first, "starting")?;
    if let Err(error) = send(state, &message) {
        lock(&state.worker.cancellation)?.finish()?;
        lock(&state.database)?.finish_job(&first, "failed", "", &error.to_string())?;
        let mut runtime = lock(&state.runtime)?;
        runtime.take_active_job();
        runtime.active_batch_job_ids.clear();
        return Err(error);
    }
    Ok(Dispatch::Sent)
}

/// Record the folders just made for this dispatch with their jobs, before the engine
/// can write into them: a later start removes exactly these after a crash.
fn record_scratch_folders(state: &AppState) -> AppResult<()> {
    let folders = lock(&state.worker.cancellation)?.scratch_folders();
    let database = lock(&state.database)?;
    for (id, folder) in folders {
        database.record_scratch(&id, &folder)?;
    }
    Ok(())
}

/// The rows of the jobs a stopped engine was running. A cancellation that had to
/// stop it cancels the jobs it covered; otherwise a started job is interrupted
/// (the started photos of a batch first run again alone) and a batch photo that
/// never started stays queued for the next engine.
fn settle_stopped_jobs(
    database: &crate::database::Database,
    active: &[String],
    forced: &[String],
    interruption: &str,
) {
    // When an engine running a batch stops on its own, any photo it had started,
    // or the one it was decoding ahead, may have caused it: the started ones run
    // again alone, once (a batch never holds a photo marked to run alone), so a
    // repeat stop names the photo. A photo that stops the engine alone is
    // interrupted.
    let batch = forced.is_empty() && active.len() > 1;
    for id in active {
        let _ = if batch {
            // A photo the user was already cancelling is interrupted instead.
            database
                .requeue_alone(id)
                .and_then(|()| database.interrupt_active_job(id, interruption))
        } else if forced.contains(id) {
            database.finish_job(
                id,
                "cancelled",
                "",
                "Cancelled by the user; inference engine restarted.",
            )
        } else {
            database.interrupt_active_job(id, interruption)
        };
    }
}

fn supervisor_should_return(worker: &crate::state::WorkerControl, generation: u64) -> bool {
    if worker.generation.load(Ordering::SeqCst) != generation {
        return true;
    }
    if worker.shutting_down.load(Ordering::SeqCst) {
        if let Err(error) =
            lock(&worker.cancellation).and_then(|mut cancellation| cancellation.finish())
        {
            eprintln!("LocalSR could not clean up job scratch: {error}");
        }
        return true;
    }
    false
}

pub fn shutdown(state: &AppState) {
    state.video_preview.shutdown();
    state.worker.shutting_down.store(true, Ordering::SeqCst);
    let _ = send(state, &json!({"type": "shutdown_request", "data": {}}));
    if let Ok(mut sender) = state.worker.sender.lock() {
        *sender = None;
    }
}

fn handle_worker_envelope(
    state: &Arc<AppState>,
    app: &AppHandle,
    envelope: WorkerEnvelope,
    generation: u64,
) {
    let Ok(mut cancellation) = state.worker.cancellation.lock() else {
        return;
    };
    if cancellation.resetting || state.worker.generation.load(Ordering::SeqCst) != generation {
        return;
    }
    let job_id = string(&envelope.data, "job_id");
    let terminal = matches!(
        envelope.message_type.as_str(),
        "job_completed"
            | "video_job_completed"
            | "job_cancelled"
            | "job_failed"
            | "benchmark_completed"
            | "benchmark_cancelled"
            | "benchmark_failed"
    );
    // Once cancelling, late progress must not restore "Enhancing" or enable
    // controls. Measurements may still update while the GPU drains.
    if cancellation.is_cancelling(&job_id) && !terminal && envelope.message_type != "video_memory" {
        return;
    }
    // A photo saved off screen needs no full preview: nothing would show it.
    let background = state
        .runtime
        .lock()
        .map(|runtime| is_background_photo(&runtime, &job_id))
        .unwrap_or(false);
    let envelope = match refuse_wrong_result(state, &envelope) {
        Ok(Some(failure)) => {
            eprintln!("LocalSR failed a job: the engine reported a result the app did not ask for");
            failure
        }
        _ => envelope,
    };
    let result = accept_envelope(state, &envelope);
    if matches!(result, Ok(false)) {
        return;
    }
    let result = result.map(|_| ());
    if terminal && cancellation.job_ids.contains(&job_id) {
        if let Err(error) = cancellation.finish_member(&job_id) {
            if let Ok(mut runtime) = state.runtime.lock() {
                runtime.status_detail =
                    format!("Work stopped. Temporary output cleanup failed: {error}");
            }
        }
    }
    drop(cancellation);
    if let Err(error) = result {
        set_worker_failure(state, app, error.to_string());
        return;
    }
    // Apply native state first so a frontend callback can immediately issue a
    // command against the state represented by this envelope.
    let _ = app.emit("worker-message", envelope.clone());
    if should_emit_state_changed(&envelope.message_type) {
        emit_state_changed(app);
    }

    // Request the final full-image preview after updating native state.
    if let Some(request) = completion_preview_request(state, &envelope).filter(|_| !background) {
        if let Err(error) = send(state, &request) {
            if cfg!(debug_assertions) {
                eprintln!("Could not request completed preview: {error}");
            }
        }
    }

    match envelope.message_type.as_str() {
        "job_completed" | "video_job_completed" => {
            if let Some(body) = finished_notice(state, &envelope.data) {
                let _ = app
                    .notification()
                    .builder()
                    .title("LocalSR finished")
                    .body(body)
                    .show();
            }
        }
        "job_failed" => {
            let _ = app
                .notification()
                .builder()
                .title("LocalSR could not finish")
                .body("Open LocalSR to review the error and diagnostics.")
                .show();
        }
        _ => {}
    }

    if envelope.message_type == "engine_info" {
        if let Err(error) = dispatch_next(state, app) {
            set_worker_failure(state, app, error.to_string());
        }
    }
    if matches!(
        envelope.message_type.as_str(),
        "job_completed"
            | "video_job_completed"
            | "job_cancelled"
            | "job_failed"
            | "benchmark_completed"
            | "benchmark_cancelled"
            | "benchmark_failed"
    ) {
        if let Err(error) = dispatch_next(state, app) {
            set_worker_failure(state, app, error.to_string());
        }
    }
}

/// A worker that lost its GPU device exits after its failure message; until its
/// replacement is ready, no queued job may be sent to it.
/// An engine that has exited takes no more work, even while its last messages are read:
/// a completion read then must not send the next job to it. A requested restart keeps
/// its state, which tells the exit handler that the stop was expected.
fn mark_exited(runtime: &mut RuntimeStatus) {
    if runtime.worker != "restarting" {
        runtime.worker = "unavailable".into();
    }
}

/// The longest line the engine may send. A line must fit the largest message, a
/// Compare crop: a PNG of at most 2048 x 2048 pixels (12.6 MB when the pixels are
/// noise), in base64 (16.8 MB), inside its JSON. Previews, thumbnails and tiles are
/// JPEGs of at most 2048 pixels on the long side and stay far below that. The limit
/// leaves about twice the largest message and still keeps the host's memory bounded.
pub(crate) const MAX_ENGINE_LINE_BYTES: usize = 32 * 1024 * 1024;

/// The longest engine stderr line kept. Stderr is a diagnostic stream: the start of a
/// line is enough, and the rest is dropped as it arrives.
const MAX_ENGINE_STDERR_LINE_BYTES: usize = 64 * 1024;

/// What the line buffer keeps between messages; a larger one is released.
const READ_BUFFER_KEEP_BYTES: usize = 1024 * 1024;

/// How long the app reads an exited engine's remaining output before moving on.
const OUTPUT_DRAIN_LIMIT: Duration = Duration::from_secs(2);

/// How long an engine that asked for a restart may take to exit before it is stopped.
const RESTART_EXIT_GRACE: Duration = Duration::from_secs(15);

/// Whether an engine that ended on its own died of a fault in native code, such as an
/// access violation in a graphics driver. A kill (the OOM killer, Task Manager) or an
/// ordinary failing exit is not one: the engine then cannot blame the GPU it was using.
fn engine_crashed(status: &std::io::Result<std::process::ExitStatus>) -> bool {
    let Ok(exit) = status else {
        return false;
    };
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;

        // SIGILL, SIGABRT, SIGFPE, SIGSEGV and SIGBUS (7 on Linux, 10 on macOS).
        let bus = if cfg!(target_os = "linux") { 7 } else { 10 };
        exit.signal()
            .is_some_and(|signal| [4, 6, 8, 11, bus].contains(&signal))
    }
    #[cfg(windows)]
    {
        // NTSTATUS error codes (0xC0000005 access violation, 0xC0000409 fast fail, ...)
        // and 3, the exit code of abort().
        exit.code()
            .is_some_and(|code| code == 3 || code as u32 >= 0xC000_0000)
    }
}

/// An engine that stayed up this long, or finished a job, did work: its stopping
/// starts the count of failed starts over.
/// The title while frames of a video are enhanced; every message of that stage uses it, so
/// the status line does not alternate between two wordings.
const VIDEO_FRAMES_TITLE: &str = "Enhancing video";

const STABLE_RUN: Duration = Duration::from_secs(60);
/// Failed starts in a row after which the host gives up and shows the failure.
const MAX_FAILED_STARTS: u32 = 3;

/// How long to wait before starting the engine again after it stopped, or None when
/// it has failed too often. A start counts as failed when the engine stopped on its
/// own; its handshake does not clear the count, only a stable run or a finished job.
/// A stop the host asked for restarts at once, after the first delay.
fn restart_delay(
    failed_starts: &AtomicU32,
    ran: Duration,
    finished_job: bool,
    failed: bool,
) -> Option<Duration> {
    if ran >= STABLE_RUN || finished_job {
        failed_starts.store(0, Ordering::SeqCst);
    }
    let attempt = if failed {
        failed_starts.fetch_add(1, Ordering::SeqCst) + 1
    } else {
        1
    };
    (attempt <= MAX_FAILED_STARTS).then(|| Duration::from_secs(u64::from(attempt)))
}

fn restart_overdue(restarting: bool, since: &mut Option<Instant>, now: Instant) -> bool {
    if !restarting {
        *since = None;
        return false;
    }
    now.duration_since(*since.get_or_insert(now)) >= RESTART_EXIT_GRACE
}

fn hold_until_restart(runtime: &mut RuntimeStatus, data: &Value) {
    if data.get("restart_required").and_then(Value::as_bool) == Some(true) {
        runtime.worker = "restarting".into();
    }
}

/// Whether an envelope belongs to a photo of the running batch that is not on screen.
fn is_background_photo(runtime: &RuntimeStatus, id: &str) -> bool {
    !id.is_empty()
        && id != runtime.active_job_id
        && runtime.active_batch_job_ids.iter().any(|m| m == id)
}

/// Apply an engine message, unless it reports on work the host never sent. False
/// when it was ignored: nothing more may be done with it.
fn accept_envelope(state: &Arc<AppState>, envelope: &WorkerEnvelope) -> AppResult<bool> {
    if let Some(reason) = rejection(state, envelope)? {
        eprintln!("LocalSR ignored an engine message: {reason}");
        return Ok(false);
    }
    apply_worker_envelope(state, envelope)?;
    if matches!(
        envelope.message_type.as_str(),
        "job_completed" | "video_job_completed"
    ) {
        state.worker.finished_job.store(true, Ordering::SeqCst);
    }
    Ok(true)
}

/// The failure that settles a completion naming a job the host dispatched, still
/// active, with a result path the host did not ask for. Left alone the job would stay
/// active and hold up the queue; the engine's state can no longer be trusted, so it is
/// restarted. Messages about unknown or ended jobs get None and stay ignored.
fn refuse_wrong_result(
    state: &AppState,
    envelope: &WorkerEnvelope,
) -> AppResult<Option<WorkerEnvelope>> {
    if stale_reason(state, envelope)?.is_some() || wrong_output(state, envelope)?.is_none() {
        return Ok(None);
    }
    Ok(Some(WorkerEnvelope {
        message_type: "job_failed".into(),
        data: json!({
            "job_id": string(&envelope.data, "job_id"),
            "error_message": "The engine reported a result the app did not ask for.",
            "restart_required": true,
        }),
    }))
}

/// Messages that report on one job or benchmark. The engine only ever sends them
/// for work the host sent it.
fn is_job_scoped(message_type: &str) -> bool {
    matches!(
        message_type,
        "job_started"
            | "stage_started"
            | "stage_progress"
            | "stage_completed"
            | "progress"
            | "tile_update"
            | "live_preview_frame"
            | "live_preview_warning"
            | "engine_status"
            | "job_completed"
            | "video_job_completed"
            | "job_cancelled"
            | "job_failed"
            | "video_memory"
            | "video_stage_progress"
            | "video_frame_started"
            | "video_frame_completed"
            | "video_tile_progress"
            | "benchmark_started"
            | "benchmark_stage_started"
            | "benchmark_stage_progress"
            | "benchmark_stage_completed"
            | "benchmark_progress"
            | "benchmark_completed"
            | "benchmark_cancelled"
            | "benchmark_failed"
    )
}

/// Why a message must be ignored, or None when the host may act on it: it has to
/// name a job the host dispatched and that has not ended, and a completion has to
/// report the output file the host asked for. A job's id and result path decide
/// what the host records, previews and later lets the user open, so neither is
/// taken from the engine's word alone.
fn rejection(state: &AppState, envelope: &WorkerEnvelope) -> AppResult<Option<String>> {
    match stale_reason(state, envelope)? {
        Some(reason) => Ok(Some(reason)),
        None => wrong_output(state, envelope),
    }
}

/// A job-scoped message that names no job the host dispatched, or one that ended.
fn stale_reason(state: &AppState, envelope: &WorkerEnvelope) -> AppResult<Option<String>> {
    let kind = envelope.message_type.as_str();
    if !is_job_scoped(kind) {
        return Ok(None);
    }
    let id = string(&envelope.data, "job_id");
    let dispatched = {
        let runtime = lock(&state.runtime)?;
        !id.is_empty()
            && (runtime.active_job_id == id || runtime.active_batch_job_ids.contains(&id))
    };
    if !dispatched {
        let shown: String = id.chars().take(64).collect();
        return Ok(Some(format!(
            "{kind} for job {shown:?}, which the host has not dispatched or has already ended"
        )));
    }
    if lock(&state.database)?.job_has_ended(&id)? {
        let shown: String = id.chars().take(64).collect();
        return Ok(Some(format!(
            "{kind} for job {shown:?}, which already ended"
        )));
    }
    Ok(None)
}

/// A completion that reports another file than the output the host requested.
fn wrong_output(state: &AppState, envelope: &WorkerEnvelope) -> AppResult<Option<String>> {
    let kind = envelope.message_type.as_str();
    if !matches!(kind, "job_completed" | "video_job_completed") {
        return Ok(None);
    }
    let id = string(&envelope.data, "job_id");
    let reported = string(&envelope.data, "output_path");
    let requested = lock(&state.database)?.requested_output(&id)?;
    // The engine writes the file the request names and reports that same path.
    if requested.as_deref().map(Path::new) == Some(Path::new(&reported)) {
        return Ok(None);
    }
    let shown: String = reported.chars().take(256).collect();
    Ok(Some(format!(
        "{kind} for job {id} reports {shown:?}, which is not the output the host requested"
    )))
}

fn apply_worker_envelope(state: &Arc<AppState>, envelope: &WorkerEnvelope) -> AppResult<()> {
    let id = string(&envelope.data, "job_id");
    let (member, background) = {
        let runtime = lock(&state.runtime)?;
        (
            runtime.active_batch_job_ids.contains(&id),
            is_background_photo(&runtime, &id),
        )
    };
    if !member {
        return apply_job_envelope(state, envelope);
    }
    // A photo that starts is the one the window shows from then on.
    if background && envelope.message_type != "job_started" {
        apply_background_photo(state, envelope)?;
    } else {
        apply_job_envelope(state, envelope)?;
    }
    if matches!(
        envelope.message_type.as_str(),
        "job_completed" | "job_failed" | "job_cancelled"
    ) {
        let failed = envelope.message_type == "job_failed";
        leave_batch(&mut *lock(&state.runtime)?, &id, failed);
    }
    Ok(())
}

/// A batch photo off screen (saving, or not started yet) updates its own row only;
/// the shown photo keeps its progress, status and preview.
fn apply_background_photo(state: &Arc<AppState>, envelope: &WorkerEnvelope) -> AppResult<()> {
    let data = &envelope.data;
    let id = string(data, "job_id");
    match envelope.message_type.as_str() {
        "progress" => {
            lock(&state.database)?.update_job_progress(&id, percentage(data, "percentage"))?;
        }
        "job_completed" => {
            let output = string(data, "output_path");
            let database = lock(&state.database)?;
            database.finish_job(&id, "completed", &output, &string(data, "notes"))?;
            database.record_job_duration(&id, seconds(data, "elapsed_seconds"))?;
            drop(database);
            let mut runtime = lock(&state.runtime)?;
            // The result shown after the batch belongs to last_output_path.
            runtime.last_output_path = output;
            runtime.result_preview_data_url.clear();
        }
        "job_failed" => {
            lock(&state.database)?.finish_job(&id, "failed", "", &string(data, "error_message"))?;
            hold_until_restart(&mut *lock(&state.runtime)?, data);
        }
        "job_cancelled" => {
            lock(&state.database)?.finish_job(&id, "cancelled", "", "Cancelled by the user.")?;
        }
        _ => {}
    }
    Ok(())
}

/// A batch photo has ended. While others remain, the batch stays active: when the
/// shown photo ended before the next one started, the window shows the batch.
fn leave_batch(runtime: &mut RuntimeStatus, id: &str, failed: bool) {
    runtime.active_batch_job_ids.retain(|member| member != id);
    let Some(next) = runtime.active_batch_job_ids.first().cloned() else {
        return;
    };
    if !runtime.active_job_id.is_empty() && runtime.active_job_id != id {
        return;
    }
    runtime.active_job_id = next;
    runtime.progress = 0.0;
    runtime.elapsed_seconds = 0.0;
    runtime.estimated_remaining_seconds = 0.0;
    runtime.throughput = 0.0;
    runtime.result_preview_data_url.clear();
    // A failure stays on the status line until the next photo starts.
    if runtime.worker != "restarting" && !failed {
        runtime.status_title = "Processing".into();
        runtime.status_detail = "Finishing the other photos of this batch.".into();
    }
}

fn apply_job_envelope(state: &Arc<AppState>, envelope: &WorkerEnvelope) -> AppResult<()> {
    let data = &envelope.data;
    match envelope.message_type.as_str() {
        "worker_ready" => {
            {
                let mut runtime = lock(&state.runtime)?;
                runtime.worker = "negotiating".into();
                runtime.status_title = "Checking engine".into();
                runtime.status_detail = "Negotiating a compatible inference protocol.".into();
            }
            send(
                state,
                &json!({
                    "type": "handshake_request",
                    "data": {
                        "client_name": "LocalSR",
                        "client_version": env!("CARGO_PKG_VERSION"),
                        "protocol_version": PROTOCOL_VERSION
                    }
                }),
            )?;
        }
        "engine_info" => {
            let info: EngineInfo = serde_json::from_value(data.clone())?;
            if info.minimum_protocol_version > PROTOCOL_VERSION
                || info.protocol_version < PROTOCOL_VERSION
            {
                return Err(AppError::Worker(format!(
                    "inference protocol mismatch: app {}, engine {}–{}",
                    PROTOCOL_VERSION, info.minimum_protocol_version, info.protocol_version
                )));
            }
            *lock(&state.engine)? = Some(info);
            {
                let mut runtime = lock(&state.runtime)?;
                runtime.worker = "ready".into();
                runtime.status_title = "Ready".into();
                runtime.status_detail = "The isolated inference engine is ready.".into();
            }
            send(state, &json!({"type": "capabilities_request", "data": {}}))?;
            for path in lock(&state.database)?.pending_probe_paths()? {
                send(state, &media_probe_request(state, &path))?;
            }
        }
        "capabilities_info" => {
            let capabilities = serde_json::from_value::<CapabilityInfo>(data.clone())?;
            if state.choose_device.swap(false, Ordering::SeqCst) {
                if let Some(device) = first_run_device(&capabilities.devices) {
                    let mut settings = lock(&state.settings)?;
                    if settings.device_id == "cpu" {
                        settings.device_id = device;
                        drop(settings);
                        if let Err(error) = state.persist_settings() {
                            eprintln!("Could not save the first-run device: {error}");
                        }
                    }
                }
            }
            state.programs.grant_reported(&capabilities.media);
            *lock(&state.capabilities)? = capabilities;
        }
        "media_info" => {
            let path = string(data, "media_path");
            let encoded = string(data, "jpeg_base64");
            let preview = if encoded.is_empty() {
                String::new()
            } else {
                format!("data:image/jpeg;base64,{encoded}")
            };
            lock(&state.database)?.update_media_probe(
                &path,
                &string(data, "media_kind"),
                count32(data, "width"),
                count32(data, "height"),
                integer(data, "frame_count"),
                number(data, "fps"),
                number(data, "duration_seconds"),
                &preview,
                &string(data, "hdr_format"),
                &string(data, "audio_warning"),
            )?;
            if lock(&state.database)?.get_media_by_path(&path)?.is_none() && !preview.is_empty() {
                let mut runtime = lock(&state.runtime)?;
                if runtime.active_job_id.is_empty() && runtime.last_output_path == path {
                    runtime.result_preview_data_url = preview;
                }
            }
        }
        "media_probe_failed" => {
            lock(&state.database)?
                .fail_media_probe(&string(data, "media_path"), &string(data, "error_message"))?;
        }
        "region_ready" | "region_failed" => {
            let database = lock(&state.database)?;
            let mut runtime = lock(&state.runtime)?;
            crate::compare::apply_region(
                &database,
                &mut runtime.compare,
                data,
                envelope.message_type == "region_failed",
            )?;
        }
        "preview_ready" => {
            {
                let database = lock(&state.database)?;
                let mut runtime = lock(&state.runtime)?;
                crate::compare::apply(&database, &mut runtime.compare, data, false)?;
            }
            apply_pair_preview(&mut *lock(&state.runtime)?, data, false);
            let path = string(data, "image_path");
            let encoded = string(data, "jpeg_base64");
            if !encoded.is_empty() {
                let mut runtime = lock(&state.runtime)?;
                // A selection without pair slots (the fallback when the
                // result history is unavailable) is matched by its path.
                if string(data, "slot").is_empty() && runtime.comparison_output_path == path {
                    runtime.comparison_preview_data_url =
                        format!("data:image/jpeg;base64,{encoded}");
                    runtime.comparison_error.clear();
                }
                if runtime.active_job_id.is_empty() && runtime.last_output_path == path {
                    runtime.result_preview_data_url = format!("data:image/jpeg;base64,{encoded}");
                }
            }
        }
        "preview_failed" => {
            {
                let database = lock(&state.database)?;
                let mut runtime = lock(&state.runtime)?;
                crate::compare::apply(&database, &mut runtime.compare, data, true)?;
            }
            let mut runtime = lock(&state.runtime)?;
            apply_pair_preview(&mut runtime, data, true);
            if string(data, "slot").is_empty()
                && runtime.comparison_output_path == string(data, "image_path")
            {
                runtime.comparison_preview_data_url.clear();
                runtime.comparison_error = string(data, "error_message");
            }
        }
        "job_started" => {
            let id = string(data, "job_id");
            lock(&state.database)?.set_job_status(&id, "running")?;
            let mut runtime = lock(&state.runtime)?;
            runtime.begin_active_job(id);
            runtime.video_memory = None;
            runtime.status_title = "Processing".into();
            runtime.status_detail = "Preparing the selected model.".into();
            runtime.elapsed_seconds = 0.0;
            runtime.estimated_remaining_seconds = 0.0;
            runtime.throughput = 0.0;
            runtime.throughput_unit = "tiles/s".into();
            runtime.active_tile_size = 0;
            runtime.result_preview_data_url.clear();
        }
        "engine_status" => {
            // The engine's word on the hardware route. The next progress tick replaces the
            // detail line, so the note keeps it for the rest of the job.
            let mut runtime = lock(&state.runtime)?;
            if !runtime.active_job_id.is_empty() && runtime.active_job_id == string(data, "job_id")
            {
                runtime.status_detail = string(data, "message");
                runtime.hardware_note = runtime.status_detail.clone();
            }
        }
        "stage_started" => {
            let stage = stage_label(&string(data, "stage_kind"));
            let index = integer(data, "stage_index").saturating_add(1);
            let count = integer(data, "stage_count");
            let mut runtime = lock(&state.runtime)?;
            runtime.status_title = format!("{stage} · stage {index}/{count}");
            runtime.status_detail = format!("Preparing {}.", string(data, "model_id"));
        }
        "stage_completed" => {
            let stage = stage_label(&string(data, "stage_kind"));
            let mut runtime = lock(&state.runtime)?;
            runtime.status_detail = format!("{stage} stage complete.");
        }
        "progress" => {
            let id = string(data, "job_id");
            let progress = percentage(data, "percentage");
            lock(&state.database)?.update_job_progress(&id, progress)?;
            let mut runtime = lock(&state.runtime)?;
            runtime.progress = progress;
            let editing = string(data, "unit") == "steps";
            // In a job of several stages the title keeps which one runs (two fixes can
            // both be "Restoring"); a one-stage job reads "Enhancing".
            let staged = runtime.status_title.contains(" · stage ")
                && !runtime.status_title.ends_with(" 1/1");
            if editing {
                runtime.status_title = "Editing".into();
            } else if !staged {
                runtime.status_title = "Enhancing".into();
            }
            let remaining = seconds(data, "estimated_remaining_seconds");
            // Editing spends most of its time outside the sampling steps; the
            // worker then names the phase it is in.
            let detail = string(data, "detail");
            runtime.status_detail = if detail.is_empty() {
                format!(
                    "{} of {} {}{}",
                    integer(data, "completed_tiles"),
                    integer(data, "total_tiles"),
                    if editing { "steps" } else { "tiles" },
                    eta_suffix(remaining)
                )
            } else {
                detail
            };
            runtime.elapsed_seconds = seconds(data, "elapsed_seconds");
            runtime.estimated_remaining_seconds = seconds(data, "estimated_remaining_seconds");
            let completed = integer(data, "completed_tiles") as f64;
            runtime.throughput = if runtime.elapsed_seconds > 0.0 {
                completed / runtime.elapsed_seconds
            } else {
                0.0
            };
            runtime.throughput_unit = if editing { "steps/s" } else { "tiles/s" }.into();
            runtime.active_tile_size = count32(data, "active_tile_size");
            runtime.device_free_memory = integer(data, "device_free_memory");
            runtime.device_allocated_memory = integer(data, "device_allocated_memory");
            runtime.live_system_ram_available = integer(data, "system_ram_available");
            runtime.live_memory_pressure_percent = number(data, "system_memory_pressure_percent");
        }
        // Tile JPEGs are coordinates plus pixels, not complete images. The
        // webview composites them into its bounded live canvas.
        "tile_update" => {}
        "video_memory" => {
            let mut runtime = lock(&state.runtime)?;
            if runtime.active_job_id == string(data, "job_id") {
                runtime.video_memory = Some(data.clone());
                if data
                    .get("gpu_sample_available")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
                {
                    runtime.device_free_memory = integer(data, "device_free_memory");
                    runtime.device_allocated_memory = integer(data, "device_allocated_memory");
                }
                runtime.live_system_ram_available = integer(data, "system_ram_available");
            }
        }
        "video_stage_progress" => {
            let mut runtime = lock(&state.runtime)?;
            if runtime.active_job_id == string(data, "job_id") {
                runtime.status_title = video_stage_title(&string(data, "stage")).into();
                let frames_done = number(data, "frames_processed");
                let frames_total = number(data, "total_frames");
                if frames_total > 0.0 {
                    runtime.progress = (frames_done / frames_total * 100.0).clamp(0.0, 100.0);
                }
                runtime.estimated_remaining_seconds = seconds(data, "estimated_remaining_seconds");
                let steps = if integer(data, "total") > 0 {
                    format!(
                        "{} / {} · ",
                        integer(data, "completed"),
                        integer(data, "total")
                    )
                } else {
                    String::new()
                };
                let frames = if integer(data, "total_frames") > 0 {
                    format!(
                        "From frame {} of {} · ",
                        integer(data, "frame_index").saturating_add(1),
                        integer(data, "total_frames")
                    )
                } else {
                    String::new()
                };
                let eta = if runtime.estimated_remaining_seconds > 0.0 {
                    eta_suffix(runtime.estimated_remaining_seconds)
                        .trim_start_matches(" · ")
                        .to_string()
                } else {
                    "ETA: measuring first clip…".into()
                };
                runtime.status_detail = format!("{steps}{frames}{eta}");
                if string(data, "stage") == "saving" {
                    runtime.status_detail = "Finalizing video timing and audio…".into();
                }
                runtime.elapsed_seconds = seconds(data, "elapsed_seconds");
                runtime.throughput_unit = "frames/s".into();
            }
        }
        "video_frame_started" => {
            let mut runtime = lock(&state.runtime)?;
            runtime.status_title = VIDEO_FRAMES_TITLE.into();
            runtime.status_detail = format!(
                "Frame {} of {}{}",
                integer(data, "frame_index").saturating_add(1),
                integer(data, "total_frames"),
                if runtime.estimated_remaining_seconds > 0.0 {
                    eta_suffix(runtime.estimated_remaining_seconds)
                } else {
                    " · ETA: measuring first tile…".into()
                }
            );
        }
        "video_tile_progress" => {
            let frame = integer(data, "frame_index") as f64;
            let done = integer(data, "completed_tiles") as f64;
            let count = integer(data, "total_tiles") as f64;
            let total = integer(data, "total_frames") as f64;
            let fraction = frame + if count > 0.0 { done / count } else { 0.0 };
            let mut runtime = lock(&state.runtime)?;
            runtime.progress = if total > 0.0 {
                (fraction / total * 100.0).clamp(0.0, 100.0)
            } else {
                0.0
            };
            runtime.status_title = VIDEO_FRAMES_TITLE.into();
            runtime.elapsed_seconds = seconds(data, "elapsed_seconds");
            runtime.estimated_remaining_seconds = seconds(data, "estimated_remaining_seconds");
            runtime.active_tile_size = count32(data, "active_tile_size");
            runtime.status_detail = format!(
                "Frame {} of {} · Tile {} / {}{}",
                (frame as u64).saturating_add(1),
                total as u64,
                done as u64,
                count as u64,
                if runtime.estimated_remaining_seconds > 0.0 {
                    eta_suffix(runtime.estimated_remaining_seconds)
                } else if fraction == 0.0 {
                    " · ETA: measuring first tile…".into()
                } else {
                    " · Finishing frame".into()
                }
            );
        }
        "video_frame_completed" => {
            let id = string(data, "job_id");
            let completed = integer(data, "frames_processed") as f64;
            let total = integer(data, "total_frames") as f64;
            let progress = if total > 0.0 {
                (completed / total * 100.0).clamp(0.0, 100.0)
            } else {
                0.0
            };
            lock(&state.database)?.update_job_progress(&id, progress)?;
            let mut runtime = lock(&state.runtime)?;
            runtime.progress = progress;
            let elapsed = seconds(data, "elapsed_seconds");
            let remaining = seconds(data, "estimated_remaining_seconds");
            runtime.status_title = VIDEO_FRAMES_TITLE.into();
            runtime.status_detail = format!(
                "Frame {} of {}{}",
                integer(data, "frames_processed"),
                integer(data, "total_frames"),
                eta_suffix(remaining)
            );
            if completed > 0.0 && elapsed > 0.0 {
                runtime.elapsed_seconds = elapsed;
                runtime.estimated_remaining_seconds = remaining;
                runtime.throughput = completed / elapsed;
                runtime.throughput_unit = "frames/s".into();
            }
            let encoded = string(data, "jpeg_base64");
            if !encoded.is_empty() {
                runtime.result_preview_data_url = format!("data:image/jpeg;base64,{encoded}");
            }
        }
        "live_preview_frame" => {
            let id = string(data, "job_id");
            let encoded = string(data, "jpeg_base64");
            let mut runtime = lock(&state.runtime)?;
            if id == runtime.active_job_id
                && string(data, "preview_kind") == "video"
                && !encoded.is_empty()
            {
                runtime.result_preview_data_url = format!("data:image/jpeg;base64,{encoded}");
            }
        }
        "benchmark_started" => {
            let mut runtime = lock(&state.runtime)?;
            runtime.begin_active_job(string(data, "job_id"));
            let is_v2 = string(data, "workload_version").starts_with("localsr-benchmark-v2");
            runtime.status_title = "Benchmark running".into();
            runtime.status_detail = if is_v2 {
                format!(
                    "Benchmark · {} stages · warming up.",
                    integer(data, "measured_frame_count")
                )
            } else {
                format!(
                    "Warming up {} iteration(s), then measuring {} frames.",
                    integer(data, "warmup_count"),
                    integer(data, "measured_frame_count")
                )
            };
            runtime.progress = 0.0;
            runtime.estimated_remaining_seconds = 0.0;
            runtime.throughput = 0.0;
            runtime.throughput_unit = "frames/s".into();
        }
        "benchmark_progress" => {
            let stage = string(data, "stage");
            let mut runtime = lock(&state.runtime)?;
            runtime.progress = percentage(data, "percentage");
            runtime.status_title = "Benchmark running".into();
            runtime.status_detail = if stage.is_empty() {
                format!(
                    "Measured {} of {} frames.",
                    integer(data, "completed_frames"),
                    integer(data, "total_frames")
                )
            } else {
                format!(
                    "Phase {stage} · {} of {}.",
                    integer(data, "completed_frames"),
                    integer(data, "total_frames")
                )
            };
        }
        "benchmark_stage_started" | "benchmark_stage_progress" | "benchmark_stage_completed" => {
            let event = envelope.message_type.trim_start_matches("benchmark_stage_");
            let stage = string(data, "stage").replace(':', " · ");
            let completed_units = integer(data, "completed_units");
            let total_units = integer(data, "total_units");
            let mut runtime = lock(&state.runtime)?;
            runtime.progress = percentage(data, "percentage");
            runtime.status_title = "Benchmark running".into();
            runtime.status_detail = if event == "progress" && completed_units > 0 {
                if total_units > 0 {
                    format!("{stage} · iteration {completed_units} of {total_units}.")
                } else {
                    format!("{stage} · iteration {completed_units}.")
                }
            } else {
                format!("{stage} · {event}.")
            };
        }
        "benchmark_completed" => {
            let Ok(mut result) = serde_json::from_value::<BenchmarkResult>(data["result"].clone())
            else {
                // The engine is alive; only this answer is unusable.
                let mut runtime = lock(&state.runtime)?;
                runtime.take_active_job();
                runtime.progress = 0.0;
                runtime.status_title = "Benchmark failed".into();
                runtime.status_detail =
                    "The engine's benchmark result could not be read. Nothing was saved.".into();
                return Ok(());
            };
            result.retain_device_scores(
                lock(&state.latest_benchmark)?.as_ref(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs(),
            );
            settings::save_benchmark(&state.paths, &result)?;
            *lock(&state.latest_benchmark)? = Some(result.clone());
            let mut runtime = lock(&state.runtime)?;
            runtime.take_active_job();
            runtime.progress = 100.0;
            runtime.status_title = "Benchmark complete".into();
            runtime.status_detail = if result.is_v2() {
                if result.cv_percent.is_none() {
                    "Measurements saved. Too few repetitions to assess consistency on this device."
                        .into()
                } else if result.stable {
                    if let Some(system_score) = result.system_score {
                        format!(
                            "GPU score {:.2} output MP/s · stable ({:.1}% spread).",
                            system_score,
                            result.cv_percent.unwrap_or_default()
                        )
                    } else {
                        format!(
                            "CPU score {:.2} output MP/s · stable ({:.1}% spread).",
                            result.cpu_score.unwrap_or_default(),
                            result.cv_percent.unwrap_or_default()
                        )
                    }
                } else {
                    format!(
                        "Unstable run ({:.1}% spread) — close background apps and retry.",
                        result.cv_percent.unwrap_or_default()
                    )
                }
            } else {
                format!(
                    "Score {:.2} · {:.2} frames/s · local result saved.",
                    result.score, result.end_to_end_fps
                )
            };
            runtime.elapsed_seconds = result.total_elapsed_seconds;
            if result.is_v2() {
                runtime.throughput = result.system_score.or(result.cpu_score).unwrap_or_default();
                runtime.throughput_unit = "output MP/s".into();
                runtime.thermal_status = result.thermal_state.clone();
            } else {
                runtime.throughput = result.end_to_end_fps;
                runtime.throughput_unit = "frames/s".into();
            }
        }
        "benchmark_cancelled" => {
            let mut runtime = lock(&state.runtime)?;
            runtime.take_active_job();
            runtime.progress = 0.0;
            runtime.status_title = "Benchmark cancelled".into();
            runtime.status_detail = "No partial benchmark result was stored.".into();
        }
        "benchmark_failed" => {
            let mut runtime = lock(&state.runtime)?;
            runtime.take_active_job();
            hold_until_restart(&mut runtime, data);
            runtime.progress = 0.0;
            runtime.status_title = "Benchmark failed".into();
            runtime.status_detail = string(data, "error_message");
        }
        "job_completed" | "video_job_completed" => {
            let id = string(data, "job_id");
            let output = string(data, "output_path");
            // What the user should know about the finished job, such as a skipped fix;
            // kept with the job and on the status line.
            let notes = string(data, "notes");
            lock(&state.database)?.finish_job(&id, "completed", &output, &notes)?;
            lock(&state.database)?.record_job_duration(&id, seconds(data, "elapsed_seconds"))?;
            let mut runtime = lock(&state.runtime)?;
            runtime.take_active_job();
            // A video frame thumbnail is a live preview, not a completed
            // before/after result. The full-output request issued by the host
            // will populate this again only after it has decoded successfully.
            runtime.result_preview_data_url.clear();
            runtime.progress = 100.0;
            runtime.last_output_path = output;
            runtime.status_title = "Complete".into();
            runtime.status_detail = if notes.is_empty() {
                "The output was written successfully.".into()
            } else {
                notes
            };
            runtime.elapsed_seconds =
                seconds(data, "inference_seconds").max(seconds(data, "elapsed_seconds"));
            runtime.estimated_remaining_seconds = 0.0;
        }
        "job_cancelled" => {
            let id = string(data, "job_id");
            lock(&state.database)?.finish_job(&id, "cancelled", "", "Cancelled by the user.")?;
            let mut runtime = lock(&state.runtime)?;
            runtime.take_active_job();
            runtime.status_title = "Cancelled".into();
            runtime.status_detail = "Temporary output was cleaned up.".into();
            runtime.estimated_remaining_seconds = 0.0;
        }
        "job_failed" => {
            let id = string(data, "job_id");
            let error = string(data, "error_message");
            lock(&state.database)?.finish_job(&id, "failed", "", &error)?;
            let mut runtime = lock(&state.runtime)?;
            runtime.take_active_job();
            runtime.status_title = "Could not finish".into();
            runtime.status_detail = error;
            runtime.estimated_remaining_seconds = 0.0;
            hold_until_restart(&mut runtime, data);
        }
        "protocol_error" => {
            let mut runtime = lock(&state.runtime)?;
            runtime.worker = "failed".into();
            runtime.status_title = "Engine incompatible".into();
            runtime.status_detail = string(data, "message");
        }
        _ => {}
    }
    Ok(())
}

/// A side of the selected pair takes an answer only when the slot, the request
/// generation, the target and the decoded file all match. Late answers for an
/// earlier pair, another media or another side are dropped, cache hits too.
fn apply_pair_preview(runtime: &mut RuntimeStatus, data: &Value, failed: bool) {
    let pair = &mut runtime.comparison_pair;
    if data["generation"].as_u64() != Some(pair.generation) || pair.generation == 0 {
        return;
    }
    let slot = match data["slot"].as_str() {
        Some("showing") => &mut pair.showing,
        Some("against") => &mut pair.against,
        _ => return,
    };
    if slot.target_id == "original"
        || slot.target_id != string(data, "target_id")
        || slot.output_path != string(data, "image_path")
    {
        return;
    }
    if failed {
        slot.preview_data_url.clear();
        slot.error = string(data, "error_message");
        if slot.error.is_empty() {
            slot.error = "The preview could not be decoded. The saved file is unchanged.".into();
        }
        return;
    }
    let encoded = string(data, "jpeg_base64");
    if encoded.is_empty() {
        return;
    }
    slot.preview_data_url = format!("data:image/jpeg;base64,{encoded}");
    slot.error.clear();
    slot.width = u32::try_from(integer(data, "width")).unwrap_or_default();
    slot.height = u32::try_from(integer(data, "height")).unwrap_or_default();
    slot.preview_width = u32::try_from(integer(data, "preview_width")).unwrap_or_default();
    slot.preview_height = u32::try_from(integer(data, "preview_height")).unwrap_or_default();
}

/// A media probe carries the user's optional FFmpeg for formats LocalSR omits.
pub(crate) fn media_probe_request(state: &AppState, path: &str) -> serde_json::Value {
    let external = state
        .settings
        .lock()
        .map(|settings| settings.external_ffmpeg_path.clone())
        .unwrap_or_default();
    json!({
        "type": "media_probe_request",
        "data": {"media_path": path, "max_dimension": 2048, "external_ffmpeg": external}
    })
}

pub(crate) fn codec_helper_command(state: &AppState, app: &AppHandle) -> AppResult<Command> {
    let specification = resolve_worker_command(state, app)?;
    let mut command = Command::new(specification.program);
    command.args(specification.arguments);
    if let Ok(settings) = state.settings.lock() {
        if !settings.external_ffmpeg_path.is_empty() {
            command.env("LOCALSR_EXTERNAL_FFMPEG", &settings.external_ffmpeg_path);
        }
    }
    if let Some(directory) = specification.working_directory {
        command.current_dir(directory);
    }
    if let Some(path) = specification.python_path {
        command.env("PYTHONPATH", path);
        command.env("PYTHONUTF8", "1");
    }
    command.env("PYTHONUNBUFFERED", "1");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    Ok(command)
}

pub fn installed_worker_path(state: &AppState, app: &AppHandle) -> AppResult<PathBuf> {
    Ok(resolve_worker_command(state, app)?.program)
}

fn resolve_worker_command(state: &AppState, app: &AppHandle) -> AppResult<WorkerCommand> {
    #[cfg(debug_assertions)]
    {
        if let Some(path) =
            env::var_os("LOCALSR_WORKER").filter(|_| !crate::distribution::managed_by_store())
        {
            let path = PathBuf::from(path);
            if path.is_file() {
                return Ok(WorkerCommand {
                    program: path,
                    arguments: Vec::new(),
                    working_directory: None,
                    python_path: None,
                });
            }
            return Err(AppError::Worker(
                "LOCALSR_WORKER does not point to a file".into(),
            ));
        }
    }
    // Prefer an installed engine after any explicit development worker; otherwise
    // fall back to bundled resources or Python sources.
    if let Some(path) = crate::updates::active_engine(&state.paths) {
        return Ok(WorkerCommand {
            program: path,
            arguments: Vec::new(),
            working_directory: None,
            python_path: None,
        });
    }

    let executable_name = if cfg!(windows) {
        "localsr-worker.exe"
    } else {
        "localsr-worker"
    };
    let mut candidates = Vec::new();
    if let Ok(resources) = app.path().resource_dir() {
        candidates.push(resources.join("engine").join(executable_name));
        candidates.push(resources.join("localsr-worker").join(executable_name));
    }
    if let Ok(executable) = env::current_exe() {
        if let Some(parent) = executable.parent() {
            candidates.push(parent.join("engine").join(executable_name));
            candidates.push(parent.join(executable_name));
        }
    }
    if let Some(path) = candidates.into_iter().find(|path| path.is_file()) {
        return Ok(WorkerCommand {
            program: path,
            arguments: Vec::new(),
            working_directory: None,
            python_path: None,
        });
    }

    #[cfg(not(debug_assertions))]
    return Err(AppError::Worker(
        "the packaged application is missing its bundled inference engine".into(),
    ));

    #[cfg(debug_assertions)]
    {
        if crate::distribution::managed_by_store() {
            return Err(AppError::Worker(
                "the Store application is missing its bundled inference engine".into(),
            ));
        }
        let python_candidates = if cfg!(windows) {
            vec![
                state
                    .paths
                    .repo_root
                    .join(".venv")
                    .join("Scripts")
                    .join("python.exe"),
                state
                    .paths
                    .repo_root
                    .join(".venv311")
                    .join("Scripts")
                    .join("python.exe"),
            ]
        } else {
            vec![
                state
                    .paths
                    .repo_root
                    .join(".venv")
                    .join("bin")
                    .join("python"),
                state
                    .paths
                    .repo_root
                    .join(".venv311")
                    .join("bin")
                    .join("python"),
            ]
        };
        let program = python_candidates
            .into_iter()
            .find(|path| path.is_file())
            .unwrap_or_else(|| PathBuf::from(if cfg!(windows) { "python" } else { "python3" }));
        let source = state.paths.repo_root.join("src");
        if !source.is_dir() {
            return Err(AppError::Worker(
                "the bundled inference engine is missing; rebuild the desktop package".into(),
            ));
        }
        Ok(WorkerCommand {
            program,
            arguments: vec!["-u".into(), "-m".into(), "localsr.worker".into()],
            working_directory: Some(state.paths.repo_root.clone()),
            python_path: Some(source),
        })
    }
}

fn set_worker_failure(state: &AppState, app: &AppHandle, detail: String) {
    if let Ok(mut runtime) = state.runtime.lock() {
        runtime.worker = "failed".into();
        runtime.status_title = "Engine unavailable".into();
        runtime.status_detail = detail;
        runtime.engine_retry = false;
    }
    emit_state_changed(app);
}

/// The restart limit is reached: the failure stays, and the user may try again.
fn give_up_on_engine(state: &AppState, app: &AppHandle, detail: String) {
    if let Ok(mut runtime) = state.runtime.lock() {
        runtime.worker = "failed".into();
        runtime.status_title = "Engine unavailable".into();
        runtime.status_detail = detail;
        runtime.engine_retry = true;
    }
    emit_state_changed(app);
}

/// Accepts a retry only after the host gave up restarting the engine: resets the
/// restart count and takes the offer back, so a second call is refused until it
/// gives up again.
fn begin_engine_retry(state: &AppState) -> AppResult<()> {
    let mut runtime = lock(&state.runtime)?;
    if runtime.worker != "failed" || !runtime.engine_retry {
        return Err(AppError::Worker(
            "the inference engine is not waiting to be tried again".into(),
        ));
    }
    runtime.engine_retry = false;
    state.worker.restart_count.store(0, Ordering::SeqCst);
    Ok(())
}

/// Starts the engine again after the host gave up on it. It runs through the normal
/// start, so a new failure takes the usual attempts and ends here again.
pub fn retry_engine(state: &Arc<AppState>, app: &AppHandle) -> AppResult<()> {
    begin_engine_retry(state)?;
    if let Err(error) = start_worker(state.clone(), app.clone()) {
        give_up_on_engine(state, app, error.to_string());
        return Err(error);
    }
    Ok(())
}

fn string(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn integer(value: &Value, key: &str) -> u64 {
    value.get(key).and_then(Value::as_u64).unwrap_or_default()
}

fn number(value: &Value, key: &str) -> f64 {
    value.get(key).and_then(Value::as_f64).unwrap_or_default()
}

/// A percentage the engine reported, kept within 0 to 100 for the progress bar.
fn percentage(value: &Value, key: &str) -> f64 {
    number(value, key).clamp(0.0, 100.0)
}

/// A time span the engine reported; a negative one counts as none.
fn seconds(value: &Value, key: &str) -> f64 {
    number(value, key).max(0.0)
}

/// A count that has to fit 32 bits, such as an image side; a larger one saturates
/// instead of wrapping around to a small number.
fn count32(value: &Value, key: &str) -> u32 {
    u32::try_from(integer(value, key)).unwrap_or(u32::MAX)
}

fn eta_suffix(seconds: f64) -> String {
    if !seconds.is_finite() || seconds <= 0.0 {
        return String::new();
    }
    let total = seconds.round() as u64;
    if total >= 86400 {
        format!(" · ETA ≈ {}d {}h", total / 86400, total % 86400 / 3600)
    } else if total >= 3600 {
        format!(" · ETA ≈ {}h {}m", total / 3600, total % 3600 / 60)
    } else if total >= 60 {
        format!(" · ETA {}:{:02}", total / 60, total % 60)
    } else {
        format!(" · ETA {total}s")
    }
}

fn worker_interruption_detail(exit_summary: &str, last_confirmed_stage: &str) -> String {
    let stage = last_confirmed_stage.trim();
    if stage.is_empty() || matches!(stage, "Ready" | "Starting" | "Checking engine") {
        format!("{exit_summary} before the worker confirmed a processing stage.")
    } else {
        format!("{exit_summary}. Last confirmed stage: {stage}.")
    }
}

fn video_stage_title(stage: &str) -> &'static str {
    match stage {
        "verifying_model" => "Checking model files",
        "external_decode" => "Converting the source with your FFmpeg",
        "external_encode" => "Encoding with your FFmpeg",
        "loading_model" => "Loading video model",
        "loading_interpolation" => "Loading frame interpolation",
        "reading_frames" => "Reading video frames",
        "preparing_clip" => "Preparing clip",
        "encoding" => "Encoding clip",
        "enhancing" => "Enhancing clip",
        "decoding" => "Decoding enhanced clip",
        "finishing" => "Finishing clip",
        "saving" => "Saving video",
        _ => "Processing video",
    }
}

fn stage_label(kind: &str) -> &'static str {
    match kind {
        "deblock" => "Removing JPEG artifacts",
        "restore" => "Restoring",
        "upscale" => "Upscaling",
        "face_restore" => "Restoring faces",
        "edit" => "Preparing edit",
        _ => "Processing",
    }
}

/// High-frequency inference messages are already delivered through the typed
/// `worker-message` event. Emitting a second full snapshot for every tile used
/// to make the webview copy and deserialize the entire catalog thousands of
/// times during one image, which could leave zoom and pan controls unresponsive.
fn should_emit_state_changed(message_type: &str) -> bool {
    !matches!(
        message_type,
        "progress"
            | "media_probe_progress"
            | "stage_progress"
            | "tile_update"
            | "video_frame_started"
            | "video_stage_progress"
            | "video_memory"
            | "video_frame_completed"
            | "benchmark_progress"
            | "benchmark_stage_started"
            | "benchmark_stage_progress"
            | "benchmark_stage_completed"
            | "benchmark_preview"
            | "benchmark_tile"
            | "live_preview_frame"
            | "video_tile_progress"
    )
}

/// One bound for every result preview, the automatic one after a job and both
/// sides of a comparison, so a finished result is decoded once and then served
/// from the decoder's cache.
pub const RESULT_PREVIEW_MAX_DIMENSION: u32 = 2048;

/// The text of the "finished" notice, or None while more work is queued: the notice says
/// LocalSR is done, which a photo of a long batch is not.
fn finished_notice(state: &AppState, data: &Value) -> Option<&'static str> {
    let more_work = state
        .database
        .lock()
        .ok()
        .and_then(|database| database.has_inflight_jobs().ok())
        .unwrap_or(false);
    if more_work {
        return None;
    }
    Some(if string(data, "notes").is_empty() {
        "Your enhanced media is ready."
    } else {
        "Your enhanced media is ready. A fix was skipped; open LocalSR for details."
    })
}

/// A completed comparison must come from the full output file, never from the
/// last progressive tile or video frame. Keep this request in the native host
/// so it cannot race the completion state applied above.
fn completion_preview_request(state: &AppState, envelope: &WorkerEnvelope) -> Option<Value> {
    let output_path = string(&envelope.data, "output_path");
    // A result that is a link could show another file's pixels.
    if output_path.is_empty()
        || crate::results::ensure_not_linked(std::path::Path::new(&output_path)).is_err()
    {
        return None;
    }
    match envelope.message_type.as_str() {
        "job_completed" => Some(json!({
            "type": "preview_request",
            "data": {"image_path": output_path, "max_dimension": RESULT_PREVIEW_MAX_DIMENSION}
        })),
        // The probe carries the user's FFmpeg too: a video it encoded may need it to open.
        "video_job_completed" => Some(media_probe_request(state, &output_path)),
        _ => None,
    }
}

/// The device a new installation starts on: Apple's GPU or a CUDA/ROCm card.
/// DirectML stays opt-in because it is untested.
fn first_run_device(devices: &[DeviceInfo]) -> Option<String> {
    devices
        .iter()
        .find(|device| device.id == "mps" || device.id == "cuda" || device.id.starts_with("cuda:"))
        .map(|device| device.id.clone())
}

#[cfg(test)]
mod tests;
