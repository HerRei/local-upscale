//! Programs the host starts on the web view's word. The web view may name an
//! executable (the user's FFmpeg), but the host runs one only when it obtained the
//! path itself: from its own file dialog, from its own search, from the engine's
//! search, or from the settings file of an earlier run. A page that cannot show a
//! native dialog therefore cannot make the host run a program it picked.
use std::{collections::HashSet, fs, sync::Mutex};

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

    /// Grant the paths the engine reported under `media.external_ffmpeg`.
    pub fn grant_reported(&self, media: &Value) {
        for list in ["detected", "candidates"] {
            for path in media["external_ffmpeg"][list]
                .as_array()
                .into_iter()
                .flatten()
            {
                if let Some(path) = path.as_str() {
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
    fn the_engines_search_results_are_granted() {
        let granted = GrantedPrograms::default();
        granted.grant_reported(&json!({"external_ffmpeg": {
            "detected": ["/usr/bin/ffmpeg"], "candidates": ["/opt/homebrew/bin/ffmpeg", 5]}}));
        granted.grant_reported(&json!(null));
        assert!(granted.check("/usr/bin/ffmpeg").is_ok());
        assert!(granted.check("/opt/homebrew/bin/ffmpeg").is_ok());
        assert!(granted.check("/usr/local/bin/ffmpeg").is_err());
    }
}
