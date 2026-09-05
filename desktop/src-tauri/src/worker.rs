use std::{
    env,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Command, Stdio},
    sync::{atomic::Ordering, mpsc, Arc},
    thread,
    time::{Duration, Instant},
};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_notification::NotificationExt;

use crate::{
    error::{AppError, AppResult},
    settings,
    state::{emit_state_changed, lock, AppState},
    types::{BenchmarkResult, CapabilityInfo, EngineInfo, WorkerEnvelope, PROTOCOL_VERSION},
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
    last_tile_started: Option<Instant>,
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
                "started" => sampled(
                    &mut self.last_tile_started,
                    now,
                    false,
                    Duration::from_millis(80),
                ),
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
            "video_frame_completed" => sampled(
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
        self.last_tile_started = None;
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
    }
    command.env("PYTHONUNBUFFERED", "1");

    let mut child = command.spawn().map_err(|error| {
        AppError::Worker(format!(
            "could not start the LocalSR inference engine: {error}"
        ))
    })?;
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
    thread::spawn(move || {
        let mut event_gate = WorkerEventGate::default();
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else {
                break;
            };
            match serde_json::from_str::<WorkerEnvelope>(&line) {
                Ok(envelope) => {
                    if event_gate.should_forward(&envelope, Instant::now()) {
                        handle_worker_envelope(&reader_state, &reader_app, envelope);
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
        }
    });

    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            if cfg!(debug_assertions) {
                eprintln!("LocalSR engine: {line}");
            }
        }
    });

    thread::spawn(move || {
        let status = child.wait();
        if state.worker.generation.load(Ordering::SeqCst) != generation
            || state.worker.shutting_down.load(Ordering::SeqCst)
        {
            return;
        }
        if let Ok(mut sender) = state.worker.sender.lock() {
            *sender = None;
        }
        if let Ok(mut runtime) = state.runtime.lock() {
            let active = std::mem::take(&mut runtime.active_job_id);
            let last_confirmed_stage = runtime.status_title.clone();
            let exit_summary = match &status {
                Ok(exit) => format!("The inference engine exited with {exit}"),
                Err(error) => {
                    format!("The inference engine could not report its exit status: {error}")
                }
            };
            let interruption = worker_interruption_detail(&exit_summary, &last_confirmed_stage);
            runtime.worker = "unavailable".into();
            runtime.status_title = "Engine stopped".into();
            runtime.status_detail = format!("{interruption} Restarting…");
            if !active.is_empty() {
                if let Ok(database) = state.database.lock() {
                    let _ = database.interrupt_active_job(&active, &interruption);
                }
            }
        }
        emit_state_changed(&app);

        let attempt = state.worker.restart_count.fetch_add(1, Ordering::SeqCst) + 1;
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

pub fn dispatch_next(state: &Arc<AppState>, app: &AppHandle) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    if !lock(&state.runtime)?.active_job_id.is_empty() {
        return Ok(());
    }
    let pending = lock(&state.database)?.next_queued_job()?;
    let Some(pending) = pending else {
        return Ok(());
    };
    let message: Value = serde_json::from_str(&pending.request_json)?;
    {
        let mut runtime = lock(&state.runtime)?;
        if runtime.worker != "ready" {
            return Ok(());
        }
        runtime.active_job_id = pending.id.clone();
        runtime.status_title = "Queued".into();
        runtime.status_detail = "Sending the next item to the inference engine.".into();
        runtime.progress = 0.0;
        runtime.result_preview_data_url.clear();
    }
    lock(&state.database)?.set_job_status(&pending.id, "starting")?;
    if let Err(error) = send(state, &message) {
        lock(&state.database)?.finish_job(&pending.id, "failed", "", &error.to_string())?;
        lock(&state.runtime)?.active_job_id.clear();
        return Err(error);
    }
    emit_state_changed(app);
    Ok(())
}

pub fn shutdown(state: &AppState) {
    state.worker.shutting_down.store(true, Ordering::SeqCst);
    let _ = send(state, &json!({"type": "shutdown_request", "data": {}}));
    if let Ok(mut sender) = state.worker.sender.lock() {
        *sender = None;
    }
}

fn handle_worker_envelope(state: &Arc<AppState>, app: &AppHandle, envelope: WorkerEnvelope) {
    let result = apply_worker_envelope(state, &envelope);
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
    if let Some(request) = completion_preview_request(&envelope) {
        if let Err(error) = send(state, &request) {
            if cfg!(debug_assertions) {
                eprintln!("Could not request completed preview: {error}");
            }
        }
    }

    match envelope.message_type.as_str() {
        "job_completed" | "video_job_completed" => {
            let _ = app
                .notification()
                .builder()
                .title("LocalSR finished")
                .body("Your enhanced media is ready.")
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

fn apply_worker_envelope(state: &Arc<AppState>, envelope: &WorkerEnvelope) -> AppResult<()> {
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
                        "client_name": "LocalSR Next Preview",
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
                send(
                    state,
                    &json!({
                        "type": "media_probe_request",
                        "data": {"media_path": path, "max_dimension": 2048}
                    }),
                )?;
            }
        }
        "capabilities_info" => {
            *lock(&state.capabilities)? = serde_json::from_value::<CapabilityInfo>(data.clone())?;
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
        "preview_ready" => {
            let path = string(data, "image_path");
            let encoded = string(data, "jpeg_base64");
            if !encoded.is_empty() {
                let mut runtime = lock(&state.runtime)?;
                if runtime.active_job_id.is_empty() && runtime.last_output_path == path {
                    runtime.result_preview_data_url = format!("data:image/jpeg;base64,{encoded}");
                }
            }
        }
        "job_started" => {
            let id = string(data, "job_id");
            lock(&state.database)?.set_job_status(&id, "running")?;
            let mut runtime = lock(&state.runtime)?;
            runtime.active_job_id = id;
            runtime.status_title = "Processing".into();
            runtime.status_detail = "The model is preparing the first tile.".into();
            runtime.elapsed_seconds = 0.0;
            runtime.estimated_remaining_seconds = 0.0;
            runtime.throughput = 0.0;
            runtime.throughput_unit = "tiles/s".into();
            runtime.active_tile_size = 0;
            runtime.result_preview_data_url.clear();
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
            runtime.status_title = "Enhancing".into();
            let remaining = number(data, "estimated_remaining_seconds");
            runtime.status_detail = format!(
                "{} of {} tiles{}",
                integer(data, "completed_tiles"),
                integer(data, "total_tiles"),
                eta_suffix(remaining)
            );
            runtime.elapsed_seconds = number(data, "elapsed_seconds");
            runtime.estimated_remaining_seconds = number(data, "estimated_remaining_seconds");
            let completed = integer(data, "completed_tiles") as f64;
            runtime.throughput = if runtime.elapsed_seconds > 0.0 {
                completed / runtime.elapsed_seconds
            } else {
                0.0
            };
            runtime.throughput_unit = "tiles/s".into();
            runtime.active_tile_size = integer(data, "active_tile_size") as u32;
            runtime.device_free_memory = integer(data, "device_free_memory");
            runtime.device_allocated_memory = integer(data, "device_allocated_memory");
            runtime.live_system_ram_available = integer(data, "system_ram_available");
            runtime.live_memory_pressure_percent = number(data, "system_memory_pressure_percent");
        }
        // Tile JPEGs are coordinates plus pixels, not complete images. The
        // webview composites them into its bounded live canvas just as the
        // released Slint image provider does.
        "tile_update" => {}
        "video_frame_started" => {
            let mut runtime = lock(&state.runtime)?;
            runtime.status_title = "Enhancing video · Labs".into();
            runtime.status_detail = format!(
                "Frame {} of {}",
                integer(data, "frame_index") + 1,
                integer(data, "total_frames")
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
            runtime.active_job_id = string(data, "job_id");
            let is_v2 = string(data, "workload_version").starts_with("localsr-benchmark-v2");
            runtime.status_title = "Benchmark running".into();
            runtime.status_detail = if is_v2 {
                format!(
                    "Multi-device benchmark · {} phase(s) · warming up each device.",
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
            let result: BenchmarkResult = serde_json::from_value(data["result"].clone())?;
            settings::save_benchmark(&state.paths, &result)?;
            *lock(&state.latest_benchmark)? = Some(result.clone());
            let mut runtime = lock(&state.runtime)?;
            runtime.active_job_id.clear();
            runtime.progress = 100.0;
            runtime.status_title = "Benchmark complete".into();
            runtime.status_detail = if result.is_v2() {
                if result.stable {
                    if let Some(system_score) = result.system_score {
                        format!(
                            "System score {:.2} · CPU {:.2} output MP/s · stable ({:.1}% spread).",
                            system_score,
                            result.cpu_score.unwrap_or_default(),
                            result.cv_percent
                        )
                    } else {
                        format!(
                            "CPU score {:.2} output MP/s · stable ({:.1}% spread).",
                            result.cpu_score.unwrap_or_default(),
                            result.cv_percent
                        )
                    }
                } else {
                    format!(
                        "Unstable run ({:.1}% spread) — close background apps and retry.",
                        result.cv_percent
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
                runtime.throughput = result.system_score.unwrap_or_default();
                runtime.throughput_unit = "output MP/s".into();
                runtime.thermal_status = result.thermal_state.clone();
            } else {
                runtime.throughput = result.end_to_end_fps;
                runtime.throughput_unit = "frames/s".into();
            }
        }
        "benchmark_cancelled" => {
            let mut runtime = lock(&state.runtime)?;
            runtime.active_job_id.clear();
            runtime.progress = 0.0;
            runtime.status_title = "Benchmark cancelled".into();
            runtime.status_detail = "No partial benchmark result was stored.".into();
        }
        "benchmark_failed" => {
            let mut runtime = lock(&state.runtime)?;
            runtime.active_job_id.clear();
            runtime.progress = 0.0;
            runtime.status_title = "Benchmark failed".into();
            runtime.status_detail = string(data, "error_message");
        }
        "job_completed" | "video_job_completed" => {
            let id = string(data, "job_id");
            let output = string(data, "output_path");
            lock(&state.database)?.finish_job(&id, "completed", &output, "")?;
            let mut runtime = lock(&state.runtime)?;
            runtime.active_job_id.clear();
            // A video frame thumbnail is a live preview, not a completed
            // before/after result. The full-output request issued by the host
            // will populate this again only after it has decoded successfully.
            runtime.result_preview_data_url.clear();
            runtime.progress = 100.0;
            runtime.last_output_path = output;
            runtime.status_title = "Complete".into();
            runtime.status_detail = "The output was written successfully.".into();
            runtime.elapsed_seconds =
                number(data, "inference_seconds").max(number(data, "elapsed_seconds"));
            runtime.estimated_remaining_seconds = 0.0;
        }
        "job_cancelled" => {
            let id = string(data, "job_id");
            lock(&state.database)?.finish_job(&id, "cancelled", "", "Cancelled by the user.")?;
            let mut runtime = lock(&state.runtime)?;
            runtime.active_job_id.clear();
            runtime.status_title = "Cancelled".into();
            runtime.status_detail = "Temporary output was cleaned up.".into();
            runtime.estimated_remaining_seconds = 0.0;
        }
        "job_failed" => {
            let id = string(data, "job_id");
            let error = string(data, "error_message");
            lock(&state.database)?.finish_job(&id, "failed", "", &error)?;
            let mut runtime = lock(&state.runtime)?;
            runtime.active_job_id.clear();
            runtime.status_title = "Could not finish".into();
            runtime.status_detail = error;
            runtime.estimated_remaining_seconds = 0.0;
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

fn resolve_worker_command(_state: &AppState, app: &AppHandle) -> AppResult<WorkerCommand> {
    #[cfg(debug_assertions)]
    {
        if let Some(path) = env::var_os("LOCALSR_WORKER") {
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
    if total >= 60 {
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

fn stage_label(kind: &str) -> &'static str {
    match kind {
        "deblock" => "Removing JPEG artifacts",
        "restore" => "Restoring",
        "upscale" => "Upscaling",
        "face_restore" => "Restoring faces",
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
            | "stage_progress"
            | "tile_update"
            | "video_frame_started"
            | "video_frame_completed"
            | "benchmark_progress"
            | "benchmark_stage_started"
            | "benchmark_stage_progress"
            | "benchmark_stage_completed"
    )
}

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
            "data": {"image_path": output_path, "max_dimension": 2048}
        })),
        "video_job_completed" => Some(json!({
            "type": "media_probe_request",
            "data": {"media_path": output_path, "max_dimension": 2048}
        })),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn capability_contract_preserves_every_supported_backend_identifier() {
        let device_specs = [
            ("mps", "mps", true),
            ("cpu", "cpu", false),
            ("cuda:0", "cuda", false),
            ("cuda:0", "rocm", false),
            ("xpu:0", "xpu", true),
            ("directml:0", "directml", true),
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
}
