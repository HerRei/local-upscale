//! Explicit, signed updates. Public feeds stay disabled until the first beta
//! is built with an update identity and public verification key.
use crate::{
    error::{AppError, AppResult},
    state::{lock, AppState},
    types::PROTOCOL_VERSION,
    update_storage, worker,
};
use base64::Engine;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tauri::{AppHandle, Emitter, State};
use tauri_plugin_updater::UpdaterExt;

#[derive(Clone, Deserialize, Serialize)]
pub struct UpdateConfig {
    pub public_key: String,
    pub feed: String,
    pub backend: String,
    pub engine_id: String,
    pub kind: String,
}
impl UpdateConfig {
    fn load() -> Self {
        #[cfg(debug_assertions)]
        if let Ok(path) = std::env::var("LOCALSR_TEST_UPDATE_CONFIG") {
            if let Ok(bytes) = fs::read(path) {
                if let Ok(config) = serde_json::from_slice::<Self>(&bytes) {
                    if config.feed.starts_with("http://127.0.0.1:") {
                        return config;
                    }
                }
            }
        }
        Self {
            public_key: option_env!("LOCALSR_UPDATE_PUBLIC_KEY")
                .unwrap_or("")
                .into(),
            feed: option_env!("LOCALSR_UPDATE_FEED")
                .unwrap_or("https://herrei.github.io/localsr/updates")
                .into(),
            backend: option_env!("LOCALSR_UPDATE_BACKEND").unwrap_or("").into(),
            engine_id: option_env!("LOCALSR_ENGINE_ID").unwrap_or("").into(),
            kind: option_env!("LOCALSR_UPDATE_KIND")
                .unwrap_or("native")
                .into(),
        }
    }
    fn target(&self) -> String {
        let os = if cfg!(target_os = "macos") {
            "darwin"
        } else {
            std::env::consts::OS
        };
        format!(
            "{os}-{}-{}-{}",
            std::env::consts::ARCH,
            self.backend,
            self.kind
        )
    }
    fn enabled(&self) -> bool {
        !self.public_key.is_empty() && !self.backend.is_empty() && !self.engine_id.is_empty()
    }
}
#[derive(Clone, Deserialize, Serialize)]
pub struct DownloadFile {
    pub name: String,
    pub url: String,
    pub size: u64,
    pub sha256: String,
    pub signature: String,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct EngineUpdate {
    pub id: String,
    pub manifest: DownloadFile,
    pub parts: Vec<DownloadFile>,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct ReleaseContract {
    pub channel: String,
    pub backend: String,
    pub kind: String,
    pub protocol: u32,
    pub engine_id: String,
    pub size: u64,
    pub unpacked_size: u64,
    pub sha256: String,
    #[serde(default)]
    pub engine: Option<EngineUpdate>,
}
#[derive(Clone, Default, Serialize)]
pub struct UpdateStatus {
    pub configured: bool,
    pub managed_by_store: bool,
    pub channel: String,
    pub target: String,
    pub stage: String,
    pub version: String,
    pub notes: String,
    pub size: u64,
    pub downloaded: u64,
    pub message: String,
    pub settings_recovery: bool,
}
struct CheckedUpdate {
    update: tauri_plugin_updater::Update,
    contract: ReleaseContract,
    config: UpdateConfig,
}
#[derive(Default)]
pub struct UpdateControl {
    checked: std::sync::Mutex<Option<CheckedUpdate>>,
    pub busy: AtomicBool,
    pub installing: AtomicBool,
    pub cancel: AtomicBool,
    status: std::sync::Mutex<UpdateStatus>,
    staged: std::sync::Mutex<Option<PathBuf>>,
}
struct Busy<'a>(&'a AtomicBool);
impl Drop for Busy<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}
fn acquire(flag: &AtomicBool) -> AppResult<Busy<'_>> {
    flag.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .map_err(|_| invalid("An update operation is already running"))?;
    Ok(Busy(flag))
}
fn invalid(message: impl Into<String>) -> AppError {
    AppError::Validation(message.into())
}
fn key(value: &str) -> AppResult<minisign_verify::PublicKey> {
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(value)
        .map_err(|e| invalid(e.to_string()))?;
    minisign_verify::PublicKey::decode(
        std::str::from_utf8(&decoded).map_err(|e| invalid(e.to_string()))?,
    )
    .map_err(|e| invalid(e.to_string()))
}
fn signature(value: &str) -> AppResult<minisign_verify::Signature> {
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(value)
        .map_err(|e| invalid(e.to_string()))?;
    minisign_verify::Signature::decode(
        std::str::from_utf8(&decoded).map_err(|e| invalid(e.to_string()))?,
    )
    .map_err(|e| invalid(e.to_string()))
}
pub fn validate_contract(
    config: &UpdateConfig,
    channel: &str,
    version: &str,
    contract: &ReleaseContract,
) -> AppResult<()> {
    let version = semver::Version::parse(version).map_err(|e| invalid(e.to_string()))?;
    if !matches!(channel, "stable" | "beta")
        || contract.channel != channel
        || channel == "stable" && !version.pre.is_empty()
        || contract.backend != config.backend
        || contract.kind != config.kind
        || contract.protocol != PROTOCOL_VERSION
        || contract.engine_id.is_empty()
        || contract.size == 0
        || contract.size > 32 * 1024u64.pow(3)
        || contract.unpacked_size == 0
        || contract.sha256.len() != 64
    {
        return Err(invalid(
            "This release does not match the installed backend, channel or worker protocol",
        ));
    }
    if !safe_id(&contract.engine_id)
        || !contract.sha256.bytes().all(|b| b.is_ascii_hexdigit())
        || contract.unpacked_size > 128 * 1024u64.pow(3)
    {
        return Err(invalid("Invalid update size, digest or engine identity"));
    }
    if let Some(engine) = &contract.engine {
        if engine.parts.len() > 64
            || engine.manifest.size > 1024 * 1024
            || engine
                .parts
                .iter()
                .any(|p| p.size == 0 || p.size >= 2 * 1024u64.pow(3))
        {
            return Err(invalid("Invalid split engine package"));
        }
    }
    if contract.engine_id != config.engine_id
        && contract
            .engine
            .as_ref()
            .is_none_or(|engine| engine.id != contract.engine_id)
    {
        return Err(invalid("This release needs a different inference engine but supplies no compatible engine package"));
    }
    Ok(())
}
fn channel(state: &AppState) -> String {
    fs::read_to_string(state.paths.next_root.join("update-channel.json"))
        .ok()
        .filter(|s| matches!(s.as_str(), "stable" | "beta"))
        .unwrap_or("beta".into())
}
fn publish(state: &AppState, app: &AppHandle, status: UpdateStatus) {
    if let Ok(mut stored) = state.updates.status.lock() {
        *stored = status.clone();
    }
    let _ = app.emit("update-status", status);
}
#[tauri::command]
pub fn update_status(state: State<'_, Arc<AppState>>) -> AppResult<UpdateStatus> {
    if crate::distribution::managed_by_store() {
        return Ok(UpdateStatus {
            managed_by_store: true,
            stage: "store".into(),
            message:
                "App and inference engine updates are provided together through Microsoft Store."
                    .into(),
            settings_recovery: crate::settings::needs_recovery(&state.paths),
            ..Default::default()
        });
    }
    let config = UpdateConfig::load();
    let mut status = lock(&state.updates.status)?.clone();
    status.configured = config.enabled();
    status.settings_recovery = crate::settings::needs_recovery(&state.paths);
    if status.channel.is_empty() {
        status.channel = channel(&state);
        status.target = config.target();
        status.stage = "idle".into();
    }
    if !config.enabled() {
        status.message = "Updates are not published for this preview yet. This build needs a signed update feed and a matching backend package.".into();
    }
    Ok(status)
}

#[tauri::command]
pub fn open_store_updates(state: State<'_, Arc<AppState>>) -> AppResult<()> {
    if !crate::distribution::managed_by_store() {
        return Err(invalid(
            "This installation is not managed by Microsoft Store",
        ));
    }
    if !lock(&state.runtime)?.active_job_id.is_empty() {
        return Err(invalid(
            "Finish or cancel processing before opening Store updates",
        ));
    }
    open::that_detached("ms-windows-store://downloadsandupdates")?;
    Ok(())
}
#[tauri::command]
pub async fn check_update(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    channel: String,
) -> AppResult<UpdateStatus> {
    crate::distribution::require_direct_updates()?;
    let _busy = acquire(&state.updates.busy)?;
    if !matches!(channel.as_str(), "stable" | "beta") {
        return Err(invalid("Unknown update channel"));
    }
    let config = UpdateConfig::load();
    if !config.enabled() {
        return Err(invalid(
            "Signed updates have not been configured for this build",
        ));
    }
    if lock(&state.updates.staged)?.is_some() {
        return Err(invalid(
            "Discard the downloaded update before changing channels",
        ));
    }
    let endpoint = format!("{}/{}.json", config.feed.trim_end_matches('/'), channel);
    let mut status = UpdateStatus {
        configured: true,
        channel: channel.clone(),
        target: config.target(),
        stage: "checking".into(),
        ..Default::default()
    };
    publish(&state, &app, status.clone());
    let result = async {
        let builder = app
            .updater_builder()
            .pubkey(&config.public_key)
            .target(config.target());
        #[cfg(target_os = "windows")]
        let builder = builder.installer_arg("/LOCALSR_ENGINE_MANAGED=1");
        builder
            .endpoints(vec![endpoint
                .parse()
                .map_err(|e| invalid(format!("Invalid update URL: {e}")))?])
            .map_err(|e| invalid(e.to_string()))?
            .timeout(Duration::from_secs(30))
            .build()
            .map_err(|e| invalid(e.to_string()))?
            .check()
            .await
            .map_err(|e| invalid(e.to_string()))
    }
    .await;
    if result.is_ok() && !config.feed.starts_with("http://127.0.0.1:") {
        send_usage_ping(&state, &app, &config, &channel);
    }
    let update = match result {
        Ok(update) => update,
        Err(error) => {
            status.stage = "error".into();
            status.message = error.to_string();
            publish(&state, &app, status);
            return Err(error);
        }
    };
    *lock(&state.updates.checked)? = None;
    let validated = (|| -> AppResult<()> {
        if let Some(update) = update {
            let contract: ReleaseContract = serde_json::from_value(
                update.raw_json["platforms"][config.target()]["localsr"].clone(),
            )?;
            validate_contract(&config, &channel, &update.version, &contract)?;
            status.version = update.version.clone();
            status.notes = update.body.clone().unwrap_or_default();
            status.size = contract.size
                + contract
                    .engine
                    .as_ref()
                    .filter(|e| e.id != config.engine_id)
                    .map_or(0, |e| {
                        e.manifest.size + e.parts.iter().map(|p| p.size).sum::<u64>()
                    });
            status.stage = "available".into();
            status.message = "A compatible signed update is available.".into();
            *lock(&state.updates.checked)? = Some(CheckedUpdate {
                update,
                contract,
                config,
            });
        } else {
            status.stage = "current".into();
            status.message = "No newer compatible release is available in this channel.".into();
        }
        fs::write(state.paths.next_root.join("update-channel.json"), channel)?;
        Ok(())
    })();
    if let Err(error) = validated {
        status.stage = "error".into();
        status.message = error.to_string();
        publish(&state, &app, status);
        return Err(error);
    }
    publish(&state, &app, status.clone());
    Ok(status)
}

const USAGE_PING: &str = "https://macmini-ci.tail34a4e0.ts.net/ping/update-check";

/// Only the version, build target and channel: no install ID and no cookies.
fn usage_ping_url(base: &str, version: &str, target: &str, channel: &str) -> Option<reqwest::Url> {
    let valid = |value: &str| {
        (1..=80).contains(&value.len())
            && value
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._+-".contains(&b))
    };
    if ![version, target, channel].into_iter().all(valid) {
        return None;
    }
    let mut url = reqwest::Url::parse(base)
        .ok()
        .filter(|url| url.scheme() == "https")?;
    url.set_query(None);
    url.query_pairs_mut()
        .append_pair("v", version)
        .append_pair("t", target)
        .append_pair("c", channel);
    Some(url)
}

fn send_usage_ping(state: &AppState, app: &AppHandle, config: &UpdateConfig, channel: &str) {
    let enabled = lock(&state.settings).is_ok_and(|settings| settings.anonymous_update_count);
    if !enabled || crate::distribution::managed_by_store() {
        return;
    }
    let Some(url) = usage_ping_url(
        option_env!("LOCALSR_USAGE_PING").unwrap_or(USAGE_PING),
        &app.package_info().version.to_string(),
        &config.target(),
        channel,
    ) else {
        return;
    };
    tauri::async_runtime::spawn(async move {
        let Ok(client) = reqwest::Client::builder()
            .https_only(true)
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(5))
            .user_agent(concat!("LocalSR/", env!("CARGO_PKG_VERSION")))
            .build()
        else {
            return;
        };
        let _ = client.get(url).send().await;
    });
}

pub fn verify_file(path: &Path, file: &DownloadFile, public_key: &str) -> AppResult<()> {
    let public_key = key(public_key)?;
    let sig = signature(&file.signature)?;
    let mut verifier = public_key
        .verify_stream(&sig)
        .map_err(|e| invalid(e.to_string()))?;
    let mut reader = fs::File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0; 1024 * 1024];
    let mut size = 0;
    loop {
        let count = reader.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        size += count as u64;
        digest.update(&buffer[..count]);
        verifier.update(&buffer[..count]);
    }
    if size != file.size
        || digest
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
            != file.sha256
    {
        return Err(invalid("Update size or checksum verification failed"));
    }
    verifier
        .finalize()
        .map_err(|e| invalid(format!("Update signature verification failed: {e}")))
}
async fn download_file(
    file: &DownloadFile,
    directory: &Path,
    config: &UpdateConfig,
    cancel: &AtomicBool,
    progress: &mut impl FnMut(u64),
) -> AppResult<()> {
    if file.name.is_empty()
        || Path::new(&file.name).components().count() != 1
        || file.name.contains(['/', '\\', ':'])
        || file.name == ".."
        || file.name == "."
    {
        return Err(invalid("Unsafe update filename"));
    }
    let url: reqwest::Url = file
        .url
        .parse()
        .map_err(|e| invalid(format!("Invalid download URL: {e}")))?;
    let local = cfg!(debug_assertions)
        && config.feed.starts_with("http://127.0.0.1:")
        && url.host_str() == Some("127.0.0.1");
    if url.scheme() != "https" && !local {
        return Err(invalid("Update downloads require HTTPS"));
    }
    let path = directory.join(&file.name);
    if path.is_file() && verify_file(&path, file, &config.public_key).is_ok() {
        progress(file.size);
        return Ok(());
    }
    let temporary = directory.join(format!("{}.partial", file.name));
    let result = async {
        let client = reqwest::Client::builder()
            .https_only(!local)
            .connect_timeout(Duration::from_secs(15))
            .timeout(Duration::from_secs(3600))
            .build()?;
        let request = client.get(url).send();
        tokio::pin!(request);
        let response = loop {
            if cancel.load(Ordering::SeqCst) {
                return Err(invalid("Update download cancelled"));
            }
            match tokio::time::timeout(Duration::from_millis(200), &mut request).await {
                Err(_) => continue,
                Ok(response) => break response?.error_for_status()?,
            }
        };
        let mut stream = response.bytes_stream();
        let mut output = fs::File::create(&temporary)?;
        let mut size = 0;
        loop {
            if cancel.load(Ordering::SeqCst) {
                return Err(invalid("Update download cancelled"));
            }
            let next = tokio::time::timeout(Duration::from_millis(200), stream.next()).await;
            let chunk = match next {
                Err(_) => continue,
                Ok(None) => break,
                Ok(Some(chunk)) => chunk?,
            };
            size += chunk.len() as u64;
            if size > file.size {
                return Err(invalid("Update exceeded its declared download size"));
            }
            output.write_all(&chunk)?;
            progress(chunk.len() as u64);
        }
        output.sync_all()?;
        drop(output);
        if cancel.load(Ordering::SeqCst) {
            return Err(invalid("Update download cancelled"));
        }
        verify_file(&temporary, file, &config.public_key)?;
        fs::rename(&temporary, &path)?;
        Ok(())
    }
    .await;
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}
#[tauri::command]
pub async fn download_update(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    crate::distribution::require_direct_updates()?;
    let _busy = acquire(&state.updates.busy)?;
    state.updates.cancel.store(false, Ordering::SeqCst);
    let (file, config, contract) = {
        let checked = lock(&state.updates.checked)?;
        let checked = checked
            .as_ref()
            .ok_or_else(|| invalid("Check for updates first"))?;
        (
            DownloadFile {
                name: "application.update".into(),
                url: checked.update.download_url.to_string(),
                size: checked.contract.size,
                sha256: checked.contract.sha256.clone(),
                signature: checked.update.signature.clone(),
            },
            checked.config.clone(),
            checked.contract.clone(),
        )
    };
    let directory = state.paths.next_root.join("updates");
    fs::create_dir_all(&directory)?;
    let mut status = lock(&state.updates.status)?.clone();
    let needed = status
        .size
        .checked_add(contract.unpacked_size)
        .and_then(|s| s.checked_add(256 * 1024 * 1024))
        .ok_or_else(|| invalid("Update size overflow"))?;
    require_space(fs2::available_space(&directory)?, needed)?;
    status.stage = "downloading".into();
    status.downloaded = 0;
    publish(&state, &app, status.clone());
    let result = async {
        let mut progress = |count| {
            status.downloaded += count;
            publish(&state, &app, status.clone());
        };
        download_file(
            &file,
            &directory,
            &config,
            &state.updates.cancel,
            &mut progress,
        )
        .await?;
        if let Some(engine) = contract.engine.filter(|e| e.id != config.engine_id) {
            download_file(
                &engine.manifest,
                &directory,
                &config,
                &state.updates.cancel,
                &mut progress,
            )
            .await?;
            for part in &engine.parts {
                download_file(
                    part,
                    &directory,
                    &config,
                    &state.updates.cancel,
                    &mut progress,
                )
                .await?;
            }
        }
        Ok::<_, AppError>(())
    }
    .await;
    status.stage = if result.is_ok() {
        "downloaded"
    } else {
        "available"
    }
    .into();
    status.message = if let Err(error) = &result {
        error.to_string()
    } else {
        "Signature verified. Install when your queue has finished.".into()
    };
    if result.is_ok() {
        *lock(&state.updates.staged)? = Some(directory);
    }
    publish(&state, &app, status);
    result
}
#[tauri::command]
pub fn cancel_update(state: State<'_, Arc<AppState>>) {
    state.updates.cancel.store(true, Ordering::SeqCst);
}
#[tauri::command]
pub fn discard_update(state: State<'_, Arc<AppState>>) -> AppResult<()> {
    crate::distribution::require_direct_updates()?;
    let _busy = acquire(&state.updates.busy)?;
    let directory = state.paths.next_root.join("updates");
    if directory.exists() {
        fs::remove_dir_all(directory)?;
    }
    *lock(&state.updates.staged)? = None;
    *lock(&state.updates.checked)? = None;
    *lock(&state.updates.status)? = UpdateStatus::default();
    Ok(())
}
#[tauri::command]
pub fn recover_update_settings(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    if !lock(&state.runtime)?.active_job_id.is_empty() {
        return Err(invalid("Finish or cancel processing before recovery"));
    }
    crate::settings::recover_defaults(&state.paths)?;
    let (settings, recipes) = crate::settings::load(&state.paths);
    *lock(&state.settings)? = settings;
    *lock(&state.recipes)? = recipes;
    state.persist_settings()?;
    crate::state::emit_state_changed(&app);
    Ok(())
}

pub fn ensure_not_installing(state: &AppState) -> AppResult<()> {
    if state.updates.installing.load(Ordering::SeqCst) {
        return Err(invalid("An update is being installed"));
    }
    Ok(())
}
fn require_space(available: u64, needed: u64) -> AppResult<()> {
    if available < needed {
        return Err(invalid(
            "Not enough free disk space to download and install this update",
        ));
    }
    Ok(())
}
fn safe_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}
pub fn active_engine(paths: &crate::paths::AppPaths) -> Option<PathBuf> {
    active_engine_for_distribution(paths, crate::distribution::managed_by_store())
}
fn active_engine_for_distribution(
    paths: &crate::paths::AppPaths,
    managed_by_store: bool,
) -> Option<PathBuf> {
    if managed_by_store {
        return None;
    }
    let id = fs::read_to_string(paths.next_root.join("active-engine.txt")).ok()?;
    if !safe_id(&id) {
        return None;
    }
    let executable = paths
        .next_root
        .join("engines")
        .join(id)
        .join("engine")
        .join(if cfg!(windows) {
            "localsr-worker.exe"
        } else {
            "localsr-worker"
        });
    executable.is_file().then_some(executable)
}
fn copy_tree(source: &Path, destination: &Path) -> AppResult<()> {
    fs::create_dir_all(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let dest = destination.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_tree(&entry.path(), &dest)?;
        } else if fs::hard_link(entry.path(), &dest).is_err() {
            fs::copy(entry.path(), &dest)?;
        }
    }
    Ok(())
}
#[tauri::command]
pub async fn install_update(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    crate::distribution::require_direct_updates()?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let result = install_checked(&state, &app);
        if let Err(error) = &result {
            let mut status = lock(&state.updates.status)?.clone();
            status.stage = if lock(&state.updates.staged)?.is_some() {
                "downloaded"
            } else {
                "error"
            }
            .into();
            status.message = error.to_string();
            publish(&state, &app, status);
        }
        result
    })
    .await
    .map_err(|e| invalid(e.to_string()))?
}
fn install_checked(state: &Arc<AppState>, app: &AppHandle) -> AppResult<()> {
    let _busy = acquire(&state.updates.busy)?;
    let _installing = acquire(&state.updates.installing)?;
    // Scheduling and new settings/media mutations are blocked for the complete
    // transaction. Cancellation must have really finished, not merely been sent.
    let _scheduler = lock(&state.scheduler)?;
    if !lock(&state.runtime)?.active_job_id.is_empty()
        || lock(&state.database)?.has_inflight_jobs()?
        || !lock(&state.downloads)?.is_empty()
    {
        return Err(invalid(
            "Finish processing, cancellation and model downloads before installing",
        ));
    }
    let directory = lock(&state.updates.staged)?
        .clone()
        .ok_or_else(|| invalid("Download and verify the update first"))?;
    let checked = lock(&state.updates.checked)?;
    let checked = checked
        .as_ref()
        .ok_or_else(|| invalid("Check for updates first"))?;
    let config = &checked.config;
    let contract = &checked.contract;
    validate_contract(config, &channel(state), &checked.update.version, contract)?;
    let file = DownloadFile {
        name: "application.update".into(),
        url: checked.update.download_url.to_string(),
        size: contract.size,
        sha256: contract.sha256.clone(),
        signature: checked.update.signature.clone(),
    };
    verify_file(&directory.join(&file.name), &file, &config.public_key)?;
    require_space(
        fs2::available_space(&directory)?,
        contract.unpacked_size + 256 * 1024 * 1024,
    )?;
    state.persist_settings()?;
    let backup = update_storage::backup_profile(&state.paths, "before-install")?;
    let mut status = lock(&state.updates.status)?.clone();
    status.stage = "installing".into();
    status.message = "Backing up data and preparing the verified update…".into();
    publish(state, app, status.clone());
    let engine_root = state
        .paths
        .next_root
        .join("engines")
        .join(&contract.engine_id);
    if !safe_id(&contract.engine_id) {
        return Err(invalid("Invalid engine version"));
    }
    fs::create_dir_all(&engine_root)?;
    let engine_dir = engine_root.join("engine");
    if !engine_dir.exists() {
        if let Some(engine) = contract
            .engine
            .as_ref()
            .filter(|e| e.id != config.engine_id)
        {
            verify_file(
                &directory.join(&engine.manifest.name),
                &engine.manifest,
                &config.public_key,
            )?;
            for part in &engine.parts {
                verify_file(&directory.join(&part.name), part, &config.public_key)?;
            }
            crate::engine_payload::install(
                &directory.join(&engine.manifest.name),
                &directory,
                &engine_dir,
            )?;
        } else {
            let old_worker = worker::installed_worker_path(state, app)?;
            let source = old_worker
                .parent()
                .ok_or_else(|| invalid("Installed engine path has no parent"))?;
            let staging = engine_root.join("engine.adopting");
            if staging.exists() {
                fs::remove_dir_all(&staging)?;
            }
            if let Err(error) = copy_tree(source, &staging) {
                let _ = fs::remove_dir_all(&staging);
                return Err(error);
            }
            fs::rename(staging, &engine_dir)?;
        }
    }
    let engine_exe = engine_dir.join(if cfg!(windows) {
        "localsr-worker.exe"
    } else {
        "localsr-worker"
    });
    crate::headless_smoke::run_worker_handshake(&engine_exe, Duration::from_secs(60))
        .map_err(AppError::Worker)?;
    let active_file = state.paths.next_root.join("active-engine.txt");
    let previous_engine = fs::read(&active_file).ok();
    let result = (|| -> AppResult<()> {
        worker::shutdown(state);
        let start = std::time::Instant::now();
        while state.worker.pid.load(Ordering::SeqCst) != 0 {
            if start.elapsed() > Duration::from_secs(10) {
                return Err(invalid(
                    "The inference worker did not stop; update was not installed",
                ));
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        lock(&state.database)?.close_for_update()?;
        fs::write(&active_file, &contract.engine_id)?;
        if config.kind == "portable" && cfg!(target_os = "linux") {
            let unpacked = directory.join("unpacked");
            if unpacked.exists() {
                fs::remove_dir_all(&unpacked)?;
            }
            update_storage::extract_portable(
                &directory.join(&file.name),
                &unpacked,
                contract.unpacked_size,
            )
            .and_then(|_| {
                let current = std::env::current_exe()?;
                let parent = current
                    .parent()
                    .ok_or_else(|| invalid("No application directory"))?;
                let staged = parent.join(format!(".localsr-update-{}", uuid::Uuid::new_v4()));
                fs::copy(unpacked.join("localsr-next"), &staged)?;
                let plan = update_storage::PortablePlan {
                    previous: parent.join(".localsr-before-update"),
                    current,
                    staged,
                    report: backup.join("install-report.json"),
                };
                update_storage::install_portable(&plan, |path| {
                    update_storage::smoke(path, Duration::from_secs(60))
                })
            })
        } else {
            checked
                .update
                .install(fs::read(directory.join(&file.name))?)
                .map_err(|e| invalid(e.to_string()))
        }
    })();
    if let Err(error) = result {
        match previous_engine {
            Some(bytes) => fs::write(&active_file, bytes)?,
            None => {
                let _ = fs::remove_file(&active_file);
            }
        }
        *lock(&state.database)? = crate::database::Database::open(&state.paths.database)?;
        state.worker.shutting_down.store(false, Ordering::SeqCst);
        worker::start_worker(state.clone(), app.clone())?;
        status.stage = "downloaded".into();
        status.message = format!("Update failed; previous version restored. {error}");
        publish(state, app, status);
        return Err(error);
    }
    let _ = fs::write(
        backup.join("installed-version.txt"),
        &checked.update.version,
    );
    // Keep profile backups; discard only downloaded/staged update material.
    let _ = fs::remove_dir_all(&directory);
    restart_application(app)
}

fn restart_application(app: &AppHandle) -> AppResult<()> {
    #[cfg(target_os = "linux")]
    if let Ok(cgroup) = fs::read_to_string("/proc/self/cgroup") {
        if let Some(unit) = cgroup
            .lines()
            .filter_map(|line| line.rsplit('/').next())
            .find(|unit| {
                unit.starts_with("localsr-")
                    && unit.ends_with(".service")
                    && !unit.contains([' ', '\n'])
            })
        {
            // A normal child relaunch is killed when a systemd service's main
            // process exits. Ask the user manager to restart this exact unit.
            let status = std::process::Command::new("systemctl")
                .args(["--user", "--no-block", "restart", unit])
                .status()?;
            if status.success() {
                app.exit(0);
                return Ok(());
            }
            return Err(invalid(
                "Update installed. The desktop service could not restart; open LocalSR again.",
            ));
        }
    }
    app.restart();
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn store_install_ignores_a_previous_direct_install_engine() {
        let root = tempfile::tempdir().unwrap();
        let paths = crate::paths::AppPaths::under(root.path());
        let engine = paths
            .next_root
            .join("engines/previous/engine")
            .join(if cfg!(windows) {
                "localsr-worker.exe"
            } else {
                "localsr-worker"
            });
        fs::create_dir_all(engine.parent().unwrap()).unwrap();
        fs::write(&engine, b"previous engine").unwrap();
        fs::write(paths.next_root.join("active-engine.txt"), b"previous").unwrap();
        assert_eq!(
            active_engine_for_distribution(&paths, false),
            Some(engine.clone())
        );
        assert_eq!(active_engine_for_distribution(&paths, true), None);
        assert_eq!(fs::read(engine).unwrap(), b"previous engine");
    }

    #[test]
    fn usage_ping_carries_only_valid_version_target_and_channel() {
        let url = super::usage_ping_url(
            "https://stats.example/ping/update-check?old=1",
            "0.1.1-beta",
            "darwin-aarch64-mps-native",
            "beta",
        )
        .unwrap();
        assert_eq!(
            url.as_str(),
            "https://stats.example/ping/update-check?v=0.1.1-beta&t=darwin-aarch64-mps-native&c=beta"
        );
        assert!(super::usage_ping_url("http://stats.example/p", "1.0", "t", "beta").is_none());
        assert!(super::usage_ping_url(super::USAGE_PING, "1.0 x", "t", "beta").is_none());
        assert!(super::usage_ping_url(super::USAGE_PING, "1.0", "", "beta").is_none());
        assert!(super::usage_ping_url(super::USAGE_PING, &"1".repeat(81), "t", "beta").is_none());
    }

    fn fixture() -> (Vec<u8>, DownloadFile, UpdateConfig) {
        let value: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/update-signature.json")).unwrap();
        let data = value["data"].as_str().unwrap().as_bytes().to_vec();
        let file = DownloadFile {
            name: "app.update".into(),
            url: "https://example.test/update".into(),
            size: data.len() as u64,
            sha256: value["sha256"].as_str().unwrap().into(),
            signature: value["signature"].as_str().unwrap().into(),
        };
        let config = UpdateConfig {
            public_key: value["public_key"].as_str().unwrap().into(),
            feed: "http://127.0.0.1:1".into(),
            backend: "rocm".into(),
            engine_id: "abc".into(),
            kind: "portable".into(),
        };
        (data, file, config)
    }
    #[test]
    fn insufficient_space_stops_before_installation() {
        assert!(require_space(1024, 1025).is_err());
        assert!(require_space(1025, 1025).is_ok());
    }
    #[test]
    fn signed_artifact_rejects_corruption_even_with_a_recomputed_hash() {
        let (mut bytes, mut file, config) = fixture();
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join(&file.name);
        fs::write(&path, &bytes).unwrap();
        verify_file(&path, &file, &config.public_key).unwrap();
        bytes[0] ^= 1;
        fs::write(&path, &bytes).unwrap();
        assert!(verify_file(&path, &file, &config.public_key).is_err());
        file.sha256 = Sha256::digest(&bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert!(verify_file(&path, &file, &config.public_key)
            .unwrap_err()
            .to_string()
            .contains("signature"));
        fs::write(&path, &bytes[..2]).unwrap();
        assert!(verify_file(&path, &file, &config.public_key).is_err());
    }
    #[test]
    fn release_contract_separates_channels_backends_and_engine_versions() {
        let (_, file, config) = fixture();
        let mut contract = ReleaseContract {
            channel: "beta".into(),
            backend: "rocm".into(),
            kind: "portable".into(),
            protocol: PROTOCOL_VERSION,
            engine_id: "abc".into(),
            size: file.size,
            unpacked_size: 1024,
            sha256: file.sha256,
            engine: None,
        };
        validate_contract(&config, "beta", "0.0.13-beta.1", &contract).unwrap();
        assert!(validate_contract(&config, "stable", "0.0.13-beta.1", &contract).is_err());
        contract.backend = "cuda".into();
        assert!(validate_contract(&config, "beta", "0.0.13-beta.1", &contract).is_err());
        contract.backend = "rocm".into();
        contract.protocol += 1;
        assert!(validate_contract(&config, "beta", "0.0.13-beta.1", &contract).is_err());
        contract.protocol = PROTOCOL_VERSION;
        contract.engine_id = "different".into();
        assert!(validate_contract(&config, "beta", "0.0.13-beta.1", &contract).is_err());
    }
    #[test]
    fn interrupted_and_cancelled_downloads_never_become_installable() {
        for cancel_requested in [false, true] {
            let (bytes, mut file, config) = fixture();
            let root = tempfile::tempdir().unwrap();
            let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            file.url = format!("http://{}/app", listener.local_addr().unwrap());
            let length = bytes.len();
            listener.set_nonblocking(true).unwrap();
            let server = std::thread::spawn(move || {
                let start = std::time::Instant::now();
                let mut socket = loop {
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            if start.elapsed() > Duration::from_secs(2) {
                                return;
                            }
                            std::thread::sleep(Duration::from_millis(10));
                        }
                        Err(error) => panic!("{error}"),
                    }
                };
                let mut request = [0; 2048];
                let _ = socket.read(&mut request);
                write!(
                    socket,
                    "HTTP/1.1 200 OK\r\nContent-Length: {length}\r\n\r\n"
                )
                .unwrap();
                let _ = socket.write_all(&bytes[..2]);
            });
            let cancel = AtomicBool::new(cancel_requested);
            let result = tauri::async_runtime::block_on(download_file(
                &file,
                root.path(),
                &config,
                &cancel,
                &mut |_| {},
            ));
            assert!(result.is_err());
            assert!(!root.path().join(&file.name).exists());
            assert!(!root.path().join(format!("{}.partial", file.name)).exists());
            server.join().unwrap();
        }
    }

    /// Run explicitly against the retained, production-signed CPU AppImage.
    /// Uses the app's actual downloader/verifier without replacing an installed app.
    #[test]
    #[ignore = "requires LOCALSR_SIGNED_ACCEPTANCE_ARTIFACT and its production signature"]
    fn production_candidate_download_and_tamper_rejection() {
        let artifact =
            PathBuf::from(std::env::var_os("LOCALSR_SIGNED_ACCEPTANCE_ARTIFACT").unwrap());
        let public_key = include_str!("../../../packaging/updates/production.pub").trim();
        let signature = fs::read_to_string(format!("{}.sig", artifact.display())).unwrap();
        let size = fs::metadata(&artifact).unwrap().len();
        let mut hasher = Sha256::new();
        let mut input = fs::File::open(&artifact).unwrap();
        let mut buffer = vec![0; 1024 * 1024];
        loop {
            let count = input.read(&mut buffer).unwrap();
            if count == 0 {
                break;
            }
            hasher.update(&buffer[..count]);
        }
        let digest: String = hasher
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        for interrupted in [false, true] {
            let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            let mut file = DownloadFile {
                name: "candidate.AppImage".into(),
                url: format!("http://{}/candidate", listener.local_addr().unwrap()),
                size,
                sha256: digest.clone(),
                signature: signature.trim().into(),
            };
            let config = UpdateConfig {
                public_key: public_key.into(),
                feed: file.url.clone(),
                backend: "cpu".into(),
                engine_id: "linux-x86_64-cpu-beta1-20260913".into(),
                kind: "native".into(),
            };
            let source = artifact.clone();
            let server = std::thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(30)))
                    .unwrap();
                socket
                    .set_write_timeout(Some(Duration::from_secs(30)))
                    .unwrap();
                let mut request = [0; 4096];
                let mut received = 0;
                while !request[..received]
                    .windows(4)
                    .any(|bytes| bytes == b"\r\n\r\n")
                {
                    assert!(
                        received < request.len(),
                        "HTTP request headers exceed test limit"
                    );
                    let count = socket.read(&mut request[received..]).unwrap();
                    assert!(
                        count > 0,
                        "HTTP client closed before sending complete headers"
                    );
                    received += count;
                }
                write!(
                    socket,
                    "HTTP/1.1 200 OK\r\nContent-Length: {size}\r\nConnection: close\r\n\r\n"
                )
                .unwrap();
                let input = fs::File::open(source).unwrap();
                if interrupted {
                    std::io::copy(&mut input.take(1024), &mut socket).unwrap();
                } else {
                    std::io::copy(&mut std::io::BufReader::new(input), &mut socket).unwrap();
                }
            });
            let root = tempfile::tempdir().unwrap();
            let cancel = AtomicBool::new(false);
            let mut downloaded = 0;
            let result = tauri::async_runtime::block_on(download_file(
                &file,
                root.path(),
                &config,
                &cancel,
                &mut |bytes| downloaded += bytes,
            ));
            server.join().unwrap();
            if interrupted {
                assert!(result.is_err());
                assert!(!root.path().join(&file.name).exists());
                assert!(!root.path().join(format!("{}.partial", file.name)).exists());
            } else {
                result.unwrap();
                assert_eq!(downloaded, size);
                let path = root.path().join(&file.name);
                verify_file(&path, &file, public_key).unwrap();
                // Recompute the digest of substituted data; the production
                // signature must still prevent treating it as an update.
                let replacement = b"substituted update";
                fs::write(&path, replacement).unwrap();
                file.size = replacement.len() as u64;
                file.sha256 = Sha256::digest(replacement)
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect();
                assert!(verify_file(&path, &file, public_key)
                    .unwrap_err()
                    .to_string()
                    .contains("signature"));
            }
        }
    }
}
