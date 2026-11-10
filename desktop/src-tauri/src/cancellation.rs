//! Image/video work has a host-owned scratch directory so a stalled GPU worker can
//! be stopped without leaving a partial export or touching an existing result.
use std::{
    fs,
    path::PathBuf,
    time::{Duration, Instant},
};

use serde_json::{json, Value};
use uuid::Uuid;

use crate::{database::ScratchRecord, error::AppResult};

pub const CANCEL_GRACE: Duration = Duration::from_secs(8);

/// Remove the scratch folders a killed host recorded for its unfinished jobs: it ended
/// without cleaning up (a crash, a power loss) and left them with every partial frame.
/// Only the exact folder recorded for a job goes, and only when it is what the
/// dispatcher made: a plain folder named `.localsr-job-<id>` directly in the output's
/// own folder, neither of them a link. Nothing is removed while an engine of this
/// instance runs. Returns the jobs whose folder is gone, so their records can be dropped.
pub fn sweep_scratch(
    records: &[ScratchRecord],
    worker: &crate::state::WorkerControl,
) -> Vec<String> {
    let mut swept = Vec::new();
    if worker.pid.load(std::sync::atomic::Ordering::SeqCst) != 0 {
        return swept;
    }
    for record in records {
        if provably_ours(record) {
            match fs::remove_dir_all(&record.scratch) {
                Ok(()) => swept.push(record.id.clone()),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    swept.push(record.id.clone());
                }
                Err(_) => {}
            }
        } else if fs::symlink_metadata(&record.scratch)
            .is_err_and(|error| error.kind() == std::io::ErrorKind::NotFound)
        {
            // Nothing is there to remove.
            swept.push(record.id.clone());
        }
    }
    swept
}

/// Whether the recorded folder is a real folder of the shape the dispatcher makes,
/// beside the output, reached without a link.
fn provably_ours(record: &ScratchRecord) -> bool {
    let Some(parent) = record.output.parent() else {
        return false;
    };
    let named = record
        .scratch
        .file_name()
        .and_then(|name| name.to_str())
        .and_then(|name| name.strip_prefix(".localsr-job-"))
        .is_some_and(|id| Uuid::parse_str(id).is_ok());
    if !named || record.scratch.parent() != Some(parent) {
        return false;
    }
    let plain = |path: &std::path::Path| {
        fs::symlink_metadata(path).is_ok_and(|info| info.is_dir() && !info.file_type().is_symlink())
    };
    if !plain(parent) || !plain(&record.scratch) {
        return false;
    }
    match (fs::canonicalize(parent), fs::canonicalize(&record.scratch)) {
        (Ok(parent), Ok(scratch)) => record
            .scratch
            .file_name()
            .is_some_and(|name| scratch == parent.join(name)),
        _ => false,
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

    /// The scratch folder made for each job of the dispatch under way.
    pub fn scratch_folders(&self) -> Vec<(String, PathBuf)> {
        self.scratch.clone()
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

    fn record(id: &str, output: &std::path::Path, scratch: &std::path::Path) -> ScratchRecord {
        ScratchRecord {
            id: id.into(),
            output: output.into(),
            scratch: scratch.into(),
        }
    }

    fn scratch_name() -> String {
        format!(".localsr-job-{}", Uuid::new_v4())
    }

    #[test]
    fn only_the_recorded_scratch_folder_is_swept() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("clip.mp4");
        let ours = directory.path().join(scratch_name());
        fs::create_dir(&ours).unwrap();
        fs::write(ours.join("frame-000001.png"), b"partial frame").unwrap();
        // Folders that look like ours but were not recorded for a job stay, whoever
        // made them.
        let keep = [
            directory.path().join(".localsr-job-not-an-id"),
            directory.path().join("holiday"),
            directory.path().join(scratch_name()),
        ];
        for folder in &keep {
            fs::create_dir(folder).unwrap();
            fs::write(folder.join("file"), b"x").unwrap();
        }
        let done = sweep_scratch(
            &[record("job", &output, &ours)],
            &crate::state::WorkerControl::default(),
        );
        assert_eq!(done, ["job"]);
        assert!(!ours.exists());
        assert!(keep.iter().all(|folder| folder.join("file").is_file()));
    }

    #[test]
    fn a_recorded_name_that_the_dispatcher_could_not_have_made_is_refused() {
        let directory = tempfile::tempdir().unwrap();
        let other = tempfile::tempdir().unwrap();
        let output = directory.path().join("clip.mp4");
        let worker = crate::state::WorkerControl::default();
        let holiday = directory.path().join("holiday");
        let elsewhere = other.path().join(scratch_name());
        let nested = directory.path().join("sub").join(scratch_name());
        for folder in [&holiday, &elsewhere, &nested] {
            fs::create_dir_all(folder).unwrap();
            fs::write(folder.join("file"), b"x").unwrap();
        }
        let records: Vec<ScratchRecord> = [&holiday, &elsewhere, &nested]
            .iter()
            .map(|folder| record("job", &output, folder))
            .collect();
        for one in &records {
            assert!(sweep_scratch(std::slice::from_ref(one), &worker).is_empty());
        }
        for folder in [&holiday, &elsewhere, &nested] {
            assert!(folder.join("file").is_file());
        }
    }

    #[test]
    fn a_folder_that_is_gone_counts_as_swept() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("clip.mp4");
        let gone = directory.path().join(scratch_name());
        let done = sweep_scratch(
            &[record("job", &output, &gone)],
            &crate::state::WorkerControl::default(),
        );
        assert_eq!(done, ["job"]);
    }

    #[test]
    fn nothing_is_swept_while_an_engine_of_this_instance_runs() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("clip.mp4");
        let ours = directory.path().join(scratch_name());
        fs::create_dir(&ours).unwrap();
        fs::write(ours.join("frame"), b"in use").unwrap();
        let worker = crate::state::WorkerControl::default();
        worker.pid.store(4242, std::sync::atomic::Ordering::SeqCst);
        assert!(sweep_scratch(&[record("job", &output, &ours)], &worker).is_empty());
        assert!(ours.join("frame").is_file());
    }

    #[cfg(unix)]
    #[test]
    fn a_redirected_output_folder_or_scratch_folder_is_refused() {
        use std::os::unix::fs::symlink;

        let directory = tempfile::tempdir().unwrap();
        let worker = crate::state::WorkerControl::default();
        // The output's folder was replaced by a link to another folder.
        let real = directory.path().join("real");
        fs::create_dir(&real).unwrap();
        let name = scratch_name();
        fs::create_dir(real.join(&name)).unwrap();
        fs::write(real.join(&name).join("file"), b"x").unwrap();
        let link = directory.path().join("out");
        symlink(&real, &link).unwrap();
        let swept = sweep_scratch(
            &[record("job", &link.join("clip.mp4"), &link.join(&name))],
            &worker,
        );
        assert!(swept.is_empty());
        assert!(real.join(&name).join("file").is_file());
        // The scratch folder itself is a link to a folder of the user's.
        let precious = directory.path().join("precious");
        fs::create_dir(&precious).unwrap();
        fs::write(precious.join("file"), b"keep").unwrap();
        let folder = directory.path().join("folder");
        fs::create_dir(&folder).unwrap();
        let name = scratch_name();
        symlink(&precious, folder.join(&name)).unwrap();
        let swept = sweep_scratch(
            &[record("job", &folder.join("clip.mp4"), &folder.join(&name))],
            &worker,
        );
        assert!(swept.is_empty());
        assert!(precious.join("file").is_file());
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
