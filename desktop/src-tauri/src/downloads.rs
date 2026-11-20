use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Weak,
    },
    time::Duration,
};

use futures_util::StreamExt;
use reqwest::{header, Client, StatusCode, Url};
use serde_json::json;
use tauri::{AppHandle, Emitter};
use tokio::{
    fs,
    io::AsyncWriteExt,
    sync::{Mutex as AsyncMutex, Notify, Semaphore, SemaphorePermit},
};

use crate::{
    catalog::{safe_model_filename, sha256_file, verified_file},
    error::{AppError, AppResult},
    state::{emit_state_changed, lock, AppState},
    types::{
        CatalogManifest, CatalogModel, CatalogVideoModel, ModelDownloadStatus, ModelFile,
        WorkerEnvelope,
    },
};

pub const MAX_CONCURRENT_DOWNLOADS: usize = 3;

#[derive(Clone, Debug)]
pub(crate) struct PlannedFile {
    pub destination: PathBuf,
    pub partial: PathBuf,
    pub expected_size: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum DownloadPhase {
    Queued,
    Downloading,
}

pub(crate) struct DownloadEntry {
    pub order: u64,
    pub status: DownloadPhase,
    pub progress: f64,
    pub downloaded_bytes: u64,
    pub total_bytes: u64,
    pub planned_files: Vec<PlannedFile>,
    pub cancellation: Arc<AtomicBool>,
    pub cancel_notify: Arc<Notify>,
}

#[derive(Clone, Copy, Default)]
struct DownloadEnv {
    #[cfg(test)]
    allow_local_http: bool,
    #[cfg(test)]
    available_space_override: Option<u64>,
}

impl DownloadEnv {
    fn is_allowed_url(self, parsed: &Url) -> bool {
        if parsed.host_str().is_none() {
            return false;
        }
        if parsed.scheme() == "https" {
            return true;
        }
        #[cfg(test)]
        if self.allow_local_http
            && parsed.scheme() == "http"
            && parsed.host_str() == Some("127.0.0.1")
        {
            return true;
        }
        false
    }

    fn available_space(self, path: &Path) -> AppResult<u64> {
        #[cfg(test)]
        if let Some(bytes) = self.available_space_override {
            return Ok(bytes);
        }
        Ok(fs2::available_space(path)?)
    }

    fn client(self) -> AppResult<Client> {
        let policy = reqwest::redirect::Policy::custom(move |attempt| {
            let allowed = self.is_allowed_url(attempt.url());
            if attempt.previous().len() >= 10 || !allowed {
                attempt.stop()
            } else {
                attempt.follow()
            }
        });
        Ok(Client::builder()
            .user_agent(concat!("LocalSR/", env!("CARGO_PKG_VERSION")))
            .connect_timeout(Duration::from_secs(20))
            .redirect(policy)
            .build()?)
    }
}

/// A multi-file bundle by id: a video engine or a frame-interpolation model.
fn bundle_model(catalog: &CatalogManifest, model_id: &str) -> Option<CatalogVideoModel> {
    catalog
        .video_models
        .iter()
        .chain(catalog.interpolation_models.iter())
        .chain(catalog.audio_models.iter())
        .find(|model| model.model_id == model_id)
        .cloned()
}

pub async fn download_model(
    state: Arc<AppState>,
    app: AppHandle,
    model_id: String,
    accepted_terms: bool,
) -> AppResult<()> {
    download_model_with_env(state, app, model_id, accepted_terms, DownloadEnv::default()).await
}

async fn download_model_with_env<R: tauri::Runtime>(
    state: Arc<AppState>,
    app: AppHandle<R>,
    model_id: String,
    accepted_terms: bool,
    env: DownloadEnv,
) -> AppResult<()> {
    let target = resolve_target(&state, &model_id)?;
    target.validate_policy(accepted_terms)?;
    let planned_files = target.planned_files(&state, env)?;
    let total_bytes: u64 = planned_files.iter().map(|file| file.expected_size).sum();

    let cancellation = Arc::new(AtomicBool::new(false));
    let cancel_notify = Arc::new(Notify::new());
    {
        crate::updates::ensure_not_installing(&state)?;
        let _scheduler = lock(&state.scheduler)?;
        crate::updates::ensure_not_installing(&state)?;
        let mut downloads = lock(&state.downloads)?;
        if downloads.contains_key(&model_id) {
            return Err(AppError::Download(
                "this model is already downloading".into(),
            ));
        }
        let order = state.download_sequence.fetch_add(1, Ordering::Relaxed);
        downloads.insert(
            model_id.clone(),
            DownloadEntry {
                order,
                status: DownloadPhase::Queued,
                progress: 0.0,
                downloaded_bytes: 0,
                total_bytes,
                planned_files: planned_files.clone(),
                cancellation: cancellation.clone(),
                cancel_notify: cancel_notify.clone(),
            },
        );
    }
    let slot = DownloadSlot::new(&state, &model_id);
    sync_runtime_downloads(&state, None);
    emit_download_event(
        &app,
        "download-queued",
        json!({"model_id": model_id, "total_bytes": total_bytes}),
    );
    emit_state_changed(&app);

    let result = match acquire_download_slot(
        &state.download_semaphore,
        cancellation.as_ref(),
        cancel_notify.as_ref(),
    )
    .await
    {
        Ok(permit) => {
            let start_check = begin_running_download(&state, &model_id, &planned_files, env).await;
            let outcome = match start_check {
                Ok(()) => {
                    sync_runtime_downloads(&state, None);
                    emit_download_event(
                        &app,
                        "download-started",
                        json!({"model_id": model_id, "total_bytes": total_bytes}),
                    );
                    emit_state_changed(&app);
                    target
                        .download(
                            &state,
                            &app,
                            &model_id,
                            cancellation.as_ref(),
                            cancel_notify.as_ref(),
                            env,
                        )
                        .await
                }
                Err(error) => Err(error),
            };
            drop(permit);
            outcome
        }
        Err(error) => Err(error),
    };

    drop(slot);
    sync_runtime_downloads(&state, Some(&result));
    if result.is_ok() {
        // The file is installed and verified; a failed catalog refresh must not
        // leave its row stuck at 100%, so report completion first.
        let refreshed = state.refresh_catalog();
        emit_download_event(&app, "download-completed", json!({"model_id": model_id}));
        if let Err(error) = refreshed {
            emit_state_changed(&app);
            return Err(error);
        }
    } else if is_cancelled_error(&result) {
        emit_download_event(&app, "download-cancelled", json!({"model_id": model_id}));
    } else if let Err(error) = &result {
        emit_download_event(
            &app,
            "download-failed",
            json!({"model_id": model_id, "error_message": error.to_string()}),
        );
    }
    emit_state_changed(&app);
    result
}

fn resolve_target(state: &AppState, model_id: &str) -> AppResult<DownloadTarget> {
    let catalog = lock(&state.catalog)?;
    if let Some(model) = catalog
        .models
        .iter()
        .find(|model| model.model_id == model_id)
    {
        Ok(DownloadTarget::Image(Box::new(model.clone())))
    } else if let Some(model) = bundle_model(&catalog, model_id) {
        Ok(DownloadTarget::Video(Box::new(model)))
    } else if let Some(model) = crate::editing::catalog(&state.paths)?
        .into_iter()
        .find(|model| model.model_id == model_id)
    {
        Ok(DownloadTarget::Edit(Box::new(model)))
    } else {
        Err(AppError::Validation("unknown model identifier".into()))
    }
}

fn is_cancelled(error: &AppError) -> bool {
    matches!(error, AppError::Download(message) if message == "download cancelled")
}

fn is_cancelled_error(result: &AppResult<()>) -> bool {
    matches!(result, Err(error) if is_cancelled(error))
}

async fn acquire_download_slot<'a>(
    semaphore: &'a Semaphore,
    cancellation: &AtomicBool,
    cancel_notify: &Notify,
) -> AppResult<SemaphorePermit<'a>> {
    loop {
        let notified = cancel_notify.notified();
        tokio::pin!(notified);
        check_cancel(cancellation)?;
        tokio::select! {
            permit = semaphore.acquire() => {
                let permit = permit.map_err(|_| AppError::Download("download queue closed".into()))?;
                check_cancel(cancellation)?;
                return Ok(permit);
            }
            _ = &mut notified => {
                check_cancel(cancellation)?;
            }
        }
    }
}

async fn begin_running_download(
    state: &AppState,
    model_id: &str,
    planned_files: &[PlannedFile],
    env: DownloadEnv,
) -> AppResult<()> {
    for file in planned_files {
        if let Some(parent) = file.destination.parent() {
            fs::create_dir_all(parent).await?;
        }
    }
    let check_dir = planned_files
        .first()
        .and_then(|file| file.destination.parent())
        .unwrap_or(&state.paths.model_root);
    let mut downloads = lock(&state.downloads)?;
    if let Some(entry) = downloads.get(model_id) {
        check_cancel(entry.cancellation.as_ref())?;
    }
    check_summed_disk_space_locked(&downloads, planned_files, check_dir, env)?;
    if let Some(entry) = downloads.get_mut(model_id) {
        entry.status = DownloadPhase::Downloading;
    }
    Ok(())
}

fn check_summed_disk_space(
    state: &AppState,
    fallback_file: &PlannedFile,
    check_dir: &Path,
    env: DownloadEnv,
) -> AppResult<()> {
    let downloads = lock(&state.downloads)?;
    check_summed_disk_space_locked(
        &downloads,
        std::slice::from_ref(fallback_file),
        check_dir,
        env,
    )
}

fn check_summed_disk_space_locked(
    downloads: &HashMap<String, DownloadEntry>,
    extra_files: &[PlannedFile],
    check_dir: &Path,
    env: DownloadEnv,
) -> AppResult<()> {
    let running_files = downloads
        .values()
        .filter(|entry| entry.status == DownloadPhase::Downloading)
        .flat_map(|entry| entry.planned_files.iter());
    let mut unique: HashMap<&Path, &PlannedFile> = HashMap::new();
    for file in running_files.chain(extra_files.iter()) {
        unique.entry(file.destination.as_path()).or_insert(file);
    }
    let mut total_remaining: u64 = 0;
    for file in unique.into_values() {
        let installed = file
            .destination
            .metadata()
            .is_ok_and(|metadata| metadata.is_file() && metadata.len() == file.expected_size);
        if installed {
            continue;
        }
        let partial_size = std::fs::metadata(&file.partial)
            .map(|metadata| metadata.len())
            .ok()
            .filter(|size| *size <= file.expected_size)
            .unwrap_or(0);
        total_remaining =
            total_remaining.saturating_add(file.expected_size.saturating_sub(partial_size));
    }
    let available = env.available_space(check_dir)?;
    let needed = total_remaining.saturating_mul(115) / 100;
    if available < needed {
        return Err(AppError::Download(format!(
            "not enough disk space: the download needs about {} MB free, but only {} MB is available",
            needed / 1_000_000,
            available / 1_000_000
        )));
    }
    Ok(())
}

fn sync_runtime_downloads(state: &AppState, finished: Option<&AppResult<()>>) {
    let statuses = {
        let downloads = state
            .downloads
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let mut entries: Vec<(&String, &DownloadEntry)> = downloads.iter().collect();
        entries.sort_by_key(|(_, entry)| entry.order);
        entries
            .into_iter()
            .map(|(model_id, entry)| ModelDownloadStatus {
                model_id: model_id.clone(),
                status: match entry.status {
                    DownloadPhase::Queued => "queued".into(),
                    DownloadPhase::Downloading => "downloading".into(),
                },
                progress: entry.progress,
                downloaded_bytes: entry.downloaded_bytes,
                total_bytes: entry.total_bytes,
            })
            .collect::<Vec<_>>()
    };

    let mut runtime = state
        .runtime
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let primary = statuses
        .iter()
        .find(|item| item.status == "downloading")
        .or_else(|| statuses.first());
    runtime.download_model_id = primary
        .map(|item| item.model_id.clone())
        .unwrap_or_default();
    runtime.download_progress = primary.map(|item| item.progress).unwrap_or(0.0);
    runtime.downloads = statuses;

    // A running job owns the status line; per-model rows show download progress.
    if !runtime.active_job_id.is_empty() {
        return;
    }
    if runtime.downloads.is_empty() {
        if let Some(outcome) = finished {
            match outcome {
                Ok(()) => {
                    runtime.download_progress = 100.0;
                    runtime.status_title = "Model ready".into();
                    runtime.status_detail = "Checksum verification succeeded.".into();
                }
                Err(error) if is_cancelled(error) => {
                    runtime.download_progress = 0.0;
                    runtime.status_title = "Download paused".into();
                    runtime.status_detail = "The verified download can resume later.".into();
                }
                Err(error) => {
                    runtime.download_progress = 0.0;
                    runtime.status_title = "Download failed".into();
                    runtime.status_detail = error.to_string();
                }
            }
        }
    } else if runtime.downloads.len() == 1 {
        runtime.status_title = "Downloading model".into();
        if runtime.download_progress > 0.0 {
            runtime.status_detail =
                format!("Verified download · {:.1}%", runtime.download_progress);
        } else {
            runtime.status_detail = "Downloads are pinned and verified before installation.".into();
        }
    } else {
        let active_count = runtime
            .downloads
            .iter()
            .filter(|item| item.status == "downloading")
            .count();
        let queued_count = runtime.downloads.len().saturating_sub(active_count);
        runtime.status_title = format!("Downloading {} models", active_count.max(1));
        runtime.status_detail = if queued_count > 0 {
            format!("{active_count} active · {queued_count} waiting")
        } else {
            format!("{active_count} active · pinned and verified before installation")
        };
    }
}

/// A model's place among the running downloads. Dropping it frees the place however
/// the download ended, so a failure or a panic never blocks the next download.
struct DownloadSlot<'a> {
    state: &'a AppState,
    model_id: &'a str,
}

impl<'a> DownloadSlot<'a> {
    fn new(state: &'a AppState, model_id: &'a str) -> Self {
        Self { state, model_id }
    }
}

impl Drop for DownloadSlot<'_> {
    fn drop(&mut self) {
        {
            let mut downloads = self
                .state
                .downloads
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            downloads.remove(self.model_id);
        }
        sync_runtime_downloads(self.state, None);
    }
}

pub fn cancel_download(state: &AppState, model_id: &str) -> AppResult<()> {
    let downloads = lock(&state.downloads)?;
    let entry = downloads
        .get(model_id)
        .ok_or_else(|| AppError::Validation("that model is not being downloaded".into()))?;
    entry.cancellation.store(true, Ordering::SeqCst);
    entry.cancel_notify.notify_waiters();
    Ok(())
}

fn file_lock_for(state: &AppState, destination: &Path) -> AppResult<Arc<AsyncMutex<()>>> {
    let mut locks = lock(&state.download_file_locks)?;
    locks.retain(|_, weak| weak.strong_count() > 0);
    if let Some(existing) = locks.get(destination).and_then(Weak::upgrade) {
        return Ok(existing);
    }
    let created = Arc::new(AsyncMutex::new(()));
    locks.insert(destination.to_path_buf(), Arc::downgrade(&created));
    Ok(created)
}

async fn lock_file_with_cancel<'a>(
    mutex: &'a AsyncMutex<()>,
    cancellation: &AtomicBool,
    cancel_notify: &Notify,
) -> AppResult<tokio::sync::MutexGuard<'a, ()>> {
    loop {
        let notified = cancel_notify.notified();
        tokio::pin!(notified);
        check_cancel(cancellation)?;
        tokio::select! {
            guard = mutex.lock() => {
                check_cancel(cancellation)?;
                return Ok(guard);
            }
            _ = &mut notified => {
                check_cancel(cancellation)?;
            }
        }
    }
}

enum DownloadTarget {
    Image(Box<CatalogModel>),
    Video(Box<CatalogVideoModel>),
    Edit(Box<crate::editing::EditModel>),
}

impl DownloadTarget {
    fn validate_policy(&self, accepted_terms: bool) -> AppResult<()> {
        let (allowed, terms_required) = match self {
            Self::Image(model) => (
                model.automated_download_allowed,
                model.terms_acceptance_required,
            ),
            Self::Video(model) => (
                model.automated_download_allowed,
                model.terms_acceptance_required,
            ),
            Self::Edit(model) => (
                model.automated_download_allowed,
                model.terms_acceptance_required,
            ),
        };
        if !allowed {
            return Err(AppError::Validation(
                "this model requires a manual upstream download because its terms are unclear"
                    .into(),
            ));
        }
        if terms_required && !accepted_terms {
            return Err(AppError::Validation(
                "review and accept the model-specific license before downloading".into(),
            ));
        }
        Ok(())
    }

    fn planned_files(&self, state: &AppState, env: DownloadEnv) -> AppResult<Vec<PlannedFile>> {
        match self {
            Self::Image(model) => {
                safe_model_filename(&model.filename)?;
                validate_artifact_metadata_with_env(
                    &model.download_url,
                    model.size_bytes,
                    &model.sha256,
                    env,
                )?;
                let destination = state.paths.model_root.join(&model.filename);
                Ok(vec![PlannedFile {
                    partial: partial_path(&destination),
                    destination,
                    expected_size: model.size_bytes,
                }])
            }
            Self::Video(model) => planned_bundle_files(state, &model.family, &model.files, env),
            Self::Edit(model) => planned_bundle_files(state, &model.family, &model.files, env),
        }
    }

    async fn download<R: tauri::Runtime>(
        &self,
        state: &Arc<AppState>,
        app: &AppHandle<R>,
        model_id: &str,
        cancellation: &AtomicBool,
        cancel_notify: &Notify,
        env: DownloadEnv,
    ) -> AppResult<()> {
        let client = env.client()?;
        match self {
            Self::Image(model) => {
                safe_model_filename(&model.filename)?;
                let destination = state.paths.model_root.join(&model.filename);
                download_one(
                    &client,
                    app,
                    state,
                    model_id,
                    &model.download_url,
                    &destination,
                    model.size_bytes,
                    &model.sha256,
                    0,
                    model.size_bytes,
                    cancellation,
                    cancel_notify,
                    env,
                )
                .await
            }
            Self::Video(model) => {
                download_bundle(
                    &client,
                    state,
                    app,
                    model_id,
                    &model.family,
                    &model.files,
                    cancellation,
                    cancel_notify,
                    env,
                )
                .await
            }
            Self::Edit(model) => {
                download_bundle(
                    &client,
                    state,
                    app,
                    model_id,
                    &model.family,
                    &model.files,
                    cancellation,
                    cancel_notify,
                    env,
                )
                .await
            }
        }
    }
}

fn planned_bundle_files(
    state: &AppState,
    family: &str,
    files: &[ModelFile],
    env: DownloadEnv,
) -> AppResult<Vec<PlannedFile>> {
    safe_model_filename(family)?;
    let mut planned = Vec::with_capacity(files.len());
    for file in files {
        safe_model_filename(&file.filename)?;
        validate_artifact_metadata_with_env(
            &file.download_url,
            file.size_bytes,
            &file.sha256,
            env,
        )?;
        let destination = state.paths.model_root.join(family).join(&file.filename);
        planned.push(PlannedFile {
            partial: partial_path(&destination),
            destination,
            expected_size: file.size_bytes,
        });
    }
    Ok(planned)
}

fn partial_path(destination: &Path) -> PathBuf {
    destination.with_extension(format!(
        "{}.part",
        destination
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("download")
    ))
}

#[allow(clippy::too_many_arguments)]
async fn download_bundle<R: tauri::Runtime>(
    client: &Client,
    state: &Arc<AppState>,
    app: &AppHandle<R>,
    model_id: &str,
    family: &str,
    files: &[ModelFile],
    cancellation: &AtomicBool,
    cancel_notify: &Notify,
    env: DownloadEnv,
) -> AppResult<()> {
    safe_model_filename(family)?;
    let total = files.iter().map(|file| file.size_bytes).sum();
    let mut completed = 0;
    for file in files {
        check_cancel(cancellation)?;
        safe_model_filename(&file.filename)?;
        let destination = state.paths.model_root.join(family).join(&file.filename);
        if verified_file_async(&destination, file.size_bytes, &file.sha256).await? {
            completed += file.size_bytes;
            publish_progress(state, app, model_id, completed, total);
            continue;
        }
        download_one(
            client,
            app,
            state,
            model_id,
            &file.download_url,
            &destination,
            file.size_bytes,
            &file.sha256,
            completed,
            total,
            cancellation,
            cancel_notify,
            env,
        )
        .await?;
        completed += file.size_bytes;
        publish_progress(state, app, model_id, completed, total);
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
async fn download_one<R: tauri::Runtime>(
    client: &Client,
    app: &AppHandle<R>,
    state: &Arc<AppState>,
    model_id: &str,
    url: &str,
    destination: &Path,
    expected_size: u64,
    expected_sha256: &str,
    completed_before: u64,
    overall_total: u64,
    cancellation: &AtomicBool,
    cancel_notify: &Notify,
    env: DownloadEnv,
) -> AppResult<()> {
    validate_artifact_metadata_with_env(url, expected_size, expected_sha256, env)?;
    let file_mutex = file_lock_for(state, destination)?;
    let _file_guard = lock_file_with_cancel(&file_mutex, cancellation, cancel_notify).await?;
    if verified_file_async(destination, expected_size, expected_sha256).await? {
        return Ok(());
    }
    let partial = partial_path(destination);
    let parent = destination
        .parent()
        .ok_or_else(|| AppError::Validation("model destination has no parent".into()))?;
    fs::create_dir_all(parent).await?;
    if reuse_complete_partial(&partial, destination, expected_size, expected_sha256).await? {
        return Ok(());
    }
    let fallback = PlannedFile {
        destination: destination.to_path_buf(),
        partial: partial.clone(),
        expected_size,
    };
    check_summed_disk_space(state, &fallback, parent, env)?;

    for attempt in 1..=4_u64 {
        check_cancel(cancellation)?;
        match download_attempt(
            client,
            app,
            state,
            model_id,
            url,
            &partial,
            expected_size,
            completed_before,
            overall_total,
            cancellation,
            env,
        )
        .await
        {
            Ok(()) => break,
            Err(AppError::Download(message)) if message == "download cancelled" => {
                return Err(AppError::Download(message));
            }
            Err(error) => {
                if attempt == 4 {
                    return Err(AppError::Download(format!(
                        "download did not complete after four attempts: {error}"
                    )));
                }
                tokio::time::sleep(Duration::from_secs(attempt)).await;
            }
        }
    }

    let actual_size = fs::metadata(&partial).await?.len();
    if actual_size != expected_size {
        return Err(AppError::Download(format!(
            "download size mismatch: expected {expected_size}, received {actual_size}"
        )));
    }
    let partial_for_hash = partial.clone();
    let actual_sha256 = tokio::task::spawn_blocking(move || sha256_file(&partial_for_hash))
        .await
        .map_err(|error| AppError::Download(format!("checksum task failed: {error}")))??;
    if !actual_sha256.eq_ignore_ascii_case(expected_sha256) {
        let _ = fs::remove_file(&partial).await;
        return Err(AppError::Download(
            "download checksum mismatch; the untrusted partial file was removed".into(),
        ));
    }

    install_atomically(&partial, destination).await?;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
async fn download_attempt<R: tauri::Runtime>(
    client: &Client,
    app: &AppHandle<R>,
    state: &Arc<AppState>,
    model_id: &str,
    url: &str,
    partial: &Path,
    expected_size: u64,
    completed_before: u64,
    overall_total: u64,
    cancellation: &AtomicBool,
    env: DownloadEnv,
) -> AppResult<()> {
    let existing = fs::metadata(partial)
        .await
        .map(|metadata| metadata.len())
        .unwrap_or(0);
    let offset = if existing <= expected_size {
        existing
    } else {
        0
    };
    if existing > expected_size {
        fs::remove_file(partial).await?;
    }
    if offset == expected_size {
        return Ok(());
    }
    let mut request = client.get(url);
    if offset > 0 {
        request = request.header(header::RANGE, format!("bytes={offset}-"));
    }
    let response = until_data(request.send(), cancellation, STALL_LIMIT).await??;
    if !env.is_allowed_url(response.url()) {
        return Err(AppError::Download("model redirect left HTTPS".into()));
    }
    let status = response.status();
    if !status.is_success() {
        return Err(AppError::Download(format!(
            "model server returned HTTP {status}"
        )));
    }
    let resume = offset > 0 && status == StatusCode::PARTIAL_CONTENT;
    if resume {
        let valid_range = response
            .headers()
            .get(header::CONTENT_RANGE)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| valid_content_range(value, offset, expected_size));
        if !valid_range {
            return Err(AppError::Download(
                "model server returned an invalid resume range".into(),
            ));
        }
    }
    let mut downloaded = if resume { offset } else { 0 };
    let mut last_published = downloaded;
    let mut output = if resume {
        fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(partial)
            .await?
    } else {
        fs::File::create(partial).await?
    };
    let mut stream = response.bytes_stream();
    while let Some(chunk) = until_data(stream.next(), cancellation, STALL_LIMIT).await? {
        let chunk = chunk?;
        downloaded = downloaded.saturating_add(chunk.len() as u64);
        if downloaded > expected_size {
            return Err(AppError::Download(
                "model server sent more data than expected".into(),
            ));
        }
        output.write_all(&chunk).await?;
        if downloaded.saturating_sub(last_published) >= 4 * 1024 * 1024
            || downloaded == expected_size
        {
            publish_progress(
                state,
                app,
                model_id,
                completed_before.saturating_add(downloaded),
                overall_total,
            );
            last_published = downloaded;
        }
    }
    output.flush().await?;
    output.sync_all().await?;
    if downloaded != expected_size {
        return Err(AppError::Download(format!(
            "connection ended at {downloaded} of {expected_size} bytes"
        )));
    }
    Ok(())
}

async fn verified_file_async(
    path: &Path,
    expected_size: u64,
    expected_sha256: &str,
) -> AppResult<bool> {
    let path = path.to_path_buf();
    let expected_sha256 = expected_sha256.to_owned();
    tokio::task::spawn_blocking(move || verified_file(&path, expected_size, &expected_sha256))
        .await
        .map_err(|error| AppError::Download(format!("verification task failed: {error}")))
}

#[cfg(test)]
fn validate_artifact_metadata(url: &str, size: u64, sha256: &str) -> AppResult<()> {
    validate_artifact_metadata_with_env(url, size, sha256, DownloadEnv::default())
}

fn validate_artifact_metadata_with_env(
    url: &str,
    size: u64,
    sha256: &str,
    env: DownloadEnv,
) -> AppResult<()> {
    let parsed = Url::parse(url)
        .map_err(|_| AppError::Validation("model catalog contains an invalid URL".into()))?;
    if !env.is_allowed_url(&parsed) {
        return Err(AppError::Validation("model downloads require HTTPS".into()));
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(AppError::Validation(
            "model download URLs must not contain credentials".into(),
        ));
    }
    if size == 0 || sha256.len() != 64 || !sha256.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(AppError::Validation(
            "model catalog is missing a valid size or SHA-256 digest".into(),
        ));
    }
    Ok(())
}

/// A partial that already has the full length was cut off before its check. A sound one is
/// installed; a corrupt one is removed so this download starts over instead of failing.
async fn reuse_complete_partial(
    partial: &Path,
    destination: &Path,
    expected_size: u64,
    expected_sha256: &str,
) -> AppResult<bool> {
    let complete = fs::metadata(partial)
        .await
        .is_ok_and(|metadata| metadata.len() == expected_size);
    if !complete {
        return Ok(false);
    }
    let partial_for_hash = partial.to_path_buf();
    let actual_sha256 = tokio::task::spawn_blocking(move || sha256_file(&partial_for_hash))
        .await
        .map_err(|error| AppError::Download(format!("checksum task failed: {error}")))??;
    if actual_sha256.eq_ignore_ascii_case(expected_sha256) {
        install_atomically(partial, destination).await?;
        return Ok(true);
    }
    fs::remove_file(partial).await?;
    Ok(false)
}

async fn install_atomically(partial: &Path, destination: &Path) -> AppResult<()> {
    let backup = appended_path(destination, ".replaced");
    let had_destination = fs::metadata(destination).await.is_ok();
    if had_destination {
        if fs::metadata(&backup).await.is_ok() {
            fs::remove_file(&backup).await?;
        }
        fs::rename(destination, &backup).await?;
    }
    if let Err(error) = fs::rename(partial, destination).await {
        if had_destination {
            let _ = fs::rename(&backup, destination).await;
        }
        return Err(error.into());
    }
    // The copy a crash between the two renames left behind is as stale as the one
    // just replaced.
    let _ = fs::remove_file(backup).await;
    Ok(())
}

fn appended_path(path: &Path, suffix: &str) -> PathBuf {
    let mut value = path.as_os_str().to_os_string();
    value.push(suffix);
    PathBuf::from(value)
}

fn valid_content_range(value: &str, offset: u64, expected_size: u64) -> bool {
    let Some(value) = value.strip_prefix("bytes ") else {
        return false;
    };
    let Some((range, total)) = value.split_once('/') else {
        return false;
    };
    let Some((start, end)) = range.split_once('-') else {
        return false;
    };
    let Ok(start) = start.parse::<u64>() else {
        return false;
    };
    let Ok(end) = end.parse::<u64>() else {
        return false;
    };
    let Ok(total) = total.parse::<u64>() else {
        return false;
    };
    start == offset && end >= start && end < expected_size && total == expected_size
}

fn publish_progress<R: tauri::Runtime>(
    state: &AppState,
    app: &AppHandle<R>,
    model_id: &str,
    downloaded: u64,
    total: u64,
) {
    let progress = if total > 0 {
        (downloaded as f64 / total as f64 * 100.0).clamp(0.0, 100.0)
    } else {
        0.0
    };
    if let Ok(mut downloads) = state.downloads.lock() {
        if let Some(entry) = downloads.get_mut(model_id) {
            entry.progress = progress;
            entry.downloaded_bytes = downloaded;
            entry.total_bytes = total;
        }
    }
    sync_runtime_downloads(state, None);
    emit_download_event(
        app,
        "download-progress",
        json!({
            "model_id": model_id,
            "downloaded_bytes": downloaded,
            "total_bytes": total,
            "progress": progress
        }),
    );
    emit_state_changed(app);
}

fn emit_download_event<R: tauri::Runtime>(
    app: &AppHandle<R>,
    message_type: &str,
    data: serde_json::Value,
) {
    let _ = app.emit(
        "worker-message",
        WorkerEnvelope {
            message_type: message_type.into(),
            data,
        },
    );
}

/// How long a server may stay silent, before it answers and between two pieces of the
/// file, before the attempt is given up. The attempt can resume from the partial file.
const STALL_LIMIT: Duration = Duration::from_secs(45);
/// How often a wait for the server looks at the cancel flag.
const CANCEL_POLL: Duration = Duration::from_millis(250);

/// Wait for `data`, but end the wait when the user cancels or the server has been
/// silent for `stall_limit`. A dead connection never answers, so without this a
/// cancel would be seen only after the next piece of data.
async fn until_data<T>(
    data: impl std::future::Future<Output = T>,
    cancellation: &AtomicBool,
    stall_limit: Duration,
) -> AppResult<T> {
    let mut data = std::pin::pin!(data);
    let started = tokio::time::Instant::now();
    loop {
        check_cancel(cancellation)?;
        let silent = started.elapsed();
        if silent >= stall_limit {
            return Err(AppError::Download(
                "the model server stopped sending data".into(),
            ));
        }
        let wait = CANCEL_POLL.min(stall_limit - silent);
        if let Ok(value) = tokio::time::timeout(wait, data.as_mut()).await {
            return Ok(value);
        }
    }
}

fn check_cancel(cancellation: &AtomicBool) -> AppResult<()> {
    if cancellation.load(Ordering::SeqCst) {
        Err(AppError::Download("download cancelled".into()))
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::AtomicUsize;

    use axum::{
        body::Body,
        extract::{Path as AxumPath, State as AxumState},
        http::{header as http_header, StatusCode as AxumStatus},
        response::Response,
        routing::get,
        Router,
    };
    use sha2::{Digest, Sha256};

    use super::*;

    fn current_thread_runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
    }

    fn multi_thread_runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(4)
            .enable_all()
            .build()
            .unwrap()
    }

    fn sha256_hex(bytes: &[u8]) -> String {
        use std::fmt::Write as _;
        let mut digest = Sha256::new();
        digest.update(bytes);
        let mut out = String::with_capacity(64);
        for byte in digest.finalize() {
            write!(&mut out, "{byte:02x}").unwrap();
        }
        out
    }

    fn test_image_model(id: &str, filename: &str, bytes: &[u8], url: String) -> CatalogModel {
        let mut model: CatalogModel = serde_json::from_value(
            serde_json::to_value(
                serde_json::from_str::<CatalogManifest>(include_str!(
                    "../resources/model-catalog.json"
                ))
                .unwrap()
                .models[0]
                    .clone(),
            )
            .unwrap(),
        )
        .unwrap();
        model.model_id = id.into();
        model.name = id.into();
        model.filename = filename.into();
        model.size_bytes = bytes.len() as u64;
        model.sha256 = sha256_hex(bytes);
        model.download_url = url;
        model.automated_download_allowed = true;
        model.terms_acceptance_required = false;
        model.installed = false;
        model.installed_path = None;
        model
    }

    fn test_video_bundle(id: &str, family: &str, files: Vec<ModelFile>) -> CatalogVideoModel {
        let mut model = serde_json::from_str::<CatalogManifest>(include_str!(
            "../resources/model-catalog.json"
        ))
        .unwrap()
        .video_models[0]
            .clone();
        model.model_id = id.into();
        model.name = id.into();
        model.family = family.into();
        model.total_size_bytes = files.iter().map(|f| f.size_bytes).sum();
        model.files = files;
        model.automated_download_allowed = true;
        model.terms_acceptance_required = false;
        model.installed = false;
        model.installed_path = None;
        model
    }

    #[test]
    fn an_old_copy_left_by_an_interrupted_install_is_removed_by_the_next_install() {
        let directory = tempfile::tempdir().unwrap();
        let destination = directory.path().join("model.bin");
        let leftover = directory.path().join("model.bin.replaced");
        std::fs::write(&leftover, b"old").unwrap();
        let partial = directory.path().join("model.bin.part");
        std::fs::write(&partial, b"new").unwrap();
        current_thread_runtime()
            .block_on(install_atomically(&partial, &destination))
            .unwrap();
        assert_eq!(std::fs::read(&destination).unwrap(), b"new");
        assert!(!leftover.exists());
    }

    #[test]
    fn a_silent_server_ends_the_wait_after_the_stall_limit() {
        let cancel = AtomicBool::new(false);
        let started = std::time::Instant::now();
        let outcome = current_thread_runtime().block_on(until_data(
            std::future::pending::<()>(),
            &cancel,
            Duration::from_millis(300),
        ));
        assert!(
            matches!(outcome, Err(AppError::Download(message)) if message.contains("stopped sending"))
        );
        assert!(started.elapsed() < Duration::from_secs(3));
    }

    #[test]
    fn a_cancel_ends_the_wait_for_a_silent_server_within_a_second() {
        let cancel = Arc::new(AtomicBool::new(false));
        let flag = cancel.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(200));
            flag.store(true, Ordering::SeqCst);
        });
        let started = std::time::Instant::now();
        let outcome = current_thread_runtime().block_on(until_data(
            std::future::pending::<()>(),
            cancel.as_ref(),
            Duration::from_secs(600),
        ));
        assert!(
            matches!(outcome, Err(AppError::Download(message)) if message == "download cancelled")
        );
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn data_that_arrives_in_time_is_returned_unchanged() {
        let cancel = AtomicBool::new(false);
        let outcome = current_thread_runtime().block_on(until_data(
            async {
                tokio::time::sleep(Duration::from_millis(400)).await;
                7
            },
            &cancel,
            Duration::from_secs(5),
        ));
        assert_eq!(outcome.unwrap(), 7);
    }

    #[test]
    fn a_download_gives_up_its_place_on_every_way_out() {
        let directory = tempfile::tempdir().unwrap();
        let state =
            crate::state::AppState::from_paths(crate::paths::AppPaths::under(directory.path()))
                .unwrap();
        let register = |id: &str| {
            state.downloads.lock().unwrap().insert(
                id.into(),
                DownloadEntry {
                    order: 0,
                    status: DownloadPhase::Downloading,
                    progress: 0.0,
                    downloaded_bytes: 0,
                    total_bytes: 10,
                    planned_files: Vec::new(),
                    cancellation: Arc::new(AtomicBool::new(false)),
                    cancel_notify: Arc::new(Notify::new()),
                },
            );
        };
        register("finished");
        drop(DownloadSlot::new(&state, "finished"));
        register("panicked");
        let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _slot = DownloadSlot::new(&state, "panicked");
            panic!("the download task failed");
        }));
        assert!(outcome.is_err());
        assert!(state.downloads.lock().unwrap().is_empty());
    }

    #[test]
    fn a_complete_partial_is_installed_when_sound_and_downloaded_again_when_corrupt() {
        let directory = tempfile::tempdir().unwrap();
        let destination = directory.path().join("model.pth");
        let partial = directory.path().join("model.pth.part");
        let content = b"twelve bytes";
        let sound = sha256_file(&{
            std::fs::write(&partial, content).unwrap();
            partial.clone()
        })
        .unwrap();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let reuse = |sha: &str| {
            runtime.block_on(reuse_complete_partial(
                &partial,
                &destination,
                content.len() as u64,
                sha,
            ))
        };
        // Corrupt (another hash): removed, so the download starts over.
        assert!(!reuse(&"0".repeat(64)).unwrap());
        assert!(!partial.exists() && !destination.exists());
        // Sound: installed without a request.
        std::fs::write(&partial, content).unwrap();
        assert!(reuse(&sound).unwrap());
        assert_eq!(std::fs::read(&destination).unwrap(), content);
        // A shorter partial is left to resume.
        std::fs::write(&partial, b"short").unwrap();
        assert!(!reuse(&sound).unwrap());
        assert!(partial.exists());
    }

    #[test]
    fn interpolation_models_download_as_verified_bundles() {
        let manifest: crate::types::CatalogManifest =
            serde_json::from_str(include_str!("../resources/model-catalog.json")).unwrap();
        let rife = bundle_model(&manifest, "rife_4_26").unwrap();
        assert_eq!(rife.engine_kind, "rife");
        assert!(rife.automated_download_allowed);
        assert!(!rife.terms_acceptance_required);
        for file in &rife.files {
            assert!(
                validate_artifact_metadata(&file.download_url, file.size_bytes, &file.sha256)
                    .is_ok()
            );
        }
        assert!(DownloadTarget::Video(Box::new(rife))
            .validate_policy(false)
            .is_ok());
        assert_eq!(
            bundle_model(&manifest, "seedvr2_3b").unwrap().engine_kind,
            "seedvr2"
        );
        let df3 = bundle_model(&manifest, "deepfilternet3").unwrap();
        assert_eq!(df3.engine_kind, "deepfilternet3");
        assert!(df3.automated_download_allowed);
        assert!(!df3.terms_acceptance_required);
        for file in &df3.files {
            assert!(
                validate_artifact_metadata(&file.download_url, file.size_bytes, &file.sha256)
                    .is_ok()
            );
        }
        assert!(DownloadTarget::Video(Box::new(df3))
            .validate_policy(false)
            .is_ok());
        let dpdf = bundle_model(&manifest, "dpdfnet8_48khz_hr").unwrap();
        assert_eq!(dpdf.engine_kind, "dpdfnet");
        assert!(dpdf.automated_download_allowed);
        assert!(!dpdf.terms_acceptance_required);
        for file in &dpdf.files {
            assert!(
                validate_artifact_metadata(&file.download_url, file.size_bytes, &file.sha256)
                    .is_ok()
            );
        }
        assert!(DownloadTarget::Video(Box::new(dpdf))
            .validate_policy(false)
            .is_ok());
        assert!(bundle_model(&manifest, "span_photo_x4").is_none());
    }

    #[test]
    fn declared_cc_by_downloads_do_not_unlock_unresolved_face_weights() {
        let manifest: crate::types::CatalogManifest =
            serde_json::from_str(include_str!("../resources/model-catalog.json")).unwrap();
        for id in ["realplksr_hfa2k_anime_x4", "realplksr_nomoswebphoto_x4"] {
            let model = manifest.models.iter().find(|m| m.model_id == id).unwrap();
            assert_eq!(model.commercial_use_allowed, Some(true));
            assert_eq!(
                model.license_url,
                "https://creativecommons.org/licenses/by/4.0/"
            );
            assert!(model
                .source_url
                .starts_with("https://huggingface.co/Phips/"));
            assert!(DownloadTarget::Image(Box::new(model.clone()))
                .validate_policy(false)
                .is_ok());
        }
        for id in ["hat_s_x4_face", "hat_l_x4_face"] {
            let model = manifest.models.iter().find(|m| m.model_id == id).unwrap();
            assert_eq!(model.commercial_use_allowed, None);
            assert!(DownloadTarget::Image(Box::new(model.clone()))
                .validate_policy(true)
                .is_err());
        }
    }

    #[test]
    fn rejects_unpinned_or_insecure_downloads() {
        let digest = "a".repeat(64);
        assert!(validate_artifact_metadata("http://example.com/model", 1, &digest).is_err());
        assert!(
            validate_artifact_metadata("https://user:secret@example.com/model", 1, &digest)
                .is_err()
        );
        assert!(validate_artifact_metadata("https://example.com/model", 0, &digest).is_err());
        assert!(validate_artifact_metadata("https://example.com/model", 1, "placeholder").is_err());
        assert!(validate_artifact_metadata("https://example.com/model", 1, &digest).is_ok());
    }

    #[test]
    fn validates_the_complete_resume_range() {
        assert!(valid_content_range("bytes 10-99/100", 10, 100));
        assert!(!valid_content_range("bytes 9-99/100", 10, 100));
        assert!(!valid_content_range("bytes 10-99/101", 10, 100));
        assert!(!valid_content_range("bytes 10-100/100", 10, 100));
        assert!(!valid_content_range("items 10-99/100", 10, 100));
    }

    #[test]
    fn runs_up_to_three_downloads_at_once_and_queues_the_fourth() {
        multi_thread_runtime().block_on(async {
            let directory = tempfile::tempdir().unwrap();
            let state = Arc::new(
                AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
            );
            let app = tauri::test::mock_app();
            let handle = app.handle().clone();

            let active_now = Arc::new(AtomicUsize::new(0));
            let peak_active = Arc::new(AtomicUsize::new(0));
            let first_three_gate = Arc::new(Notify::new());
            let release_gate = Arc::new(AtomicBool::new(false));
            let payloads: HashMap<String, Vec<u8>> = (1..=4)
                .map(|i| {
                    (
                        format!("m{i}.bin"),
                        format!("payload-{i}-data").into_bytes(),
                    )
                })
                .collect();

            #[derive(Clone)]
            struct ServerState {
                payloads: HashMap<String, Vec<u8>>,
                active_now: Arc<AtomicUsize>,
                peak_active: Arc<AtomicUsize>,
                first_three_gate: Arc<Notify>,
                release_gate: Arc<AtomicBool>,
            }

            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let addr = listener.local_addr().unwrap();
            let router = Router::new()
                .route(
                    "/{name}",
                    get(
                        |AxumPath(name): AxumPath<String>,
                         AxumState(srv): AxumState<ServerState>| async move {
                            let now = srv.active_now.fetch_add(1, Ordering::SeqCst) + 1;
                            srv.peak_active.fetch_max(now, Ordering::SeqCst);
                            if now == 3 {
                                srv.first_three_gate.notify_waiters();
                            }
                            while !srv.release_gate.load(Ordering::SeqCst) {
                                tokio::time::sleep(Duration::from_millis(10)).await;
                            }
                            srv.active_now.fetch_sub(1, Ordering::SeqCst);
                            let body = srv.payloads.get(&name).cloned().unwrap();
                            Response::builder()
                                .status(AxumStatus::OK)
                                .header(http_header::CONTENT_LENGTH, body.len())
                                .body(Body::from(body))
                                .unwrap()
                        },
                    ),
                )
                .with_state(ServerState {
                    payloads: payloads.clone(),
                    active_now: active_now.clone(),
                    peak_active: peak_active.clone(),
                    first_three_gate: first_three_gate.clone(),
                    release_gate: release_gate.clone(),
                });
            let server = tokio::spawn(async move {
                axum::serve(listener, router).await.unwrap();
            });

            {
                let mut catalog = state.catalog.lock().unwrap();
                catalog.models = (1..=4)
                    .map(|i| {
                        let name = format!("m{i}.bin");
                        test_image_model(
                            &format!("m{i}"),
                            &name,
                            &payloads[&name],
                            format!("http://{addr}/{name}"),
                        )
                    })
                    .collect();
            }

            let env = DownloadEnv {
                allow_local_http: true,
                available_space_override: Some(10_000_000),
            };
            let mut tasks = Vec::new();
            for i in 1..=3 {
                let s = state.clone();
                let h = handle.clone();
                tasks.push(tokio::spawn(async move {
                    download_model_with_env(s, h, format!("m{i}"), false, env).await
                }));
            }

            tokio::time::timeout(Duration::from_secs(5), async {
                while active_now.load(Ordering::SeqCst) < 3 {
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            })
            .await
            .unwrap();

            let s4 = state.clone();
            let h4 = handle.clone();
            let t4 = tokio::spawn(async move {
                download_model_with_env(s4, h4, "m4".into(), false, env).await
            });

            tokio::time::timeout(Duration::from_secs(5), async {
                loop {
                    let downloads = state.runtime.lock().unwrap().downloads.clone();
                    if downloads.len() == 4 {
                        assert_eq!(
                            downloads
                                .iter()
                                .filter(|d| d.status == "downloading")
                                .count(),
                            3
                        );
                        assert_eq!(downloads.iter().filter(|d| d.status == "queued").count(), 1);
                        assert_eq!(downloads[3].model_id, "m4");
                        break;
                    }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            })
            .await
            .unwrap();

            assert_eq!(active_now.load(Ordering::SeqCst), 3);

            // A 5th download can also be cancelled while still queued without touching the active 3.
            {
                let mut catalog = state.catalog.lock().unwrap();
                catalog.models.push(test_image_model(
                    "m5",
                    "m5.bin",
                    b"m5",
                    format!("http://{addr}/m1.bin"),
                ));
            }
            let t5 = tokio::spawn(download_model_with_env(
                state.clone(),
                handle.clone(),
                "m5".into(),
                false,
                env,
            ));
            tokio::time::timeout(Duration::from_secs(5), async {
                loop {
                    if state.downloads.lock().unwrap().contains_key("m5") {
                        break;
                    }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            })
            .await
            .unwrap();
            cancel_download(&state, "m5").unwrap();
            assert!(is_cancelled_error(&t5.await.unwrap()));
            assert_eq!(active_now.load(Ordering::SeqCst), 3);

            release_gate.store(true, Ordering::SeqCst);
            for task in tasks {
                task.await.unwrap().unwrap();
            }
            t4.await.unwrap().unwrap();

            assert_eq!(peak_active.load(Ordering::SeqCst), 3);
            assert!(state.downloads.lock().unwrap().is_empty());
            assert!(state.runtime.lock().unwrap().downloads.is_empty());
            for i in 1..=4 {
                assert_eq!(
                    std::fs::read(state.paths.model_root.join(format!("m{i}.bin"))).unwrap(),
                    payloads[&format!("m{i}.bin")]
                );
            }
            server.abort();
        });
    }

    #[test]
    fn cancelling_one_download_keeps_others_running_and_leaves_partial_uninstalled() {
        multi_thread_runtime().block_on(async {
            let directory = tempfile::tempdir().unwrap();
            let state = Arc::new(
                AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
            );
            let app = tauri::test::mock_app();
            let handle = app.handle().clone();

            let payloads: HashMap<String, Vec<u8>> = [
                ("keep1.bin".into(), vec![b'a'; 128]),
                ("drop.bin".into(), vec![b'b'; 128]),
                ("keep2.bin".into(), vec![b'c'; 128]),
            ]
            .into_iter()
            .collect();
            let drop_started = Arc::new(Notify::new());
            let release_keeps = Arc::new(AtomicBool::new(false));

            #[derive(Clone)]
            struct ServerState {
                payloads: HashMap<String, Vec<u8>>,
                drop_started: Arc<Notify>,
                release_keeps: Arc<AtomicBool>,
            }

            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let addr = listener.local_addr().unwrap();
            let router = Router::new()
                .route(
                    "/{name}",
                    get(
                        |AxumPath(name): AxumPath<String>,
                         AxumState(srv): AxumState<ServerState>| async move {
                            let data = srv.payloads.get(&name).cloned().unwrap();
                            if name == "drop.bin" {
                                srv.drop_started.notify_waiters();
                                let stream = futures_util::stream::unfold(0usize, move |step| {
                                    let chunk = data[..16].to_vec();
                                    async move {
                                        if step == 0 {
                                            Some((Ok::<_, std::io::Error>(bytes_chunk(chunk)), 1))
                                        } else {
                                            tokio::time::sleep(Duration::from_secs(30)).await;
                                            None
                                        }
                                    }
                                });
                                return Response::builder()
                                    .status(AxumStatus::OK)
                                    .header(http_header::CONTENT_LENGTH, 128)
                                    .body(Body::from_stream(stream))
                                    .unwrap();
                            }
                            while !srv.release_keeps.load(Ordering::SeqCst) {
                                tokio::time::sleep(Duration::from_millis(10)).await;
                            }
                            Response::builder()
                                .status(AxumStatus::OK)
                                .header(http_header::CONTENT_LENGTH, data.len())
                                .body(Body::from(data))
                                .unwrap()
                        },
                    ),
                )
                .with_state(ServerState {
                    payloads: payloads.clone(),
                    drop_started: drop_started.clone(),
                    release_keeps: release_keeps.clone(),
                });
            let server = tokio::spawn(async move {
                axum::serve(listener, router).await.unwrap();
            });

            {
                let mut catalog = state.catalog.lock().unwrap();
                catalog.models = vec![
                    test_image_model(
                        "keep1",
                        "keep1.bin",
                        &payloads["keep1.bin"],
                        format!("http://{addr}/keep1.bin"),
                    ),
                    test_image_model(
                        "drop",
                        "drop.bin",
                        &payloads["drop.bin"],
                        format!("http://{addr}/drop.bin"),
                    ),
                    test_image_model(
                        "keep2",
                        "keep2.bin",
                        &payloads["keep2.bin"],
                        format!("http://{addr}/keep2.bin"),
                    ),
                ];
            }

            let env = DownloadEnv {
                allow_local_http: true,
                available_space_override: Some(10_000_000),
            };
            let t_keep1 = tokio::spawn(download_model_with_env(
                state.clone(),
                handle.clone(),
                "keep1".into(),
                false,
                env,
            ));
            let t_drop = tokio::spawn(download_model_with_env(
                state.clone(),
                handle.clone(),
                "drop".into(),
                false,
                env,
            ));
            let t_keep2 = tokio::spawn(download_model_with_env(
                state.clone(),
                handle.clone(),
                "keep2".into(),
                false,
                env,
            ));

            tokio::time::timeout(Duration::from_secs(5), drop_started.notified())
                .await
                .unwrap();
            tokio::time::sleep(Duration::from_millis(50)).await;
            cancel_download(&state, "drop").unwrap();

            let drop_outcome = t_drop.await.unwrap();
            assert!(is_cancelled_error(&drop_outcome));
            assert!(!state.paths.model_root.join("drop.bin").exists());
            assert!(state.paths.model_root.join("drop.bin.part").exists());

            // Other downloads are still running and complete normally.
            assert_eq!(state.downloads.lock().unwrap().len(), 2);
            release_keeps.store(true, Ordering::SeqCst);
            t_keep1.await.unwrap().unwrap();
            t_keep2.await.unwrap().unwrap();
            assert!(state.paths.model_root.join("keep1.bin").is_file());
            assert!(state.paths.model_root.join("keep2.bin").is_file());
            server.abort();
        });
    }

    #[test]
    fn disk_space_is_checked_for_the_sum_of_running_downloads() {
        multi_thread_runtime().block_on(async {
            let directory = tempfile::tempdir().unwrap();
            let state = Arc::new(
                AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
            );
            let app = tauri::test::mock_app();
            let handle = app.handle().clone();

            let data_a = vec![b'x'; 1000];
            let data_b = vec![b'y'; 1000];
            let a_started = Arc::new(Notify::new());
            let release_a = Arc::new(AtomicBool::new(false));

            #[derive(Clone)]
            struct ServerState {
                data_a: Vec<u8>,
                a_started: Arc<Notify>,
                release_a: Arc<AtomicBool>,
            }

            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let addr = listener.local_addr().unwrap();
            let router = Router::new()
                .route(
                    "/a.bin",
                    get(|AxumState(srv): AxumState<ServerState>| async move {
                        srv.a_started.notify_waiters();
                        while !srv.release_a.load(Ordering::SeqCst) {
                            tokio::time::sleep(Duration::from_millis(10)).await;
                        }
                        Response::builder()
                            .status(AxumStatus::OK)
                            .header(http_header::CONTENT_LENGTH, srv.data_a.len())
                            .body(Body::from(srv.data_a))
                            .unwrap()
                    }),
                )
                .with_state(ServerState {
                    data_a: data_a.clone(),
                    a_started: a_started.clone(),
                    release_a: release_a.clone(),
                });
            let server = tokio::spawn(async move {
                axum::serve(listener, router).await.unwrap();
            });

            {
                let mut catalog = state.catalog.lock().unwrap();
                catalog.models = vec![
                    test_image_model("a", "a.bin", &data_a, format!("http://{addr}/a.bin")),
                    test_image_model("b", "b.bin", &data_b, format!("http://{addr}/b.bin")),
                ];
            }

            // Enough for one 1000-byte download (needs 1150), NOT enough for two (needs 2300).
            let env = DownloadEnv {
                allow_local_http: true,
                available_space_override: Some(1500),
            };
            let t_a = tokio::spawn(download_model_with_env(
                state.clone(),
                handle.clone(),
                "a".into(),
                false,
                env,
            ));

            tokio::time::timeout(Duration::from_secs(5), a_started.notified())
                .await
                .unwrap();

            let err_b =
                download_model_with_env(state.clone(), handle.clone(), "b".into(), false, env)
                    .await
                    .unwrap_err();
            assert!(
                err_b.to_string().contains("not enough disk space"),
                "unexpected error: {err_b}"
            );

            release_a.store(true, Ordering::SeqCst);
            t_a.await.unwrap().unwrap();
            assert!(state.paths.model_root.join("a.bin").is_file());
            assert!(!state.paths.model_root.join("b.bin").exists());
            server.abort();
        });
    }

    #[test]
    fn disk_space_sum_deduplicates_shared_bundle_components() {
        let directory = tempfile::tempdir().unwrap();
        let family_dir = directory.path().join("family");
        let shared = PlannedFile {
            destination: family_dir.join("shared.safetensors"),
            partial: family_dir.join("shared.safetensors.part"),
            expected_size: 800,
        };
        let unique_a = PlannedFile {
            destination: family_dir.join("a.safetensors"),
            partial: family_dir.join("a.safetensors.part"),
            expected_size: 400,
        };
        let unique_b = PlannedFile {
            destination: family_dir.join("b.safetensors"),
            partial: family_dir.join("b.safetensors.part"),
            expected_size: 400,
        };
        let mut downloads = HashMap::new();
        downloads.insert(
            "bundle_a".into(),
            DownloadEntry {
                order: 0,
                status: DownloadPhase::Downloading,
                progress: 0.0,
                downloaded_bytes: 0,
                total_bytes: 1200,
                planned_files: vec![shared.clone(), unique_a],
                cancellation: Arc::new(AtomicBool::new(false)),
                cancel_notify: Arc::new(Notify::new()),
            },
        );
        // Unique files across bundle_a + bundle_b total 800 + 400 + 400 = 1600 bytes (1840 with 15% margin).
        // Without deduplicating `shared`, the sum would be 2400 (2760 needed) and fail against 1900.
        let env = DownloadEnv {
            allow_local_http: true,
            available_space_override: Some(1900),
        };
        assert!(check_summed_disk_space_locked(
            &downloads,
            &[shared.clone(), unique_b.clone()],
            directory.path(),
            env,
        )
        .is_ok());

        let too_tight = DownloadEnv {
            allow_local_http: true,
            available_space_override: Some(1800),
        };
        assert!(check_summed_disk_space_locked(
            &downloads,
            &[shared, unique_b],
            directory.path(),
            too_tight
        )
        .is_err());
    }

    #[test]
    fn shared_bundle_component_is_downloaded_only_once() {
        multi_thread_runtime().block_on(async {
            let directory = tempfile::tempdir().unwrap();
            let state = Arc::new(
                AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
            );
            let app = tauri::test::mock_app();
            let handle = app.handle().clone();

            let shared_bytes = b"shared-vae-weights-payload".to_vec();
            let unique_a = b"unique-bundle-a".to_vec();
            let unique_b = b"unique-bundle-b".to_vec();
            let shared_hits = Arc::new(AtomicUsize::new(0));
            let shared_entered = Arc::new(Notify::new());
            let release_shared = Arc::new(AtomicBool::new(false));

            #[derive(Clone)]
            struct ServerState {
                shared_bytes: Vec<u8>,
                unique_a: Vec<u8>,
                unique_b: Vec<u8>,
                shared_hits: Arc<AtomicUsize>,
                shared_entered: Arc<Notify>,
                release_shared: Arc<AtomicBool>,
            }

            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let addr = listener.local_addr().unwrap();
            let router = Router::new()
                .route(
                    "/{name}",
                    get(
                        |AxumPath(name): AxumPath<String>,
                         AxumState(srv): AxumState<ServerState>| async move {
                            let body = match name.as_str() {
                                "shared.safetensors" => {
                                    srv.shared_hits.fetch_add(1, Ordering::SeqCst);
                                    srv.shared_entered.notify_waiters();
                                    while !srv.release_shared.load(Ordering::SeqCst) {
                                        tokio::time::sleep(Duration::from_millis(10)).await;
                                    }
                                    srv.shared_bytes
                                }
                                "a.safetensors" => srv.unique_a,
                                "b.safetensors" => srv.unique_b,
                                _ => unreachable!(),
                            };
                            Response::builder()
                                .status(AxumStatus::OK)
                                .header(http_header::CONTENT_LENGTH, body.len())
                                .body(Body::from(body))
                                .unwrap()
                        },
                    ),
                )
                .with_state(ServerState {
                    shared_bytes: shared_bytes.clone(),
                    unique_a: unique_a.clone(),
                    unique_b: unique_b.clone(),
                    shared_hits: shared_hits.clone(),
                    shared_entered: shared_entered.clone(),
                    release_shared: release_shared.clone(),
                });
            let server = tokio::spawn(async move {
                axum::serve(listener, router).await.unwrap();
            });

            let shared_file = ModelFile {
                role: "vae".into(),
                filename: "shared.safetensors".into(),
                size_bytes: shared_bytes.len() as u64,
                sha256: sha256_hex(&shared_bytes),
                download_url: format!("http://{addr}/shared.safetensors"),
            };
            let bundle_a = test_video_bundle(
                "bundle_a",
                "shared_family",
                vec![
                    shared_file.clone(),
                    ModelFile {
                        role: "dit".into(),
                        filename: "a.safetensors".into(),
                        size_bytes: unique_a.len() as u64,
                        sha256: sha256_hex(&unique_a),
                        download_url: format!("http://{addr}/a.safetensors"),
                    },
                ],
            );
            let bundle_b = test_video_bundle(
                "bundle_b",
                "shared_family",
                vec![
                    shared_file,
                    ModelFile {
                        role: "dit".into(),
                        filename: "b.safetensors".into(),
                        size_bytes: unique_b.len() as u64,
                        sha256: sha256_hex(&unique_b),
                        download_url: format!("http://{addr}/b.safetensors"),
                    },
                ],
            );

            {
                let mut catalog = state.catalog.lock().unwrap();
                catalog.video_models = vec![bundle_a, bundle_b];
            }

            let env = DownloadEnv {
                allow_local_http: true,
                available_space_override: Some(10_000_000),
            };
            let t_a = tokio::spawn(download_model_with_env(
                state.clone(),
                handle.clone(),
                "bundle_a".into(),
                false,
                env,
            ));
            let t_b = tokio::spawn(download_model_with_env(
                state.clone(),
                handle.clone(),
                "bundle_b".into(),
                false,
                env,
            ));

            tokio::time::timeout(Duration::from_secs(5), shared_entered.notified())
                .await
                .unwrap();
            tokio::time::sleep(Duration::from_millis(80)).await;
            assert_eq!(shared_hits.load(Ordering::SeqCst), 1);

            release_shared.store(true, Ordering::SeqCst);
            t_a.await.unwrap().unwrap();
            t_b.await.unwrap().unwrap();

            assert_eq!(shared_hits.load(Ordering::SeqCst), 1);
            let dir = state.paths.model_root.join("shared_family");
            assert_eq!(
                std::fs::read(dir.join("shared.safetensors")).unwrap(),
                shared_bytes
            );
            assert_eq!(std::fs::read(dir.join("a.safetensors")).unwrap(), unique_a);
            assert_eq!(std::fs::read(dir.join("b.safetensors")).unwrap(), unique_b);
            server.abort();
        });
    }

    #[test]
    fn failed_verification_of_one_download_does_not_affect_the_others() {
        multi_thread_runtime().block_on(async {
            let directory = tempfile::tempdir().unwrap();
            let state = Arc::new(
                AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
            );
            let app = tauri::test::mock_app();
            let handle = app.handle().clone();

            let good1 = b"good-checkpoint-1".to_vec();
            let good2 = b"good-checkpoint-2".to_vec();
            let bad_expected = b"expected-checkpoint".to_vec();
            let bad_actual = b"tampered-checkpoint".to_vec();
            assert_eq!(bad_expected.len(), bad_actual.len());

            #[derive(Clone)]
            struct ServerState {
                good1: Vec<u8>,
                good2: Vec<u8>,
                bad_actual: Vec<u8>,
            }

            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let addr = listener.local_addr().unwrap();
            let router = Router::new()
                .route(
                    "/{name}",
                    get(
                        |AxumPath(name): AxumPath<String>,
                         AxumState(srv): AxumState<ServerState>| async move {
                            let body = match name.as_str() {
                                "good1.bin" => srv.good1,
                                "good2.bin" => srv.good2,
                                "bad.bin" => srv.bad_actual,
                                _ => unreachable!(),
                            };
                            Response::builder()
                                .status(AxumStatus::OK)
                                .header(http_header::CONTENT_LENGTH, body.len())
                                .body(Body::from(body))
                                .unwrap()
                        },
                    ),
                )
                .with_state(ServerState {
                    good1: good1.clone(),
                    good2: good2.clone(),
                    bad_actual,
                });
            let server = tokio::spawn(async move {
                axum::serve(listener, router).await.unwrap();
            });

            {
                let mut catalog = state.catalog.lock().unwrap();
                catalog.models = vec![
                    test_image_model(
                        "good1",
                        "good1.bin",
                        &good1,
                        format!("http://{addr}/good1.bin"),
                    ),
                    test_image_model(
                        "bad",
                        "bad.bin",
                        &bad_expected,
                        format!("http://{addr}/bad.bin"),
                    ),
                    test_image_model(
                        "good2",
                        "good2.bin",
                        &good2,
                        format!("http://{addr}/good2.bin"),
                    ),
                ];
            }

            let env = DownloadEnv {
                allow_local_http: true,
                available_space_override: Some(10_000_000),
            };
            let (r1, r_bad, r2) = tokio::join!(
                download_model_with_env(state.clone(), handle.clone(), "good1".into(), false, env),
                download_model_with_env(state.clone(), handle.clone(), "bad".into(), false, env),
                download_model_with_env(state.clone(), handle.clone(), "good2".into(), false, env),
            );

            assert!(r1.is_ok());
            assert!(r2.is_ok());
            assert!(r_bad.unwrap_err().to_string().contains("checksum mismatch"));
            assert!(state.paths.model_root.join("good1.bin").is_file());
            assert!(state.paths.model_root.join("good2.bin").is_file());
            assert!(!state.paths.model_root.join("bad.bin").exists());
            assert!(!state.paths.model_root.join("bad.bin.part").exists());
            server.abort();
        });
    }

    fn bytes_chunk(bytes: Vec<u8>) -> Vec<u8> {
        bytes
    }
}
