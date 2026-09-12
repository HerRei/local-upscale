//! Video work has a host-owned scratch directory so a stalled GPU worker can
//! be stopped without leaving a partial export or touching an existing result.
use std::{
    fs,
    path::PathBuf,
    time::{Duration, Instant},
};

use serde_json::Value;
use uuid::Uuid;

use crate::error::AppResult;

pub const CANCEL_GRACE: Duration = Duration::from_secs(8);

#[derive(Default)]
pub struct Cancellation {
    pub job_id: String,
    pub requested_at: Option<Instant>,
    pub resetting: bool,
    scratch: Option<PathBuf>,
}

impl Cancellation {
    pub fn prepare(&mut self, id: &str, message: &mut Value) -> AppResult<()> {
        self.cleanup()?;
        self.job_id = id.into();
        self.requested_at = None;
        self.resetting = false;
        if message["type"] == "video_job_request" {
            if let Some(output) = message["data"]["output_video_path"].as_str() {
                let output = PathBuf::from(output);
                if let Some(parent) = output.parent() {
                    let scratch = parent.join(format!(".localsr-job-{}", Uuid::new_v4()));
                    fs::create_dir(&scratch)?;
                    message["data"]["temporary_directory"] =
                        scratch.to_string_lossy().into_owned().into();
                    self.scratch = Some(scratch);
                }
            }
        }
        Ok(())
    }

    pub fn request(&mut self, id: &str, now: Instant) {
        if self.job_id == id && self.requested_at.is_none() {
            self.requested_at = Some(now);
        }
    }

    pub fn expired(&self, now: Instant) -> bool {
        self.scratch.is_some()
            && self
                .requested_at
                .is_some_and(|at| now.duration_since(at) >= CANCEL_GRACE)
    }

    pub fn cleanup(&mut self) -> AppResult<()> {
        if let Some(path) = &self.scratch {
            match fs::remove_dir_all(path) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
        }
        self.scratch = None;
        Ok(())
    }

    pub fn finish(&mut self) -> AppResult<()> {
        self.cleanup()?;
        *self = Self::default();
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn deadline_does_not_extend_on_repeated_cancel_or_target_the_next_job() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("output.mp4");
        fs::write(&output, b"existing result").unwrap();
        let mut message = json!({"type":"video_job_request", "data":{"output_video_path":output}});
        let mut control = Cancellation::default();
        control.prepare("first", &mut message).unwrap();
        let scratch = PathBuf::from(message["data"]["temporary_directory"].as_str().unwrap());
        fs::write(scratch.join("partial.tmp"), b"unfinished video").unwrap();
        let now = Instant::now();
        control.request("other", now);
        assert!(!control.expired(now + CANCEL_GRACE));
        control.request("first", now);
        control.request("first", now + CANCEL_GRACE / 2);
        assert!(!control.expired(now + CANCEL_GRACE / 2));
        assert!(control.expired(now + CANCEL_GRACE));
        control.finish().unwrap();
        assert!(!scratch.exists());
        assert_eq!(fs::read(&output).unwrap(), b"existing result");
        control.prepare("second", &mut message).unwrap();
        assert!(!control.expired(now + CANCEL_GRACE * 2));
        control.finish().unwrap();
    }
}
