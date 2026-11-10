//! Programs the host starts on the web view's word. The web view may name an
//! executable (the user's FFmpeg), but the host runs one only when it obtained the
//! path itself: from its own file dialog, from its own search, or from the settings
//! file of an earlier run. A page that cannot show a native dialog therefore cannot
//! make the host run a program it picked.
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
        let known = path.is_empty()
            || self
                .0
                .lock()
                .map(|granted| granted.contains(&key(path)))
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
            let text = path.to_string_lossy().into_owned();
            if !found.contains(&text) {
                found.push(text);
            }
        }
    };
    if let Some(paths) = std::env::var_os("PATH") {
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
    fn only_a_path_the_host_obtained_is_accepted() {
        let granted = GrantedPrograms::default();
        assert!(granted.check("").is_ok());
        assert!(granted.check("/tmp/planted-tool").is_err());
        granted.grant("/tmp/planted-tool");
        assert!(granted.check("/tmp/planted-tool").is_ok());
        assert!(granted.check("/tmp/another-tool").is_err());
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
