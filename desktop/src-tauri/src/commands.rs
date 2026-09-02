use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
    sync::Arc,
};

use serde_json::{json, Value};
use tauri::{AppHandle, State};
use uuid::Uuid;

use crate::{
    downloads,
    error::{AppError, AppResult},
    integrations,
    launch::LaunchIntent,
    native_menu,
    state::{emit_state_changed, lock, AppState},
    types::{AppSnapshot, CatalogModel, Recipe, StartBatchInput, UiSettings},
    worker,
};

const IMAGE_EXTENSIONS: &[&str] = &["jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp", "dng"];
const VIDEO_EXTENSIONS: &[&str] = &["mp4", "mov", "m4v", "mkv", "webm", "avi"];
const MAX_MEDIA_ITEMS: usize = 10_000;

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
            let _ = worker::send(
                &state,
                &json!({
                    "type": "media_probe_request",
                    "data": {"media_path": encoded, "max_dimension": 2048}
                }),
            );
        }
    }
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn select_media(state: State<'_, Arc<AppState>>, app: AppHandle, id: String) -> AppResult<()> {
    lock(&state.database)?.select_media(&id)?;
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn remove_media(state: State<'_, Arc<AppState>>, app: AppHandle, id: String) -> AppResult<()> {
    ensure_queue_idle(&state)?;
    lock(&state.database)?.remove_media(&id)?;
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn clear_media(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    ensure_queue_idle(&state)?;
    lock(&state.database)?.clear_media()?;
    clear_completed_result(&state)?;
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn save_settings(state: State<'_, Arc<AppState>>, settings: UiSettings) -> AppResult<()> {
    validate_settings(&settings)?;
    *lock(&state.settings)? = settings;
    state.persist_settings()
}

#[tauri::command]
pub fn save_recipe(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    mut recipe: Recipe,
) -> AppResult<()> {
    recipe.name = recipe.name.trim().chars().take(60).collect();
    if recipe.name.is_empty() || !matches!(recipe.task.as_str(), "upscale" | "denoise" | "video") {
        return Err(AppError::Validation(
            "recipe name or task is invalid".into(),
        ));
    }
    if (!recipe.output_format.is_empty()
        && !matches!(recipe.output_format.as_str(), "png" | "jpg" | "tif"))
        || (!recipe.video_container.is_empty()
            && !matches!(recipe.video_container.as_str(), "mp4" | "mkv"))
        || recipe
            .jpeg_quality
            .is_some_and(|value| !(1..=100).contains(&value))
        || recipe.video_crf.is_some_and(|value| value > 51)
        || recipe
            .deflicker_window
            .is_some_and(|value| !(1..=9).contains(&value))
    {
        return Err(AppError::Validation(
            "recipe contains unsupported output settings".into(),
        ));
    }
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
    native_menu::rebuild(&app, &lock(&state.recipes)?).map_err(|error| {
        AppError::Config(format!(
            "could not refresh the native presets menu: {error}"
        ))
    })?;
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn delete_recipe(state: State<'_, Arc<AppState>>, app: AppHandle, id: String) -> AppResult<()> {
    lock(&state.recipes)?.retain(|recipe| recipe.id != id);
    state.persist_settings()?;
    native_menu::rebuild(&app, &lock(&state.recipes)?).map_err(|error| {
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
    validate_start_input(&input)?;

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

    let mut reserved = HashSet::new();
    let mut jobs = Vec::with_capacity(media.len());
    for item in &media {
        if item.probe_status != "ready" {
            return Err(AppError::Validation(format!(
                "wait for {} to finish inspection",
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
        let job_id = Uuid::new_v4().to_string();
        let output = unique_output_path(&output_directory, &item.path, &input, &mut reserved);
        let message = build_job_message(&job_id, item, &output, &input, &selection)?;
        jobs.push((job_id, item.id.clone(), serde_json::to_string(&message)?));
    }

    for (id, media_id, request) in jobs {
        lock(&state.database)?.insert_job(&id, &media_id, &request)?;
    }
    worker::dispatch_next(&state, &app)?;
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn cancel_jobs(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    lock(&state.database)?.cancel_queued_jobs()?;
    let active = lock(&state.runtime)?.active_job_id.clone();
    if !active.is_empty() {
        lock(&state.database)?.set_job_status(&active, "cancelling")?;
        {
            let mut runtime = lock(&state.runtime)?;
            runtime.status_title = "Cancelling".into();
            runtime.status_detail = "The worker will stop at a safe boundary.".into();
        }
        worker::send(
            &state,
            &json!({"type": "cancel_request", "data": {"job_id": active}}),
        )?;
    }
    emit_state_changed(&app);
    Ok(())
}

#[tauri::command]
pub fn refresh_capabilities(state: State<'_, Arc<AppState>>) -> AppResult<()> {
    worker::send(&state, &json!({"type": "capabilities_request", "data": {}}))
}

#[tauri::command]
pub fn probe_path(state: State<'_, Arc<AppState>>, path: String) -> AppResult<()> {
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
    // is reserved for the completed output comparison; using it for sources
    // previously left the center canvas empty while populating result state.
    if queued_media.is_some() || media_kind(&canonical) == Some("video") {
        worker::send(
            &state,
            &json!({
                "type": "media_probe_request",
                "data": {"media_path": encoded, "max_dimension": 2048}
            }),
        )
    } else {
        worker::send(
            &state,
            &json!({
                "type": "preview_request",
                "data": {"image_path": encoded, "max_dimension": 2048}
            }),
        )
    }
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

#[tauri::command]
pub fn cancel_download(state: State<'_, Arc<AppState>>, model_id: String) -> AppResult<()> {
    downloads::cancel_download(&state, &model_id)
}

#[tauri::command]
pub fn open_result(state: State<'_, Arc<AppState>>, path: String) -> AppResult<()> {
    let authorized = authorized_result(&state, &path)?;
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

fn validate_settings(settings: &UiSettings) -> AppResult<()> {
    if !matches!(settings.interface_scale, 100 | 110 | 125) {
        return Err(AppError::Validation(
            "interface scale must be 100, 110, or 125 percent".into(),
        ));
    }
    if !settings.task.is_empty()
        && !matches!(settings.task.as_str(), "upscale" | "denoise" | "video")
    {
        return Err(AppError::Validation("unknown task".into()));
    }
    if !matches!(settings.output_format.as_str(), "png" | "jpg" | "tif")
        || !matches!(settings.video_container.as_str(), "mp4" | "mkv")
        || !matches!(settings.precision.as_str(), "fp32" | "fp16" | "bf16")
    {
        return Err(AppError::Validation(
            "unsupported output or precision setting".into(),
        ));
    }
    if !(1..=8).contains(&settings.output_scale)
        || !(32..=2048).contains(&settings.tile_size)
        || settings.halo > 256
        || !(1..=9).contains(&settings.deflicker_window)
        || !(0..=51).contains(&settings.video_crf)
        || !(1..=100).contains(&settings.jpeg_quality)
    {
        return Err(AppError::Validation(
            "one or more numeric settings are out of range".into(),
        ));
    }
    Ok(())
}

fn validate_start_input(input: &StartBatchInput) -> AppResult<()> {
    let settings = UiSettings {
        interface_scale: 100,
        batch_mode: input.batch_mode,
        task: input.task.clone(),
        selected_model_id: input.model_id.clone(),
        selected_video_model_id: input.video_model_id.clone(),
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
        video_crf: input.video_crf,
        enable_face_model: input.enable_face_model,
        allow_unsafe_pickle_model: input.allow_unsafe_pickle_model,
    };
    validate_settings(&settings)?;
    if input.media_ids.is_empty() || input.media_ids.len() > MAX_MEDIA_ITEMS {
        return Err(AppError::Validation("invalid batch size".into()));
    }
    Ok(())
}

#[derive(Clone)]
enum ModelSelection {
    Image {
        path: String,
        face_path: Option<String>,
        native_scale: u32,
    },
    Temporal {
        model_id: String,
        engine_kind: String,
        bundle_dir: String,
        temporal_window: u32,
        temporal_overlap: u32,
    },
}

fn resolve_model_selection(state: &AppState, input: &StartBatchInput) -> AppResult<ModelSelection> {
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
                "download and accept the face model terms before enabling face-aware processing"
                    .into(),
            ));
        }
        face.installed_path.clone()
    } else {
        None
    };
    Ok(ModelSelection::Image {
        path,
        face_path,
        native_scale: selected_model
            .map(|model| model.native_scale)
            .unwrap_or(input.output_scale),
    })
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
    input: &StartBatchInput,
    selection: &ModelSelection,
) -> AppResult<Value> {
    let output = output.to_string_lossy().into_owned();
    match selection {
        ModelSelection::Image {
            path,
            face_path,
            native_scale,
        } if input.task != "video" => Ok(json!({
            "type": "job_request",
            "data": {
                "job_id": job_id,
                "image_path": media.path,
                "model_path": path,
                "output_path": output,
                "output_format": input.output_format,
                "device": input.device,
                "tile_size": input.tile_size,
                "halo": input.halo,
                "precision": input.precision,
                "jpeg_quality": input.jpeg_quality,
                "preserve_metadata": input.preserve_metadata,
                "safe_memory": input.safe_memory,
                "preview_interval_ms": 80,
                "output_scale": if input.task == "denoise" { 1 } else { input.output_scale.min(*native_scale).max(1) },
                "face_model_path": face_path,
                "allow_unverified_checkpoint": input.allow_unsafe_pickle_model
            }
        })),
        ModelSelection::Image {
            path, face_path, ..
        } => Ok(json!({
            "type": "video_job_request",
            "data": {
                "job_id": job_id,
                "video_path": media.path,
                "model_path": path,
                "output_video_path": output,
                "container": input.video_container,
                "crf": input.video_crf,
                "fps": if media.fps > 0.0 { Some(media.fps) } else { None },
                "device": input.device,
                "tile_size": input.tile_size,
                "halo": input.halo,
                "precision": input.precision,
                "safe_memory": input.safe_memory,
                "keyframe_interval": null,
                "start_frame": null,
                "end_frame": null,
                "face_model_path": face_path,
                "deflicker": input.deflicker,
                "deflicker_window": input.deflicker_window,
                "model_kind": "spandrel_image",
                "bundle_dir": null,
                "temporal_window": 0,
                "temporal_overlap": 0,
                "target_resolution": 0,
                "output_scale": input.output_scale,
                "allow_unverified_checkpoint": input.allow_unsafe_pickle_model
            }
        })),
        ModelSelection::Temporal {
            model_id,
            engine_kind,
            bundle_dir,
            temporal_window,
            temporal_overlap,
        } => {
            let shortest = u64::from(media.width.min(media.height));
            let target = shortest
                .saturating_mul(u64::from(input.output_scale))
                .min(u64::from(u32::MAX));
            Ok(json!({
                "type": "video_job_request",
                "data": {
                    "job_id": job_id,
                    "video_path": media.path,
                    "model_path": "",
                    "output_video_path": output,
                    "container": input.video_container,
                    "crf": input.video_crf,
                    "fps": if media.fps > 0.0 { Some(media.fps) } else { None },
                    "device": input.device,
                    "tile_size": input.tile_size,
                    "halo": input.halo,
                    "precision": input.precision,
                    "safe_memory": input.safe_memory,
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
                    "output_scale": input.output_scale,
                    "allow_unverified_checkpoint": false
                }
            }))
        }
    }
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
    reserved: &mut HashSet<PathBuf>,
) -> PathBuf {
    let stem = Path::new(input_path)
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("output");
    let suffix = match input.task.as_str() {
        "denoise" => "_denoised".to_owned(),
        "video" => format!("_video_{}x", input.output_scale),
        _ => format!("_upscaled_{}x", input.output_scale),
    };
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
        let candidate = directory.join(format!("{stem}{suffix}{numbered}.{extension}"));
        if !candidate.exists() && reserved.insert(candidate.clone()) {
            return candidate;
        }
        counter = counter.saturating_add(1);
    }
}

fn media_kind(path: &Path) -> Option<&'static str> {
    let extension = extension(path)?;
    if IMAGE_EXTENSIONS.contains(&extension.as_str()) {
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

fn authorized_result(state: &AppState, requested: &str) -> AppResult<PathBuf> {
    let expected = lock(&state.runtime)?.last_output_path.clone();
    if expected.is_empty() {
        return Err(AppError::Validation("there is no completed output".into()));
    }
    let requested = fs::canonicalize(requested)?;
    let expected = fs::canonicalize(expected)?;
    if requested != expected || !requested.is_file() {
        return Err(AppError::Validation(
            "output path was not authorized".into(),
        ));
    }
    Ok(requested)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(task: &str) -> StartBatchInput {
        StartBatchInput {
            media_ids: vec!["media".into()],
            batch_mode: false,
            task: task.into(),
            model_id: "model".into(),
            video_model_id: "frame_by_frame".into(),
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
            video_crf: 18,
            enable_face_model: false,
            allow_unsafe_pickle_model: false,
        }
    }

    #[test]
    fn output_names_do_not_overwrite_existing_or_batched_files() {
        let directory = tempfile::tempdir().unwrap();
        let options = input("upscale");
        fs::write(directory.path().join("photo_upscaled_4x.png"), b"old").unwrap();
        let mut reserved = HashSet::new();
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
    }

    #[test]
    fn extension_matching_is_case_insensitive_and_bounded() {
        assert_eq!(media_kind(Path::new("A.DNG")), Some("image"));
        assert_eq!(media_kind(Path::new("pixel.BMP")), Some("image"));
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
    fn image_jobs_request_a_bounded_progressive_preview_rate() {
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
            error: String::new(),
            selected: true,
        };
        let selection = ModelSelection::Image {
            path: "/models/upscaler.safetensors".into(),
            face_path: None,
            native_scale: 4,
        };

        let message = build_job_message(
            "job",
            &media,
            Path::new("/output/photo.png"),
            &input("upscale"),
            &selection,
        )
        .unwrap();

        assert_eq!(message["data"]["preview_interval_ms"], 80);
    }
}
