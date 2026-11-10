//! Image/video work has a host-owned scratch directory so a stalled GPU worker can
//! be stopped without leaving a partial export or touching an existing result.
use std::{
    fs,
    path::PathBuf,
    time::{Duration, Instant},
};

use serde_json::{json, Value};
use uuid::Uuid;

use crate::error::AppResult;

pub const CANCEL_GRACE: Duration = Duration::from_secs(8);

/// Remove the scratch folders a killed host left beside the outputs of the jobs it was
/// running: a host that ended without cleaning up (a crash, a power loss) leaves them
/// with every partial frame. Only folders named like the host's own, and never a link.
pub fn sweep_scratch(outputs: &[PathBuf]) {
    let folders: std::collections::HashSet<&std::path::Path> = outputs
        .iter()
        .filter_map(|output| output.parent())
        .collect();
    for folder in folders {
        let Ok(entries) = fs::read_dir(folder) else {
            continue;
        };
        for entry in entries.flatten() {
            let ours = entry
                .file_name()
                .to_string_lossy()
                .strip_prefix(".localsr-job-")
                .is_some_and(|id| Uuid::parse_str(id).is_ok());
            let plain_folder = entry
                .file_type()
                .is_ok_and(|kind| kind.is_dir() && !kind.is_symlink());
            if ours && plain_folder {
                let _ = fs::remove_dir_all(entry.path());
            }
        }
    }
}

#[derive(Default)]
pub struct Cancellation {
    pub job_id: String,
    /// When the first cancellation still pending was asked for; the engine is stopped
    /// when it does not answer within `CANCEL_GRACE`.
    pub requested_at: Option<Instant>,
    /// The jobs a cancellation was asked for and that have not ended.
    requested: Vec<String>,
    pub resetting: bool,
    pub job_ids: Vec<String>,
    scratch: Vec<(String, PathBuf)>,
}

impl Cancellation {
    pub fn prepare(&mut self, id: &str, message: &mut Value) -> AppResult<()> {
        // What an earlier job left behind and cannot be removed now stays listed for a
        // later try; it does not keep the next job from running.
        if let Err(error) = self.cleanup() {
            eprintln!("LocalSR could not remove an earlier job's temporary folder: {error}");
        }
        self.job_id = id.into();
        self.requested_at = None;
        self.requested.clear();
        self.resetting = false;
        if message["type"] == "image_batch_request" {
            let mut jobs = message["data"]["jobs"].take();
            let members = jobs.as_array_mut().map(std::mem::take).unwrap_or_default();
            let mut kept = Vec::with_capacity(members.len());
            for mut data in members {
                let member = data["job_id"].as_str().unwrap_or_default().to_owned();
                if let Err(error) = self.prepare_scratch(&member, &mut data, false) {
                    if kept.is_empty() {
                        self.finish()?;
                        return Err(error);
                    }
                    // A folder that cannot be used (an ejected drive) ends the batch
                    // before that photo; it fails on its own when its turn comes.
                    break;
                }
                self.job_ids.push(member);
                kept.push(data);
            }
            *message = if kept.len() == 1 {
                json!({"type": "job_request", "data": kept.pop()})
            } else {
                json!({"type": "image_batch_request", "data": {"jobs": kept}})
            };
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
        // A folder that cannot be removed stays listed, for a later try; the member
        // leaves the batch either way.
        let mut failure = None;
        if let Some(index) = self.scratch.iter().position(|(member, _)| member == id) {
            match remove_folder(&self.scratch[index].1) {
                Ok(()) => {
                    self.scratch.remove(index);
                }
                Err(error) => failure = Some(error),
            }
        }
        self.job_ids.retain(|member| member != id);
        // A cancellation ends with the job it was asked for: the photos after it are
        // not being cancelled and must not be stopped by its deadline.
        self.requested.retain(|member| member != id);
        if self.requested.is_empty() {
            self.requested_at = None;
        }
        let finished = if self.job_ids.is_empty() {
            self.finish()
        } else {
            self.job_id = self.job_ids[0].clone();
            Ok(())
        };
        match failure {
            Some(error) => Err(error.into()),
            None => finished,
        }
    }

    pub fn request(&mut self, id: &str, now: Instant) {
        if self.job_ids.iter().any(|member| member == id) {
            if !self.requested.iter().any(|member| member == id) {
                self.requested.push(id.to_owned());
            }
            self.requested_at.get_or_insert(now);
        }
    }

    /// Whether a cancellation was asked for this job and it has not ended yet.
    pub fn is_cancelling(&self, id: &str) -> bool {
        self.requested.iter().any(|member| member == id)
    }

    pub fn expired(&self, now: Instant) -> bool {
        !self.job_id.is_empty()
            && self
                .requested_at
                .is_some_and(|at| now.duration_since(at) >= CANCEL_GRACE)
    }

    /// Remove every scratch folder. One that cannot be removed does not stop the rest;
    /// it stays listed and the first such error is returned.
    pub fn cleanup(&mut self) -> AppResult<()> {
        let mut failure = None;
        self.scratch.retain(|(_, path)| match remove_folder(path) {
            Ok(()) => false,
            Err(error) => {
                failure.get_or_insert(error);
                true
            }
        });
        self.job_ids.clear();
        match failure {
            Some(error) => Err(error.into()),
            None => Ok(()),
        }
    }

    /// End all work: remove the folders and forget the jobs, even when a folder cannot
    /// be removed (the error is returned; the state is reset regardless).
    pub fn finish(&mut self) -> AppResult<()> {
        let result = self.cleanup();
        *self = Self::default();
        result
    }
}

fn remove_folder(path: &std::path::Path) -> std::io::Result<()> {
    match fs::remove_dir_all(path) {
        Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(error),
        _ => Ok(()),
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

    #[test]
    fn cancelling_one_photo_of_a_batch_never_stops_the_engine_for_the_next() {
        let directory = tempfile::tempdir().unwrap();
        let mut message = json!({"type":"image_batch_request", "data":{"jobs":[
            {"job_id":"a", "output_path":directory.path().join("a.png")},
            {"job_id":"b", "output_path":directory.path().join("b.png")}
        ]}});
        let mut control = Cancellation::default();
        control.prepare("a", &mut message).unwrap();
        let now = Instant::now();
        control.request("a", now);
        assert!(control.is_cancelling("a"));
        assert!(!control.is_cancelling("b"));
        control.finish_member("a").unwrap();
        // The second photo runs on; the first one's deadline is over with it.
        assert!(!control.is_cancelling("a"));
        assert!(!control.expired(now + CANCEL_GRACE * 2));
        // Cancelling it later starts its own deadline.
        control.request("b", now + CANCEL_GRACE);
        assert!(control.is_cancelling("b"));
        assert!(!control.expired(now + CANCEL_GRACE));
        assert!(control.expired(now + CANCEL_GRACE * 2));
        control.finish().unwrap();
    }

    #[test]
    fn a_member_whose_folder_cannot_be_removed_still_leaves_the_batch() {
        let directory = tempfile::tempdir().unwrap();
        let mut control = Cancellation::default();
        // A file where a scratch folder should be cannot be removed as a folder.
        let stuck = directory.path().join("stuck");
        fs::write(&stuck, b"x").unwrap();
        control.job_ids = vec!["a".into(), "b".into()];
        control.scratch.push(("a".into(), stuck));
        assert!(control.finish_member("a").is_err());
        assert_eq!(control.job_ids, ["b"]);
        // The last one resets the state, and still reports the folder that stays.
        assert!(control.finish_member("b").is_err());
        assert!(control.job_ids.is_empty());
        assert!(control.job_id.is_empty());
    }

    #[test]
    fn the_next_job_is_prepared_although_an_earlier_folder_cannot_be_removed() {
        let directory = tempfile::tempdir().unwrap();
        let mut control = Cancellation::default();
        let stuck = directory.path().join("stuck");
        fs::write(&stuck, b"x").unwrap();
        control.scratch.push(("earlier".into(), stuck));
        let mut message =
            json!({"type":"job_request","data":{"output_path":directory.path().join("o.png")}});
        control.prepare("next", &mut message).unwrap();
        assert!(message["data"]["scratch_directory"].is_string());
        control.finish().ok();
    }

    #[test]
    fn leftover_scratch_folders_are_swept_and_nothing_else() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("clip.mp4");
        let ours = directory
            .path()
            .join(format!(".localsr-job-{}", Uuid::new_v4()));
        fs::create_dir(&ours).unwrap();
        fs::write(ours.join("frame-000001.png"), b"partial frame").unwrap();
        let keep = [
            directory.path().join(".localsr-job-not-an-id"),
            directory.path().join("holiday"),
        ];
        for folder in &keep {
            fs::create_dir(folder).unwrap();
            fs::write(folder.join("file"), b"x").unwrap();
        }
        let other = tempfile::tempdir().unwrap();
        let elsewhere = other
            .path()
            .join(format!(".localsr-job-{}", Uuid::new_v4()));
        fs::create_dir(&elsewhere).unwrap();
        sweep_scratch(&[output, directory.path().join("missing/clip.mp4")]);
        assert!(!ours.exists());
        assert!(keep.iter().all(|folder| folder.join("file").is_file()));
        assert!(elsewhere.is_dir());
    }

    #[test]
    fn a_member_without_a_scratch_folder_ends_the_batch_before_it() {
        let directory = tempfile::tempdir().unwrap();
        let at = |name: &str| directory.path().join(name);
        let mut message = json!({"type":"image_batch_request", "data":{"jobs":[
            {"job_id":"a", "output_path":at("a.png")},
            {"job_id":"b", "output_path":at("b.png")},
            {"job_id":"c", "output_path":at("gone/c.png")},
            {"job_id":"d", "output_path":at("d.png")}
        ]}});
        let mut control = Cancellation::default();
        control.prepare("a", &mut message).unwrap();
        // The photos before it still run together; c and later wait in the queue.
        assert_eq!(control.job_ids, ["a", "b"]);
        let jobs = message["data"]["jobs"].as_array().unwrap();
        assert_eq!(jobs.len(), 2);
        assert!(PathBuf::from(jobs[1]["scratch_directory"].as_str().unwrap()).is_dir());
        control.finish().unwrap();

        let mut message = json!({"type":"image_batch_request", "data":{"jobs":[
            {"job_id":"a", "output_path":at("a.png")},
            {"job_id":"b", "output_path":at("gone/b.png")}
        ]}});
        control.prepare("a", &mut message).unwrap();
        // One photo left is an ordinary job request.
        assert_eq!(message["type"], "job_request");
        assert_eq!(message["data"]["job_id"], "a");
        assert!(message["data"]["scratch_directory"].is_string());
        assert_eq!(control.job_ids, ["a"]);
        control.finish().unwrap();

        let mut message = json!({"type":"image_batch_request", "data":{"jobs":[
            {"job_id":"a", "output_path":at("gone/a.png")},
            {"job_id":"b", "output_path":at("b.png")}
        ]}});
        assert!(control.prepare("a", &mut message).is_err());
        assert!(control.job_ids.is_empty());
    }
}
