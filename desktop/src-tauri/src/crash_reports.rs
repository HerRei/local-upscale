//! Opt-in, local crash reports. Reports contain only explicitly selected fields.
use crate::{paths::AppPaths, state::AppState};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::Write,
    path::PathBuf,
    sync::{Mutex, OnceLock},
    time::{Duration, SystemTime},
};

const MAX_REPORTS: usize = 5;
const MAX_AGE: Duration = Duration::from_secs(30 * 24 * 60 * 60);
static HOOK_PROFILE: OnceLock<Mutex<Option<(AppPaths, bool)>>> = OnceLock::new();
static OS_VERSION: OnceLock<String> = OnceLock::new();
static STORE: Mutex<()> = Mutex::new(());
thread_local! {
    static IN_STORE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

struct StoreGuard<'a> {
    _guard: std::sync::MutexGuard<'a, ()>,
}

impl Drop for StoreGuard<'_> {
    fn drop(&mut self) {
        IN_STORE.with(|in_store| in_store.set(false));
    }
}

fn lock_store() -> StoreGuard<'static> {
    let guard = STORE.lock().unwrap_or_else(|e| e.into_inner());
    IN_STORE.with(|in_store| in_store.set(true));
    StoreGuard { _guard: guard }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CrashReport {
    pub app_version: String,
    pub build_target: String,
    pub os: String,
    pub os_version: String,
    pub architecture: String,
    pub source: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub location: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stack: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exit: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub device: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    pub limitation: String,
}

#[derive(Serialize)]
pub struct StoredReport {
    pub id: String,
    pub report: CrashReport,
}

fn os_version() -> String {
    OS_VERSION
        .get_or_init(|| {
            #[cfg(target_os = "windows")]
            {
                "unknown".into()
            }
            #[cfg(not(target_os = "windows"))]
            {
                std::process::Command::new("uname")
                    .arg("-r")
                    .output()
                    .ok()
                    .filter(|o| o.status.success())
                    .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_owned())
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| "unknown".into())
            }
        })
        .clone()
}

fn device_kind(device_id: &str, neural_engine: bool) -> &'static str {
    if device_id == "cpu" {
        "CPU"
    } else if device_id == "mps" {
        if neural_engine {
            "Neural Engine"
        } else {
            "GPU (Metal)"
        }
    } else if device_id.starts_with("cuda") {
        "GPU (CUDA)"
    } else if device_id.starts_with("dml") || device_id.starts_with("directml") {
        "GPU (DirectML)"
    } else if device_id.starts_with("openvino") {
        "GPU (OpenVINO)"
    } else if cfg!(target_os = "macos") && neural_engine {
        "Neural Engine"
    } else if cfg!(target_os = "macos") {
        "GPU (Metal)"
    } else {
        "GPU"
    }
}

impl CrashReport {
    pub fn new(source: &str, message: &str) -> Self {
        Self {
            app_version: env!("CARGO_PKG_VERSION").into(),
            build_target: format!("{}-{}", std::env::consts::ARCH, std::env::consts::OS),
            os: std::env::consts::OS.into(),
            os_version: os_version(),
            architecture: std::env::consts::ARCH.into(),
            source: source.into(),
            message: message.into(),
            location: None,
            stack: None,
            exit: None,
            last_error: None,
            device: None,
            task: None,
            model: None,
            limitation: "A native host crash such as a segmentation fault may not be captured."
                .into(),
        }
    }

    pub fn with_job(mut self, state: &AppState) -> Self {
        let active = state
            .runtime
            .try_lock()
            .ok()
            .map(|r| !r.active_job_id.is_empty() || !r.active_batch_job_ids.is_empty())
            .unwrap_or(false);
        if active {
            if let Ok(settings) = state.settings.try_lock() {
                if !settings.task.is_empty() {
                    self.task = Some(settings.task.clone());
                }
                let model = match settings.task.as_str() {
                    "video" => settings.selected_video_model_id.as_str(),
                    "edit" => settings.edit_model_id.as_str(),
                    _ if settings.selected_model_id.is_empty()
                        && !settings.custom_model_path.is_empty() =>
                    {
                        "custom"
                    }
                    _ => settings.selected_model_id.as_str(),
                };
                if !model.is_empty() {
                    self.model = Some(model.to_owned());
                }
                self.device = Some(device_kind(&settings.device_id, settings.neural_engine).into());
            }
        }
        self
    }

    fn redact(&mut self) {
        self.message = redact(&self.message);
        for s in [
            &mut self.location,
            &mut self.stack,
            &mut self.exit,
            &mut self.last_error,
            &mut self.device,
            &mut self.task,
            &mut self.model,
        ]
        .into_iter()
        .flatten()
        {
            *s = redact(s);
        }
    }
}

fn is_media_ext(ext: &str) -> bool {
    matches!(
        ext.to_ascii_lowercase().as_str(),
        "jpg"
            | "jpeg"
            | "png"
            | "webp"
            | "tif"
            | "tiff"
            | "dng"
            | "heic"
            | "raw"
            | "mp4"
            | "mov"
            | "mkv"
            | "avi"
            | "webm"
            | "gif"
            | "bmp"
            | "arw"
            | "nef"
            | "cr2"
            | "cr3"
    )
}

fn has_media_ext(text: &str) -> bool {
    let trimmed = text.trim_matches(|c: char| !c.is_alphanumeric() && c != '.');
    trimmed
        .rsplit_once('.')
        .is_some_and(|(stem, ext)| !stem.is_empty() && is_media_ext(ext))
}

fn is_boundary_before(line: &str, byte_idx: usize) -> bool {
    byte_idx == 0
        || line[..byte_idx]
            .chars()
            .next_back()
            .is_some_and(|c| c.is_whitespace() || "(:=\"'[,<".contains(c))
}

fn starts_with_ci(haystack: &str, prefix: &str) -> bool {
    haystack
        .get(..prefix.len())
        .is_some_and(|s| s.eq_ignore_ascii_case(prefix))
}

fn path_placeholder_at(line: &str, i: usize) -> Option<&'static str> {
    let rest = &line[i..];
    if rest.starts_with("~/<path>") {
        return None;
    }
    if starts_with_ci(rest, "file://") {
        let after = rest[7..].trim_start_matches('/');
        let home = starts_with_ci(after, "users/")
            || starts_with_ci(after, "home/")
            || (after.len() >= 9
                && after.as_bytes()[0].is_ascii_alphabetic()
                && (starts_with_ci(&after[1..], ":/users/")
                    || starts_with_ci(&after[1..], ":\\users\\")));
        return Some(if home { "~/<path>" } else { "<path>" });
    }
    if !is_boundary_before(line, i) {
        return None;
    }
    if rest.starts_with("/Users/") || rest.starts_with("/home/") {
        return Some("~/<path>");
    }
    if rest.starts_with("~/") || rest.starts_with("~\\") {
        return Some("~/<path>");
    }
    let bytes = rest.as_bytes();
    if bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && (bytes[2] == b'\\' || bytes[2] == b'/')
    {
        return Some(
            if starts_with_ci(&rest[1..], ":\\users\\") || starts_with_ci(&rest[1..], ":/users/") {
                "~/<path>"
            } else {
                "<path>"
            },
        );
    }
    if rest.starts_with("\\\\") && bytes.get(2).is_some_and(|b| !b.is_ascii_whitespace()) {
        return Some("<path>");
    }
    if rest.starts_with('/')
        && bytes
            .get(1)
            .is_some_and(|b| !b.is_ascii_whitespace() && *b != b'/' && *b != b'<')
    {
        return Some("<path>");
    }
    None
}

fn is_email(word: &str) -> bool {
    let trimmed = word.trim_matches(|c: char| "()<>\"',;:[]".contains(c));
    let Some((local, domain)) = trimmed.split_once('@') else {
        return false;
    };
    !local.is_empty()
        && !local.contains('/')
        && !domain.is_empty()
        && !domain.contains('/')
        && domain.contains('.')
        && !domain.starts_with('.')
        && !domain.ends_with('.')
}

fn is_secret_token(token: &str) -> bool {
    if token.len() < 24 {
        return false;
    }
    if token.chars().all(|c| c.is_ascii_hexdigit()) {
        return true;
    }
    if token.starts_with("eyJ") && token.contains('.') {
        return token.split('.').all(|seg| {
            !seg.is_empty()
                && seg
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || "_-".contains(c))
        });
    }
    let allowed = token
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || "+/=_-".contains(c));
    if !allowed {
        return false;
    }
    if token.contains('/') && !token.contains('+') && !token.contains('=') {
        return false;
    }
    if token.contains('_') && !token.ends_with('=') {
        let prefix_token = matches!(
            token.split_once('_'),
            Some((prefix, rest))
                if matches!(prefix, "ghp" | "gho" | "ghu" | "ghs" | "sk" | "pk" | "rk")
                    && rest.len() >= 16
                    && !rest.contains('_')
        );
        if !prefix_token {
            return false;
        }
    }
    let has_digit = token.chars().any(|c| c.is_ascii_digit());
    let has_upper = token.chars().any(|c| c.is_ascii_uppercase());
    let has_lower = token.chars().any(|c| c.is_ascii_lowercase());
    (has_digit && (has_upper || has_lower)) || token.contains('+') || token.contains('=')
}

fn redact_word(word: &str) -> String {
    let lead_len = word
        .find(|c: char| !"(\"'[<".contains(c))
        .unwrap_or(word.len());
    let lead = &word[..lead_len];
    let rest = &word[lead_len..];
    let trail_start = rest
        .rfind(|c: char| !")]>\"',;:".contains(c))
        .map(|i| i + rest[i..].chars().next().map_or(0, char::len_utf8))
        .unwrap_or(0);
    let core = &rest[..trail_start];
    let trail = &rest[trail_start..];
    if core.is_empty() {
        return word.to_owned();
    }
    if starts_with_ci(core, "http://") || starts_with_ci(core, "https://") {
        return format!("{lead}<url>{trail}");
    }
    if is_email(core) {
        return format!("{lead}<email>{trail}");
    }
    if is_secret_token(core) {
        return format!("{lead}<token>{trail}");
    }
    if let Some((file_part, line_suffix)) = core.split_once(':') {
        if has_media_ext(file_part) && line_suffix.chars().all(|c| c.is_ascii_digit() || c == ':') {
            return format!("{lead}<file>:{line_suffix}{trail}");
        }
    }
    if has_media_ext(core) {
        return format!("{lead}<file>{trail}");
    }
    word.to_owned()
}

fn redact_line(raw: &str) -> String {
    let line: String = raw.chars().take(500).collect();
    let mut out = String::new();
    let mut i = 0;
    while i < line.len() {
        let rest = &line[i..];
        if rest.starts_with("~/<path>") {
            out.push_str("~/<path>");
            i += "~/<path>".len();
            continue;
        }
        if let Some(placeholder) = path_placeholder_at(&line, i) {
            out.push_str(placeholder);
            let opening_quote = line[..i]
                .chars()
                .next_back()
                .filter(|c| *c == '"' || *c == '\'');
            if let Some(quote) = opening_quote {
                if let Some(end_rel) = rest.find(quote) {
                    i += end_rel;
                    continue;
                }
            }
            break;
        }
        let ch = rest.chars().next().unwrap();
        if (ch == '"' || ch == '\'') && rest[1..].find(ch).is_some() {
            let end_rel = 1 + rest[1..].find(ch).unwrap();
            let inner = &rest[1..end_rel];
            if has_media_ext(inner)
                && path_placeholder_at(inner, 0).is_none()
                && !starts_with_ci(inner, "http://")
                && !starts_with_ci(inner, "https://")
            {
                out.push(ch);
                out.push_str("<file>");
                out.push(ch);
                i += end_rel + 1;
                continue;
            }
        }
        if ch.is_whitespace() {
            out.push(ch);
            i += ch.len_utf8();
            continue;
        }
        let word_end = rest
            .char_indices()
            .find(|(_, c)| c.is_whitespace())
            .map(|(idx, _)| idx)
            .unwrap_or(rest.len());
        let word = &rest[..word_end];
        if let Some(inner_path_rel) = word
            .char_indices()
            .skip(1)
            .find_map(|(idx, _)| path_placeholder_at(word, idx).map(|p| (idx, p)))
        {
            out.push_str(&word[..inner_path_rel.0]);
            i += inner_path_rel.0;
            continue;
        }
        out.push_str(&redact_word(word));
        i += word_end;
    }
    out
}

/// Redact paths, media file names, contacts, URLs and tokens before writing or showing a report.
pub fn redact(input: &str) -> String {
    input
        .lines()
        .take(40)
        .map(redact_line)
        .collect::<Vec<_>>()
        .join("\n")
}

fn folder(paths: &AppPaths) -> PathBuf {
    paths.next_root.join("crash-reports")
}
fn files(paths: &AppPaths) -> Vec<PathBuf> {
    let mut files: Vec<_> = fs::read_dir(folder(paths))
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e == "json"))
        .collect();
    files.sort_by_key(|p| {
        p.metadata()
            .and_then(|m| m.modified())
            .unwrap_or(SystemTime::UNIX_EPOCH)
    });
    files
}
pub fn cleanup(paths: &AppPaths) {
    cleanup_at(paths, SystemTime::now());
}
fn cleanup_at(paths: &AppPaths, now: SystemTime) {
    let _guard = lock_store();
    let mut kept = Vec::new();
    for path in files(paths) {
        let expired = path
            .metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| now.duration_since(t).ok())
            .is_some_and(|age| age > MAX_AGE);
        if expired {
            let _ = fs::remove_file(path);
        } else {
            kept.push(path);
        }
    }
    let excess = kept.len().saturating_sub(MAX_REPORTS);
    for path in kept.into_iter().take(excess) {
        let _ = fs::remove_file(path);
    }
    if let Ok(entries) = fs::read_dir(folder(paths)) {
        for entry in entries.flatten() {
            if entry.path().extension().is_some_and(|x| x == "tmp") {
                let _ = fs::remove_file(entry.path());
            }
        }
    }
}
pub fn delete_all(paths: &AppPaths) -> std::io::Result<()> {
    let _guard = lock_store();
    match fs::remove_dir_all(folder(paths)) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        result => result,
    }
}
pub fn delete(paths: &AppPaths, id: &str) -> std::io::Result<()> {
    if uuid::Uuid::parse_str(id).is_err() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "invalid report",
        ));
    }
    let _guard = lock_store();
    match fs::remove_file(folder(paths).join(format!("{id}.json"))) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        r => r,
    }
}
pub fn list(paths: &AppPaths) -> Vec<StoredReport> {
    let _guard = lock_store();
    files(paths)
        .into_iter()
        .filter_map(|p| {
            Some(StoredReport {
                id: p.file_stem()?.to_str()?.to_owned(),
                report: serde_json::from_slice(&fs::read(&p).ok()?).ok()?,
            })
        })
        .collect()
}
pub fn write(paths: &AppPaths, enabled: bool, report: CrashReport) -> std::io::Result<()> {
    write_with(paths, enabled, report, false, |file, report| {
        serde_json::to_writer_pretty(file, report).map_err(std::io::Error::other)
    })
}
fn write_with(
    paths: &AppPaths,
    enabled: bool,
    mut report: CrashReport,
    panic_hook: bool,
    write_body: impl FnOnce(&mut tempfile::NamedTempFile, &CrashReport) -> std::io::Result<()>,
) -> std::io::Result<()> {
    if !enabled {
        return Ok(());
    }
    report.redact();
    if panic_hook && IN_STORE.with(|in_store| in_store.get()) {
        return Ok(());
    }
    let guard = lock_store();
    fs::create_dir_all(folder(paths))?;
    let id = uuid::Uuid::new_v4();
    let dest = folder(paths).join(format!("{id}.json"));
    let mut temp = tempfile::Builder::new()
        .prefix("crash-")
        .suffix(".tmp")
        .tempfile_in(folder(paths))?;
    write_body(&mut temp, &report)?;
    temp.flush()?;
    temp.as_file().sync_all()?;
    temp.persist(&dest).map_err(|e| e.error)?;
    let all = files(paths);
    let excess = all.len().saturating_sub(MAX_REPORTS);
    for path in all.into_iter().take(excess) {
        let _ = fs::remove_file(path);
    }
    drop(guard);
    Ok(())
}
pub fn record(state: &AppState, report: CrashReport) {
    // Serialise opt-out and writes: after the off switch returns, no stale writer can recreate reports.
    if let Ok(_io) = state.settings_io.lock() {
        let enabled = state
            .settings
            .lock()
            .map(|s| s.crash_reports)
            .unwrap_or(false);
        let _ = write(&state.paths, enabled, report);
    }
}
pub fn configure_hook(paths: &AppPaths, enabled: bool) {
    let _ = os_version();
    if let Ok(mut profile) = HOOK_PROFILE.get_or_init(|| Mutex::new(None)).lock() {
        *profile = Some((paths.clone(), enabled));
    }
}
fn record_panic_info(paths: &AppPaths, enabled: bool, info: &std::panic::PanicHookInfo<'_>) {
    let message = info
        .payload()
        .downcast_ref::<&str>()
        .copied()
        .or_else(|| info.payload().downcast_ref::<String>().map(String::as_str))
        .unwrap_or("Rust panic");
    let mut report = CrashReport::new("host panic", message);
    report.location = info.location().and_then(|loc| {
        let normalized = loc.file().replace('\\', "/");
        let relative = if let Some(i) = normalized.find("src-tauri/src/") {
            Some(&normalized[i..])
        } else if normalized.starts_with("src/") {
            Some(normalized.as_str())
        } else {
            None
        };
        relative.map(|file| format!("{file}:{}", loc.line()))
    });
    let _ = write_with(paths, enabled, report, true, |file, report| {
        serde_json::to_writer_pretty(file, report).map_err(std::io::Error::other)
    });
}
pub fn install_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if let Some(profile) = HOOK_PROFILE.get().and_then(|p| p.try_lock().ok()) {
            if let Some((paths, enabled)) = &*profile {
                record_panic_info(paths, *enabled, info);
            }
        }
        previous(info);
    }));
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn redacts_paths_contacts_and_tokens() {
        // A made-up token, assembled here so secret scanners do not flag the fixture.
        let jwt = format!(
            "jwt {}.{}.{}",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
            "dozjgNryP4J3jVmNHl0w5N"
        );
        for text in [
            "Error /Users/Élodie Smith/Pictures/family photo.jpg",
            r"at C:\Users\Alice Smith\Pictures\photo.png:12",
            r#"File "C:\Users\Alice Smith\app.py", line 12"#,
            "File C:/Users/Bob/video.mp4",
            "at /home/李 明/private data/model.bin",
            "Trace (/opt/Private Folder/user name/model.py:22)",
            r"UNC \\fileserver\share\family photo.png",
            "mail alice@example.com",
            "https://example.com/?secret=1234",
            "token abcdef1234567890abcdef1234567890",
            "bearer SGVsbG8gd29ybGQgdGhpcyBpcyBhIHRlc3Q=",
            jwt.as_str(),
            "Could not decode holiday.jpg",
        ] {
            let result = redact(text);
            for secret in [
                "Élodie",
                "Smith",
                "Alice",
                "Bob",
                "李",
                "Private",
                "fileserver",
                "holiday",
                "photo",
                "video",
                "alice@example",
                "secret=",
                "abcdef1234567890",
                "SGVsbG8",
                "eyJhbGci",
            ] {
                assert!(!result.contains(secret), "{result:?} from {text:?}");
            }
        }
        assert_eq!(
            redact("Error /Users/Élodie Smith/Pictures/family photo.jpg"),
            "Error ~/<path>"
        );
        assert_eq!(
            redact(r#"File "C:\Users\Alice Smith\app.py", line 12"#),
            r#"File "~/<path>", line 12"#
        );
        assert_eq!(redact("https://example.com/?secret=1234"), "<url>");
        assert_eq!(
            redact("src-tauri/src/commands.rs:42"),
            "src-tauri/src/commands.rs:42"
        );
        assert_eq!(
            redact("realesrgan_x4plus_anime_6b"),
            "realesrgan_x4plus_anime_6b"
        );
        assert_eq!(redact("Error ~/<path>"), "Error ~/<path>");
    }

    #[test]
    fn disabled_and_atomic_partial_are_not_reports() {
        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        write(&paths, false, CrashReport::new("frontend", "failure")).unwrap();
        assert!(!folder(&paths).exists());
        let err = write_with(
            &paths,
            true,
            CrashReport::new("frontend", "failure"),
            false,
            |file, _| {
                file.write_all(b"{\"incomplete\":")?;
                Err(std::io::Error::other("simulated write failure"))
            },
        );
        assert!(err.is_err());
        assert!(list(&paths).is_empty());
        let entries: Vec<_> = fs::read_dir(folder(&paths))
            .unwrap()
            .filter_map(Result::ok)
            .collect();
        assert!(entries.is_empty(), "leftover temp files: {entries:?}");

        fs::write(folder(&paths).join("crash-partial.tmp"), b"{ incomplete").unwrap();
        assert!(list(&paths).is_empty());
        cleanup(&paths);
        assert!(!folder(&paths).join("crash-partial.tmp").exists());
    }

    #[test]
    fn caps_reports_and_expires_after_thirty_days() {
        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        for _ in 0..7 {
            write(&paths, true, CrashReport::new("engine", "failure")).unwrap();
        }
        assert_eq!(list(&paths).len(), 5);
        cleanup_at(
            &paths,
            SystemTime::now() + Duration::from_secs(29 * 24 * 60 * 60),
        );
        assert_eq!(list(&paths).len(), 5);
        cleanup_at(&paths, SystemTime::now() + MAX_AGE + Duration::from_secs(1));
        assert!(list(&paths).is_empty());
    }

    #[test]
    fn disabling_deletes_existing_reports() {
        let root = tempfile::tempdir().unwrap();
        let state = AppState::from_paths(AppPaths::under(root.path())).unwrap();
        super::super::commands::test_crash_switch(&state, true).unwrap();
        record(
            &state,
            CrashReport::new("frontend", "failure /home/alice/private.jpg"),
        );
        assert_eq!(list(&state.paths).len(), 1);
        super::super::commands::test_crash_switch(&state, false).unwrap();
        assert!(list(&state.paths).is_empty());
    }

    #[test]
    fn panic_hook_writes_redacted_report_only_when_enabled() {
        struct ResetHook;
        impl Drop for ResetHook {
            fn drop(&mut self) {
                if let Some(cell) = HOOK_PROFILE.get() {
                    if let Ok(mut guard) = cell.lock() {
                        *guard = None;
                    }
                }
            }
        }
        static HOOK_TEST_LOCK: Mutex<()> = Mutex::new(());
        let _test_guard = HOOK_TEST_LOCK.lock().unwrap();
        let _reset = ResetHook;

        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        install_hook();

        configure_hook(&paths, false);
        let res = std::panic::catch_unwind(|| {
            panic!("boom /Users/alice/Pictures/secret.jpg");
        });
        assert!(res.is_err());
        assert!(list(&paths).is_empty());

        configure_hook(&paths, true);
        let res = std::panic::catch_unwind(|| {
            panic!("boom /Users/alice/Pictures/secret.jpg");
        });
        assert!(res.is_err());
        let reports = list(&paths);
        assert_eq!(reports.len(), 1);
        let report = &reports[0].report;
        assert_eq!(report.source, "host panic");
        assert!(!report.message.contains("alice"));
        assert!(!report.message.contains("secret.jpg"));
        assert!(report.message.contains("~/<path>"));
        assert!(report.location.as_deref().is_some_and(|loc| {
            loc.starts_with("src-tauri/src/crash_reports.rs:")
                || loc.starts_with("src/crash_reports.rs:")
        }));
    }
}
