//! Programs the host starts on the web view's word. The web view may name an
//! executable (the user's FFmpeg), but the host runs one only when it obtained the
//! path itself: from its own file dialog, from its own search, or from the settings
//! file of an earlier run. A page that cannot show a native dialog therefore cannot
//! make the host run a program it picked.
#[cfg(any(windows, test))]
use std::path::Path;
use std::{collections::HashSet, fs, path::PathBuf, sync::Mutex};

use serde_json::Value;

use crate::error::{AppError, AppResult};

#[derive(Default)]
pub struct GrantedPrograms(Mutex<HashSet<String>>);

impl GrantedPrograms {
    pub fn grant(&self, path: &str) {
        if path.is_empty() {
            return;
        }
        if let Ok(mut granted) = self.0.lock() {
            granted.insert(key(path));
        }
    }

    /// The engine's `media.external_ffmpeg` paths are suggestions: one is granted only
    /// when the host's own search finds that same program.
    pub fn grant_reported(&self, media: &Value) {
        self.grant_reported_among(media, &find_ffmpeg());
    }

    fn grant_reported_among(&self, media: &Value, host_found: &[String]) {
        let found: HashSet<String> = host_found.iter().map(|path| key(path)).collect();
        for list in ["detected", "candidates"] {
            for path in media["external_ffmpeg"][list]
                .as_array()
                .into_iter()
                .flatten()
            {
                if let Some(path) = path.as_str().filter(|path| found.contains(&key(path))) {
                    self.grant(path);
                }
            }
        }
    }

    /// An empty path (no program) always passes.
    pub fn check(&self, path: &str) -> AppResult<()> {
        // A share or device path no granted program lives on is refused from its text,
        // before resolving it touches the network.
        let known = path.is_empty()
            || self
                .0
                .lock()
                .map(|granted| {
                    crate::grants::local_or_on_share_of(path, granted.iter().cloned())
                        && granted.contains(&key(path))
                })
                .unwrap_or(false);
        if known {
            Ok(())
        } else {
            Err(AppError::Validation(
                "LocalSR only runs a program it found itself or that you chose in its own file dialog. Choose the program again.".into(),
            ))
        }
    }
}

/// The FFmpeg programs the host itself finds in the usual places: the `PATH` and the
/// folders package managers and installers use. The engine searches the same ones.
pub fn find_ffmpeg() -> Vec<String> {
    let name = if cfg!(windows) {
        "ffmpeg.exe"
    } else {
        "ffmpeg"
    };
    let mut found: Vec<String> = Vec::new();
    let mut consider = |path: PathBuf| {
        if path.is_file() {
            let resolved = if cfg!(windows) {
                match fs::canonicalize(&path) {
                    Ok(path) => path,
                    Err(_) => return,
                }
            } else {
                path
            };
            let text = resolved.to_string_lossy().into_owned();
            if !found.contains(&text) {
                found.push(text);
            }
        }
    };
    if let Some(paths) = std::env::var_os("PATH") {
        #[cfg(windows)]
        {
            let current = std::env::current_dir()
                .ok()
                .and_then(|path| fs::canonicalize(path).ok());
            let directories = windows_path_entries(&paths.to_string_lossy(), |name| {
                std::env::var_os(name).map(|value| value.to_string_lossy().into_owned())
            });
            for candidate in windows_ffmpeg_candidates(
                &directories,
                current.as_deref(),
                |path| fs::canonicalize(path).ok(),
                |path| path.is_file(),
            ) {
                consider(candidate);
            }
        }
        #[cfg(not(windows))]
        for directory in std::env::split_paths(&paths) {
            consider(directory.join(name));
        }
    }
    for directory in [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/snap/bin",
        "C:\\ffmpeg\\bin",
    ] {
        consider(PathBuf::from(directory).join(name));
    }
    found
}

#[cfg(any(windows, test))]
fn windows_path_entries(path: &str, expand: impl Fn(&str) -> Option<String>) -> Vec<String> {
    path.split(';')
        .filter_map(|entry| {
            let entry = entry.trim().trim_matches('"');
            let mut expanded = String::new();
            let mut remaining = entry;
            while let Some(start) = remaining.find('%') {
                expanded.push_str(&remaining[..start]);
                let tail = &remaining[start + 1..];
                let Some(end) = tail.find('%') else {
                    expanded.push_str(&remaining[start..]);
                    remaining = "";
                    break;
                };
                let name = &tail[..end];
                if let Some(value) = expand(name) {
                    expanded.push_str(&value);
                } else {
                    expanded.push('%');
                    expanded.push_str(name);
                    expanded.push('%');
                }
                remaining = &tail[end + 1..];
            }
            expanded.push_str(remaining);
            let normalized = expanded.replace('/', "\\");
            let bytes = normalized.as_bytes();
            let drive = bytes.len() >= 3
                && bytes[0].is_ascii_alphabetic()
                && bytes[1] == b':'
                && bytes[2] == b'\\';
            let share = normalized.strip_prefix(r"\\").is_some_and(|rest| {
                if let Some(extended) = rest.strip_prefix(r"?\") {
                    let bytes = extended.as_bytes();
                    let extended_drive = bytes.len() >= 3
                        && bytes[0].is_ascii_alphabetic()
                        && bytes[1] == b':'
                        && bytes[2] == b'\\';
                    let extended_share = extended
                        .get(..4)
                        .filter(|prefix| prefix.eq_ignore_ascii_case("UNC\\"))
                        .and_then(|_| extended.get(4..))
                        .is_some_and(|share| {
                            let mut parts = share.split('\\');
                            parts.next().is_some_and(|part| !part.is_empty())
                                && parts.next().is_some_and(|part| !part.is_empty())
                        });
                    return extended_drive || extended_share;
                }
                let mut parts = rest.split('\\');
                parts
                    .next()
                    .is_some_and(|part| !part.is_empty() && part != "." && part != "?")
                    && parts.next().is_some_and(|part| !part.is_empty())
            });
            (drive || share).then_some(expanded)
        })
        .collect()
}

#[cfg(any(windows, test))]
fn windows_ffmpeg_candidates(
    directories: &[String],
    current: Option<&Path>,
    canonicalize: impl Fn(&Path) -> Option<PathBuf>,
    is_file: impl Fn(&Path) -> bool,
) -> Vec<PathBuf> {
    directories
        .iter()
        .filter_map(|directory| {
            let directory = canonicalize(Path::new(directory))?;
            if current.is_some_and(|current| {
                directory.to_string_lossy().to_lowercase()
                    == current.to_string_lossy().to_lowercase()
            }) {
                return None;
            }
            let candidate = canonicalize(&directory.join("ffmpeg.exe"))?;
            is_file(&candidate).then_some(candidate)
        })
        .collect()
}

/// A program is known by its real location, so a link to it and the link's target are
/// one program.
fn key(path: &str) -> String {
    fs::canonicalize(path)
        .map(|real| real.to_string_lossy().into_owned())
        .unwrap_or_else(|_| path.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn windows_path_entries_require_absolute_drive_or_share_and_expand_variables() {
        let entries = windows_path_entries(
            r#""%TOOLS%";C:relative;\rooted;.;C:\Current;\\server\share\bin;\\server;\\.\pipe;\\?\C:relative;\\?\UnC\server\share\bin"#,
            |name| (name == "TOOLS").then(|| r"C:\Tools".to_owned()),
        );
        assert_eq!(
            entries,
            vec![
                r"C:\Tools",
                r"C:\Current",
                r"\\server\share\bin",
                r"\\?\UnC\server\share\bin"
            ]
        );
    }

    #[test]
    fn windows_search_skips_resolved_current_directory_and_returns_resolved_program() {
        let directories = vec![r"C:\alias".to_owned(), r"C:\tools".to_owned()];
        let found = windows_ffmpeg_candidates(
            &directories,
            Some(Path::new(r"C:\current")),
            |path| match path.to_string_lossy().replace('/', "\\").as_str() {
                r"C:\alias" => Some(PathBuf::from(r"C:\CURRENT")),
                r"C:\tools" => Some(PathBuf::from(r"C:\actual")),
                r"C:\actual\ffmpeg.exe" => Some(PathBuf::from(r"C:\actual\real.exe")),
                _ => None,
            },
            |_| true,
        );
        assert_eq!(found, vec![PathBuf::from(r"C:\actual\real.exe")]);
    }

    #[test]
    fn windows_search_excludes_case_variants_of_unicode_current_directory() {
        let directories = vec![r"C:\BÜRO".to_owned()];
        let found = windows_ffmpeg_candidates(
            &directories,
            Some(Path::new(r"C:\büro")),
            |path| Some(path.to_path_buf()),
            |_| true,
        );
        assert!(found.is_empty());
    }

    #[test]
    fn only_a_path_the_host_obtained_is_accepted() {
        let granted = GrantedPrograms::default();
        assert!(granted.check("").is_ok());
        assert!(granted.check("/tmp/planted-tool").is_err());
        granted.grant("/tmp/planted-tool");
        assert!(granted.check("/tmp/planted-tool").is_ok());
        assert!(granted.check("/tmp/another-tool").is_err());
    }

    #[test]
    fn a_share_or_device_program_is_refused_unless_a_granted_one_is_there() {
        let granted = GrantedPrograms::default();
        assert!(granted.check(r"\\attacker\share\ffmpeg.exe").is_err());
        assert!(granted.check(r"\\.\pipe\ffmpeg").is_err());
        granted.grant(r"\\?\UNC\nas\tools\ffmpeg.exe");
        assert!(crate::grants::local_or_on_share_of(
            r"\\NAS\tools\ffmpeg.exe",
            [r"\\?\UNC\nas\tools\ffmpeg.exe".to_string()]
        ));
        assert!(!crate::grants::local_or_on_share_of(
            r"\\attacker\share\ffmpeg.exe",
            [r"\\?\UNC\nas\tools\ffmpeg.exe".to_string()]
        ));
        assert!(!crate::grants::is_local(r"\\attacker\share\hint"));
        assert!(crate::grants::is_local("/Users/me/Pictures"));
    }

    #[test]
    fn a_link_and_its_target_are_one_program() {
        let directory = tempfile::tempdir().unwrap();
        let target = directory.path().join("ffmpeg");
        std::fs::write(&target, b"program").unwrap();
        let link = directory.path().join("link");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&target, &link).unwrap();
        #[cfg(not(unix))]
        std::fs::copy(&target, &link).map(drop).unwrap();
        let granted = GrantedPrograms::default();
        granted.grant(target.to_str().unwrap());
        #[cfg(unix)]
        assert!(granted.check(link.to_str().unwrap()).is_ok());
        assert!(granted
            .check(directory.path().join("other").to_str().unwrap())
            .is_err());
    }

    #[test]
    fn a_path_only_the_engine_reports_is_not_granted() {
        let granted = GrantedPrograms::default();
        let directory = tempfile::tempdir().unwrap();
        let planted = directory.path().join("ffmpeg");
        std::fs::write(&planted, b"planted").unwrap();
        let planted = planted.to_string_lossy().into_owned();
        granted.grant_reported(&json!({"external_ffmpeg": {
            "detected": [planted], "candidates": [planted, 5]}}));
        granted.grant_reported(&json!(null));
        assert!(granted.check(&planted).is_err());
    }

    #[test]
    fn an_engine_reported_path_is_granted_when_the_host_finds_the_same_one() {
        let granted = GrantedPrograms::default();
        let media = json!({"external_ffmpeg": {
            "detected": ["/usr/bin/ffmpeg"], "candidates": ["/opt/homebrew/bin/ffmpeg", 5]}});
        granted.grant_reported_among(&media, &["/usr/bin/ffmpeg".to_owned()]);
        assert!(granted.check("/usr/bin/ffmpeg").is_ok());
        assert!(granted.check("/opt/homebrew/bin/ffmpeg").is_err());
        assert!(granted.check("/usr/local/bin/ffmpeg").is_err());
    }
}
