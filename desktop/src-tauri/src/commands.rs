use std::{
    collections::HashSet,
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::Arc,
};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State};
use uuid::Uuid;

use crate::{
    catalog,
    database::NewJob,
    downloads,
    error::{AppError, AppResult},
    integrations,
    launch::LaunchIntent,
    native_menu,
    output_names::ReservedOutputs,
    results::{MediaResults, ResultUi},
    state::{emit_state_changed, lock, AppState},
    types::{
        AppSnapshot, CatalogModel, ComparisonPair, ComparisonSlot, Recipe, StartBatchInput,
        StartBenchmarkInput, UiSettings, VideoComparisonSources,
    },
    worker,
};

const IMAGE_EXTENSIONS: &[&str] = &[
    "jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp", "gif", "avif", "jp2", "j2k",
    // Camera RAW, developed by LibRaw.
    "dng", "cr2", "cr3", "nef", "nrw", "arw", "srf", "sr2", "raf", "orf", "rw2", "pef", "srw",
    "3fr", "iiq", "erf", "rwl", "mrw", "mef", "mos", "dcr", "kdc",
];
/// HEIC/HEIF (HEVC inside) and JPEG XL are decoded by macOS, which licenses the
/// codec; LocalSR ships no decoder of its own for them.
#[cfg(target_os = "macos")]
const SYSTEM_IMAGE_EXTENSIONS: &[&str] = &["heic", "heif", "hif", "jxl"];
#[cfg(not(target_os = "macos"))]
const SYSTEM_IMAGE_EXTENSIONS: &[&str] = &[];
const VIDEO_EXTENSIONS: &[&str] = &[
    "mp4", "mov", "m4v", "mkv", "webm", "avi", "mpg", "mpeg", "mpe", "vob", "ts", "mts", "m2ts",
    "wmv", "asf", "flv", "f4v", "3gp", "3g2", "ogv", "divx",
];
const MAX_MEDIA_ITEMS: usize = 10_000;

/// Open an editing bundle's license or source page in the browser.
#[tauri::command]
pub fn open_edit_link(
    state: State<'_, Arc<AppState>>,
    model_id: String,
    link: String,
) -> AppResult<()> {
    let model = crate::editing::catalog(&state.paths)?
        .into_iter()
        .find(|m| m.model_id == model_id)
        .ok_or_else(|| AppError::Validation("Unknown editing model.".into()))?;
    let url = match link.as_str() {
        "license" => model.license_url,
        "source" => model.source_url,
        _ => return Err(AppError::Validation("Unknown link.".into())),
    };
    open::that(url).map_err(|error| AppError::Validation(error.to_string()))?;
    Ok(())
}

#[tauri::command]
pub fn start_edit(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    input: crate::editing::StartEditInput,
) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    ensure_queue_idle(&state)?;
    if lock(&state.runtime)?.worker != "ready" {
        return Err(AppError::Validation(
            "Wait for the editing worker to become ready.".into(),
        ));
    }
    if !lock(&state.engine)?
        .as_ref()
        .is_some_and(|engine| engine.features.iter().any(|f| f == "image_edit"))
    {
        return Err(AppError::Validation(
            "This worker does not support editing. Update LocalSR.".into(),
        ));
    }
    let model = crate::editing::catalog(&state.paths)?
        .into_iter()
        .find(|model| model.model_id == input.model_id)
        .ok_or_else(|| AppError::Validation("Unknown editing model.".into()))?;
    crate::editing::validate(&input, &model).map_err(AppError::Validation)?;
    if !lock(&state.capabilities)?
        .devices
        .iter()
        .any(|device| device.id == input.device)
    {
        return Err(AppError::Validation(
            "The selected editing GPU is unavailable.".into(),
        ));
    }
    let runtime = edit_runtime(&state, &input.runtime_path)?;
    let media = lock(&state.database)?
        .get_media(&input.media_id)?
        .ok_or_else(|| AppError::Validation("Choose an image to edit.".into()))?;
    if media.kind != "image" || media.probe_status != "ready" || !Path::new(&media.path).is_file() {
        return Err(AppError::Validation(
            "Wait for a valid image preview before editing.".into(),
        ));
    }
    let directory = prepare_output_directory(&state, &input.output_directory)?;
    let id = Uuid::new_v4().to_string();
    let stem = Path::new(&media.path)
        .file_stem()
        .unwrap_or_default()
        .to_string_lossy();
    let output = directory.join(format!("{stem}_localsr_edit_{}.png", &id[..8]));
    let request = json!({"type": "edit_job_request", "data": {
        "job_id": id, "image_path": media.path, "output_path": output.to_string_lossy(),
        "model_id": model.model_id,
        "bundle_dir": state.paths.model_root.join(&model.family).to_string_lossy(),
        "runtime_path": runtime.to_string_lossy(), "prompt": input.prompt,
        "device": input.device, "max_dimension": input.max_dimension,
        "steps": input.steps, "seed": input.seed, "accepted_terms": input.accepted_terms,
    }});
    lock(&state.database)?.insert_job(&id, &media.id, &serde_json::to_string(&request)?)?;
    worker::dispatch_next_locked(&state, &app)?;
    emit_state_changed(&app);
    Ok(())
}

/// The editing program to run: the one the host found at start-up. The web view
/// may repeat that path but cannot name another.
fn edit_runtime(state: &AppState, requested: &str) -> AppResult<PathBuf> {
    let found = lock(&state.edit_runtime)?.clone();
    if found.is_empty() {
        return Err(AppError::Validation(
            "Choose the native editing runtime.".into(),
        ));
    }
    let found = fs::canonicalize(found)?;
    if !found.is_file() {
        return Err(AppError::Validation(
            "Choose the native editing runtime.".into(),
        ));
    }
    if !requested.is_empty() && fs::canonicalize(requested).ok().as_ref() != Some(&found) {
        return Err(AppError::Validation(
            "Only the editing runtime LocalSR found itself can be used.".into(),
        ));
    }
    Ok(found)
}

#[tauri::command]
pub fn bootstrap(state: State<'_, Arc<AppState>>) -> AppResult<AppSnapshot> {
    state.snapshot()
}

#[tauri::command]
pub fn get_snapshot(state: State<'_, Arc<AppState>>) -> AppResult<AppSnapshot> {
    state.snapshot()
}

#[tauri::command]
pub fn take_launch_intents(state: State<'_, Arc<AppState>>) -> AppResult<Vec<LaunchIntent>> {
    Ok(std::mem::take(&mut *lock(&state.launch_intents)?))
}

#[tauri::command]
pub fn scan_media_folder(path: String) -> AppResult<Vec<String>> {
    let directory = canonical_directory(&path)?;
    let mut paths = Vec::new();
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let path = entry.path();
        if path.is_file() && media_kind(&path).is_some() {
            paths.push(path.to_string_lossy().into_owned());
            if paths.len() >= MAX_MEDIA_ITEMS {
                break;
            }
        }
    }
    paths.sort_by_key(|path| path.to_lowercase());
    Ok(paths)
}

#[tauri::command]
pub fn add_media(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    paths: Vec<String>,
    replace: bool,
) -> AppResult<()> {
    crate::updates::ensure_not_installing(&state)?;
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    if paths.len() > MAX_MEDIA_ITEMS {
        return Err(AppError::Validation(
            "too many media files were selected".into(),
        ));
    }
    if lock(&state.database)?.has_inflight_jobs()? {
        return Err(AppError::Validation(
            "wait for the current queue to finish or cancel it before changing media".into(),
        ));
    }
    let normalized = normalize_media_paths(paths)?;
    if replace {
        lock(&state.database)?.clear_media()?;
        clear_completed_result(&state)?;
    }

    for (path, kind) in normalized {
        let encoded = path.to_string_lossy().into_owned();
        let name = path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("Media")
            .to_owned();
        if lock(&state.database)?.insert_media(
            &Uuid::new_v4().to_string(),
            &encoded,
            &name,
            kind,
        )? {
            let _ = worker::send(&state, &worker::media_probe_request(&state, &encoded));
        }
    }
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn select_media(state: State<'_, Arc<AppState>>, app: AppHandle, id: String) -> AppResult<()> {
    native_menu::set_comparison_labels(&app, false);
    crate::updates::ensure_not_installing(&state)?;
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    lock(&state.database)?.select_media(&id)?;
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn remove_media(state: State<'_, Arc<AppState>>, app: AppHandle, id: String) -> AppResult<()> {
    crate::updates::ensure_not_installing(&state)?;
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    ensure_queue_idle(&state)?;
    lock(&state.database)?.remove_media(&id)?;
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn clear_media(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    crate::updates::ensure_not_installing(&state)?;
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    ensure_queue_idle(&state)?;
    lock(&state.database)?.clear_media()?;
    clear_completed_result(&state)?;
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn save_settings(state: State<'_, Arc<AppState>>, settings: UiSettings) -> AppResult<()> {
    crate::updates::ensure_not_installing(&state)?;
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    if !lock(&state.runtime)?.active_job_id.is_empty() {
        return Err(AppError::Validation(
            "Finish or cancel the current queue before changing settings.".into(),
        ));
    }
    validate_settings(&settings)?;
    state.programs.check(&settings.external_ffmpeg_path)?;
    let mut settings = settings;
    {
        // Read and replace under one guard, so a switch saved in between is kept.
        let mut current = lock(&state.settings)?;
        keep_update_switches(&current, &mut settings);
        *current = settings;
    }
    state.persist_settings()
}

/// The update switches change only through set_update_switch. A full settings save
/// carries the window's copy, which an older refresh may have left behind; it must
/// never turn an opt-out back on.
fn keep_update_switches(current: &UiSettings, incoming: &mut UiSettings) {
    incoming.check_updates_at_launch = current.check_updates_at_launch;
    incoming.anonymous_update_count = current.anonymous_update_count;
}

/// Set one update switch. It may change while jobs run: it does not touch them.
#[tauri::command]
pub fn set_update_switch(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    name: String,
    value: bool,
) -> AppResult<()> {
    let result = apply_update_switch(&state, &name, |_| value);
    refresh_update_switches(&app);
    result
}

/// The menu's own click: flip the saved value, whatever the window shows.
pub fn toggle_update_switch(state: &AppState, app: &AppHandle, name: &str) {
    if let Err(error) = apply_update_switch(state, name, |current| !current) {
        eprintln!("LocalSR: could not save {name}: {error}");
    }
    refresh_update_switches(app);
}

/// Change one switch and write it, as one step: other writers wait, so two quick clicks
/// are two flips and no older copy of the settings lands after this one.
fn apply_update_switch(
    state: &AppState,
    name: &str,
    change: impl FnOnce(bool) -> bool,
) -> AppResult<()> {
    let _io = lock(&state.settings_io)?;
    let previous = {
        let mut settings = lock(&state.settings)?;
        let field = match name {
            "check_updates_at_launch" => &mut settings.check_updates_at_launch,
            "anonymous_update_count" => &mut settings.anonymous_update_count,
            _ => {
                return Err(AppError::Validation(format!(
                    "unknown update switch: {name}"
                )))
            }
        };
        let previous = *field;
        *field = change(previous);
        previous
    };
    if let Err(error) = state.write_settings() {
        // Shown and used must match what is on disk.
        if let Ok(mut settings) = lock(&state.settings) {
            match name {
                "check_updates_at_launch" => settings.check_updates_at_launch = previous,
                _ => settings.anonymous_update_count = previous,
            }
        }
        return Err(error);
    }
    Ok(())
}

/// The menu sets its own check mark when clicked; it and the window show what is saved.
fn refresh_update_switches(app: &AppHandle) {
    native_menu::reconcile(app);
    emit_state_changed(app);
}

#[tauri::command]
pub fn save_recipe(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    mut recipe: Recipe,
) -> AppResult<()> {
    crate::updates::ensure_not_installing(&state)?;
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    recipe.name = recipe.name.trim().chars().take(60).collect();
    if recipe.name.is_empty() || !matches!(recipe.task.as_str(), "upscale" | "denoise" | "video") {
        return Err(AppError::Validation(
            "recipe name or task is invalid".into(),
        ));
    }
    if (!recipe.output_format.is_empty() && !output_format_is_valid(&recipe.output_format))
        || (!recipe.video_container.is_empty()
            && !video_container_is_valid(&recipe.video_container))
        || (!recipe.video_codec.is_empty() && !video_codec_is_valid(&recipe.video_codec))
        || recipe
            .jpeg_quality
            .is_some_and(|value| !(1..=100).contains(&value))
        || recipe.video_crf.is_some_and(|value| value > 51)
        || recipe
            .deflicker_window
            .is_some_and(|value| !(1..=9).contains(&value))
        || (!recipe.video_interpolation.is_empty()
            && !interpolation_choice_is_valid(
                &recipe.video_interpolation,
                if recipe.video_interpolation_order.is_empty() {
                    "after_upscale"
                } else {
                    &recipe.video_interpolation_order
                },
            ))
    {
        return Err(AppError::Validation(
            "recipe contains unsupported output settings".into(),
        ));
    }
    validate_recipe_stages(&recipe)?;
    let mut recipes = lock(&state.recipes)?;
    if let Some(existing) = recipes.iter_mut().find(|existing| existing.id == recipe.id) {
        *existing = recipe;
    } else {
        if recipes.len() >= 100 {
            return Err(AppError::Validation("recipe limit reached".into()));
        }
        recipes.push(recipe);
    }
    drop(recipes);
    state.persist_settings()?;
    let switches = native_menu::switches(&*lock(&state.settings)?);
    native_menu::rebuild(&app, &lock(&state.recipes)?, switches).map_err(|error| {
        AppError::Config(format!(
            "could not refresh the native presets menu: {error}"
        ))
    })?;
    emit_state_changed(&app);
    Ok(())
}

fn validate_recipe_stages(recipe: &Recipe) -> AppResult<()> {
    if recipe.stages.is_empty() {
        // The frontend reconstructs recipes without stages from their top-level fields.
        return Ok(());
    }
    if recipe.stages.len() > 5
        || recipe.stages.iter().any(|stage| {
            stage.model_id.trim().is_empty()
                || !matches!(
                    stage.kind.as_str(),
                    "deblock" | "restore" | "upscale" | "face_restore" | "video"
                )
                || stage
                    .fidelity
                    .is_some_and(|value| !(0.0..=1.0).contains(&value))
        })
    {
        return Err(AppError::Validation(
            "recipe contains an invalid or unbounded stage list".into(),
        ));
    }

    // Recipes saved before several fixes name no fix; newer ones name it on each restoration.
    let mut fixes = HashSet::new();
    if recipe.stages.iter().any(|stage| {
        stage.fix.as_deref().is_some_and(|fix| {
            !matches!(fix, "blur" | "jpeg" | "noise")
                || !matches!(stage.kind.as_str(), "deblock" | "restore")
                || !fixes.insert(fix)
        })
    }) {
        return Err(AppError::Validation(
            "recipe fix kinds must be known, unique and on restoration stages".into(),
        ));
    }
    let kinds: Vec<&str> = recipe
        .stages
        .iter()
        .map(|stage| stage.kind.as_str())
        .collect();
    let valid = match recipe.task.as_str() {
        "upscale" => {
            let upscale = kinds.iter().position(|kind| *kind == "upscale");
            upscale.is_some_and(|index| {
                kinds.iter().filter(|kind| **kind == "upscale").count() == 1
                    && index <= 3
                    && kinds[..index]
                        .iter()
                        .all(|kind| matches!(*kind, "deblock" | "restore"))
                    && kinds[index + 1..]
                        .iter()
                        .all(|kind| *kind == "face_restore")
                    && kinds[index + 1..].len() <= 1
            })
        }
        // Up to three fixes, then the Restore model itself, which is always a restore stage.
        "denoise" => kinds.split_last().is_some_and(|(last, before)| {
            *last == "restore"
                && before.len() <= 3
                && before
                    .iter()
                    .all(|kind| matches!(*kind, "restore" | "deblock"))
        }),
        "video" => {
            matches!(kinds.as_slice(), ["video"] | ["video", "face_restore"])
        }
        _ => false,
    };
    if !valid {
        return Err(AppError::Validation(
            "recipe stages do not match the selected task or stage order".into(),
        ));
    }
    Ok(())
}

#[tauri::command]
pub fn delete_recipe(state: State<'_, Arc<AppState>>, app: AppHandle, id: String) -> AppResult<()> {
    crate::updates::ensure_not_installing(&state)?;
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    lock(&state.recipes)?.retain(|recipe| recipe.id != id);
    state.persist_settings()?;
    let switches = native_menu::switches(&*lock(&state.settings)?);
    native_menu::rebuild(&app, &lock(&state.recipes)?, switches).map_err(|error| {
        AppError::Config(format!(
            "could not refresh the native presets menu: {error}"
        ))
    })?;
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn start_jobs(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    input: StartBatchInput,
) -> AppResult<()> {
    if state
        .updates
        .installing
        .load(std::sync::atomic::Ordering::SeqCst)
    {
        return Err(AppError::Validation("An update is being installed".into()));
    }
    let _scheduler = lock(&state.scheduler)?;
    if state
        .updates
        .installing
        .load(std::sync::atomic::Ordering::SeqCst)
    {
        return Err(AppError::Validation("An update is being installed".into()));
    }
    let mut input = input;
    input.device = resolve_device(&state, &input.device)?;
    validate_start(&input, &state)?;
    validate_fix_engine(&input, lock(&state.engine)?.as_ref())?;
    if input.task == "video"
        && input.video_interpolation != "off"
        && !lock(&state.engine)?
            .as_ref()
            .is_some_and(|engine| engine.features.iter().any(|f| f == "video_interpolation"))
    {
        return Err(AppError::Validation(
            "This worker does not support frame interpolation. Update LocalSR or turn interpolation off.".into(),
        ));
    }

    // Do not hash the complete model library on the UI command path. A
    // SeedVR2 installation alone can exceed 6 GB; refreshing every catalog
    // entry here made a click on Start block the macOS event loop long enough
    // to show the spinning wait cursor. Catalog downloads are SHA-256 checked
    // before installation, catalog discovery is size-bounded, and
    // the isolated Python worker independently verifies risky pickle-based
    // image checkpoints immediately before deserializing the selected model.
    // A deliberate catalog refresh belongs on a background maintenance path,
    // never in this latency-sensitive command.

    let output_directory = prepare_output_directory(&state, &input.output_directory)?;
    let selection = resolve_model_selection(&state, &input)?;
    let media = {
        let database = lock(&state.database)?;
        let inflight_media: HashSet<String> = database
            .list_jobs()?
            .into_iter()
            .filter(|job| {
                matches!(
                    job.status.as_str(),
                    "queued" | "starting" | "running" | "cancelling"
                )
            })
            .map(|job| job.media_id)
            .collect();
        let mut selected = Vec::new();
        for id in &input.media_ids {
            if inflight_media.contains(id) {
                return Err(AppError::Validation(
                    "one or more selected items are already running or queued".into(),
                ));
            }
            selected.push(database.get_media(id)?.ok_or_else(|| {
                AppError::Validation("a selected media item no longer exists".into())
            })?);
        }
        selected
    };
    if media.is_empty() {
        return Err(AppError::Validation(
            "choose at least one media item".into(),
        ));
    }
    if !input.batch_mode && media.len() != 1 {
        return Err(AppError::Validation(
            "single mode accepts exactly one media item".into(),
        ));
    }

    for item in &media {
        if item.probe_status == "failed" {
            return Err(AppError::Validation(format!(
                "{} could not be opened: {}",
                item.name, item.error
            )));
        }
        if item.probe_status != "ready" {
            return Err(AppError::Validation(format!(
                "the preview for {} is still being prepared",
                item.name
            )));
        }
        if !Path::new(&item.path).is_file() {
            return Err(AppError::Validation(format!(
                "{} is no longer available",
                item.name
            )));
        }
        let wants_video = input.task == "video";
        if wants_video != (item.kind == "video") {
            return Err(AppError::Validation(
                "the chosen task does not match one or more media items".into(),
            ));
        }
    }
    let ui_json = ui_json(input.ui.as_ref())?;
    let media_ids: Vec<&str> = media.iter().map(|item| item.id.as_str()).collect();
    lock(&state.database)?.admit_jobs(&media_ids, |reserved| {
        media
            .iter()
            .map(|item| -> AppResult<NewJob> {
                let job_id = Uuid::new_v4().to_string();
                let output = unique_output_path(&output_directory, &item.path, &input, reserved);
                let message = build_job_message(
                    &job_id,
                    item,
                    &output,
                    &state.paths.work_root,
                    &input,
                    &selection,
                )?;
                Ok(NewJob {
                    id: job_id,
                    media_id: item.id.clone(),
                    request_json: serde_json::to_string(&message)?,
                    ui_json: ui_json.clone(),
                })
            })
            .collect()
    })?;
    worker::dispatch_next_locked(&state, &app)?;
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn cancel_jobs(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    let mut cancellation = lock(&state.worker.cancellation)?;
    lock(&state.database)?.cancel_queued_jobs()?;
    let active = lock(&state.runtime)?.active_job_id.clone();
    if !active.is_empty() {
        cancellation.request(&active, std::time::Instant::now());
        // Photos of a batch that never started were cancelled with the queue above.
        for id in &cancellation.job_ids {
            lock(&state.database)?.mark_cancelling(id)?;
        }
        {
            let mut runtime = lock(&state.runtime)?;
            runtime.status_title = "Cancelling".into();
            runtime.status_detail =
                "Stopping at the next model step. A stalled engine will restart automatically."
                    .into();
        }
        worker::send(
            &state,
            &json!({"type": "cancel_request", "data": {"job_id": active}}),
        )?;
    }
    emit_state_changed(&app);
    Ok(())
}

/// "Cancel runs for this image": its queued jobs are cancelled and its active
/// job is signalled. Other media keep their queued work, and completed results
/// stay. The queue-wide action remains `cancel_jobs` ("Cancel queue").
#[tauri::command]
pub fn cancel_jobs_for_media(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    media_id: String,
) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    let mut cancellation = lock(&state.worker.cancellation)?;
    let active = lock(&state.database)?.cancel_jobs_for_media(&media_id)?;
    if let Some(active) = active {
        cancellation.request(&active, std::time::Instant::now());
        {
            let mut runtime = lock(&state.runtime)?;
            if runtime.active_job_id == active {
                runtime.status_title = "Cancelling".into();
                runtime.status_detail =
                    "Stopping this image's run at the next model step. Other images stay queued."
                        .into();
            }
        }
        worker::send(
            &state,
            &json!({"type": "cancel_request", "data": {"job_id": active}}),
        )?;
    }
    drop(cancellation);
    emit_state_changed(&app);
    Ok(())
}

/// "Try others…": the same image once per model, as ordinary jobs admitted
/// together. Returns their ids in queue order.
#[tauri::command]
pub fn start_model_trials(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    media_id: String,
    model_ids: Vec<String>,
    base_settings: StartBatchInput,
) -> AppResult<Vec<String>> {
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    let ids = admit_model_trials(&state, &media_id, &model_ids, &base_settings)?;
    worker::dispatch_next_locked(&state, &app)?;
    emit_state_changed(&app);
    Ok(ids)
}

struct TrialCandidate {
    input: StartBatchInput,
    selection: ModelSelection,
}

/// Problems a 1× checkpoint fixes; mirrors `fixesOf` in desktop/src/lib/state.ts.
fn model_fixes(model: &CatalogModel) -> Vec<String> {
    if !model.fixes.is_empty() {
        return model.fixes.clone();
    }
    let has = |purpose: &str| model.purposes.iter().any(|item| item == purpose);
    if model.native_scale != 1 || has("face") {
        return Vec::new();
    }
    let mut found = Vec::new();
    if has("denoise") {
        found.push("noise".to_owned());
    }
    if has("deblur") {
        found.push("blur".to_owned());
    }
    if has("restoration") && found.is_empty() {
        found.push("jpeg".to_owned());
    }
    found
}

/// Validate every candidate against the settings the plan card shows. A model
/// that cannot keep the scale, the fix recipe or Faces is refused; nothing is
/// adjusted to make it fit, except that a model gets at least the tile overlap
/// it needs (as choosing it in the library does).
fn trial_candidates(
    state: &AppState,
    media_id: &str,
    model_ids: &[String],
    base: &StartBatchInput,
) -> AppResult<Vec<TrialCandidate>> {
    if model_ids.is_empty()
        || model_ids.len() > 4
        || model_ids.iter().collect::<HashSet<_>>().len() != model_ids.len()
    {
        return Err(AppError::Validation(
            "Pick 2 to 4 different installed models.".into(),
        ));
    }
    if !matches!(base.task.as_str(), "upscale" | "denoise")
        || base.batch_mode
        || base.media_ids != [media_id]
    {
        return Err(AppError::Validation(
            "Try others needs one selected image in Upscale or Restore.".into(),
        ));
    }
    let mut resolved = base.clone();
    resolved.device = resolve_device(state, &base.device)?;
    let base = &resolved;
    validate_start(base, state)?;
    // The Restore model is the fix: each candidate must fix the active problem.
    // With several fixes it is the last one: the fixes before it keep their models.
    let active_fix = base.fix_stages.last().map(|stage| stage.fix.clone());
    let mut candidates = Vec::with_capacity(model_ids.len());
    for model_id in model_ids {
        let (halo, fixes, name) = {
            let catalog = lock(&state.catalog)?;
            let model = catalog
                .models
                .iter()
                .find(|model| model.model_id == *model_id)
                .ok_or_else(|| {
                    AppError::Validation("Try others uses installed catalog models.".into())
                })?;
            let name = crate::results::catalog_label(&catalog, model_id)
                .unwrap_or_else(|| model.name.clone());
            if !model.installed {
                return Err(AppError::Validation(format!("{name} is not installed.")));
            }
            (model.recommended_halo, model_fixes(model), name)
        };
        if base.task == "denoise" && active_fix.as_ref().is_some_and(|fix| !fixes.contains(fix)) {
            return Err(AppError::Validation(format!(
                "{name} does not fix the selected problem."
            )));
        }
        let mut input = base.clone();
        input.model_id = model_id.clone();
        if base.task == "denoise" {
            if let Some(last) = input.fix_stages.last_mut() {
                last.model_id = model_id.clone();
            }
        }
        input.name_by_model = true;
        input.halo = base.halo.max(halo);
        input.allow_unsafe_pickle_model = false;
        if *model_id != base.model_id {
            // Another model under the same settings is a custom choice, not
            // the Quick, Best or recipe pick that was showing.
            if let Some(ui) = &mut input.ui {
                ui.quality = "custom".into();
                ui.recipe_name.clear();
            }
        }
        // Scale, the fix stage and the Faces companion are checked here, as
        // for any job; the candidate is refused rather than changed.
        let selection = resolve_model_selection(state, &input)
            .map_err(|error| AppError::Validation(format!("{name}: {error}")))?;
        if let ModelSelection::Image {
            path,
            preprocess,
            face_path,
            ..
        } = &selection
        {
            let missing = std::iter::once(path.as_str())
                .chain(preprocess.iter().map(|stage| stage.path.as_str()))
                .chain(face_path.iter().map(String::as_str))
                .any(|path| !Path::new(path).is_file());
            if missing {
                return Err(AppError::Validation(format!(
                    "{name}: a checkpoint is no longer installed."
                )));
            }
        }
        candidates.push(TrialCandidate { input, selection });
    }
    Ok(candidates)
}

/// Admit 2-4 candidates for one idle image under the scheduler lock (held by
/// the caller): all of them, in the order given, or none.
fn admit_model_trials(
    state: &AppState,
    media_id: &str,
    model_ids: &[String],
    base: &StartBatchInput,
) -> AppResult<Vec<String>> {
    if model_ids.len() < 2 {
        return Err(AppError::Validation(
            "Pick 2 to 4 different installed models.".into(),
        ));
    }
    let candidates = trial_candidates(state, media_id, model_ids, base)?;
    let directory = prepare_output_directory(state, &base.output_directory)?;
    let database = lock(&state.database)?;
    let media = database
        .get_media(media_id)?
        .ok_or_else(|| AppError::Validation("The selected image is no longer queued.".into()))?;
    if media.kind != "image" || media.probe_status != "ready" || !Path::new(&media.path).is_file() {
        return Err(AppError::Validation(
            "Choose an available image with a ready preview.".into(),
        ));
    }
    let mut ids = Vec::with_capacity(candidates.len());
    database.admit_jobs(&[media_id], |reserved| {
        candidates
            .iter()
            .map(|candidate| -> AppResult<NewJob> {
                let id = Uuid::new_v4().to_string();
                let output =
                    unique_output_path(&directory, &media.path, &candidate.input, reserved);
                let message = build_job_message(
                    &id,
                    &media,
                    &output,
                    &state.paths.work_root,
                    &candidate.input,
                    &candidate.selection,
                )?;
                ids.push(id.clone());
                Ok(NewJob {
                    id,
                    media_id: media_id.to_owned(),
                    request_json: serde_json::to_string(&message)?,
                    ui_json: ui_json(candidate.input.ui.as_ref())?,
                })
            })
            .collect()
    })?;
    Ok(ids)
}

fn ui_json(ui: Option<&ResultUi>) -> AppResult<String> {
    Ok(ui
        .map(serde_json::to_string)
        .transpose()?
        .unwrap_or_default())
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct TrialEstimate {
    model_id: String,
    model_label: String,
    /// Measured run time of the same work on this device; None when unknown.
    seconds: Option<f64>,
}

/// Run times for "Try others…" before anything is queued, from earlier jobs
/// with the same work profile.
#[tauri::command]
pub fn estimate_model_trials(
    state: State<'_, Arc<AppState>>,
    media_id: String,
    model_ids: Vec<String>,
    base_settings: StartBatchInput,
) -> AppResult<Vec<TrialEstimate>> {
    let candidates = trial_candidates(&state, &media_id, &model_ids, &base_settings)?;
    let labels: Vec<String> = {
        let catalog = lock(&state.catalog)?;
        candidates
            .iter()
            .map(|candidate| {
                crate::results::catalog_label(&catalog, &candidate.input.model_id)
                    .unwrap_or_else(|| candidate.input.model_id.clone())
            })
            .collect()
    };
    let database = lock(&state.database)?;
    let media = database
        .get_media(&media_id)?
        .ok_or_else(|| AppError::Validation("The selected image is no longer queued.".into()))?;
    candidates
        .iter()
        .zip(labels)
        .map(|(candidate, model_label)| -> AppResult<TrialEstimate> {
            // The work profile ignores paths and ids; these placeholders never run.
            let message = build_job_message(
                "estimate",
                &media,
                Path::new(""),
                &state.paths.work_root,
                &candidate.input,
                &candidate.selection,
            )?;
            Ok(TrialEstimate {
                model_label,
                model_id: candidate.input.model_id.clone(),
                seconds: database.estimate_request_seconds(&message.to_string(), &media)?,
            })
        })
        .collect()
}

/// The finished attempts of one media for the result choosers, with catalog
/// names. `before` is the `next_before` of the previous page.
#[tauri::command]
pub fn list_media_results(
    state: State<'_, Arc<AppState>>,
    media_id: String,
    limit: Option<u32>,
    before: Option<String>,
) -> AppResult<MediaResults> {
    let mut history = lock(&state.database)?.list_media_results(
        &media_id,
        limit.unwrap_or(50),
        before.as_deref(),
    )?;
    let catalog = lock(&state.catalog)?;
    for result in &mut history.results {
        crate::results::label_summary(&catalog, result);
    }
    Ok(history)
}

/// All image histories in one IPC call for the Outputs pane.
#[tauri::command]
pub fn list_all_media_results(state: State<'_, Arc<AppState>>) -> AppResult<Vec<MediaResults>> {
    let mut histories = lock(&state.database)?.list_all_media_results()?;
    let catalog = lock(&state.catalog)?;
    for history in &mut histories {
        for result in &mut history.results {
            crate::results::label_summary(&catalog, result);
        }
    }
    Ok(histories)
}

/// Replace the Compare tab's picks in slot order, using only host-owned files.
#[tauri::command]
pub fn request_compare(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    picks: Vec<crate::compare::ComparePick>,
) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    let requests = {
        let database = lock(&state.database)?;
        let mut runtime = lock(&state.runtime)?;
        crate::compare::plan(&database, &mut runtime.compare, &picks)?
    };
    for request in requests {
        if let Err(error) = worker::send(&state, &request) {
            let mut failure = request["data"].clone();
            failure["error_message"] = json!(error.to_string());
            let database = lock(&state.database)?;
            let mut runtime = lock(&state.runtime)?;
            crate::compare::apply(&database, &mut runtime.compare, &failure, true)?;
        }
    }
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn request_compare_regions(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    generation: u64,
    regions: Vec<Option<crate::compare::Region>>,
) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    if !lock(&state.engine)?.as_ref().is_some_and(|engine| {
        engine
            .features
            .iter()
            .any(|feature| feature == "compare_regions")
    }) {
        return Ok(());
    }
    let requests = {
        let database = lock(&state.database)?;
        let mut runtime = lock(&state.runtime)?;
        if runtime.compare.generation != generation {
            return Ok(());
        }
        crate::compare::plan_regions(&database, &mut runtime.compare, &regions)?
    };
    for request in requests {
        if worker::send(&state, &request).is_err() {
            let database = lock(&state.database)?;
            let mut runtime = lock(&state.runtime)?;
            crate::compare::apply_region(&database, &mut runtime.compare, &request["data"], true)?;
        }
    }
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn clear_compare_regions(state: State<'_, Arc<AppState>>) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    {
        let database = lock(&state.database)?;
        let mut runtime = lock(&state.runtime)?;
        crate::compare::plan_regions(&database, &mut runtime.compare, &[])?;
    }
    if lock(&state.engine)?.as_ref().is_some_and(|engine| {
        engine
            .features
            .iter()
            .any(|feature| feature == "compare_regions")
    }) {
        let _ = worker::send(&state, &json!({"type":"region_clear", "data":{}}));
    }
    Ok(())
}

#[tauri::command]
pub fn start_benchmark(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    input: StartBenchmarkInput,
) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    if state
        .updates
        .installing
        .load(std::sync::atomic::Ordering::SeqCst)
    {
        return Err(AppError::Validation("An update is being installed".into()));
    }

    ensure_queue_idle(&state)?;
    if lock(&state.runtime)?.worker != "ready" {
        return Err(AppError::Validation(
            "wait for the inference worker to become ready".into(),
        ));
    }
    let (model_path, model_name) = {
        let catalog = lock(&state.catalog)?;
        let model = catalog
            .models
            .iter()
            .find(|model| model.model_id == "span_photo_x4")
            .ok_or_else(|| {
                AppError::Config("benchmark model is missing from the catalog".into())
            })?;
        if !model.installed {
            return Err(AppError::Validation(
                "download and verify the Quick model before running the benchmark".into(),
            ));
        }
        (
            model.installed_path.clone().unwrap_or_default(),
            model.name.clone(),
        )
    };
    let device_exists = lock(&state.capabilities)?
        .devices
        .iter()
        .any(|device| device.id == input.device);
    if !device_exists {
        return Err(AppError::Validation(
            "the selected benchmark device is unavailable".into(),
        ));
    }
    let job_id = format!("benchmark-{}", Uuid::new_v4());
    let mut message = json!({"type":"benchmark_request", "data": {
        "job_id": job_id, "model_path": model_path, "model_id":"span_photo_x4",
        "model_name": model_name, "workload":"v2", "device": input.device
    }});
    lock(&state.worker.cancellation)?.prepare(&job_id, &mut message)?;
    {
        let mut runtime = lock(&state.runtime)?;
        if !runtime.active_job_id.is_empty() {
            return Err(AppError::Validation(
                "wait for the current work to finish before benchmarking".into(),
            ));
        }
        runtime.begin_active_job(job_id.clone());
        runtime.status_title = "Benchmark preparing".into();
        runtime.status_detail = "Loading the fixed LocalSR benchmark workload.".into();
        runtime.progress = 0.0;
    }
    if let Err(error) = worker::send(&state, &message) {
        lock(&state.worker.cancellation)?.finish()?;
        lock(&state.runtime)?.take_active_job();
        return Err(error);
    }
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn export_benchmark(state: State<'_, Arc<AppState>>, destination: String) -> AppResult<()> {
    let result = lock(&state.latest_benchmark)?
        .clone()
        .ok_or_else(|| AppError::Validation("run a benchmark before exporting it".into()))?;
    let destination = PathBuf::from(destination);
    write_benchmark_export(&destination, &serde_json::to_vec_pretty(&result)?)
}

fn write_benchmark_export(destination: &Path, bytes: &[u8]) -> AppResult<()> {
    if destination.file_name().is_none() {
        return Err(AppError::Validation(
            "choose a benchmark JSON filename".into(),
        ));
    }
    let parent = destination
        .parent()
        .ok_or_else(|| AppError::Validation("benchmark destination is invalid".into()))?;
    if !parent.is_dir() {
        return Err(AppError::Validation(
            "benchmark destination folder does not exist".into(),
        ));
    }
    if destination.exists() {
        return Err(AppError::Validation(
            "the chosen benchmark file already exists; choose a new name".into(),
        ));
    }
    // A short fixed name: the destination's own name may already be at the file
    // system's limit.
    let temporary = parent.join(format!(".localsr-benchmark-{}.tmp", Uuid::new_v4()));
    let export_result = (|| -> AppResult<()> {
        let mut output = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        output.write_all(bytes)?;
        output.sync_all()?;
        drop(output);
        publish_without_replacing(&temporary, destination, |from, to| fs::hard_link(from, to))?;
        fs::remove_file(&temporary)?;
        Ok(())
    })();
    if export_result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    export_result
}

/// Give `temporary` the name `destination` without replacing a file that is there.
/// A hard link does it atomically; a volume that has none (FAT, exFAT, a network
/// share) gets a copy into a file only this call created.
fn publish_without_replacing(
    temporary: &Path,
    destination: &Path,
    link: impl Fn(&Path, &Path) -> std::io::Result<()>,
) -> AppResult<()> {
    let Err(error) = link(temporary, destination) else {
        return Ok(());
    };
    if destination.exists() {
        return Err(AppError::Validation(
            "the chosen benchmark file already exists; choose a new name".into(),
        ));
    }
    let copied = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)
        .and_then(|mut output| {
            std::io::copy(&mut fs::File::open(temporary)?, &mut output)?;
            output.sync_all()
        });
    if copied.is_err() {
        let _ = fs::remove_file(destination);
        return Err(AppError::Io(error));
    }
    Ok(())
}

/// Decode saved results for the preview. With `showing` (and `against`, which
/// defaults to the Original), each target is "original" or the id of a
/// completed job of this media whose file exists; the host checks that before
/// anything is decoded, and the two previews arrive in the pair slots. The
/// Original side is drawn from the probe thumbnail and is never decoded here.
/// Without targets, the newest completed result is decoded on its own: the
/// fallback the preview uses when the result history cannot be read.
#[tauri::command]
pub fn request_image_comparison(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    media_id: String,
    showing: Option<String>,
    against: Option<String>,
) -> AppResult<()> {
    let Some(showing) = showing else {
        native_menu::set_comparison_labels(&app, false);
        return request_latest_comparison(&state, media_id);
    };
    let against = against.unwrap_or_else(|| "original".into());
    let (media, showing_path, against_path) = {
        let database = lock(&state.database)?;
        let media = database
            .get_media(&media_id)?
            .ok_or_else(|| AppError::Validation("the selected image is no longer queued".into()))?;
        if media.kind != "image" {
            return Err(AppError::Validation(
                "select an image for this comparison".into(),
            ));
        }
        let resolve = |target: &str| -> AppResult<String> {
            if target == "original" {
                Ok(String::new())
            } else {
                database.media_result_output(&media_id, target)
            }
        };
        let showing_path = resolve(&showing)?;
        let against_path = resolve(&against)?;
        (media, showing_path, against_path)
    };
    native_menu::set_comparison_labels(&app, against != "original");
    let requests = plan_pair_requests(
        &mut *lock(&state.runtime)?,
        &media,
        (&showing, &showing_path),
        (&against, &against_path),
    );
    for request in requests {
        worker::send(&state, &request)?;
    }
    Ok(())
}

/// Record the requested pair and return the decodes it still needs. A side
/// that already shows the same file keeps its preview, also on the other side;
/// asking again for a fully decoded pair changes nothing. `(target, path)`:
/// the path is empty for the Original.
fn plan_pair_requests(
    runtime: &mut crate::types::RuntimeStatus,
    media: &crate::types::MediaItem,
    showing: (&str, &str),
    against: (&str, &str),
) -> Vec<Value> {
    let previous = &runtime.comparison_pair;
    let current = |slot: &ComparisonSlot, (target, path): (&str, &str)| {
        slot.target_id == target && slot.output_path == path
    };
    let settled =
        |slot: &ComparisonSlot| slot.target_id == "original" || !slot.preview_data_url.is_empty();
    // A side that is still loading or failed is asked for again, as after an
    // engine restart.
    if previous.media_id == media.id
        && current(&previous.showing, showing)
        && current(&previous.against, against)
        && settled(&previous.showing)
        && settled(&previous.against)
    {
        return Vec::new();
    }
    let generation = previous.generation + 1;
    let side = |(target, path): (&str, &str)| -> ComparisonSlot {
        if target == "original" {
            return ComparisonSlot {
                target_id: target.into(),
                width: media.width,
                height: media.height,
                ..Default::default()
            };
        }
        [&previous.showing, &previous.against]
            .into_iter()
            .filter(|_| previous.media_id == media.id)
            .find(|slot| current(slot, (target, path)) && !slot.preview_data_url.is_empty())
            .cloned()
            .unwrap_or_else(|| ComparisonSlot {
                target_id: target.into(),
                output_path: path.into(),
                ..Default::default()
            })
    };
    let pair = ComparisonPair {
        media_id: media.id.clone(),
        generation,
        showing: side(showing),
        against: side(against),
    };
    let requests = [("showing", &pair.showing), ("against", &pair.against)]
        .into_iter()
        .filter(|(_, slot)| slot.target_id != "original" && slot.preview_data_url.is_empty())
        .map(|(name, slot)| {
            json!({"type": "preview_request", "data": {
                "image_path": slot.output_path,
                "max_dimension": worker::RESULT_PREVIEW_MAX_DIMENSION,
                "comparison": true,
                "slot": name,
                "generation": generation,
                "target_id": slot.target_id,
            }})
        })
        .collect();
    runtime.comparison_pair = pair;
    requests
}

/// The newest completed result of an image, as one bounded comparison.
fn request_latest_comparison(state: &AppState, media_id: String) -> AppResult<()> {
    let output = {
        let database = lock(&state.database)?;
        let media = database
            .get_media(&media_id)?
            .ok_or_else(|| AppError::Validation("the selected image is no longer queued".into()))?;
        if media.kind != "image" {
            return Err(AppError::Validation(
                "select an image for this comparison".into(),
            ));
        }
        database
            .latest_completed_output_for_media(&media_id)?
            .ok_or_else(|| AppError::Validation("this image has no completed output".into()))?
    };
    if !Path::new(&output).is_file() {
        return Err(AppError::Validation(
            "the completed image is no longer available".into(),
        ));
    }
    {
        let mut runtime = lock(&state.runtime)?;
        if runtime.comparison_media_id == media_id
            && runtime.comparison_output_path == output
            && !runtime.comparison_preview_data_url.is_empty()
        {
            return Ok(());
        }
        runtime.comparison_media_id = media_id;
        runtime.comparison_output_path = output.clone();
        runtime.comparison_preview_data_url.clear();
        runtime.comparison_error.clear();
    }
    // A single bounded comparison is retained independently of the active
    // job. The worker response carries its path, so a late response from a
    // previously selected result cannot replace the current comparison.
    worker::send(
        state,
        &json!({
            "type": "preview_request",
            "data": {"image_path": output, "max_dimension": worker::RESULT_PREVIEW_MAX_DIMENSION, "comparison": true}
        }),
    )
}

#[tauri::command]
pub async fn prepare_video_comparison(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    media_id: String,
    request_id: String,
    force_compatible: bool,
) -> AppResult<VideoComparisonSources> {
    let cancel = state.video_preview.begin(&request_id)?;
    let state = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        video_comparison_sources(
            &state,
            &app,
            &media_id,
            &request_id,
            force_compatible,
            cancel,
        )
    })
    .await
    .map_err(|error| AppError::Worker(format!("Playback preparation failed: {error}")))?
}

fn video_comparison_sources(
    state: &AppState,
    app: &AppHandle,
    media_id: &str,
    request_id: &str,
    force_compatible: bool,
    cancel: Arc<std::sync::atomic::AtomicBool>,
) -> AppResult<VideoComparisonSources> {
    let (original, enhanced) = {
        let database = lock(&state.database)?;
        let media = database
            .get_media(media_id)?
            .ok_or_else(|| AppError::Validation("the selected video is no longer queued".into()))?;
        if media.kind != "video" {
            return Err(AppError::Validation(
                "video playback is available only for videos".into(),
            ));
        }
        // Before a job finishes there is no output: play the source alone.
        let output = database.latest_completed_output_for_media(media_id)?;
        (PathBuf::from(media.path), output.map(PathBuf::from))
    };
    let original = fs::canonicalize(original)?;
    let enhanced = enhanced.map(fs::canonicalize).transpose()?;
    if !original.is_file() || enhanced.as_ref().is_some_and(|path| !path.is_file()) {
        return Err(AppError::Validation(
            "the original or enhanced video is no longer available".into(),
        ));
    }
    let mut converted = false;
    let mut playback = |path: PathBuf| -> AppResult<PathBuf> {
        if force_compatible || crate::video_preview::needs_conversion(&path) {
            converted = true;
            let reporter = app.clone();
            let request = request_id.to_owned();
            let media = media_id.to_owned();
            state.video_preview.convert(
                &path,
                &state.paths.work_root,
                worker::codec_helper_command(state, app)?,
                Arc::clone(&cancel),
                move |mut data| {
                    data["request_id"] = json!(request);
                    data["media_id"] = json!(media);
                    let _ = reporter.emit("video-comparison-progress", data);
                },
            )
        } else {
            Ok(path)
        }
    };
    let original = playback(original)?;
    let enhanced = enhanced.map(&mut playback).transpose()?;
    if cancel.load(std::sync::atomic::Ordering::SeqCst) {
        return Err(AppError::Validation(
            "Playback conversion cancelled.".into(),
        ));
    }
    allow_video_preview_pair(&app.asset_protocol_scope(), &original, enhanced.as_deref())?;
    // WebKitGTK cannot reliably play media through a custom URI scheme.
    // A private loopback stream provides normal HTTP byte-range seeking.
    #[cfg(target_os = "linux")]
    let (original_url, enhanced_url) = {
        let mut server = lock(&state.media_server)?;
        if server.is_none() {
            *server = Some(crate::media_server::MediaServer::start()?);
        }
        let server = server.as_ref().expect("media server was initialized");
        (
            Some(server.url_for(&original)?),
            enhanced
                .as_deref()
                .map(|path| server.url_for(path))
                .transpose()?,
        )
    };
    #[cfg(not(target_os = "linux"))]
    let (original_url, enhanced_url) = (None, None);
    Ok(VideoComparisonSources {
        original_path: original.to_string_lossy().into_owned(),
        enhanced_path: enhanced
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_default(),
        original_url,
        enhanced_url,
        playback_note: if converted {
            "Compatible SDR playback copy · up to 1280 px. Source and saved export are unchanged."
                .into()
        } else {
            String::new()
        },
    })
}

#[tauri::command]
pub fn cancel_video_comparison(
    state: State<'_, Arc<AppState>>,
    request_id: String,
) -> AppResult<()> {
    state.video_preview.cancel(&request_id)
}

fn allow_video_preview_pair(
    scope: &tauri::scope::fs::Scope,
    original: &Path,
    enhanced: Option<&Path>,
) -> AppResult<()> {
    // Authorize only these canonical files (the source, and the result once
    // there is one) for this app session. Tauri's
    // forbid_file is a permanent deny, not the inverse of allow_file: revoking
    // on selection change prevented every subsequent visit to the same video.
    // The unmounted player releases its media elements; no folder is authorized.
    scope.allow_file(original).map_err(|error| {
        AppError::Config(format!(
            "could not authorize the original video preview: {error}"
        ))
    })?;
    let Some(enhanced) = enhanced else {
        return Ok(());
    };
    scope.allow_file(enhanced).map_err(|error| {
        AppError::Config(format!(
            "could not authorize the enhanced video preview: {error}"
        ))
    })
}

#[tauri::command]
pub fn refresh_capabilities(state: State<'_, Arc<AppState>>) -> AppResult<()> {
    worker::send(&state, &json!({"type": "capabilities_request", "data": {}}))
}

#[tauri::command]
pub fn probe_path(state: State<'_, Arc<AppState>>, app: AppHandle, path: String) -> AppResult<()> {
    let canonical = fs::canonicalize(path)?;
    let encoded = canonical.to_string_lossy().into_owned();
    let queued_media = lock(&state.database)?.get_media_by_path(&encoded)?;
    let last_output = lock(&state.runtime)?.last_output_path.clone();
    let is_last_output = !last_output.is_empty()
        && fs::canonicalize(last_output)
            .map(|expected| expected == canonical)
            .unwrap_or(false);
    if queued_media.is_none() && !is_last_output {
        return Err(AppError::Validation(
            "preview path was not authorized by the media queue".into(),
        ));
    }
    // A queued source must update its own persisted thumbnail. `preview_ready`
    // is reserved for the completed output comparison.
    if queued_media.is_some() {
        lock(&state.database)?.start_media_probe(&encoded)?;
        emit_state_changed(&app);
    }
    let result = if queued_media.is_some() || media_kind(&canonical) == Some("video") {
        worker::send(&state, &worker::media_probe_request(&state, &encoded))
    } else {
        worker::send(
            &state,
            &json!({
                "type": "preview_request",
                "data": {"image_path": encoded, "max_dimension": 2048}
            }),
        )
    };
    if let Err(error) = &result {
        if queued_media.is_some() {
            lock(&state.database)?.fail_media_probe(&encoded, &error.to_string())?;
            emit_state_changed(&app);
        }
    }
    result
}

#[tauri::command]
pub async fn download_model(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    model_id: String,
    accepted_terms: bool,
) -> AppResult<()> {
    downloads::download_model(state.inner().clone(), app, model_id, accepted_terms).await
}

/// Remove an installed catalog checkpoint from the shared model directory.
///
/// Only files that the catalog itself placed under `model_root` are eligible,
/// so a user-supplied custom checkpoint elsewhere on disk is never touched.
/// Processing and downloads must be idle: a job may be reading the file.
#[tauri::command]
pub fn remove_model(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    model_id: String,
) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    if !lock(&state.runtime)?.active_job_id.is_empty() {
        return Err(AppError::Validation(
            "finish or cancel processing before removing a model".into(),
        ));
    }
    if !lock(&state.runtime)?.download_model_id.is_empty() {
        return Err(AppError::Validation(
            "wait for the current download to finish before removing a model".into(),
        ));
    }
    let path = {
        let catalog = lock(&state.catalog)?;
        match catalog
            .models
            .iter()
            .find(|model| model.model_id == model_id)
        {
            Some(model) if !model.installed => {
                return Err(AppError::Validation("the model is not installed".into()));
            }
            Some(model) => Some(
                state
                    .paths
                    .model_root
                    .join(crate::catalog::safe_model_filename(&model.filename)?),
            ),
            None => None,
        }
    };
    match path {
        Some(path) => {
            if lock(&state.database)?.unfinished_job_needs_model(&path)? {
                return Err(AppError::Validation(
                    "a queued job still needs this model; finish or cancel it first".into(),
                ));
            }
            if path.is_file() {
                fs::remove_file(&path)?;
            }
            state.refresh_catalog()?;
        }
        // Editing bundles live beside the catalog; shared components survive.
        None if !crate::editing::remove_bundle(&state.paths, &model_id)? => {
            return Err(AppError::Validation("unknown image model".into()));
        }
        None => {}
    }
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn cancel_download(state: State<'_, Arc<AppState>>, model_id: String) -> AppResult<()> {
    downloads::cancel_download(&state, &model_id)
}

#[tauri::command]
pub fn import_catalog_model(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    model_id: String,
    source_path: String,
    accepted_terms: bool,
) -> AppResult<()> {
    crate::updates::ensure_not_installing(&state)?;
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    let (filename, size_bytes, sha256, terms_required) = {
        let catalog = lock(&state.catalog)?;
        let model = catalog
            .models
            .iter()
            .find(|model| model.model_id == model_id)
            .ok_or_else(|| AppError::Validation("unknown catalog model".into()))?;
        (
            catalog::safe_model_filename(&model.filename)?.to_owned(),
            model.size_bytes,
            model.sha256.clone(),
            model.terms_acceptance_required,
        )
    };
    if terms_required && !accepted_terms {
        return Err(AppError::Validation(
            "review and accept this checkpoint's stated terms before importing it".into(),
        ));
    }
    let source = fs::canonicalize(source_path)
        .map_err(|_| AppError::Validation("the selected checkpoint is unavailable".into()))?;
    if !source.is_file() || !catalog::verified_file(&source, size_bytes, &sha256) {
        return Err(AppError::Validation(
            "the selected file does not match the catalog size and SHA-256 digest".into(),
        ));
    }
    fs::create_dir_all(&state.paths.model_root)?;
    let destination = state.paths.model_root.join(filename);
    import_verified_catalog_file(&source, &destination, size_bytes, &sha256)?;
    state.refresh_catalog()?;
    emit_state_changed(&app);
    Ok(())
}

fn import_verified_catalog_file(
    source: &Path,
    destination: &Path,
    size_bytes: u64,
    sha256: &str,
) -> AppResult<()> {
    if destination.exists() {
        let existing = fs::canonicalize(destination)?;
        if existing == source || catalog::verified_file(destination, size_bytes, sha256) {
            return Ok(());
        }
        return Err(AppError::Validation(
            "a conflicting checkpoint already exists in LocalSR's model library; remove it manually before importing"
                .into(),
        ));
    }
    let parent = destination
        .parent()
        .ok_or_else(|| AppError::Validation("model destination is invalid".into()))?;
    let temporary = parent.join(format!(".model-import-{}.tmp", Uuid::new_v4()));
    let result = (|| -> AppResult<()> {
        fs::copy(source, &temporary)?;
        if !catalog::verified_file(&temporary, size_bytes, sha256) {
            return Err(AppError::Validation(
                "the imported copy failed its SHA-256 verification".into(),
            ));
        }
        fs::rename(&temporary, destination)?;
        Ok(())
    })();
    if temporary.exists() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[tauri::command]
pub fn open_result(state: State<'_, Arc<AppState>>, path: String) -> AppResult<()> {
    let authorized = authorized_result(&state, &path)?;
    if !is_openable_output(&authorized) {
        return Err(AppError::Validation(
            "this file type is not opened from LocalSR; use Reveal instead".into(),
        ));
    }
    open::that_detached(authorized)
        .map_err(|error| AppError::Config(format!("could not open the output: {error}")))
}

#[tauri::command]
pub fn reveal_result(state: State<'_, Arc<AppState>>, path: String) -> AppResult<()> {
    let authorized = authorized_result(&state, &path)?;
    let parent = authorized
        .parent()
        .ok_or_else(|| AppError::Validation("output directory is unavailable".into()))?;
    open::that_detached(parent)
        .map_err(|error| AppError::Config(format!("could not reveal the output: {error}")))
}

#[tauri::command]
pub fn open_output_directory(state: State<'_, Arc<AppState>>) -> AppResult<()> {
    let requested = lock(&state.settings)?.output_directory.clone();
    let directory = prepare_output_directory(&state, &requested)?;
    open::that_detached(directory)
        .map_err(|error| AppError::Config(format!("could not open the output folder: {error}")))
}

#[tauri::command]
pub fn integration_status() -> AppResult<integrations::IntegrationStatus> {
    integrations::status()
}

#[tauri::command]
pub fn install_integrations() -> AppResult<integrations::IntegrationStatus> {
    integrations::install()
}

#[tauri::command]
pub fn uninstall_integrations() -> AppResult<integrations::IntegrationStatus> {
    integrations::uninstall()
}

/// Suggest FFmpeg executables the user installed. LocalSR never runs one
/// until the user selects it, and never downloads or bundles FFmpeg.
#[tauri::command]
pub fn detect_external_ffmpeg(state: State<'_, Arc<AppState>>) -> Vec<String> {
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
    for path in &found {
        state.programs.grant(path);
    }
    found
}

/// Let the user pick the FFmpeg program in the host's own file dialog. The host
/// remembers the choice: only programs it found or was shown this way are run.
#[tauri::command]
pub async fn choose_external_ffmpeg(
    app: AppHandle,
    state: State<'_, Arc<AppState>>,
    current: String,
) -> AppResult<Option<String>> {
    use tauri_plugin_dialog::DialogExt;

    let mut dialog = app
        .dialog()
        .file()
        .set_title("Select the FFmpeg program installed on this computer");
    if let Some(folder) = Path::new(&current)
        .parent()
        .filter(|folder| folder.is_dir())
    {
        dialog = dialog.set_directory(folder);
    }
    let picked = tauri::async_runtime::spawn_blocking(move || dialog.blocking_pick_file())
        .await
        .map_err(|error| AppError::Config(format!("the file dialog failed: {error}")))?;
    let Some(picked) = picked else {
        return Ok(None);
    };
    let path = picked
        .into_path()
        .map_err(|error| AppError::Validation(format!("that is not a file path: {error}")))?
        .to_string_lossy()
        .into_owned();
    state.programs.grant(&path);
    Ok(Some(path))
}

pub(crate) const USER_GUIDE_URL: &str = "https://herrei.github.io/localsr/guide/";

#[tauri::command]
pub fn open_user_guide() -> AppResult<()> {
    open::that_detached(USER_GUIDE_URL)
        .map_err(|error| AppError::Config(format!("could not open the user guide: {error}")))
}

/// The editions that use a GPU this one cannot (CUDA, DirectML, ROCm) are on this page;
/// ci/public-beta-release.json publishes the same address.
pub(crate) const DOWNLOAD_PAGE_URL: &str = "https://herrei.github.io/localsr/download/";

#[tauri::command]
pub fn open_download_page() -> AppResult<()> {
    open::that_detached(DOWNLOAD_PAGE_URL)
        .map_err(|error| AppError::Config(format!("could not open the download page: {error}")))
}

/// FFmpeg stays the user's own action: LocalSR never downloads or bundles it.
#[tauri::command]
pub fn open_ffmpeg_download_page() -> AppResult<()> {
    open::that_detached("https://ffmpeg.org/download.html").map_err(|error| {
        AppError::Config(format!("could not open the FFmpeg download page: {error}"))
    })
}

/// Whether something other than the user's selected FFmpeg can handle H.264/HEVC:
/// the system codecs (macOS) or an FFmpeg the worker found on this computer.
pub(crate) fn external_codecs_available(state: &AppState) -> bool {
    let Ok(capabilities) = state.capabilities.lock() else {
        return false;
    };
    let media = &capabilities.media;
    media["system_codecs"].is_object()
        || media["external_ffmpeg"]["detected"]
            .as_array()
            .is_some_and(|found| !found.is_empty())
}

/// How to install FFmpeg on this computer: the package manager's command and,
/// where a distribution needs an extra repository, a note about it.
#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct FfmpegInstallHint {
    pub command: String,
    pub note: String,
    pub system: String,
}

pub(crate) fn install_hint_for(os: &str, os_release: &str) -> FfmpegInstallHint {
    let hint = |command: &str, note: &str, system: &str| FfmpegInstallHint {
        command: command.into(),
        note: note.into(),
        system: system.into(),
    };
    match os {
        "macos" => return hint("brew install ffmpeg", "Needs Homebrew (brew.sh).", "macOS"),
        "windows" => {
            return hint(
                "winget install Gyan.FFmpeg",
                "Open a new terminal afterwards so PATH is refreshed.",
                "Windows",
            )
        }
        _ => {}
    }
    let field = |name: &str| -> String {
        os_release
            .lines()
            .find_map(|line| {
                line.strip_prefix(name)
                    .and_then(|rest| rest.strip_prefix('='))
            })
            .map(|value| value.trim().trim_matches('"').to_ascii_lowercase())
            .unwrap_or_default()
    };
    let id = field("ID");
    let like = format!("{id} {}", field("ID_LIKE"));
    let has = |name: &str| like.split_whitespace().any(|word| word == name);
    if has("fedora") {
        hint(
            "sudo dnf install ffmpeg",
            "Fedora's own ffmpeg-free package has no H.264; enable RPM Fusion first.",
            "Fedora",
        )
    } else if has("rhel") || has("centos") {
        hint(
            "sudo dnf install ffmpeg",
            "Needs the RPM Fusion or EPEL repository.",
            "RHEL",
        )
    } else if has("arch") {
        hint("sudo pacman -S ffmpeg", "", "Arch Linux")
    } else if has("suse") || has("opensuse") {
        hint(
            "sudo zypper install ffmpeg",
            "Needs the Packman repository for H.264.",
            "openSUSE",
        )
    } else if has("alpine") {
        hint("sudo apk add ffmpeg", "", "Alpine")
    } else if has("nixos") {
        hint("nix-env -iA nixpkgs.ffmpeg", "", "NixOS")
    } else if has("debian") || has("ubuntu") || id.is_empty() {
        hint(
            "sudo apt install ffmpeg",
            "",
            if has("ubuntu") { "Ubuntu" } else { "Debian" },
        )
    } else {
        hint(
            "sudo apt install ffmpeg",
            "Use your distribution's package manager if it is not apt.",
            "Linux",
        )
    }
}

#[tauri::command]
pub fn ffmpeg_install_hint() -> FfmpegInstallHint {
    let release = std::fs::read_to_string("/etc/os-release").unwrap_or_default();
    install_hint_for(std::env::consts::OS, &release)
}

/// Open the user's terminal with the install command typed in, so they can
/// read it and approve any password prompt themselves. Only the command from
/// `ffmpeg_install_hint` is accepted.
#[tauri::command]
pub fn open_terminal_with_install_command(command: String) -> AppResult<()> {
    if command != ffmpeg_install_hint().command {
        return Err(AppError::Validation("unexpected install command".into()));
    }
    open_terminal(&command).map_err(|error| {
        AppError::Config(format!(
            "could not open a terminal for the install command: {error}"
        ))
    })
}

#[cfg(target_os = "macos")]
fn open_terminal(command: &str) -> std::io::Result<()> {
    let script = format!("tell application \"Terminal\" to do script \"{command}\"");
    std::process::Command::new("osascript")
        .args([
            "-e",
            "tell application \"Terminal\" to activate",
            "-e",
            &script,
        ])
        .status()
        .and_then(|status| {
            if status.success() {
                Ok(())
            } else {
                Err(std::io::Error::other("osascript failed"))
            }
        })
}

#[cfg(target_os = "windows")]
fn open_terminal(command: &str) -> std::io::Result<()> {
    std::process::Command::new("cmd")
        .args(["/c", "start", "cmd", "/k", command])
        .spawn()
        .map(|_| ())
}

#[cfg(target_os = "linux")]
fn open_terminal(command: &str) -> std::io::Result<()> {
    let script = format!("{command}; echo; read -r -p 'Press Enter to close this window'");
    // xfce4-terminal takes the whole command as one -e argument, so it needs its
    // own string; binding it here keeps it alive for as long as the array.
    let single_argument = format!("bash -c \"{script}\"");
    let attempts: [(&str, Vec<&str>); 5] = [
        ("x-terminal-emulator", vec!["-e", "bash", "-c", &script]),
        ("gnome-terminal", vec!["--", "bash", "-c", &script]),
        ("konsole", vec!["-e", "bash", "-c", &script]),
        ("xfce4-terminal", vec!["-e", &single_argument]),
        ("xterm", vec!["-e", "bash", "-c", &script]),
    ];
    let mut last = std::io::Error::other("no terminal emulator found");
    for (program, arguments) in attempts {
        match std::process::Command::new(program).args(&arguments).spawn() {
            Ok(_) => return Ok(()),
            Err(error) => last = error,
        }
    }
    Err(last)
}

#[tauri::command]
pub fn diagnostic_summary(state: State<'_, Arc<AppState>>) -> AppResult<String> {
    let snapshot = state.snapshot()?;
    let installed_images = snapshot
        .catalog
        .models
        .iter()
        .filter(|model| model.installed)
        .count();
    let installed_video = snapshot
        .catalog
        .video_models
        .iter()
        .filter(|model| model.installed)
        .count();
    let jobs = snapshot
        .jobs
        .iter()
        .fold(std::collections::BTreeMap::new(), |mut counts, job| {
            *counts.entry(job.status.clone()).or_insert(0_u64) += 1;
            counts
        });
    let devices: Vec<Value> = snapshot
        .capabilities
        .devices
        .iter()
        .map(|device| {
            json!({
                "id": device.id,
                "type": device.device_type,
                "name": device.name,
                "total_memory": device.total_memory,
                "supports_fp16": device.supports_fp16
            })
        })
        .collect();
    let summary = json!({
        "privacy": "File paths and media names omitted",
        "video_memory": snapshot.runtime.video_memory,
        "app_version": snapshot.app_version,
        "protocol_version": snapshot.protocol_version,
        "platform": std::env::consts::OS,
        "architecture": std::env::consts::ARCH,
        "worker": snapshot.runtime.worker,
        "engine": snapshot.engine,
        "catalog_revision": snapshot.catalog.catalog_revision,
        "installed_image_models": installed_images,
        "installed_video_models": installed_video,
        "media_count": snapshot.media.len(),
        "job_status_counts": jobs,
        "memory_pressure": snapshot.capabilities.system_memory_pressure_level,
        "devices": devices
    });
    Ok(serde_json::to_string_pretty(&summary)?)
}

fn ensure_queue_idle(state: &AppState) -> AppResult<()> {
    if lock(&state.database)?.has_inflight_jobs()? {
        Err(AppError::Validation(
            "the processing queue is already active; cancel it before starting another batch"
                .into(),
        ))
    } else {
        Ok(())
    }
}

fn clear_completed_result(state: &AppState) -> AppResult<()> {
    let mut runtime = lock(&state.runtime)?;
    runtime.progress = 0.0;
    runtime.last_output_path.clear();
    runtime.result_preview_data_url.clear();
    runtime.elapsed_seconds = 0.0;
    runtime.estimated_remaining_seconds = 0.0;
    runtime.throughput = 0.0;
    runtime.throughput_unit.clear();
    runtime.active_tile_size = 0;
    if runtime.worker == "ready" {
        runtime.status_title = "Ready".into();
        runtime.status_detail = "The isolated inference engine is ready.".into();
    }
    Ok(())
}

/// HEIC, MOV and ProRes are written by macOS's own encoders, so only macOS accepts them.
fn output_format_is_valid(format: &str) -> bool {
    matches!(format, "png" | "jpg" | "tif" | "webp")
        || (format == "heic" && cfg!(target_os = "macos"))
}

fn video_container_is_valid(container: &str) -> bool {
    matches!(container, "mp4" | "mkv") || (container == "mov" && cfg!(target_os = "macos"))
}

fn video_codec_is_valid(codec: &str) -> bool {
    matches!(codec, "auto" | "av1" | "vp9" | "ffv1" | "h264" | "hevc")
        || (codec == "prores" && cfg!(target_os = "macos"))
}

/// Whether the engine announced `name` in the list `media[key]` (or in `media[key][sub]`).
fn announced(list: &Value, name: &str) -> bool {
    list.as_array()
        .is_some_and(|items| items.iter().any(|item| item == name))
}

/// HEIC images and MOV/ProRes videos need the macOS codecs the engine reports. The
/// engine says so in the capabilities; a host that has not heard from it offers neither.
fn validate_system_outputs(input: &StartBatchInput, media: &Value) -> AppResult<()> {
    if input.task == "video" {
        if (input.video_container == "mov" || input.video_codec == "prores")
            && !announced(&media["system_codecs"]["encode"], "prores")
        {
            return Err(AppError::Validation(
                "MOV and ProRes export use the codecs of macOS, which this computer does not have."
                    .into(),
            ));
        }
    } else if input.output_format == "heic" && !announced(&media["image_output"], "heic") {
        return Err(AppError::Validation(
            "HEIC output needs macOS, which provides the HEIC encoder.".into(),
        ));
    }
    Ok(())
}

/// The device a run uses. A device this computer does not report is refused, except
/// the one saved in the settings: it stays saved, since the device may come back,
/// and the run uses the CPU, which is always there.
fn resolve_device(state: &AppState, requested: &str) -> AppResult<String> {
    let capabilities = lock(&state.capabilities)?;
    let reported = |id: &str| capabilities.devices.iter().any(|device| device.id == id);
    if reported(requested) {
        return Ok(requested.to_owned());
    }
    if capabilities.system_ram_total == 0 {
        return Err(AppError::Validation(
            "LocalSR is still detecting this computer's hardware. Try again in a moment.".into(),
        ));
    }
    if requested == lock(&state.settings)?.device_id && reported("cpu") {
        return Ok("cpu".into());
    }
    Err(AppError::Validation(
        "The selected device is not available on this computer. Choose another one in Advanced settings.".into(),
    ))
}

fn validate_start(input: &StartBatchInput, state: &AppState) -> AppResult<()> {
    validate_start_input(input, external_codecs_available(state))?;
    state.programs.check(&input.external_ffmpeg_path)?;
    let media = state
        .capabilities
        .lock()
        .map(|capabilities| capabilities.media.clone())
        .unwrap_or(Value::Null);
    validate_system_outputs(input, &media)
}

fn validate_settings(settings: &UiSettings) -> AppResult<()> {
    validate_fix_selections(&settings.fix_stages)?;
    if !matches!(settings.interface_scale, 100 | 110 | 125) {
        return Err(AppError::Validation(
            "interface scale must be 100, 110, or 125 percent".into(),
        ));
    }
    if !settings.task.is_empty()
        && !matches!(
            settings.task.as_str(),
            "upscale" | "denoise" | "edit" | "video"
        )
    {
        return Err(AppError::Validation("unknown task".into()));
    }
    // Zero steps means the chosen model's own default.
    if !matches!(settings.edit_max_dimension, 512 | 768 | 1024)
        || settings.edit_steps > 60
        || settings.edit_seed > i32::MAX as u32
        || settings.edit_model_id.len() > 64
    {
        return Err(AppError::Validation(
            "unsupported edit size, step count, seed or model".into(),
        ));
    }
    if !matches!(settings.quality.as_str(), "" | "quick" | "best" | "custom")
        || !matches!(settings.content.as_str(), "" | "photo" | "illustration")
        || settings.fixes.len() > 4
        || !settings
            .fixes
            .iter()
            .all(|fix| matches!(fix.as_str(), "noise" | "jpeg" | "blur" | "faces"))
        || settings.preset_pins.len() > 64
        || !settings
            .preset_pins
            .iter()
            .all(|(slot, model_id)| slot.len() <= 64 && model_id.len() <= 64)
    {
        return Err(AppError::Validation(
            "unsupported quality, content, fix, or preset pin setting".into(),
        ));
    }
    if !output_format_is_valid(&settings.output_format)
        || !video_container_is_valid(&settings.video_container)
        || !video_codec_is_valid(&settings.video_codec)
        || !matches!(settings.video_hdr_mode.as_str(), "tone_map" | "preserve")
        || !matches!(settings.precision.as_str(), "fp32" | "fp16" | "bf16")
        || !interpolation_choice_is_valid(
            &settings.video_interpolation,
            &settings.video_interpolation_order,
        )
    {
        return Err(AppError::Validation(
            "unsupported output or precision setting".into(),
        ));
    }
    if settings.video_codec == "ffv1" && settings.video_container != "mkv" {
        return Err(AppError::Validation(
            "Lossless FFV1 video requires the MKV container.".into(),
        ));
    }
    if settings.video_container == "mov"
        && !matches!(settings.video_codec.as_str(), "hevc" | "prores")
    {
        return Err(AppError::Validation(
            "MOV holds only HEVC or Apple ProRes video.".into(),
        ));
    }
    if settings.video_codec == "prores" && settings.video_container != "mov" {
        return Err(AppError::Validation(
            "Apple ProRes video requires the MOV container.".into(),
        ));
    }
    if settings.external_ffmpeg_path.len() > 4096 || settings.external_ffmpeg_path.contains('\0') {
        return Err(AppError::Validation(
            "the external FFmpeg path is invalid".into(),
        ));
    }
    if !(1..=8).contains(&settings.output_scale)
        || !(32..=2048).contains(&settings.tile_size)
        || settings.halo > 256
        || !(1..=9).contains(&settings.deflicker_window)
        || !(0..=51).contains(&settings.video_crf)
        || !(1..=100).contains(&settings.jpeg_quality)
        || settings.face_fidelity > 100
        || !matches!(
            settings.video_target_resolution,
            0 | 256 | 512 | 720 | 1080 | 1440 | 2160
        )
    {
        return Err(AppError::Validation(
            "one or more numeric settings are out of range".into(),
        ));
    }
    Ok(())
}

/// Engines without image_multi_fix run at most one fix; a longer plan would only fail in the
/// worker after it was queued.
fn validate_fix_engine(
    input: &StartBatchInput,
    engine: Option<&crate::types::EngineInfo>,
) -> AppResult<()> {
    if input.fix_stages.len() > 1
        && !engine.is_some_and(|engine| {
            engine
                .features
                .iter()
                .any(|feature| feature == "image_multi_fix")
        })
    {
        return Err(AppError::Validation(
            "This engine runs one fix at a time. Update LocalSR to run several.".into(),
        ));
    }
    Ok(())
}

fn validate_fix_selections(stages: &[crate::types::FixStageSelection]) -> AppResult<()> {
    let mut fixes = HashSet::new();
    if stages.len() > 3
        || stages.iter().any(|stage| {
            !matches!(stage.fix.as_str(), "blur" | "jpeg" | "noise")
                || !fixes.insert(&stage.fix)
                || stage.model_id.len() > 64
        })
    {
        return Err(AppError::Validation(
            "choose up to three different fixes: blur, JPEG or noise".into(),
        ));
    }
    Ok(())
}

/// Frame interpolation targets and orders the worker accepts.
fn interpolation_choice_is_valid(target: &str, order: &str) -> bool {
    matches!(target, "off" | "x2" | "x4" | "fps60" | "fps120")
        && matches!(order, "after_upscale" | "before_upscale")
}

fn validate_start_input(input: &StartBatchInput, external_codecs: bool) -> AppResult<()> {
    let settings = UiSettings {
        interface_scale: 100,
        batch_mode: input.batch_mode,
        task: input.task.clone(),
        selected_model_id: input.model_id.clone(),
        selected_video_model_id: input.video_model_id.clone(),
        fix_stages: input.fix_stages.clone(),
        custom_model_path: input.custom_model_path.clone(),
        output_scale: input.output_scale,
        output_directory: input.output_directory.clone(),
        output_format: input.output_format.clone(),
        preserve_metadata: input.preserve_metadata,
        jpeg_quality: input.jpeg_quality,
        device_id: input.device.clone(),
        tile_size: input.tile_size,
        halo: input.halo,
        precision: input.precision.clone(),
        safe_memory: input.safe_memory,
        deflicker: input.deflicker,
        deflicker_window: input.deflicker_window,
        video_container: input.video_container.clone(),
        video_codec: input.video_codec.clone(),
        external_ffmpeg_path: input.external_ffmpeg_path.clone(),
        video_hdr_mode: input.video_hdr_mode.clone(),
        video_target_resolution: input.video_target_resolution,
        video_low_memory: input.video_low_memory,
        video_crf: input.video_crf,
        neural_engine: input.neural_engine,
        enable_face_model: input.enable_face_model,
        face_fidelity: input.face_fidelity,
        enable_live_preview: input.enable_live_preview,
        allow_unsafe_pickle_model: input.allow_unsafe_pickle_model,
        video_interpolation: input.video_interpolation.clone(),
        video_interpolation_order: input.video_interpolation_order.clone(),
        ..UiSettings::default()
    };
    validate_settings(&settings)?;
    if input.task == "video"
        && input.video_interpolation != "off"
        && input.video_model_id != "frame_by_frame"
    {
        return Err(AppError::Validation(
            "Frame interpolation works with the Frame-by-frame video engine in this version. Choose Frame-by-frame or turn interpolation off.".into(),
        ));
    }
    if input.task == "video"
        && matches!(input.video_codec.as_str(), "h264" | "hevc")
        && input.external_ffmpeg_path.trim().is_empty()
        && !external_codecs
    {
        return Err(AppError::Validation(
            "H.264 and HEVC export use the codecs of this system or an FFmpeg installed on this computer. Install FFmpeg and select it under Advanced settings → Video, or choose AV1, VP9 or FFV1.".into(),
        ));
    }
    if input.media_ids.is_empty() || input.media_ids.len() > MAX_MEDIA_ITEMS {
        return Err(AppError::Validation("invalid batch size".into()));
    }
    Ok(())
}

#[derive(Clone)]
enum ModelSelection {
    Image {
        model_id: String,
        path: String,
        preprocess: Vec<ResolvedImageStage>,
        face_path: Option<String>,
        face_model_id: String,
        native_scale: u32,
        interpolation: Option<ResolvedInterpolation>,
    },
    Temporal {
        model_id: String,
        engine_kind: String,
        bundle_dir: String,
        temporal_window: u32,
        temporal_overlap: u32,
    },
}

/// The downloaded frame-interpolation bundle a video job uses.
#[derive(Clone)]
struct ResolvedInterpolation {
    model_id: String,
    bundle_dir: String,
}

#[derive(Clone)]
struct ResolvedImageStage {
    kind: &'static str,
    fix: String,
    model_id: String,
    path: String,
}

fn resolve_model_selection(state: &AppState, input: &StartBatchInput) -> AppResult<ModelSelection> {
    validate_fix_selections(&input.fix_stages)?;
    if !input.fix_stages.is_empty() && !matches!(input.task.as_str(), "upscale" | "denoise") {
        return Err(AppError::Validation(
            "fixes run only before an image upscale or restore".into(),
        ));
    }
    let catalog = lock(&state.catalog)?;
    if input.task == "video" && input.video_model_id != "frame_by_frame" {
        let model = catalog
            .video_models
            .iter()
            .find(|model| model.model_id == input.video_model_id)
            .ok_or_else(|| AppError::Validation("unknown temporal video model".into()))?;
        if !model.installed {
            return Err(AppError::Validation(
                "download and verify the video model first".into(),
            ));
        }
        return Ok(ModelSelection::Temporal {
            model_id: model.model_id.clone(),
            engine_kind: model.engine_kind.clone(),
            bundle_dir: model.installed_path.clone().unwrap_or_default(),
            temporal_window: model.temporal_window,
            temporal_overlap: model.temporal_overlap,
        });
    }

    let (path, selected_model) = if input.model_id == "__custom__" {
        let path = fs::canonicalize(&input.custom_model_path)
            .map_err(|_| AppError::Validation("custom checkpoint is unavailable".into()))?;
        if !path.is_file() {
            return Err(AppError::Validation(
                "custom checkpoint is not a file".into(),
            ));
        }
        let extension = extension(&path).unwrap_or_default();
        if extension != "safetensors" && !input.allow_unsafe_pickle_model {
            return Err(AppError::Validation(
                "pickle-based checkpoints require the explicit unsafe checkpoint confirmation"
                    .into(),
            ));
        }
        if !matches!(extension.as_str(), "safetensors" | "pth" | "pt" | "ckpt") {
            return Err(AppError::Validation(
                "unsupported custom checkpoint format".into(),
            ));
        }
        (path.to_string_lossy().into_owned(), None)
    } else {
        let model = catalog
            .models
            .iter()
            .find(|model| model.model_id == input.model_id)
            .ok_or_else(|| AppError::Validation("unknown image model".into()))?;
        validate_model_for_task(model, &input.task)?;
        if input.task != "denoise" && input.output_scale > model.native_scale {
            return Err(AppError::Validation(format!(
                "the selected model supports output scales up to {}×",
                model.native_scale
            )));
        }
        if !model.installed {
            return Err(AppError::Validation(
                "download and verify the selected model first".into(),
            ));
        }
        (
            model.installed_path.clone().unwrap_or_default(),
            Some(model),
        )
    };

    let face_path = if input.enable_face_model {
        let face = selected_model
            .and_then(|model| {
                catalog.models.iter().find(|candidate| {
                    candidate.model_id == model.pair_with
                        && candidate.purposes.iter().any(|p| p == "face")
                })
            })
            .ok_or_else(|| {
                AppError::Validation(
                    "the selected checkpoint has no compatible face companion".into(),
                )
            })?;
        if !face.installed {
            return Err(AppError::Validation(
                "import the verified face checkpoint before enabling face-aware processing".into(),
            ));
        }
        if selected_model.is_some_and(|primary| primary.native_scale != face.native_scale) {
            return Err(AppError::Validation(
                "the face companion native scale does not match the selected upscaler".into(),
            ));
        }
        face.installed_path.clone()
    } else {
        None
    };
    let mut preprocess = Vec::with_capacity(input.fix_stages.len());
    for selection in &input.fix_stages {
        let model = catalog
            .models
            .iter()
            .find(|model| model.model_id == selection.model_id)
            .ok_or_else(|| {
                AppError::Validation(format!("choose a model for the {} fix", selection.fix))
            })?;
        if model.native_scale != 1
            || model.purposes.iter().any(|purpose| purpose == "face")
            || !model.fixes.contains(&selection.fix)
        {
            return Err(AppError::Validation(format!(
                "the {} fix must use a 1× restoration model that fixes it",
                selection.fix
            )));
        }
        if !model.installed || model.installed_path.as_deref().is_none_or(str::is_empty) {
            return Err(AppError::Validation(
                "download and verify every selected fix model first".into(),
            ));
        }
        preprocess.push(ResolvedImageStage {
            // As the plan card names it (state.ts resolvePlan).
            kind: if model.stage == "deblock" || selection.fix == "jpeg" {
                "deblock"
            } else {
                "restore"
            },
            fix: selection.fix.clone(),
            model_id: model.model_id.clone(),
            path: model.installed_path.clone().unwrap_or_default(),
        });
    }
    if input.task == "denoise" && !preprocess.is_empty() {
        if preprocess
            .last()
            .is_none_or(|stage| stage.model_id != input.model_id)
        {
            // Restore's model is its last fix; the fixes before it run first.
            return Err(AppError::Validation(
                "Restore must use the last fix as its model.".into(),
            ));
        }
        preprocess.pop();
    }
    Ok(ModelSelection::Image {
        model_id: input.model_id.clone(),
        path,
        preprocess,
        face_path,
        face_model_id: selected_model
            .map(|model| model.pair_with.clone())
            .unwrap_or_default(),
        native_scale: selected_model
            .map(|model| model.native_scale)
            .unwrap_or(input.output_scale),
        interpolation: resolve_interpolation(&catalog, input)?,
    })
}

fn resolve_interpolation(
    catalog: &crate::types::CatalogManifest,
    input: &StartBatchInput,
) -> AppResult<Option<ResolvedInterpolation>> {
    if input.task != "video" || input.video_interpolation == "off" {
        return Ok(None);
    }
    let model = catalog.interpolation_models.first().ok_or_else(|| {
        AppError::Validation("this build has no frame interpolation model".into())
    })?;
    if !model.installed {
        return Err(AppError::Validation(
            "download the frame interpolation model first".into(),
        ));
    }
    Ok(Some(ResolvedInterpolation {
        model_id: model.model_id.clone(),
        bundle_dir: model.installed_path.clone().unwrap_or_default(),
    }))
}

fn validate_model_for_task(model: &CatalogModel, task: &str) -> AppResult<()> {
    if model.purposes.iter().any(|purpose| purpose == "face") {
        return Err(AppError::Validation(
            "face checkpoints cannot be used as the primary model".into(),
        ));
    }
    match task {
        "upscale" | "video" if model.native_scale <= 1 => Err(AppError::Validation(
            "choose an upscaling model for this task".into(),
        )),
        "denoise" if model.native_scale != 1 => Err(AppError::Validation(
            "choose a restoration or denoising model for this task".into(),
        )),
        _ => Ok(()),
    }
}

fn build_job_message(
    job_id: &str,
    media: &crate::types::MediaItem,
    output: &Path,
    scratch_directory: &Path,
    input: &StartBatchInput,
    selection: &ModelSelection,
) -> AppResult<Value> {
    let output = output.to_string_lossy().into_owned();
    match selection {
        ModelSelection::Image {
            model_id,
            path,
            preprocess,
            face_path,
            face_model_id,
            native_scale,
            ..
        } if input.task != "video" => Ok(json!({
            "type": "job_request",
            "data": {
                "job_id": job_id,
                "image_path": media.path,
                "model_path": path,
                "output_path": output,
                "scratch_directory": scratch_directory.to_string_lossy(),
                "output_format": input.output_format,
                "device": input.device,
                "tile_size": input.tile_size,
                "halo": input.halo,
                "precision": input.precision,
                "jpeg_quality": input.jpeg_quality,
                "preserve_metadata": input.preserve_metadata,
                "safe_memory": input.safe_memory,
                "neural_engine": input.neural_engine,
                "preview_enabled": input.enable_live_preview,
                "preview_max_fps": 2.0,
                "preview_max_dimension": 320,
                "output_scale": if input.task == "denoise" { 1 } else { input.output_scale.min(*native_scale).max(1) },
                "face_model_path": face_path,
                "face_fidelity": f64::from(input.face_fidelity) / 100.0,
                "stages": image_pipeline_stages(
                    preprocess,
                    model_id,
                    path,
                    face_path.as_deref(),
                    face_model_id,
                    input,
                ),
                "allow_unverified_checkpoint": input.allow_unsafe_pickle_model
            }
        })),
        ModelSelection::Image {
            path,
            face_path,
            interpolation,
            ..
        } => {
            if interpolation.is_some()
                && input.video_hdr_mode == "preserve"
                && !media.hdr_format.is_empty()
            {
                return Err(AppError::Validation(format!(
                    "Frame interpolation is not yet available with HDR preservation. Choose Convert to SDR for {}, or turn interpolation off.",
                    media.name
                )));
            }
            Ok(json!({
                "type": "video_job_request",
                "data": {
                    "job_id": job_id,
                    "video_path": media.path,
                    "hdr_mode": input.video_hdr_mode,
                    "model_path": path,
                    "output_video_path": output,
                    "container": input.video_container,
                    "video_codec": input.video_codec,
                    "external_ffmpeg": input.external_ffmpeg_path,
                    "crf": input.video_crf,
                    "fps": null,
                    "device": input.device,
                    "tile_size": input.tile_size,
                    "halo": input.halo,
                    "precision": input.precision,
                    "safe_memory": input.safe_memory,
                    "neural_engine": input.neural_engine,
                    "keyframe_interval": null,
                    "start_frame": null,
                    "end_frame": null,
                    "face_model_path": face_path,
                    "face_fidelity": f64::from(input.face_fidelity) / 100.0,
                    "deflicker": input.deflicker,
                    "deflicker_window": input.deflicker_window,
                    "model_kind": "spandrel_image",
                    "bundle_dir": null,
                    "temporal_window": 0,
                    "temporal_overlap": 0,
                    "target_resolution": 0,
                    "output_scale": input.output_scale,
                    "allow_unverified_checkpoint": input.allow_unsafe_pickle_model,
                    "preview_enabled": input.enable_live_preview,
                    "preview_max_fps": 2.0,
                    "preview_max_dimension": 320,
                    "interpolation": if interpolation.is_some() { input.video_interpolation.as_str() } else { "off" },
                    "interpolation_order": input.video_interpolation_order,
                    "interpolation_model_id": interpolation.as_ref().map(|resolved| resolved.model_id.clone()),
                    "interpolation_bundle_dir": interpolation.as_ref().map(|resolved| resolved.bundle_dir.clone())
                }
            }))
        }
        ModelSelection::Temporal {
            model_id,
            engine_kind,
            bundle_dir,
            temporal_window,
            temporal_overlap,
        } => {
            let shortest = u64::from(media.width.min(media.height));
            let target = if input.video_target_resolution > 0 {
                u64::from(input.video_target_resolution)
            } else {
                shortest
                    .saturating_mul(u64::from(input.output_scale))
                    .min(u64::from(u32::MAX))
            };
            Ok(json!({
                "type": "video_job_request",
                "data": {
                    "job_id": job_id,
                    "video_path": media.path,
                    "hdr_mode": input.video_hdr_mode,
                    "model_path": "",
                    "output_video_path": output,
                    "container": input.video_container,
                    "video_codec": input.video_codec,
                    "external_ffmpeg": input.external_ffmpeg_path,
                    "crf": input.video_crf,
                    "fps": null,
                    "device": input.device,
                    "tile_size": input.tile_size,
                    "halo": input.halo,
                    "precision": input.precision,
                    "safe_memory": input.safe_memory,
                    "neural_engine": input.neural_engine,
                    "keyframe_interval": null,
                    "start_frame": null,
                    "end_frame": null,
                    "face_model_path": null,
                    "deflicker": false,
                    "deflicker_window": input.deflicker_window,
                    "model_kind": engine_kind,
                    "video_model_id": model_id,
                    "bundle_dir": bundle_dir,
                    "temporal_window": temporal_window,
                    "temporal_overlap": temporal_overlap,
                    "target_resolution": target,
                    "video_low_memory": input.video_low_memory,
                    "output_scale": input.output_scale,
                    "allow_unverified_checkpoint": false,
                    "preview_enabled": input.enable_live_preview,
                    "preview_max_fps": 2.0,
                    "preview_max_dimension": 320,
                    "interpolation": "off"
                }
            }))
        }
    }
}

fn image_pipeline_stages(
    preprocess: &[ResolvedImageStage],
    model_id: &str,
    model_path: &str,
    face_path: Option<&str>,
    face_model_id: &str,
    input: &StartBatchInput,
) -> Vec<Value> {
    let mut stages = Vec::with_capacity(5);
    for stage in preprocess {
        stages.push(json!({
            "kind": stage.kind,
            "role": "fix",
            "fix": stage.fix,
            "model_id": stage.model_id,
            "model_path": stage.path,
        }));
    }
    stages.push(json!({
        "kind": if input.task == "denoise" { "restore" } else { "upscale" },
        "role": "primary",
        "fix": if input.task == "denoise" { input.fix_stages.last().map(|stage| stage.fix.as_str()) } else { None },
        "model_id": model_id,
        "model_path": model_path,
    }));
    if let Some(path) = face_path {
        stages.push(json!({
            "kind": "face_restore",
            "model_id": face_model_id,
            "model_path": path,
            "fidelity": f64::from(input.face_fidelity) / 100.0,
            "execution": "fused-with-upscale",
        }));
    }
    stages
}

fn prepare_output_directory(state: &AppState, requested: &str) -> AppResult<PathBuf> {
    let path = if requested.trim().is_empty() {
        state.paths.default_output.clone()
    } else {
        PathBuf::from(requested)
    };
    fs::create_dir_all(&path)?;
    let canonical = fs::canonicalize(path)?;
    if !canonical.is_dir() {
        return Err(AppError::Validation(
            "output path is not a directory".into(),
        ));
    }
    Ok(canonical)
}

fn unique_output_path(
    directory: &Path,
    input_path: &str,
    input: &StartBatchInput,
    reserved: &mut ReservedOutputs,
) -> PathBuf {
    let stem = Path::new(input_path)
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("output");
    let mut suffix = match input.task.as_str() {
        "denoise" => "_denoised".to_owned(),
        "video"
            if input.video_model_id != "frame_by_frame" && input.video_target_resolution > 0 =>
        {
            format!("_video_{}px", input.video_target_resolution)
        }
        "video" => format!("_video_{}x", input.output_scale),
        _ => format!("_upscaled_{}x", input.output_scale),
    };
    if input.name_by_model && input.task != "video" {
        suffix.push('_');
        suffix.push_str(&model_slug(&input.model_id));
    }
    let extension = if input.task == "video" {
        input.video_container.as_str()
    } else {
        input.output_format.as_str()
    };
    let mut counter = 0_u32;
    loop {
        let numbered = if counter == 0 {
            String::new()
        } else {
            format!("_{counter}")
        };
        let ending = format!("{suffix}{numbered}.{extension}");
        let stem = fit_stem(stem, MAX_FILE_NAME_BYTES.saturating_sub(ending.len()));
        let candidate = directory.join(format!("{stem}{ending}"));
        if !candidate.exists() && reserved.insert(&candidate) {
            return candidate;
        }
        counter = counter.saturating_add(1);
    }
}

/// The longest file name file systems accept (APFS, ext4, NTFS and exFAT all allow 255
/// bytes or characters). A name in UTF-8 that fits is short enough for each.
const MAX_FILE_NAME_BYTES: usize = 255;

/// `stem` cut to at most `limit` bytes, on a character boundary.
fn fit_stem(stem: &str, limit: usize) -> &str {
    let mut end = limit.min(stem.len());
    while !stem.is_char_boundary(end) {
        end -= 1;
    }
    &stem[..end]
}

fn model_slug(id: &str) -> String {
    let mut slug = String::new();
    for character in id.chars() {
        if character.is_ascii_alphanumeric() {
            slug.push(character.to_ascii_lowercase());
        } else if !slug.is_empty() && !slug.ends_with('-') {
            slug.push('-');
        }
        if slug.len() == 24 {
            break;
        }
    }
    let slug = slug.trim_end_matches('-');
    if slug.is_empty() {
        "model".into()
    } else {
        slug.into()
    }
}

fn media_kind(path: &Path) -> Option<&'static str> {
    let extension = extension(path)?;
    if IMAGE_EXTENSIONS.contains(&extension.as_str())
        || SYSTEM_IMAGE_EXTENSIONS.contains(&extension.as_str())
    {
        Some("image")
    } else if VIDEO_EXTENSIONS.contains(&extension.as_str()) {
        Some("video")
    } else {
        None
    }
}

fn normalize_media_paths(paths: Vec<String>) -> AppResult<Vec<(PathBuf, &'static str)>> {
    let mut normalized = Vec::new();
    let mut seen = HashSet::new();
    for requested in paths {
        let canonical = fs::canonicalize(&requested)
            .map_err(|_| AppError::Validation(format!("media path is unavailable: {requested}")))?;
        if canonical.is_file() {
            let kind = media_kind(&canonical)
                .ok_or_else(|| AppError::Validation("unsupported media file type".into()))?;
            push_normalized_media(&mut normalized, &mut seen, canonical, kind)?;
            continue;
        }
        if !canonical.is_dir() {
            return Err(AppError::Validation(format!(
                "media selection is not a file or directory: {requested}"
            )));
        }

        let mut children = fs::read_dir(&canonical)?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| path.is_file() && media_kind(path).is_some())
            .collect::<Vec<_>>();
        children.sort_by_key(|path| path.to_string_lossy().to_lowercase());
        for child in children {
            let child = fs::canonicalize(child)?;
            if let Some(kind) = media_kind(&child) {
                push_normalized_media(&mut normalized, &mut seen, child, kind)?;
            }
        }
    }
    Ok(normalized)
}

fn push_normalized_media(
    normalized: &mut Vec<(PathBuf, &'static str)>,
    seen: &mut HashSet<PathBuf>,
    path: PathBuf,
    kind: &'static str,
) -> AppResult<()> {
    if !seen.insert(path.clone()) {
        return Ok(());
    }
    if normalized.len() >= MAX_MEDIA_ITEMS {
        return Err(AppError::Validation(
            "too many media files were selected".into(),
        ));
    }
    normalized.push((path, kind));
    Ok(())
}

fn extension(path: &Path) -> Option<String> {
    path.extension()
        .and_then(|value| value.to_str())
        .map(|value| value.to_ascii_lowercase())
}

fn canonical_directory(path: &str) -> AppResult<PathBuf> {
    let canonical = fs::canonicalize(path)
        .map_err(|_| AppError::Validation("selected directory is unavailable".into()))?;
    if !canonical.is_dir() {
        return Err(AppError::Validation(
            "selected path is not a directory".into(),
        ));
    }
    Ok(canonical)
}

/// The file types LocalSR writes as results. Open hands the file to the system's
/// default handler, so anything else (an executable, a script, a shortcut) must
/// never be launched through it, whatever the history says.
fn is_openable_output(path: &Path) -> bool {
    const OUTPUT_EXTENSIONS: &[&str] = &[
        "png", "jpg", "jpeg", "tif", "tiff", "webp", "heic", "mp4", "mkv", "mov",
    ];
    extension(path).is_some_and(|extension| OUTPUT_EXTENSIONS.contains(&extension.as_str()))
}

/// Open and Reveal act on the selected item's result, which is often not the
/// most recent output (a finished batch, or a result from an earlier session),
/// so any completed job's output is allowed alongside the last one.
fn authorized_result(state: &AppState, requested: &str) -> AppResult<PathBuf> {
    let recorded = lock(&state.database)?.is_completed_output(requested)?;
    let last_output = lock(&state.runtime)?.last_output_path.clone();
    if !recorded && last_output.is_empty() {
        return Err(AppError::Validation("there is no completed output".into()));
    }
    // A result is a file the engine wrote. One that has since become a link could
    // lead anywhere, and canonicalizing would authorize its target.
    if fs::symlink_metadata(requested)?.file_type().is_symlink() {
        return Err(AppError::Validation(
            "output path was not authorized".into(),
        ));
    }
    let requested = fs::canonicalize(requested)?;
    let is_last_output = !last_output.is_empty()
        && fs::canonicalize(last_output)
            .map(|expected| expected == requested)
            .unwrap_or(false);
    if !(recorded || is_last_output) || !requested.is_file() {
        return Err(AppError::Validation(
            "output path was not authorized".into(),
        ));
    }
    Ok(requested)
}

#[tauri::command]
pub fn open_model_license(
    state: State<'_, Arc<AppState>>,
    model_id: String,
    source: Option<bool>,
) -> AppResult<()> {
    let catalog = lock(&state.catalog)?;
    let model = catalog
        .models
        .iter()
        .find(|model| model.model_id == model_id)
        .ok_or_else(|| AppError::Validation("Unknown model".into()))?;
    let url = if source.unwrap_or(false) || model.license_url.is_empty() {
        &model.source_url
    } else {
        &model.license_url
    };
    if !url.starts_with("https://") {
        return Err(AppError::Validation(
            "No HTTPS license link is available".into(),
        ));
    }
    open::that(url)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::paths::AppPaths;
    use crate::types::RecipeStage;

    #[test]
    fn the_download_page_is_the_one_the_release_publishes() {
        let release: serde_json::Value =
            serde_json::from_str(include_str!("../../../ci/public-beta-release.json")).unwrap();
        assert_eq!(release["download_page"], DOWNLOAD_PAGE_URL);
    }

    #[test]
    fn multi_fix_requires_a_capable_engine_before_queueing() {
        let mut options = input("upscale");
        options.fix_stages = serde_json::from_value(json!([
            {"fix":"blur","model_id":"blur"}, {"fix":"noise","model_id":"noise"}
        ]))
        .unwrap();
        let mut engine: crate::types::EngineInfo = serde_json::from_value(json!({
            "protocol_version":1,"minimum_protocol_version":1,"engine_id":"test","engine_version":"test",
            "features":["image"],"model_formats":[],"video_engines":[]
        })).unwrap();
        assert!(validate_fix_engine(&options, Some(&engine)).is_err());
        engine.features.push("image_multi_fix".into());
        assert!(validate_fix_engine(&options, Some(&engine)).is_ok());
    }

    #[test]
    fn multi_fix_serialization_preserves_five_stages_and_restore_primary_role() {
        let prefix: Vec<_> = [
            ("blur", "restore"),
            ("jpeg", "deblock"),
            ("noise", "restore"),
        ]
        .iter()
        .map(|(fix, kind)| ResolvedImageStage {
            kind,
            fix: (*fix).into(),
            model_id: (*fix).into(),
            path: format!("/models/{fix}"),
        })
        .collect();
        let mut options = input("upscale");
        let stages = image_pipeline_stages(
            &prefix,
            "main",
            "/models/main",
            Some("/models/face"),
            "face",
            &options,
        );
        assert_eq!(stages.len(), 5);
        assert_eq!(
            stages
                .iter()
                .map(|stage| stage["model_id"].as_str().unwrap())
                .collect::<Vec<_>>(),
            ["blur", "jpeg", "noise", "main", "face"]
        );
        assert_eq!(stages[3]["role"], "primary");
        options.task = "denoise".into();
        options.fix_stages =
            serde_json::from_value(json!([{"fix":"noise","model_id":"noise"}])).unwrap();
        let stages =
            image_pipeline_stages(&prefix[..2], "noise", "/models/noise", None, "", &options);
        assert_eq!(stages.len(), 3);
        assert_eq!(stages[2]["role"], "primary");
        assert_eq!(stages[2]["fix"], "noise");
    }

    #[test]
    fn multi_fix_host_resolves_every_catalog_stage_and_rejects_invalid_choices() {
        use crate::types::FixStageSelection;
        let directory = tempfile::tempdir().unwrap();
        let paths = crate::paths::AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::create_dir_all(&paths.model_root).unwrap();
        let state = AppState::from_paths(paths).unwrap();
        {
            let mut catalog = lock(&state.catalog).unwrap();
            for model in &mut catalog.models {
                model.installed = true;
                model.installed_path = Some(format!("/models/{}", model.filename));
            }
        }
        let mut options = input("upscale");
        options.model_id = "hat_s_x4".into();
        options.fix_stages = [
            ("blur", "nafnet_gopro_deblur"),
            ("jpeg", "fbcnn_color"),
            ("noise", "scunet_color_real_psnr"),
        ]
        .iter()
        .map(|(fix, id)| FixStageSelection {
            fix: (*fix).into(),
            model_id: (*id).into(),
        })
        .collect();
        match resolve_model_selection(&state, &options).unwrap() {
            ModelSelection::Image { preprocess, .. } => {
                assert_eq!(
                    preprocess
                        .iter()
                        .map(|stage| stage.model_id.as_str())
                        .collect::<Vec<_>>(),
                    [
                        "nafnet_gopro_deblur",
                        "fbcnn_color",
                        "scunet_color_real_psnr"
                    ]
                );
                assert_eq!(preprocess[1].kind, "deblock");
                assert!(preprocess
                    .iter()
                    .all(|stage| stage.path.starts_with("/models/")));
            }
            _ => panic!("expected image plan"),
        }
        let valid = options.fix_stages.clone();
        for id in ["unknown", "hat_s_x4", "hat_s_x4_face", "fbcnn_color"] {
            options.fix_stages[0].model_id = id.into();
            assert!(
                resolve_model_selection(&state, &options).is_err(),
                "accepted {id} as blur"
            );
        }
        options.fix_stages = valid.clone();
        options.fix_stages[1].fix = "blur".into();
        assert!(resolve_model_selection(&state, &options).is_err());
        options.fix_stages = valid.clone();
        lock(&state.catalog)
            .unwrap()
            .models
            .iter_mut()
            .find(|m| m.model_id == "fbcnn_color")
            .unwrap()
            .installed = false;
        assert!(resolve_model_selection(&state, &options).is_err());
        lock(&state.catalog)
            .unwrap()
            .models
            .iter_mut()
            .find(|m| m.model_id == "fbcnn_color")
            .unwrap()
            .installed = true;
        options.task = "denoise".into();
        options.model_id = "scunet_color_real_psnr".into();
        match resolve_model_selection(&state, &options).unwrap() {
            ModelSelection::Image { preprocess, .. } => assert_eq!(preprocess.len(), 2),
            _ => panic!("expected restore plan"),
        }
        options.model_id = "fbcnn_color".into();
        assert!(
            resolve_model_selection(&state, &options).is_err(),
            "Restore primary must be the final fix"
        );
        options.task = "video".into();
        for engine in ["frame_by_frame", "seedvr2_3b"] {
            options.video_model_id = engine.into();
            assert!(resolve_model_selection(&state, &options).is_err());
        }
    }

    #[test]
    fn multi_fix_recipe_validation_accepts_bounded_orders_for_both_image_tasks() {
        let base = serde_json::json!({"id":"r", "name":"r", "task":"upscale", "model_id":"main", "output_scale":4, "tile_size":128, "halo":16, "precision":"fp32", "safe_memory":true});
        let mut recipe: Recipe = serde_json::from_value(base).unwrap();
        for task in ["upscale", "denoise"] {
            recipe.task = task.into();
            recipe.stages = serde_json::from_value(serde_json::json!([
                {"kind":"restore","model_id":"blur","fix":"blur"},
                {"kind":"deblock","model_id":"jpeg","fix":"jpeg"},
                {"kind":"restore","model_id":"noise","fix":"noise"}
            ]))
            .unwrap();
            if task == "upscale" {
                recipe.stages.extend(serde_json::from_value::<Vec<RecipeStage>>(serde_json::json!([
                    {"kind":"upscale","model_id":"main"}, {"kind":"face_restore","model_id":"face"}
                ])).unwrap());
            }
            assert!(validate_recipe_stages(&recipe).is_ok());
            let good = recipe.stages.clone();
            recipe.stages.insert(0, good[0].clone());
            assert!(validate_recipe_stages(&recipe).is_err());
            recipe.stages = good;
            recipe.stages.swap(0, 2);
            assert!(validate_recipe_stages(&recipe).is_ok());
        }
    }

    #[test]
    fn a_restore_recipe_ends_with_its_restore_model_and_only_restorations_name_a_fix() {
        let base = serde_json::json!({"id":"r", "name":"r", "task":"denoise", "model_id":"main", "output_scale":1, "tile_size":128, "halo":16, "precision":"fp32", "safe_memory":true});
        let mut recipe: Recipe = serde_json::from_value(base).unwrap();
        let stages = |value: Value| serde_json::from_value::<Vec<RecipeStage>>(value).unwrap();
        // A 0.3.5 Restore recipe: one restore stage, no fix named.
        recipe.stages = stages(json!([{"kind":"restore","model_id":"main"}]));
        assert!(validate_recipe_stages(&recipe).is_ok());
        // The host sends Restore's model as a restore stage, never as a deblock.
        recipe.stages = stages(json!([{"kind":"deblock","model_id":"jpeg","fix":"jpeg"}]));
        assert!(validate_recipe_stages(&recipe).is_err());
        recipe.stages = stages(json!([
            {"kind":"restore","model_id":"a"}, {"kind":"restore","model_id":"b"},
            {"kind":"restore","model_id":"c"}, {"kind":"restore","model_id":"d"},
            {"kind":"restore","model_id":"e"}
        ]));
        assert!(validate_recipe_stages(&recipe).is_err());
        recipe.task = "upscale".into();
        for invalid in [
            json!([{"kind":"upscale","model_id":"main","fix":"noise"}]),
            json!([
                {"kind":"upscale","model_id":"main"},
                {"kind":"face_restore","model_id":"face","fix":"blur"}
            ]),
        ] {
            recipe.stages = stages(invalid);
            assert!(validate_recipe_stages(&recipe).is_err());
        }
    }

    #[test]
    fn an_update_switch_is_written_at_once_and_rolled_back_when_writing_fails() {
        let directory = tempfile::tempdir().unwrap();
        let paths = crate::paths::AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::create_dir_all(&paths.model_root).unwrap();
        let state = AppState::from_paths(paths).unwrap();
        apply_update_switch(&state, "anonymous_update_count", |_| false).unwrap();
        let (saved, _) = crate::settings::load(&state.paths);
        assert!(!saved.anonymous_update_count);
        // Two flips are two flips.
        apply_update_switch(&state, "check_updates_at_launch", |current| !current).unwrap();
        apply_update_switch(&state, "check_updates_at_launch", |current| !current).unwrap();
        assert!(
            crate::settings::load(&state.paths)
                .0
                .check_updates_at_launch
        );
        assert!(apply_update_switch(&state, "device_id", |_| true).is_err());
        // An unreadable settings file refuses the write; memory goes back to match the disk.
        fs::write(&state.paths.settings, b"{ not json").unwrap();
        assert!(apply_update_switch(&state, "anonymous_update_count", |_| true).is_err());
        assert!(!lock(&state.settings).unwrap().anonymous_update_count);
    }

    #[test]
    fn a_full_settings_save_never_changes_the_update_switches() {
        let current = UiSettings {
            anonymous_update_count: false,
            ..UiSettings::default()
        };
        // A window copy from an older refresh still has the count on.
        let mut incoming = UiSettings {
            tile_size: UiSettings::default().tile_size + 64,
            ..UiSettings::default()
        };
        keep_update_switches(&current, &mut incoming);
        assert!(!incoming.anonymous_update_count);
        assert!(incoming.check_updates_at_launch);
        assert_eq!(incoming.tile_size, UiSettings::default().tile_size + 64);
        // Settings saved before 0.3.3 check at launch, as before.
        let mut saved = serde_json::to_value(UiSettings::default()).unwrap();
        saved
            .as_object_mut()
            .unwrap()
            .remove("check_updates_at_launch");
        assert!(
            serde_json::from_value::<UiSettings>(saved)
                .unwrap()
                .check_updates_at_launch
        );
    }

    #[test]
    fn face_companions_require_the_matching_installed_model_and_explicit_selection() {
        let directory = tempfile::tempdir().unwrap();
        let paths = crate::paths::AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::create_dir_all(&paths.model_root).unwrap();
        let state = AppState::from_paths(paths).unwrap();
        for (primary_id, face_id) in [
            ("hat_s_x4", "hat_s_x4_face"),
            ("hat_l_x4_imagenet", "hat_l_x4_face"),
        ] {
            {
                let mut catalog = lock(&state.catalog).unwrap();
                for model in &mut catalog.models {
                    model.installed = model.model_id == primary_id || model.model_id == face_id;
                    model.installed_path = model
                        .installed
                        .then(|| format!("/models/{}", model.filename));
                }
            }
            let mut options = input("upscale");
            options.model_id = primary_id.into();
            options.enable_face_model = false;
            assert!(matches!(
                resolve_model_selection(&state, &options).unwrap(),
                ModelSelection::Image {
                    face_path: None,
                    ..
                }
            ));
            options.enable_face_model = true;
            match resolve_model_selection(&state, &options).unwrap() {
                ModelSelection::Image {
                    face_path,
                    face_model_id,
                    ..
                } => {
                    assert_eq!(face_model_id, face_id);
                    assert!(face_path.unwrap().starts_with("/models/"));
                }
                _ => panic!("expected an image model"),
            }
            {
                let mut catalog = lock(&state.catalog).unwrap();
                catalog
                    .models
                    .iter_mut()
                    .find(|model| model.model_id == face_id)
                    .unwrap()
                    .installed = false;
            }
            let missing = resolve_model_selection(&state, &options).err().unwrap();
            assert!(missing
                .to_string()
                .contains("import the verified face checkpoint"));
            options.model_id = face_id.into();
            assert!(resolve_model_selection(&state, &options).is_err());
        }
    }

    #[test]
    fn revisiting_video_comparisons_keeps_only_explicit_files_authorized() {
        let app = tauri::test::mock_app();
        let scope = tauri::scope::fs::Scope::new(
            &app,
            &tauri::utils::config::FsScope::AllowedPaths(Vec::new()),
        )
        .unwrap();
        let directory = tempfile::tempdir().unwrap();
        let files: Vec<_> = [
            "first.mov",
            "first-output.mp4",
            "second-output.mp4",
            "private.mp4",
        ]
        .iter()
        .map(|name| {
            let path = directory.path().join(name);
            fs::write(&path, b"test media").unwrap();
            fs::canonicalize(path).unwrap()
        })
        .collect();
        // Open A, start a job using A's output, inspect B, then return to A.
        for (original, enhanced) in [(0, 1), (1, 2), (0, 1), (1, 2)] {
            allow_video_preview_pair(&scope, &files[original], Some(&files[enhanced])).unwrap();
            assert!(scope.is_allowed(&files[original]));
            assert!(scope.is_allowed(&files[enhanced]));
            assert!(!scope.is_allowed(&files[3]));
            assert!(!scope.is_allowed(directory.path()));
        }
        // An unfinished video authorizes only its source.
        allow_video_preview_pair(&scope, &files[3], None).unwrap();
        assert!(scope.is_allowed(&files[3]));
        assert!(!scope.is_allowed(directory.path()));
        assert!(scope.forbidden_patterns().is_empty());
    }

    fn input(task: &str) -> StartBatchInput {
        StartBatchInput {
            name_by_model: false,
            ui: None,
            media_ids: vec!["media".into()],
            batch_mode: false,
            task: task.into(),
            model_id: "model".into(),
            video_model_id: "frame_by_frame".into(),
            fix_stages: Vec::new(),
            custom_model_path: String::new(),
            output_directory: String::new(),
            output_format: "png".into(),
            output_scale: 4,
            preserve_metadata: true,
            jpeg_quality: 98,
            device: "cpu".into(),
            tile_size: 256,
            halo: 32,
            precision: "fp32".into(),
            safe_memory: false,
            deflicker: false,
            deflicker_window: 3,
            video_container: "mp4".into(),
            video_codec: "av1".into(),
            external_ffmpeg_path: String::new(),
            video_hdr_mode: "tone_map".into(),
            video_target_resolution: 0,
            video_low_memory: true,
            video_crf: 18,
            neural_engine: true,
            enable_face_model: false,
            face_fidelity: 70,
            enable_live_preview: true,
            allow_unsafe_pickle_model: false,
            video_interpolation: "off".into(),
            video_interpolation_order: "after_upscale".into(),
        }
    }

    /// An app state with one ready image and `count` installed catalog models
    /// matching `pick`, all pointing at a real file.
    fn trial_state(
        directory: &Path,
        count: usize,
        pick: impl Fn(&CatalogModel) -> bool,
    ) -> (AppState, Vec<String>) {
        let state = AppState::from_paths(crate::paths::AppPaths::under(directory)).unwrap();
        let image = directory.join("photo.png");
        fs::write(&image, "source").unwrap();
        {
            let db = lock(&state.database).unwrap();
            db.insert_media("media", image.to_str().unwrap(), "photo.png", "image")
                .unwrap();
            db.update_media_probe(
                image.to_str().unwrap(),
                "image",
                100,
                80,
                1,
                0.0,
                0.0,
                "preview",
                "",
                "",
            )
            .unwrap();
        }
        let ids: Vec<String> = {
            let mut catalog = lock(&state.catalog).unwrap();
            catalog
                .models
                .iter_mut()
                .filter(|m| pick(m))
                .take(count)
                .map(|m| {
                    m.installed = true;
                    m.installed_path = Some(image.to_string_lossy().into_owned());
                    m.model_id.clone()
                })
                .collect()
        };
        assert_eq!(ids.len(), count, "the bundled catalog has enough models");
        (state, ids)
    }

    fn upscaler(model: &CatalogModel) -> bool {
        model.native_scale == 4 && !model.purposes.iter().any(|p| p == "face")
    }

    fn no_jobs(state: &AppState) -> bool {
        lock(&state.database)
            .unwrap()
            .list_jobs()
            .unwrap()
            .is_empty()
    }

    #[test]
    fn model_trials_validate_every_candidate_before_admitting_any() {
        let directory = tempfile::tempdir().unwrap();
        let (state, ids) = trial_state(directory.path(), 2, upscaler);
        let mut base = input("upscale");
        base.output_directory = directory.path().to_string_lossy().into_owned();
        for invalid in [
            vec![ids[0].clone()],
            vec![ids[0].clone(), ids[0].clone()],
            vec![ids[0].clone(), "missing".into()],
            vec![ids[0].clone(), "__custom__".into()],
            vec![
                ids[0].clone(),
                ids[1].clone(),
                "a".into(),
                "b".into(),
                "c".into(),
            ],
        ] {
            assert!(admit_model_trials(&state, "media", &invalid, &base).is_err());
            assert!(no_jobs(&state));
        }
        let mut batch = base.clone();
        batch.batch_mode = true;
        let mut video = base.clone();
        video.task = "video".into();
        let mut other_media = base.clone();
        other_media.media_ids = vec!["elsewhere".into()];
        // Faces, a fix stage and a scale no candidate can keep.
        let mut faces = base.clone();
        faces.enable_face_model = true;
        let mut fix = base.clone();
        fix.fix_stages = vec![crate::types::FixStageSelection {
            fix: "blur".into(),
            model_id: "missing-fix".into(),
        }];
        let mut scale = base.clone();
        scale.output_scale = 8;
        for refused in [batch, video, other_media, faces, fix, scale] {
            assert!(admit_model_trials(&state, "media", &ids, &refused).is_err());
            assert!(no_jobs(&state));
        }
        // The second checkpoint vanished: the first is not queued alone.
        lock(&state.catalog)
            .unwrap()
            .models
            .iter_mut()
            .find(|m| m.model_id == ids[1])
            .unwrap()
            .installed_path = Some("/missing-checkpoint.pth".into());
        assert!(admit_model_trials(&state, "media", &ids, &base).is_err());
        assert!(no_jobs(&state));
    }

    #[test]
    fn model_trials_queue_ordinary_jobs_in_order_named_by_model() {
        let directory = tempfile::tempdir().unwrap();
        let (state, ids) = trial_state(directory.path(), 3, upscaler);
        let mut base = input("upscale");
        base.output_directory = directory.path().to_string_lossy().into_owned();
        base.model_id = ids[0].clone();
        base.halo = 0;
        base.ui = Some(ResultUi {
            quality: "best".into(),
            content: "photo".into(),
            recipe_name: "Mine".into(),
            fixes: vec![],
        });
        let jobs = admit_model_trials(&state, "media", &ids, &base).unwrap();
        assert_eq!(jobs.len(), 3);
        let db = lock(&state.database).unwrap();
        assert_eq!(db.next_queued_job().unwrap().unwrap().id, jobs[0]);
        // Snapshot order is newest first; the queue runs the reverse.
        let queued: Vec<String> = db
            .list_jobs()
            .unwrap()
            .into_iter()
            .rev()
            .map(|j| j.id)
            .collect();
        assert_eq!(queued, jobs);
        let requests = db.unfinished_requests().unwrap();
        let catalog = lock(&state.catalog).unwrap();
        for request in &requests {
            let data = &request["data"];
            assert!(data.get("ui").is_none() && data.get("name_by_model").is_none());
            let model_id = data["stages"][0]["model_id"].as_str().unwrap();
            let output = data["output_path"].as_str().unwrap();
            assert!(
                output.contains(&format!("_upscaled_4x_{}", model_slug(model_id))),
                "{output}"
            );
            let model = catalog
                .models
                .iter()
                .find(|m| m.model_id == model_id)
                .unwrap();
            assert!(data["halo"].as_u64().unwrap() >= u64::from(model.recommended_halo));
        }
        drop(catalog);
        drop(db);
        // The image now has work: a second submission is refused whole.
        assert!(admit_model_trials(&state, "media", &ids[..2], &base).is_err());
        assert_eq!(lock(&state.database).unwrap().list_jobs().unwrap().len(), 3);
        // The current model keeps its labels; another model reads Custom.
        let db = lock(&state.database).unwrap();
        for id in &jobs {
            db.finish_job(id, "failed", "", "stopped").unwrap();
        }
        let history = db.list_media_results("media", 50, None).unwrap();
        let ui_of = |job: &str| {
            history
                .results
                .iter()
                .find(|r| r.job_id == job)
                .and_then(|r| r.ui.clone())
                .unwrap()
        };
        assert_eq!(
            (ui_of(&jobs[0]).quality, ui_of(&jobs[0]).recipe_name),
            ("best".to_owned(), "Mine".to_owned())
        );
        assert_eq!(
            (ui_of(&jobs[1]).quality, ui_of(&jobs[1]).recipe_name),
            ("custom".to_owned(), String::new())
        );
        assert_eq!(ui_of(&jobs[1]).content, "photo");
    }

    #[test]
    fn restore_trials_need_models_that_fix_the_selected_problem() {
        let directory = tempfile::tempdir().unwrap();
        let restorer =
            |m: &CatalogModel| m.native_scale == 1 && !m.purposes.iter().any(|p| p == "face");
        let (state, ids) = trial_state(directory.path(), 2, restorer);
        let catalog = lock(&state.catalog).unwrap().clone();
        let fixes =
            |id: &str| model_fixes(catalog.models.iter().find(|m| m.model_id == id).unwrap());
        let mut base = input("denoise");
        base.output_scale = 1;
        base.output_directory = directory.path().to_string_lossy().into_owned();
        // A problem one of the models does not fix refuses the whole trial.
        let missing = ["noise", "blur", "jpeg"]
            .into_iter()
            .find(|fix| !fixes(&ids[0]).iter().any(|f| f == fix))
            .expect("a restorer that does not fix everything");
        base.fix_stages = vec![crate::types::FixStageSelection {
            fix: missing.into(),
            model_id: ids[1].clone(),
        }];
        assert!(admit_model_trials(&state, "media", &ids, &base).is_err());
        assert!(no_jobs(&state));
        // Without an active problem both run, named like Restore outputs.
        base.fix_stages.clear();
        base.ui = Some(ResultUi::default());
        admit_model_trials(&state, "media", &ids, &base).unwrap();
        let requests = lock(&state.database)
            .unwrap()
            .unfinished_requests()
            .unwrap();
        assert!(requests.iter().all(|r| r["data"]["output_path"]
            .as_str()
            .unwrap()
            .contains("_denoised_")));
    }

    #[test]
    fn restore_trials_replace_the_last_fix_model_with_each_candidate() {
        let directory = tempfile::tempdir().unwrap();
        let restorer =
            |m: &CatalogModel| m.native_scale == 1 && !m.purposes.iter().any(|p| p == "face");
        let (state, ids) = trial_state(directory.path(), 2, restorer);
        let catalog = lock(&state.catalog).unwrap().clone();
        let fixes =
            |id: &str| model_fixes(catalog.models.iter().find(|m| m.model_id == id).unwrap());
        let Some(common) = ["noise", "blur", "jpeg"]
            .into_iter()
            .find(|fix| ids.iter().all(|id| fixes(id).iter().any(|f| f == fix)))
        else {
            return;
        };
        let mut base = input("denoise");
        base.output_scale = 1;
        base.model_id = ids[0].clone();
        base.output_directory = directory.path().to_string_lossy().into_owned();
        base.fix_stages = vec![crate::types::FixStageSelection {
            fix: common.into(),
            model_id: ids[0].clone(),
        }];
        admit_model_trials(&state, "media", &ids, &base).unwrap();
        let requests = lock(&state.database)
            .unwrap()
            .unfinished_requests()
            .unwrap();
        assert_eq!(requests.len(), 2);
        for id in &ids {
            assert!(requests.iter().any(|r| {
                let stages = r["data"]["stages"].as_array().unwrap();
                stages.len() == 1 && stages[0]["model_id"] == id.as_str()
            }));
        }
    }

    #[test]
    fn a_run_names_only_a_device_the_computer_reports() {
        let directory = tempfile::tempdir().unwrap();
        let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
        lock(&state.capabilities).unwrap().system_ram_total = 8 << 30;
        assert_eq!(resolve_device(&state, "cpu").unwrap(), "cpu");
        let error = resolve_device(&state, "cuda:3").unwrap_err();
        assert!(error.to_string().contains("not available"));
    }

    #[test]
    fn a_saved_device_that_is_gone_runs_on_the_cpu_and_stays_saved() {
        let directory = tempfile::tempdir().unwrap();
        let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
        lock(&state.capabilities).unwrap().system_ram_total = 8 << 30;
        lock(&state.settings).unwrap().device_id = "cuda:1".into();
        assert_eq!(resolve_device(&state, "cuda:1").unwrap(), "cpu");
        assert_eq!(lock(&state.settings).unwrap().device_id, "cuda:1");
        assert!(resolve_device(&state, "cuda:2").is_err());
    }

    #[test]
    fn no_device_is_judged_before_the_hardware_has_been_reported() {
        let directory = tempfile::tempdir().unwrap();
        let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
        lock(&state.settings).unwrap().device_id = "mps".into();
        let error = resolve_device(&state, "mps").unwrap_err();
        assert!(error.to_string().contains("still detecting"));
    }

    #[test]
    fn try_others_runs_a_gone_saved_device_on_the_cpu() {
        let directory = tempfile::tempdir().unwrap();
        let (state, ids) = trial_state(directory.path(), 2, upscaler);
        lock(&state.capabilities).unwrap().system_ram_total = 8 << 30;
        lock(&state.settings).unwrap().device_id = "cuda:1".into();
        let mut base = input("upscale");
        base.device = "cuda:1".into();
        let candidates = trial_candidates(&state, "media", &ids, &base).unwrap();
        assert!(candidates
            .iter()
            .all(|candidate| candidate.input.device == "cpu"));
    }

    #[test]
    fn trial_estimates_check_candidates_without_queueing_or_creating_folders() {
        let directory = tempfile::tempdir().unwrap();
        let (state, ids) = trial_state(directory.path(), 2, upscaler);
        let mut base = input("upscale");
        let output = directory.path().join("not-yet");
        base.output_directory = output.to_string_lossy().into_owned();
        let candidates = trial_candidates(&state, "media", &ids, &base).unwrap();
        assert_eq!(candidates.len(), 2);
        assert!(!output.exists());
        assert!(no_jobs(&state));
    }

    #[test]
    fn a_pair_decodes_each_result_once_and_never_the_original() {
        let directory = tempfile::tempdir().unwrap();
        let db = crate::database::Database::open(&directory.path().join("q.db")).unwrap();
        db.insert_media("photo", "/tmp/photo.png", "photo.png", "image")
            .unwrap();
        db.update_media_probe(
            "/tmp/photo.png",
            "image",
            640,
            480,
            1,
            0.0,
            0.0,
            "thumb",
            "",
            "",
        )
        .unwrap();
        let media = db.get_media("photo").unwrap().unwrap();
        let mut runtime = crate::types::RuntimeStatus::default();
        let sent = |requests: Vec<Value>| -> Vec<(String, String, u64)> {
            requests
                .iter()
                .map(|r| {
                    let data = &r["data"];
                    assert_eq!(data["max_dimension"], worker::RESULT_PREVIEW_MAX_DIMENSION);
                    (
                        data["slot"].as_str().unwrap().to_owned(),
                        data["target_id"].as_str().unwrap().to_owned(),
                        data["generation"].as_u64().unwrap(),
                    )
                })
                .collect()
        };
        // Result against Original: one decode, the Original keeps its size.
        let first = plan_pair_requests(&mut runtime, &media, ("a", "/a.png"), ("original", ""));
        assert_eq!(sent(first), vec![("showing".into(), "a".into(), 1)]);
        assert_eq!(
            (
                runtime.comparison_pair.against.width,
                runtime.comparison_pair.against.height
            ),
            (640, 480)
        );
        // Still loading: asking again sends it again under a new generation.
        let again = plan_pair_requests(&mut runtime, &media, ("a", "/a.png"), ("original", ""));
        assert_eq!(sent(again), vec![("showing".into(), "a".into(), 2)]);
        runtime.comparison_pair.showing.preview_data_url = "data:a".into();
        // Decoded: the same pair needs nothing.
        assert!(
            plan_pair_requests(&mut runtime, &media, ("a", "/a.png"), ("original", "")).is_empty()
        );
        // A second result: only it is decoded; the first moves sides intact.
        let pair = plan_pair_requests(&mut runtime, &media, ("b", "/b.png"), ("a", "/a.png"));
        assert_eq!(sent(pair), vec![("showing".into(), "b".into(), 3)]);
        assert_eq!(runtime.comparison_pair.against.preview_data_url, "data:a");
        runtime.comparison_pair.showing.preview_data_url = "data:b".into();
        // Swapping both decoded sides decodes nothing.
        assert!(
            plan_pair_requests(&mut runtime, &media, ("a", "/a.png"), ("b", "/b.png")).is_empty()
        );
        assert_eq!(runtime.comparison_pair.showing.preview_data_url, "data:a");
        assert_eq!(runtime.comparison_pair.against.preview_data_url, "data:b");
        // Another image never reuses these previews.
        db.insert_media("other", "/tmp/other.png", "other.png", "image")
            .unwrap();
        let other = db.get_media("other").unwrap().unwrap();
        let fresh = plan_pair_requests(&mut runtime, &other, ("a", "/a.png"), ("original", ""));
        assert_eq!(sent(fresh).len(), 1);
        assert!(runtime.comparison_pair.showing.preview_data_url.is_empty());
    }

    #[test]
    fn trial_names_sanitize_models_and_keep_collision_counters() {
        let directory = tempfile::tempdir().unwrap();
        let mut base = input("upscale");
        base.name_by_model = true;
        base.model_id = "../SwinIR_M///".into();
        let mut reserved = ReservedOutputs::default();
        let first = unique_output_path(directory.path(), "/photo.png", &base, &mut reserved);
        assert_eq!(first.file_name().unwrap(), "photo_upscaled_4x_swinir-m.png");
        let second = unique_output_path(directory.path(), "/photo.png", &base, &mut reserved);
        assert_eq!(
            second.file_name().unwrap(),
            "photo_upscaled_4x_swinir-m_1.png"
        );
        assert_eq!(model_slug("///"), "model");
        assert!(model_slug(&"a".repeat(100)).len() <= 24);
    }

    #[test]
    fn pending_output_is_reserved_across_submissions() {
        let directory = tempfile::tempdir().unwrap();
        let db = crate::database::Database::open(&directory.path().join("queue.db")).unwrap();
        db.insert_media("m", "/first/photo.png", "photo.png", "image")
            .unwrap();
        let options = input("upscale");
        let first = unique_output_path(
            directory.path(),
            "/first/photo.png",
            &options,
            &mut ReservedOutputs::default(),
        );
        db.insert_job(
            "j",
            "m",
            &serde_json::json!({"data":{"output_path":first}}).to_string(),
        )
        .unwrap();
        let mut reserved = db.unfinished_output_paths().unwrap().into_iter().collect();
        let second = unique_output_path(
            directory.path(),
            "/second/photo.png",
            &options,
            &mut reserved,
        );
        assert_ne!(first, second, "pending output must be reserved");
    }

    #[test]
    fn output_names_do_not_overwrite_existing_or_batched_files() {
        let directory = tempfile::tempdir().unwrap();
        let options = input("upscale");
        fs::write(directory.path().join("photo_upscaled_4x.png"), b"old").unwrap();
        let mut reserved = ReservedOutputs::default();
        let first = unique_output_path(
            directory.path(),
            "/media/photo.png",
            &options,
            &mut reserved,
        );
        let second = unique_output_path(
            directory.path(),
            "/other/photo.jpg",
            &options,
            &mut reserved,
        );
        assert_eq!(first.file_name().unwrap(), "photo_upscaled_4x_1.png");
        assert_eq!(second.file_name().unwrap(), "photo_upscaled_4x_2.png");
        let mut video = input("video");
        video.video_model_id = "seedvr2_3b".into();
        video.video_target_resolution = 256;
        let smaller =
            unique_output_path(directory.path(), "/media/clip.mov", &video, &mut reserved);
        assert_eq!(smaller.file_name().unwrap(), "clip_video_256px.mp4");
        video.video_model_id = "frame_by_frame".into();
        let tiled = unique_output_path(directory.path(), "/media/clip.mov", &video, &mut reserved);
        assert_eq!(tiled.file_name().unwrap(), "clip_video_4x.mp4");
    }

    #[cfg(unix)]
    #[test]
    fn a_recorded_output_that_became_a_link_is_not_authorized() {
        let directory = tempfile::tempdir().unwrap();
        let state =
            AppState::from_paths(crate::paths::AppPaths::under(&directory.path().join("app")))
                .unwrap();
        let output = directory.path().join("result.png");
        let private = directory.path().join("private.png");
        fs::write(&output, b"result").unwrap();
        fs::write(&private, b"private").unwrap();
        {
            let database = lock(&state.database).unwrap();
            database
                .insert_media("m", "/in/photo.png", "photo.png", "image")
                .unwrap();
            database.insert_job("j", "m", "{}").unwrap();
            database
                .finish_job("j", "completed", &output.to_string_lossy(), "")
                .unwrap();
        }
        let requested = output.to_string_lossy().into_owned();
        assert_eq!(
            authorized_result(&state, &requested).unwrap(),
            fs::canonicalize(&output).unwrap()
        );
        fs::remove_file(&output).unwrap();
        std::os::unix::fs::symlink(&private, &output).unwrap();
        assert!(authorized_result(&state, &requested).is_err());
    }

    #[test]
    fn open_launches_only_the_types_the_app_writes() {
        for name in [
            "result.png",
            "result.JPG",
            "result.tif",
            "result.webp",
            "result.heic",
            "clip.mp4",
            "clip.MKV",
            "clip.mov",
        ] {
            assert!(is_openable_output(Path::new(name)), "{name}");
        }
        for name in [
            "result.exe",
            "result.bat",
            "result.cmd",
            "result.lnk",
            "result.ps1",
            "result.msi",
            "result.scr",
            "result.com",
            "result.js",
            "result.app",
            "result.png.exe",
            "result.exe.",
            "result.exe ",
            "result",
            ".png",
        ] {
            assert!(!is_openable_output(Path::new(name)), "{name}");
        }
    }

    #[cfg(unix)]
    #[test]
    fn importing_a_folder_follows_links_to_media_files_but_never_descends_into_links() {
        use std::os::unix::fs::symlink;
        let directory = tempfile::tempdir().unwrap();
        let chosen = directory.path().join("chosen");
        let elsewhere = directory.path().join("elsewhere");
        fs::create_dir_all(chosen.join("real-folder")).unwrap();
        fs::create_dir_all(&elsewhere).unwrap();
        fs::write(chosen.join("own.png"), b"own").unwrap();
        fs::write(chosen.join("real-folder").join("inner.png"), b"inner").unwrap();
        fs::write(elsewhere.join("linked.jpg"), b"linked").unwrap();
        fs::write(elsewhere.join("nested.jpg"), b"nested").unwrap();
        fs::write(elsewhere.join("notes.txt"), b"text").unwrap();
        // A link to one media file is a layout users keep their photos in.
        symlink(elsewhere.join("linked.jpg"), chosen.join("link.jpg")).unwrap();
        // A link to a folder is not followed, a link that loops cannot hang the scan,
        // and neither can a link named like media that points at something else.
        symlink(&elsewhere, chosen.join("folder-link")).unwrap();
        symlink(&chosen, chosen.join("loop")).unwrap();
        symlink(elsewhere.join("notes.txt"), chosen.join("disguised.png")).unwrap();
        symlink(
            directory.path().join("missing.png"),
            chosen.join("dangling.png"),
        )
        .unwrap();

        let found = normalize_media_paths(vec![chosen.to_string_lossy().into_owned()]).unwrap();
        let names: Vec<String> = found
            .iter()
            .map(|(path, _)| path.file_name().unwrap().to_string_lossy().into_owned())
            .collect();
        assert_eq!(names, ["linked.jpg", "own.png"]);
        let listed = scan_media_folder(chosen.to_string_lossy().into_owned()).unwrap();
        assert!(listed.iter().all(|path| !path.contains("nested")
            && !path.contains("inner")
            && !path.contains("notes")));
    }

    #[test]
    fn names_that_differ_only_by_case_are_one_name() {
        let directory = tempfile::tempdir().unwrap();
        let options = input("upscale");
        let mut reserved = ReservedOutputs::default();
        let first = unique_output_path(directory.path(), "/day1/A.jpg", &options, &mut reserved);
        let second = unique_output_path(directory.path(), "/day2/a.jpg", &options, &mut reserved);
        let third = unique_output_path(directory.path(), "/day3/Ä.jpg", &options, &mut reserved);
        let fourth = unique_output_path(directory.path(), "/day4/ä.jpg", &options, &mut reserved);
        let names: Vec<String> = [&first, &second, &third, &fourth]
            .iter()
            .map(|path| path.to_string_lossy().to_uppercase().to_lowercase())
            .collect();
        let distinct: HashSet<&String> = names.iter().collect();
        assert_eq!(distinct.len(), 4, "{names:?}");
    }

    #[test]
    fn benchmark_export_is_unique_atomic_and_never_clobbers() {
        let directory = tempfile::tempdir().unwrap();
        let destination = directory.path().join("result.json");
        let unrelated = directory.path().join("result.json.tmp");
        fs::write(&unrelated, b"user-owned").unwrap();

        write_benchmark_export(&destination, br#"{"score":42}"#).unwrap();
        assert_eq!(fs::read(&destination).unwrap(), br#"{"score":42}"#);
        assert_eq!(fs::read(&unrelated).unwrap(), b"user-owned");
        assert!(directory.path().read_dir().unwrap().all(|entry| !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .contains("localsr-benchmark")));

        let error = write_benchmark_export(&destination, b"replacement").unwrap_err();
        assert!(error.to_string().contains("already exists"));
        assert_eq!(fs::read(&destination).unwrap(), br#"{"score":42}"#);
    }

    #[test]
    fn extension_matching_is_case_insensitive_and_bounded() {
        assert_eq!(media_kind(Path::new("A.DNG")), Some("image"));
        assert_eq!(media_kind(Path::new("pixel.BMP")), Some("image"));
        assert_eq!(media_kind(Path::new("DSCF0001.RAF")), Some("image"));
        assert_eq!(media_kind(Path::new("IMG_0001.CR3")), Some("image"));
        assert_eq!(media_kind(Path::new("photo.avif")), Some("image"));
        let heic = media_kind(Path::new("IMG_0001.HEIC"));
        assert_eq!(heic, cfg!(target_os = "macos").then_some("image"));
        assert_eq!(media_kind(Path::new("clip.MKV")), Some("video"));
        assert_eq!(media_kind(Path::new("notes.txt")), None);
    }

    #[test]
    fn launch_directories_expand_supported_files_without_recursing() {
        let directory = tempfile::tempdir().unwrap();
        fs::write(directory.path().join("B.MP4"), b"video").unwrap();
        fs::write(directory.path().join("a.png"), b"image").unwrap();
        fs::write(directory.path().join("notes.txt"), b"ignored").unwrap();
        fs::create_dir(directory.path().join("nested")).unwrap();
        fs::write(directory.path().join("nested/hidden.png"), b"ignored").unwrap();

        let normalized = normalize_media_paths(vec![directory.path().to_string_lossy().into()])
            .expect("directory should expand");

        assert_eq!(normalized.len(), 2);
        assert_eq!(normalized[0].0.file_name().unwrap(), "a.png");
        assert_eq!(normalized[0].1, "image");
        assert_eq!(normalized[1].0.file_name().unwrap(), "B.MP4");
        assert_eq!(normalized[1].1, "video");
    }

    #[test]
    fn launch_path_expansion_deduplicates_and_rejects_explicit_unknown_files() {
        let directory = tempfile::tempdir().unwrap();
        let image = directory.path().join("photo.png");
        let text = directory.path().join("notes.txt");
        fs::write(&image, b"image").unwrap();
        fs::write(&text, b"text").unwrap();

        let duplicated = normalize_media_paths(vec![
            image.to_string_lossy().into(),
            image.to_string_lossy().into(),
        ])
        .unwrap();
        assert_eq!(duplicated.len(), 1);
        assert!(normalize_media_paths(vec![text.to_string_lossy().into()]).is_err());
    }

    #[test]
    fn image_jobs_request_a_bounded_nonblocking_progressive_preview() {
        let media = crate::types::MediaItem {
            id: "media".into(),
            path: "/input/photo.png".into(),
            name: "photo.png".into(),
            kind: "image".into(),
            width: 100,
            height: 80,
            frame_count: 1,
            fps: 0.0,
            duration_seconds: 0.0,
            preview_data_url: String::new(),
            probe_status: "ready".into(),
            hdr_format: String::new(),
            audio_warning: String::new(),
            error: String::new(),
            selected: true,
        };
        let selection = ModelSelection::Image {
            model_id: "model".into(),
            path: "/models/upscaler.safetensors".into(),
            preprocess: Vec::new(),
            face_path: None,
            face_model_id: String::new(),
            native_scale: 4,
            interpolation: None,
        };

        let message = build_job_message(
            "job",
            &media,
            Path::new("/output/photo.png"),
            Path::new("/app-owned/work"),
            &input("upscale"),
            &selection,
        )
        .unwrap();

        assert_eq!(message["data"]["preview_enabled"], true);
        assert_eq!(message["data"]["preview_max_fps"], 2.0);
        assert_eq!(message["data"]["preview_max_dimension"], 320);
        assert_eq!(message["data"]["face_fidelity"], 0.7);
        assert_eq!(message["data"]["scratch_directory"], "/app-owned/work");
        assert_eq!(message["data"]["stages"].as_array().unwrap().len(), 1);
        assert_eq!(message["data"]["stages"][0]["kind"], "upscale");
        assert_eq!(message["data"]["neural_engine"], true);

        // Probed average FPS is metadata, not a request to retime a VFR clip.
        let mut video = media.clone();
        video.kind = "video".into();
        video.fps = 29.97;
        let video_message = build_job_message(
            "video-job",
            &video,
            Path::new("/output/clip.mp4"),
            Path::new("/app-owned/work"),
            &input("video"),
            &selection,
        )
        .unwrap();
        assert_eq!(video_message["type"], "video_job_request");
        assert!(video_message["data"]["fps"].is_null());
        assert_eq!(video_message["data"]["neural_engine"], true);
        let mut without_neural_engine = input("video");
        without_neural_engine.neural_engine = false;
        let gpu_message = build_job_message(
            "gpu-job",
            &video,
            Path::new("/output/gpu.mp4"),
            Path::new("/app-owned/work"),
            &without_neural_engine,
            &selection,
        )
        .unwrap();
        assert_eq!(gpu_message["data"]["neural_engine"], false);
        assert_eq!(video_message["data"]["hdr_mode"], "tone_map");
        let mut hdr_input = input("video");
        hdr_input.video_hdr_mode = "preserve".into();
        let hdr_message = build_job_message(
            "hdr-job",
            &video,
            Path::new("/output/hdr.mp4"),
            Path::new("/app-owned/work"),
            &hdr_input,
            &selection,
        )
        .unwrap();
        assert_eq!(hdr_message["data"]["hdr_mode"], "preserve");

        let temporal = ModelSelection::Temporal {
            model_id: "seedvr2_3b".into(),
            engine_kind: "seedvr2".into(),
            bundle_dir: "/models/seedvr2".into(),
            temporal_window: 9,
            temporal_overlap: 2,
        };
        let temporal_message = build_job_message(
            "temporal-job",
            &video,
            Path::new("/output/clip.mp4"),
            Path::new("/app-owned/work"),
            &input("video"),
            &temporal,
        )
        .unwrap();
        assert!(temporal_message["data"]["fps"].is_null());
        assert_eq!(temporal_message["data"]["video_low_memory"], true);
        assert_eq!(temporal_message["data"]["hdr_mode"], "tone_map");
        let mut smaller = input("video");
        smaller.video_target_resolution = 512;
        smaller.video_low_memory = false;
        let smaller_message = build_job_message(
            "small-job",
            &video,
            Path::new("/output/small.mp4"),
            Path::new("/app-owned/work"),
            &smaller,
            &temporal,
        )
        .unwrap();
        assert_eq!(smaller_message["data"]["target_resolution"], 512);
        assert_eq!(smaller_message["data"]["video_low_memory"], false);
        let mut old_settings = serde_json::to_value(UiSettings::default()).unwrap();
        old_settings
            .as_object_mut()
            .unwrap()
            .remove("neural_engine");
        assert!(
            serde_json::from_value::<UiSettings>(old_settings)
                .unwrap()
                .neural_engine
        );
        let mut old_memory_settings = serde_json::to_value(UiSettings::default()).unwrap();
        old_memory_settings
            .as_object_mut()
            .unwrap()
            .remove("video_low_memory");
        assert!(
            serde_json::from_value::<UiSettings>(old_memory_settings)
                .unwrap()
                .video_low_memory
        );
        let mut old_settings = serde_json::to_value(UiSettings::default()).unwrap();
        old_settings
            .as_object_mut()
            .unwrap()
            .remove("video_target_resolution");
        assert_eq!(
            serde_json::from_value::<UiSettings>(old_settings)
                .unwrap()
                .video_target_resolution,
            0
        );
    }

    #[test]
    fn image_recipe_serializes_restore_upscale_and_fused_face_in_order() {
        let media = crate::types::MediaItem {
            id: "media".into(),
            path: "/input/photo.png".into(),
            name: "photo.png".into(),
            kind: "image".into(),
            width: 100,
            height: 80,
            frame_count: 1,
            fps: 0.0,
            duration_seconds: 0.0,
            preview_data_url: String::new(),
            probe_status: "ready".into(),
            hdr_format: String::new(),
            audio_warning: String::new(),
            error: String::new(),
            selected: true,
        };
        let selection = ModelSelection::Image {
            model_id: "upscale".into(),
            path: "/models/upscale.pth".into(),
            preprocess: vec![ResolvedImageStage {
                kind: "deblock",
                fix: "jpeg".into(),
                model_id: "fbcnn_color".into(),
                path: "/models/fbcnn.pth".into(),
            }],
            face_path: Some("/models/face.pth".into()),
            face_model_id: "hat_l_x4_face".into(),
            native_scale: 4,
            interpolation: None,
        };
        let mut options = input("upscale");
        options.enable_face_model = true;
        options.face_fidelity = 65;
        let message = build_job_message(
            "job",
            &media,
            Path::new("/output/photo.png"),
            Path::new("/app-owned/work"),
            &options,
            &selection,
        )
        .unwrap();

        let stages = message["data"]["stages"].as_array().unwrap();
        assert_eq!(
            stages
                .iter()
                .map(|stage| stage["kind"].as_str().unwrap())
                .collect::<Vec<_>>(),
            vec!["deblock", "upscale", "face_restore"]
        );
        assert_eq!(stages[2]["model_id"], "hat_l_x4_face");
        assert_eq!(stages[2]["model_path"], "/models/face.pth");
        assert_eq!(stages[2]["execution"], "fused-with-upscale");
        assert_eq!(stages[2]["fidelity"], 0.65);
    }

    #[test]
    fn install_hints_follow_the_distribution() {
        let fedora = "NAME=\"Fedora Linux\"\nID=fedora\nID_LIKE=\"rhel centos\"\n";
        assert_eq!(
            install_hint_for("linux", fedora).command,
            "sudo dnf install ffmpeg"
        );
        assert!(install_hint_for("linux", fedora)
            .note
            .contains("RPM Fusion"));
        let mint = "ID=linuxmint\nID_LIKE=\"ubuntu debian\"\n";
        assert_eq!(
            install_hint_for("linux", mint).command,
            "sudo apt install ffmpeg"
        );
        assert_eq!(install_hint_for("linux", mint).system, "Ubuntu");
        assert_eq!(
            install_hint_for("linux", "ID=arch\n").command,
            "sudo pacman -S ffmpeg"
        );
        assert_eq!(
            install_hint_for("linux", "").command,
            "sudo apt install ffmpeg"
        );
        assert_eq!(install_hint_for("macos", "").command, "brew install ffmpeg");
        assert_eq!(
            install_hint_for("windows", "").command,
            "winget install Gyan.FFmpeg"
        );
    }

    #[test]
    fn video_codec_settings_follow_the_media_policy() {
        let mut video = input("video");
        assert!(validate_start_input(&video, false).is_ok());
        video.video_codec = "ffv1".into();
        assert!(
            validate_start_input(&video, false).is_err(),
            "FFV1 requires MKV"
        );
        video.video_container = "mkv".into();
        assert!(validate_start_input(&video, false).is_ok());
        video.video_codec = "h264".into();
        video.video_container = "mp4".into();
        assert!(
            validate_start_input(&video, false).is_err(),
            "H.264 export needs the system codecs or an FFmpeg"
        );
        assert!(
            validate_start_input(&video, true).is_ok(),
            "the system codecs or a found FFmpeg write H.264"
        );
        video.external_ffmpeg_path = "/opt/homebrew/bin/ffmpeg".into();
        assert!(validate_start_input(&video, false).is_ok());
        video.video_codec = "x264".into();
        assert!(validate_start_input(&video, false).is_err());
    }

    #[test]
    fn heic_mov_and_prores_are_accepted_on_macos_only() {
        let mac = cfg!(target_os = "macos");
        let mut photo = input("upscale");
        photo.output_format = "heic".into();
        assert_eq!(validate_start_input(&photo, false).is_ok(), mac);
        photo.output_format = "webp".into();
        assert!(validate_start_input(&photo, false).is_ok());

        let mut video = input("video");
        video.video_container = "mov".into();
        video.video_codec = "hevc".into();
        assert_eq!(validate_start_input(&video, true).is_ok(), mac);
        video.video_codec = "prores".into();
        assert_eq!(validate_start_input(&video, true).is_ok(), mac);
        // MOV holds HEVC or ProRes; ProRes needs MOV, on every platform.
        for (container, codec) in [("mov", "av1"), ("mov", "h264"), ("mp4", "prores")] {
            video.video_container = container.into();
            video.video_codec = codec.into();
            assert!(
                validate_start_input(&video, true).is_err(),
                "{container} with {codec}"
            );
        }
        let settings = UiSettings {
            output_format: "heic".into(),
            ..UiSettings::default()
        };
        assert_eq!(validate_settings(&settings).is_ok(), mac);
    }

    #[test]
    fn heic_and_mov_options_need_the_engines_announcement() {
        let announced = json!({
            "image_output": ["heic"],
            "system_codecs": {"encode": ["h264", "hevc", "prores", "aac"]},
        });
        let silent = json!({"image_output": [], "system_codecs": null});
        let mut photo = input("upscale");
        photo.output_format = "heic".into();
        assert!(validate_system_outputs(&photo, &announced).is_ok());
        assert!(validate_system_outputs(&photo, &silent).is_err());
        assert!(validate_system_outputs(&photo, &Value::Null).is_err());
        photo.output_format = "png".into();
        assert!(validate_system_outputs(&photo, &silent).is_ok());

        let mut video = input("video");
        video.video_container = "mov".into();
        video.video_codec = "prores".into();
        assert!(validate_system_outputs(&video, &announced).is_ok());
        assert!(validate_system_outputs(&video, &silent).is_err());
        // A leftover HEIC choice does not stop a video job.
        video.output_format = "heic".into();
        video.video_container = "mp4".into();
        video.video_codec = "av1".into();
        assert!(validate_system_outputs(&video, &silent).is_ok());
    }

    #[test]
    fn exports_take_the_extension_of_the_chosen_format() {
        let mut reserved = ReservedOutputs::default();
        let directory = tempfile::tempdir().unwrap();
        let mut photo = input("upscale");
        photo.output_format = "heic".into();
        let path = unique_output_path(directory.path(), "/in/photo.jpg", &photo, &mut reserved);
        assert_eq!(path.extension().and_then(|e| e.to_str()), Some("heic"));
        let mut video = input("video");
        video.video_container = "mov".into();
        let path = unique_output_path(directory.path(), "/in/clip.mp4", &video, &mut reserved);
        assert_eq!(path.extension().and_then(|e| e.to_str()), Some("mov"));
    }

    #[test]
    fn recipe_stage_validation_accepts_legacy_and_rejects_ambiguous_order() {
        let legacy = Recipe {
            id: "legacy".into(),
            name: "Legacy".into(),
            task: "upscale".into(),
            model_id: "model".into(),
            output_scale: 4,
            tile_size: 256,
            halo: 32,
            precision: "fp32".into(),
            safe_memory: true,
            video_model_id: String::new(),
            custom_model_path: String::new(),
            output_format: "png".into(),
            preserve_metadata: Some(true),
            jpeg_quality: Some(98),
            deflicker: None,
            deflicker_window: None,
            video_container: String::new(),
            video_codec: String::new(),
            video_hdr_mode: "tone_map".into(),
            video_target_resolution: 0,
            video_low_memory: true,
            video_crf: None,
            enable_face_model: None,
            face_fidelity: None,
            stages: Vec::new(),
            quality: String::new(),
            content: String::new(),
            fixes: Vec::new(),
            video_interpolation: String::new(),
            video_interpolation_order: String::new(),
        };
        assert!(validate_recipe_stages(&legacy).is_ok());

        let mut invalid = legacy;
        invalid.stages = vec![
            RecipeStage {
                kind: "upscale".into(),
                model_id: "first".into(),
                fidelity: None,
                fix: None,
            },
            RecipeStage {
                kind: "upscale".into(),
                model_id: "second".into(),
                fidelity: None,
                fix: None,
            },
        ];
        assert!(validate_recipe_stages(&invalid).is_err());
    }

    #[test]
    fn verified_catalog_import_is_atomic_and_idempotent() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("downloaded.pth");
        let destination = directory.path().join("models").join("model.pth");
        fs::create_dir(destination.parent().unwrap()).unwrap();
        fs::write(&source, b"trusted checkpoint").unwrap();
        let source = fs::canonicalize(source).unwrap();
        let digest = catalog::sha256_file(&source).unwrap();

        import_verified_catalog_file(&source, &destination, 18, &digest).unwrap();
        assert_eq!(fs::read(&destination).unwrap(), b"trusted checkpoint");
        assert!(directory
            .path()
            .join("models")
            .read_dir()
            .unwrap()
            .all(|entry| !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with(".tmp")));

        import_verified_catalog_file(&source, &destination, 18, &digest).unwrap();
        assert_eq!(fs::read(&destination).unwrap(), b"trusted checkpoint");
    }

    #[test]
    fn verified_catalog_import_rejects_conflict_and_bad_digest() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("downloaded.pth");
        let destination = directory.path().join("models").join("model.pth");
        fs::create_dir(destination.parent().unwrap()).unwrap();
        fs::write(&source, b"trusted checkpoint").unwrap();
        let source = fs::canonicalize(source).unwrap();

        assert!(import_verified_catalog_file(&source, &destination, 18, &"0".repeat(64)).is_err());
        assert!(!destination.exists());

        fs::write(&destination, b"conflicting bytes").unwrap();
        let digest = catalog::sha256_file(&source).unwrap();
        assert!(import_verified_catalog_file(&source, &destination, 18, &digest).is_err());
        assert_eq!(fs::read(&destination).unwrap(), b"conflicting bytes");
    }

    fn video_media() -> crate::types::MediaItem {
        crate::types::MediaItem {
            id: "media".into(),
            path: "/input/clip.mp4".into(),
            name: "clip.mp4".into(),
            kind: "video".into(),
            width: 640,
            height: 360,
            frame_count: 48,
            fps: 23.976,
            duration_seconds: 2.0,
            preview_data_url: String::new(),
            probe_status: "ready".into(),
            hdr_format: String::new(),
            audio_warning: String::new(),
            error: String::new(),
            selected: true,
        }
    }

    #[test]
    fn frame_interpolation_reaches_the_worker_with_its_downloaded_model() {
        let directory = tempfile::tempdir().unwrap();
        let paths = crate::paths::AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::create_dir_all(&paths.model_root).unwrap();
        let state = AppState::from_paths(paths).unwrap();
        {
            let mut catalog = lock(&state.catalog).unwrap();
            for model in &mut catalog.models {
                model.installed = model.model_id == "span_photo_x4";
                model.installed_path = model.installed.then(|| "/models/span.pth".into());
            }
        }
        let mut options = input("video");
        options.model_id = "span_photo_x4".into();
        options.video_interpolation = "x2".into();
        options.video_interpolation_order = "before_upscale".into();
        let missing = resolve_model_selection(&state, &options).err().unwrap();
        assert!(missing
            .to_string()
            .contains("download the frame interpolation model first"));
        {
            let mut catalog = lock(&state.catalog).unwrap();
            let rife = catalog
                .interpolation_models
                .iter_mut()
                .find(|model| model.model_id == "rife_4_26")
                .unwrap();
            rife.installed = true;
            rife.installed_path = Some("/models/rife_4_26".into());
        }
        let selection = resolve_model_selection(&state, &options).unwrap();
        let work = Path::new("/app-owned/work");
        let output = Path::new("/output/clip.mp4");
        let message =
            build_job_message("job", &video_media(), output, work, &options, &selection).unwrap();
        assert_eq!(message["data"]["interpolation"], "x2");
        assert_eq!(message["data"]["interpolation_order"], "before_upscale");
        assert_eq!(message["data"]["interpolation_model_id"], "rife_4_26");
        assert_eq!(
            message["data"]["interpolation_bundle_dir"],
            "/models/rife_4_26"
        );

        options.video_interpolation = "off".into();
        let plain = resolve_model_selection(&state, &options).unwrap();
        let message =
            build_job_message("plain", &video_media(), output, work, &options, &plain).unwrap();
        assert_eq!(message["data"]["interpolation"], "off");
        assert!(message["data"]["interpolation_model_id"].is_null());
        assert!(message["data"]["interpolation_bundle_dir"].is_null());

        // A photo job ignores a leftover video setting.
        let mut photo = input("upscale");
        photo.model_id = "span_photo_x4".into();
        photo.video_interpolation = "x2".into();
        let selection = resolve_model_selection(&state, &photo).unwrap();
        let mut image = video_media();
        image.kind = "image".into();
        let message = build_job_message(
            "photo",
            &image,
            Path::new("/output/photo.png"),
            work,
            &photo,
            &selection,
        )
        .unwrap();
        assert_eq!(message["type"], "job_request");
        assert!(message["data"].get("interpolation").is_none());
    }

    #[test]
    fn temporal_jobs_always_send_interpolation_off() {
        let temporal = ModelSelection::Temporal {
            model_id: "seedvr2_3b".into(),
            engine_kind: "seedvr2".into(),
            bundle_dir: "/models/seedvr2".into(),
            temporal_window: 9,
            temporal_overlap: 2,
        };
        let message = build_job_message(
            "temporal",
            &video_media(),
            Path::new("/output/clip.mp4"),
            Path::new("/app-owned/work"),
            &input("video"),
            &temporal,
        )
        .unwrap();
        assert_eq!(message["data"]["interpolation"], "off");
    }

    #[test]
    fn interpolation_settings_are_validated_and_default_to_off() {
        let defaults = UiSettings::default();
        assert_eq!(defaults.video_interpolation, "off");
        assert_eq!(defaults.video_interpolation_order, "after_upscale");
        let mut older = serde_json::to_value(UiSettings::default()).unwrap();
        let object = older.as_object_mut().unwrap();
        object.remove("video_interpolation");
        object.remove("video_interpolation_order");
        let older: UiSettings = serde_json::from_value(older).unwrap();
        assert_eq!(older.video_interpolation, "off");
        assert_eq!(older.video_interpolation_order, "after_upscale");

        for target in ["off", "x2", "x4", "fps60", "fps120"] {
            for order in ["after_upscale", "before_upscale"] {
                assert!(
                    interpolation_choice_is_valid(target, order),
                    "{target} {order}"
                );
            }
        }
        assert!(!interpolation_choice_is_valid("x3", "after_upscale"));
        assert!(!interpolation_choice_is_valid("x2", "sideways"));
        let mut settings = UiSettings {
            video_interpolation: "x8".into(),
            ..UiSettings::default()
        };
        assert!(validate_settings(&settings).is_err());
        settings.video_interpolation = "fps60".into();
        assert!(validate_settings(&settings).is_ok());

        let mut video = input("video");
        video.video_interpolation = "x2".into();
        assert!(validate_start_input(&video, false).is_ok());
        video.video_model_id = "seedvr2_3b".into();
        let error = validate_start_input(&video, false).err().unwrap();
        assert!(error.to_string().contains("Frame-by-frame"));
        // Photos never interpolate, so a leftover video setting does not block them.
        let mut photo = input("upscale");
        photo.video_interpolation = "x2".into();
        assert!(validate_start_input(&photo, false).is_ok());
    }

    #[test]
    fn hdr_preservation_refuses_interpolation_only_for_hdr_sources() {
        let selection = ModelSelection::Image {
            model_id: "span_photo_x4".into(),
            path: "/models/span.pth".into(),
            preprocess: Vec::new(),
            face_path: None,
            face_model_id: String::new(),
            native_scale: 4,
            interpolation: Some(ResolvedInterpolation {
                model_id: "rife_4_26".into(),
                bundle_dir: "/models/rife_4_26".into(),
            }),
        };
        let mut options = input("video");
        options.video_interpolation = "x2".into();
        options.video_hdr_mode = "preserve".into();
        let work = Path::new("/app-owned/work");
        let output = Path::new("/output/clip.mp4");
        // The saved HDR choice stays "preserve" for an SDR clip; it has no effect there.
        assert!(
            build_job_message("sdr", &video_media(), output, work, &options, &selection).is_ok()
        );
        let mut hdr = video_media();
        hdr.hdr_format = "HLG".into();
        let error = build_job_message("hdr", &hdr, output, work, &options, &selection)
            .err()
            .unwrap();
        assert!(error.to_string().contains("HDR"));
        options.video_hdr_mode = "tone_map".into();
        assert!(build_job_message("tone", &hdr, output, work, &options, &selection).is_ok());
    }

    #[test]
    fn the_editing_runtime_is_the_one_the_host_found() {
        let directory = tempfile::tempdir().unwrap();
        let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
        let found = directory.path().join("sd-cli");
        let planted = directory.path().join("planted-tool");
        fs::write(&found, b"found").unwrap();
        fs::write(&planted, b"planted").unwrap();
        // Nothing found at start-up: no program can be named.
        assert!(edit_runtime(&state, planted.to_str().unwrap()).is_err());
        *lock(&state.edit_runtime).unwrap() = found.to_string_lossy().into_owned();
        assert_eq!(
            edit_runtime(&state, "").unwrap(),
            fs::canonicalize(&found).unwrap()
        );
        assert_eq!(
            edit_runtime(&state, found.to_str().unwrap()).unwrap(),
            fs::canonicalize(&found).unwrap()
        );
        assert!(edit_runtime(&state, planted.to_str().unwrap()).is_err());
        assert!(edit_runtime(&state, "/nonexistent/sd-cli").is_err());
    }

    #[test]
    fn a_batch_names_only_an_ffmpeg_the_host_obtained() {
        let directory = tempfile::tempdir().unwrap();
        let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
        let mut input = input("upscale");
        assert!(validate_start(&input, &state).is_ok());
        input.external_ffmpeg_path = "/tmp/planted-tool".into();
        assert!(validate_start(&input, &state).is_err());
        state.programs.grant("/tmp/planted-tool");
        assert!(validate_start(&input, &state).is_ok());
    }

    #[test]
    fn a_long_source_name_is_shortened_so_the_result_name_fits_the_file_system() {
        let directory = tempfile::tempdir().unwrap();
        let mut video = input("video");
        video.video_container = "mkv".into();
        let mut named = input("upscale");
        named.name_by_model = true;
        for (stem, task) in [
            ("a".repeat(300), input("upscale")),
            ("写".repeat(200), input("upscale")),
            ("🎞".repeat(120), input("denoise")),
            ("é".repeat(127) + "x", named),
            ("v".repeat(255), video),
        ] {
            let source = format!("/in/{stem}.png");
            let mut reserved = ReservedOutputs::default();
            let mut names = Vec::new();
            for _ in 0..3 {
                let path = unique_output_path(directory.path(), &source, &task, &mut reserved);
                names.push(path.file_name().unwrap().to_str().unwrap().to_owned());
            }
            let ending = match task.task.as_str() {
                "denoise" => "_denoised",
                "video" => "_video_4x",
                _ => "_upscaled_4x",
            };
            for (index, name) in names.iter().enumerate() {
                assert!(name.len() <= 255, "{} bytes: {name}", name.len());
                assert!(name.contains(ending));
                let extension = if task.task == "video" { ".mkv" } else { ".png" };
                assert!(name.ends_with(extension), "{name}");
                if index > 0 {
                    assert!(name[..name.len() - extension.len()].ends_with(&format!("_{index}")));
                }
            }
            assert_eq!(names.iter().collect::<HashSet<_>>().len(), 3);
        }
    }

    #[test]
    fn a_name_that_already_fits_is_left_as_it_is() {
        let directory = tempfile::tempdir().unwrap();
        let mut reserved = ReservedOutputs::default();
        let path = unique_output_path(
            directory.path(),
            "/in/holiday.png",
            &input("upscale"),
            &mut reserved,
        );
        assert_eq!(path.file_name().unwrap(), "holiday_upscaled_4x.png");
    }

    #[test]
    fn a_benchmark_can_be_exported_under_the_longest_file_name() {
        let directory = tempfile::tempdir().unwrap();
        let destination = directory.path().join(format!("{}.json", "n".repeat(250)));
        write_benchmark_export(&destination, b"{}").unwrap();
        assert_eq!(fs::read(&destination).unwrap(), b"{}");
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
    }

    #[test]
    fn a_volume_without_hard_links_gets_a_copy_that_never_replaces_a_file() {
        let directory = tempfile::tempdir().unwrap();
        let no_links = |_: &Path, _: &Path| -> std::io::Result<()> {
            Err(std::io::ErrorKind::Unsupported.into())
        };
        let temporary = directory.path().join("temporary");
        fs::write(&temporary, b"new").unwrap();
        let destination = directory.path().join("export.json");
        publish_without_replacing(&temporary, &destination, no_links).unwrap();
        assert_eq!(fs::read(&destination).unwrap(), b"new");
        let error = publish_without_replacing(&temporary, &destination, no_links).unwrap_err();
        assert!(error.to_string().contains("already exists"));
        assert_eq!(fs::read(&destination).unwrap(), b"new");
    }

    #[test]
    fn start_requests_from_an_older_window_default_to_no_interpolation() {
        let mut request = serde_json::json!({
            "media_ids": ["media"], "batch_mode": false, "task": "video", "model_id": "model",
            "video_model_id": "frame_by_frame", "fix_stages": [], "custom_model_path": "",
            "output_directory": "", "output_format": "png", "output_scale": 4,
            "preserve_metadata": true, "jpeg_quality": 98, "device": "cpu", "tile_size": 256,
            "halo": 32, "precision": "fp32", "safe_memory": false, "deflicker": false,
            "deflicker_window": 3, "video_container": "mp4", "video_crf": 18,
            "enable_face_model": false, "face_fidelity": 70, "enable_live_preview": true,
            "allow_unsafe_pickle_model": false
        });
        let parsed: StartBatchInput = serde_json::from_value(request.clone()).unwrap();
        assert_eq!(parsed.video_interpolation, "off");
        assert_eq!(parsed.video_interpolation_order, "after_upscale");
        request["video_interpolation"] = "fps120".into();
        let parsed: StartBatchInput = serde_json::from_value(request).unwrap();
        assert_eq!(parsed.video_interpolation, "fps120");
    }
}
