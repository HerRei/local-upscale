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

/// The editing program to run: the one the host found at start-up or one the user
/// picked in the host's own file dialog. The web view may repeat such a path but
/// cannot name another.
fn edit_runtime(state: &AppState, requested: &str) -> AppResult<PathBuf> {
    let missing = || AppError::Validation("Choose the native editing runtime.".into());
    let found = lock(&state.edit_runtime)?.clone();
    let found = if found.is_empty() {
        None
    } else {
        fs::canonicalize(found).ok().filter(|path| path.is_file())
    };
    if requested.is_empty() {
        return found.ok_or_else(missing);
    }
    let requested_real = fs::canonicalize(requested)
        .ok()
        .filter(|path| path.is_file());
    match requested_real {
        Some(real) if found.as_ref() == Some(&real) || state.edit_programs.check(requested).is_ok() => {
            Ok(real)
        }
        _ => Err(AppError::Validation(
            "Only an editing runtime LocalSR found itself or that you chose in its file dialog can be used.".into(),
        )),
    }
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
        // A name that is not valid text cannot be sent to the window and back: its
        // lossy copy would name another file or none.
        let Some(text) = path.to_str() else {
            continue;
        };
        if path.is_file() && media_kind(&path).is_some() {
            paths.push(text.to_owned());
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

/// Adds a finished result as a new input and selects it. The result is named the way Reveal
/// and Open name it (a recorded, completed output that still exists and is no link), so the
/// window never passes a path of its own choosing. A file already in the list is only selected.
/// Returns the input's id and, when it is new, the path the engine has to probe.
fn add_result_as_input(state: &AppState, requested: &str) -> AppResult<(String, Option<String>)> {
    let authorized = authorized_result(state, requested)?;
    let kind = media_kind(&authorized)
        .ok_or_else(|| AppError::Validation("this file type cannot be used as an input".into()))?;
    let encoded = authorized.to_string_lossy().into_owned();
    let database = lock(&state.database)?;
    let (id, to_probe) = match database.get_media_by_path(&encoded)? {
        Some(existing) => (existing.id, None),
        None => {
            let id = Uuid::new_v4().to_string();
            let name = authorized
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or("Media")
                .to_owned();
            database.insert_media(&id, &encoded, &name, kind)?;
            (id, Some(encoded))
        }
    };
    database.select_media(&id)?;
    Ok((id, to_probe))
}

#[tauri::command]
pub fn use_result_as_input(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    path: String,
) -> AppResult<String> {
    native_menu::set_comparison_labels(&app, false);
    crate::updates::ensure_not_installing(&state)?;
    let _scheduler = lock(&state.scheduler)?;
    crate::updates::ensure_not_installing(&state)?;
    let (id, to_probe) = add_result_as_input(&state, &path)?;
    if let Some(encoded) = to_probe {
        let _ = worker::send(&state, &worker::media_probe_request(&state, &encoded));
    }
    emit_state_changed(&app);
    Ok(id)
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
    // The scheduler lock keeps the dispatcher from sending this picture's job meanwhile. The
    // batch the engine already holds is not removed from under it.
    let engine_jobs = lock(&state.runtime)?.active_batch_job_ids.clone();
    lock(&state.database)?.remove_media_guarded(&id, &engine_jobs)?;
    emit_state_changed(&app);
    Ok(())
}

/// Clear in the Outputs list: every result leaves the list, no file is touched.
#[tauri::command]
pub fn clear_results(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    let engine_jobs = lock(&state.runtime)?.active_batch_job_ids.clone();
    lock(&state.database)?.hide_all_results(&engine_jobs)?;
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
    replace_settings(&state, settings)
}

/// Make `settings` the current ones and write them. When the write fails the
/// previous settings stay, so the window never uses what the file does not hold.
fn replace_settings(state: &AppState, mut settings: UiSettings) -> AppResult<()> {
    let previous = {
        // Read and replace under one guard, so a switch saved in between is kept.
        let mut current = lock(&state.settings)?;
        keep_update_switches(&current, &mut settings);
        std::mem::replace(&mut *current, settings)
    };
    persist_or_restore(state, |state| {
        *state.settings.lock().unwrap_or_else(|e| e.into_inner()) = previous
    })
}

/// Write the settings and recipes; on a failed write run `restore` to put the
/// previous values back.
fn persist_or_restore(state: &AppState, restore: impl FnOnce(&AppState)) -> AppResult<()> {
    let written = state.persist_settings();
    if written.is_err() {
        restore(state);
    }
    written
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
    store_recipe(&state, recipe)?;
    let switches = native_menu::switches(&*lock(&state.settings)?);
    native_menu::rebuild(&app, &lock(&state.recipes)?, switches).map_err(|error| {
        AppError::Config(format!(
            "could not refresh the native presets menu: {error}"
        ))
    })?;
    emit_state_changed(&app);
    Ok(())
}

/// Add or replace a recipe and write it; a failed write leaves the recipes as they were.
fn store_recipe(state: &AppState, recipe: Recipe) -> AppResult<()> {
    let previous = {
        let mut recipes = lock(&state.recipes)?;
        let previous = recipes.clone();
        if let Some(existing) = recipes.iter_mut().find(|existing| existing.id == recipe.id) {
            *existing = recipe;
        } else {
            if recipes.len() >= 100 {
                return Err(AppError::Validation("recipe limit reached".into()));
            }
            recipes.push(recipe);
        }
        previous
    };
    persist_or_restore(state, |state| {
        *state.recipes.lock().unwrap_or_else(|e| e.into_inner()) = previous
    })
}

/// Remove a recipe and write the change; a failed write leaves the recipes as they were.
fn remove_recipe(state: &AppState, id: &str) -> AppResult<()> {
    let previous = {
        let mut recipes = lock(&state.recipes)?;
        let previous = recipes.clone();
        recipes.retain(|recipe| recipe.id != id);
        previous
    };
    persist_or_restore(state, |state| {
        *state.recipes.lock().unwrap_or_else(|e| e.into_inner()) = previous
    })
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
    remove_recipe(&state, &id)?;
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
    check_output_room(&output_directory, &input, platform_path_limit())?;
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
    refuse_when_disk_is_short(&input, &selection, &media[0], &output_directory)?;
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

/// "Try again" after the host gave up restarting the inference engine.
#[tauri::command]
pub fn retry_engine(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    worker::retry_engine(&state, &app)
}

#[tauri::command]
pub fn cancel_jobs(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    let mut cancellation = lock(&state.worker.cancellation)?;
    lock(&state.database)?.cancel_queued_jobs()?;
    let active = lock(&state.runtime)?.active_job_id.clone();
    // A second request while the first is still being carried out changes nothing.
    if !active.is_empty() && !cancellation.is_cancelling(&active) {
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

/// Tell the user before anything is queued when even the first job cannot be written to
/// the output folder's volume. The queue checks each job again before it is sent.
fn refuse_when_disk_is_short(
    input: &StartBatchInput,
    selection: &ModelSelection,
    first: &crate::types::MediaItem,
    output_directory: &Path,
) -> AppResult<()> {
    let ModelSelection::Image { native_scale, .. } = selection else {
        return Ok(());
    };
    let native = u64::from(*native_scale);
    let saved = if input.task == "denoise" {
        1
    } else {
        u64::from(input.output_scale).min(native).max(1)
    };
    let (width, height) = (u64::from(first.width), u64::from(first.height));
    let need = if input.task == "video" {
        crate::disk_room::video_bytes(
            width.saturating_mul(saved),
            height.saturating_mul(saved),
            first.frame_count,
            false,
        )
    } else {
        crate::disk_room::image_need(width, height, native, saved, &input.output_format, false)
            .total()
    };
    match crate::disk_room::check(output_directory, need) {
        Some(reason) => Err(AppError::Validation(reason)),
        None => Ok(()),
    }
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

/// Problems a 1× checkpoint fixes; mirrors `fixesOf` in desktop/src/lib/plan.ts.
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
    if model_ids.len() < 2
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
    validate_fix_engine(base, lock(&state.engine)?.as_ref())?;
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
    check_output_room(&directory, base, platform_path_limit())?;
    let database = lock(&state.database)?;
    let media = database
        .get_media(media_id)?
        .ok_or_else(|| AppError::Validation("The selected image is no longer queued.".into()))?;
    if media.kind != "image" || media.probe_status != "ready" || !Path::new(&media.path).is_file() {
        return Err(AppError::Validation(
            "Choose an available image with a ready preview.".into(),
        ));
    }
    refuse_when_disk_is_short(
        &candidates[0].input,
        &candidates[0].selection,
        &media,
        &directory,
    )?;
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

/// All media histories in one IPC call for the Outputs pane.
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
    let shortfall = lock(&state.engine)?
        .as_ref()
        .and_then(|engine| crate::compare::engine_shortfall(&engine.features));
    for request in requests {
        let sent = match shortfall {
            Some(reason) => Err(AppError::Worker(reason.into())),
            None => worker::send(&state, &request),
        };
        if let Err(error) = sent {
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
pub fn clear_compare_regions(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
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
    emit_state_changed(&app);
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
    crate::results::ensure_not_linked(Path::new(&output))?;
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
    result_id: Option<String>,
    defer_conversion: Option<bool>,
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
            result_id.as_deref(),
            defer_conversion.unwrap_or(false),
            cancel,
        )
    })
    .await
    .map_err(|error| AppError::Worker(format!("Playback preparation failed: {error}")))?
}

/// A completed video result: the real path of a file the engine wrote, never what a
/// link left in its place points to.
fn canonical_result_video(path: PathBuf) -> AppResult<PathBuf> {
    crate::results::ensure_not_linked(&path)?;
    canonical_video(path)
}

/// The real path of a video file, or the message that it is gone (a deleted or moved
/// file) instead of the system's own error text.
fn canonical_video(path: PathBuf) -> AppResult<PathBuf> {
    match fs::canonicalize(path) {
        Ok(real) if real.is_file() => Ok(real),
        _ => Err(AppError::Validation(
            "the original or enhanced video is no longer available".into(),
        )),
    }
}

#[allow(clippy::too_many_arguments)]
fn video_comparison_sources(
    state: &AppState,
    app: &AppHandle,
    media_id: &str,
    request_id: &str,
    force_compatible: bool,
    result_id: Option<&str>,
    defer_conversion: bool,
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
        let output = match result_id {
            Some("") => None,
            Some(id) => Some(database.media_result_output(media_id, id)?),
            None => database.latest_completed_output_for_media(media_id)?,
        };
        (PathBuf::from(media.path), output.map(PathBuf::from))
    };
    let original = canonical_video(original)?;
    let enhanced = enhanced.map(canonical_result_video).transpose()?;
    let mut converted = false;
    let mut playback = |path: PathBuf| -> AppResult<PathBuf> {
        if force_compatible || crate::video_preview::needs_conversion(&path) {
            if defer_conversion {
                return Err(AppError::Validation(
                    "This video needs a playback conversion. It will load when the current video is done.".into(),
                ));
            }
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
            return Err(AppError::Validation("unknown model".into()));
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
    let found = crate::programs::find_ffmpeg();
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
    let title = "Select the FFmpeg program installed on this computer";
    let picked = pick_program(app, title, &current).await?;
    if let Some(path) = &picked {
        state.programs.grant(path);
    }
    Ok(picked)
}

/// Let the user pick the native editing runtime (sd-cli) in the host's own file
/// dialog. Start Edit runs only the runtime the host found or one picked here.
#[tauri::command]
pub async fn choose_edit_runtime(
    app: AppHandle,
    state: State<'_, Arc<AppState>>,
) -> AppResult<Option<String>> {
    let picked = pick_program(app, "Choose the sd-cli image editing runtime", "").await?;
    if let Some(path) = &picked {
        state.edit_programs.grant(path);
    }
    Ok(picked)
}

/// The host's own file dialog for one program, opened beside `current` when that
/// names a file in an existing folder.
async fn pick_program(app: AppHandle, title: &str, current: &str) -> AppResult<Option<String>> {
    use tauri_plugin_dialog::DialogExt;

    let mut dialog = app.dialog().file().set_title(title);
    if let Some(folder) = Path::new(current).parent().filter(|folder| folder.is_dir()) {
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
            // As the plan card names it (plan.ts resolvePlan).
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
    // A relative folder would be made below the working directory, which for a
    // desktop app is wherever the system started it.
    if !path.is_absolute() {
        return Err(AppError::Validation(
            "choose the full path of the output folder".into(),
        ));
    }
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
    unique_output_path_within(
        directory,
        input_path,
        input,
        reserved,
        platform_path_limit(),
    )
}

/// The name and extension a result of `input` ends with, before any counter.
fn output_ending(input: &StartBatchInput) -> (String, &str) {
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
    (suffix, extension)
}

/// `unique_output_path` for a given limit on the whole path (see `path_room`).
fn unique_output_path_within(
    directory: &Path,
    input_path: &str,
    input: &StartBatchInput,
    reserved: &mut ReservedOutputs,
    path_limit: Option<usize>,
) -> PathBuf {
    let stem = Path::new(input_path)
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("output");
    let (suffix, extension) = output_ending(input);
    let room = path_limit.map(|limit| path_room(directory, limit));
    let mut counter = 0_u32;
    loop {
        let numbered = if counter == 0 {
            String::new()
        } else {
            format!("_{counter}")
        };
        let ending = format!("{suffix}{numbered}.{extension}");
        let stem = fit_stem(stem, MAX_FILE_NAME_BYTES.saturating_sub(ending.len()));
        let stem = match room {
            Some(room) => fit_stem_units(stem, room.saturating_sub(ending.encode_utf16().count())),
            None => stem,
        };
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

/// `stem` cut to at most `limit` UTF-16 units, on a character boundary.
fn fit_stem_units(stem: &str, limit: usize) -> &str {
    let mut units = 0;
    for (index, character) in stem.char_indices() {
        units += character.len_utf16();
        if units > limit {
            return &stem[..index];
        }
    }
    stem
}

/// How long a full path may be where long paths are off: Windows' MAX_PATH, which counts
/// the terminating NUL. The engine and the tools it starts do not all use the extended
/// form that lifts it.
const WINDOWS_PATH_LIMIT: usize = 260;

fn platform_path_limit() -> Option<usize> {
    cfg!(windows).then_some(WINDOWS_PATH_LIMIT)
}

/// What the engine makes below the output folder: its scratch folder
/// (`.localsr-job-` and a 36-character id), a separator and its longest temporary name
/// (`.localsr-external-` and 8 characters and `.tmp`).
const ENGINE_PATH_BELOW_FOLDER: usize = 49 + 1 + 30;

/// The fewest characters of the source's name worth keeping in the result's.
const MIN_STEM_UNITS: usize = 16;

/// The length of `directory` the way another tool sees it: the extended-length prefix
/// (`\\?\`, `\\?\UNC\`) that `canonicalize` adds on Windows is not part of it.
fn path_units_without_prefix(directory: &Path) -> usize {
    let text = directory.to_string_lossy();
    let units = text.encode_utf16().count();
    if text.starts_with("\\\\?\\UNC\\") {
        units - "\\\\?\\UNC\\".len() + 2
    } else if text.starts_with("\\\\?\\") {
        units - "\\\\?\\".len()
    } else {
        units
    }
}

/// How many UTF-16 units a file name in `directory` may have so that the whole path
/// stays under `limit`: the terminating NUL, the folder and a separator come off.
fn path_room(directory: &Path, limit: usize) -> usize {
    limit.saturating_sub(path_units_without_prefix(directory) + 2)
}

/// Refuse a folder whose path leaves no room for a sensible result name and for what the
/// engine makes below it, instead of failing after the work is done.
fn check_output_room(
    directory: &Path,
    input: &StartBatchInput,
    path_limit: Option<usize>,
) -> AppResult<()> {
    let Some(limit) = path_limit else {
        return Ok(());
    };
    let (suffix, extension) = output_ending(input);
    // The ending, a counter such as `_12`, and a stem worth reading.
    let name = MIN_STEM_UNITS + suffix.len() + 1 + extension.len() + 4;
    if path_room(directory, limit) < name.max(ENGINE_PATH_BELOW_FOLDER) {
        return Err(AppError::Validation(format!(
            "The folder {} has too long a path for LocalSR to save results in it: Windows limits a full path to {} characters. Choose a shorter output folder.",
            directory.display(),
            limit - 1
        )));
    }
    Ok(())
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
            .filter(|path| path.to_str().is_some() && path.is_file() && media_kind(path).is_some())
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
    let is_last_output = !last_output.is_empty()
        && (last_output == requested
            || fs::canonicalize(&last_output)
                .map(|expected| expected == Path::new(requested))
                .unwrap_or(false));
    if !recorded && !is_last_output {
        return Err(AppError::Validation(
            "output path was not authorized".into(),
        ));
    }
    fs::symlink_metadata(requested)?;
    crate::results::ensure_not_linked(Path::new(requested))?;
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
mod tests;
