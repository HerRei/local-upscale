use std::{
    env,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, ExitStatus, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

use serde_json::{json, Value};

use crate::types::{EngineInfo, WorkerEnvelope, PROTOCOL_VERSION};

const START_TIMEOUT: Duration = Duration::from_secs(180);
const MIN_SHUTDOWN_TIMEOUT: u64 = 15;
const MAX_SHUTDOWN_TIMEOUT: u64 = 120;
const POLL_INTERVAL: Duration = Duration::from_millis(250);
const PRODUCT_NAME: &str = "LocalSR Next Preview";

pub fn run() -> i32 {
    let timeout = smoke_timeout();
    let worker_path = packaged_worker_path();
    let result = worker_path
        .as_ref()
        .map_err(|error| error.clone())
        .and_then(|path| run_worker_handshake(path, timeout));

    let (worker, passed, status_title, status_detail, engine) = match result {
        Ok(info) => (
            "ready",
            true,
            "Ready",
            "The installed Rust host completed a protocol handshake with the bundled inference engine."
                .to_owned(),
            Some(info),
        ),
        Err(error) => (
            "failed",
            false,
            "Engine unavailable",
            error,
            None,
        ),
    };
    let report = json!({
        "app": "LocalSR Next Preview",
        "version": env!("CARGO_PKG_VERSION"),
        "platform": env::consts::OS,
        "architecture": env::consts::ARCH,
        "mode": "headless-installed-host",
        "worker": worker,
        "worker_path": worker_path.ok().map(|path| path.to_string_lossy().into_owned()),
        "passed": passed,
        "status_title": status_title,
        "status_detail": status_detail,
        "engine_id": engine.as_ref().map(|info| info.engine_id.as_str()),
        "engine_version": engine.as_ref().map(|info| info.engine_version.as_str()),
        "protocol_version": engine.as_ref().map(|info| info.protocol_version),
    });

    if let Some(report_path) = env::var_os("LOCALSR_SMOKE_REPORT") {
        if let Err(error) = write_report(Path::new(&report_path), &report) {
            eprintln!("could not write the installed-host smoke report: {error}");
            return 1;
        }
    }
    match serde_json::to_string_pretty(&report) {
        Ok(encoded) => println!("{encoded}"),
        Err(error) => eprintln!("could not encode the installed-host smoke report: {error}"),
    }
    i32::from(!passed)
}

fn packaged_worker_path() -> Result<PathBuf, String> {
    if let Ok(paths) = crate::paths::AppPaths::discover() {
        if let Some(path) = crate::updates::active_engine(&paths) {
            return Ok(path);
        }
    }
    let executable = env::current_exe()
        .map_err(|error| format!("could not locate the installed desktop host: {error}"))?;
    let worker_name = if cfg!(windows) {
        "localsr-worker.exe"
    } else {
        "localsr-worker"
    };
    let appdir = trusted_appimage_dir(&executable);
    worker_candidates(&executable, worker_name, appdir.as_deref())
        .into_iter()
        .find(|path| path.is_file())
        .ok_or_else(|| {
            "the installed desktop host is missing its bundled inference engine".to_owned()
        })
}

fn trusted_appimage_dir(executable: &Path) -> Option<PathBuf> {
    let appdir = env::var_os("APPDIR").map(PathBuf::from)?;
    let canonical_appdir = appdir.canonicalize().ok()?;
    let canonical_executable = executable.canonicalize().ok()?;
    canonical_executable
        .starts_with(&canonical_appdir)
        .then_some(canonical_appdir)
}

fn worker_candidates(executable: &Path, worker_name: &str, appdir: Option<&Path>) -> Vec<PathBuf> {
    let Some(parent) = executable.parent() else {
        return Vec::new();
    };
    let mut candidates = vec![
        parent.join("engine").join(worker_name),
        parent.join("resources").join("engine").join(worker_name),
        parent.join(worker_name),
    ];
    // A macOS executable lives in App.app/Contents/MacOS while Tauri resources
    // live in App.app/Contents/Resources. Keeping this candidate also makes the
    // headless path useful for local package diagnostics on macOS.
    if let Some(contents) = parent.parent() {
        candidates.push(contents.join("Resources").join("engine").join(worker_name));
    }
    if let Some(appdir) = appdir {
        // Tauri stores AppImage resources in APPDIR/usr/lib/<productName>.
        // Keep the Cargo package name as a compatibility fallback for locally
        // produced bundles whose package metadata predates productName.
        candidates.push(
            appdir
                .join("usr")
                .join("lib")
                .join(PRODUCT_NAME)
                .join("engine")
                .join(worker_name),
        );
        candidates.push(
            appdir
                .join("usr")
                .join("lib")
                .join(env!("CARGO_PKG_NAME"))
                .join("engine")
                .join(worker_name),
        );
    }
    candidates
}

pub(crate) fn run_worker_handshake(path: &Path, timeout: Duration) -> Result<EngineInfo, String> {
    let mut command = Command::new(path);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;

        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("PYTHONUNBUFFERED", "1");

    let mut child = command.spawn().map_err(|error| {
        format!(
            "could not start the bundled inference engine at {}: {error}",
            path.display()
        )
    })?;
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| "the bundled inference engine did not expose stdin".to_owned())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "the bundled inference engine did not expose stdout".to_owned())?;
    let mut stderr = child
        .stderr
        .take()
        .ok_or_else(|| "the bundled inference engine did not expose stderr".to_owned())?;

    let (sender, receiver) = mpsc::channel::<Result<WorkerEnvelope, String>>();
    let stdout_thread = thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            let envelope = line
                .map_err(|error| format!("could not read the inference engine response: {error}"))
                .and_then(|line| {
                    serde_json::from_str::<WorkerEnvelope>(&line).map_err(|error| {
                        format!("the inference engine sent an invalid JSON response: {error}")
                    })
                });
            if sender.send(envelope).is_err() {
                break;
            }
        }
    });
    let stderr_thread = thread::spawn(move || {
        let mut output = String::new();
        let _ = stderr.read_to_string(&mut output);
        output
    });

    let handshake = wait_for_handshake(&mut child, &mut stdin, &receiver, timeout);
    let shutdown = send_message(&mut stdin, &json!({"type": "shutdown_request", "data": {}}));
    drop(stdin);
    let exit_status = wait_for_exit(&mut child, shutdown_timeout(timeout));
    let _ = stdout_thread.join();
    let stderr = stderr_thread.join().unwrap_or_default();

    let result = handshake
        .and_then(|info| shutdown.map(|()| info))
        .and_then(|info| match exit_status {
            Ok(status) if status.success() => Ok(info),
            Ok(status) => Err(format!(
                "the bundled inference engine exited with {status} after the handshake"
            )),
            Err(error) => Err(error),
        });
    result.map_err(|error| append_stderr(error, &stderr))
}

fn smoke_timeout() -> Duration {
    std::env::var("LOCALSR_SMOKE_TIMEOUT_SECONDS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .map(|seconds| Duration::from_secs(seconds.clamp(30, 1800)))
        .unwrap_or(START_TIMEOUT)
}

fn shutdown_timeout(smoke_timeout: Duration) -> Duration {
    let seconds = (smoke_timeout.as_secs() / 10).clamp(MIN_SHUTDOWN_TIMEOUT, MAX_SHUTDOWN_TIMEOUT);
    Duration::from_secs(seconds)
}

fn wait_for_handshake(
    child: &mut Child,
    stdin: &mut ChildStdin,
    receiver: &mpsc::Receiver<Result<WorkerEnvelope, String>>,
    timeout: Duration,
) -> Result<EngineInfo, String> {
    let deadline = Instant::now() + timeout;
    let mut requested_handshake = false;
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("could not inspect the inference engine: {error}"))?
        {
            return Err(format!(
                "the bundled inference engine exited with {status} before completing the handshake"
            ));
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(format!(
                "the bundled inference engine did not complete protocol negotiation within {} seconds",
                timeout.as_secs()
            ));
        }
        match receiver.recv_timeout(remaining.min(POLL_INTERVAL)) {
            Ok(Ok(envelope)) if envelope.message_type == "worker_ready" => {
                if !requested_handshake {
                    send_message(
                        stdin,
                        &json!({
                            "type": "handshake_request",
                            "data": {
                                "client_name": "LocalSR Next Preview",
                                "client_version": env!("CARGO_PKG_VERSION"),
                                "protocol_version": PROTOCOL_VERSION,
                            }
                        }),
                    )?;
                    requested_handshake = true;
                }
            }
            Ok(Ok(envelope)) if envelope.message_type == "engine_info" => {
                if !requested_handshake {
                    return Err(
                        "the inference engine answered before receiving a handshake request".into(),
                    );
                }
                let info = serde_json::from_value::<EngineInfo>(envelope.data)
                    .map_err(|error| format!("the engine_info response was invalid: {error}"))?;
                validate_engine_info(&info)?;
                return Ok(info);
            }
            Ok(Ok(envelope)) if envelope.message_type == "protocol_error" => {
                let detail = envelope
                    .data
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("the bundled inference engine rejected the protocol handshake");
                return Err(detail.to_owned());
            }
            Ok(Ok(_)) => {}
            Ok(Err(error)) => return Err(error),
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                return Err("the inference engine closed stdout before the handshake".into());
            }
        }
    }
}

fn validate_engine_info(info: &EngineInfo) -> Result<(), String> {
    if info.minimum_protocol_version > PROTOCOL_VERSION || info.protocol_version < PROTOCOL_VERSION
    {
        return Err(format!(
            "inference protocol mismatch: app {}, engine {}–{}",
            PROTOCOL_VERSION, info.minimum_protocol_version, info.protocol_version
        ));
    }
    if info.engine_id != "localsr.pytorch-spandrel" {
        return Err(format!(
            "unexpected bundled inference engine identity: {}",
            info.engine_id
        ));
    }
    Ok(())
}

fn send_message(stdin: &mut ChildStdin, message: &Value) -> Result<(), String> {
    let encoded = serde_json::to_string(message)
        .map_err(|error| format!("could not encode a worker request: {error}"))?;
    writeln!(stdin, "{encoded}")
        .and_then(|()| stdin.flush())
        .map_err(|error| format!("could not write to the bundled inference engine: {error}"))
}

fn wait_for_exit(child: &mut Child, timeout: Duration) -> Result<ExitStatus, String> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("could not inspect the inference engine: {error}"))?
        {
            return Ok(status);
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!(
                "the bundled inference engine did not stop within {} seconds",
                timeout.as_secs()
            ));
        }
        thread::sleep(POLL_INTERVAL);
    }
}

fn append_stderr(error: String, stderr: &str) -> String {
    let stderr = stderr.trim();
    if stderr.is_empty() {
        return error;
    }
    let tail = if stderr.chars().count() > 2_000 {
        stderr
            .char_indices()
            .rev()
            .nth(1_999)
            .map_or(stderr, |(index, _)| &stderr[index..])
    } else {
        stderr
    };
    format!("{error}. Engine stderr: {tail}")
}

fn write_report(path: &Path, report: &Value) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, serde_json::to_vec_pretty(report).unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packaged_worker_candidates_cover_windows_and_macos_bundle_layouts() {
        let candidates = worker_candidates(
            Path::new("/install/LocalSR Next Preview.exe"),
            "localsr-worker.exe",
            None,
        );
        assert_eq!(
            candidates[0],
            Path::new("/install/engine/localsr-worker.exe")
        );

        let candidates = worker_candidates(
            Path::new("/Applications/LocalSR.app/Contents/MacOS/localsr-next"),
            "localsr-worker",
            None,
        );
        assert!(candidates.contains(&PathBuf::from(
            "/Applications/LocalSR.app/Contents/Resources/engine/localsr-worker"
        )));
    }

    #[test]
    fn packaged_worker_candidates_cover_tauri_appimage_layout() {
        let candidates = worker_candidates(
            Path::new("/tmp/.mount_LocalSR/usr/bin/localsr-next"),
            "localsr-worker",
            Some(Path::new("/tmp/.mount_LocalSR")),
        );
        assert!(candidates.contains(&PathBuf::from(
            "/tmp/.mount_LocalSR/usr/lib/LocalSR Next Preview/engine/localsr-worker"
        )));
        assert!(candidates.contains(&PathBuf::from(
            "/tmp/.mount_LocalSR/usr/lib/localsr-next/engine/localsr-worker"
        )));
    }

    #[test]
    fn rejects_an_unexpected_or_incompatible_worker() {
        let valid = EngineInfo {
            protocol_version: PROTOCOL_VERSION,
            minimum_protocol_version: PROTOCOL_VERSION,
            engine_id: "localsr.pytorch-spandrel".into(),
            engine_version: "0.0.9a0".into(),
            features: vec!["image".into()],
            model_formats: vec![".safetensors".into()],
            video_engines: Vec::new(),
        };
        assert!(validate_engine_info(&valid).is_ok());

        let mut wrong_identity = valid.clone();
        wrong_identity.engine_id = "unexpected.worker".into();
        assert!(validate_engine_info(&wrong_identity).is_err());

        let mut incompatible = valid;
        incompatible.minimum_protocol_version = PROTOCOL_VERSION + 1;
        assert!(validate_engine_info(&incompatible).is_err());
    }

    #[test]
    fn shutdown_timeout_scales_with_smoke_timeout_inside_bounds() {
        assert_eq!(
            shutdown_timeout(Duration::from_secs(30)),
            Duration::from_secs(MIN_SHUTDOWN_TIMEOUT)
        );
        assert_eq!(
            shutdown_timeout(Duration::from_secs(600)),
            Duration::from_secs(60)
        );
        assert_eq!(
            shutdown_timeout(Duration::from_secs(1800)),
            Duration::from_secs(MAX_SHUTDOWN_TIMEOUT)
        );
    }
}
