//! The preview patch: a square of the open photo run through the ticked fixes, asked for
//! by the window and answered by the engine's `patch_*` messages
//! (`docs/design/previews-and-recipes.md`, section 3).
//!
//! The engine runs a patch like a job, one after the other, so a patch never overlaps a
//! job there; the host adds the rest: no patch while a job runs or waits, one run per slot
//! (a newer request cancels the older), and the rule that decides whether an update runs
//! by itself or waits for a click.

use std::{path::Path, sync::Arc};

use serde::Deserialize;
use serde_json::{json, Value};
use tauri::{AppHandle, State};
use uuid::Uuid;

use crate::{
    commands::{patch_plan, PatchPlan},
    error::{AppError, AppResult},
    state::{emit_state_changed, lock, AppState},
    types::{PatchRegion, PatchState, PatchStep, Patches, RuntimeStatus, StartBatchInput},
    worker::{cleanup_stale_previews, send},
};

/// An update predicted at this many seconds or less runs by itself in `auto`.
const AUTO_SECONDS: f64 = 5.0;
/// Source pixels around the region the engine works on, so the borders of the picture sent
/// are the picture's own pixels.
const PADDING: u32 = 32;
/// The longest side of a region (the window sends 384).
const MAX_REGION_SIDE: u32 = 384;

/// What the host keeps of a slot beyond the snapshot.
#[derive(Default)]
pub struct SlotBook {
    /// The run the engine's messages are accepted for.
    patch_id: String,
    /// The engine may still be working on `patch_id`.
    running: bool,
    /// What the pictures of the slot were made from, except the steps.
    context: String,
    /// One signature per step of the slot's plan.
    signatures: Vec<String>,
    /// The same signatures without any `strength`: a step that differs only there is a
    /// new mix of a result the engine still holds.
    bases: Vec<String>,
    /// The steps after the ones that were mixed again were left for `Update preview`.
    rest_waits: bool,
    device: String,
}

#[derive(Default)]
pub struct PatchBook {
    a: SlotBook,
    b: SlotBook,
}

impl PatchBook {
    fn slot(&mut self, slot: &str) -> &mut SlotBook {
        if slot == "a" {
            &mut self.a
        } else {
            &mut self.b
        }
    }

    fn slot_of(&mut self, patch_id: &str) -> Option<&'static str> {
        if patch_id.is_empty() {
            None
        } else if self.a.patch_id == patch_id {
            Some("a")
        } else if self.b.patch_id == patch_id {
            Some("b")
        } else {
            None
        }
    }
}

/// Variant B's change to the plan: the model of one fix (or `upscale`) replaced.
#[derive(Clone, Debug, Deserialize)]
pub struct PatchReplace {
    pub slot: String,
    pub fix: String,
    pub model_id: String,
}

pub struct PatchAsk {
    pub media_id: String,
    pub slot: String,
    pub region: PatchRegion,
    pub input: StartBatchInput,
    pub replace: Option<PatchReplace>,
    pub stage_limit: Option<usize>,
    pub force: bool,
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
pub async fn request_patch_preview(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    media_id: String,
    slot: String,
    region: PatchRegion,
    input: StartBatchInput,
    replace: Option<PatchReplace>,
    stage_limit: Option<usize>,
    force: bool,
) -> AppResult<()> {
    let state = Arc::clone(&state);
    tauri::async_runtime::spawn_blocking(move || {
        let _scheduler = lock(&state.scheduler)?;
        let outcome = request_checked(
            &state,
            PatchAsk {
                media_id,
                slot,
                region,
                input,
                replace,
                stage_limit,
                force,
            },
        );
        emit_state_changed(&app);
        outcome
    })
    .await
    .map_err(|error| AppError::Config(format!("Could not ask for a preview: {error}")))?
}

#[tauri::command]
pub fn cancel_patch_preview(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    slot: String,
) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    cancel_checked(&state, &slot)?;
    emit_state_changed(&app);
    Ok(())
}

fn slot_name(slot: &str) -> AppResult<&'static str> {
    match slot {
        "a" => Ok("a"),
        "b" => Ok("b"),
        _ => Err(AppError::Validation("unknown preview patch slot".into())),
    }
}

fn slot_state<'a>(runtime: &'a mut RuntimeStatus, slot: &str) -> &'a mut PatchState {
    let patches = runtime.patches.get_or_insert_with(Patches::default);
    if slot == "a" {
        &mut patches.a
    } else {
        &mut patches.b
    }
}

/// Stops the slot's run on the engine, when it has one.
fn stop_run(state: &AppState, book: &mut SlotBook) {
    if book.running {
        let _ = send(
            state,
            &json!({"type": "cancel_request", "data": {"job_id": book.patch_id}}),
        );
        book.running = false;
    }
}

pub(crate) fn cancel_checked(state: &AppState, slot: &str) -> AppResult<()> {
    let slot = slot_name(slot)?;
    let mut runtime = lock(&state.runtime)?;
    let mut book = lock(&state.patch)?;
    stop_run(state, book.slot(slot));
    let patch = slot_state(&mut runtime, slot);
    if matches!(patch.state.as_str(), "running" | "stale") {
        patch.state = "idle".into();
        for step in &mut patch.steps {
            if matches!(step.state.as_str(), "running") {
                step.state = "waiting".into();
            }
        }
    }
    Ok(())
}

/// A job is about to be sent: running patches stop, and both slots read `busy` until the
/// window asks again.
pub(crate) fn job_dispatched(state: &AppState) {
    let Ok(mut runtime) = lock(&state.runtime) else {
        return;
    };
    let Ok(mut book) = lock(&state.patch) else {
        return;
    };
    for slot in ["a", "b"] {
        stop_run(state, book.slot(slot));
        if runtime.patches.is_some() {
            let patch = slot_state(&mut runtime, slot);
            patch.state = "busy".into();
            for step in &mut patch.steps {
                if step.state == "running" {
                    step.state = "waiting".into();
                }
            }
        }
    }
}

/// The preview patch was switched off: the engine lets go of the Colour model it kept loaded
/// for the next photo. An engine without the feature never kept one.
pub(crate) fn switched_off(state: &AppState) {
    let has_feature = lock(&state.engine).ok().is_some_and(|engine| {
        engine
            .as_ref()
            .is_some_and(|engine| engine.features.iter().any(|f| f == "patch_preview"))
    });
    if has_feature {
        let _ = send(state, &json!({"type": "patch_release", "data": {}}));
    }
}

/// A new engine process knows no patch: runs that were under way are over.
pub(crate) fn engine_started(state: &AppState) {
    let Ok(mut runtime) = lock(&state.runtime) else {
        return;
    };
    let Ok(mut book) = lock(&state.patch) else {
        return;
    };
    for slot in ["a", "b"] {
        let book = book.slot(slot);
        book.running = false;
        book.patch_id.clear();
        if runtime.patches.is_some() {
            let patch = slot_state(&mut runtime, slot);
            if patch.state == "running" {
                patch.state = "idle".into();
            }
        }
    }
}

fn apply_replace(input: &mut StartBatchInput, replace: &PatchReplace) {
    let restoring = input.task == "denoise";
    if restoring {
        if input
            .fix_stages
            .last()
            .is_some_and(|stage| stage.fix == replace.fix)
        {
            input.model_id.clone_from(&replace.model_id);
        }
    } else if replace.fix == "upscale" {
        input.model_id.clone_from(&replace.model_id);
    }
    for stage in &mut input.fix_stages {
        if stage.fix == replace.fix {
            stage.model_id.clone_from(&replace.model_id);
        }
    }
    for stage in &mut input.step_stages {
        if stage.step == replace.fix {
            stage.model_id = Some(replace.model_id.clone());
        }
    }
}

/// Whether a request for `mode` runs now: Some(true) runs, Some(false) waits for a click
/// and None is refused. `estimate` is the predicted seconds of the steps that would run.
fn runs_by_itself(mode: &str, force: bool, estimate: f64) -> Option<bool> {
    match mode {
        "off" => None,
        _ if force => Some(true),
        "live" => Some(true),
        "manual" => Some(false),
        _ => Some(estimate <= AUTO_SECONDS),
    }
}

fn validate_region(region: &PatchRegion, width: u32, height: u32) -> AppResult<()> {
    if region.width == 0
        || region.height == 0
        || region.width > MAX_REGION_SIDE
        || region.height > MAX_REGION_SIDE
        || region.x.checked_add(region.width).is_none_or(|v| v > width)
        || region
            .y
            .checked_add(region.height)
            .is_none_or(|v| v > height)
    {
        return Err(AppError::Validation("Invalid preview region".into()));
    }
    Ok(())
}

fn step_signature(stage: &crate::protocol::PipelineStageRequest) -> String {
    // The path is part of it: a file replaced at the same place makes a different step.
    serde_json::to_string(stage).unwrap_or_default()
}

/// The signature of a step with its `strength` left out.
fn step_base(stage: &crate::protocol::PipelineStageRequest) -> String {
    let mut base = stage.clone();
    if let Some(params) = base.params.as_mut() {
        params.remove(crate::commands::FIX_STRENGTH_PARAM);
        if params.is_empty() {
            base.params = None;
        }
    }
    step_signature(&base)
}

pub(crate) fn request_checked(state: &AppState, ask: PatchAsk) -> AppResult<()> {
    let slot = slot_name(&ask.slot)?;
    if !lock(&state.engine)?
        .as_ref()
        .is_some_and(|engine| engine.features.iter().any(|f| f == "patch_preview"))
    {
        return Err(AppError::Validation(
            "This engine cannot preview a part of a photo. Update LocalSR.".into(),
        ));
    }
    let media = lock(&state.database)?
        .get_media(&ask.media_id)?
        .ok_or_else(|| AppError::Validation("the picture no longer exists".into()))?;
    state.grants.check_media_input(Path::new(&media.path))?;
    if !matches!(media.kind.as_str(), "image" | "video") || media.probe_status != "ready" {
        return Err(AppError::Validation(
            "the preview patch needs a photo or video that is ready".into(),
        ));
    }
    if !Path::new(&media.path).is_file() {
        return Err(AppError::Validation(format!(
            "{} is no longer available",
            media.name
        )));
    }
    validate_region(&ask.region, media.width, media.height)?;

    let busy = !lock(&state.runtime)?.active_job_id.is_empty()
        || lock(&state.database)?.next_queued_job()?.is_some();
    if busy {
        let mut runtime = lock(&state.runtime)?;
        let patch = slot_state(&mut runtime, slot);
        patch.state = "busy".into();
        patch.media_id.clone_from(&ask.media_id);
        patch.region = Some(ask.region.clone());
        return Ok(());
    }
    let mode = lock(&state.settings)?.preview_patch.clone();
    if mode == "off" {
        return Err(AppError::Validation(
            "The preview patch is turned off in Settings.".into(),
        ));
    }

    let mut input = ask.input;
    if let Some(replace) = ask.replace.as_ref().filter(|replace| replace.slot == slot) {
        apply_replace(&mut input, replace);
    }
    let PatchPlan {
        stages,
        output_scale,
        missing,
        device,
        precision,
    } = patch_plan(state, input.clone())?;
    let first_missing = stages.iter().position(|stage| {
        stage
            .model_id
            .as_ref()
            .is_some_and(|id| missing.contains(id))
    });
    let mut runnable = first_missing.unwrap_or(stages.len());
    if let Some(limit) = ask.stage_limit {
        runnable = runnable.min(limit);
    }
    let context = format!(
        "{}|{}|{:?}|{}|{}|{}|{}|{}|{}",
        media.path,
        ask.media_id,
        (
            ask.region.x,
            ask.region.y,
            ask.region.width,
            ask.region.height
        ),
        device,
        precision,
        input.tile_size,
        input.halo,
        input.neural_engine,
        input.safe_memory,
    ) + &format!("|{output_scale}");
    let signatures: Vec<String> = stages.iter().map(step_signature).collect();
    let bases: Vec<String> = stages.iter().map(step_base).collect();
    let template: Vec<PatchStep> = stages
        .iter()
        .enumerate()
        .map(|(index, stage)| PatchStep {
            kind: stage.kind.clone().unwrap_or_default(),
            fix: stage.fix.clone().unwrap_or_default(),
            model_id: stage.model_id.clone().unwrap_or_default(),
            state: if first_missing == Some(index) {
                "missing"
            } else {
                "waiting"
            }
            .into(),
            ..Default::default()
        })
        .collect();

    // The steps the slot already shows for this file and position cost nothing again.
    let (shown, mixed) = {
        let runtime = lock(&state.runtime)?;
        let book = lock(&state.patch)?;
        let slot_book = if slot == "a" { &book.a } else { &book.b };
        let old = runtime.patches.as_ref().map(
            |patches| {
                if slot == "a" {
                    &patches.a
                } else {
                    &patches.b
                }
            },
        );
        match old {
            Some(old) if slot_book.context == context => {
                let is_done = |index: usize| {
                    old.steps
                        .get(index)
                        .is_some_and(|step| step.state == "done")
                };
                let shown = (0..runnable)
                    .take_while(|&index| {
                        slot_book.signatures.get(index) == Some(&signatures[index])
                            && is_done(index)
                    })
                    .count();
                // Fixes that differ from the shown ones only in strength are mixed again
                // from the engine's cache: no model runs for them.
                let mixed = (shown..runnable)
                    .take_while(|&index| {
                        slot_book.signatures.get(index) != Some(&signatures[index])
                            && slot_book.bases.get(index) == Some(&bases[index])
                            && is_done(index)
                    })
                    .count();
                (shown, mixed)
            }
            _ => (0, 0),
        }
    };
    let estimate = {
        let database = lock(&state.database)?;
        let mut seconds = 0.0;
        for step in &template[shown + mixed..runnable] {
            seconds += database
                .patch_seconds(&step.model_id, &device)?
                .unwrap_or(0.0);
        }
        seconds
    };
    let mut wait = match runs_by_itself(&mode, ask.force, estimate) {
        None => {
            return Err(AppError::Validation(
                "The preview patch is turned off in Settings.".into(),
            ))
        }
        Some(runs) => !runs,
    };
    // Under Automatic, steps that are too slow to run by themselves do not hold back the
    // fix whose strength changed: the request is cut after the last step that costs nothing
    // and the rest waits behind `Update preview`.
    let mut rest_waits = false;
    let mut sent = runnable;
    if wait && mixed > 0 && mode != "manual" {
        wait = false;
        rest_waits = true;
        sent = shown + mixed;
    }
    // Even a wait replaces the run before it: its answer would be for another plan.
    let patch_id = format!("patch-{}", Uuid::new_v4());
    let message = json!({
        "type": "patch_request",
        "data": {
            "patch_id": patch_id,
            "slot": slot,
            "image_path": media.path,
            "frame_index": if media.kind == "video" { 0 } else { -1 },
            "region": {
                "x": ask.region.x,
                "y": ask.region.y,
                "width": ask.region.width,
                "height": ask.region.height,
            },
            "padding": PADDING,
            "stages": &stages[..sent],
            "output_scale": output_scale,
            "device": device,
            "precision": precision,
            "tile_size": input.tile_size,
            "halo": input.halo,
            "neural_engine": input.neural_engine,
            "safe_memory": input.safe_memory,
        }
    });
    let run = !wait && sent > 0;

    let stale = {
        let mut runtime = lock(&state.runtime)?;
        let mut book = lock(&state.patch)?;
        let slot_book = book.slot(slot);
        stop_run(state, slot_book);
        let patch = slot_state(&mut runtime, slot);
        let mut steps = template;
        for (index, step) in steps.iter_mut().enumerate().take(shown) {
            *step = patch.steps[index].clone();
        }
        let kept: Vec<&str> = steps.iter().map(|step| step.url.as_str()).collect();
        let stale: Vec<String> = patch
            .steps
            .iter()
            .filter(|step| !step.url.is_empty() && !kept.contains(&step.url.as_str()))
            .map(|step| step.url.clone())
            .collect();
        patch.media_id.clone_from(&ask.media_id);
        patch.region = Some(ask.region.clone());
        patch.error.clear();
        patch.steps = steps;
        patch.estimate_seconds = if wait || rest_waits { estimate } else { 0.0 };
        patch.state = if wait {
            "stale"
        } else if run {
            "running"
        } else {
            "ready"
        }
        .into();
        slot_book.context = context;
        slot_book.signatures = signatures;
        slot_book.bases = bases;
        slot_book.rest_waits = rest_waits;
        slot_book.device = device;
        slot_book.patch_id = if run { patch_id } else { String::new() };
        stale
    };
    cleanup_stale_previews(state, stale)?;
    if run {
        if let Err(error) = send(state, &message) {
            let mut runtime = lock(&state.runtime)?;
            let patch = slot_state(&mut runtime, slot);
            patch.state = "failed".into();
            patch.error = error.to_string();
            return Err(error);
        }
        lock(&state.patch)?.slot(slot).running = true;
    }
    Ok(())
}

/// The engine's `patch_*` messages, applied to the slot whose run they name. A message for
/// a run that was replaced or cancelled is dropped.
pub(crate) fn apply_message(
    state: &Arc<AppState>,
    envelope: &crate::types::WorkerEnvelope,
) -> AppResult<()> {
    let data = &envelope.data;
    let patch_id = data["patch_id"].as_str().unwrap_or_default();
    let kind = envelope.message_type.as_str();
    if kind == "patch_stage" && data["state"] == "done" {
        return apply_done(state, patch_id, data);
    }
    let mut runtime = lock(&state.runtime)?;
    let mut book = lock(&state.patch)?;
    let Some(slot) = book.slot_of(patch_id) else {
        return Ok(());
    };
    let patch = slot_state(&mut runtime, slot);
    match kind {
        "patch_started" => patch.state = "running".into(),
        "patch_stage" => {
            let index = data["stage_index"].as_u64().unwrap_or(u64::MAX);
            let Some(step) = usize::try_from(index)
                .ok()
                .and_then(|index| patch.steps.get_mut(index))
            else {
                return Ok(());
            };
            match data["state"].as_str() {
                Some("running") if step.state == "waiting" => step.state = "running".into(),
                Some("skipped") => {
                    step.state = "skipped".into();
                    step.note = data["note"].as_str().unwrap_or_default().to_owned();
                }
                _ => {}
            }
        }
        "patch_completed" => {
            book.slot(slot).running = false;
            if patch.state == "running" {
                patch.state = if book.slot(slot).rest_waits {
                    "stale"
                } else {
                    "ready"
                }
                .into();
            }
        }
        "patch_failed" => {
            book.slot(slot).running = false;
            patch.state = "failed".into();
            patch.error = data["error_message"]
                .as_str()
                .unwrap_or_default()
                .to_owned();
            for step in &mut patch.steps {
                if step.state == "running" {
                    step.state = "waiting".into();
                }
            }
        }
        "patch_cancelled" => {
            book.slot(slot).running = false;
            if patch.state == "running" {
                patch.state = "idle".into();
                for step in &mut patch.steps {
                    if step.state == "running" {
                        step.state = "waiting".into();
                    }
                }
            }
        }
        _ => {}
    }
    Ok(())
}

fn apply_done(state: &Arc<AppState>, patch_id: &str, data: &Value) -> AppResult<()> {
    let index =
        usize::try_from(data["stage_index"].as_u64().unwrap_or(u64::MAX)).unwrap_or(usize::MAX);
    let cached = data["cached"].as_bool().unwrap_or(false);
    let elapsed = data["elapsed_seconds"].as_f64().unwrap_or(0.0);
    let known = {
        let runtime = lock(&state.runtime)?;
        let mut book = lock(&state.patch)?;
        book.slot_of(patch_id).map(|slot| {
            let model = runtime
                .patches
                .as_ref()
                .map(|patches| if slot == "a" { &patches.a } else { &patches.b })
                .and_then(|patch| patch.steps.get(index))
                .map(|step| step.model_id.clone());
            (slot, model, book.slot(slot).device.clone())
        })
    };
    let Some((_, Some(model_id), device)) = known else {
        return Ok(());
    };
    if !cached {
        lock(&state.database)?.record_patch_seconds(&model_id, &device, elapsed)?;
    }
    // Written outside the locks: the window reads snapshots meanwhile.
    let url = crate::preview_cache::save_png_base64(
        &state.paths.preview_root,
        data["png_base64"].as_str().unwrap_or_default(),
    )
    .unwrap_or_default();
    let stale = {
        let mut runtime = lock(&state.runtime)?;
        let mut book = lock(&state.patch)?;
        match book.slot_of(patch_id) {
            Some(slot) => {
                let step = slot_state(&mut runtime, slot).steps.get_mut(index);
                match step {
                    Some(step) => {
                        step.state = "done".into();
                        step.width =
                            u32::try_from(data["width"].as_u64().unwrap_or(0)).unwrap_or(0);
                        step.height =
                            u32::try_from(data["height"].as_u64().unwrap_or(0)).unwrap_or(0);
                        step.seconds = elapsed;
                        step.note.clear();
                        if url.is_empty() || url == step.url {
                            String::new()
                        } else {
                            std::mem::replace(&mut step.url, url)
                        }
                    }
                    None => url,
                }
            }
            // The run was replaced while the picture was written.
            None => url,
        }
    };
    cleanup_stale_previews(state, vec![stale])
}

/// The address of a finished step's picture, for the message the window gets.
pub(crate) fn step_url(state: &AppState, patch_id: &str, index: u64) -> Option<String> {
    let runtime = state.runtime.lock().ok()?;
    let mut book = state.patch.lock().ok()?;
    let slot = book.slot_of(patch_id)?;
    let patches = runtime.patches.as_ref()?;
    let patch = if slot == "a" { &patches.a } else { &patches.b };
    patch
        .steps
        .get(usize::try_from(index).ok()?)
        .map(|step| step.url.clone())
        .filter(|url| !url.is_empty())
}

#[cfg(test)]
mod tests;
