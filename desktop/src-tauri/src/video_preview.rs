use std::{
    fs,
    io::{BufReader, Read},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant, UNIX_EPOCH},
};

use crate::{
    error::{AppError, AppResult},
    pipe_lines::{read_frame, Frame},
    process_tree::{lead_own_group, ProcessTree},
    state::lock,
};
use sha2::{Digest, Sha256};
use tempfile::TempDir;

const COPY_LIMIT: u64 = 2 * 1024 * 1024 * 1024;
const CACHE_LIMIT: u64 = 6 * 1024 * 1024 * 1024;
/// The converter prints one small JSON progress object per line; a line this long is a
/// fault, not progress.
const MAX_CONVERTER_LINE_BYTES: usize = 1024 * 1024;

#[derive(Default)]
pub struct VideoPreviewControl {
    active: Mutex<Option<(String, Arc<AtomicBool>)>>,
    gate: Mutex<()>,
    cache: Mutex<Option<TempDir>>,
    closed: AtomicBool,
}

impl VideoPreviewControl {
    pub fn begin(&self, request: &str) -> AppResult<Arc<AtomicBool>> {
        if request.is_empty() || request.len() > 128 || self.closed.load(Ordering::SeqCst) {
            return Err(AppError::Validation("invalid playback request".into()));
        }
        let mut active = lock(&self.active)?;
        if let Some((_, cancel)) = active.as_ref() {
            cancel.store(true, Ordering::SeqCst);
        }
        let cancel = Arc::new(AtomicBool::new(false));
        *active = Some((request.into(), Arc::clone(&cancel)));
        Ok(cancel)
    }

    pub fn cancel(&self, request: &str) -> AppResult<()> {
        if let Some((id, cancel)) = lock(&self.active)?.as_ref() {
            if id == request {
                cancel.store(true, Ordering::SeqCst);
            }
        }
        Ok(())
    }

    pub fn shutdown(&self) {
        self.closed.store(true, Ordering::SeqCst);
        if let Ok(active) = self.active.lock() {
            if let Some((_, cancel)) = active.as_ref() {
                cancel.store(true, Ordering::SeqCst);
            }
        }
        // The owned process observes cancellation before its next 100 ms poll.
        if let Ok(_guard) = self.gate.lock() {
            if let Ok(mut cache) = self.cache.lock() {
                *cache = None;
            }
        }
    }

    pub fn convert(
        &self,
        source: &Path,
        root: &Path,
        mut command: Command,
        cancel: Arc<AtomicBool>,
        report: impl Fn(serde_json::Value) + Send + 'static,
    ) -> AppResult<PathBuf> {
        let _guard = lock(&self.gate)?;
        check_cancelled(&cancel)?;
        if self.closed.load(Ordering::SeqCst) {
            return Err(AppError::Validation(
                "Playback conversion cancelled.".into(),
            ));
        }
        let mut cache = lock(&self.cache)?;
        if cache.is_none() {
            fs::create_dir_all(root)?;
            *cache = Some(
                tempfile::Builder::new()
                    .prefix("playback-")
                    .tempdir_in(root)?,
            );
        }
        let directory = cache.as_ref().expect("playback cache initialized").path();
        let destination = directory.join(cache_key(source)?);
        if destination.is_file() {
            return Ok(destination);
        }
        let used = fs::read_dir(directory)?
            .filter_map(Result::ok)
            .filter_map(|entry| entry.metadata().ok())
            .filter(|m| m.is_file())
            .map(|m| m.len())
            .sum::<u64>();
        if used + COPY_LIMIT > CACHE_LIMIT {
            return Err(AppError::Validation("Playback cache is full. Restart LocalSR to clear temporary playback copies; saved exports remain available.".into()));
        }
        let work = tempfile::Builder::new()
            .prefix("converting-")
            .tempdir_in(directory)?;
        // Royalty-free VP9/Opus WebM plays in every supported web view.
        let output = work.path().join("preview.webm");
        command
            .arg("--video-playback-preview")
            .arg(source)
            .arg(&output);
        run_conversion(
            command,
            work.path(),
            cancel,
            report,
            Duration::from_secs(1800),
            COPY_LIMIT,
        )?;
        if !output.is_file() || output.metadata()?.len() == 0 {
            return Err(AppError::Worker(
                "Playback converter produced no video.".into(),
            ));
        }
        fs::rename(output, &destination)?;
        Ok(destination)
    }
}

pub fn needs_conversion(path: &Path) -> bool {
    !matches!(
        path.extension()
            .and_then(|v| v.to_str())
            .unwrap_or("")
            .to_ascii_lowercase()
            .as_str(),
        "mp4" | "mov" | "m4v"
    )
}

fn check_cancelled(cancel: &AtomicBool) -> AppResult<()> {
    if cancel.load(Ordering::SeqCst) {
        Err(AppError::Validation(
            "Playback conversion cancelled.".into(),
        ))
    } else {
        Ok(())
    }
}

fn cache_key(source: &Path) -> AppResult<String> {
    let metadata = source.metadata()?;
    let modified = metadata
        .modified()?
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let mut hash = Sha256::new();
    hash.update(format!(
        "playback-v2:{}:{}:{}",
        source.display(),
        metadata.len(),
        modified
    ));
    let hex: String = hash
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    Ok(format!("{hex}.webm"))
}

/// The size of every file below `work`, however deep the converter put it. Links are
/// not followed: a link to a large file or to a folder above is not data the
/// conversion wrote, and a link back up cannot make the count run for ever.
fn tree_bytes(work: &Path) -> u64 {
    let mut total = 0_u64;
    let mut folders = vec![work.to_path_buf()];
    while let Some(folder) = folders.pop() {
        let Ok(entries) = fs::read_dir(&folder) else {
            continue;
        };
        for entry in entries.filter_map(Result::ok) {
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            if kind.is_dir() {
                folders.push(entry.path());
            } else if kind.is_file() {
                total = total.saturating_add(entry.metadata().map_or(0, |m| m.len()));
            }
        }
    }
    total
}

fn run_conversion(
    mut command: Command,
    work: &Path,
    cancel: Arc<AtomicBool>,
    report: impl Fn(serde_json::Value) + Send + 'static,
    timeout: Duration,
    size_limit: u64,
) -> AppResult<()> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // The converter starts FFmpeg or the system media helper. They run in its group,
    // so stopping the conversion stops them too.
    lead_own_group(&mut command);
    let mut child = command.spawn()?;
    let tree = ProcessTree::new(&child);
    let stdout = child.stdout.take().expect("piped converter stdout");
    let stderr = child.stderr.take().expect("piped converter stderr");
    let overflowed = Arc::new(AtomicBool::new(false));
    let progress_overflowed = Arc::clone(&overflowed);
    let progress = thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = Vec::new();
        loop {
            match read_frame(&mut reader, MAX_CONVERTER_LINE_BYTES, &mut line) {
                Ok(Frame::Line) => {}
                Ok(Frame::End) | Err(_) => break,
                Ok(Frame::TooLong) => {
                    progress_overflowed.store(true, Ordering::SeqCst);
                    break;
                }
            }
            if let Ok(value) = serde_json::from_slice(&line) {
                report(value);
            }
        }
    });
    let errors = thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        let mut message = String::new();
        let _ = reader.by_ref().take(8192).read_to_string(&mut message);
        let _ = std::io::copy(&mut reader, &mut std::io::sink());
        message
    });
    let started = Instant::now();
    let result = loop {
        let bytes = tree_bytes(work);
        let problem = if cancel.load(Ordering::SeqCst) {
            Some("Playback conversion cancelled.")
        } else if started.elapsed() > timeout {
            Some("Playback conversion exceeded 30 minutes. Open the saved video in an external player.")
        } else if overflowed.load(Ordering::SeqCst) {
            Some("The playback converter sent a message that is too long.")
        } else if bytes > size_limit {
            Some("Playback copy exceeded its disk budget. Open the saved video in an external player.")
        } else {
            None
        };
        if let Some(message) = problem {
            tree.terminate();
            let _ = child.kill();
            let _ = child.wait();
            break Err(AppError::Validation(message.into()));
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                break if status.success() {
                    Ok(())
                } else {
                    Err(AppError::Worker("Playback conversion failed".into()))
                }
            }
            Ok(None) => thread::sleep(Duration::from_millis(100)),
            Err(error) => {
                tree.terminate();
                let _ = child.kill();
                let _ = child.wait();
                break Err(error.into());
            }
        }
    };
    // Whatever ended the converter, nothing it started may keep the pipes or the
    // work folder.
    tree.terminate();
    let _ = progress.join();
    let message = errors.join().unwrap_or_default();
    match result {
        Err(AppError::Worker(_)) => Err(AppError::Worker(format!(
            "Could not create a compatible playback copy: {}",
            message.trim()
        ))),
        other => other,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cancellation_is_owned_by_the_request() {
        let control = VideoPreviewControl::default();
        let old = control.begin("old").unwrap();
        let new = control.begin("new").unwrap();
        assert!(old.load(Ordering::SeqCst));
        control.cancel("old").unwrap();
        assert!(!new.load(Ordering::SeqCst));
        control.cancel("new").unwrap();
        assert!(new.load(Ordering::SeqCst));
    }

    #[test]
    fn legacy_containers_need_a_playback_copy() {
        for name in ["old.AVI", "tape.MPG", "capture.wmv", "result.mkv"] {
            assert!(needs_conversion(Path::new(name)));
        }
        assert!(!needs_conversion(Path::new("export.MP4")));
    }

    #[test]
    fn playback_copies_are_webm() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("clip.mkv");
        fs::write(&source, b"video").unwrap();
        assert!(cache_key(&source).unwrap().ends_with(".webm"));
    }

    #[test]
    fn changed_source_has_a_new_cache_key() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source.avi");
        fs::write(&path, "old").unwrap();
        let first = cache_key(&path).unwrap();
        fs::write(&path, "new content").unwrap();
        assert_ne!(first, cache_key(&path).unwrap());
    }

    #[cfg(unix)]
    #[test]
    fn cancellation_stops_the_owned_process_and_cleans_its_work() {
        let directory = tempfile::tempdir().unwrap();
        let cancel = Arc::new(AtomicBool::new(false));
        let signal = Arc::clone(&cancel);
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "echo '{\"frame\":1}'; exec sleep 30"]);
        let started = Instant::now();
        let result = run_conversion(
            command,
            directory.path(),
            cancel,
            move |_| {
                signal.store(true, Ordering::SeqCst);
            },
            Duration::from_secs(5),
            1024,
        );
        assert!(result.unwrap_err().to_string().contains("cancelled"));
        assert!(started.elapsed() < Duration::from_secs(3));
    }

    #[cfg(unix)]
    #[test]
    fn a_line_that_never_ends_stops_the_converter() {
        let directory = tempfile::tempdir().unwrap();
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "exec tr '\\0' x < /dev/zero"]);
        let started = Instant::now();
        let result = run_conversion(
            command,
            directory.path(),
            Arc::new(AtomicBool::new(false)),
            |_| {},
            Duration::from_secs(20),
            1024,
        );
        assert!(result.unwrap_err().to_string().contains("too long"));
        assert!(started.elapsed() < Duration::from_secs(10));
    }

    #[cfg(unix)]
    fn wait_until_stopped(pid: u32) -> bool {
        let deadline = Instant::now() + Duration::from_secs(5);
        while crate::process_tree::process_is_running(pid) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        !crate::process_tree::process_is_running(pid)
    }

    #[cfg(unix)]
    #[test]
    fn cancelling_a_conversion_ends_the_helpers_it_started() {
        let work = tempfile::tempdir().unwrap();
        let pids = tempfile::tempdir().unwrap();
        let pid_file = pids.path().join("helper.pid");
        let cancel = Arc::new(AtomicBool::new(false));
        let signal = Arc::clone(&cancel);
        let started = Instant::now();
        let mut command = Command::new("/bin/sh");
        // Like FFmpeg under the Python converter: a helper that outlives a kill of
        // its parent unless the whole tree is ended.
        command
            .arg("-c")
            .arg("sleep 60 & echo $! > \"$1\"; echo '{\"frame\":1}'; wait")
            .arg("sh")
            .arg(&pid_file);
        let result = run_conversion(
            command,
            work.path(),
            cancel,
            move |_| signal.store(true, Ordering::SeqCst),
            Duration::from_secs(20),
            1 << 30,
        );
        assert!(result.unwrap_err().to_string().contains("cancelled"));
        // The helper holds the converter's output pipe; ending it frees the reader.
        assert!(started.elapsed() < Duration::from_secs(10));
        let helper: u32 = fs::read_to_string(&pid_file)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        assert!(wait_until_stopped(helper), "the helper still runs");
    }

    #[cfg(unix)]
    #[test]
    fn every_way_a_conversion_ends_ends_its_helpers() {
        let pids = tempfile::tempdir().unwrap();
        for (index, how) in ["exit 0", "exit 1", "sleep 30"].into_iter().enumerate() {
            let work = tempfile::tempdir().unwrap();
            let pid_file = pids.path().join(format!("helper-{index}.pid"));
            let mut command = Command::new("/bin/sh");
            command
                .arg("-c")
                .arg(format!(
                    "sleep 60 > /dev/null 2>&1 & echo $! > \"$1\"; {how}"
                ))
                .arg("sh")
                .arg(&pid_file);
            let _ = run_conversion(
                command,
                work.path(),
                Arc::new(AtomicBool::new(false)),
                |_| {},
                Duration::from_millis(1500),
                1 << 30,
            );
            let helper: u32 = fs::read_to_string(&pid_file)
                .unwrap()
                .trim()
                .parse()
                .unwrap();
            assert!(wait_until_stopped(helper), "{how}: the helper still runs");
        }
    }

    #[cfg(unix)]
    #[test]
    fn intermediates_in_subfolders_count_against_the_disk_budget() {
        let directory = tempfile::tempdir().unwrap();
        let mut command = Command::new("/bin/sh");
        command.current_dir(directory.path()).args([
            "-c",
            "mkdir -p deep/er; printf 'sixteen bytes...' > deep/er/partial.tmp; exec sleep 30",
        ]);
        let started = Instant::now();
        let result = run_conversion(
            command,
            directory.path(),
            Arc::new(AtomicBool::new(false)),
            |_| {},
            Duration::from_secs(5),
            8,
        );
        assert!(result.unwrap_err().to_string().contains("disk budget"));
        assert!(started.elapsed() < Duration::from_secs(3));
    }

    #[cfg(unix)]
    #[test]
    fn the_disk_budget_counts_files_and_never_follows_links() {
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("big.bin"), vec![0_u8; 4096]).unwrap();
        fs::create_dir(outside.path().join("folder")).unwrap();
        fs::write(
            outside.path().join("folder").join("big.bin"),
            vec![0_u8; 4096],
        )
        .unwrap();
        let work = tempfile::tempdir().unwrap();
        std::os::unix::fs::symlink(outside.path().join("big.bin"), work.path().join("file"))
            .unwrap();
        std::os::unix::fs::symlink(outside.path().join("folder"), work.path().join("folder"))
            .unwrap();
        // A loop must not make the count run for ever.
        std::os::unix::fs::symlink(work.path(), work.path().join("loop")).unwrap();
        fs::create_dir_all(work.path().join("a").join("b")).unwrap();
        fs::write(work.path().join("a").join("b").join("one"), vec![0_u8; 100]).unwrap();
        fs::write(work.path().join("two"), vec![0_u8; 50]).unwrap();
        assert_eq!(tree_bytes(work.path()), 150);
    }

    #[cfg(unix)]
    #[test]
    fn disk_and_time_limits_stop_the_converter() {
        for disk_limit in [true, false] {
            let directory = tempfile::tempdir().unwrap();
            let mut command = Command::new("/bin/sh");
            command.current_dir(directory.path()).args([
                "-c",
                "printf 'sixteen bytes...' > partial.tmp; exec sleep 30",
            ]);
            let started = Instant::now();
            let result = run_conversion(
                command,
                directory.path(),
                Arc::new(AtomicBool::new(false)),
                |_| {},
                if disk_limit {
                    Duration::from_secs(5)
                } else {
                    Duration::from_millis(50)
                },
                if disk_limit { 8 } else { 1024 },
            );
            let error = result.unwrap_err().to_string();
            assert!(error.contains(if disk_limit {
                "disk budget"
            } else {
                "exceeded"
            }));
            assert!(started.elapsed() < Duration::from_secs(3));
        }
    }

    #[cfg(unix)]
    #[test]
    fn failed_conversion_removes_partial_files_and_shutdown_clears_cache() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("source.avi");
        fs::write(&source, "source unchanged").unwrap();
        let work = directory.path().join("work");
        let control = VideoPreviewControl::default();
        let mut command = Command::new("/bin/sh");
        command.args([
            "-c",
            "printf partial > \"$2\"; echo conversion-failed >&2; exit 1",
        ]);
        let result = control.convert(
            &source,
            &work,
            command,
            control.begin("test").unwrap(),
            |_| {},
        );
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("conversion-failed"));
        let cache = fs::read_dir(&work).unwrap().next().unwrap().unwrap().path();
        assert_eq!(fs::read_dir(&cache).unwrap().count(), 0);
        assert_eq!(fs::read_to_string(&source).unwrap(), "source unchanged");
        control.shutdown();
        assert!(!cache.exists());
    }
}
