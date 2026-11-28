//! The full-size picture of each step of the last lone photo job, kept so the step chips can
//! show it through the comparison path. The files live in `<work_root>/steps/<job_id>`: they
//! are pictures to look at, never exports.

use std::path::{Path, PathBuf};

use crate::{
    error::{AppError, AppResult},
    state::{lock, AppState},
    types::{LastSteps, RuntimeStatus, StepFilesRun},
};

/// Separates the job id from the step's position in a comparison target.
const TARGET_SEPARATOR: char = '#';

pub fn steps_root(work_root: &Path) -> PathBuf {
    work_root.join("steps")
}

pub fn folder_of(work_root: &Path, job_id: &str) -> PathBuf {
    steps_root(work_root).join(job_id)
}

/// A comparison target of a step: `<job_id>#<index>`.
pub fn is_step_target(target: &str) -> bool {
    target.contains(TARGET_SEPARATOR)
}

/// Start-up: whatever an earlier run left behind (a crash) goes.
pub fn empty_at_start(work_root: &Path) {
    let _ = std::fs::remove_dir_all(steps_root(work_root));
}

fn remove_folder(directory: &Path) {
    let _ = std::fs::remove_dir_all(directory);
}

/// The step file `target` names, for a comparison. Only the last lone job's kept steps
/// resolve; any other index or job is an error. The path is built here from the job id and
/// the index, never taken from the engine.
pub fn step_file(last_steps: Option<&LastSteps>, target: &str) -> AppResult<String> {
    let unavailable = || AppError::Validation("this step's picture is no longer available".into());
    let (job_id, index) = target
        .split_once(TARGET_SEPARATOR)
        .ok_or_else(unavailable)?;
    let last = last_steps
        .filter(|last| last.job_id == job_id)
        .ok_or_else(unavailable)?;
    let index: usize = index.parse().map_err(|_| unavailable())?;
    if !last.steps.get(index).is_some_and(|step| step.kept) {
        return Err(unavailable());
    }
    let path = last.directory.join(format!("{index}.png"));
    let metadata = std::fs::symlink_metadata(&path).map_err(|_| unavailable())?;
    if !metadata.is_file() {
        return Err(unavailable());
    }
    Ok(path.to_string_lossy().into_owned())
}

/// Ends the folder of the running job when it is not going to be kept (cancelled, failed,
/// the engine gone).
pub fn drop_run(state: &AppState) -> AppResult<()> {
    let run = lock(&state.runtime)?.step_files_run.take();
    if let Some(run) = run {
        remove_folder(&run.directory);
    }
    Ok(())
}

/// The job that wrote step files completed: its steps become `last_steps` when at least one
/// file was kept, otherwise its folder goes. Called with the runtime locked.
pub fn keep_run(runtime: &mut RuntimeStatus, job_id: &str) -> Option<PathBuf> {
    let run: StepFilesRun = runtime.step_files_run.take()?;
    if run.job_id != job_id {
        return Some(run.directory);
    }
    let steps: Vec<_> = runtime
        .job_steps
        .iter()
        .cloned()
        .map(|mut step| {
            // The window takes the pictures from the files, not from the preview folder.
            step.preview_url.clear();
            step
        })
        .collect();
    if !steps.iter().any(|step| step.kept) {
        return Some(run.directory);
    }
    runtime.last_steps = Some(LastSteps {
        job_id: run.job_id,
        media_id: run.media_id,
        steps,
        directory: run.directory,
    });
    None
}

pub fn remove_kept_folder(directory: Option<PathBuf>) {
    if let Some(directory) = directory {
        remove_folder(&directory);
    }
}

/// Clears `last_steps` and its folder, and the running job's folder too when `running` is
/// set. Returns the preview-folder URLs a comparison of a step held, for the caller to
/// release. `only_media` limits it to the steps of that file.
pub fn release_locked(
    runtime: &mut RuntimeStatus,
    only_media: Option<&str>,
    running: bool,
) -> Vec<String> {
    let mut stale = Vec::new();
    let matches = |media_id: &str| only_media.is_none_or(|only| only == media_id);
    if runtime
        .last_steps
        .as_ref()
        .is_some_and(|last| matches(&last.media_id))
    {
        if let Some(last) = runtime.last_steps.take() {
            remove_folder(&last.directory);
        }
        // A comparison that shows a step has nothing to show any more.
        let pair = &runtime.comparison_pair;
        if is_step_target(&pair.showing.target_id) || is_step_target(&pair.against.target_id) {
            let pair = std::mem::take(&mut runtime.comparison_pair);
            stale.push(pair.showing.preview_data_url);
            stale.push(pair.against.preview_data_url);
        }
    }
    if running {
        if let Some(run) = runtime.step_files_run.take() {
            remove_folder(&run.directory);
        }
    }
    stale
}

/// The window's steps and the folder go: the next job starts, a result or file is removed,
/// the app quits.
pub fn release(state: &AppState) -> AppResult<()> {
    let stale = {
        let mut runtime = lock(&state.runtime)?;
        release_locked(&mut runtime, None, false)
    };
    release_previews(state, &stale)
}

/// Drops pictures no snapshot refers to any more from the preview folder.
pub fn release_previews(state: &AppState, stale: &[String]) -> AppResult<()> {
    if stale.iter().all(String::is_empty) {
        return Ok(());
    }
    let database = lock(&state.database)?;
    let runtime = lock(&state.runtime)?;
    let refs: Vec<&str> = stale.iter().map(String::as_str).collect();
    crate::preview_cache::remove_if_unreferenced(
        &state.paths.preview_root,
        &database,
        Some(&runtime),
        &refs,
    )
}
