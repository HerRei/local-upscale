//! Image/video work has a host-owned scratch directory so a stalled GPU worker can
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
    pub job_ids: Vec<String>,
    scratch: Vec<(String, PathBuf)>,
}

impl Cancellation {
    pub fn prepare(&mut self, id: &str, message: &mut Value) -> AppResult<()> {
        self.cleanup()?;
        self.job_id = id.into();
        self.requested_at = None;
        self.resetting = false;
        if message["type"] == "image_batch_request" {
            if let Some(jobs) = message["data"]["jobs"].as_array_mut() {
                for data in jobs {
                    let member = data["job_id"].as_str().unwrap_or_default().to_owned();
                    self.job_ids.push(member.clone());
                    if let Err(error) = self.prepare_scratch(&member, data, false) {
                        self.finish()?;
                        return Err(error);
                    }
                }
            }
        } else {
            self.job_ids = vec![id.into()];
            let video = message["type"] == "video_job_request";
            if video || message["type"] == "job_request" || message["type"] == "edit_job_request" {
                self.prepare_scratch(id, &mut message["data"], video)?;
            }
        }
        Ok(())
    }

    fn prepare_scratch(&mut self, id: &str, data: &mut Value, video: bool) -> AppResult<()> {
        if let Some(output) = data[if video {
            "output_video_path"
        } else {
            "output_path"
        }]
        .as_str()
        {
            let output = PathBuf::from(output);
            if let Some(parent) = output.parent() {
                let scratch = parent.join(format!(".localsr-job-{}", Uuid::new_v4()));
                fs::create_dir(&scratch)?;
                if video {
                    data["temporary_directory"] = scratch.to_string_lossy().into_owned().into();
                } else {
                    data["scratch_directory"] = scratch.to_string_lossy().into_owned().into();
                    data["output_temporary_directory"] =
                        scratch.to_string_lossy().into_owned().into();
                }
                self.scratch.push((id.into(), scratch));
            }
        }
        Ok(())
    }

    pub fn finish_member(&mut self, id: &str) -> AppResult<()> {
        if let Some(index) = self.scratch.iter().position(|(member, _)| member == id) {
            let path = &self.scratch[index].1;
            match fs::remove_dir_all(path) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
            self.scratch.remove(index);
        }
        self.job_ids.retain(|member| member != id);
        if self.job_ids.is_empty() {
            self.finish()?;
        } else {
            self.job_id = self.job_ids[0].clone();
        }
        Ok(())
    }

    pub fn request(&mut self, id: &str, now: Instant) {
        if self.job_ids.iter().any(|member| member == id) && self.requested_at.is_none() {
            self.requested_at = Some(now);
        }
    }

    pub fn expired(&self, now: Instant) -> bool {
        !self.job_id.is_empty()
            && self
                .requested_at
                .is_some_and(|at| now.duration_since(at) >= CANCEL_GRACE)
    }

    pub fn cleanup(&mut self) -> AppResult<()> {
        for (_, path) in &self.scratch {
            match fs::remove_dir_all(path) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
        }
        self.scratch.clear();
        self.job_ids.clear();
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

    #[test]
    fn image_and_benchmark_cancellation_have_a_bounded_deadline_too() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("existing.png");
        fs::write(&output, b"original result").unwrap();
        let mut message = json!({"type":"job_request","data":{"output_path":output}});
        let mut control = Cancellation::default();
        control.prepare("image", &mut message).unwrap();
        let scratch = PathBuf::from(message["data"]["scratch_directory"].as_str().unwrap());
        assert_eq!(
            message["data"]["scratch_directory"],
            message["data"]["output_temporary_directory"]
        );
        fs::write(scratch.join("partial.tmp"), b"partial").unwrap();
        let now = Instant::now();
        control.request("image", now);
        assert!(control.expired(now + CANCEL_GRACE));
        control.finish().unwrap();
        assert!(!scratch.exists());
        assert_eq!(fs::read(output).unwrap(), b"original result");
        control
            .prepare(
                "benchmark",
                &mut json!({"type":"benchmark_request","data":{}}),
            )
            .unwrap();
        control.request("benchmark", now);
        assert!(control.expired(now + CANCEL_GRACE));
        control.finish().unwrap();
        assert!(!control.expired(now + CANCEL_GRACE));
    }

    #[test]
    fn edit_cancellation_cleans_only_owned_scratch_and_preserves_existing_images() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("existing.png");
        fs::write(&output, b"existing image").unwrap();
        let mut message = json!({"type":"edit_job_request","data":{"output_path":output}});
        let mut control = Cancellation::default();
        control.prepare("edit", &mut message).unwrap();
        let scratch = PathBuf::from(message["data"]["scratch_directory"].as_str().unwrap());
        fs::write(scratch.join("reference.png"), b"temporary reference").unwrap();
        control.finish().unwrap();
        assert!(!scratch.exists());
        assert_eq!(fs::read(output).unwrap(), b"existing image");
    }
    #[test]
    fn batch_scratch_and_deadline_survive_an_early_member_completion() {
        let directory = tempfile::tempdir().unwrap();
        let mut message = json!({"type":"image_batch_request", "data":{"jobs":[
            {"job_id":"a", "output_path":directory.path().join("a.png")},
            {"job_id":"b", "output_path":directory.path().join("b.png")}
        ]}});
        let mut control = Cancellation::default();
        control.prepare("a", &mut message).unwrap();
        let paths: Vec<PathBuf> = message["data"]["jobs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|job| PathBuf::from(job["scratch_directory"].as_str().unwrap()))
            .collect();
        let now = Instant::now();
        control.request("b", now);
        control.finish_member("a").unwrap();
        assert!(!paths[0].exists());
        assert!(paths[1].exists());
        assert!(control.expired(now + CANCEL_GRACE));
        control.finish().unwrap();
        assert!(!paths[1].exists());
    }
}
