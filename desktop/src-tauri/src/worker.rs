use std::{
    env,
    io::{BufReader, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
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
    let (sender, receiver) = mpsc::channel::<String>();
    *lock(&state.worker.sender)? = Some(sender);
    {
        let mut runtime = lock(&state.runtime)?;
        runtime.worker = "starting".into();
        runtime.status_title = "Starting engine".into();
        runtime.status_detail = "The inference process is loading.".into();
        // A new engine has run nothing yet.
        runtime.hardware_note.clear();
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
        let mut stopped_by_app = false;
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break Ok(status),
                Err(error) => break Err(error),
                Ok(None) => {}
            }
            if state.worker.shutting_down.load(Ordering::SeqCst) {
                stopped_by_app = true;
                let _ = child.kill();
                break child.wait();
            }
            if overflowed.load(Ordering::SeqCst) {
                // Not an app-requested stop: the engine failed, and the restart
                // counter says so.
                let _ = child.kill();
                break child.wait();
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
                let _ = child.kill();
                break child.wait();
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
                    let _ = child.kill();
                    break child.wait();
                }
            }
            thread::sleep(Duration::from_millis(100));
        };
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

        let attempt = if forced_jobs.is_empty() && !requested_restart {
            state.worker.restart_count.fetch_add(1, Ordering::SeqCst) + 1
        } else {
            1
        };
        if attempt <= 3 {
            thread::sleep(Duration::from_secs(u64::from(attempt)));
            if let Err(error) = start_worker(state.clone(), app.clone()) {
                set_worker_failure(&state, &app, error.to_string());
            }
        } else {
            set_worker_failure(
                &state,
                &app,
                "The inference engine repeatedly stopped. Restart LocalSR to try again.".into(),
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
/// true when something was sent.
fn begin_dispatch(state: &Arc<AppState>, batches: bool) -> AppResult<bool> {
    if !lock(&state.runtime)?.active_job_id.is_empty() {
        return Ok(false);
    }
    let Some(pending) = lock(&state.database)?.next_queued_job()? else {
        return Ok(false);
    };
    if lock(&state.runtime)?.worker != "ready" {
        return Ok(false);
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
        return Ok(false);
    };
    let first = ids[0].clone();
    lock(&state.worker.cancellation)?.prepare(&first, &mut message)?;
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
            return Ok(false);
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
    Ok(true)
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
    // interrupted, as before.
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
    if cancellation.requested_at.is_some()
        && cancellation.job_ids.contains(&job_id)
        && !terminal
        && envelope.message_type != "video_memory"
    {
        return;
    }
    // A photo saved off screen needs no full preview: nothing would show it.
    let background = state
        .runtime
        .lock()
        .map(|runtime| is_background_photo(&runtime, &job_id))
        .unwrap_or(false);
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

    // Generate the final full-image preview inside the native control plane.
    // The previous frontend request raced the Rust completion update and could
    // leave a successful job without a center preview until another refresh.
    if let Some(request) = completion_preview_request(&envelope).filter(|_| !background) {
        if let Err(error) = send(state, &request) {
            if cfg!(debug_assertions) {
                eprintln!("Could not request completed preview: {error}");
            }
        }
    }

    match envelope.message_type.as_str() {
        "job_completed" | "video_job_completed" => {
            let body = if string(&envelope.data, "notes").is_empty() {
                "Your enhanced media is ready."
            } else {
                "Your enhanced media is ready. A fix was skipped; open LocalSR for details."
            };
            let _ = app
                .notification()
                .builder()
                .title("LocalSR finished")
                .body(body)
                .show();
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
    Ok(true)
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
    if matches!(kind, "job_completed" | "video_job_completed") {
        let reported = string(&envelope.data, "output_path");
        let requested = lock(&state.database)?.requested_output(&id)?;
        // The engine writes the file the request names and reports that same path.
        if requested.as_deref().map(Path::new) != Some(Path::new(&reported)) {
            let shown: String = reported.chars().take(256).collect();
            return Ok(Some(format!(
                "{kind} for job {id} reports {shown:?}, which is not the output the host requested"
            )));
        }
    }
    Ok(None)
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
            lock(&state.database)?.update_job_progress(&id, number(data, "percentage"))?;
        }
        "job_completed" => {
            let output = string(data, "output_path");
            let database = lock(&state.database)?;
            database.finish_job(&id, "completed", &output, &string(data, "notes"))?;
            database.record_job_duration(&id, number(data, "elapsed_seconds"))?;
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
    runtime.estimated_remaining_seconds = 0.0;
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
            state.worker.restart_count.store(0, Ordering::SeqCst);
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
                integer(data, "width") as u32,
                integer(data, "height") as u32,
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
            let index = integer(data, "stage_index") + 1;
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
        "stage_progress" => {}
        "progress" => {
            let id = string(data, "job_id");
            let progress = number(data, "percentage");
            lock(&state.database)?.update_job_progress(&id, progress)?;
            let mut runtime = lock(&state.runtime)?;
            runtime.progress = progress;
            let editing = string(data, "unit") == "steps";
            // In a job of several stages the title keeps which one runs (two fixes can
            // both be "Restoring"); a one-stage job reads "Enhancing" as before.
            let staged = runtime.status_title.contains(" · stage ")
                && !runtime.status_title.ends_with(" 1/1");
            if editing {
                runtime.status_title = "Editing".into();
            } else if !staged {
                runtime.status_title = "Enhancing".into();
            }
            let remaining = number(data, "estimated_remaining_seconds");
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
            runtime.elapsed_seconds = number(data, "elapsed_seconds");
            runtime.estimated_remaining_seconds = number(data, "estimated_remaining_seconds");
            let completed = integer(data, "completed_tiles") as f64;
            runtime.throughput = if runtime.elapsed_seconds > 0.0 {
                completed / runtime.elapsed_seconds
            } else {
                0.0
            };
            runtime.throughput_unit = if editing { "steps/s" } else { "tiles/s" }.into();
            runtime.active_tile_size = integer(data, "active_tile_size") as u32;
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
                    runtime.progress = frames_done / frames_total * 100.0;
                }
                runtime.estimated_remaining_seconds = number(data, "estimated_remaining_seconds");
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
                        integer(data, "frame_index") + 1,
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
                runtime.elapsed_seconds = number(data, "elapsed_seconds");
                runtime.throughput_unit = "frames/s".into();
            }
        }
        "video_frame_started" => {
            let mut runtime = lock(&state.runtime)?;
            runtime.status_title = "Enhancing video · Labs".into();
            runtime.status_detail = format!(
                "Frame {} of {}{}",
                integer(data, "frame_index") + 1,
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
                fraction / total * 100.0
            } else {
                0.0
            };
            runtime.status_title = "Enhancing video".into();
            runtime.elapsed_seconds = number(data, "elapsed_seconds");
            runtime.estimated_remaining_seconds = number(data, "estimated_remaining_seconds");
            runtime.active_tile_size = integer(data, "active_tile_size") as u32;
            runtime.status_detail = format!(
                "Frame {} of {} · Tile {} / {}{}",
                frame as u64 + 1,
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
                completed / total * 100.0
            } else {
                0.0
            };
            lock(&state.database)?.update_job_progress(&id, progress)?;
            let mut runtime = lock(&state.runtime)?;
            runtime.progress = progress;
            let elapsed = number(data, "elapsed_seconds");
            let remaining = number(data, "estimated_remaining_seconds");
            runtime.status_title = "Enhancing video · Labs".into();
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
        "live_preview_warning" => {}
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
            runtime.progress = number(data, "percentage");
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
            runtime.progress = number(data, "percentage");
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
            let mut result: BenchmarkResult = serde_json::from_value(data["result"].clone())?;
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
            lock(&state.database)?.record_job_duration(&id, number(data, "elapsed_seconds"))?;
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
                number(data, "inference_seconds").max(number(data, "elapsed_seconds"));
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

fn resolve_worker_command(_state: &AppState, app: &AppHandle) -> AppResult<WorkerCommand> {
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
    // An explicit development worker takes precedence over a cached installed
    // engine; release builds still use the signed active engine as before.
    if let Some(path) = crate::updates::active_engine(&_state.paths) {
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
                _state
                    .paths
                    .repo_root
                    .join(".venv")
                    .join("Scripts")
                    .join("python.exe"),
                _state
                    .paths
                    .repo_root
                    .join(".venv311")
                    .join("Scripts")
                    .join("python.exe"),
            ]
        } else {
            vec![
                _state
                    .paths
                    .repo_root
                    .join(".venv")
                    .join("bin")
                    .join("python"),
                _state
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
        let source = _state.paths.repo_root.join("src");
        if !source.is_dir() {
            return Err(AppError::Worker(
                "the bundled inference engine is missing; rebuild the desktop package".into(),
            ));
        }
        Ok(WorkerCommand {
            program,
            arguments: vec!["-u".into(), "-m".into(), "localsr.worker".into()],
            working_directory: Some(_state.paths.repo_root.clone()),
            python_path: Some(source),
        })
    }
}

fn set_worker_failure(state: &AppState, app: &AppHandle, detail: String) {
    if let Ok(mut runtime) = state.runtime.lock() {
        runtime.worker = "failed".into();
        runtime.status_title = "Engine unavailable".into();
        runtime.status_detail = detail;
    }
    emit_state_changed(app);
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

/// A completed comparison must come from the full output file, never from the
/// last progressive tile or video frame. Keep this request in the native host
/// so it cannot race the completion state applied above.
fn completion_preview_request(envelope: &WorkerEnvelope) -> Option<Value> {
    let output_path = string(&envelope.data, "output_path");
    if output_path.is_empty() {
        return None;
    }
    match envelope.message_type.as_str() {
        "job_completed" => Some(json!({
            "type": "preview_request",
            "data": {"image_path": output_path, "max_dimension": RESULT_PREVIEW_MAX_DIMENSION}
        })),
        "video_job_completed" => Some(json!({
            "type": "media_probe_request",
            "data": {"media_path": output_path, "max_dimension": 2048}
        })),
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
mod tests {
    use super::*;

    #[test]
    fn shutdown_supervisor_cleans_scratch() {
        let directory = tempfile::tempdir().unwrap();
        let worker = crate::state::WorkerControl::default();
        let mut message = json!({"type":"job_request", "data":{"output_path":directory.path().join("output.png")}});
        worker
            .cancellation
            .lock()
            .unwrap()
            .prepare("job", &mut message)
            .unwrap();
        let scratch =
            std::path::PathBuf::from(message["data"]["scratch_directory"].as_str().unwrap());
        std::fs::write(scratch.join("partial"), b"unfinished").unwrap();
        worker.shutting_down.store(true, Ordering::SeqCst);
        assert!(supervisor_should_return(&worker, 0));
        assert!(!scratch.exists(), "shutdown must remove scratch");
    }

    #[test]
    fn the_apple_gpu_reports_whether_the_neural_engine_is_available() {
        let mut reported = serde_json::json!({
            "id": "mps",
            "type": "mps",
            "name": "Apple GPU (Metal)",
            "total_memory": 1,
            "free_memory": 1,
            "supports_fp16": true,
            "recommended_tile_sizes": [256],
            "neural_engine": true
        });
        assert!(
            serde_json::from_value::<DeviceInfo>(reported.clone())
                .unwrap()
                .neural_engine
        );
        reported.as_object_mut().unwrap().remove("neural_engine");
        assert!(
            !serde_json::from_value::<DeviceInfo>(reported)
                .unwrap()
                .neural_engine
        );
    }

    #[test]
    fn a_new_installation_starts_on_the_gpu_but_not_on_directml() {
        let device = |id: &str| DeviceInfo {
            id: id.into(),
            ..DeviceInfo::default()
        };
        assert_eq!(
            first_run_device(&[device("cpu"), device("mps")]).as_deref(),
            Some("mps")
        );
        assert_eq!(
            first_run_device(&[device("cpu"), device("cuda:0")]).as_deref(),
            Some("cuda:0")
        );
        assert_eq!(
            first_run_device(&[device("cpu"), device("directml:0")]),
            None
        );
    }

    #[test]
    fn video_stages_have_titles_including_frame_interpolation() {
        assert_eq!(
            video_stage_title("loading_interpolation"),
            "Loading frame interpolation"
        );
        assert_eq!(video_stage_title("saving"), "Saving video");
        assert_eq!(video_stage_title("something_new"), "Processing video");
    }

    #[test]
    fn protocol_values_are_tolerant_of_missing_optional_fields() {
        let value = json!({"job_id": "job-1"});
        assert_eq!(string(&value, "job_id"), "job-1");
        assert_eq!(number(&value, "percentage"), 0.0);
        assert_eq!(integer(&value, "frame_count"), 0);
    }

    #[test]
    fn worker_death_records_the_last_confirmed_video_stage() {
        let detail = worker_interruption_detail(
            "The inference engine exited with status 1",
            "Enhancing video · Labs",
        );
        assert!(detail.contains("Last confirmed stage: Enhancing video · Labs"));
        assert!(!detail.contains("post-processing"));
    }

    #[test]
    fn worker_envelope_round_trips_protocol_type() {
        let envelope: WorkerEnvelope = serde_json::from_value(json!({
            "type": "worker_ready",
            "data": {}
        }))
        .unwrap();
        assert_eq!(envelope.message_type, "worker_ready");
        assert_eq!(
            serde_json::to_value(envelope).unwrap()["type"],
            "worker_ready"
        );
    }

    #[test]
    fn high_frequency_messages_do_not_emit_duplicate_full_snapshots() {
        for message_type in [
            "progress",
            "tile_update",
            "video_frame_started",
            "video_frame_completed",
            "video_memory",
        ] {
            assert!(!should_emit_state_changed(message_type));
        }
        for message_type in ["job_started", "job_completed", "preview_ready", "warning"] {
            assert!(should_emit_state_changed(message_type));
        }
    }

    #[test]
    fn high_frequency_worker_events_are_sampled_but_final_progress_is_forwarded() {
        let started = Instant::now();
        let mut gate = WorkerEventGate::default();
        let tile = |completed| WorkerEnvelope {
            message_type: "tile_update".into(),
            data: json!({
                "job_id": "job-1",
                "phase": "completed",
                "completed_tiles": completed,
                "total_tiles": 100,
                "jpeg_base64": "tile"
            }),
        };
        let progress = |completed| WorkerEnvelope {
            message_type: "progress".into(),
            data: json!({
                "job_id": "job-1",
                "completed_tiles": completed,
                "total_tiles": 100
            }),
        };

        assert!(gate.should_forward(&tile(1), started));
        assert!(!gate.should_forward(&tile(2), started + Duration::from_millis(5)));
        assert!(gate.should_forward(&tile(3), started + Duration::from_millis(80)));
        assert!(gate.should_forward(&tile(100), started + Duration::from_millis(81)));

        assert!(gate.should_forward(&progress(1), started));
        assert!(!gate.should_forward(&progress(2), started + Duration::from_millis(10)));
        assert!(gate.should_forward(&progress(100), started + Duration::from_millis(11)));

        let first_region = WorkerEnvelope {
            message_type: "tile_update".into(),
            data: json!({"job_id": "job-1", "phase": "started", "completed_tiles": 0}),
        };
        assert!(gate.should_forward(&first_region, started));
        // A new frame or VAE/DiT stage must establish its geometry even when
        // its first region arrives inside the normal animation throttle.
        assert!(gate.should_forward(&first_region, started + Duration::from_millis(1)));
        for index in 1..20 {
            for phase in ["started", "completed"] {
                let position = WorkerEnvelope {
                    message_type: "tile_update".into(),
                    data: json!({"job_id": "job-1", "phase": phase, "completed_tiles": index,
                        "output_x": index * 256, "jpeg_base64": ""}),
                };
                assert!(gate.should_forward(&position, started + Duration::from_millis(index)));
            }
        }
    }

    #[test]
    fn ordinary_progress_preserves_the_active_image_stage() {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(
            AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
        );
        let title_after_progress = |index: u32, count: u32| {
            for (kind, data) in [
                (
                    "stage_started",
                    json!({"job_id":"j", "stage_index":index, "stage_count":count, "stage_kind":"restore", "model_id":"noise"}),
                ),
                (
                    "progress",
                    json!({"job_id":"j", "percentage":45, "completed_tiles":1, "total_tiles":4}),
                ),
            ] {
                apply_worker_envelope(
                    &state,
                    &WorkerEnvelope {
                        message_type: kind.into(),
                        data,
                    },
                )
                .unwrap();
            }
            lock(&state.runtime).unwrap().status_title.clone()
        };
        // Several fixes can share a kind; the title keeps which stage is running.
        assert_eq!(title_after_progress(2, 5), "Restoring · stage 3/5");
        // A one-stage job reads as before.
        assert_eq!(title_after_progress(0, 1), "Enhancing");
    }

    #[test]
    fn selected_image_comparison_ignores_late_results_while_another_job_runs() {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(
            AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
        );
        {
            let mut runtime = lock(&state.runtime).unwrap();
            runtime.active_job_id = "running-third".into();
            runtime.comparison_media_id = "second".into();
            runtime.comparison_output_path = "/results/second.png".into();
        }
        for (path, encoded) in [
            ("/results/first.png", "old"),
            ("/results/second.png", "correct"),
        ] {
            apply_worker_envelope(
                &state,
                &WorkerEnvelope {
                    message_type: "preview_ready".into(),
                    data: json!({"image_path": path, "jpeg_base64": encoded}),
                },
            )
            .unwrap();
        }
        apply_worker_envelope(
            &state,
            &WorkerEnvelope {
                message_type: "preview_ready".into(),
                data: json!({"image_path": "/results/first.png", "jpeg_base64": "late"}),
            },
        )
        .unwrap();
        let runtime = lock(&state.runtime).unwrap();
        assert_eq!(
            runtime.comparison_preview_data_url,
            "data:image/jpeg;base64,correct"
        );
        assert_eq!(runtime.comparison_media_id, "second");
        assert!(runtime.result_preview_data_url.is_empty());
        assert_eq!(runtime.active_job_id, "running-third");
    }

    #[test]
    fn compare_answers_and_snapshots_revalidate_the_selected_job() {
        use crate::compare::{ComparePick, CompareStatus};
        let directory = tempfile::tempdir().unwrap();
        let paths = crate::paths::AppPaths::under(directory.path());
        let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
        let input = directory.path().join("input.png");
        let output = directory.path().join("output.png");
        std::fs::write(&input, "input").unwrap();
        std::fs::write(&output, "output").unwrap();
        let request = {
            let db = lock(&state.database).unwrap();
            db.insert_media("a", input.to_str().unwrap(), "a", "image")
                .unwrap();
            db.insert_job("job", "a", "{}").unwrap();
            db.finish_job("job", "completed", output.to_str().unwrap(), "")
                .unwrap();
            let mut runtime = lock(&state.runtime).unwrap();
            crate::compare::plan(
                &db,
                &mut runtime.compare,
                &[ComparePick::Output {
                    media_id: "a".into(),
                    job_id: "job".into(),
                }],
            )
            .unwrap()
            .remove(0)
        };
        let mut data = request["data"].clone();
        data["jpeg_base64"] = json!("decoded");
        apply_worker_envelope(
            &state,
            &WorkerEnvelope {
                message_type: "preview_ready".into(),
                data: data.clone(),
            },
        )
        .unwrap();
        let snapshot = state.snapshot().unwrap();
        assert_eq!(
            snapshot.runtime.compare.slots[0].status,
            CompareStatus::Ready
        );
        assert!(
            serde_json::to_value(&snapshot.runtime.compare).unwrap()["slots"][0]
                .get("path")
                .is_none()
        );
        rusqlite::Connection::open(&paths.database)
            .unwrap()
            .execute("DELETE FROM jobs WHERE id='job'", [])
            .unwrap();
        assert_eq!(
            state.snapshot().unwrap().runtime.compare.slots[0].status,
            CompareStatus::Missing
        );
        apply_worker_envelope(
            &state,
            &WorkerEnvelope {
                message_type: "preview_ready".into(),
                data,
            },
        )
        .unwrap();
        let snapshot = state.snapshot().unwrap();
        assert_eq!(
            snapshot.runtime.compare.slots[0].status,
            CompareStatus::Missing
        );
        assert!(snapshot.runtime.compare.slots[0]
            .preview_data_url
            .is_empty());
    }

    #[test]
    fn pair_previews_reject_stale_success_and_failure_even_for_the_same_path() {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(
            AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
        );
        {
            let mut runtime = lock(&state.runtime).unwrap();
            runtime.comparison_pair.generation = 2;
            runtime.comparison_pair.showing.target_id = "job-a".into();
            runtime.comparison_pair.showing.output_path = "/a.png".into();
            runtime.comparison_pair.against.target_id = "job-b".into();
            runtime.comparison_pair.against.output_path = "/b.png".into();
        }
        for (slot, target, path) in [
            ("showing", "job-a", "/a.png"),
            ("against", "job-b", "/b.png"),
        ] {
            apply_worker_envelope(&state, &WorkerEnvelope { message_type:"preview_ready".into(), data:json!({"slot":slot,"target_id":target,"generation":2,"image_path":path,"jpeg_base64":"correct","width":800,"height":600,"preview_width":400,"preview_height":300}) }).unwrap();
            for generation in [1, 3] {
                for kind in ["preview_ready", "preview_failed"] {
                    apply_worker_envelope(&state, &WorkerEnvelope { message_type:kind.into(), data:json!({"slot":slot,"target_id":target,"generation":generation,"image_path":path,"jpeg_base64":"stale","error_message":"stale"}) }).unwrap();
                }
            }
        }
        let runtime = lock(&state.runtime).unwrap();
        for slot in [
            &runtime.comparison_pair.showing,
            &runtime.comparison_pair.against,
        ] {
            assert_eq!(slot.preview_data_url, "data:image/jpeg;base64,correct");
            assert!(slot.error.is_empty());
            assert_eq!(
                (
                    slot.width,
                    slot.height,
                    slot.preview_width,
                    slot.preview_height
                ),
                (800, 600, 400, 300)
            );
        }
    }

    #[test]
    fn pair_previews_need_the_same_target_and_file_and_show_failures_per_side() {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(
            AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
        );
        {
            let mut runtime = lock(&state.runtime).unwrap();
            runtime.comparison_pair.generation = 4;
            runtime.comparison_pair.media_id = "photo".into();
            runtime.comparison_pair.showing.target_id = "original".into();
            runtime.comparison_pair.against.target_id = "job-b".into();
            runtime.comparison_pair.against.output_path = "/b.png".into();
        }
        let answer = |kind: &str, data: Value| {
            apply_worker_envelope(
                &state,
                &WorkerEnvelope {
                    message_type: kind.into(),
                    data,
                },
            )
            .unwrap()
        };
        // The Original side is never filled from the result decoder.
        answer(
            "preview_ready",
            json!({"slot":"showing","target_id":"original","generation":4,"image_path":"","jpeg_base64":"x"}),
        );
        // Another target, or another file under the right target, is dropped.
        answer(
            "preview_ready",
            json!({"slot":"against","target_id":"job-c","generation":4,"image_path":"/b.png","jpeg_base64":"x"}),
        );
        answer(
            "preview_ready",
            json!({"slot":"against","target_id":"job-b","generation":4,"image_path":"/c.png","jpeg_base64":"x"}),
        );
        {
            let runtime = lock(&state.runtime).unwrap();
            assert!(runtime.comparison_pair.showing.preview_data_url.is_empty());
            assert!(runtime.comparison_pair.against.preview_data_url.is_empty());
            // A slot answer never fills the automatic or single comparison.
            assert!(runtime.comparison_preview_data_url.is_empty());
        }
        answer(
            "preview_failed",
            json!({"slot":"against","target_id":"job-b","generation":4,"image_path":"/b.png","error_message":"truncated file"}),
        );
        let runtime = lock(&state.runtime).unwrap();
        assert_eq!(runtime.comparison_pair.against.error, "truncated file");
        assert!(runtime.comparison_pair.against.preview_data_url.is_empty());
    }

    #[test]
    fn completion_and_comparison_previews_share_one_bound() {
        let image = WorkerEnvelope {
            message_type: "job_completed".into(),
            data: json!({"job_id": "j", "output_path": "/output/enhanced.png"}),
        };
        assert_eq!(
            completion_preview_request(&image).unwrap()["data"]["max_dimension"],
            RESULT_PREVIEW_MAX_DIMENSION
        );
    }

    #[test]
    fn a_completed_job_keeps_its_note_on_the_status_line() {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(
            AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
        );
        let completed = |notes: Value| WorkerEnvelope {
            message_type: "job_completed".into(),
            data: json!({"job_id": "job", "output_path": "/out.png", "notes": notes}),
        };
        {
            let database = lock(&state.database).unwrap();
            database
                .insert_media("media", "/in.png", "in.png", "image")
                .unwrap();
            database
                .insert_job("job", "media", r#"{"type":"job_request","data":{}}"#)
                .unwrap();
        }
        apply_worker_envelope(&state, &completed(json!("The fix was skipped."))).unwrap();
        assert_eq!(
            lock(&state.runtime).unwrap().status_detail,
            "The fix was skipped."
        );
        // Kept with the job too, where the next refresh and the history read it.
        let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
        let job = jobs.iter().find(|job| job.id == "job").unwrap();
        assert_eq!(
            (job.status.as_str(), job.error.as_str()),
            ("completed", "The fix was skipped.")
        );
        apply_worker_envelope(&state, &completed(Value::Null)).unwrap();
        assert_eq!(
            lock(&state.runtime).unwrap().status_detail,
            "The output was written successfully."
        );
    }

    #[test]
    fn the_engine_route_note_outlives_progress_until_the_job_ends() {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(
            AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
        );
        let envelope = |message_type: &str, data: Value| WorkerEnvelope {
            message_type: message_type.into(),
            data,
        };
        let note = "SPAN does not run on Intel AI Boost (NPU), so it runs on the CPU.";
        apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "job"}))).unwrap();
        // A note for another job (a late message) changes nothing.
        apply_worker_envelope(
            &state,
            &envelope(
                "engine_status",
                json!({"job_id": "old", "message": "HAT runs on the GPU."}),
            ),
        )
        .unwrap();
        assert!(lock(&state.runtime).unwrap().hardware_note.is_empty());
        apply_worker_envelope(
            &state,
            &envelope("engine_status", json!({"job_id": "job", "message": note})),
        )
        .unwrap();
        assert_eq!(lock(&state.runtime).unwrap().status_detail, note);
        apply_worker_envelope(
            &state,
            &envelope(
                "progress",
                json!({"job_id": "job", "percentage": 12.0, "completed_tiles": 3, "total_tiles": 25}),
            ),
        )
        .unwrap();
        {
            let runtime = lock(&state.runtime).unwrap();
            assert_eq!(runtime.status_detail, "3 of 25 tiles");
            assert_eq!(runtime.hardware_note, note);
        }
        // The snapshot the interface refreshes from carries the note.
        let snapshot = serde_json::to_value(&*lock(&state.runtime).unwrap()).unwrap();
        assert_eq!(snapshot["hardware_note"], note);
        apply_worker_envelope(
            &state,
            &envelope(
                "job_failed",
                json!({"job_id": "job", "error_message": "The engine stopped."}),
            ),
        )
        .unwrap();
        assert!(lock(&state.runtime).unwrap().hardware_note.is_empty());
    }

    #[test]
    fn a_stale_route_note_never_reaches_a_benchmark() {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(
            AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
        );
        // A forced cancellation killed the engine mid-job; the next one is ready.
        lock(&state.runtime).unwrap().hardware_note =
            "RealESRGAN runs on Intel UHD Graphics 620 (DirectML), but slower than on the CPU here."
                .into();
        apply_worker_envelope(
            &state,
            &WorkerEnvelope {
                message_type: "benchmark_started".into(),
                data: json!({"job_id": "bench", "workload_version": "localsr-benchmark-v2"}),
            },
        )
        .unwrap();
        let runtime = lock(&state.runtime).unwrap();
        assert_eq!(runtime.active_job_id, "bench");
        assert!(runtime.hardware_note.is_empty());
    }

    #[test]
    fn abandoning_or_replacing_the_active_job_drops_its_route_note() {
        // The supervisor path (a killed or crashed engine) and the dispatcher use these.
        let mut runtime = RuntimeStatus {
            active_job_id: "job".into(),
            hardware_note: "SPAN runs on the NPU.".into(),
            ..RuntimeStatus::default()
        };
        assert_eq!(runtime.take_active_job(), "job");
        assert!(runtime.active_job_id.is_empty() && runtime.hardware_note.is_empty());
        runtime.hardware_note = "SPAN runs on the NPU.".into();
        runtime.begin_active_job("next".into());
        assert_eq!(runtime.active_job_id, "next");
        assert!(runtime.hardware_note.is_empty());
    }

    #[test]
    fn eta_suffix_is_compact_and_omits_unknown_values() {
        assert_eq!(eta_suffix(0.0), "");
        assert_eq!(eta_suffix(f64::NAN), "");
        assert_eq!(eta_suffix(7.4), " · ETA 7s");
        assert_eq!(eta_suffix(65.0), " · ETA 1:05");
    }

    #[test]
    fn completion_requests_a_full_preview_for_the_finished_output() {
        let image = WorkerEnvelope {
            message_type: "job_completed".into(),
            data: json!({"output_path": "/output/enhanced.png"}),
        };
        assert_eq!(
            completion_preview_request(&image),
            Some(json!({
                "type": "preview_request",
                "data": {"image_path": "/output/enhanced.png", "max_dimension": 2048}
            }))
        );

        let video = WorkerEnvelope {
            message_type: "video_job_completed".into(),
            data: json!({"output_path": "/output/enhanced.mp4"}),
        };
        assert_eq!(
            completion_preview_request(&video),
            Some(json!({
                "type": "media_probe_request",
                "data": {"media_path": "/output/enhanced.mp4", "max_dimension": 2048}
            }))
        );

        let incomplete = WorkerEnvelope {
            message_type: "job_completed".into(),
            data: json!({}),
        };
        assert_eq!(completion_preview_request(&incomplete), None);
    }

    #[test]
    fn legacy_capability_devices_default_to_discrete_memory() {
        let capability: CapabilityInfo = serde_json::from_value(json!({
            "system_ram_total": 16,
            "system_ram_available": 8,
            "system_memory_pressure_percent": 0.0,
            "system_memory_pressure_level": "unknown",
            "system_compressed_memory": 0,
            "system_swap_total": 0,
            "system_swap_used": 0,
            "devices": [{
                "id": "cpu",
                "type": "cpu",
                "name": "CPU",
                "total_memory": 16,
                "free_memory": 8,
                "supports_fp16": false,
                "recommended_tile_sizes": [64]
            }]
        }))
        .unwrap();

        assert!(!capability.devices[0].is_integrated);
    }

    #[test]
    fn a_lost_gpu_holds_dispatch_until_the_engine_restarts() {
        let mut runtime = RuntimeStatus {
            worker: "ready".into(),
            ..RuntimeStatus::default()
        };
        hold_until_restart(&mut runtime, &json!({"job_id": "a", "error_message": "x"}));
        assert_eq!(runtime.worker, "ready");
        hold_until_restart(
            &mut runtime,
            &json!({"job_id": "a", "error_message": "x", "restart_required": true}),
        );
        assert_eq!(runtime.worker, "restarting");
    }

    #[test]
    fn an_exited_engine_takes_no_more_work() {
        let mut runtime = RuntimeStatus {
            worker: "ready".into(),
            ..RuntimeStatus::default()
        };
        mark_exited(&mut runtime);
        assert_eq!(runtime.worker, "unavailable");
        runtime.worker = "restarting".into();
        mark_exited(&mut runtime);
        assert_eq!(runtime.worker, "restarting");
    }

    #[test]
    fn only_a_native_fault_counts_as_a_crash() {
        #[cfg(unix)]
        use std::os::unix::process::ExitStatusExt;
        #[cfg(windows)]
        use std::os::windows::process::ExitStatusExt;
        use std::process::ExitStatus;

        #[cfg(unix)]
        let (clean, restart, failure, killed, faults) = (
            ExitStatus::from_raw(0),
            ExitStatus::from_raw(75 << 8),
            ExitStatus::from_raw(1 << 8),
            ExitStatus::from_raw(9), // SIGKILL, as from the OOM killer
            vec![ExitStatus::from_raw(11), ExitStatus::from_raw(6)], // SIGSEGV, SIGABRT
        );
        #[cfg(windows)]
        let (clean, restart, failure, killed, faults) = (
            ExitStatus::from_raw(0),
            ExitStatus::from_raw(75),
            ExitStatus::from_raw(2),
            ExitStatus::from_raw(1), // TerminateProcess from Task Manager
            vec![ExitStatus::from_raw(0xC000_0005), ExitStatus::from_raw(3)],
        );
        for status in [clean, restart, failure, killed] {
            assert!(!engine_crashed(&Ok(status)), "{status:?}");
        }
        for status in faults {
            assert!(engine_crashed(&Ok(status)), "{status:?}");
        }
        assert!(!engine_crashed(&Err(std::io::Error::other("no status"))));
    }

    #[test]
    fn an_engine_that_does_not_exit_after_asking_for_a_restart_is_stopped() {
        let start = Instant::now();
        let mut since = None;
        assert!(!restart_overdue(false, &mut since, start));
        assert!(!restart_overdue(true, &mut since, start));
        assert!(!restart_overdue(
            true,
            &mut since,
            start + RESTART_EXIT_GRACE / 2
        ));
        assert!(restart_overdue(
            true,
            &mut since,
            start + RESTART_EXIT_GRACE
        ));
        // A fresh request starts its own grace period.
        assert!(!restart_overdue(
            false,
            &mut since,
            start + RESTART_EXIT_GRACE * 2
        ));
        assert!(!restart_overdue(
            true,
            &mut since,
            start + RESTART_EXIT_GRACE * 2
        ));
    }

    #[test]
    fn capability_contract_preserves_every_supported_backend_identifier() {
        let device_specs = [
            ("mps", "mps", true),
            ("cpu", "cpu", false),
            ("cuda:0", "cuda", false),
            ("cuda:0", "rocm", false),
            ("xpu:0", "xpu", true),
            ("directml:0", "directml", true),
            ("openvino:8680677d06", "openvino", true),
            ("openvino:80d1d11eb7", "npu", true),
        ];

        for (id, backend, integrated) in device_specs {
            let capability: CapabilityInfo = serde_json::from_value(json!({
                "system_ram_total": 16,
                "system_ram_available": 8,
                "system_memory_pressure_percent": 0.0,
                "system_memory_pressure_level": "low",
                "system_compressed_memory": 0,
                "system_swap_total": 0,
                "system_swap_used": 0,
                "devices": [{
                    "id": id,
                    "type": backend,
                    "name": backend,
                    "total_memory": 16,
                    "free_memory": 8,
                    "supports_fp16": backend != "cpu",
                    "is_integrated": integrated,
                    "recommended_tile_sizes": [64, 128]
                }]
            }))
            .unwrap();

            assert_eq!(capability.devices[0].id, id);
            assert_eq!(capability.devices[0].device_type, backend);
            assert_eq!(capability.devices[0].is_integrated, integrated);
        }
    }
    fn photo_job(id: &str) -> Value {
        json!({"type": "job_request", "data": {
            "job_id": id, "image_path": format!("/in/{id}.png"),
            "output_path": format!("/out/{id}.png"), "scratch_directory": "/work",
            "model_path": "/models/span.pth", "device": "mps", "neural_engine": true,
            "output_format": "png", "tile_size": 256, "halo": 16, "face_model_path": null,
            "stages": [{"kind": "upscale", "model_id": "span", "model_path": "/models/span.pth"}]
        }})
    }

    fn pending(messages: &[Value]) -> Vec<crate::database::PendingJob> {
        messages
            .iter()
            .map(|message| crate::database::PendingJob {
                id: string(&message["data"], "job_id"),
                request_json: message.to_string(),
            })
            .collect()
    }

    #[test]
    fn queued_photos_that_share_one_recipe_go_out_as_one_batch_in_queue_order() {
        let mut other = photo_job("d");
        other["data"]["halo"] = json!(32);
        let window = pending(&[
            photo_job("a"),
            photo_job("b"),
            photo_job("c"),
            other,
            photo_job("e"),
        ]);
        let (ids, message) = next_dispatch(&window, |_| true).unwrap().unwrap();
        // Never past a job with another recipe: the queue keeps its order.
        assert_eq!(ids, ["a", "b", "c"]);
        assert_eq!(message["type"], "image_batch_request");
        let jobs = message["data"]["jobs"].as_array().unwrap();
        assert_eq!(jobs.len(), 3);
        assert_eq!(jobs[1], photo_job("b")["data"]);
        let mut jpeg = photo_job("f");
        jpeg["data"]["output_format"] = json!("jpg");
        let mut jpeg_too = photo_job("g");
        jpeg_too["data"]["output_format"] = json!("jpg");
        let (ids, _) = next_dispatch(&pending(&[jpeg, jpeg_too]), |_| true)
            .unwrap()
            .unwrap();
        assert_eq!(ids, ["f", "g"]);
    }

    #[test]
    fn a_batch_never_exceeds_its_limit() {
        let window: Vec<Value> = (0..MAX_BATCH_PHOTOS + 3)
            .map(|index| photo_job(&format!("p{index}")))
            .collect();
        let (ids, message) = next_dispatch(&pending(&window), |_| true).unwrap().unwrap();
        assert_eq!(ids.len(), MAX_BATCH_PHOTOS);
        assert_eq!(
            message["data"]["jobs"].as_array().unwrap().len(),
            MAX_BATCH_PHOTOS
        );
    }

    #[test]
    fn every_other_job_is_sent_as_before() {
        let changes: [(&str, Value); 6] = [
            ("device", json!("cpu")),
            ("neural_engine", json!(false)),
            ("face_model_path", json!("/models/face.pth")),
            (
                "stages",
                json!([{"kind": "restore"}, {"kind": "upscale", "model_path": "/models/span.pth"}]),
            ),
            (
                "stages",
                json!([{"kind": "upscale"}, {"kind": "face_restore", "model_path": "/f.pth"}]),
            ),
            ("model_path", json!("/models/hat.pth")),
        ];
        for (field, value) in changes {
            let mut first = photo_job("a");
            first["data"][field] = value.clone();
            let mut second = photo_job("b");
            second["data"][field] = value;
            let window = pending(&[first.clone(), second]);
            let (ids, message) = next_dispatch(&window, |path| path == "/models/span.pth")
                .unwrap()
                .unwrap();
            assert_eq!(ids, ["a"], "{field}");
            assert_eq!(message, first, "{field}");
        }
        for kind in ["video_job_request", "edit_job_request"] {
            let mut first = photo_job("a");
            first["type"] = json!(kind);
            let mut second = photo_job("b");
            second["type"] = json!(kind);
            let (ids, message) = next_dispatch(&pending(&[first.clone(), second]), |_| true)
                .unwrap()
                .unwrap();
            assert_eq!((ids.len(), message), (1, first));
        }
        // A single photo keeps the plain job request.
        let (ids, message) = next_dispatch(&pending(&[photo_job("a")]), |_| true)
            .unwrap()
            .unwrap();
        assert_eq!((ids.len(), message), (1, photo_job("a")));
        assert!(next_dispatch(&[], |_| true).unwrap().is_none());
    }

    fn batch_state(directory: &std::path::Path) -> Arc<AppState> {
        let state =
            Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory)).unwrap());
        let output = directory.join("out");
        std::fs::create_dir_all(&output).unwrap();
        let db = lock(&state.database).unwrap();
        for id in ["a", "b", "c"] {
            let mut job = photo_job(id);
            job["data"]["output_path"] = json!(output.join(format!("{id}.png")));
            db.insert_media(id, &format!("/in/{id}.png"), id, "image")
                .unwrap();
            db.insert_job(id, id, &job.to_string()).unwrap();
        }
        drop(db);
        state
    }

    fn status(state: &AppState, id: &str) -> String {
        let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
        jobs.into_iter().find(|job| job.id == id).unwrap().status
    }

    fn envelope(kind: &str, data: Value) -> WorkerEnvelope {
        WorkerEnvelope {
            message_type: kind.into(),
            data,
        }
    }

    #[test]
    fn a_dispatched_batch_shows_its_first_photo_and_keeps_the_others_queued() {
        let directory = tempfile::tempdir().unwrap();
        let state = batch_state(directory.path());
        ready_for_batches(&state);
        let (sender, receiver) = mpsc::channel();
        *lock(&state.worker.sender).unwrap() = Some(sender);

        assert!(begin_dispatch(&state, true).unwrap());
        let sent: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
        let runtime = lock(&state.runtime).unwrap().clone();
        assert_eq!(runtime.active_job_id, "a");
        assert_eq!(sent["type"], "image_batch_request");
        assert_eq!(runtime.active_batch_job_ids, ["a", "b", "c"]);
        // Each photo owns its scratch directory next to its output.
        let jobs = sent["data"]["jobs"].as_array().unwrap();
        let scratch: Vec<_> = jobs
            .iter()
            .map(|job| PathBuf::from(job["scratch_directory"].as_str().unwrap()))
            .collect();
        assert!(scratch.iter().all(|path| path.is_dir()));
        assert_eq!(
            scratch
                .iter()
                .collect::<std::collections::HashSet<_>>()
                .len(),
            3
        );
        // Only the photo on screen is starting; the others wait as queued photos.
        assert_eq!(status(&state, "a"), "starting");
        assert_eq!(status(&state, "b"), "queued");
        // Nothing else is dispatched while the batch runs.
        assert!(!begin_dispatch(&state, true).unwrap());
        lock(&state.worker.cancellation).unwrap().finish().unwrap();
    }

    #[test]
    fn hosts_other_than_a_mac_send_one_job_at_a_time() {
        let directory = tempfile::tempdir().unwrap();
        let state = batch_state(directory.path());
        ready_for_batches(&state);
        let (sender, receiver) = mpsc::channel();
        *lock(&state.worker.sender).unwrap() = Some(sender);
        assert!(begin_dispatch(&state, false).unwrap());
        let sent: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
        assert_eq!(sent["type"], "job_request");
        assert!(lock(&state.runtime)
            .unwrap()
            .active_batch_job_ids
            .is_empty());
        lock(&state.worker.cancellation).unwrap().finish().unwrap();
    }

    #[test]
    fn a_photo_whose_scratch_folder_cannot_be_made_ends_the_batch_before_it() {
        let directory = tempfile::tempdir().unwrap();
        let state = batch_state(directory.path());
        {
            // The third photo writes to a folder that no longer exists (an ejected drive).
            let db = lock(&state.database).unwrap();
            let mut job = photo_job("c");
            job["data"]["output_path"] = json!(directory.path().join("gone").join("c.png"));
            db.cancel_queued_jobs().unwrap();
            for id in ["a2", "b2", "c2"] {
                db.insert_media(id, &format!("/in/{id}.png"), id, "image")
                    .unwrap();
            }
            let mut a = photo_job("a2");
            a["data"]["output_path"] = json!(directory.path().join("out").join("a2.png"));
            let mut b = photo_job("b2");
            b["data"]["output_path"] = json!(directory.path().join("out").join("b2.png"));
            job["data"]["job_id"] = json!("c2");
            db.insert_job("a2", "a2", &a.to_string()).unwrap();
            db.insert_job("b2", "b2", &b.to_string()).unwrap();
            db.insert_job("c2", "c2", &job.to_string()).unwrap();
        }
        ready_for_batches(&state);
        let (sender, receiver) = mpsc::channel();
        *lock(&state.worker.sender).unwrap() = Some(sender);
        assert!(begin_dispatch(&state, true).unwrap());
        let sent: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
        assert_eq!(sent["type"], "image_batch_request");
        assert_eq!(sent["data"]["jobs"].as_array().unwrap().len(), 2);
        assert_eq!(
            lock(&state.runtime).unwrap().active_batch_job_ids,
            ["a2", "b2"]
        );
        // The healthy photos run; the other waits for its own turn.
        assert_eq!(status(&state, "c2"), "queued");
        lock(&state.worker.cancellation).unwrap().finish().unwrap();
    }

    fn ready_for_batches(state: &AppState) {
        *lock(&state.engine).unwrap() = Some(
            serde_json::from_value(json!({"protocol_version": 1, "minimum_protocol_version": 1,
                "engine_id": "e", "engine_version": "1", "features": ["image", "image_batch_v1"],
                "model_formats": [], "video_engines": []}))
            .unwrap(),
        );
        for model in &mut lock(&state.catalog).unwrap().models {
            if model.architecture == "SPAN" {
                model.installed = true;
                model.installed_path = Some("/models/span.pth".into());
            }
        }
        lock(&state.runtime).unwrap().worker = "ready".into();
    }

    fn running_batch(directory: &std::path::Path) -> Arc<AppState> {
        let state = batch_state(directory);
        let mut runtime = lock(&state.runtime).unwrap();
        runtime.worker = "ready".into();
        runtime.active_job_id = "a".into();
        runtime.active_batch_job_ids = vec!["a".into(), "b".into(), "c".into()];
        drop(runtime);
        state
    }

    #[test]
    fn a_batch_shows_one_photo_while_the_others_finish_in_the_background() {
        let directory = tempfile::tempdir().unwrap();
        let state = running_batch(directory.path());
        let apply = |kind: &str, data: Value| apply_worker_envelope(&state, &envelope(kind, data));
        apply("job_started", json!({"job_id": "a"})).unwrap();
        apply("progress", json!({"job_id": "a", "percentage": 90.0})).unwrap();
        // The next photo starts as the first one's last tiles finish: it is shown now.
        apply("job_started", json!({"job_id": "b"})).unwrap();
        apply("progress", json!({"job_id": "b", "percentage": 20.0})).unwrap();
        apply("progress", json!({"job_id": "a", "percentage": 100.0})).unwrap();
        apply(
            "job_completed",
            json!({"job_id": "a", "output_path": "/out/a.png", "elapsed_seconds": 1.5}),
        )
        .unwrap();
        {
            let runtime = lock(&state.runtime).unwrap();
            assert_eq!(runtime.active_job_id, "b");
            assert_eq!(runtime.progress, 20.0);
            assert_eq!(runtime.status_title, "Enhancing");
            assert_eq!(runtime.active_batch_job_ids, ["b", "c"]);
            assert_eq!(runtime.last_output_path, "/out/a.png");
        }
        assert_eq!(status(&state, "a"), "completed");
        assert_eq!(status(&state, "b"), "running");
        assert_eq!(status(&state, "c"), "queued");
        // A photo that fails to open before it starts fails alone.
        apply(
            "job_failed",
            json!({"job_id": "c", "error_message": "cannot identify image file"}),
        )
        .unwrap();
        assert_eq!(status(&state, "c"), "failed");
        assert_eq!(lock(&state.runtime).unwrap().active_job_id, "b");
        apply(
            "job_completed",
            json!({"job_id": "b", "output_path": "/out/b.png"}),
        )
        .unwrap();
        let runtime = lock(&state.runtime).unwrap();
        assert!(runtime.active_job_id.is_empty() && runtime.active_batch_job_ids.is_empty());
        assert_eq!(runtime.status_title, "Complete");
        assert_eq!(runtime.last_output_path, "/out/b.png");
    }

    #[test]
    fn messages_about_work_the_host_never_sent_change_nothing() {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(
            AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
        );
        for (kind, data) in [
            (
                "job_completed",
                json!({"job_id": "ghost", "output_path": "/etc/hosts"}),
            ),
            (
                "video_job_completed",
                json!({"job_id": "ghost", "output_path": "/etc/hosts"}),
            ),
            ("job_completed", json!({"output_path": "/etc/hosts"})),
            ("job_started", json!({"job_id": "ghost"})),
            ("progress", json!({"job_id": "ghost", "percentage": 5.0})),
            (
                "benchmark_started",
                json!({"job_id": "ghost", "workload_version": "v2"}),
            ),
        ] {
            assert!(
                !accept_envelope(&state, &envelope(kind, data)).unwrap(),
                "{kind}"
            );
        }
        let runtime = lock(&state.runtime).unwrap();
        assert!(runtime.last_output_path.is_empty());
        assert!(runtime.active_job_id.is_empty());
    }

    #[test]
    fn a_completion_must_name_the_output_the_host_requested() {
        let directory = tempfile::tempdir().unwrap();
        let state = running_batch(directory.path());
        let requested = directory.path().join("out").join("a.png");
        let done = |path: &std::path::Path| {
            envelope(
                "job_completed",
                json!({"job_id": "a", "output_path": path.to_string_lossy()}),
            )
        };
        assert!(accept_envelope(&state, &envelope("job_started", json!({"job_id": "a"}))).unwrap());
        // Another file the engine can read, and a sibling that was never requested.
        for forged in [
            std::path::Path::new("/etc/hosts"),
            &directory.path().join("out").join("b.png"),
            &directory.path().join("out").join("A.png"),
            &directory.path().join("a.png"),
        ] {
            assert!(!accept_envelope(&state, &done(forged)).unwrap());
        }
        {
            let runtime = lock(&state.runtime).unwrap();
            assert!(runtime.last_output_path.is_empty());
            assert_eq!(runtime.active_job_id, "a");
        }
        assert_eq!(status(&state, "a"), "running");
        assert!(accept_envelope(&state, &done(&requested)).unwrap());
        assert_eq!(status(&state, "a"), "completed");
        assert_eq!(
            lock(&state.runtime).unwrap().last_output_path,
            requested.to_string_lossy()
        );
        // The same completion again is a message about a job that has ended.
        assert!(!accept_envelope(&state, &done(&requested)).unwrap());
    }

    #[test]
    fn a_batch_photo_completes_only_with_its_own_output() {
        let directory = tempfile::tempdir().unwrap();
        let state = running_batch(directory.path());
        let own = |id: &str| directory.path().join("out").join(format!("{id}.png"));
        let done = |id: &str, path: &std::path::Path| {
            envelope(
                "job_completed",
                json!({"job_id": id, "output_path": path.to_string_lossy()}),
            )
        };
        // c off screen reports the file of a.
        assert!(!accept_envelope(&state, &done("c", &own("a"))).unwrap());
        assert_eq!(status(&state, "c"), "queued");
        assert!(lock(&state.runtime).unwrap().last_output_path.is_empty());
        assert!(accept_envelope(&state, &done("c", &own("c"))).unwrap());
        assert_eq!(status(&state, "c"), "completed");
        assert_eq!(
            lock(&state.runtime).unwrap().last_output_path,
            own("c").to_string_lossy()
        );
    }

    #[test]
    fn a_video_completion_must_name_the_requested_video() {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(
            AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
        );
        let output = directory.path().join("clip_video_4x.mp4");
        {
            let database = lock(&state.database).unwrap();
            database
                .insert_media("m", "/in/clip.mp4", "clip.mp4", "video")
                .unwrap();
            let request = json!({"type": "video_job_request", "data": {
                "job_id": "v", "output_video_path": output.to_string_lossy()}});
            database.insert_job("v", "m", &request.to_string()).unwrap();
        }
        lock(&state.runtime).unwrap().begin_active_job("v".into());
        let done = |path: &str| {
            envelope(
                "video_job_completed",
                json!({"job_id": "v", "output_path": path, "frames_processed": 3}),
            )
        };
        assert!(!accept_envelope(&state, &done("/etc/hosts")).unwrap());
        assert!(accept_envelope(&state, &done(&output.to_string_lossy())).unwrap());
        assert_eq!(status(&state, "v"), "completed");
    }

    #[test]
    fn a_failed_item_keeps_the_engines_reason_in_the_history() {
        let directory = tempfile::tempdir().unwrap();
        let state = running_batch(directory.path());
        let reason = "The output file appeared while the photo was processed; it was not replaced.";
        assert!(accept_envelope(
            &state,
            &envelope(
                "job_failed",
                json!({"job_id": "b", "error_message": reason})
            )
        )
        .unwrap());
        let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
        let job = jobs.iter().find(|job| job.id == "b").unwrap();
        assert_eq!(
            (job.status.as_str(), job.error.as_str()),
            ("failed", reason)
        );
        assert!(job.output_path.is_empty());
        assert_eq!(lock(&state.runtime).unwrap().active_job_id, "a");
    }

    #[test]
    fn when_the_shown_photo_ends_first_the_batch_stays_on_screen() {
        let directory = tempfile::tempdir().unwrap();
        let state = running_batch(directory.path());
        apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "a"}))).unwrap();
        apply_worker_envelope(
            &state,
            &envelope(
                "job_failed",
                json!({"job_id": "a", "error_message": "disk full"}),
            ),
        )
        .unwrap();
        let runtime = lock(&state.runtime).unwrap();
        // Still busy: settings stay locked and nothing else is dispatched.
        assert_eq!(runtime.active_job_id, "b");
        assert_eq!(runtime.active_batch_job_ids, ["b", "c"]);
        // The failure stays on the status line until the next photo starts.
        assert_eq!(runtime.status_title, "Could not finish");
        assert_eq!(runtime.status_detail, "disk full");
        drop(runtime);
        assert_eq!(status(&state, "a"), "failed");
        apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "b"}))).unwrap();
        apply_worker_envelope(
            &state,
            &envelope(
                "job_completed",
                json!({"job_id": "b", "output_path": "/out/b.png"}),
            ),
        )
        .unwrap();
        assert_eq!(lock(&state.runtime).unwrap().status_title, "Processing");
    }

    #[test]
    fn the_last_photo_of_a_batch_gets_its_full_preview() {
        let directory = tempfile::tempdir().unwrap();
        let state = running_batch(directory.path());
        let apply = |kind: &str, data: Value| apply_worker_envelope(&state, &envelope(kind, data));
        apply("job_started", json!({"job_id": "a"})).unwrap();
        apply("job_started", json!({"job_id": "b"})).unwrap();
        // a saved off screen: no full preview is requested for it.
        assert!(is_background_photo(&lock(&state.runtime).unwrap(), "a"));
        apply(
            "job_completed",
            json!({"job_id": "a", "output_path": "/out/a.png"}),
        )
        .unwrap();
        apply("job_cancelled", json!({"job_id": "c"})).unwrap();
        // b is on screen when it completes, so its preview is requested and shown.
        assert!(!is_background_photo(&lock(&state.runtime).unwrap(), "b"));
        let done = envelope(
            "job_completed",
            json!({"job_id": "b", "output_path": "/out/b.png"}),
        );
        assert!(completion_preview_request(&done).is_some());
        apply_worker_envelope(&state, &done).unwrap();
        apply(
            "preview_ready",
            json!({"image_path": "/out/b.png", "jpeg_base64": "full-b"}),
        )
        .unwrap();
        assert_eq!(
            lock(&state.runtime).unwrap().result_preview_data_url,
            "data:image/jpeg;base64,full-b"
        );
    }

    #[test]
    fn a_photo_saved_off_screen_drops_a_preview_that_is_not_its_own() {
        let directory = tempfile::tempdir().unwrap();
        let state = running_batch(directory.path());
        apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "b"}))).unwrap();
        apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "a"}))).unwrap();
        lock(&state.runtime).unwrap().result_preview_data_url = "a-pixels".into();
        apply_worker_envelope(
            &state,
            &envelope(
                "job_completed",
                json!({"job_id": "b", "output_path": "/out/b.png"}),
            ),
        )
        .unwrap();
        // last_output_path now names b, so no picture of a may stay beside it.
        let runtime = lock(&state.runtime).unwrap();
        assert_eq!(runtime.last_output_path, "/out/b.png");
        assert!(runtime.result_preview_data_url.is_empty());
    }

    #[test]
    fn a_background_photo_that_lost_the_gpu_holds_the_queue_for_the_restart() {
        let directory = tempfile::tempdir().unwrap();
        let state = running_batch(directory.path());
        apply_worker_envelope(
            &state,
            &envelope(
                "job_failed",
                json!({"job_id": "b", "error_message": "device lost", "restart_required": true}),
            ),
        )
        .unwrap();
        let runtime = lock(&state.runtime).unwrap();
        assert_eq!(runtime.worker, "restarting");
        assert_eq!(runtime.active_job_id, "a");
    }

    #[test]
    fn after_a_crash_the_started_batch_photos_run_again_alone_once() {
        let directory = tempfile::tempdir().unwrap();
        let state = batch_state(directory.path());
        let ids: Vec<String> = ["a", "b", "c"].map(String::from).to_vec();
        {
            let db = lock(&state.database).unwrap();
            db.set_job_status("a", "running").unwrap();
            db.set_job_status("b", "running").unwrap();
            settle_stopped_jobs(&db, &ids, &[], "The engine stopped.");
        }
        // Any of the started photos may have crashed the engine: each runs alone next.
        for id in ["a", "b", "c"] {
            assert_eq!(status(&state, id), "queued");
        }
        let window = lock(&state.database)
            .unwrap()
            .queued_job_window(MAX_BATCH_PHOTOS)
            .unwrap();
        let (sent, message) = next_dispatch(&window, |_| true).unwrap().unwrap();
        assert_eq!(sent, ["a"]);
        assert_eq!(message["type"], "job_request");
        // The hint stays with the desktop; the engine gets the photo's own request.
        assert!(message["data"].get("run_alone").is_none());
        // Run alone, a photo that crashes the engine again is interrupted.
        {
            let db = lock(&state.database).unwrap();
            db.set_job_status("a", "running").unwrap();
            settle_stopped_jobs(&db, &["a".to_string()], &[], "The engine stopped.");
        }
        assert_eq!(status(&state, "a"), "interrupted");
    }

    #[test]
    fn after_an_engine_stop_unstarted_batch_photos_are_queued_again() {
        let directory = tempfile::tempdir().unwrap();
        let state = batch_state(directory.path());
        let ids: Vec<String> = ["a", "b", "c"].map(String::from).to_vec();
        {
            let db = lock(&state.database).unwrap();
            db.set_job_status("a", "running").unwrap();
            settle_stopped_jobs(&db, &ids, &[], "The engine stopped.");
        }
        // Even one started photo may not be the cause: the photo decoded ahead of it
        // had not started yet. It runs again alone; the others stay queued.
        assert_eq!(status(&state, "a"), "queued");
        assert_eq!(status(&state, "b"), "queued");
        let window = lock(&state.database)
            .unwrap()
            .queued_job_window(MAX_BATCH_PHOTOS)
            .unwrap();
        let (sent, _) = next_dispatch(&window, |_| true).unwrap().unwrap();
        assert_eq!(sent, ["a"]);
        let rest: Vec<_> = window.into_iter().skip(1).collect();
        let (sent, _) = next_dispatch(&rest, |_| true).unwrap().unwrap();
        assert_eq!(sent, ["b", "c"]);
        {
            let db = lock(&state.database).unwrap();
            db.set_job_status("b", "running").unwrap();
            settle_stopped_jobs(&db, &ids, &ids, "The engine stopped.");
        }
        // A cancellation that had to stop the engine cancels every photo it covered.
        assert_eq!(status(&state, "b"), "cancelled");
        assert_eq!(status(&state, "c"), "cancelled");
    }

    #[test]
    fn a_crash_while_cancelling_a_batch_never_leaves_a_photo_cancelling() {
        let directory = tempfile::tempdir().unwrap();
        let state = batch_state(directory.path());
        let ids: Vec<String> = ["a", "b", "c"].map(String::from).to_vec();
        {
            let db = lock(&state.database).unwrap();
            db.set_job_status("a", "cancelling").unwrap();
            db.set_job_status("b", "running").unwrap();
            settle_stopped_jobs(&db, &ids, &[], "The engine stopped.");
        }
        assert_eq!(status(&state, "a"), "interrupted");
        assert_eq!(status(&state, "b"), "queued");
    }
}
