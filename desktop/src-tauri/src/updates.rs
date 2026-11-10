//! Explicit, signed updates for every direct edition. A build checks a feed only when
//! it was compiled with an update identity: the public verification key, its backend,
//! its package kind and its engine id (scripts/release_targets.py --update-env).
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
        platform_key(
            std::env::consts::OS,
            std::env::consts::ARCH,
            &self.backend,
            &self.kind,
        )
    }
    fn enabled(&self) -> bool {
        !self.public_key.is_empty() && !self.backend.is_empty() && !self.engine_id.is_empty()
    }
}

/// The feed's platform key, `<os>-<arch>-<backend>-<kind>`, with "darwin" for macOS:
/// `windows-x86_64-cuda-native`, `linux-x86_64-rocm-native`, `darwin-aarch64-mps-native`.
/// scripts/release_targets.py writes the feed entries under the same keys.
fn platform_key(os: &str, arch: &str, backend: &str, kind: &str) -> String {
    let os = if os == "macos" { "darwin" } else { os };
    format!("{os}-{arch}-{backend}-{kind}")
}

/// Whether this build checks a signed update feed itself: a direct build with an updater
/// key, not a Store build. Only then do the launch check and the count exist.
pub fn checks_for_updates() -> bool {
    checks_feed(
        &UpdateConfig::load(),
        crate::distribution::managed_by_store(),
    )
}

fn checks_feed(config: &UpdateConfig, managed_by_store: bool) -> bool {
    config.enabled() && !managed_by_store
}

/// The compiled update identity, for the installed-package smoke report: release CI
/// checks it, so a package built without its updater key cannot be published.
pub fn identity_report() -> serde_json::Value {
    identity(
        &UpdateConfig::load(),
        crate::distribution::managed_by_store(),
    )
}

fn identity(config: &UpdateConfig, managed_by_store: bool) -> serde_json::Value {
    serde_json::json!({
        "enabled": checks_feed(config, managed_by_store),
        "target": config.target(),
        "backend": config.backend,
        "kind": config.kind,
        "engine_id": config.engine_id,
    })
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
    /// A package above GitHub's 2 GiB file limit (the CUDA and ROCm AppImages) comes as
    /// signed parts that join to the signed package; empty for a single file.
    #[serde(default)]
    pub parts: Vec<DownloadFile>,
    /// The release statement, format 2 (base64 JSON), and its signature. Only these, not
    /// the feed, vouch for the version, channel, edition, protocol, engine id, sizes,
    /// digests and the names of every file. A feed also carries `statement` (format 1)
    /// for clients before 0.3.8; this client ignores it and refuses an entry without
    /// format 2 (docs/updates.md).
    #[serde(default)]
    pub statement_v2: String,
    #[serde(default)]
    pub statement_v2_signature: String,
    #[serde(default)]
    pub engine: Option<EngineUpdate>,
}

/// Name, size and digest of a file, as the signed statement lists it.
#[derive(Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct StatedFile {
    name: String,
    size: u64,
    sha256: String,
}
impl From<&DownloadFile> for StatedFile {
    fn from(file: &DownloadFile) -> Self {
        Self {
            name: file.name.clone(),
            size: file.size,
            sha256: file.sha256.clone(),
        }
    }
}

/// The signed release statement format 2 (scripts/beta_update_signing.py,
/// release_statement_v2). Every field is required, except that `engine_manifest` may be
/// left out where it would be null (an entry without an engine package); a statement
/// lacking any other field is not format 2 and the client refuses the release.
#[derive(Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct ReleaseStatement {
    schema_version: u32,
    version: String,
    channel: String,
    target: String,
    backend: String,
    kind: String,
    protocol: u32,
    engine_id: String,
    size: u64,
    unpacked_size: u64,
    sha256: String,
    parts: Vec<StatedFile>,
    engine_manifest: Option<StatedFile>,
    engine_parts: Vec<StatedFile>,
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
    if !contract.parts.is_empty() {
        let mut names = std::collections::HashSet::new();
        let total = contract
            .parts
            .iter()
            .try_fold(0u64, |total, part| total.checked_add(part.size));
        if !(2..=64).contains(&contract.parts.len())
            || total != Some(contract.size)
            || contract.parts.iter().any(|part| {
                part.size == 0
                    || part.size >= 2 * 1024u64.pow(3)
                    || part.name == APPLICATION_FILE
                    || !safe_file_name(&part.name)
                    || !names.insert(part.name.as_str())
            })
        {
            return Err(invalid("Invalid split application package"));
        }
    }
    if let Some(engine) = &contract.engine {
        // Every downloaded file lands in one folder: the names must not collide with the
        // application package, its parts or a partial download.
        let mut names: std::collections::HashSet<&str> = contract
            .parts
            .iter()
            .map(|part| part.name.as_str())
            .chain([APPLICATION_FILE])
            .collect();
        if engine.parts.len() > 64
            || engine.manifest.size == 0
            || engine.manifest.size > 1024 * 1024
            || std::iter::once(&engine.manifest)
                .chain(&engine.parts)
                .any(|file| {
                    !safe_file_name(&file.name)
                        || file.name.ends_with(".partial")
                        || !names.insert(file.name.as_str())
                })
            || engine
                .parts
                .iter()
                .any(|p| p.size == 0 || p.size >= 2 * 1024u64.pow(3))
        {
            return Err(invalid("Invalid split engine package"));
        }
    }
    if contract
        .engine
        .as_ref()
        .is_some_and(|engine| engine.id != contract.engine_id)
    {
        return Err(invalid(
            "The engine package does not carry the release's engine id",
        ));
    }
    if contract.engine_id != config.engine_id && contract.engine.is_none() {
        return Err(invalid("This release needs a different inference engine but supplies no compatible engine package"));
    }
    Ok(())
}
/// Trust the entry only as far as its signed release statement agrees with it and with
/// this build, and only for a version newer than the installed one.
fn verify_statement(
    config: &UpdateConfig,
    target: &str,
    channel: &str,
    version: &str,
    installed: &str,
    contract: &ReleaseContract,
) -> AppResult<()> {
    let unsigned = || invalid("This release has no valid signed release statement");
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(&contract.statement_v2)
        .map_err(|_| unsigned())?;
    key(&config.public_key)?
        .verify(&bytes, &signature(&contract.statement_v2_signature)?, false)
        .map_err(|_| unsigned())?;
    let statement: ReleaseStatement = serde_json::from_slice(&bytes).map_err(|_| unsigned())?;
    let expected = ReleaseStatement {
        schema_version: 2,
        version: version.into(),
        channel: channel.into(),
        target: target.into(),
        backend: config.backend.clone(),
        kind: config.kind.clone(),
        protocol: PROTOCOL_VERSION,
        engine_id: contract.engine_id.clone(),
        size: contract.size,
        unpacked_size: contract.unpacked_size,
        sha256: contract.sha256.clone(),
        parts: contract.parts.iter().map(StatedFile::from).collect(),
        engine_manifest: contract.engine.as_ref().map(|e| (&e.manifest).into()),
        engine_parts: contract
            .engine
            .as_ref()
            .map_or_else(Vec::new, |e| e.parts.iter().map(StatedFile::from).collect()),
    };
    if statement != expected {
        return Err(invalid(
            "The signed release statement does not match this update or this build",
        ));
    }
    let newer = semver::Version::parse(version).map_err(|e| invalid(e.to_string()))?
        > semver::Version::parse(installed).map_err(|e| invalid(e.to_string()))?;
    if !newer {
        return Err(invalid(
            "This release is not newer than the installed version",
        ));
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
            verify_statement(
                &config,
                &config.target(),
                &channel,
                &update.version,
                &app.package_info().version.to_string(),
                &contract,
            )?;
            status.version = update.version.clone();
            status.notes = update.body.clone().unwrap_or_default();
            status.size = contract.size
                + contract
                    .engine
                    .as_ref()
                    .filter(|e| engine_needs_package(&state.paths.next_root, &config, &e.id))
                    .map_or(0, |e| {
                        e.manifest.size + e.parts.iter().map(|p| p.size).sum::<u64>()
                    });
            status.stage = "available".into();
            status.message = "A compatible signed update is available.".into();
            // The install checks the entry against this file: write it first, so a
            // failed write never leaves an update that no longer matches its channel.
            update_storage::write_atomic(
                &state.paths.next_root.join("update-channel.json"),
                channel.as_bytes(),
            )?;
            *lock(&state.updates.checked)? = Some(CheckedUpdate {
                update,
                contract,
                config,
            });
        } else {
            status.stage = "current".into();
            status.message = "No newer compatible release is available in this channel.".into();
            update_storage::write_atomic(
                &state.paths.next_root.join("update-channel.json"),
                channel.as_bytes(),
            )?;
        }
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
/// The downloaded application package, whether it came as one file or as parts.
const APPLICATION_FILE: &str = "application.update";

fn safe_file_name(name: &str) -> bool {
    !name.is_empty()
        && Path::new(name).components().count() == 1
        && !name.contains(['/', '\\', ':'])
        && name != ".."
        && name != "."
}
fn verify_bytes(bytes: &[u8], file: &DownloadFile, public_key: &str) -> AppResult<()> {
    let digest: String = Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    if bytes.len() as u64 != file.size || digest != file.sha256 {
        return Err(invalid("Update size or checksum verification failed"));
    }
    key(public_key)?
        .verify(bytes, &signature(&file.signature)?, false)
        .map_err(|e| invalid(format!("Update signature verification failed: {e}")))
}

/// Read a downloaded file and verify those bytes: they are what gets installed.
fn read_verified(path: &Path, file: &DownloadFile, public_key: &str) -> AppResult<Vec<u8>> {
    if fs::metadata(path)?.len() != file.size {
        return Err(invalid("Update size or checksum verification failed"));
    }
    let bytes = fs::read(path)?;
    verify_bytes(&bytes, file, public_key)?;
    Ok(bytes)
}

/// Copy a downloaded file into a new file only this process created, then verify the
/// copy: it is what gets installed, so a download replaced after its check never is.
fn stage_verified(
    source: &Path,
    staged: &Path,
    file: &DownloadFile,
    public_key: &str,
) -> AppResult<()> {
    update_storage::copy_new(source, staged)?;
    if let Err(error) = verify_file(staged, file, public_key) {
        let _ = fs::remove_file(staged);
        return Err(error);
    }
    Ok(())
}

/// Install an engine package from the manifest bytes that were verified; the engine
/// installer hashes every part against that manifest on the handles it unpacks from.
fn install_engine_package(
    directory: &Path,
    engine: &EngineUpdate,
    public_key: &str,
    engine_dir: &Path,
) -> AppResult<()> {
    let manifest = read_verified(
        &directory.join(&engine.manifest.name),
        &engine.manifest,
        public_key,
    )?;
    for part in &engine.parts {
        verify_file(&directory.join(&part.name), part, public_key)?;
    }
    crate::engine_payload::install_manifest(&manifest, directory, engine_dir)?;
    Ok(())
}

/// A temporary file of our own: a leftover (or a planted link) is removed, never followed.
fn create_temporary(path: &Path) -> AppResult<fs::File> {
    let _ = fs::remove_file(path);
    Ok(fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?)
}
async fn download_file(
    file: &DownloadFile,
    directory: &Path,
    config: &UpdateConfig,
    cancel: &AtomicBool,
    progress: &mut impl FnMut(u64),
) -> AppResult<()> {
    if !safe_file_name(&file.name) {
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
        let mut output = create_temporary(&temporary)?;
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
/// Join verified parts into the package and verify it as one signed file. Each part is
/// removed once copied, so the parts and the package never need twice the space.
fn join_parts(
    parts: &[DownloadFile],
    directory: &Path,
    whole: &DownloadFile,
    public_key: &str,
) -> AppResult<()> {
    let path = directory.join(&whole.name);
    let temporary = directory.join(format!("{}.partial", whole.name));
    let result = (|| -> AppResult<()> {
        let mut output = create_temporary(&temporary)?;
        for part in parts {
            let source = directory.join(&part.name);
            std::io::copy(&mut fs::File::open(&source)?, &mut output)?;
            fs::remove_file(&source)?;
        }
        output.sync_all()?;
        drop(output);
        verify_file(&temporary, whole, public_key)?;
        fs::rename(&temporary, &path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

/// Download the application package: one file, or its parts joined into one.
async fn download_application(
    file: &DownloadFile,
    parts: &[DownloadFile],
    directory: &Path,
    config: &UpdateConfig,
    cancel: &AtomicBool,
    progress: &mut impl FnMut(u64),
) -> AppResult<()> {
    if parts.is_empty() {
        return download_file(file, directory, config, cancel, progress).await;
    }
    let path = directory.join(&file.name);
    if path.is_file() && verify_file(&path, file, &config.public_key).is_ok() {
        progress(file.size);
        return Ok(());
    }
    for part in parts {
        download_file(part, directory, config, cancel, progress).await?;
    }
    if cancel.load(Ordering::SeqCst) {
        return Err(invalid("Update download cancelled"));
    }
    let (parts, directory, file) = (parts.to_vec(), directory.to_path_buf(), file.clone());
    let public_key = config.public_key.clone();
    tauri::async_runtime::spawn_blocking(move || join_parts(&parts, &directory, &file, &public_key))
        .await
        .map_err(|e| invalid(e.to_string()))?
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
                name: APPLICATION_FILE.into(),
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
    // While split parts are joined, one part exists twice.
    let joining = contract
        .parts
        .iter()
        .map(|part| part.size)
        .max()
        .unwrap_or(0);
    // Files an earlier attempt left complete are on the disk already: the free space
    // measured now no longer includes them.
    let engine_files = contract
        .engine
        .iter()
        .filter(|engine| engine_needs_package(&state.paths.next_root, &config, &engine.id))
        .flat_map(|engine| std::iter::once(&engine.manifest).chain(&engine.parts));
    let present = already_downloaded(&directory, std::iter::once(&file).chain(engine_files));
    let needed = status
        .size
        .saturating_sub(present)
        .checked_add(contract.unpacked_size)
        .and_then(|s| s.checked_add(joining))
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
        download_application(
            &file,
            &contract.parts,
            &directory,
            &config,
            &state.updates.cancel,
            &mut progress,
        )
        .await?;
        if let Some(engine) = contract
            .engine
            .filter(|e| engine_needs_package(&state.paths.next_root, &config, &e.id))
        {
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
    {
        // One settings writer at a time, from moving the broken file aside to writing the
        // recovered one: a switch saved meanwhile waits and then applies on top.
        let _io = lock(&state.settings_io)?;
        crate::settings::recover_defaults(&state.paths)?;
        let (settings, recipes) = crate::settings::load(&state.paths);
        *lock(&state.settings)? = settings;
        *lock(&state.recipes)? = recipes;
        state.write_settings()?;
    }
    crate::native_menu::reconcile(&app);
    crate::state::emit_state_changed(&app);
    Ok(())
}

pub fn ensure_not_installing(state: &AppState) -> AppResult<()> {
    if state.updates.installing.load(Ordering::SeqCst) {
        return Err(invalid("An update is being installed"));
    }
    Ok(())
}
/// The bytes of those files that already exist in `directory` at their full size.
fn already_downloaded<'a>(directory: &Path, files: impl Iterator<Item = &'a DownloadFile>) -> u64 {
    files
        .filter(|file| {
            safe_file_name(&file.name)
                && fs::metadata(directory.join(&file.name)).is_ok_and(|m| m.len() == file.size)
        })
        .map(|file| file.size)
        .sum()
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
    active_engine_for_distribution(
        paths,
        crate::distribution::managed_by_store(),
        option_env!("LOCALSR_ENGINE_ID").unwrap_or(""),
    )
}
/// The engine this host runs, given the one it ships and the one an update installed.
/// A host built for an engine id runs the installed engine of that id (`installed`
/// already honours the id) and falls back to the one it ships. A host without an id,
/// a development or preview build, runs the engine it ships: the installed one belongs
/// to whatever release last updated this computer and can be older than the host.
pub fn choose_engine(
    compiled_engine_id: &str,
    bundled: Option<PathBuf>,
    installed: Option<PathBuf>,
) -> Option<PathBuf> {
    if compiled_engine_id.is_empty() {
        bundled.or(installed)
    } else {
        installed.or(bundled)
    }
}

pub fn choose_host_engine(bundled: Option<PathBuf>, installed: Option<PathBuf>) -> Option<PathBuf> {
    choose_engine(
        option_env!("LOCALSR_ENGINE_ID").unwrap_or(""),
        bundled,
        installed,
    )
}

/// The engine an in-app update installed, if this host was built for it.
///
/// A host with a compiled engine id selects only the matching installed engine.
/// A host without one reads the active engine id from storage.
fn active_engine_for_distribution(
    paths: &crate::paths::AppPaths,
    managed_by_store: bool,
    bundled_engine_id: &str,
) -> Option<PathBuf> {
    if managed_by_store {
        return None;
    }
    // A host compiled for an engine id runs exactly that id's engine: the shared
    // active-engine.txt may already name the engine of an installer that was cancelled.
    let id = if bundled_engine_id.is_empty() {
        fs::read_to_string(paths.next_root.join("active-engine.txt"))
            .ok()?
            .trim()
            .to_owned()
    } else {
        bundled_engine_id.to_string()
    };
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
#[tauri::command]
pub async fn install_update(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    crate::distribution::require_direct_updates()?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        // A second request while one runs must not rewrite the first one's status.
        let _busy = acquire(&state.updates.busy)?;
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
/// Whether the update must bring the engine package. A different engine id always does.
/// The same id does not, unless an earlier update delivered this engine as a versioned
/// engine (`active-engine.txt` names it) and that engine is gone: the bundle may then
/// still hold the older engine, which the handshake cannot tell apart.
fn engine_needs_package(next_root: &Path, config: &UpdateConfig, engine_id: &str) -> bool {
    if engine_id != config.engine_id {
        return true;
    }
    let delivered = fs::read_to_string(next_root.join("active-engine.txt"))
        .is_ok_and(|active| active.trim() == engine_id);
    delivered
        && !next_root
            .join("engines")
            .join(engine_id)
            .join("engine")
            .join(if cfg!(windows) {
                "localsr-worker.exe"
            } else {
                "localsr-worker"
            })
            .is_file()
}

fn prepare_update_engine(
    config: &UpdateConfig,
    contract: &ReleaseContract,
    directory: &Path,
    next_root: &Path,
    installed_worker: impl FnOnce() -> AppResult<PathBuf>,
) -> AppResult<PathBuf> {
    if !engine_needs_package(next_root, config, &contract.engine_id) {
        // The new application carries the same engine. Keep the existing versioned
        // engine, or use the bundle; no payload, adoption or activation is needed.
        return installed_worker();
    }
    let engine = contract
        .engine
        .as_ref()
        .ok_or_else(|| invalid("The changed engine has no package"))?;
    let engine_root = next_root.join("engines").join(&contract.engine_id);
    fs::create_dir_all(&engine_root)?;
    let engine_dir = engine_root.join("engine");
    if !engine_dir.exists() {
        install_engine_package(directory, engine, &config.public_key, &engine_dir)?;
    }
    let engine_exe = engine_dir.join(if cfg!(windows) {
        "localsr-worker.exe"
    } else {
        "localsr-worker"
    });
    Ok(engine_exe)
}

/// The caller holds `updates.busy`.
fn install_checked(state: &Arc<AppState>, app: &AppHandle) -> AppResult<()> {
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
    verify_statement(
        config,
        &config.target(),
        &channel(state),
        &checked.update.version,
        &app.package_info().version.to_string(),
        contract,
    )?;
    let file = DownloadFile {
        name: APPLICATION_FILE.into(),
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
    let engine_exe =
        prepare_update_engine(config, contract, &directory, &state.paths.next_root, || {
            worker::installed_worker_path(state, app)
        })?;
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
        if contract.engine_id != config.engine_id {
            update_storage::write_atomic(&active_file, contract.engine_id.as_bytes())?;
        }
        if config.kind == "portable" && cfg!(target_os = "linux") {
            // Unpack only a copy of our own that was verified after it was made.
            let verified = directory.join("application.verified");
            let _ = fs::remove_file(&verified);
            stage_verified(
                &directory.join(&file.name),
                &verified,
                &file,
                &config.public_key,
            )?;
            let unpacked = directory.join("unpacked");
            if unpacked.exists() {
                fs::remove_dir_all(&unpacked)?;
            }
            update_storage::extract_portable(&verified, &unpacked, contract.unpacked_size)?;
            // The path this process started from: after a swap /proc/self/exe reads
            // "... (deleted)", so it is never asked again.
            replace_portable_host(
                &tauri::utils::platform::current_exe()?,
                &unpacked.join("localsr-next"),
                &state.paths.next_root.join(SWAP_JOURNAL),
                &checked.update.version,
                |path| update_storage::smoke(path, Duration::from_secs(60)),
            )
        } else {
            install_package(state, app, checked, &file, &directory, &backup)
        }
    })();
    if let Err(error) = result {
        let restored_engine = match previous_engine {
            Some(bytes) => update_storage::write_atomic(&active_file, &bytes),
            None => match fs::remove_file(&active_file) {
                Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(error.into()),
                _ => Ok(()),
            },
        };
        if let Err(restore) = restored_engine {
            eprintln!("LocalSR: could not restore the active engine: {restore}");
        }
        if let Err(resume) = resume_after_failed_install(
            state,
            || {
                *lock(&state.database)? = crate::database::Database::open(&state.paths.database)?;
                Ok(())
            },
            || worker::start_worker(state.clone(), app.clone()),
        ) {
            eprintln!("LocalSR: could not resume after the failed update: {resume}");
        }
        // On Windows the downloads are gone once the installer was handed over.
        status.stage = if lock(&state.updates.staged)?.is_some() {
            "downloaded"
        } else {
            "available"
        }
        .into();
        status.message = format!("Update failed; previous version restored. {error}");
        publish(state, app, status);
        return Err(error);
    }
    // The new application is in place and this process has stopped its worker and closed
    // its database: from here it only leaves, whatever the cleanup or restart says.
    if let Err(error) = finish_install(&backup, &checked.update.version, &directory) {
        eprintln!("LocalSR: could not clean up after the update: {error}");
    }
    restart_application(app)
}

/// Take the application back to work after an install failed before it was handed over.
/// The worker is only started again when the old one really ended: a worker that did not
/// stop within the wait must not get a second one beside it. Every step runs, and the
/// flag that blocks new workers is cleared first, so one failure cannot leave the worker
/// disabled for the rest of the session.
fn resume_after_failed_install(
    state: &AppState,
    reopen_database: impl FnOnce() -> AppResult<()>,
    start_worker: impl FnOnce() -> AppResult<()>,
) -> AppResult<()> {
    let database = reopen_database();
    state.worker.shutting_down.store(false, Ordering::SeqCst);
    let worker = if state.worker.pid.load(Ordering::SeqCst) == 0 {
        start_worker()
    } else {
        Ok(())
    };
    database.and(worker)
}

/// Keep profile backups; discard only downloaded/staged update material.
fn finish_install(backup: &Path, version: &str, downloads: &Path) -> AppResult<()> {
    update_storage::fault("confirm-version")?;
    fs::write(backup.join("installed-version.txt"), version)?;
    update_storage::fault("confirm-downloads")?;
    match fs::remove_dir_all(downloads) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

/// Hand the verified package to the platform. macOS replaces the app bundle through the
/// Tauri updater; Windows runs the NSIS installer, which ends this process; LocalSR swaps
/// an AppImage itself, streaming from disk and checking the new image before it keeps it.
/// Only bytes verified where they are installed from are handed over.
fn install_package(
    state: &AppState,
    app: &AppHandle,
    checked: &CheckedUpdate,
    file: &DownloadFile,
    downloads: &Path,
    backup: &Path,
) -> AppResult<()> {
    let package = downloads.join(&file.name);
    #[cfg(target_os = "linux")]
    {
        let _ = backup;
        let env = tauri::Manager::env(app);
        let appimage = running_appimage(
            env.appimage.map(PathBuf::from),
            env.appdir.map(PathBuf::from),
            &std::env::current_exe()?,
        )
        .ok_or_else(|| {
            invalid(
                "This copy of LocalSR does not run from its AppImage, so the update cannot \
                 replace it. Download the new version from the website.",
            )
        })?;
        replace_appimage(
            &appimage,
            &package,
            file,
            &checked.config.public_key,
            &state.paths.next_root.join(SWAP_JOURNAL),
            &checked.update.version,
            |path| update_storage::smoke(path, Duration::from_secs(120)),
        )
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = app;
        let bytes = read_verified(&package, file, &checked.config.public_key)?;
        if cfg!(windows) {
            // NSIS writes the new files in place, on the install folder's own volume.
            let folder = std::env::current_exe()?
                .parent()
                .map(Path::to_path_buf)
                .ok_or_else(|| invalid("The installation has no folder"))?;
            require_space(
                fs2::available_space(&folder)?,
                checked.contract.unpacked_size + 256 * 1024 * 1024,
            )?;
            // The installer runs after this process exits: the next start confirms the
            // new version before the downloads go, or keeps them for another try.
            record_pending_install(
                &state.paths.next_root,
                &checked.update.version,
                downloads,
                backup,
            )?;
            let handed_over = checked
                .update
                .install(bytes)
                .map_err(|e| invalid(e.to_string()));
            // Reached only when the installer could not be started.
            let _ = fs::remove_file(state.paths.next_root.join(PENDING_INSTALL));
            return handed_over;
        }
        let _ = (state, backup, downloads);
        checked
            .update
            .install(bytes)
            .map_err(|e| invalid(e.to_string()))
    }
}

const SWAP_JOURNAL: &str = "swap-journal.json";
const PENDING_INSTALL: &str = "update-pending.json";

#[derive(Deserialize, Serialize)]
struct PendingInstall {
    version: String,
    downloads: PathBuf,
    backup: PathBuf,
}

/// Remember a Windows installation that the installer finishes after LocalSR exits.
#[cfg_attr(not(windows), allow(dead_code))]
fn record_pending_install(
    next_root: &Path,
    version: &str,
    downloads: &Path,
    backup: &Path,
) -> AppResult<()> {
    let pending = PendingInstall {
        version: version.into(),
        downloads: downloads.into(),
        backup: backup.into(),
    };
    update_storage::write_atomic(
        &next_root.join(PENDING_INSTALL),
        &serde_json::to_vec(&pending)?,
    )
}

/// At start: when the version the installer was given is the one running, finish its
/// bookkeeping; otherwise the installer failed or was cancelled, and the verified
/// downloads stay for another try. Returns whether an installation was confirmed.
pub fn confirm_pending_install(next_root: &Path, running_version: &str) -> bool {
    let marker = next_root.join(PENDING_INSTALL);
    // A crash while the marker was written leaves a temporary file.
    update_storage::remove_stale_temporaries(&marker);
    let Ok(bytes) = fs::read(&marker) else {
        return false;
    };
    let Ok(pending) = serde_json::from_slice::<PendingInstall>(&bytes) else {
        return false;
    };
    // Only LocalSR's own folders are ever removed.
    if pending.version != running_version
        || pending.downloads != next_root.join("updates")
        || pending.backup.parent() != Some(next_root.join("backups").as_path())
    {
        return false;
    }
    if finish_install(&pending.backup, &pending.version, &pending.downloads).is_err() {
        return false;
    }
    update_storage::fault("confirm-marker").is_ok() && fs::remove_file(marker).is_ok()
}

/// Before anything else at start: roll back an executable swap that a crash interrupted,
/// and start the restored executable when it is the one this process runs. Returns
/// whether this process should exit for it.
pub fn recover_interrupted_update() -> bool {
    let Ok(paths) = crate::paths::AppPaths::discover() else {
        return false;
    };
    match update_storage::recover_swap(&paths.next_root.join(SWAP_JOURNAL)) {
        Ok(Some(restored)) => {
            let running = std::env::var_os("APPIMAGE")
                .map(PathBuf::from)
                .or_else(|| std::env::current_exe().ok());
            if !running.is_some_and(|running| same_file(&running, &restored)) {
                return false;
            }
            match restart_service() {
                Ok(true) => true,
                Ok(false) => relaunch(&restored),
                Err(error) => {
                    eprintln!("LocalSR: restored the previous version; reopen LocalSR: {error}");
                    // Exit the unaccepted process even when the service manager failed.
                    true
                }
            }
        }
        Ok(None) => false,
        Err(error) => {
            eprintln!("LocalSR: could not roll back an interrupted update: {error}");
            false
        }
    }
}

/// Whether two paths name one file, also through links.
fn same_file(a: &Path, b: &Path) -> bool {
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => a == b,
    }
}

/// Start the restored executable. This process is the unaccepted one and exits either
/// way: a failed start leaves the user to reopen LocalSR, never to keep it running.
fn relaunch(restored: &Path) -> bool {
    if let Err(error) = std::process::Command::new(restored)
        .args(std::env::args_os().skip(1))
        .spawn()
    {
        eprintln!("LocalSR: restored the previous version; reopen LocalSR: {error}");
    }
    true
}

/// The AppImage file this process runs from: $APPIMAGE, when this executable really
/// lives inside the image's mount ($APPDIR). Anything else is no AppImage to replace.
#[cfg(any(target_os = "linux", test))]
fn running_appimage(
    appimage: Option<PathBuf>,
    appdir: Option<PathBuf>,
    executable: &Path,
) -> Option<PathBuf> {
    // The file itself, not a link to it: the swap renames over this path.
    let appimage = appimage
        .filter(|path| path.is_absolute() && path.is_file())?
        .canonicalize()
        .ok()?;
    let appdir = appdir?.canonicalize().ok()?;
    executable
        .canonicalize()
        .ok()?
        .starts_with(appdir)
        .then_some(appimage)
}

/// Read the version at the destination under the swap lock. A surviving old
/// process must not replace a newer release another process already installed.
// Compiled everywhere because the portable branch that calls it is; it only runs for the
// Linux AppImage.
fn require_newer_executable(executable: &Path, candidate: &str) -> AppResult<()> {
    use std::io::{Seek, SeekFrom};
    use std::process::{Command, Stdio};
    let mut output = tempfile::tempfile()?;
    let mut command = Command::new(executable);
    command
        .arg("--headless-update-version")
        .stdin(Stdio::null())
        .stdout(output.try_clone()?)
        .stderr(Stdio::null());
    crate::process_tree::lead_own_group(&mut command);
    let mut child = command.spawn()?;
    let tree = crate::process_tree::ProcessTree::new(&child);
    let start = std::time::Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            if !status.success() {
                return Err(invalid("Could not read the installed application version"));
            }
            break;
        }
        if start.elapsed() >= Duration::from_secs(10) {
            tree.terminate();
            let _ = child.kill();
            let _ = child.wait();
            return Err(invalid("The installed application version check timed out"));
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    output.seek(SeekFrom::Start(0))?;
    let mut installed = String::new();
    output.take(256).read_to_string(&mut installed)?;
    let installed = semver::Version::parse(installed.trim()).map_err(|e| invalid(e.to_string()))?;
    let candidate = semver::Version::parse(candidate).map_err(|e| invalid(e.to_string()))?;
    if candidate <= installed {
        return Err(invalid(
            "This release is not newer than the application installed at this path",
        ));
    }
    Ok(())
}

/// Replace the unpacked Linux host at `current` with `new_host`, which the caller has
/// unpacked from a verified package. `current` is also where the installed version is read
/// under the swap lock.
fn replace_portable_host(
    current: &Path,
    new_host: &Path,
    journal: &Path,
    version: &str,
    smoke: impl Fn(&Path) -> AppResult<()>,
) -> AppResult<()> {
    update_storage::replace_executable(
        current,
        journal,
        |staged| {
            require_newer_executable(current, version)?;
            update_storage::copy_new(new_host, staged)
        },
        smoke,
    )
}

/// Swap the AppImage for a verified copy of the update made next to it, run the new
/// image's startup check, and restore the previous image if that fails. The running image
/// stays mounted, so replacing its file does not disturb this process.
#[cfg(any(target_os = "linux", all(test, unix)))]
fn replace_appimage(
    appimage: &Path,
    update: &Path,
    file: &DownloadFile,
    public_key: &str,
    journal: &Path,
    version: &str,
    smoke: impl Fn(&Path) -> AppResult<()>,
) -> AppResult<()> {
    let parent = appimage
        .parent()
        .ok_or_else(|| invalid("The AppImage has no folder"))?;
    require_space(fs2::available_space(parent)?, file.size + 256 * 1024 * 1024)?;
    update_storage::replace_executable(
        appimage,
        journal,
        |staged| {
            require_newer_executable(appimage, version)?;
            stage_verified(update, staged, file, public_key)
        },
        smoke,
    )
}

fn restart_service() -> AppResult<bool> {
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
                return Ok(true);
            }
            return Err(invalid(
                "Update installed. The desktop service could not restart; open LocalSR again.",
            ));
        }
    }
    Ok(false)
}

#[derive(Debug, PartialEq)]
enum RestartPlan {
    /// The service manager restarts LocalSR, or only the user can: this process exits.
    Exit,
    /// Start the application again ourselves.
    Respawn,
}

fn restart_plan(service: AppResult<bool>) -> RestartPlan {
    match service {
        Ok(false) => RestartPlan::Respawn,
        Ok(true) => RestartPlan::Exit,
        Err(error) => {
            // The update is installed: a process with a stopped worker and a closed
            // database must not carry on, so it exits and the user reopens LocalSR.
            eprintln!("LocalSR: {error}");
            RestartPlan::Exit
        }
    }
}

fn restart_application(app: &AppHandle) -> AppResult<()> {
    match restart_plan(restart_service()) {
        RestartPlan::Exit => {
            app.exit(0);
            Ok(())
        }
        RestartPlan::Respawn => app.restart(),
    }
}

#[cfg(test)]
mod tests;
