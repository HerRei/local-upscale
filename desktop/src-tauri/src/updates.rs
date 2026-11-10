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

/// What the Mac signs for each feed entry, format 2 (scripts/beta_update_signing.py,
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
/// The engine an in-app update installed, if this host was built for it.
///
/// An update writes its engine and a host compiled with the same engine id. A
/// host installed from a disk image over an updated copy has a different id
/// and must use the engine bundled with it: otherwise 0.1.6 ran the 0.1.5
/// engine that an earlier update had left active.
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
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn arbitrary_json() -> impl Strategy<Value = serde_json::Value> {
        let leaf = prop_oneof![
            Just(serde_json::Value::Null),
            any::<bool>().prop_map(serde_json::Value::from),
            any::<i64>().prop_map(serde_json::Value::from),
            ".*".prop_map(serde_json::Value::from),
        ];
        leaf.prop_recursive(3, 32, 5, |inner| {
            prop_oneof![
                prop::collection::vec(inner.clone(), 0..5).prop_map(serde_json::Value::Array),
                prop::collection::btree_map(".*", inner, 0..5)
                    .prop_map(|fields| { serde_json::Value::Object(fields.into_iter().collect()) }),
            ]
        })
    }

    proptest! {
        #![proptest_config(ProptestConfig { cases: 64, rng_seed: proptest::test_runner::RngSeed::Fixed(0x038), .. ProptestConfig::default() })]

        #[test]
        fn arbitrary_json_never_panics(bytes in prop::collection::vec(any::<u8>(), 0..4096)) {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let _ = serde_json::from_slice::<ReleaseContract>(&bytes);
                let _ = serde_json::from_slice::<ReleaseStatement>(&bytes);
            }));
            prop_assert!(result.is_ok());
        }

        #[test]
        fn generated_json_values_never_panic(value in arbitrary_json()) {
            let bytes = serde_json::to_vec(&value).unwrap();
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let _ = serde_json::from_slice::<ReleaseContract>(&bytes);
                let _ = serde_json::from_slice::<ReleaseStatement>(&bytes);
            }));
            prop_assert!(result.is_ok());
        }

        #[test]
        fn changing_any_signed_release_field_is_rejected(field in 0u8..10) {
            let (mut config, mut contract) = statement_contract();
            let mut target = "linux-x86_64-rocm-native".to_owned();
            let mut channel = "beta".to_owned();
            let mut version = "0.3.7-beta".to_owned();
            match field {
                0 => version = "0.3.8-beta".into(),
                1 => channel = "stable".into(),
                2 => target = "linux-x86_64-cuda-native".into(),
                3 => config.backend = "cuda".into(),
                4 => config.kind = "portable".into(),
                5 => contract.protocol += 1,
                6 => contract.engine_id.push('x'),
                7 => contract.sha256 = "f".repeat(64),
                8 => contract.unpacked_size += 1,
                _ => contract.engine.as_mut().unwrap().manifest.sha256 = "f".repeat(64),
            }
            prop_assert!(validate_contract(&config, &channel, &version, &contract).is_err()
                || verify_statement(
                    &config, &target, &channel, &version, "0.3.6-beta", &contract
                ).is_err());
        }

        #[test]
        fn mutating_any_signed_statement_field_is_rejected(field in 0u8..12) {
            let (config, mut contract) = statement_contract();
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(&contract.statement_v2)
                .unwrap();
            let mut statement: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
            let field = match field {
                0 => "schema_version",
                1 => "version",
                2 => "channel",
                3 => "target",
                4 => "backend",
                5 => "kind",
                6 => "protocol",
                7 => "engine_id",
                8 => "sha256",
                9 => "engine_manifest",
                10 => "size",
                _ => "unpacked_size",
            };
            statement[field] = match field {
                "schema_version" | "protocol" | "size" | "unpacked_size" => serde_json::json!(0),
                "engine_manifest" => serde_json::Value::Null,
                _ => serde_json::json!("mutated"),
            };
            contract.statement_v2 = base64::engine::general_purpose::STANDARD
                .encode(serde_json::to_vec(&statement).unwrap());
            prop_assert!(verify_statement(
                &config,
                "linux-x86_64-rocm-native",
                "beta",
                "0.3.7-beta",
                "0.3.6-beta",
                &contract,
            ).is_err());
        }

        #[test]
        fn release_versions_follow_semver_precedence(
            major in 0u32..5,
            minor in 0u32..20,
            patch in 0u32..20,
            prerelease in prop::sample::select(vec!["", "-alpha", "-beta", "-beta.1", "-rc.1"]),
        ) {
            let (config, contract) = statement_contract();
            let installed = format!("{major}.{minor}.{patch}{prerelease}");
            let expected = semver::Version::parse("0.3.7-beta").unwrap()
                > semver::Version::parse(&installed).unwrap();
            prop_assert_eq!(
                verify_statement(
                    &config, "linux-x86_64-rocm-native", "beta", "0.3.7-beta", &installed, &contract
                ).is_ok(),
                expected,
            );
        }
    }

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
            active_engine_for_distribution(&paths, false, "previous"),
            Some(engine.clone())
        );
        assert_eq!(
            active_engine_for_distribution(&paths, true, "previous"),
            None
        );
        assert_eq!(fs::read(engine).unwrap(), b"previous engine");
    }

    #[test]
    fn a_host_from_another_release_ignores_the_engine_an_update_left_active() {
        let root = tempfile::tempdir().unwrap();
        let paths = crate::paths::AppPaths::under(root.path());
        let engine = paths
            .next_root
            .join("engines/macos-arm64-mps-old/engine")
            .join(if cfg!(windows) {
                "localsr-worker.exe"
            } else {
                "localsr-worker"
            });
        fs::create_dir_all(engine.parent().unwrap()).unwrap();
        fs::write(&engine, b"old engine").unwrap();
        fs::write(
            paths.next_root.join("active-engine.txt"),
            b"macos-arm64-mps-old",
        )
        .unwrap();
        assert_eq!(
            active_engine_for_distribution(&paths, false, "macos-arm64-mps-new"),
            None
        );
        assert_eq!(
            active_engine_for_distribution(&paths, false, "macos-arm64-mps-old"),
            Some(engine.clone())
        );
        // Development builds carry no engine id and keep using the installed engine.
        assert_eq!(
            active_engine_for_distribution(&paths, false, ""),
            Some(engine)
        );
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
            parts: Vec::new(),
            statement_v2: String::new(),
            statement_v2_signature: String::new(),
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

    #[test]
    fn every_edition_looks_up_its_own_feed_entry() {
        assert_eq!(
            platform_key("macos", "aarch64", "mps", "native"),
            "darwin-aarch64-mps-native"
        );
        assert_eq!(
            platform_key("windows", "x86_64", "cuda", "native"),
            "windows-x86_64-cuda-native"
        );
        assert_eq!(
            platform_key("windows", "x86_64", "directml", "native"),
            "windows-x86_64-directml-native"
        );
        assert_eq!(
            platform_key("linux", "x86_64", "rocm", "native"),
            "linux-x86_64-rocm-native"
        );
        assert_eq!(
            platform_key("linux", "x86_64", "cpu", "portable"),
            "linux-x86_64-cpu-portable"
        );
        let (_, _, config) = fixture();
        assert_eq!(
            config.target(),
            platform_key(
                std::env::consts::OS,
                std::env::consts::ARCH,
                "rocm",
                "portable"
            )
        );
    }

    fn configured(public_key: &str, backend: &str) -> UpdateConfig {
        UpdateConfig {
            public_key: public_key.into(),
            feed: "https://herrei.github.io/localsr/updates".into(),
            backend: backend.into(),
            engine_id: "windows-x86_64-cuda-0123456789ab".into(),
            kind: "native".into(),
        }
    }

    #[test]
    fn a_windows_or_linux_build_with_its_identity_checks_its_feed() {
        // checks_for_updates() is this with the compiled identity: true means the launch
        // check, the anonymous count and both menu switches exist.
        assert!(checks_feed(&configured("KEY", "cuda"), false));
        assert!(!checks_feed(&configured("", "cuda"), false));
        assert!(!checks_feed(&configured("KEY", ""), false));
        let mut no_engine = configured("KEY", "cuda");
        no_engine.engine_id.clear();
        assert!(!checks_feed(&no_engine, false));
        // Store builds update through the Store.
        assert!(!checks_feed(&configured("KEY", "cuda"), true));
    }

    #[test]
    fn the_installed_smoke_report_states_the_compiled_update_identity() {
        let config = configured("KEY", "cuda");
        assert_eq!(
            identity(&config, false),
            serde_json::json!({
                "enabled": true,
                "target": config.target(),
                "backend": "cuda",
                "kind": "native",
                "engine_id": "windows-x86_64-cuda-0123456789ab",
            })
        );
        assert_eq!(identity(&config, true)["enabled"], false);
        assert_eq!(identity(&configured("", "cuda"), false)["enabled"], false);
    }

    fn part(name: &str, size: u64) -> DownloadFile {
        DownloadFile {
            name: name.into(),
            url: format!("https://example.test/{name}"),
            size,
            sha256: "a".repeat(64),
            signature: "signature".into(),
        }
    }

    #[test]
    fn a_split_package_must_add_up_and_keep_every_part_below_the_file_limit() {
        let (_, file, config) = fixture();
        let gib = 1024u64.pow(3);
        let mut contract = ReleaseContract {
            channel: "beta".into(),
            backend: "rocm".into(),
            kind: "portable".into(),
            protocol: PROTOCOL_VERSION,
            engine_id: "abc".into(),
            size: 3 * gib,
            unpacked_size: 4 * gib,
            sha256: file.sha256,
            parts: vec![
                part("a.AppImage.part-0001", 2 * gib - 1),
                part("a.AppImage.part-0002", gib + 1),
            ],
            statement_v2: String::new(),
            statement_v2_signature: String::new(),
            engine: None,
        };
        validate_contract(&config, "beta", "0.3.7-beta", &contract).unwrap();
        contract.parts[1].size -= 1;
        assert!(validate_contract(&config, "beta", "0.3.7-beta", &contract).is_err());
        contract.parts = vec![
            part("a.AppImage.part-0001", 2 * gib),
            part("a.AppImage.part-0002", gib),
        ];
        assert!(validate_contract(&config, "beta", "0.3.7-beta", &contract).is_err());
        for names in [
            ["a.part-0001", "a.part-0001"],
            ["application.update", "a.part-0002"],
            ["../a.part-0001", "a.part-0002"],
        ] {
            contract.parts = vec![part(names[0], 2 * gib - 1), part(names[1], gib + 1)];
            assert!(validate_contract(&config, "beta", "0.3.7-beta", &contract).is_err());
        }
        contract.parts = vec![part("a.part-0001", 3 * gib)];
        assert!(validate_contract(&config, "beta", "0.3.7-beta", &contract).is_err());
        contract.parts = (0..65)
            .map(|index| part(&format!("a.part-{index:04}"), 1))
            .collect();
        contract.size = 65;
        assert!(validate_contract(&config, "beta", "0.3.7-beta", &contract).is_err());
    }

    /// Two signed parts and the signed whole file they join to, with a disposable key.
    fn split_fixture() -> (UpdateConfig, DownloadFile, Vec<DownloadFile>, Vec<Vec<u8>>) {
        let value: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/update-parts.json")).unwrap();
        let file = |item: &serde_json::Value, name: &str| {
            let data = item["data"].as_str().unwrap().as_bytes().to_vec();
            (
                DownloadFile {
                    name: name.into(),
                    url: format!("https://example.test/{name}"),
                    size: data.len() as u64,
                    sha256: item["sha256"].as_str().unwrap().into(),
                    signature: item["signature"].as_str().unwrap().into(),
                },
                data,
            )
        };
        let (whole, _) = file(&value["whole"], "application.update");
        let mut parts = Vec::new();
        let mut data = Vec::new();
        for (index, item) in value["parts"].as_array().unwrap().iter().enumerate() {
            let (part, bytes) = file(item, &format!("LocalSR.AppImage.part-{:04}", index + 1));
            parts.push(part);
            data.push(bytes);
        }
        let config = UpdateConfig {
            public_key: value["public_key"].as_str().unwrap().into(),
            feed: "http://127.0.0.1:1".into(),
            backend: "rocm".into(),
            engine_id: "linux-x86_64-rocm-0123456789ab".into(),
            kind: "native".into(),
        };
        (config, whole, parts, data)
    }

    #[test]
    fn a_split_package_is_joined_and_verified_as_one_signed_file() {
        let (config, whole, parts, data) = split_fixture();
        let root = tempfile::tempdir().unwrap();
        for (part, bytes) in parts.iter().zip(&data) {
            fs::write(root.path().join(&part.name), bytes).unwrap();
        }
        join_parts(&parts, root.path(), &whole, &config.public_key).unwrap();
        let joined = root.path().join(&whole.name);
        assert_eq!(fs::read(&joined).unwrap(), data.concat());
        verify_file(&joined, &whole, &config.public_key).unwrap();
        for part in &parts {
            assert!(!root.path().join(&part.name).exists());
        }

        // Parts in the wrong order are each intact, but never become an installable file.
        let root = tempfile::tempdir().unwrap();
        for (part, bytes) in parts.iter().zip(data.iter().rev()) {
            fs::write(root.path().join(&part.name), bytes).unwrap();
        }
        assert!(join_parts(&parts, root.path(), &whole, &config.public_key).is_err());
        assert!(!root.path().join(&whole.name).exists());
        assert!(!root.path().join(format!("{}.partial", whole.name)).exists());
    }

    /// Serve each named file once over plain HTTP on the loopback interface.
    fn serve(files: Vec<(String, Vec<u8>)>) -> (String, std::thread::JoinHandle<()>) {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            for _ in 0..files.len() {
                let (mut socket, _) = listener.accept().unwrap();
                let mut request = Vec::new();
                let mut buffer = [0; 1024];
                while !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
                    let count = socket.read(&mut buffer).unwrap();
                    assert!(count > 0);
                    request.extend_from_slice(&buffer[..count]);
                }
                let line = String::from_utf8_lossy(&request);
                let path = line
                    .split_whitespace()
                    .nth(1)
                    .unwrap()
                    .trim_start_matches('/');
                let (_, body) = files.iter().find(|(name, _)| name == path).unwrap();
                write!(
                    socket,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )
                .unwrap();
                socket.write_all(body).unwrap();
            }
        });
        (base, server)
    }

    #[test]
    fn a_split_package_downloads_its_parts_and_is_reused_once_joined() {
        let (config, whole, mut parts, data) = split_fixture();
        let (base, server) = serve(
            parts
                .iter()
                .zip(&data)
                .map(|(part, bytes)| (part.name.clone(), bytes.clone()))
                .collect(),
        );
        for part in &mut parts {
            part.url = format!("{base}/{}", part.name);
        }
        let root = tempfile::tempdir().unwrap();
        let cancel = AtomicBool::new(false);
        let mut downloaded = 0;
        tauri::async_runtime::block_on(download_application(
            &whole,
            &parts,
            root.path(),
            &config,
            &cancel,
            &mut |bytes| downloaded += bytes,
        ))
        .unwrap();
        server.join().unwrap();
        assert_eq!(downloaded, whole.size);
        verify_file(&root.path().join(&whole.name), &whole, &config.public_key).unwrap();
        // A later download after another check needs no network: the joined file verifies.
        downloaded = 0;
        tauri::async_runtime::block_on(download_application(
            &whole,
            &parts,
            root.path(),
            &config,
            &cancel,
            &mut |bytes| downloaded += bytes,
        ))
        .unwrap();
        assert_eq!(downloaded, whole.size);
    }

    /// A package, an engine package and the signed release statement that binds them,
    /// signed with a disposable key.
    fn release_fixture() -> serde_json::Value {
        serde_json::from_str(include_str!("../tests/fixtures/update-release.json")).unwrap()
    }

    fn signed_package() -> (Vec<u8>, DownloadFile, String) {
        let value = release_fixture();
        let data = value["package"]["data"]
            .as_str()
            .unwrap()
            .as_bytes()
            .to_vec();
        let file = DownloadFile {
            name: APPLICATION_FILE.into(),
            url: "https://example.test/package".into(),
            size: data.len() as u64,
            sha256: value["package"]["sha256"].as_str().unwrap().into(),
            signature: value["package"]["signature"].as_str().unwrap().into(),
        };
        (data, file, value["public_key"].as_str().unwrap().into())
    }

    #[cfg(unix)]
    const OLD_IMAGE: &[u8] = b"#!/bin/sh\nprintf '0.3.6-beta\\n'\n";

    #[cfg(unix)]
    struct SwapFixture {
        root: tempfile::TempDir,
        applications: PathBuf,
        appimage: PathBuf,
        update: PathBuf,
        journal: PathBuf,
        file: DownloadFile,
        public_key: String,
        data: Vec<u8>,
    }

    #[cfg(unix)]
    impl SwapFixture {
        fn new() -> Self {
            use std::os::unix::fs::PermissionsExt;
            let (data, file, public_key) = signed_package();
            let root = tempfile::tempdir().unwrap();
            let applications = root.path().join("Applications");
            let updates = root.path().join("updates");
            fs::create_dir_all(&applications).unwrap();
            fs::create_dir_all(&updates).unwrap();
            let appimage = applications.join("LocalSR.AppImage");
            fs::write(&appimage, OLD_IMAGE).unwrap();
            fs::set_permissions(&appimage, fs::Permissions::from_mode(0o755)).unwrap();
            let update = updates.join(APPLICATION_FILE);
            fs::write(&update, &data).unwrap();
            fs::set_permissions(&update, fs::Permissions::from_mode(0o600)).unwrap();
            let journal = root.path().join("swap-journal.json");
            Self {
                root,
                applications,
                appimage,
                update,
                journal,
                file,
                public_key,
                data,
            }
        }
        fn replace(&self, smoke: impl Fn(&Path) -> AppResult<()>) -> AppResult<()> {
            replace_appimage(
                &self.appimage,
                &self.update,
                &self.file,
                &self.public_key,
                &self.journal,
                "0.3.7-beta",
                smoke,
            )
        }
        fn leftovers(&self) -> Vec<String> {
            fs::read_dir(&self.applications)
                .unwrap()
                .map(|entry| entry.unwrap().file_name().into_string().unwrap())
                .filter(|name| name != "LocalSR.AppImage")
                .collect()
        }
    }

    #[cfg(unix)]
    #[test]
    fn an_appimage_update_swaps_the_image_and_restores_it_when_the_new_one_fails() {
        use std::os::unix::fs::PermissionsExt;
        let swap = SwapFixture::new();
        let failed = swap.replace(|path| {
            assert_eq!(fs::read(path).unwrap(), swap.data);
            Err(AppError::Worker("startup failed".into()))
        });
        assert!(failed.is_err());
        assert_eq!(fs::read(&swap.appimage).unwrap(), OLD_IMAGE);
        assert!(swap.leftovers().is_empty(), "{:?}", swap.leftovers());
        assert!(!swap.journal.exists());

        swap.replace(|path| {
            // The new image runs its startup check with the old image's permissions.
            assert_eq!(
                fs::metadata(path).unwrap().permissions().mode() & 0o777,
                0o755
            );
            Ok(())
        })
        .unwrap();
        assert_eq!(fs::read(&swap.appimage).unwrap(), swap.data);
        assert!(swap.leftovers().is_empty(), "{:?}", swap.leftovers());
        assert!(!swap.journal.exists());
    }

    #[cfg(unix)]
    #[test]
    fn a_surviving_old_process_cannot_downgrade_the_installed_appimage() {
        let swap = SwapFixture::new();
        let newer = b"#!/bin/sh\nprintf '0.4.0\\n'\n";
        fs::write(&swap.appimage, newer).unwrap();
        assert!(swap.replace(|_| Ok(())).is_err());
        assert_eq!(fs::read(&swap.appimage).unwrap(), newer);
        assert!(swap.leftovers().is_empty());
        assert!(!swap.journal.exists());
    }

    // Review finding 1: the bytes installed are the bytes verified, also when the download
    // is replaced after the first verification.
    #[cfg(unix)]
    #[test]
    fn an_appimage_update_verifies_the_copy_it_installs() {
        let swap = SwapFixture::new();
        fs::write(&swap.update, b"unsigned bytes swapped in after the check").unwrap();
        let result = swap.replace(|_| panic!("an unverified image must never run"));
        assert!(result.unwrap_err().to_string().contains("verification"));
        assert_eq!(fs::read(&swap.appimage).unwrap(), OLD_IMAGE);
        assert!(swap.leftovers().is_empty(), "{:?}", swap.leftovers());
    }

    #[test]
    fn windows_and_macos_install_the_bytes_they_verified() {
        let (data, file, public_key) = signed_package();
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join(APPLICATION_FILE);
        fs::write(&path, &data).unwrap();
        assert_eq!(read_verified(&path, &file, &public_key).unwrap(), data);
        fs::write(&path, b"swapped after the download was verified").unwrap();
        assert!(read_verified(&path, &file, &public_key).is_err());
    }

    fn engine_fixture(directory: &Path) -> (EngineUpdate, String) {
        let value = release_fixture();
        let engine = &value["engine"];
        let manifest = engine["manifest"].as_str().unwrap();
        fs::write(directory.join("engine-payload.json"), manifest).unwrap();
        let mut parts = Vec::new();
        for part in engine["parts"].as_array().unwrap() {
            let name = part["name"].as_str().unwrap();
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(part["data"].as_str().unwrap())
                .unwrap();
            fs::write(directory.join(name), &bytes).unwrap();
            parts.push(DownloadFile {
                name: name.into(),
                url: format!("https://example.test/{name}"),
                size: bytes.len() as u64,
                sha256: part["sha256"].as_str().unwrap().into(),
                signature: part["signature"].as_str().unwrap().into(),
            });
        }
        let update = EngineUpdate {
            id: "linux-x86_64-rocm-0123456789ab".into(),
            manifest: DownloadFile {
                name: "engine-payload.json".into(),
                url: "https://example.test/engine-payload.json".into(),
                size: manifest.len() as u64,
                sha256: engine["manifest_sha256"].as_str().unwrap().into(),
                signature: engine["manifest_signature"].as_str().unwrap().into(),
            },
            parts,
        };
        (update, value["public_key"].as_str().unwrap().into())
    }

    #[test]
    fn an_engine_package_is_installed_from_the_manifest_bytes_that_were_verified() {
        let root = tempfile::tempdir().unwrap();
        let (engine, public_key) = engine_fixture(root.path());
        let destination = root.path().join("engines/new/engine");
        fs::create_dir_all(destination.parent().unwrap()).unwrap();
        install_engine_package(root.path(), &engine, &public_key, &destination).unwrap();
        assert_eq!(
            fs::read(destination.join("localsr-worker")).unwrap(),
            b"worker"
        );

        // A manifest swapped in after the download is never parsed.
        let root = tempfile::tempdir().unwrap();
        let (engine, public_key) = engine_fixture(root.path());
        let manifest = root.path().join("engine-payload.json");
        let swapped = fs::read_to_string(&manifest)
            .unwrap()
            .replace("\"file_count\": 2", "\"file_count\": 1");
        fs::write(&manifest, swapped).unwrap();
        let destination = root.path().join("engines/new/engine");
        fs::create_dir_all(destination.parent().unwrap()).unwrap();
        assert!(install_engine_package(root.path(), &engine, &public_key, &destination).is_err());
        assert!(!destination.exists());
    }

    // Review finding 3: a planted link at a temporary name never redirects a write.
    #[cfg(unix)]
    #[test]
    fn a_planted_partial_link_never_redirects_a_download_or_a_join() {
        let (config, whole, mut parts, data) = split_fixture();
        let (base, server) = serve(
            parts
                .iter()
                .zip(&data)
                .map(|(part, bytes)| (part.name.clone(), bytes.clone()))
                .collect(),
        );
        for part in &mut parts {
            part.url = format!("{base}/{}", part.name);
        }
        let root = tempfile::tempdir().unwrap();
        let victim = root.path().join("LocalSR.AppImage");
        fs::write(&victim, b"working image").unwrap();
        for part in &parts {
            std::os::unix::fs::symlink(&victim, root.path().join(format!("{}.partial", part.name)))
                .unwrap();
        }
        std::os::unix::fs::symlink(&victim, root.path().join(format!("{}.partial", whole.name)))
            .unwrap();
        let cancel = AtomicBool::new(false);
        tauri::async_runtime::block_on(download_application(
            &whole,
            &parts,
            root.path(),
            &config,
            &cancel,
            &mut |_| {},
        ))
        .unwrap();
        server.join().unwrap();
        assert_eq!(fs::read(&victim).unwrap(), b"working image");
        verify_file(&root.path().join(&whole.name), &whole, &config.public_key).unwrap();
    }

    // Review finding 2: a second installation never interleaves with the first.
    #[cfg(unix)]
    #[test]
    fn a_second_appimage_install_waits_for_the_first() {
        use fs2::FileExt;
        let swap = SwapFixture::new();
        let held = fs::File::open(&swap.applications).unwrap();
        held.lock_exclusive().unwrap();
        let result = swap.replace(|_| panic!("the swap must not start while another runs"));
        assert!(result.unwrap_err().to_string().contains("Another"));
        assert_eq!(fs::read(&swap.appimage).unwrap(), OLD_IMAGE);
        assert!(swap.leftovers().is_empty(), "{:?}", swap.leftovers());
        assert!(!swap.journal.exists());
        held.unlock().unwrap();
        // A backup an older version left under the fixed name neither blocks nor is touched.
        let stale = swap.applications.join(".LocalSR.AppImage.before-update");
        fs::write(&stale, b"stale").unwrap();
        swap.replace(|_| Ok(())).unwrap();
        assert_eq!(fs::read(&stale).unwrap(), b"stale");
    }

    // A failure or a crash at any step of the AppImage install (verified staging, backup,
    // journal, swap, startup check, clean-up) leaves exactly one working image at its path
    // after the next start's recovery: the old one until the new one was accepted.
    #[cfg(unix)]
    #[test]
    fn the_appimage_install_survives_a_failure_or_a_crash_at_every_step() {
        use fault::Mode;
        use std::os::unix::fs::PermissionsExt;
        use update_storage::fault;
        for accept in [true, false] {
            fault::arm(None);
            let steps = {
                let swap = SwapFixture::new();
                let ran = swap.replace(|_| {
                    if accept {
                        Ok(())
                    } else {
                        Err(AppError::Worker("startup failed".into()))
                    }
                });
                assert_eq!(ran.is_ok(), accept);
                fault::seen()
            };
            let accepted_at = steps.iter().position(|step| *step == "backup-remove");
            assert_eq!(accepted_at.is_some(), accept, "{steps:?}");
            for (index, step) in steps.iter().enumerate() {
                for mode in [Mode::Fail, Mode::Crash] {
                    let why = format!("{step} #{index} {mode:?} accept={accept}");
                    let swap = SwapFixture::new();
                    fault::arm(Some((index, mode)));
                    let _ = fault::crashed(|| {
                        swap.replace(|_| {
                            if accept {
                                Ok(())
                            } else {
                                Err(AppError::Worker("startup failed".into()))
                            }
                        })
                    });
                    fault::arm(None);
                    fault::recover_swap(&swap.journal)
                        .unwrap_or_else(|error| panic!("{why}: recovery failed: {error}"));
                    let kept = accepted_at.is_some_and(|at| index > at);
                    let expected = if kept { &swap.data[..] } else { OLD_IMAGE };
                    assert_eq!(fs::read(&swap.appimage).unwrap(), expected, "{why}");
                    let mode_bits = fs::metadata(&swap.appimage).unwrap().permissions().mode();
                    assert_eq!(mode_bits & 0o111, 0o111, "{why}: not executable");
                    assert!(!swap.journal.exists(), "{why}: journal left");
                    assert!(swap.leftovers().is_empty(), "{why}: {:?}", swap.leftovers());
                }
            }
        }
    }

    // Review finding 4: the working image keeps its name, and an interrupted swap is rolled
    // back at the next start.
    #[cfg(unix)]
    #[test]
    fn the_working_image_keeps_its_name_while_the_new_one_is_checked() {
        let swap = SwapFixture::new();
        swap.replace(|path| {
            assert_eq!(path, swap.appimage.as_path());
            // The journal names a backup that still holds the old image.
            let journal: serde_json::Value =
                serde_json::from_slice(&fs::read(&swap.journal).unwrap()).unwrap();
            let backup = PathBuf::from(journal["backup"].as_str().unwrap());
            assert_eq!(fs::read(backup).unwrap(), OLD_IMAGE);
            Err(AppError::Worker("startup failed".into()))
        })
        .unwrap_err();
        assert_eq!(fs::read(&swap.appimage).unwrap(), OLD_IMAGE);
    }

    #[cfg(unix)]
    #[test]
    fn an_interrupted_appimage_swap_is_rolled_back_at_the_next_start() {
        let swap = SwapFixture::new();
        let backup = swap
            .applications
            .join(".LocalSR.AppImage.before-update-test");
        let staged = swap.applications.join(".LocalSR.AppImage.update-test");
        let journal = serde_json::json!({
            "current": swap.appimage, "backup": backup, "staged": staged,
        });
        fs::write(&swap.journal, journal.to_string()).unwrap();
        // Killed after the new image took the name, before it was accepted.
        fs::hard_link(&swap.appimage, &backup).unwrap();
        fs::write(&staged, &swap.data).unwrap();
        fs::rename(&staged, &swap.appimage).unwrap();
        assert_eq!(
            update_storage::fault::recover_swap(&swap.journal).unwrap(),
            Some(swap.appimage.clone())
        );
        assert_eq!(fs::read(&swap.appimage).unwrap(), OLD_IMAGE);
        assert!(swap.leftovers().is_empty(), "{:?}", swap.leftovers());
        assert!(!swap.journal.exists());

        // Killed while staging: only the staged copy is removed.
        fs::write(&swap.journal, journal.to_string()).unwrap();
        fs::write(&staged, b"half").unwrap();
        assert_eq!(
            update_storage::fault::recover_swap(&swap.journal).unwrap(),
            None
        );
        assert_eq!(fs::read(&swap.appimage).unwrap(), OLD_IMAGE);
        assert!(swap.leftovers().is_empty(), "{:?}", swap.leftovers());

        // A journal naming files outside the image's folder is refused untouched.
        let outside = swap.root.path().join("other");
        fs::write(&outside, b"other").unwrap();
        let foreign = serde_json::json!({
            "current": swap.appimage, "backup": outside, "staged": staged,
        });
        fs::write(&swap.journal, foreign.to_string()).unwrap();
        assert!(update_storage::fault::recover_swap(&swap.journal).is_err());
        assert_eq!(fs::read(&outside).unwrap(), b"other");
        assert_eq!(fs::read(&swap.appimage).unwrap(), OLD_IMAGE);
    }

    #[test]
    fn only_a_running_appimage_is_replaced() {
        let root = tempfile::tempdir().unwrap();
        let image = root.path().join("LocalSR.AppImage");
        let mount = root.path().join(".mount_LocalSR");
        let executable = mount.join("usr/bin/localsr-next");
        fs::create_dir_all(executable.parent().unwrap()).unwrap();
        fs::write(&image, b"image").unwrap();
        fs::write(&executable, b"host").unwrap();
        let elsewhere = root.path().join("localsr-next");
        fs::write(&elsewhere, b"host").unwrap();
        // The result is the canonical path (macOS temp folders sit behind a /var link).
        assert_eq!(
            running_appimage(Some(image.clone()), Some(mount.clone()), &executable),
            Some(image.canonicalize().unwrap())
        );
        // An APPIMAGE variable alone, a host outside the mount, a relative or missing
        // image: nothing to replace.
        assert_eq!(
            running_appimage(Some(image.clone()), None, &executable),
            None
        );
        assert_eq!(
            running_appimage(Some(image.clone()), Some(mount.clone()), &elsewhere),
            None
        );
        assert_eq!(
            running_appimage(
                Some(PathBuf::from("LocalSR.AppImage")),
                Some(mount.clone()),
                &executable
            ),
            None
        );
        assert_eq!(
            running_appimage(Some(root.path().join("gone")), Some(mount), &executable),
            None
        );
    }

    #[test]
    fn finishing_an_install_records_the_version_and_drops_only_the_downloads() {
        let root = tempfile::tempdir().unwrap();
        let backup = root.path().join("backups/before-install");
        let downloads = root.path().join("updates");
        fs::create_dir_all(&backup).unwrap();
        fs::create_dir_all(&downloads).unwrap();
        fs::write(downloads.join("application.update"), b"installer").unwrap();
        fs::write(backup.join("settings.json"), b"{}").unwrap();
        finish_install(&backup, "0.3.7-beta", &downloads).unwrap();
        assert_eq!(
            fs::read_to_string(backup.join("installed-version.txt")).unwrap(),
            "0.3.7-beta"
        );
        assert!(backup.join("settings.json").is_file());
        assert!(!downloads.exists());
    }

    // Review finding 5: Windows records success only once the new version runs.
    #[test]
    fn a_windows_install_is_confirmed_by_the_version_that_starts_next() {
        let root = tempfile::tempdir().unwrap();
        let next = root.path().join("next");
        let backup = next.join("backups/before-install");
        let downloads = next.join("updates");
        fs::create_dir_all(&backup).unwrap();
        fs::create_dir_all(&downloads).unwrap();
        fs::write(downloads.join(APPLICATION_FILE), b"installer").unwrap();
        record_pending_install(&next, "0.3.7-beta", &downloads, &backup).unwrap();
        // The installer has not run yet: nothing is recorded or removed.
        assert!(downloads.join(APPLICATION_FILE).is_file());
        assert!(!backup.join("installed-version.txt").exists());

        // The old version starts again: the installer failed or was cancelled.
        assert!(!confirm_pending_install(&next, "0.3.6-beta"));
        assert!(downloads.join(APPLICATION_FILE).is_file());
        assert!(!backup.join("installed-version.txt").exists());
        assert!(next.join(PENDING_INSTALL).exists());

        record_pending_install(&next, "0.3.7-beta", &downloads, &backup).unwrap();
        assert!(confirm_pending_install(&next, "0.3.7-beta"));
        assert_eq!(
            fs::read_to_string(backup.join("installed-version.txt")).unwrap(),
            "0.3.7-beta"
        );
        assert!(!downloads.exists());
        assert!(!confirm_pending_install(&next, "0.3.7-beta"));
    }

    // A crash or a failure at any step of recording or confirming a Windows install
    // leaves either a retry (marker and downloads kept) or the confirmed state; the next
    // starts always reach the confirmed state once the new version runs.
    #[test]
    fn every_crash_point_of_the_windows_install_marker_retries_or_confirms() {
        use update_storage::fault::{self, Mode};
        struct Folders {
            _root: tempfile::TempDir,
            next: PathBuf,
            backup: PathBuf,
            downloads: PathBuf,
        }
        let folders = || {
            let root = tempfile::tempdir().unwrap();
            let next = root.path().join("next");
            let backup = next.join("backups/before-install");
            let downloads = next.join("updates");
            fs::create_dir_all(&backup).unwrap();
            fs::create_dir_all(&downloads).unwrap();
            fs::write(downloads.join(APPLICATION_FILE), b"installer").unwrap();
            Folders {
                _root: root,
                next,
                backup,
                downloads,
            }
        };
        let record =
            |f: &Folders| record_pending_install(&f.next, "0.3.8-beta", &f.downloads, &f.backup);
        let confirmed = |f: &Folders| {
            fs::read_to_string(f.backup.join("installed-version.txt")).ok()
                == Some("0.3.8-beta".into())
                && !f.downloads.exists()
                && !f.next.join(PENDING_INSTALL).exists()
        };
        let retry = |f: &Folders| {
            f.downloads.join(APPLICATION_FILE).is_file()
                && !f.backup.join("installed-version.txt").exists()
        };
        let stray = |f: &Folders| {
            fs::read_dir(&f.next)
                .unwrap()
                .map(|e| e.unwrap().file_name().into_string().unwrap())
                .filter(|name| name != "backups" && name != "updates" && name != PENDING_INSTALL)
                .collect::<Vec<_>>()
        };

        // Record, then confirm: the steps of a whole successful install.
        let whole = folders();
        fault::arm(None);
        record(&whole).unwrap();
        assert!(confirm_pending_install(&whole.next, "0.3.8-beta"));
        let steps = fault::seen();
        for expected in [
            "atomic-rename",
            "confirm-version",
            "confirm-downloads",
            "confirm-marker",
        ] {
            assert!(steps.contains(&expected), "{steps:?} lacks {expected}");
        }
        assert!(confirmed(&whole));
        for (index, step) in steps.iter().enumerate() {
            for mode in [Mode::Fail, Mode::Crash] {
                let why = format!("{step} #{index} {mode:?}");
                let f = folders();
                fault::arm(Some((index, mode)));
                let _ = fault::crashed(|| {
                    let recorded = record(&f);
                    if recorded.is_ok() {
                        // The old version is still running: the installer did not finish.
                        assert!(!confirm_pending_install(&f.next, "0.3.7-beta"));
                        confirm_pending_install(&f.next, "0.3.8-beta");
                    }
                });
                fault::arm(None);
                let marker = f.next.join(PENDING_INSTALL);
                if confirmed(&f) {
                    // Nothing left to do.
                } else if marker.exists() {
                    // A marker is always a whole, valid one.
                    assert!(
                        serde_json::from_slice::<PendingInstall>(&fs::read(&marker).unwrap())
                            .is_ok(),
                        "{why}"
                    );
                    // The downloads may already be gone: confirming again is harmless.
                } else {
                    // Recording never finished: the downloads wait for another try.
                    assert!(retry(&f), "{why}: neither a retry nor confirmed");
                }
                // The old version starts again, then the new one: a retry stays a
                // retry, and the confirmation completes.
                let old_start = confirm_pending_install(&f.next, "0.3.7-beta");
                assert!(!old_start || confirmed(&f), "{why}");
                if marker.exists() {
                    assert!(
                        confirm_pending_install(&f.next, "0.3.8-beta") || confirmed(&f),
                        "{why}"
                    );
                    assert!(confirmed(&f), "{why}");
                }
                assert_eq!(stray(&f), Vec::<String>::new(), "{why}");
            }
        }
    }

    #[test]
    fn round2_confirmation_retries_failed_cleanup() {
        let root = tempfile::tempdir().unwrap();
        let next = root.path();
        let backup = next.join("backups/before-install");
        let downloads = next.join("updates");
        fs::create_dir_all(&backup).unwrap();
        fs::write(&downloads, b"blocks directory removal").unwrap();
        record_pending_install(next, "0.3.7-beta", &downloads, &backup).unwrap();
        assert!(!confirm_pending_install(next, "0.3.7-beta"));
        assert!(next.join(PENDING_INSTALL).exists());
        fs::remove_file(&downloads).unwrap();
        fs::create_dir(&downloads).unwrap();
        assert!(confirm_pending_install(next, "0.3.7-beta"));
    }

    #[test]
    fn round2_install_space_is_authenticated() {
        let (config, mut contract) = statement_contract();
        contract.unpacked_size = 1;
        assert!(verify_statement(
            &config,
            "linux-x86_64-rocm-native",
            "beta",
            "0.3.7-beta",
            "0.3.6-beta",
            &contract
        )
        .is_err());
    }

    #[test]
    fn round4_a_missing_versioned_engine_is_fetched_again_not_replaced_by_the_bundle() {
        let (mut config, contract) = statement_contract();
        config.engine_id.clone_from(&contract.engine_id);
        let id = contract.engine_id.clone();
        let root = tempfile::tempdir().unwrap();
        let downloads = root.path().join("downloads");
        fs::create_dir_all(&downloads).unwrap();
        let (engine, _) = engine_fixture(&downloads);
        let contract = ReleaseContract {
            engine: Some(EngineUpdate {
                id: id.clone(),
                ..engine
            }),
            ..contract
        };
        // An earlier update delivered this engine; its folder is gone and only an older
        // bundled engine is left, which would pass the handshake.
        fs::write(root.path().join("active-engine.txt"), &id).unwrap();
        assert!(engine_needs_package(root.path(), &config, &id));
        let worker = prepare_update_engine(&config, &contract, &downloads, root.path(), || {
            panic!("the bundled engine must not stand in for the delivered one")
        })
        .unwrap();
        assert_eq!(fs::read(&worker).unwrap(), b"worker");
        assert!(worker.starts_with(root.path().join("engines").join(&id)));
        // Present again, or never delivered: the host's own engine is reused.
        assert!(!engine_needs_package(root.path(), &config, &id));
        fs::remove_file(&worker).unwrap();
        fs::remove_file(root.path().join("active-engine.txt")).unwrap();
        assert!(!engine_needs_package(root.path(), &config, &id));
    }

    #[test]
    fn unchanged_engine_does_not_install_or_copy_any_engine_files() {
        let (mut config, contract) = statement_contract();
        config.engine_id.clone_from(&contract.engine_id);
        let root = tempfile::tempdir().unwrap();
        let worker = root.path().join("bundled/localsr-worker");
        assert_eq!(
            prepare_update_engine(
                &config,
                &contract,
                &root.path().join("missing-downloads"),
                root.path(),
                || Ok(worker.clone())
            )
            .unwrap(),
            worker
        );
        assert!(!root.path().join("engines").exists());
        let mut incompatible = contract.clone();
        incompatible.protocol += 1;
        assert!(validate_contract(&config, "beta", "0.3.7-beta", &incompatible).is_err());
    }

    fn statement_contract() -> (UpdateConfig, ReleaseContract) {
        let value = release_fixture();
        let config = UpdateConfig {
            public_key: value["public_key"].as_str().unwrap().into(),
            feed: "https://herrei.github.io/localsr/updates".into(),
            backend: "rocm".into(),
            engine_id: "linux-x86_64-rocm-old".into(),
            kind: "native".into(),
        };
        let root = tempfile::tempdir().unwrap();
        let (engine, _) = engine_fixture(root.path());
        let contract = ReleaseContract {
            channel: "beta".into(),
            backend: "rocm".into(),
            kind: "native".into(),
            protocol: PROTOCOL_VERSION,
            engine_id: "linux-x86_64-rocm-0123456789ab".into(),
            size: 21,
            unpacked_size: 1024,
            sha256: value["package"]["sha256"].as_str().unwrap().into(),
            parts: Vec::new(),
            statement_v2: value["statement_v2"].as_str().unwrap().into(),
            statement_v2_signature: value["statement_v2_signature"].as_str().unwrap().into(),
            engine: Some(engine),
        };
        (config, contract)
    }

    // Review finding 6: the feed's own fields count only when the signed statement says so.
    #[test]
    fn a_signed_release_statement_binds_what_the_feed_says() {
        let target = "linux-x86_64-rocm-native";
        let check = |config: &UpdateConfig,
                     target: &str,
                     channel: &str,
                     version: &str,
                     installed: &str,
                     contract: &ReleaseContract| {
            verify_statement(config, target, channel, version, installed, contract)
        };
        let (config, contract) = statement_contract();
        check(
            &config,
            target,
            "beta",
            "0.3.7-beta",
            "0.3.6-beta",
            &contract,
        )
        .unwrap();
        // A relabelled version, channel or edition.
        assert!(check(
            &config,
            target,
            "beta",
            "0.3.8-beta",
            "0.3.6-beta",
            &contract
        )
        .is_err());
        assert!(check(
            &config,
            target,
            "stable",
            "0.3.7-beta",
            "0.3.6-beta",
            &contract
        )
        .is_err());
        assert!(check(
            &config,
            "linux-x86_64-cuda-native",
            "beta",
            "0.3.7-beta",
            "0.3.6-beta",
            &contract
        )
        .is_err());
        let mut other = config.clone();
        other.backend = "cuda".into();
        assert!(check(
            &other,
            target,
            "beta",
            "0.3.7-beta",
            "0.3.6-beta",
            &contract
        )
        .is_err());
        let mut other = config.clone();
        other.kind = "portable".into();
        assert!(check(
            &other,
            target,
            "beta",
            "0.3.7-beta",
            "0.3.6-beta",
            &contract
        )
        .is_err());
        // An older or other package or engine.
        for change in 0..4 {
            let mut changed = contract.clone();
            match change {
                0 => changed.engine_id = "linux-x86_64-rocm-ffffffffffff".into(),
                1 => changed.sha256 = "f".repeat(64),
                2 => changed.engine.as_mut().unwrap().manifest.sha256 = "f".repeat(64),
                _ => changed.engine = None,
            }
            assert!(check(
                &config,
                target,
                "beta",
                "0.3.7-beta",
                "0.3.6-beta",
                &changed
            )
            .is_err());
        }
        // Not newer than what is installed.
        assert!(check(
            &config,
            target,
            "beta",
            "0.3.7-beta",
            "0.3.7-beta",
            &contract
        )
        .is_err());
        assert!(check(&config, target, "beta", "0.3.7-beta", "0.4.0", &contract).is_err());
        // A rewritten statement under the old signature, or none at all.
        let mut forged = contract.clone();
        let text = String::from_utf8(
            base64::engine::general_purpose::STANDARD
                .decode(&contract.statement_v2)
                .unwrap(),
        )
        .unwrap()
        .replace("0.3.7-beta", "0.3.9-beta");
        forged.statement_v2 = base64::engine::general_purpose::STANDARD.encode(text);
        assert!(check(&config, target, "beta", "0.3.9-beta", "0.3.6-beta", &forged).is_err());
        let mut unsigned = contract.clone();
        unsigned.statement_v2.clear();
        unsigned.statement_v2_signature.clear();
        assert!(check(
            &config,
            target,
            "beta",
            "0.3.7-beta",
            "0.3.6-beta",
            &unsigned
        )
        .is_err());
    }

    /// The fixture's package split into two parts, with the matching signed statement.
    fn split_statement_contract() -> (UpdateConfig, ReleaseContract) {
        let (config, mut contract) = statement_contract();
        let split = &release_fixture()["split"];
        contract.parts = split["parts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|part| DownloadFile {
                name: part["name"].as_str().unwrap().into(),
                url: format!("https://example.test/{}", part["name"].as_str().unwrap()),
                size: part["size"].as_u64().unwrap(),
                sha256: part["sha256"].as_str().unwrap().into(),
                signature: String::new(),
            })
            .collect();
        contract.statement_v2 = split["statement_v2"].as_str().unwrap().into();
        contract.statement_v2_signature = split["statement_v2_signature"].as_str().unwrap().into();
        (config, contract)
    }

    // The signed statement also covers the package size and every part's name, size and
    // digest, so a feed that inflates them is refused when it is read, before any download.
    #[test]
    fn a_changed_signed_size_or_part_list_is_refused_before_any_download() {
        let check = |config: &UpdateConfig, contract: &ReleaseContract| {
            verify_statement(
                config,
                "linux-x86_64-rocm-native",
                "beta",
                "0.3.7-beta",
                "0.3.6-beta",
                contract,
            )
        };
        let (config, contract) = statement_contract();
        check(&config, &contract).unwrap();
        let (_, split) = split_statement_contract();
        check(&config, &split).unwrap();
        for case in 0..20 {
            let mut changed = if case < 9 {
                contract.clone()
            } else {
                split.clone()
            };
            match case {
                0 => changed.size += 1,
                1 => changed.engine.as_mut().unwrap().manifest.name.push('x'),
                2 => changed.engine.as_mut().unwrap().manifest.size += 1,
                3 => changed.engine.as_mut().unwrap().parts[0].name.push('x'),
                4 => changed.engine.as_mut().unwrap().parts[0].size += 1,
                5 => changed.engine.as_mut().unwrap().parts[0].sha256 = "0".repeat(64),
                6 => {
                    let engine = changed.engine.as_mut().unwrap();
                    engine.parts.push(engine.parts[0].clone());
                }
                7 => changed.engine.as_mut().unwrap().parts.clear(),
                // The unsplit package gains a part it was not signed with.
                8 => changed.parts = split.parts.clone(),
                9 => changed.parts[0].name.push('x'),
                10 => changed.parts[0].size += 1,
                11 => changed.parts[1].size -= 1,
                12 => changed.parts[1].sha256 = "0".repeat(64),
                13 => changed.parts.swap(0, 1),
                14 => changed.parts.push(changed.parts[1].clone()),
                15 => {
                    changed.parts.pop();
                }
                16 => changed.parts.clear(),
                17 => changed.size += 1,
                18 => changed.sha256 = "f".repeat(64),
                _ => changed.engine = None,
            }
            assert!(
                check(&config, &changed).is_err(),
                "case {case} was accepted"
            );
        }
    }

    // Statements of the old format, or none, are no longer enough: the entry must carry
    // the format 2 statement.
    #[test]
    fn a_release_without_the_format_2_statement_is_refused() {
        let (config, contract) = statement_contract();
        let check = |contract: &ReleaseContract| {
            verify_statement(
                &config,
                "linux-x86_64-rocm-native",
                "beta",
                "0.3.7-beta",
                "0.3.6-beta",
                contract,
            )
        };
        let mut missing = contract.clone();
        missing.statement_v2.clear();
        assert!(check(&missing).is_err());
        let mut unsigned = contract.clone();
        unsigned.statement_v2_signature.clear();
        assert!(check(&unsigned).is_err());
        // Statements signed by the right key that are not format 2: the format 1 statement
        // of an old feed, one with a field this client does not know, one lacking a field.
        // Each must be refused for what it says, not for a bad signature.
        let rejected = &release_fixture()["rejected"];
        for name in ["format_1", "extra_field", "missing_parts"] {
            let mut other = contract.clone();
            other.statement_v2 = rejected[name]["statement"].as_str().unwrap().into();
            other.statement_v2_signature = rejected[name]["signature"].as_str().unwrap().into();
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(&other.statement_v2)
                .unwrap();
            key(&config.public_key)
                .unwrap()
                .verify(
                    &bytes,
                    &signature(&other.statement_v2_signature).unwrap(),
                    false,
                )
                .unwrap();
            assert!(check(&other).is_err(), "{name} was accepted");
        }
    }

    // An engine package that names another id than the signed engine id is refused even
    // when the installed engine is unchanged, since that id decides what is downloaded.
    #[test]
    fn an_engine_package_must_carry_the_signed_engine_id() {
        let (mut config, mut contract) = statement_contract();
        config.engine_id.clone_from(&contract.engine_id);
        validate_contract(&config, "beta", "0.3.7-beta", &contract).unwrap();
        contract.engine.as_mut().unwrap().id = "linux-x86_64-rocm-ffffffffffff".into();
        assert!(validate_contract(&config, "beta", "0.3.7-beta", &contract).is_err());
        config.engine_id = "linux-x86_64-rocm-old".into();
        assert!(validate_contract(&config, "beta", "0.3.7-beta", &contract).is_err());
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

    // Round 3, finding 1: a cancelled installer leaves the next engine's id in the shared
    // file; the host still runs the engine it was built for.
    #[test]
    fn round3_a_cancelled_update_does_not_hide_the_engine_this_host_was_built_for() {
        let root = tempfile::tempdir().unwrap();
        let paths = crate::paths::AppPaths::under(root.path());
        let worker = |id: &str| {
            let path = paths
                .next_root
                .join("engines")
                .join(id)
                .join("engine")
                .join(if cfg!(windows) {
                    "localsr-worker.exe"
                } else {
                    "localsr-worker"
                });
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, b"engine").unwrap();
            path
        };
        let current = worker("windows-x86_64-cuda-e2");
        fs::write(
            paths.next_root.join("active-engine.txt"),
            b"windows-x86_64-cuda-e3",
        )
        .unwrap();
        assert_eq!(
            active_engine_for_distribution(&paths, false, "windows-x86_64-cuda-e2"),
            Some(current)
        );
        // The engine of a release that never started is not this host's engine.
        assert_eq!(
            active_engine_for_distribution(&paths, false, "windows-x86_64-cuda-e4"),
            None
        );
        // The identity is a file name, never a path.
        assert_eq!(
            active_engine_for_distribution(&paths, false, "../escape"),
            None
        );
    }

    // Round 3, finding 5: every engine file the feed names is a plain, distinct name that
    // cannot replace the application package or a partial download.
    #[test]
    fn round3_engine_file_names_are_safe_distinct_and_never_the_application_file() {
        let (_, file, config) = fixture();
        let contract = |manifest: &str, parts: &[&str]| ReleaseContract {
            channel: "beta".into(),
            backend: "rocm".into(),
            kind: "portable".into(),
            protocol: PROTOCOL_VERSION,
            engine_id: "abc".into(),
            size: file.size,
            unpacked_size: 1024,
            sha256: file.sha256.clone(),
            parts: Vec::new(),
            statement_v2: String::new(),
            statement_v2_signature: String::new(),
            engine: Some(EngineUpdate {
                id: "abc".into(),
                manifest: part(manifest, 100),
                parts: parts.iter().map(|name| part(name, 10)).collect(),
            }),
        };
        let valid = |manifest: &str, parts: &[&str]| {
            validate_contract(&config, "beta", "0.0.13-beta.1", &contract(manifest, parts)).is_ok()
        };
        assert!(valid(
            "m.engine-payload.json",
            &["m.part-0001", "m.part-0002"]
        ));
        assert!(!valid("m.engine-payload.json", &[APPLICATION_FILE]));
        assert!(!valid(APPLICATION_FILE, &["m.part-0001"]));
        assert!(!valid(
            "m.engine-payload.json",
            &["m.part-0001", "m.part-0001"]
        ));
        assert!(!valid("m.part-0001", &["m.part-0001"]));
        assert!(!valid("m.engine-payload.json", &["../m.part-0001"]));
        assert!(!valid("m.engine-payload.json", &["m.part-0001.partial"]));
        let mut empty_manifest = contract("m.engine-payload.json", &["m.part-0001"]);
        empty_manifest.engine.as_mut().unwrap().manifest.size = 0;
        assert!(validate_contract(&config, "beta", "0.0.13-beta.1", &empty_manifest).is_err());
        // Application parts and engine files share one folder and so one namespace.
        let mut clash = contract("m.engine-payload.json", &["app.part-0001"]);
        clash.parts = vec![
            part("app.part-0001", file.size / 2 + 1),
            part("app.part-0002", file.size / 2),
        ];
        clash.size = file.size / 2 + 1 + file.size / 2;
        assert!(validate_contract(&config, "beta", "0.0.13-beta.1", &clash).is_err());
    }

    // Round 3, finding 9: a link in $APPIMAGE is followed, so the swap replaces the image
    // and not the link.
    #[cfg(unix)]
    #[test]
    fn round3_a_symlinked_appimage_variable_names_the_file_it_points_to() {
        let root = tempfile::tempdir().unwrap();
        let root_path = root.path().canonicalize().unwrap();
        let image = root_path.join("LocalSR.AppImage");
        let link = root_path.join("localsr");
        let mount = root_path.join(".mount_LocalSR");
        let executable = mount.join("usr/bin/localsr-next");
        fs::create_dir_all(executable.parent().unwrap()).unwrap();
        fs::write(&image, b"image").unwrap();
        fs::write(&executable, b"host").unwrap();
        std::os::unix::fs::symlink(&image, &link).unwrap();
        assert_eq!(
            running_appimage(Some(link), Some(mount), &executable),
            Some(image)
        );
    }

    // Round 3, finding 2: the process of an interrupted swap exits after its rollback,
    // also when the restored executable cannot be started.
    #[test]
    fn round3_the_unaccepted_process_exits_even_when_the_restored_one_will_not_start() {
        let root = tempfile::tempdir().unwrap();
        assert!(relaunch(&root.path().join("missing")));
    }

    #[cfg(unix)]
    #[test]
    fn round3_a_link_to_the_restored_executable_is_the_restored_executable() {
        let root = tempfile::tempdir().unwrap();
        let image = root.path().join("LocalSR.AppImage");
        let link = root.path().join("localsr");
        fs::write(&image, b"image").unwrap();
        std::os::unix::fs::symlink(&image, &link).unwrap();
        assert!(same_file(&link, &image));
        assert!(!same_file(&root.path().join("other"), &image));
    }

    // Round 3, finding 4: once the new application is in place, a failed service restart
    // ends this process instead of returning into a session without worker or database.
    #[test]
    fn round3_a_failed_service_restart_after_the_swap_exits_the_process() {
        assert_eq!(restart_plan(Ok(true)), RestartPlan::Exit);
        assert_eq!(restart_plan(Ok(false)), RestartPlan::Respawn);
        assert_eq!(
            restart_plan(Err(invalid("systemctl failed"))),
            RestartPlan::Exit
        );
    }

    // Round 3, finding 7: a failed install resumes the worker only when the old one ended,
    // and always clears the flag that blocks workers.
    #[test]
    fn the_active_engine_is_found_when_its_file_ends_with_a_line_break() {
        let root = tempfile::tempdir().unwrap();
        let paths = crate::paths::AppPaths::under(root.path());
        let engine = paths
            .next_root
            .join("engines/edited/engine")
            .join(if cfg!(windows) {
                "localsr-worker.exe"
            } else {
                "localsr-worker"
            });
        fs::create_dir_all(engine.parent().unwrap()).unwrap();
        fs::write(&engine, b"engine").unwrap();
        fs::write(paths.next_root.join("active-engine.txt"), b"edited\r\n").unwrap();
        assert_eq!(
            active_engine_for_distribution(&paths, false, ""),
            Some(engine)
        );
    }

    #[test]
    fn round3_resuming_after_a_failed_install_never_doubles_the_worker() {
        let root = tempfile::tempdir().unwrap();
        let state = AppState::from_paths(crate::paths::AppPaths::under(root.path())).unwrap();
        let started = std::cell::Cell::new(0);
        state.worker.shutting_down.store(true, Ordering::SeqCst);
        state.worker.pid.store(4242, Ordering::SeqCst);
        resume_after_failed_install(
            &state,
            || Ok(()),
            || {
                started.set(started.get() + 1);
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(started.get(), 0, "the old worker is still alive");
        assert!(!state.worker.shutting_down.load(Ordering::SeqCst));

        state.worker.shutting_down.store(true, Ordering::SeqCst);
        state.worker.pid.store(0, Ordering::SeqCst);
        let failed = resume_after_failed_install(
            &state,
            || Err(invalid("database")),
            || {
                started.set(started.get() + 1);
                Ok(())
            },
        );
        assert!(failed.is_err());
        assert_eq!(
            started.get(),
            1,
            "a database error must not skip the worker"
        );
        assert!(!state.worker.shutting_down.load(Ordering::SeqCst));
    }

    // Round 3, finding 3: the portable swap reads the installed version at the path it
    // replaces, not at whatever /proc/self/exe says by then.
    #[cfg(unix)]
    #[test]
    fn round3_a_portable_host_is_replaced_at_the_one_path_it_was_given() {
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let current = root.path().join("localsr-next");
        let new_host = root.path().join("unpacked-localsr-next");
        let journal = root.path().join("swap-journal.json");
        fs::write(&current, OLD_IMAGE).unwrap();
        fs::set_permissions(&current, fs::Permissions::from_mode(0o755)).unwrap();
        fs::write(&new_host, b"#!/bin/sh\nprintf '0.3.7-beta\\n'\n").unwrap();
        replace_portable_host(&current, &new_host, &journal, "0.3.5-beta", |_| Ok(())).unwrap_err();
        replace_portable_host(&current, &new_host, &journal, "0.3.7-beta", |_| Ok(())).unwrap();
        assert_eq!(fs::read(&current).unwrap(), fs::read(&new_host).unwrap());
        assert!(!journal.exists());
    }

    // Round 3, finding 8: a retry is not charged again for what is already downloaded.
    #[test]
    fn round3_a_retry_is_not_charged_for_files_it_already_holds() {
        let root = tempfile::tempdir().unwrap();
        let complete = part("complete", 5);
        let short = part("short", 5);
        let absent = part("absent", 7);
        fs::write(root.path().join("complete"), b"12345").unwrap();
        fs::write(root.path().join("short"), b"123").unwrap();
        assert_eq!(
            already_downloaded(root.path(), [&complete, &short, &absent].into_iter()),
            5
        );
    }
}
