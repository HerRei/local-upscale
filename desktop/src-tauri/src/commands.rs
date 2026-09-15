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
    catalog, downloads,
    error::{AppError, AppResult},
    integrations,
    launch::LaunchIntent,
    native_menu,
    state::{emit_state_changed, lock, AppState},
    types::{
        AppSnapshot, CatalogModel, Recipe, StartBatchInput, StartBenchmarkInput, UiSettings,
        VideoComparisonSources,
    },
    worker,
};

const IMAGE_EXTENSIONS: &[&str] = &["jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp", "dng"];
const VIDEO_EXTENSIONS: &[&str] = &[
    "mp4", "mov", "m4v", "mkv", "webm", "avi", "mpg", "mpeg", "mpe", "vob", "ts", "mts", "m2ts",
    "wmv", "asf", "flv", "f4v", "3gp", "3g2", "ogv", "divx",
];
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
    *lock(&state.settings)? = settings;
    state.persist_settings()
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
    if (!recipe.output_format.is_empty()
        && !matches!(
            recipe.output_format.as_str(),
            "png" | "jpg" | "tif" | "webp"
        ))
        || (!recipe.video_container.is_empty()
            && !matches!(recipe.video_container.as_str(), "mp4" | "mkv"))
        || (!recipe.video_codec.is_empty()
            && !matches!(
                recipe.video_codec.as_str(),
                "av1" | "vp9" | "ffv1" | "h264" | "hevc"
            ))
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
    native_menu::rebuild(&app, &lock(&state.recipes)?).map_err(|error| {
        AppError::Config(format!(
            "could not refresh the native presets menu: {error}"
        ))
    })?;
    emit_state_changed(&app);
    Ok(())
}

fn validate_recipe_stages(recipe: &Recipe) -> AppResult<()> {
    if recipe.stages.is_empty() {
        // Recipes saved by pre-v0.0.11 previews are deliberately accepted and
        // reconstructed from their legacy top-level fields by the frontend.
        return Ok(());
    }
    if recipe.stages.len() > 3
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
                    && index <= 1
                    && (index == 0 || matches!(kinds[0], "deblock" | "restore"))
                    && kinds[index + 1..]
                        .iter()
                        .all(|kind| *kind == "face_restore")
                    && kinds[index + 1..].len() <= 1
            })
        }
        "denoise" => kinds.as_slice() == ["restore"],
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
    validate_start_input(&input, external_codecs_available(&state))?;

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
        let job_id = Uuid::new_v4().to_string();
        let output = unique_output_path(&output_directory, &item.path, &input, &mut reserved);
        let message = build_job_message(
            &job_id,
            item,
            &output,
            &state.paths.work_root,
            &input,
            &selection,
        )?;
        jobs.push((job_id, item.id.clone(), serde_json::to_string(&message)?));
    }

    for (id, media_id, request) in jobs {
        lock(&state.database)?.insert_job(&id, &media_id, &request)?;
    }
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
        lock(&state.database)?.set_job_status(&active, "cancelling")?;
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
        runtime.active_job_id = job_id.clone();
        runtime.status_title = "Benchmark preparing".into();
        runtime.status_detail = "Loading the fixed LocalSR benchmark workload.".into();
        runtime.progress = 0.0;
    }
    if let Err(error) = worker::send(&state, &message) {
        lock(&state.worker.cancellation)?.finish()?;
        lock(&state.runtime)?.active_job_id.clear();
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
    let file_name = destination
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| AppError::Validation("benchmark destination is invalid".into()))?;
    let temporary = parent.join(format!(
        ".{file_name}.localsr-benchmark-{}.tmp",
        Uuid::new_v4()
    ));
    let export_result = (|| -> AppResult<()> {
        let mut output = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        output.write_all(bytes)?;
        output.sync_all()?;
        drop(output);

        // hard_link() creates the destination without replacing an existing
        // file, then unlinking our same-directory temporary makes the publish
        // atomic and no-clobber on supported desktop filesystems.
        fs::hard_link(&temporary, destination).map_err(|error| {
            if destination.exists() {
                AppError::Validation(
                    "the chosen benchmark file already exists; choose a new name".into(),
                )
            } else {
                AppError::Io(error)
            }
        })?;
        fs::remove_file(&temporary)?;
        Ok(())
    })();
    if export_result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    export_result
}

#[tauri::command]
pub fn request_image_comparison(
    state: State<'_, Arc<AppState>>,
    media_id: String,
) -> AppResult<()> {
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
        &state,
        &json!({
            "type": "preview_request", "data": {"image_path": output, "max_dimension": 2048, "comparison": true}
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
                "video comparison is available only for completed video jobs".into(),
            ));
        }
        let output = database
            .latest_completed_output_for_media(media_id)?
            .ok_or_else(|| AppError::Validation("this video has no completed output".into()))?;
        (PathBuf::from(media.path), PathBuf::from(output))
    };
    let original = fs::canonicalize(original)?;
    let enhanced = fs::canonicalize(enhanced)?;
    if !original.is_file() || !enhanced.is_file() {
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
    let enhanced = playback(enhanced)?;
    if cancel.load(std::sync::atomic::Ordering::SeqCst) {
        return Err(AppError::Validation(
            "Playback conversion cancelled.".into(),
        ));
    }
    allow_video_preview_pair(&app.asset_protocol_scope(), &original, &enhanced)?;
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
            Some(server.url_for(&enhanced)?),
        )
    };
    #[cfg(not(target_os = "linux"))]
    let (original_url, enhanced_url) = (None, None);
    Ok(VideoComparisonSources {
        original_path: original.to_string_lossy().into_owned(),
        enhanced_path: enhanced.to_string_lossy().into_owned(),
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
    enhanced: &Path,
) -> AppResult<()> {
    // Authorize only these two canonical files for this app session. Tauri's
    // forbid_file is a permanent deny, not the inverse of allow_file: revoking
    // on selection change prevented every subsequent visit to the same video.
    // The unmounted player releases its media elements; no folder is authorized.
    scope.allow_file(original).map_err(|error| {
        AppError::Config(format!(
            "could not authorize the original video preview: {error}"
        ))
    })?;
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
    // is reserved for the completed output comparison; using it for sources
    // previously left the center canvas empty while populating result state.
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
        let model = catalog
            .models
            .iter()
            .find(|model| model.model_id == model_id)
            .ok_or_else(|| AppError::Validation("unknown image model".into()))?;
        if !model.installed {
            return Err(AppError::Validation("the model is not installed".into()));
        }
        state
            .paths
            .model_root
            .join(crate::catalog::safe_model_filename(&model.filename)?)
    };
    if path.is_file() {
        fs::remove_file(&path)?;
    }
    state.refresh_catalog()?;
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
pub fn detect_external_ffmpeg() -> Vec<String> {
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
    found
}

/// Open the FFmpeg project's download page in the user's browser. Installing
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
    let attempts: [(&str, Vec<&str>); 5] = [
        ("x-terminal-emulator", vec!["-e", "bash", "-c", &script]),
        ("gnome-terminal", vec!["--", "bash", "-c", &script]),
        ("konsole", vec!["-e", "bash", "-c", &script]),
        (
            "xfce4-terminal",
            vec!["-e", &format!("bash -c \"{script}\"")],
        ),
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
    if !matches!(
        settings.output_format.as_str(),
        "png" | "jpg" | "tif" | "webp"
    ) || !matches!(settings.video_container.as_str(), "mp4" | "mkv")
        || !matches!(
            settings.video_codec.as_str(),
            "av1" | "vp9" | "ffv1" | "h264" | "hevc"
        )
        || !matches!(settings.video_hdr_mode.as_str(), "tone_map" | "preserve")
        || !matches!(settings.precision.as_str(), "fp32" | "fp16" | "bf16")
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

fn validate_start_input(input: &StartBatchInput, external_codecs: bool) -> AppResult<()> {
    let settings = UiSettings {
        interface_scale: 100,
        batch_mode: input.batch_mode,
        task: input.task.clone(),
        selected_model_id: input.model_id.clone(),
        selected_video_model_id: input.video_model_id.clone(),
        preprocess_model_id: input.preprocess_model_id.clone(),
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
        enable_face_model: input.enable_face_model,
        face_fidelity: input.face_fidelity,
        enable_live_preview: input.enable_live_preview,
        allow_unsafe_pickle_model: input.allow_unsafe_pickle_model,
        ..UiSettings::default()
    };
    validate_settings(&settings)?;
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
        preprocess: Option<ResolvedImageStage>,
        face_path: Option<String>,
        face_model_id: String,
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

#[derive(Clone)]
struct ResolvedImageStage {
    kind: &'static str,
    model_id: String,
    path: String,
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
    let preprocess = if input.preprocess_model_id.is_empty() {
        None
    } else {
        if input.task != "upscale" {
            return Err(AppError::Validation(
                "pre-processing can only be chained before an image upscale".into(),
            ));
        }
        let model = catalog
            .models
            .iter()
            .find(|model| model.model_id == input.preprocess_model_id)
            .ok_or_else(|| AppError::Validation("unknown pre-processing model".into()))?;
        if model.native_scale != 1 || model.purposes.iter().any(|purpose| purpose == "face") {
            return Err(AppError::Validation(
                "the pre-processing stage must use a 1× restoration model".into(),
            ));
        }
        if !model.installed {
            return Err(AppError::Validation(
                "download and verify the selected pre-processing model first".into(),
            ));
        }
        Some(ResolvedImageStage {
            kind: if model.model_id == "fbcnn_color" {
                "deblock"
            } else {
                "restore"
            },
            model_id: model.model_id.clone(),
            path: model.installed_path.clone().unwrap_or_default(),
        })
    };
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
                "preview_enabled": input.enable_live_preview,
                "preview_max_fps": 2.0,
                "preview_max_dimension": 320,
                "output_scale": if input.task == "denoise" { 1 } else { input.output_scale.min(*native_scale).max(1) },
                "face_model_path": face_path,
                "face_fidelity": f64::from(input.face_fidelity) / 100.0,
                "stages": image_pipeline_stages(
                    preprocess.as_ref(),
                    model_id,
                    path,
                    face_path.as_deref(),
                    face_model_id,
                    input.face_fidelity,
                    &input.task,
                ),
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
                "preview_max_dimension": 320
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
                    "preview_max_dimension": 320
                }
            }))
        }
    }
}

fn image_pipeline_stages(
    preprocess: Option<&ResolvedImageStage>,
    model_id: &str,
    model_path: &str,
    face_path: Option<&str>,
    face_model_id: &str,
    face_fidelity: u32,
    task: &str,
) -> Vec<Value> {
    let mut stages = Vec::with_capacity(3);
    if let Some(stage) = preprocess {
        stages.push(json!({
            "kind": stage.kind,
            "model_id": stage.model_id,
            "model_path": stage.path,
        }));
    }
    stages.push(json!({
        "kind": if task == "denoise" { "restore" } else { "upscale" },
        "model_id": model_id,
        "model_path": model_path,
    }));
    if let Some(path) = face_path {
        stages.push(json!({
            "kind": "face_restore",
            "model_id": face_model_id,
            "model_path": path,
            "fidelity": f64::from(face_fidelity) / 100.0,
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
    reserved: &mut HashSet<PathBuf>,
) -> PathBuf {
    let stem = Path::new(input_path)
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("output");
    let suffix = match input.task.as_str() {
        "denoise" => "_denoised".to_owned(),
        "video"
            if input.video_model_id != "frame_by_frame" && input.video_target_resolution > 0 =>
        {
            format!("_video_{}px", input.video_target_resolution)
        }
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
    use crate::types::RecipeStage;

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
            allow_video_preview_pair(&scope, &files[original], &files[enhanced]).unwrap();
            assert!(scope.is_allowed(&files[original]));
            assert!(scope.is_allowed(&files[enhanced]));
            assert!(!scope.is_allowed(&files[3]));
            assert!(!scope.is_allowed(directory.path()));
        }
        assert!(scope.forbidden_patterns().is_empty());
    }

    fn input(task: &str) -> StartBatchInput {
        StartBatchInput {
            media_ids: vec!["media".into()],
            batch_mode: false,
            task: task.into(),
            model_id: "model".into(),
            video_model_id: "frame_by_frame".into(),
            preprocess_model_id: String::new(),
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
            enable_face_model: false,
            face_fidelity: 70,
            enable_live_preview: true,
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
            preprocess: None,
            face_path: None,
            face_model_id: String::new(),
            native_scale: 4,
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
            preprocess: Some(ResolvedImageStage {
                kind: "deblock",
                model_id: "fbcnn_color".into(),
                path: "/models/fbcnn.pth".into(),
            }),
            face_path: Some("/models/face.pth".into()),
            face_model_id: "hat_l_x4_face".into(),
            native_scale: 4,
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
        };
        assert!(validate_recipe_stages(&legacy).is_ok());

        let mut invalid = legacy;
        invalid.stages = vec![
            RecipeStage {
                kind: "upscale".into(),
                model_id: "first".into(),
                fidelity: None,
            },
            RecipeStage {
                kind: "upscale".into(),
                model_id: "second".into(),
                fidelity: None,
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
