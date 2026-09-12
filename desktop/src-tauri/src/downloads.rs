use std::{
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};

use futures_util::StreamExt;
use reqwest::{header, Client, StatusCode, Url};
use serde_json::json;
use tauri::{AppHandle, Emitter};
use tokio::{fs, io::AsyncWriteExt};

use crate::{
    catalog::{safe_model_filename, sha256_file, verified_file},
    error::{AppError, AppResult},
    state::{emit_state_changed, lock, AppState},
    types::{CatalogModel, CatalogVideoModel, ModelFile, WorkerEnvelope},
};

pub async fn download_model(
    state: Arc<AppState>,
    app: AppHandle,
    model_id: String,
    accepted_terms: bool,
) -> AppResult<()> {
    let target = {
        let catalog = lock(&state.catalog)?;
        if let Some(model) = catalog
            .models
            .iter()
            .find(|model| model.model_id == model_id)
        {
            DownloadTarget::Image(model.clone())
        } else if let Some(model) = catalog
            .video_models
            .iter()
            .find(|model| model.model_id == model_id)
        {
            DownloadTarget::Video(model.clone())
        } else {
            return Err(AppError::Validation("unknown model identifier".into()));
        }
    };
    target.validate_policy(accepted_terms)?;

    let cancellation = Arc::new(AtomicBool::new(false));
    {
        crate::updates::ensure_not_installing(&state)?;
        let _scheduler = lock(&state.scheduler)?;
        crate::updates::ensure_not_installing(&state)?;
        let mut downloads = lock(&state.downloads)?;
        if !downloads.is_empty() {
            return Err(AppError::Download(
                "another model download is already active".into(),
            ));
        }
        downloads.insert(model_id.clone(), cancellation.clone());
    }
    {
        let mut runtime = lock(&state.runtime)?;
        runtime.download_model_id = model_id.clone();
        runtime.download_progress = 0.0;
        runtime.status_title = "Downloading model".into();
        runtime.status_detail = "Downloads are pinned and verified before installation.".into();
    }
    emit_download_event(&app, "download-started", json!({"model_id": model_id}));
    emit_state_changed(&app);

    let result = target
        .download(&state, &app, &model_id, cancellation.as_ref())
        .await;
    lock(&state.downloads)?.remove(&model_id);
    {
        let mut runtime = lock(&state.runtime)?;
        runtime.download_model_id.clear();
        match &result {
            Ok(()) => {
                runtime.download_progress = 100.0;
                runtime.status_title = "Model ready".into();
                runtime.status_detail = "Checksum verification succeeded.".into();
            }
            Err(AppError::Download(message)) if message == "download cancelled" => {
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
    if result.is_ok() {
        state.refresh_catalog()?;
        emit_download_event(&app, "download-completed", json!({"model_id": model_id}));
    } else if matches!(&result, Err(AppError::Download(message)) if message == "download cancelled")
    {
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

pub fn cancel_download(state: &AppState, model_id: &str) -> AppResult<()> {
    let downloads = lock(&state.downloads)?;
    let cancellation = downloads
        .get(model_id)
        .ok_or_else(|| AppError::Validation("that model is not being downloaded".into()))?;
    cancellation.store(true, Ordering::SeqCst);
    Ok(())
}

enum DownloadTarget {
    Image(CatalogModel),
    Video(CatalogVideoModel),
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

    async fn download(
        &self,
        state: &Arc<AppState>,
        app: &AppHandle,
        model_id: &str,
        cancellation: &AtomicBool,
    ) -> AppResult<()> {
        let client = secure_client()?;
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
                )
                .await
            }
            Self::Video(model) => {
                safe_model_filename(&model.family)?;
                let total = model.files.iter().map(|file| file.size_bytes).sum();
                let mut completed = 0;
                for file in &model.files {
                    check_cancel(cancellation)?;
                    safe_model_filename(&file.filename)?;
                    let destination = state
                        .paths
                        .model_root
                        .join(&model.family)
                        .join(&file.filename);
                    if verified_file(&destination, file.size_bytes, &file.sha256) {
                        completed += file.size_bytes;
                        continue;
                    }
                    download_video_file(
                        &client,
                        app,
                        state,
                        model_id,
                        file,
                        &destination,
                        completed,
                        total,
                        cancellation,
                    )
                    .await?;
                    completed += file.size_bytes;
                }
                Ok(())
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
async fn download_video_file(
    client: &Client,
    app: &AppHandle,
    state: &Arc<AppState>,
    model_id: &str,
    file: &ModelFile,
    destination: &Path,
    completed: u64,
    total: u64,
    cancellation: &AtomicBool,
) -> AppResult<()> {
    download_one(
        client,
        app,
        state,
        model_id,
        &file.download_url,
        destination,
        file.size_bytes,
        &file.sha256,
        completed,
        total,
        cancellation,
    )
    .await
}

#[allow(clippy::too_many_arguments)]
async fn download_one(
    client: &Client,
    app: &AppHandle,
    state: &Arc<AppState>,
    model_id: &str,
    url: &str,
    destination: &Path,
    expected_size: u64,
    expected_sha256: &str,
    completed_before: u64,
    overall_total: u64,
    cancellation: &AtomicBool,
) -> AppResult<()> {
    validate_artifact_metadata(url, expected_size, expected_sha256)?;
    if verified_file(destination, expected_size, expected_sha256) {
        return Ok(());
    }
    let partial = destination.with_extension(format!(
        "{}.part",
        destination
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("download")
    ));
    let parent = destination
        .parent()
        .ok_or_else(|| AppError::Validation("model destination has no parent".into()))?;
    fs::create_dir_all(parent).await?;
    let partial_size = fs::metadata(&partial)
        .await
        .map(|metadata| metadata.len())
        .ok()
        .filter(|size| *size <= expected_size)
        .unwrap_or(0);
    let available = fs2::available_space(parent)?;
    let remaining = expected_size.saturating_sub(partial_size);
    let needed = remaining.saturating_mul(115) / 100;
    if available < needed {
        return Err(AppError::Download(format!(
            "not enough disk space: need about {} MB more, but only {} MB is free",
            needed / 1_000_000,
            available / 1_000_000
        )));
    }
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
async fn download_attempt(
    client: &Client,
    app: &AppHandle,
    state: &Arc<AppState>,
    model_id: &str,
    url: &str,
    partial: &Path,
    expected_size: u64,
    completed_before: u64,
    overall_total: u64,
    cancellation: &AtomicBool,
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
    let response = request.send().await?;
    if response.url().scheme() != "https" {
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
    while let Some(chunk) = stream.next().await {
        check_cancel(cancellation)?;
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

fn secure_client() -> AppResult<Client> {
    let policy = reqwest::redirect::Policy::custom(|attempt| {
        if attempt.previous().len() >= 10 || attempt.url().scheme() != "https" {
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

fn validate_artifact_metadata(url: &str, size: u64, sha256: &str) -> AppResult<()> {
    let parsed = Url::parse(url)
        .map_err(|_| AppError::Validation("model catalog contains an invalid URL".into()))?;
    if parsed.scheme() != "https" || parsed.host_str().is_none() {
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
    if had_destination {
        let _ = fs::remove_file(backup).await;
    }
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

fn publish_progress(
    state: &AppState,
    app: &AppHandle,
    model_id: &str,
    downloaded: u64,
    total: u64,
) {
    let progress = if total > 0 {
        downloaded as f64 / total as f64 * 100.0
    } else {
        0.0
    };
    if let Ok(mut runtime) = state.runtime.lock() {
        runtime.download_progress = progress.clamp(0.0, 100.0);
        runtime.status_detail = format!("Verified download · {:.1}%", runtime.download_progress);
    }
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

fn emit_download_event(app: &AppHandle, message_type: &str, data: serde_json::Value) {
    let _ = app.emit(
        "worker-message",
        WorkerEnvelope {
            message_type: message_type.into(),
            data,
        },
    );
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
    use super::*;

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
}
