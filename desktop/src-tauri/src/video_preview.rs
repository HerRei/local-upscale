use std::{
    fs,
    io::{BufReader, Read},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU32, Ordering},
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
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tempfile::TempDir;

const COPY_LIMIT: u64 = 2 * 1024 * 1024 * 1024;
const CACHE_LIMIT: u64 = 6 * 1024 * 1024 * 1024;
const COMPARE_CACHE_DIR: &str = "compare-playback";
/// The converter prints one small JSON progress object per line; a line this long is a
/// fault, not progress.
const MAX_CONVERTER_LINE_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ComparePlaybackInput {
    pub path: PathBuf,
    pub kept_frames: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
struct CompareManifestSlot {
    x: u32,
    y: u32,
    width: u32,
    height: u32,
    fps: f64,
    duration: f64,
    frames: u64,
    #[serde(default)]
    has_audio: bool,
    #[serde(default)]
    audio_file: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
struct ComparePlaybackManifest {
    width: u32,
    height: u32,
    duration: f64,
    slots: Vec<CompareManifestSlot>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PreparedCompareSlot {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
    pub fps: f64,
    pub duration: f64,
    pub frames: u64,
    pub audio_path: Option<PathBuf>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PreparedComparePlayback {
    pub video_path: PathBuf,
    pub width: u32,
    pub height: u32,
    pub duration: f64,
    pub slots: Vec<PreparedCompareSlot>,
}

#[derive(Default)]
pub struct VideoPreviewControl {
    active: Mutex<Option<(String, Arc<AtomicBool>)>>,
    gate: Mutex<()>,
    cache: Mutex<Option<TempDir>>,
    closed: AtomicBool,
    active_pid: AtomicU32,
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

    pub fn apply_low_priority(&self, low_priority: bool) {
        let pid = self.active_pid.load(Ordering::SeqCst);
        if pid != 0 {
            crate::process_priority::apply_to_pid(pid, low_priority);
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
        run_conversion_with_pid(
            command,
            work.path(),
            cancel,
            report,
            Duration::from_secs(1800),
            COPY_LIMIT,
            Some(&self.active_pid),
        )?;
        if !output.is_file() || output.metadata()?.len() == 0 {
            return Err(AppError::Worker(
                "Playback converter produced no video.".into(),
            ));
        }
        fs::rename(output, &destination)?;
        Ok(destination)
    }

    pub fn is_compare_cached(
        &self,
        inputs: &[ComparePlaybackInput],
        root: &Path,
    ) -> AppResult<bool> {
        let (key, _, _) = canonicalize_compare_inputs(inputs)?;
        let entry_dir = root.join(COMPARE_CACHE_DIR).join(key);
        Ok(entry_dir.join("combined.webm").is_file() && entry_dir.join("manifest.json").is_file())
    }

    pub fn convert_compare(
        &self,
        inputs: &[ComparePlaybackInput],
        root: &Path,
        mut command: Command,
        cancel: Arc<AtomicBool>,
        report: impl Fn(serde_json::Value) + Send + 'static,
    ) -> AppResult<PreparedComparePlayback> {
        let _guard = lock(&self.gate)?;
        check_cancelled(&cancel)?;
        if self.closed.load(Ordering::SeqCst) {
            return Err(AppError::Validation(
                "Playback conversion cancelled.".into(),
            ));
        }
        let (key, slot_to_unique, canonical) = canonicalize_compare_inputs(inputs)?;
        let compare_root = root.join(COMPARE_CACHE_DIR);
        fs::create_dir_all(&compare_root)?;
        sweep_stale_converting_dirs(&compare_root);
        let entry_dir = compare_root.join(&key);
        if let Some(prepared) = try_load_compare_entry(&entry_dir, &slot_to_unique)? {
            return Ok(prepared);
        }
        evict_compare_cache_if_needed(&compare_root)?;

        let work = tempfile::Builder::new()
            .prefix("converting-")
            .tempdir_in(&compare_root)?;
        let video_out = work.path().join("combined.webm");
        let manifest_out = work.path().join("manifest.json");
        let request_path = work.path().join("request.json");
        let sources_json: Vec<serde_json::Value> = canonical
            .iter()
            .enumerate()
            .map(|(index, entry)| {
                serde_json::json!({
                    "path": entry.input.path,
                    "kept_frames": entry.input.kept_frames,
                    "audio_path": work.path().join(format!("audio-{index}.webm")),
                })
            })
            .collect();
        let request_body = serde_json::json!({
            "sources": sources_json,
            "output_video": video_out,
            "output_manifest": manifest_out,
        });
        fs::write(&request_path, serde_json::to_vec(&request_body)?)?;

        command.arg("--video-compare-preview").arg(&request_path);
        run_conversion_with_pid(
            command,
            work.path(),
            cancel,
            report,
            Duration::from_secs(1800),
            COPY_LIMIT,
            Some(&self.active_pid),
        )?;

        if !video_out.is_file() || video_out.metadata()?.len() == 0 || !manifest_out.is_file() {
            return Err(AppError::Worker(
                "Playback converter produced no video.".into(),
            ));
        }
        let _ = fs::remove_file(&request_path);
        let work_path = work.keep();
        if let Err(rename_err) = fs::rename(&work_path, &entry_dir) {
            let _ = fs::remove_dir_all(&work_path);
            if let Some(prepared) = try_load_compare_entry(&entry_dir, &slot_to_unique)? {
                return Ok(prepared);
            }
            return Err(rename_err.into());
        }
        try_load_compare_entry(&entry_dir, &slot_to_unique)?.ok_or_else(|| {
            AppError::Worker("Compare playback manifest is missing or invalid.".into())
        })
    }
}

#[derive(Clone, Debug)]
struct CanonicalCompareEntry {
    input: ComparePlaybackInput,
    len: u64,
    modified_nanos: u128,
}

fn canonicalize_compare_inputs(
    inputs: &[ComparePlaybackInput],
) -> AppResult<(String, Vec<usize>, Vec<CanonicalCompareEntry>)> {
    if inputs.is_empty() || inputs.len() > 4 {
        return Err(AppError::Validation(
            "Compare playback requires 1 to 4 videos.".into(),
        ));
    }
    let mut entries = Vec::with_capacity(inputs.len());
    for input in inputs {
        let metadata = input.path.metadata()?;
        let modified_nanos = metadata
            .modified()?
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        entries.push(CanonicalCompareEntry {
            input: input.clone(),
            len: metadata.len(),
            modified_nanos,
        });
    }
    let mut order: Vec<usize> = (0..entries.len()).collect();
    order.sort_by(|&a, &b| {
        let ea = &entries[a];
        let eb = &entries[b];
        (
            ea.input.path.as_os_str(),
            ea.input.kept_frames,
            ea.len,
            ea.modified_nanos,
        )
            .cmp(&(
                eb.input.path.as_os_str(),
                eb.input.kept_frames,
                eb.len,
                eb.modified_nanos,
            ))
    });
    let mut canonical: Vec<CanonicalCompareEntry> = Vec::with_capacity(entries.len());
    let mut slot_to_unique = vec![0_usize; entries.len()];
    for idx in order {
        let entry = &entries[idx];
        let matches_last = canonical.last().is_some_and(|prev| {
            prev.input.path == entry.input.path
                && prev.input.kept_frames == entry.input.kept_frames
                && prev.len == entry.len
                && prev.modified_nanos == entry.modified_nanos
        });
        if matches_last {
            slot_to_unique[idx] = canonical.len() - 1;
        } else {
            slot_to_unique[idx] = canonical.len();
            canonical.push(entry.clone());
        }
    }
    let mut hash = Sha256::new();
    hash.update(b"compare-playback-v1:");
    for entry in &canonical {
        hash.update(
            format!(
                "{}:{}:{}:{};",
                entry.input.path.display(),
                entry.len,
                entry.modified_nanos,
                entry.input.kept_frames.unwrap_or(0)
            )
            .as_bytes(),
        );
    }
    let hex: String = hash
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    Ok((hex, slot_to_unique, canonical))
}

fn try_load_compare_entry(
    entry_dir: &Path,
    slot_to_unique: &[usize],
) -> AppResult<Option<PreparedComparePlayback>> {
    let video_path = entry_dir.join("combined.webm");
    let manifest_path = entry_dir.join("manifest.json");
    if !video_path.is_file() || !manifest_path.is_file() {
        return Ok(None);
    }
    let raw = fs::read(&manifest_path)?;
    let manifest: ComparePlaybackManifest = serde_json::from_slice(&raw)
        .map_err(|error| AppError::Worker(format!("Invalid Compare manifest: {error}")))?;
    let mut slots = Vec::with_capacity(slot_to_unique.len());
    for &unique_idx in slot_to_unique {
        let slot = manifest.slots.get(unique_idx).ok_or_else(|| {
            AppError::Worker("Compare playback manifest is missing a slot.".into())
        })?;
        let audio_path = slot
            .audio_file
            .as_deref()
            .filter(|name| !name.is_empty() && !name.contains('/') && !name.contains('\\'))
            .map(|name| entry_dir.join(name))
            .filter(|path| path.is_file());
        slots.push(PreparedCompareSlot {
            x: slot.x,
            y: slot.y,
            width: slot.width,
            height: slot.height,
            fps: slot.fps,
            duration: slot.duration,
            frames: slot.frames,
            audio_path,
        });
    }
    Ok(Some(PreparedComparePlayback {
        video_path,
        width: manifest.width,
        height: manifest.height,
        duration: manifest.duration,
        slots,
    }))
}

fn sweep_stale_converting_dirs(compare_root: &Path) {
    let Ok(entries) = fs::read_dir(compare_root) else {
        return;
    };
    for entry in entries.filter_map(Result::ok) {
        let name = entry.file_name();
        if name.to_string_lossy().starts_with("converting-")
            && entry.file_type().is_ok_and(|kind| kind.is_dir())
        {
            let _ = fs::remove_dir_all(entry.path());
        }
    }
}

fn evict_compare_cache_if_needed(compare_root: &Path) -> AppResult<()> {
    let mut entries = Vec::new();
    let mut used = 0_u64;
    for entry in fs::read_dir(compare_root)?.filter_map(Result::ok) {
        if !entry.file_type().is_ok_and(|kind| kind.is_dir()) {
            continue;
        }
        let bytes = tree_bytes(&entry.path());
        used = used.saturating_add(bytes);
        let modified = entry
            .metadata()
            .and_then(|m| m.modified())
            .unwrap_or(UNIX_EPOCH);
        entries.push((modified, bytes, entry.path()));
    }
    if used + COPY_LIMIT <= CACHE_LIMIT {
        return Ok(());
    }
    entries.sort_by_key(|(modified, _, _)| *modified);
    for (_, bytes, path) in entries {
        if used + COPY_LIMIT <= CACHE_LIMIT {
            break;
        }
        if fs::remove_dir_all(&path).is_ok() {
            used = used.saturating_sub(bytes);
        }
    }
    if used + COPY_LIMIT > CACHE_LIMIT {
        return Err(AppError::Validation(
            "Playback cache is full. Saved exports remain available.".into(),
        ));
    }
    Ok(())
}

/// Shared descriptor for single preview and each authorised Compare slot.
pub fn sources(
    original: PathBuf,
    enhanced: Option<PathBuf>,
    original_url: Option<String>,
    enhanced_url: Option<String>,
    converted: bool,
) -> crate::types::VideoComparisonSources {
    crate::types::VideoComparisonSources {
        original_path: original.to_string_lossy().into_owned(),
        enhanced_path: enhanced
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_default(),
        original_url,
        enhanced_url,
        playback_note: if converted {
            "Compatible SDR playback copy · up to 1280 px. Source and saved export are unchanged."
                .into()
        } else {
            String::new()
        },
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

#[cfg(all(test, unix))]
fn run_conversion(
    command: Command,
    work: &Path,
    cancel: Arc<AtomicBool>,
    report: impl Fn(serde_json::Value) + Send + 'static,
    timeout: Duration,
    size_limit: u64,
) -> AppResult<()> {
    run_conversion_with_pid(command, work, cancel, report, timeout, size_limit, None)
}

fn run_conversion_with_pid(
    mut command: Command,
    work: &Path,
    cancel: Arc<AtomicBool>,
    report: impl Fn(serde_json::Value) + Send + 'static,
    timeout: Duration,
    size_limit: u64,
    active_pid: Option<&AtomicU32>,
) -> AppResult<()> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // The converter starts FFmpeg or the system media helper. They run in its group,
    // so stopping the conversion stops them too.
    lead_own_group(&mut command);
    let mut child = command.spawn()?;
    if let Some(slot) = active_pid {
        slot.store(child.id(), Ordering::SeqCst);
    }
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
            let _ = tree.wait(&mut child);
            break Err(AppError::Validation(message.into()));
        }
        match tree.try_wait(&mut child) {
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
                let _ = tree.wait(&mut child);
                break Err(error.into());
            }
        }
    };
    // Whatever ended the converter, nothing it started may keep the pipes or the
    // work folder.
    tree.terminate();
    if let Some(slot) = active_pid {
        slot.store(0, Ordering::SeqCst);
    }
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

    #[test]
    fn compare_cache_key_is_stable_across_swaps_and_sensitive_to_kept_frames_and_contents() {
        let dir = tempfile::tempdir().unwrap();
        let a = dir.path().join("a.mp4");
        let b = dir.path().join("b.mp4");
        fs::write(&a, b"alpha").unwrap();
        fs::write(&b, b"bravo").unwrap();
        let in_a = ComparePlaybackInput {
            path: a.clone(),
            kept_frames: None,
        };
        let in_b = ComparePlaybackInput {
            path: b.clone(),
            kept_frames: Some(30),
        };
        let (key_ab, map_ab, uniq_ab) =
            canonicalize_compare_inputs(&[in_a.clone(), in_b.clone()]).unwrap();
        let (key_ba, map_ba, uniq_ba) =
            canonicalize_compare_inputs(&[in_b.clone(), in_a.clone()]).unwrap();
        assert_eq!(key_ab, key_ba);
        assert_eq!(uniq_ab.len(), 2);
        assert_eq!(uniq_ba.len(), 2);
        assert_eq!(map_ab, vec![0, 1]);
        assert_eq!(map_ba, vec![1, 0]);

        let in_b_more = ComparePlaybackInput {
            path: b.clone(),
            kept_frames: Some(45),
        };
        let (key_diff_kept, _, _) =
            canonicalize_compare_inputs(&[in_a.clone(), in_b_more]).unwrap();
        assert_ne!(key_ab, key_diff_kept);

        fs::write(&b, b"bravo-changed").unwrap();
        let (key_diff_bytes, _, _) = canonicalize_compare_inputs(&[in_a, in_b]).unwrap();
        assert_ne!(key_ab, key_diff_bytes);
    }

    #[cfg(unix)]
    #[test]
    fn compare_playback_cache_persists_across_sessions_and_cleans_failed_work() {
        let dir = tempfile::tempdir().unwrap();
        let a = dir.path().join("a.mp4");
        let b = dir.path().join("b.mp4");
        fs::write(&a, b"one").unwrap();
        fs::write(&b, b"two").unwrap();
        let work = dir.path().join("work");
        let inputs = vec![
            ComparePlaybackInput {
                path: a.clone(),
                kept_frames: None,
            },
            ComparePlaybackInput {
                path: b.clone(),
                kept_frames: Some(24),
            },
        ];

        let mut fail_cmd = Command::new("/bin/sh");
        fail_cmd.args(["-c", "echo boom >&2; exit 1"]);
        let control = VideoPreviewControl::default();
        let err = control
            .convert_compare(
                &inputs,
                &work,
                fail_cmd,
                control.begin("fail").unwrap(),
                |_| {},
            )
            .unwrap_err();
        assert!(err.to_string().contains("boom"));
        assert!(!control.is_compare_cached(&inputs, &work).unwrap());

        let mut ok_cmd = Command::new("/bin/sh");
        ok_cmd.args([
            "-c",
            r#"
            req="$1"
            dir=$(dirname "$req")
            printf 'webm-bytes' > "$dir/combined.webm"
            printf 'audio-bytes' > "$dir/audio-0.webm"
            cat > "$dir/manifest.json" <<'EOF'
{"width":128,"height":148,"duration":1.0,"slots":[{"x":0,"y":0,"width":128,"height":72,"fps":30.0,"duration":3.0,"frames":90,"has_audio":true,"audio_file":"audio-0.webm"},{"x":0,"y":76,"width":128,"height":72,"fps":24.0,"duration":1.0,"frames":24,"has_audio":false,"audio_file":null}]}
EOF
            "#,
        ]);
        let prepared = control
            .convert_compare(&inputs, &work, ok_cmd, control.begin("ok").unwrap(), |_| {})
            .unwrap();
        assert_eq!(prepared.width, 128);
        assert_eq!(prepared.slots.len(), 2);
        assert!(prepared.video_path.is_file());
        assert!(prepared.slots[0].audio_path.as_ref().unwrap().is_file());
        assert!(prepared.slots[1].audio_path.is_none());

        // Shutdown must keep the content-stable compare cache intact for the next session.
        control.shutdown();
        assert!(prepared.video_path.is_file());

        let next_session = VideoPreviewControl::default();
        assert!(next_session.is_compare_cached(&inputs, &work).unwrap());
        // Swapped order hits the same cached combined video and remaps slot rects.
        let swapped = vec![inputs[1].clone(), inputs[0].clone()];
        let never_runs = Command::new("/bin/false");
        let cached = next_session
            .convert_compare(
                &swapped,
                &work,
                never_runs,
                next_session.begin("hit").unwrap(),
                |_| {},
            )
            .unwrap();
        assert_eq!(cached.video_path, prepared.video_path);
        assert_eq!(cached.slots[0].y, 76);
        assert_eq!(cached.slots[1].y, 0);
        assert!(cached.slots[0].audio_path.is_none());
        assert!(cached.slots[1].audio_path.is_some());
    }
}
