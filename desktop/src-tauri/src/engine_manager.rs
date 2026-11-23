//! In-app engine manager: discover signed engine packages from the update feed,
//! download them with HTTP Range resume, verify signatures and digests, import
//! offline payloads, switch between installed engines and remove unused ones.

use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};

use futures_util::StreamExt;
use reqwest::{header, StatusCode, Url};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, State};

use crate::{
    engine_payload,
    error::{AppError, AppResult},
    gpu_notice,
    paths::AppPaths,
    state::{emit_state_changed, lock, AppState},
    types::{
        EngineCatalogEntry, EngineDownloadStatus, EngineManagerSnapshot, EngineRecommendation,
        InstalledEngine, PROTOCOL_VERSION,
    },
    update_storage,
    updates::{
        self, acquire, active_engine_id, channel, engine_executable_path,
        engine_meta_valid_for_host, invalid, key, read_engine_meta, require_space, safe_file_name,
        safe_id, signature, validate_contract_for_backend, verify_file, verify_statement_for_entry,
        DownloadFile, EngineMeta, EngineUpdate, ReleaseContract, UpdateConfig,
    },
    worker,
};

#[derive(Clone, Debug)]
pub struct VerifiedCatalogEntry {
    pub backend: String,
    pub engine_id: String,
    pub version: String,
    pub size: u64,
    pub manifest_url: String,
    pub engine: EngineUpdate,
}

#[derive(Default)]
pub struct EngineControl {
    pub busy: AtomicBool,
    pub cancel: AtomicBool,
    status: Mutex<EngineDownloadStatus>,
    catalog: Mutex<Vec<VerifiedCatalogEntry>>,
    catalog_error: Mutex<Option<String>>,
    #[cfg(test)]
    terminal_events: Mutex<Vec<EngineDownloadStatus>>,
}

impl EngineControl {
    pub fn snapshot_status(&self) -> EngineDownloadStatus {
        let mut status = self
            .status
            .lock()
            .map(|guard| guard.clone())
            .unwrap_or_default();
        if status.stage.is_empty() {
            status.stage = "idle".into();
        }
        status
    }
}

pub(crate) fn effective_bundled_backend(config: &UpdateConfig) -> String {
    if !config.backend.is_empty() {
        config.backend.clone()
    } else if cfg!(target_os = "macos") {
        "mps".into()
    } else {
        "cpu".into()
    }
}

fn backend_sort_rank(backend: &str) -> (u8, &str) {
    let rank = match backend {
        "cuda" => 0,
        "rocm" => 1,
        "directml" => 2,
        "mps" => 3,
        "cpu" => 4,
        _ => 5,
    };
    (rank, backend)
}

pub(crate) fn backend_supported_on_os(os: &str, backend: &str) -> bool {
    match os {
        "windows" => matches!(backend, "cpu" | "directml" | "cuda"),
        "linux" => matches!(backend, "cpu" | "cuda" | "rocm"),
        "macos" => matches!(backend, "mps"),
        _ => backend == "cpu",
    }
}

fn normalized_manifest_backend(value: &str) -> AppResult<String> {
    match value {
        "CUDA" => Ok("cuda".into()),
        "ROCM" => Ok("rocm".into()),
        "DIRECTML" => Ok("directml".into()),
        "CPU" => Ok("cpu".into()),
        "MPS" => Ok("mps".into()),
        _ => Err(invalid("Unsupported engine payload backend")),
    }
}

fn infer_backend_from_id(id: &str) -> Option<String> {
    for backend in ["cuda", "rocm", "directml", "mps", "cpu"] {
        if id.contains(&format!("-{backend}-")) {
            return Some(backend.into());
        }
    }
    None
}

#[cfg(test)]
thread_local! {
    static FREE_SPACE_OVERRIDE: std::cell::Cell<Option<u64>> = const { std::cell::Cell::new(None) };
}

fn available_space(path: &Path) -> AppResult<u64> {
    #[cfg(test)]
    if let Some(bytes) = FREE_SPACE_OVERRIDE.with(|cell| cell.get()) {
        return Ok(bytes);
    }
    Ok(fs2::available_space(path)?)
}

fn ensure_not_managed_or_busy(state: &AppState) -> AppResult<()> {
    crate::distribution::require_direct_updates()?;
    if state.updates.busy.load(Ordering::SeqCst) || state.updates.installing.load(Ordering::SeqCst)
    {
        return Err(invalid("An app update operation is already running"));
    }
    Ok(())
}

fn ensure_no_running_jobs(state: &AppState) -> AppResult<()> {
    if !lock(&state.runtime)?.active_job_id.is_empty()
        || lock(&state.database)?.has_inflight_jobs()?
    {
        return Err(invalid(
            "Finish or cancel active jobs before changing the inference engine",
        ));
    }
    Ok(())
}

pub(crate) fn is_allowed_feed_or_download_url(url: &Url, config: &UpdateConfig) -> bool {
    if url.host_str().is_none() {
        return false;
    }
    if url.scheme() == "https" {
        return true;
    }
    (cfg!(debug_assertions) || cfg!(test))
        && config.feed.starts_with("http://127.0.0.1:")
        && url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
}

fn http_client(config: &UpdateConfig) -> AppResult<reqwest::Client> {
    let owned = config.clone();
    let policy = reqwest::redirect::Policy::custom(move |attempt| {
        if attempt.previous().len() >= 10 || !is_allowed_feed_or_download_url(attempt.url(), &owned)
        {
            attempt.stop()
        } else {
            attempt.follow()
        }
    });
    Ok(reqwest::Client::builder()
        .user_agent(concat!("LocalSR/", env!("CARGO_PKG_VERSION")))
        .connect_timeout(Duration::from_secs(15))
        .timeout(Duration::from_secs(3600))
        .redirect(policy)
        .build()?)
}

pub(crate) async fn fetch_feed_json(
    config: &UpdateConfig,
    feed_channel: &str,
) -> AppResult<serde_json::Value> {
    if !matches!(feed_channel, "stable" | "beta") {
        return Err(invalid("Unknown update channel"));
    }
    if !config.enabled() {
        return Err(invalid(
            "Signed updates have not been configured for this build",
        ));
    }
    let endpoint = format!(
        "{}/{}.json",
        config.feed.trim_end_matches('/'),
        feed_channel
    );
    let url: Url = endpoint
        .parse()
        .map_err(|e| invalid(format!("Invalid update feed URL: {e}")))?;
    if !is_allowed_feed_or_download_url(&url, config) {
        return Err(invalid("Update feeds require HTTPS"));
    }
    let client = http_client(config)?;
    let response = client
        .get(url)
        .timeout(Duration::from_secs(30))
        .send()
        .await?
        .error_for_status()?;
    if !is_allowed_feed_or_download_url(response.url(), config) {
        return Err(invalid("Update feed redirect left HTTPS"));
    }
    let bytes = response.bytes().await?;
    Ok(serde_json::from_slice(&bytes)?)
}

/// Extract and signature-verify all engine packages of the feed for `os`/`arch` and
/// `config.kind`. Unlike app updates, entries for any backend of this OS/arch are listed,
/// and `version == installed_version` is accepted alongside newer versions.
pub(crate) fn parse_feed_engines_for(
    raw_json: &serde_json::Value,
    config: &UpdateConfig,
    feed_channel: &str,
    installed_version: &str,
    os: &str,
    arch: &str,
) -> AppResult<Vec<VerifiedCatalogEntry>> {
    let feed_version = raw_json["version"]
        .as_str()
        .ok_or_else(|| invalid("Update feed is missing a release version"))?;
    let platforms = raw_json["platforms"]
        .as_object()
        .ok_or_else(|| invalid("Update feed is missing platform entries"))?;
    let feed_os = if os == "macos" { "darwin" } else { os };
    let prefix = format!("{feed_os}-{arch}-");
    let suffix = format!("-{}", config.kind);
    let mut entries = Vec::new();
    for (target_key, entry) in platforms {
        let Some(rest) = target_key.strip_prefix(&prefix) else {
            continue;
        };
        let Some(backend) = rest.strip_suffix(&suffix) else {
            continue;
        };
        if backend.is_empty()
            || !backend
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
            || !backend_supported_on_os(os, backend)
        {
            continue;
        }
        let localsr = &entry["localsr"];
        if localsr.is_null() {
            continue;
        }
        let contract: ReleaseContract = serde_json::from_value(localsr.clone())?;
        if contract.protocol != PROTOCOL_VERSION {
            continue;
        }
        validate_contract_for_backend(
            config,
            backend,
            feed_channel,
            feed_version,
            &contract,
            true,
        )?;
        verify_statement_for_entry(
            config,
            target_key,
            backend,
            feed_channel,
            feed_version,
            installed_version,
            &contract,
            false,
        )?;
        let Some(engine) = contract.engine else {
            continue;
        };
        let size = engine
            .manifest
            .size
            .checked_add(
                engine
                    .parts
                    .iter()
                    .try_fold(0u64, |acc, part| acc.checked_add(part.size))
                    .ok_or_else(|| invalid("Engine package size overflow"))?,
            )
            .ok_or_else(|| invalid("Engine package size overflow"))?;
        entries.push(VerifiedCatalogEntry {
            backend: backend.to_string(),
            engine_id: contract.engine_id,
            version: feed_version.to_string(),
            size,
            manifest_url: engine.manifest.url.clone(),
            engine,
        });
    }
    entries.sort_by(|a, b| backend_sort_rank(&a.backend).cmp(&backend_sort_rank(&b.backend)));
    Ok(entries)
}

pub(crate) fn update_catalog_from_feed(
    state: &AppState,
    raw_json: &serde_json::Value,
    config: &UpdateConfig,
    feed_channel: &str,
    installed_version: &str,
) {
    match parse_feed_engines_for(
        raw_json,
        config,
        feed_channel,
        installed_version,
        std::env::consts::OS,
        std::env::consts::ARCH,
    ) {
        Ok(entries) => {
            if let Ok(mut catalog) = state.engines.catalog.lock() {
                *catalog = entries;
            }
            if let Ok(mut err) = state.engines.catalog_error.lock() {
                *err = None;
            }
        }
        Err(error) => {
            if let Ok(mut err) = state.engines.catalog_error.lock() {
                *err = Some(error.to_string());
            }
        }
    }
}

pub(crate) fn list_installed_engines_for(
    paths: &AppPaths,
    config: &UpdateConfig,
    bundled_path: Option<&Path>,
    os: &str,
    arch: &str,
) -> Vec<InstalledEngine> {
    let managed_by_store = crate::distribution::managed_by_store();
    let active_id = active_engine_id(paths, managed_by_store, &config.engine_id);
    let bundled_backend = effective_bundled_backend(config);
    let mut installed = Vec::new();
    let engines_root = paths.next_root.join("engines");
    if let Ok(entries) =
        updates::real_engine_directory(&engines_root).and_then(|()| fs::read_dir(&engines_root))
    {
        let mut dirs: Vec<_> = entries.flatten().collect();
        dirs.sort_by_key(|entry| entry.file_name());
        for entry in dirs {
            if updates::real_engine_directory(&entry.path()).is_err() {
                continue;
            }
            let id = entry.file_name().to_string_lossy().into_owned();
            if !safe_id(&id) {
                continue;
            }
            let executable = engine_executable_path(&paths.next_root, &id);
            let meta = read_engine_meta(&paths.next_root, &id);
            let verified = executable.is_some()
                && meta.as_ref().is_some_and(|meta| {
                    engine_meta_valid_for_host(meta, os, arch)
                        && meta.id == id
                        && updates::engine_update_matches_host(&entry.path())
                        && updates::quick_verify_engine_files(&paths.next_root, &id, meta)
                });
            let backend = meta
                .filter(|meta| meta.id == id)
                .map(|meta| meta.backend)
                .or_else(|| (id == config.engine_id).then(|| config.backend.clone()))
                .or_else(|| infer_backend_from_id(&id));
            let Some(backend) = backend else { continue };
            installed.push(InstalledEngine {
                active: verified && active_id.as_deref() == Some(id.as_str()),
                id,
                backend,
                bundled: false,
                path: executable
                    .map(|path| path.to_string_lossy().into_owned())
                    .unwrap_or_default(),
                needs_reinstall: !verified,
            });
        }
    }
    let bundled_id = if config.engine_id.is_empty() {
        "bundled".to_string()
    } else {
        config.engine_id.clone()
    };
    let has_bundled_binary = bundled_path.is_some();
    if (has_bundled_binary || cfg!(debug_assertions))
        && !installed
            .iter()
            .any(|item| item.id == bundled_id && !item.needs_reinstall)
    {
        installed.push(InstalledEngine {
            id: bundled_id,
            backend: bundled_backend,
            bundled: true,
            needs_reinstall: false,
            active: active_id.is_none(),
            path: bundled_path
                .map(|path| path.to_string_lossy().into_owned())
                .unwrap_or_default(),
        });
    }
    installed.sort_by(|a, b| {
        backend_sort_rank(&a.backend)
            .cmp(&backend_sort_rank(&b.backend))
            .then_with(|| a.id.cmp(&b.id))
    });
    installed
}

pub(crate) fn snapshot_for(
    state: &AppState,
    config: &UpdateConfig,
    recommendation: EngineRecommendation,
) -> EngineManagerSnapshot {
    let managed_by_store = crate::distribution::managed_by_store();
    let bundled_path = state
        .bundled_engine_path
        .lock()
        .ok()
        .and_then(|guard| guard.clone());
    let mut installed = list_installed_engines_for(
        &state.paths,
        config,
        bundled_path.as_deref(),
        std::env::consts::OS,
        std::env::consts::ARCH,
    );
    let worker_choice = state.worker.failed_engines.lock().ok().and_then(|failed| {
        updates::worker_engine_choice(&state.paths, bundled_path.clone(), &failed)
    });
    if let Some((_, Some(id))) = &worker_choice {
        for engine in &mut installed {
            engine.active = !engine.bundled && engine.id == *id;
        }
    }
    if state.worker.use_bundled_fallback.load(Ordering::SeqCst)
        || matches!(worker_choice, Some((_, None)))
    {
        for engine in &mut installed {
            engine.active = engine.bundled;
        }
        if !installed.iter().any(|engine| engine.bundled) {
            installed.push(InstalledEngine {
                id: "bundled".into(),
                backend: effective_bundled_backend(config),
                bundled: true,
                needs_reinstall: false,
                active: true,
                path: bundled_path
                    .as_ref()
                    .map(|path| path.to_string_lossy().into_owned())
                    .unwrap_or_default(),
            });
        }
    }
    let bundled_backend = effective_bundled_backend(config);
    let (active_backend, active_engine_id) = installed
        .iter()
        .find(|item| item.active)
        .map(|item| (item.backend.clone(), item.id.clone()))
        .unwrap_or_else(|| {
            (
                bundled_backend.clone(),
                if config.engine_id.is_empty() {
                    "bundled".into()
                } else {
                    config.engine_id.clone()
                },
            )
        });
    let catalog_entries = state
        .engines
        .catalog
        .lock()
        .map(|guard| {
            guard
                .iter()
                .map(|entry| EngineCatalogEntry {
                    backend: entry.backend.clone(),
                    engine_id: entry.engine_id.clone(),
                    version: entry.version.clone(),
                    size: entry.size,
                    manifest_url: entry.manifest_url.clone(),
                    parts_count: entry.engine.parts.len(),
                    installed: installed
                        .iter()
                        .any(|item| item.id == entry.engine_id && !item.needs_reinstall),
                    active: active_engine_id == entry.engine_id,
                })
                .collect()
        })
        .unwrap_or_default();
    let catalog_error = state
        .engines
        .catalog_error
        .lock()
        .ok()
        .and_then(|guard| guard.clone());
    EngineManagerSnapshot {
        supported: !managed_by_store && !cfg!(target_os = "macos"),
        managed_by_store,
        configured: config.enabled(),
        active_backend,
        active_engine_id,
        bundled_backend,
        installed,
        recommendation,
        catalog: catalog_entries,
        catalog_error,
        download: state.engines.snapshot_status(),
    }
}

fn publish_engine_status(state: &AppState, app: Option<&AppHandle>, status: &EngineDownloadStatus) {
    #[cfg(test)]
    if matches!(status.stage.as_str(), "error" | "cancelled" | "installed") {
        state
            .engines
            .terminal_events
            .lock()
            .unwrap()
            .push(status.clone());
    }
    if let Ok(mut stored) = state.engines.status.lock() {
        *stored = status.clone();
    }
    if let Some(app) = app {
        let _ = app.emit("engine-status", status.clone());
    }
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
    let (Ok(start), Ok(end), Ok(total)) = (
        start.parse::<u64>(),
        end.parse::<u64>(),
        total.parse::<u64>(),
    ) else {
        return false;
    };
    start == offset && end >= start && end < expected_size && total == expected_size
}

fn file_on_disk_progress(directory: &Path, file: &DownloadFile) -> u64 {
    if !safe_file_name(&file.name) {
        return 0;
    }
    let destination = directory.join(&file.name);
    if destination.is_file() && fs::metadata(&destination).is_ok_and(|m| m.len() == file.size) {
        return file.size;
    }
    let partial = directory.join(format!("{}.part", file.name));
    if fs::symlink_metadata(&partial).is_ok_and(|m| !m.file_type().is_symlink() && m.is_file()) {
        let len = fs::metadata(&partial).map(|m| m.len()).unwrap_or(0);
        if len <= file.size {
            return len;
        }
    }
    0
}

/// Download a file into `<name>.part` in `directory` with HTTP Range resume, verify
/// size, SHA-256 and minisign signature on completion, and atomically rename into place.
/// Leaves `.part` intact on cancellation or transient connection loss so a later run resumes.
pub(crate) async fn download_file_resumable(
    file: &DownloadFile,
    directory: &Path,
    config: &UpdateConfig,
    cancel: &AtomicBool,
    on_progress: &mut impl FnMut(i64),
) -> AppResult<()> {
    if !safe_file_name(&file.name) {
        return Err(invalid("Unsafe engine download filename"));
    }
    let url: Url = file
        .url
        .parse()
        .map_err(|e| invalid(format!("Invalid engine download URL: {e}")))?;
    if !is_allowed_feed_or_download_url(&url, config) {
        return Err(invalid("Engine downloads require HTTPS"));
    }
    let destination = directory.join(&file.name);
    if destination.is_file() && verify_file(&destination, file, &config.public_key).is_ok() {
        return Ok(());
    }
    let partial = directory.join(format!("{}.part", file.name));
    if fs::symlink_metadata(&partial).is_ok_and(|m| m.file_type().is_symlink()) {
        let _ = fs::remove_file(&partial);
    }
    let mut offset = fs::metadata(&partial).map(|m| m.len()).unwrap_or(0);
    if offset > file.size {
        let _ = fs::remove_file(&partial);
        on_progress(-(offset as i64));
        offset = 0;
    } else if offset == file.size {
        if verify_file(&partial, file, &config.public_key).is_ok() {
            fs::rename(&partial, &destination)?;
            return Ok(());
        }
        let _ = fs::remove_file(&partial);
        on_progress(-(offset as i64));
        offset = 0;
    }
    if cancel.load(Ordering::SeqCst) {
        return Err(invalid("Engine download cancelled"));
    }
    let client = http_client(config)?;
    let mut request = client.get(url);
    if offset > 0 {
        request = request.header(header::RANGE, format!("bytes={offset}-"));
    }
    let send_future = request.send();
    tokio::pin!(send_future);
    let response = loop {
        if cancel.load(Ordering::SeqCst) {
            return Err(invalid("Engine download cancelled"));
        }
        match tokio::time::timeout(Duration::from_millis(200), &mut send_future).await {
            Err(_) => continue,
            Ok(response) => break response?,
        }
    };
    if !is_allowed_feed_or_download_url(response.url(), config) {
        return Err(invalid("Engine download redirect left HTTPS"));
    }
    let status = response.status();
    let resume = offset > 0 && status == StatusCode::PARTIAL_CONTENT;
    if resume {
        let valid_range = response
            .headers()
            .get(header::CONTENT_RANGE)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| valid_content_range(value, offset, file.size));
        if !valid_range {
            let _ = fs::remove_file(&partial);
            return Err(invalid("Engine server returned an invalid resume range"));
        }
    } else if status == StatusCode::RANGE_NOT_SATISFIABLE {
        let _ = fs::remove_file(&partial);
        return Err(invalid("Engine server rejected the resume offset"));
    } else if !status.is_success() {
        return Err(invalid(format!("Engine server returned HTTP {status}")));
    }

    let mut current_size = if resume {
        offset
    } else {
        if offset > 0 {
            on_progress(-(offset as i64));
        }
        0
    };
    let mut output = if resume {
        OpenOptions::new().append(true).open(&partial)?
    } else {
        let _ = fs::remove_file(&partial);
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&partial)?
    };
    let mut stream = response.bytes_stream();
    loop {
        if cancel.load(Ordering::SeqCst) {
            output.sync_all()?;
            return Err(invalid("Engine download cancelled"));
        }
        let next = tokio::time::timeout(Duration::from_millis(200), stream.next()).await;
        let chunk = match next {
            Err(_) => continue,
            Ok(None) => break,
            Ok(Some(chunk)) => chunk.inspect_err(|_| {
                let _ = output.sync_all();
            })?,
        };
        current_size = current_size.saturating_add(chunk.len() as u64);
        if current_size > file.size {
            drop(output);
            let _ = fs::remove_file(&partial);
            return Err(invalid("Engine download exceeded its declared size"));
        }
        output.write_all(&chunk)?;
        on_progress(chunk.len() as i64);
    }
    output.sync_all()?;
    drop(output);
    if current_size != file.size {
        return Err(invalid(format!(
            "Engine download ended at {current_size} of {} bytes",
            file.size
        )));
    }
    if let Err(error) = verify_file(&partial, file, &config.public_key) {
        let _ = fs::remove_file(&partial);
        return Err(error);
    }
    fs::rename(&partial, &destination)?;
    Ok(())
}

pub(crate) async fn refresh_engine_catalog_in(
    state: &Arc<AppState>,
    app: Option<&AppHandle>,
    config: &UpdateConfig,
    feed_channel: Option<&str>,
    installed_version: &str,
) -> AppResult<Vec<EngineCatalogEntry>> {
    crate::distribution::require_direct_updates()?;
    if !lock(&state.settings)?.privacy_choice_made {
        return Err(invalid("Choose update privacy settings first"));
    }
    let chosen_channel = feed_channel
        .map(str::to_owned)
        .unwrap_or_else(|| channel(state));
    let feed_json = fetch_feed_json(config, &chosen_channel).await?;
    let parsed = parse_feed_engines_for(
        &feed_json,
        config,
        &chosen_channel,
        installed_version,
        std::env::consts::OS,
        std::env::consts::ARCH,
    )?;
    *lock(&state.engines.catalog)? = parsed;
    *lock(&state.engines.catalog_error)? = None;
    let snapshot = snapshot_for(state, config, gpu_notice::detect_engine_recommendation());
    if let Some(app) = app {
        emit_state_changed(app);
    }
    Ok(snapshot.catalog)
}

#[tauri::command]
pub async fn refresh_engine_catalog(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    channel: Option<String>,
) -> AppResult<Vec<EngineCatalogEntry>> {
    let config = UpdateConfig::load();
    let version = app.package_info().version.to_string();
    refresh_engine_catalog_in(&state, Some(&app), &config, channel.as_deref(), &version).await
}

pub(crate) async fn install_engine_in(
    state: &Arc<AppState>,
    app: Option<&AppHandle>,
    config: &UpdateConfig,
    backend: &str,
    installed_version: &str,
    handshake: impl FnOnce(&Path) -> AppResult<()> + Send + 'static,
) -> AppResult<()> {
    install_engine_for(
        state,
        app,
        config,
        backend,
        installed_version,
        std::env::consts::OS,
        std::env::consts::ARCH,
        handshake,
    )
    .await
}

#[allow(clippy::too_many_arguments)]
pub(crate) async fn install_engine_for(
    state: &Arc<AppState>,
    app: Option<&AppHandle>,
    config: &UpdateConfig,
    backend: &str,
    installed_version: &str,
    os: &str,
    arch: &str,
    handshake: impl FnOnce(&Path) -> AppResult<()> + Send + 'static,
) -> AppResult<()> {
    ensure_not_managed_or_busy(state)?;
    if !lock(&state.settings)?.privacy_choice_made {
        return Err(invalid("Choose update privacy settings first"));
    }
    ensure_no_running_jobs(state)?;
    let _busy = acquire(&state.engines.busy)?;
    state.engines.cancel.store(false, Ordering::SeqCst);

    if !backend_supported_on_os(os, backend) {
        return Err(invalid(format!(
            "The {backend} engine is not available on this platform"
        )));
    }

    let entry = {
        let existing = lock(&state.engines.catalog)?
            .iter()
            .find(|item| item.backend == backend)
            .cloned();
        match existing {
            Some(entry) => entry,
            None => {
                let feed_channel = channel(state);
                let feed_json = fetch_feed_json(config, &feed_channel).await?;
                let parsed = parse_feed_engines_for(
                    &feed_json,
                    config,
                    &feed_channel,
                    installed_version,
                    os,
                    arch,
                )?;
                let found = parsed
                    .iter()
                    .find(|item| item.backend == backend)
                    .cloned()
                    .ok_or_else(|| {
                        invalid(format!("No {backend} engine package found in the feed"))
                    })?;
                *lock(&state.engines.catalog)? = parsed;
                *lock(&state.engines.catalog_error)? = None;
                found
            }
        }
    };

    let engine = entry.engine.clone();
    let engine_id = entry.engine_id.clone();
    let cache_dir = state
        .paths
        .next_root
        .join("updates")
        .join("engine-cache")
        .join(&engine_id);
    fs::create_dir_all(&cache_dir)?;

    let already_present = std::iter::once(&engine.manifest)
        .chain(&engine.parts)
        .map(|file| file_on_disk_progress(&cache_dir, file))
        .sum::<u64>();
    let total = entry.size;
    let remaining_download = total.saturating_sub(already_present);
    require_space(
        available_space(&cache_dir)?,
        remaining_download
            .checked_add(256 * 1024 * 1024)
            .ok_or_else(|| invalid("Engine download size overflow"))?,
    )?;

    let mut status = EngineDownloadStatus {
        stage: "downloading".into(),
        backend: backend.into(),
        engine_id: engine_id.clone(),
        downloaded: already_present,
        total,
        error: None,
    };
    publish_engine_status(state, app, &status);

    let result = async {
        let download_result = async {
            let mut last_emit = std::time::Instant::now();
            let mut on_progress = |delta: i64| {
                if delta >= 0 {
                    status.downloaded = status.downloaded.saturating_add(delta as u64).min(total);
                } else {
                    status.downloaded = status.downloaded.saturating_sub((-delta) as u64);
                }
                if last_emit.elapsed() >= Duration::from_millis(100) || status.downloaded >= total {
                    publish_engine_status(state, app, &status);
                    last_emit = std::time::Instant::now();
                }
            };

            download_file_resumable(
                &engine.manifest,
                &cache_dir,
                config,
                &state.engines.cancel,
                &mut on_progress,
            )
            .await?;
            let manifest_bytes = updates::read_verified(
                &cache_dir.join(&engine.manifest.name),
                &engine.manifest,
                &config.public_key,
            )?;
            let parsed_manifest = engine_payload::parse_manifest(&manifest_bytes)?;
            if parsed_manifest.schema_version == 2
                && (normalized_manifest_backend(&parsed_manifest.backend)? != backend
                    || parsed_manifest.os.as_deref() != Some(os)
                    || parsed_manifest.arch.as_deref() != Some(arch)
                    || parsed_manifest.protocol_version != Some(PROTOCOL_VERSION)
                    || parsed_manifest.engine_id.as_deref() != Some(engine_id.as_str()))
            {
                return Err(invalid(
                    "Engine payload manifest metadata does not match this platform or engine",
                ));
            }
            let parts_remaining = engine
                .parts
                .iter()
                .map(|p| p.size.saturating_sub(file_on_disk_progress(&cache_dir, p)))
                .sum::<u64>();
            require_space(
                available_space(&state.paths.next_root)?,
                parts_remaining
                    .checked_add(parsed_manifest.unpacked_bytes)
                    .and_then(|s| s.checked_add(256 * 1024 * 1024))
                    .ok_or_else(|| invalid("Engine unpacked size overflow"))?,
            )?;
            for part in &engine.parts {
                download_file_resumable(
                    part,
                    &cache_dir,
                    config,
                    &state.engines.cancel,
                    &mut on_progress,
                )
                .await?;
            }
            Ok::<_, AppError>(())
        }
        .await;

        download_result?;

        status.stage = "installing".into();
        status.downloaded = total;
        publish_engine_status(state, app, &status);

        status.stage = "handshaking".into();
        publish_engine_status(state, app, &status);
        let state_for_commit = state.clone();
        let app_for_commit = app.cloned();
        let next_root = state.paths.next_root.clone();
        let engine_id_for_install = engine_id.clone();
        let cache_dir_clone = cache_dir.clone();
        let engine_clone = engine.clone();
        let public_key = config.public_key.clone();
        let backend_owned = backend.to_string();
        let os_owned = os.to_string();
        let arch_owned = arch.to_string();
        let version_owned = entry.version;
        tauri::async_runtime::spawn_blocking(move || -> AppResult<()> {
            let staged = updates::stage_verified_engine(
                &next_root,
                &engine_id_for_install,
                &cache_dir_clone,
                &engine_clone,
                &public_key,
                &os_owned,
                &arch_owned,
                handshake,
            )?;
            let meta = EngineMeta {
                id: engine_id_for_install,
                backend: backend_owned,
                os: os_owned,
                arch: arch_owned,
                protocol_version: PROTOCOL_VERSION,
                handshake_passed: true,
                verified_install: true,
                version: version_owned,
                files: staged.files.clone(),
            };
            commit_installed_engine(&state_for_commit, app_for_commit.as_ref(), staged, &meta)
        })
        .await
        .map_err(|e| invalid(e.to_string()))??;

        let _ = fs::remove_dir_all(&cache_dir);

        if backend != "cpu" {
            state.choose_device.store(true, Ordering::SeqCst);
        }

        status.stage = "installed".into();
        status.error = None;
        publish_engine_status(state, app, &status);
        if let Some(app) = app {
            emit_state_changed(app);
        }
        Ok::<(), AppError>(())
    }
    .await;
    if let Err(error) = &result {
        status.stage = if matches!(error, AppError::Validation(message) if message == "Engine download cancelled")
        {
            "cancelled".into()
        } else {
            "error".into()
        };
        status.error = Some(error.to_string());
        publish_engine_status(state, app, &status);
    }
    result
}

#[tauri::command]
pub async fn install_engine(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    backend: String,
) -> AppResult<()> {
    let config = UpdateConfig::load();
    let version = app.package_info().version.to_string();
    let work_root = state.paths.work_root.clone();
    install_engine_in(
        &state,
        Some(&app),
        &config,
        &backend,
        &version,
        move |exe| {
            crate::headless_smoke::run_worker_handshake(
                exe,
                Duration::from_secs(60),
                true,
                &work_root,
            )
            .map(|_| ())
            .map_err(AppError::Worker)
        },
    )
    .await
}

#[tauri::command]
pub fn cancel_engine_install(state: State<'_, Arc<AppState>>) {
    state.engines.cancel.store(true, Ordering::SeqCst);
}

pub(crate) fn use_engine_for(
    state: &Arc<AppState>,
    app: Option<&AppHandle>,
    config: &UpdateConfig,
    backend: &str,
    os: &str,
    arch: &str,
    _handshake: impl FnOnce(&Path) -> AppResult<()>,
) -> AppResult<()> {
    ensure_not_managed_or_busy(state)?;
    let _busy = acquire(&state.engines.busy)?;
    let _scheduler = lock(&state.scheduler)?;
    ensure_no_running_jobs(state)?;
    let engine_files = lock(&state.worker.engine_files)?;

    let bundled_path = state
        .bundled_engine_path
        .lock()
        .ok()
        .and_then(|guard| guard.clone());
    let installed =
        list_installed_engines_for(&state.paths, config, bundled_path.as_deref(), os, arch);

    let chosen = installed
        .iter()
        .find(|item| item.backend == backend && !item.bundled && !item.needs_reinstall)
        .or_else(|| {
            installed
                .iter()
                .find(|item| item.backend == backend && !item.needs_reinstall)
        })
        .ok_or_else(|| invalid(format!("The {backend} engine is not installed")))?;

    if chosen.bundled {
        updates::deactivate_active_engine(&state.paths)?;
    } else {
        update_storage::write_atomic(
            &state.paths.next_root.join("active-engine.txt"),
            chosen.id.as_bytes(),
        )?;
    }

    lock(&state.worker.failed_engines)?.remove(&chosen.id);
    drop(engine_files);
    if backend != "cpu" {
        state.choose_device.store(true, Ordering::SeqCst);
    }

    if let Some(app) = app {
        worker::restart_for_engine_switch(state, app)?;
        emit_state_changed(app);
    }
    Ok(())
}

#[tauri::command]
pub async fn use_engine(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    backend: String,
) -> AppResult<()> {
    let state = state.inner().clone();
    let config = UpdateConfig::load();
    tauri::async_runtime::spawn_blocking(move || {
        use_engine_for(
            &state,
            Some(&app),
            &config,
            &backend,
            std::env::consts::OS,
            std::env::consts::ARCH,
            |exe| {
                crate::headless_smoke::run_worker_handshake(
                    exe,
                    Duration::from_secs(60),
                    true,
                    &state.paths.work_root,
                )
                .map(|_| ())
                .map_err(AppError::Worker)
            },
        )
    })
    .await
    .map_err(|e| invalid(e.to_string()))?
}

pub(crate) fn remove_engine_in(
    state: &AppState,
    app: Option<&AppHandle>,
    config: &UpdateConfig,
    id: &str,
) -> AppResult<()> {
    ensure_not_managed_or_busy(state)?;
    let _busy = acquire(&state.engines.busy)?;
    let _scheduler = lock(&state.scheduler)?;
    ensure_no_running_jobs(state)?;

    if id == "bundled" || !safe_id(id) {
        return Err(invalid("Cannot remove the bundled or invalid engine"));
    }
    let active_file_id = fs::read_to_string(state.paths.next_root.join("active-engine.txt"))
        .ok()
        .map(|s| s.trim().to_owned());
    if active_file_id.as_deref() == Some(id)
        || (!state.worker.use_bundled_fallback.load(Ordering::SeqCst)
            && active_engine_id(
                &state.paths,
                crate::distribution::managed_by_store(),
                &config.engine_id,
            )
            .as_deref()
                == Some(id))
    {
        return Err(invalid(
            "Cannot remove the currently active engine. Switch to another engine first.",
        ));
    }
    let _engine_files = lock(&state.worker.engine_files)?;
    let engine_root = updates::engine_install_root(&state.paths.next_root, id)?;
    let backup = updates::engine_backup_path(&state.paths.next_root.join("engines"), id);
    let has_backup = updates::engine_directory_exists(&backup)?;
    updates::check_engine_tree_is_real(&engine_root)?;
    if has_backup {
        updates::check_engine_tree_is_real(&backup)?;
        fs::remove_dir_all(&backup)?;
    }
    fs::remove_dir_all(&engine_root)?;
    let updates_root = state.paths.next_root.join("updates");
    let cache_root = updates_root.join("engine-cache");
    let cache = cache_root.join(id);
    if updates::real_engine_directory(&updates_root).is_ok()
        && updates::real_engine_directory(&cache_root).is_ok()
        && updates::check_engine_tree_is_real(&cache).is_ok()
    {
        let _ = fs::remove_dir_all(cache);
    }
    if let Some(app) = app {
        emit_state_changed(app);
    }
    Ok(())
}

#[tauri::command]
pub async fn remove_engine(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    id: String,
) -> AppResult<()> {
    let state = state.inner().clone();
    let config = UpdateConfig::load();
    tauri::async_runtime::spawn_blocking(move || remove_engine_in(&state, Some(&app), &config, &id))
        .await
        .map_err(|error| invalid(error.to_string()))?
}

pub(crate) fn verify_engine_for(
    state: &Arc<AppState>,
    app: Option<&AppHandle>,
    config: &UpdateConfig,
    id: &str,
    os: &str,
    arch: &str,
) -> AppResult<()> {
    ensure_not_managed_or_busy(state)?;
    let _busy = acquire(&state.engines.busy)?;
    let _scheduler = lock(&state.scheduler)?;
    ensure_no_running_jobs(state)?;
    if id == "bundled" || !safe_id(id) {
        return Err(invalid("Cannot verify the bundled or invalid engine"));
    }
    let active_file_id = fs::read_to_string(state.paths.next_root.join("active-engine.txt"))
        .ok()
        .map(|s| s.trim().to_owned());
    let was_active = active_file_id.as_deref() == Some(id)
        || (!state.worker.use_bundled_fallback.load(Ordering::SeqCst)
            && active_engine_id(
                &state.paths,
                crate::distribution::managed_by_store(),
                &config.engine_id,
            )
            .as_deref()
                == Some(id));
    // A worker start can restore a backup over this engine; read, check and mark
    // one copy. Lock order: scheduler, then engine_files, as in use_engine_for.
    let engine_files = lock(&state.worker.engine_files)?;
    let verification = match read_engine_meta(&state.paths.next_root, id) {
        Some(meta) if engine_meta_valid_for_host(&meta, os, arch) && meta.id == id => {
            updates::full_verify_engine_files(&state.paths.next_root, id, &meta)
        }
        _ => Err(invalid(
            "Engine verification metadata is missing or invalid",
        )),
    };
    if let Err(error) = verification {
        updates::mark_engine_needs_reinstall(&state.paths.next_root, id);
        if was_active {
            updates::deactivate_active_engine(&state.paths)?;
            lock(&state.worker.failed_engines)?.insert(id.to_owned());
            // Starting the worker takes engine_files itself.
            drop(engine_files);
            if let Some(app) = app {
                if let Ok(mut runtime) = state.runtime.lock() {
                    runtime.status_title = "Switched to bundled engine".into();
                    runtime.status_detail =
                        "The installed engine failed verification; using the bundled engine instead."
                            .into();
                }
                let _ = worker::restart_for_engine_switch(state, app);
            } else {
                state
                    .worker
                    .use_bundled_fallback
                    .store(true, Ordering::SeqCst);
            }
        }
        if let Some(app) = app {
            emit_state_changed(app);
        }
        return Err(error);
    }
    Ok(())
}

#[tauri::command]
pub async fn verify_engine(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    id: String,
) -> AppResult<()> {
    let state = state.inner().clone();
    let config = UpdateConfig::load();
    tauri::async_runtime::spawn_blocking(move || {
        verify_engine_for(
            &state,
            Some(&app),
            &config,
            &id,
            std::env::consts::OS,
            std::env::consts::ARCH,
        )
    })
    .await
    .map_err(|e| invalid(e.to_string()))?
}

#[tauri::command]
pub async fn choose_engine_manifest(
    app: AppHandle,
    state: State<'_, Arc<AppState>>,
) -> AppResult<Option<String>> {
    use tauri_plugin_dialog::DialogExt;

    let dialog = app
        .dialog()
        .file()
        .set_title("Choose engine payload manifest")
        .add_filter("Engine payload manifest", &["json"]);
    let picked = tauri::async_runtime::spawn_blocking(move || dialog.blocking_pick_file())
        .await
        .map_err(|error| AppError::Config(format!("the file dialog failed: {error}")))?;
    let Some(picked) = picked else {
        return Ok(None);
    };
    let path = picked
        .into_path()
        .map_err(|error| AppError::Validation(format!("that is not a file path: {error}")))?;
    state.grants.grant_engine_manifest(&path);
    Ok(Some(path.to_string_lossy().into_owned()))
}

fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn read_signature_file(path: &Path) -> AppResult<String> {
    let sig_path = PathBuf::from(format!("{}.sig", path.display()));
    let meta = fs::symlink_metadata(&sig_path)
        .map_err(|_| invalid(format!("Missing signature file: {}", sig_path.display())))?;
    if meta.file_type().is_symlink() || !meta.file_type().is_file() || meta.len() > 64 * 1024 {
        return Err(invalid(format!(
            "Invalid signature file: {}",
            sig_path.display()
        )));
    }
    Ok(fs::read_to_string(sig_path)?.trim().to_owned())
}

fn verify_imported_files(
    manifest_path: &Path,
    manifest_bytes: &[u8],
    parsed: &engine_payload::ParsedManifest,
    source_dir: &Path,
    public_key: &str,
    catalog: &[VerifiedCatalogEntry],
    backend: &str,
) -> AppResult<Option<String>> {
    if public_key.is_empty() {
        return Err(invalid(
            "Signature verification key is not configured for this build",
        ));
    }
    let manifest_sha = sha256_hex(manifest_bytes);
    let manifest_sig_path = PathBuf::from(format!("{}.sig", manifest_path.display()));
    if manifest_sig_path.exists() {
        let sig_text = read_signature_file(manifest_path)?;
        key(public_key)?
            .verify(manifest_bytes, &signature(&sig_text)?, false)
            .map_err(|e| {
                invalid(format!(
                    "Engine manifest signature verification failed: {e}"
                ))
            })?;
        for part in &parsed.parts {
            if !safe_file_name(&part.filename) {
                return Err(invalid("Invalid part filename in engine manifest"));
            }
            let part_path = source_dir.join(&part.filename);
            let part_sig = read_signature_file(&part_path)?;
            let expected = DownloadFile {
                name: part.filename.clone(),
                url: String::new(),
                size: part.size,
                sha256: part.sha256.clone(),
                signature: part_sig,
            };
            verify_file(&part_path, &expected, public_key)?;
        }
        let matching_id = catalog
            .iter()
            .find(|entry| entry.backend == backend && entry.engine.manifest.sha256 == manifest_sha)
            .map(|entry| entry.engine_id.clone());
        return Ok(matching_id);
    }

    // Fallback when .sig files are absent: match a verified catalog entry's manifest & part signatures.
    let Some(entry) = catalog.iter().find(|entry| {
        entry.backend == backend
            && entry.engine.manifest.sha256 == manifest_sha
            && entry.engine.manifest.size == manifest_bytes.len() as u64
            && entry.engine.parts.len() == parsed.parts.len()
    }) else {
        return Err(invalid(
            "Missing .sig signature files beside the engine payload manifest",
        ));
    };
    let manifest_file = DownloadFile {
        name: entry.engine.manifest.name.clone(),
        url: String::new(),
        size: manifest_bytes.len() as u64,
        sha256: manifest_sha,
        signature: entry.engine.manifest.signature.clone(),
    };
    verify_file(manifest_path, &manifest_file, public_key)?;
    for (part, signed_part) in parsed.parts.iter().zip(&entry.engine.parts) {
        if part.filename != signed_part.name
            || part.size != signed_part.size
            || part.sha256 != signed_part.sha256
        {
            return Err(invalid(
                "Engine payload parts do not match the signed catalog entry",
            ));
        }
        verify_file(&source_dir.join(&part.filename), signed_part, public_key)?;
    }
    Ok(Some(entry.engine_id.clone()))
}

pub(crate) fn recover_interrupted_reimport(next_root: &Path, id: &str) -> AppResult<()> {
    recover_engine_copy(next_root, id).map(|_| ())
}

/// Finish an interrupted install of `id`. Returns whether recovery itself
/// hash-checked the copy that is now installed, so a start need not hash it again.
pub(crate) fn recover_engine_copy(next_root: &Path, id: &str) -> AppResult<bool> {
    if !safe_id(id) {
        return Ok(false);
    }
    let engines = next_root.join("engines");
    if !updates::engine_directory_exists(&engines)? {
        return Ok(false);
    }
    let root = engines.join(id);
    let root_exists = updates::engine_directory_exists(&root)?;
    if updates::engine_directory_exists(&updates::engine_backup_path(&engines, id))? {
        let ready = read_engine_meta(next_root, id).is_some_and(|meta| {
            meta.id == id
                && engine_meta_valid_for_host(&meta, &meta.os, &meta.arch)
                && updates::engine_update_matches_host(&root)
                && updates::quick_verify_engine_files(next_root, id, &meta)
        });
        if !ready {
            return updates::restore_verified_engine_backup(next_root, id);
        }
        return Ok(false);
    }
    if !root_exists {
        return Ok(false);
    }
    let mut backups = Vec::new();
    for entry in fs::read_dir(&root)? {
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name
            .strip_prefix(".backup-")
            .is_some_and(|suffix| uuid::Uuid::parse_str(suffix).is_ok())
        {
            let backup = entry.path();
            updates::check_engine_tree_is_real(&backup)?;
            backups.push(backup);
        }
    }
    let engine = root.join("engine");
    if backups.is_empty() {
        return Ok(false);
    }
    // Older versions backed up only the payload. Use its old metadata if it
    // still verifies the copy that will occupy the installed path.
    let verifies = |directory: &Path| {
        read_engine_meta(next_root, id).is_some_and(|meta| {
            meta.id == id
                && engine_meta_valid_for_host(&meta, &meta.os, &meta.arch)
                && updates::check_engine_directory_files(directory, &meta, true)
        })
    };
    let existing = updates::engine_directory_exists(&engine)?;
    let engine_verified = existing && verifies(&engine);
    let restore = if existing {
        !engine_verified && backups.len() == 1 && verifies(&backups[0])
    } else {
        backups.len() == 1
    };
    let verified = match (restore, existing) {
        (true, true) => true,
        (true, false) => verifies(&backups[0]),
        (false, _) => engine_verified,
    };
    if !verified {
        match fs::remove_file(root.join("meta.json")) {
            Ok(()) => (),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => return Err(error.into()),
        }
    }
    if restore {
        if existing {
            let retired = root.join(format!(".retired-{}", uuid::Uuid::new_v4()));
            fs::rename(&engine, &retired)?;
            fs::rename(&backups[0], &engine)?;
            update_storage::sync_dir(&root)?;
            if updates::check_engine_tree_is_real(&retired).is_ok() {
                let _ = fs::remove_dir_all(retired);
            }
        } else {
            fs::rename(&backups[0], &engine)?;
            update_storage::sync_dir(&root)?;
        }
    } else if existing {
        // Leftovers do not affect the installed copy; a failed removal is
        // retried at the next start.
        for backup in backups {
            if let Err(error) = fs::remove_dir_all(&backup) {
                eprintln!(
                    "LocalSR could not remove the old engine backup {}: {error}",
                    backup.display()
                );
            }
        }
    } else if backups.len() > 1 {
        return Err(invalid("Multiple engine backups need manual recovery"));
    }
    Ok(verified)
}

pub(crate) fn recover_interrupted_engine_installs(next_root: &Path) {
    let engines = next_root.join("engines");
    let Ok(entries) =
        updates::real_engine_directory(&engines).and_then(|()| fs::read_dir(&engines))
    else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        if let Some(id) = name.strip_prefix(".previous-") {
            if let Err(error) = recover_interrupted_reimport(next_root, id) {
                eprintln!("LocalSR could not recover engine {id}: {error}");
            }
        } else if name
            .strip_prefix(".retired-")
            .is_some_and(|suffix| uuid::Uuid::parse_str(suffix).is_ok())
        {
            updates::remove_engine_tree_if_real(&entry.path());
        }
    }
}

/// Startup recovery: finish interrupted swaps, delete retired copies and, while
/// no install or import runs, staging folders a stopped install left behind.
pub(crate) fn recover_engine_files_at_startup(state: &AppState) {
    let Ok(_engine_files) = lock(&state.worker.engine_files) else {
        return;
    };
    recover_interrupted_engine_installs(&state.paths.next_root);
    if let (Ok(_engines), Ok(_updates)) =
        (acquire(&state.engines.busy), acquire(&state.updates.busy))
    {
        remove_abandoned_engine_staging(&state.paths.next_root);
    }
}

fn remove_abandoned_engine_staging(next_root: &Path) {
    let engines = next_root.join("engines");
    let Ok(entries) =
        updates::real_engine_directory(&engines).and_then(|()| fs::read_dir(&engines))
    else {
        return;
    };
    for entry in entries.flatten() {
        let staging = entry
            .file_name()
            .to_str()
            .and_then(|name| name.strip_prefix(".import-"))
            .is_some_and(|suffix| uuid::Uuid::parse_str(suffix).is_ok());
        // A linked folder is left alone; remove_dir_all does not follow links
        // inside a real one.
        if staging && updates::real_engine_directory(&entry.path()).is_ok() {
            if let Err(error) = fs::remove_dir_all(entry.path()) {
                eprintln!(
                    "LocalSR could not remove the unfinished engine install {}: {error}",
                    entry.path().display()
                );
            }
        }
    }
}

fn commit_installed_engine(
    state: &Arc<AppState>,
    app: Option<&AppHandle>,
    staged: updates::StagedEngine,
    meta: &EngineMeta,
) -> AppResult<()> {
    let _scheduler = lock(&state.scheduler)?;
    ensure_no_running_jobs(state)?;
    // Windows cannot rename a folder whose worker or libraries are in use, so a
    // worker running from the target stops before the swap. It stays stopped
    // (shutting_down) until the restart below or the rollback starts it again.
    let stopped = worker::runs_installed_engine(state, &meta.id);
    let stop = if stopped {
        worker::stop_for_engine_switch(state)
    } else {
        Ok(())
    };
    let engine_files = lock(&state.worker.engine_files)?;
    let pointer = state.paths.next_root.join("active-engine.txt");
    let old_pointer = fs::read(&pointer).ok();
    let swap = match stop.and_then(|()| staged.publish(Some(meta), None)) {
        Ok(swap) => swap,
        Err(error) => {
            drop(engine_files);
            if stopped {
                state.worker.shutting_down.store(false, Ordering::SeqCst);
                if let Some(app) = app.filter(|_| state.worker.pid.load(Ordering::SeqCst) == 0) {
                    if let Err(restart) = worker::start_worker(state.clone(), app.clone()) {
                        eprintln!("LocalSR could not restart the previous engine: {restart}");
                    }
                }
            }
            return Err(error);
        }
    };
    let activate = update_storage::write_atomic(&pointer, meta.id.as_bytes()).and_then(|()| {
        lock(&state.worker.failed_engines)?.remove(&meta.id);
        Ok(())
    });
    drop(engine_files);
    let result = activate.and_then(|()| {
        if let Some(app) = app {
            worker::restart_for_engine_switch(state, app)?;
        } else if stopped {
            state.worker.shutting_down.store(false, Ordering::SeqCst);
        }
        Ok(())
    });
    if let Err(error) = result {
        {
            let _engine_files = lock(&state.worker.engine_files)?;
            if let Err(restore) = swap.rollback() {
                eprintln!("LocalSR could not restore the previous engine: {restore}");
            }
            let restored_pointer = if let Some(bytes) = old_pointer {
                update_storage::write_atomic(&pointer, &bytes)
            } else {
                updates::deactivate_active_engine(&state.paths)
            };
            if let Err(restore) = restored_pointer {
                eprintln!("LocalSR could not restore the active engine selection: {restore}");
            }
        }
        state.worker.shutting_down.store(false, Ordering::SeqCst);
        if let Some(app) = app.filter(|_| state.worker.pid.load(Ordering::SeqCst) == 0) {
            if let Err(restart) = worker::start_worker(state.clone(), app.clone()) {
                eprintln!("LocalSR could not restart the previous engine: {restart}");
            }
        }
        return Err(error);
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn ensure_import_not_older(
    paths: &AppPaths,
    config: &UpdateConfig,
    bundled_path: Option<&Path>,
    backend: &str,
    os: &str,
    arch: &str,
    imported: &semver::Version,
    installed_app_version: &str,
) -> AppResult<()> {
    let installed = list_installed_engines_for(paths, config, bundled_path, os, arch);
    for item in installed {
        if item.backend != backend || item.needs_reinstall {
            continue;
        }
        let current_version = if item.bundled {
            if bundled_path.is_none() && config.engine_id.is_empty() {
                continue;
            }
            installed_app_version.to_owned()
        } else {
            read_engine_meta(&paths.next_root, &item.id)
                .map(|meta| meta.version)
                .unwrap_or_default()
        };
        if let Ok(current) = semver::Version::parse(current_version.trim()) {
            if imported < &current {
                return Err(invalid(format!(
                    "Cannot import {backend} engine {imported}: version {current} is already installed"
                )));
            }
        }
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn import_engine_for(
    state: &Arc<AppState>,
    app: Option<&AppHandle>,
    config: &UpdateConfig,
    manifest_path: &Path,
    installed_version: &str,
    os: &str,
    arch: &str,
    handshake: impl FnOnce(&Path) -> AppResult<()>,
) -> AppResult<EngineMeta> {
    ensure_not_managed_or_busy(state)?;
    ensure_no_running_jobs(state)?;
    let _busy = acquire(&state.engines.busy)?;

    let meta_fs = fs::symlink_metadata(manifest_path)
        .map_err(|_| invalid("The selected engine manifest does not exist"))?;
    if meta_fs.file_type().is_symlink() || !meta_fs.file_type().is_file() {
        return Err(invalid("Engine manifest must be a regular file"));
    }
    if meta_fs.len() == 0 || meta_fs.len() > 1024 * 1024 {
        return Err(invalid("Engine manifest size is invalid"));
    }
    let source_dir = manifest_path
        .parent()
        .ok_or_else(|| invalid("Engine manifest has no parent folder"))?;
    let manifest_bytes = fs::read(manifest_path)?;
    let parsed =
        engine_payload::parse_manifest(&manifest_bytes).map_err(|e| invalid(e.to_string()))?;
    let backend = normalized_manifest_backend(&parsed.backend)?;
    if !backend_supported_on_os(os, &backend) {
        return Err(invalid(format!(
            "The {backend} engine payload is not supported on {os}"
        )));
    }
    if parsed.schema_version == 2 {
        let manifest_os = parsed.os.as_deref().unwrap_or("");
        if manifest_os != os {
            return Err(invalid(format!(
                "This engine package is for {manifest_os}, not {os}"
            )));
        }
        let manifest_arch = parsed.arch.as_deref().unwrap_or("");
        if manifest_arch != arch {
            return Err(invalid(format!(
                "This engine package is for {manifest_arch}, not {arch}"
            )));
        }
        if parsed.protocol_version != Some(PROTOCOL_VERSION) {
            return Err(invalid(
                "This engine package uses an incompatible worker protocol version",
            ));
        }
        let imported_version = parsed
            .version
            .as_deref()
            .ok_or_else(|| invalid("Engine payload manifest is missing a version"))
            .and_then(|v| semver::Version::parse(v).map_err(|e| invalid(e.to_string())))?;
        let bundled_path = state
            .bundled_engine_path
            .lock()
            .ok()
            .and_then(|guard| guard.clone());
        ensure_import_not_older(
            &state.paths,
            config,
            bundled_path.as_deref(),
            &backend,
            os,
            arch,
            &imported_version,
            installed_version,
        )?;
    }
    let catalog_snapshot = lock(&state.engines.catalog)?.clone();
    let catalog_id = verify_imported_files(
        manifest_path,
        &manifest_bytes,
        &parsed,
        source_dir,
        &config.public_key,
        &catalog_snapshot,
        &backend,
    )?;
    if parsed.schema_version == 2 {
        if let (Some(cid), Some(mid)) = (&catalog_id, &parsed.engine_id) {
            if cid != mid {
                return Err(invalid(
                    "Engine manifest id does not match the signed catalog entry",
                ));
            }
        }
    }
    let os_key = if os == "macos" { "darwin" } else { os };
    let engine_id = parsed
        .engine_id
        .clone()
        .or_else(|| catalog_id.clone())
        .unwrap_or_else(|| format!("{os_key}-{arch}-{backend}-{}", sha256_hex(&manifest_bytes)));
    if !safe_id(&engine_id) {
        return Err(invalid("Derived engine identity is invalid"));
    }
    let version_str = parsed
        .version
        .clone()
        .or_else(|| {
            catalog_id.as_ref().and_then(|cid| {
                catalog_snapshot
                    .iter()
                    .find(|entry| entry.engine_id == *cid)
                    .map(|entry| entry.version.clone())
            })
        })
        .unwrap_or_default();

    let source_dir_owned = source_dir.to_path_buf();
    let manifest_bytes_owned = manifest_bytes;
    let staged = updates::stage_engine_with(
        &state.paths.next_root,
        &engine_id,
        |staged| {
            engine_payload::install_manifest_with_files(
                &manifest_bytes_owned,
                &source_dir_owned,
                staged,
            )
            .map_err(|error| invalid(error.to_string()))
        },
        handshake,
    )?;
    let meta = EngineMeta {
        id: engine_id,
        backend: backend.clone(),
        os: os.to_string(),
        arch: arch.to_string(),
        protocol_version: PROTOCOL_VERSION,
        handshake_passed: true,
        verified_install: true,
        version: version_str,
        files: staged.files.clone(),
    };
    commit_installed_engine(state, app, staged, &meta)?;
    if backend != "cpu" {
        state.choose_device.store(true, Ordering::SeqCst);
    }
    if let Some(app) = app {
        emit_state_changed(app);
    }
    Ok(meta)
}

#[tauri::command]
pub async fn import_engine(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    path: String,
) -> AppResult<()> {
    let granted = state.grants.check_engine_manifest(Path::new(&path))?;
    let state = state.inner().clone();
    let config = UpdateConfig::load();
    let version = app.package_info().version.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        import_engine_for(
            &state,
            Some(&app),
            &config,
            &granted,
            &version,
            std::env::consts::OS,
            std::env::consts::ARCH,
            |exe| {
                crate::headless_smoke::run_worker_handshake(
                    exe,
                    Duration::from_secs(60),
                    true,
                    &state.paths.work_root,
                )
                .map(|_| ())
                .map_err(AppError::Worker)
            },
        )
        .map(|_| ())
    })
    .await
    .map_err(|e| invalid(e.to_string()))?
}

#[cfg(test)]
mod tests;
