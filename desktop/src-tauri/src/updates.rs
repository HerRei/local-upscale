//! Explicit, signed updates for every direct edition. A build checks a feed only when
//! it was compiled with an update identity: the public verification key, its backend,
//! its package kind and its engine id (scripts/release_targets.py --update-env).
//! The .deb (kind `deb`) is the exception that only reports updates: apt owns its files.
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
    collections::{HashMap, HashSet},
    fs,
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tauri::{AppHandle, Emitter, State};
use tauri_plugin_updater::UpdaterExt;

/// The package kind of the .deb. apt installs, upgrades and removes its files under /usr,
/// so LocalSR never replaces them. It has no feed entry of its own: it reads the signed
/// `native` entry of the edition whose engine it ships, to report new versions and to
/// download engines into the user's app-data folder.
pub(crate) const DEBIAN_PACKAGE_KIND: &str = "deb";
const PACKAGE_MANAGER_UPDATES: &str =
    "This copy of LocalSR was installed as a .deb package. Download the new .deb from the download page and install it with apt.";

#[derive(Clone, Deserialize, Serialize)]
pub struct UpdateConfig {
    pub public_key: String,
    pub feed: String,
    pub backend: String,
    pub engine_id: String,
    pub kind: String,
}
impl UpdateConfig {
    pub(crate) fn load() -> Self {
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
    /// Whether apt, not LocalSR, installs this build's updates.
    pub(crate) fn installed_by_package_manager(&self) -> bool {
        self.kind == DEBIAN_PACKAGE_KIND
    }
    /// The identity this build looks up in the feed: its own, or for the .deb the
    /// `native` edition with the same key, backend and engine.
    pub(crate) fn feed_config(&self) -> Self {
        let mut config = self.clone();
        if config.installed_by_package_manager() {
            config.kind = "native".into();
        }
        config
    }
    pub(crate) fn target(&self) -> String {
        platform_key(
            std::env::consts::OS,
            std::env::consts::ARCH,
            &self.backend,
            &self.kind,
        )
    }
    pub(crate) fn enabled(&self) -> bool {
        !self.public_key.is_empty() && !self.backend.is_empty() && !self.engine_id.is_empty()
    }
}

/// The feed's platform key, `<os>-<arch>-<backend>-<kind>`, with "darwin" for macOS:
/// `windows-x86_64-cuda-native`, `linux-x86_64-rocm-native`, `darwin-aarch64-mps-native`.
/// scripts/release_targets.py writes the feed entries under the same keys.
pub(crate) fn platform_key(os: &str, arch: &str, backend: &str, kind: &str) -> String {
    let os = if os == "macos" { "darwin" } else { os };
    format!("{os}-{arch}-{backend}-{kind}")
}

/// Whether this build checks a signed update feed itself: a direct build with an updater
/// key, not a Store build. Only then do the launch check and the count exist.
fn checks_feed(config: &UpdateConfig, managed_by_store: bool) -> bool {
    config.enabled() && !managed_by_store
}

/// Whether this build is the .deb (compiled with `LOCALSR_UPDATE_KIND=deb`).
pub fn installed_by_package_manager() -> bool {
    UpdateConfig::load().installed_by_package_manager()
}

/// Downloading and installing an app update replaces the installed files, which apt owns
/// for the .deb.
fn require_self_update(config: &UpdateConfig) -> AppResult<()> {
    if config.installed_by_package_manager() {
        return Err(invalid(PACKAGE_MANAGER_UPDATES));
    }
    Ok(())
}

/// A newer signed release, for a build that apt updates: nothing to download in the app.
fn show_package_manager_update(status: &mut UpdateStatus) {
    status.stage = "available".into();
    status.size = 0;
    status.message = PACKAGE_MANAGER_UPDATES.into();
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
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct DownloadFile {
    pub name: String,
    pub url: String,
    pub size: u64,
    pub sha256: String,
    pub signature: String,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
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
    /// The .deb: apt installs updates, the app only reports them.
    pub managed_by_package_manager: bool,
    pub channel: String,
    pub target: String,
    pub stage: String,
    pub version: String,
    pub notes: String,
    pub discover_url: String,
    pub skipped_version: String,
    pub size: u64,
    pub downloaded: u64,
    pub message: String,
    pub settings_recovery: bool,
}
struct CheckedUpdate {
    update: tauri_plugin_updater::Update,
    contract: ReleaseContract,
    config: UpdateConfig,
    engine_backend: String,
    engine_id: String,
    engine_package: Option<EngineUpdate>,
    engine_unpacked_size: u64,
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
pub(crate) struct Busy<'a>(&'a AtomicBool);
impl Drop for Busy<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}
pub(crate) fn acquire(flag: &AtomicBool) -> AppResult<Busy<'_>> {
    flag.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .map_err(|_| invalid("An update operation is already running"))?;
    Ok(Busy(flag))
}
pub(crate) fn invalid(message: impl Into<String>) -> AppError {
    AppError::Validation(message.into())
}
pub(crate) fn key(value: &str) -> AppResult<minisign_verify::PublicKey> {
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(value)
        .map_err(|e| invalid(e.to_string()))?;
    minisign_verify::PublicKey::decode(
        std::str::from_utf8(&decoded).map_err(|e| invalid(e.to_string()))?,
    )
    .map_err(|e| invalid(e.to_string()))
}
pub(crate) fn signature(value: &str) -> AppResult<minisign_verify::Signature> {
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
    validate_contract_for_backend(config, &config.backend, channel, version, contract, false)?;
    if contract.engine_id != config.engine_id && contract.engine.is_none() {
        return Err(invalid("This release needs a different inference engine but supplies no compatible engine package"));
    }
    Ok(())
}

pub(crate) fn validate_contract_for_backend(
    config: &UpdateConfig,
    expected_backend: &str,
    channel: &str,
    version: &str,
    contract: &ReleaseContract,
    require_engine_package: bool,
) -> AppResult<()> {
    let version = semver::Version::parse(version).map_err(|e| invalid(e.to_string()))?;
    if !matches!(channel, "stable" | "beta")
        || contract.channel != channel
        || channel == "stable" && !version.pre.is_empty()
        || contract.backend != expected_backend
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
                        || file.name.ends_with(".part")
                        || !names.insert(file.name.as_str())
                })
            || engine
                .parts
                .iter()
                .any(|p| p.size == 0 || p.size >= 2 * 1024u64.pow(3))
        {
            return Err(invalid("Invalid split engine package"));
        }
    } else if require_engine_package {
        return Err(invalid("This release supplies no engine package"));
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
    verify_statement_for_entry(
        config,
        target,
        &config.backend,
        channel,
        version,
        installed,
        contract,
        true,
    )
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn verify_statement_for_entry(
    config: &UpdateConfig,
    target: &str,
    expected_backend: &str,
    channel: &str,
    version: &str,
    installed: &str,
    contract: &ReleaseContract,
    require_strictly_newer: bool,
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
        backend: expected_backend.into(),
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
    let parsed_version = semver::Version::parse(version).map_err(|e| invalid(e.to_string()))?;
    let parsed_installed = semver::Version::parse(installed).map_err(|e| invalid(e.to_string()))?;
    let valid_version = if require_strictly_newer {
        parsed_version > parsed_installed
    } else {
        parsed_version >= parsed_installed
    };
    if !valid_version {
        return Err(invalid(
            "This release is not newer than the installed version",
        ));
    }
    Ok(())
}
pub(crate) fn channel(state: &AppState) -> String {
    fs::read_to_string(state.paths.next_root.join("update-channel.json"))
        .ok()
        .filter(|s| matches!(s.as_str(), "stable" | "beta"))
        .unwrap_or("beta".into())
}
fn attach_status_metadata(state: &AppState, status: &mut UpdateStatus) {
    status.managed_by_package_manager = installed_by_package_manager();
    if let Ok(settings) = state.settings.lock() {
        status.skipped_version = settings.skipped_update_version.clone();
    }
    status.settings_recovery = crate::settings::needs_recovery(&state.paths);
}
fn publish(state: &AppState, app: &AppHandle, status: &mut UpdateStatus) {
    attach_status_metadata(state, status);
    if let Ok(mut stored) = state.updates.status.lock() {
        *stored = status.clone();
    }
    let _ = app.emit("update-status", status.clone());
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
    status.managed_by_package_manager = config.installed_by_package_manager();
    status.skipped_version = lock(&state.settings)?.skipped_update_version.clone();
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

const DISCOVER_URL: &str = "https://herrei.github.io/localsr/whats-new/";

fn validated_discover_url(value: Option<&str>) -> String {
    value
        .filter(|value| value.len() <= 2048)
        .and_then(|value| reqwest::Url::parse(value).ok())
        .filter(|url| {
            url.scheme() == "https"
                && url.username().is_empty()
                && url.password().is_none()
                && url.port_or_known_default() == Some(443)
                && (url.host_str() == Some("herrei.github.io")
                    || (url.host_str() == Some("github.com") && url.path().starts_with("/HerRei/")))
        })
        .map(|url| url.to_string())
        .unwrap_or_else(|| DISCOVER_URL.into())
}

#[tauri::command]
pub fn open_update_discover(state: State<'_, Arc<AppState>>) -> AppResult<()> {
    let status = lock(&state.updates.status)?;
    open::that_detached(validated_discover_url(Some(&status.discover_url)))?;
    Ok(())
}

fn store_skipped_version(state: &AppState, version: &str) -> AppResult<()> {
    let _io = lock(&state.settings_io)?;
    let previous = {
        let mut settings = lock(&state.settings)?;
        std::mem::replace(&mut settings.skipped_update_version, version.to_string())
    };
    if let Err(error) = state.write_settings() {
        lock(&state.settings)?.skipped_update_version = previous;
        return Err(error);
    }
    Ok(())
}

#[tauri::command]
pub fn skip_update(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<UpdateStatus> {
    let _busy = acquire(&state.updates.busy)?;
    let mut status = lock(&state.updates.status)?.clone();
    if status.stage != "available" || semver::Version::parse(&status.version).is_err() {
        return Err(invalid("No available update to skip"));
    }
    store_skipped_version(&state, &status.version)?;
    status.skipped_version = status.version.clone();
    publish(&state, &app, &mut status);
    crate::state::emit_state_changed(&app);
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
fn launch_check_allowed(settings: &crate::types::UiSettings) -> bool {
    settings.privacy_choice_made && settings.check_updates_at_launch
}

#[tauri::command]
pub async fn check_update_at_launch(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    channel: String,
) -> AppResult<UpdateStatus> {
    {
        let settings = lock(&state.settings)?;
        if !launch_check_allowed(&settings) {
            return Err(invalid("Automatic update checks are disabled"));
        }
    }
    check_update(state, app, channel).await
}

#[tauri::command]
pub async fn check_update(
    state: State<'_, Arc<AppState>>,
    app: AppHandle,
    channel: String,
) -> AppResult<UpdateStatus> {
    if !lock(&state.settings)?.privacy_choice_made {
        return Err(invalid("Choose update privacy settings first"));
    }
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
    // The .deb reads the native entry of its edition; status and count keep its own target.
    let installed = config;
    let config = installed.feed_config();
    let endpoint = format!("{}/{}.json", config.feed.trim_end_matches('/'), channel);
    let mut status = UpdateStatus {
        configured: true,
        channel: channel.clone(),
        target: installed.target(),
        stage: "checking".into(),
        ..Default::default()
    };
    publish(&state, &app, &mut status);
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
        send_usage_ping(&state, &app, &installed, &channel);
    }
    let update = match result {
        Ok(update) => update,
        Err(error) => {
            status.stage = "error".into();
            status.message = error.to_string();
            publish(&state, &app, &mut status);
            return Err(error);
        }
    };
    *lock(&state.updates.checked)? = None;
    let catalog_config = config.clone();
    let validated = (|| -> AppResult<()> {
        if let Some(update) = update {
            let contract: ReleaseContract = serde_json::from_value(
                update.raw_json["platforms"][config.target()]["localsr"].clone(),
            )?;
            validate_contract(&config, &channel, &update.version, &contract)?;
            let installed_version = app.package_info().version.to_string();
            verify_statement(
                &config,
                &config.target(),
                &channel,
                &update.version,
                &installed_version,
                &contract,
            )?;
            if installed.installed_by_package_manager() {
                // apt installs it: report the signed release, keep the engine catalog
                // current, and keep nothing to download or install.
                crate::engine_manager::update_catalog_from_feed(
                    &state,
                    &update.raw_json,
                    &config,
                    &channel,
                    &installed_version,
                );
                status.version = update.version.clone();
                status.notes = update.body.clone().unwrap_or_default();
                status.discover_url =
                    validated_discover_url(update.raw_json["discover_url"].as_str());
                show_package_manager_update(&mut status);
                return update_storage::write_atomic(
                    &state.paths.next_root.join("update-channel.json"),
                    channel.as_bytes(),
                );
            }
            let resolved_engine = resolve_update_engine_for_backend(
                &update.raw_json,
                &config,
                &contract,
                &channel,
                &update.version,
                &installed_version,
                &state.paths,
            )?;
            crate::engine_manager::update_catalog_from_feed(
                &state,
                &update.raw_json,
                &config,
                &channel,
                &installed_version,
            );
            status.version = update.version.clone();
            status.notes = update.body.clone().unwrap_or_default();
            status.discover_url = validated_discover_url(update.raw_json["discover_url"].as_str());
            status.size = contract.size
                + resolved_engine
                    .engine
                    .as_ref()
                    .filter(|e| {
                        engine_needs_package_for(
                            &state.paths.next_root,
                            &config,
                            &e.id,
                            &resolved_engine.backend,
                        )
                    })
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
                engine_backend: resolved_engine.backend,
                engine_id: resolved_engine.engine_id,
                engine_package: resolved_engine.engine,
                engine_unpacked_size: resolved_engine.unpacked_size,
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
        publish(&state, &app, &mut status);
        return Err(error);
    }
    if status.stage == "current" {
        if let Ok(feed_json) =
            crate::engine_manager::fetch_feed_json(&catalog_config, &channel).await
        {
            crate::engine_manager::update_catalog_from_feed(
                &state,
                &feed_json,
                &catalog_config,
                &channel,
                &app.package_info().version.to_string(),
            );
        }
    }
    publish(&state, &app, &mut status);
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

pub(crate) fn safe_file_name(name: &str) -> bool {
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
pub(crate) fn read_verified(
    path: &Path,
    file: &DownloadFile,
    public_key: &str,
) -> AppResult<Vec<u8>> {
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
pub(crate) fn install_engine_package_for(
    directory: &Path,
    engine: &EngineUpdate,
    public_key: &str,
    engine_dir: &Path,
    expected_os: &str,
    expected_arch: &str,
) -> AppResult<Vec<crate::engine_payload::InstalledEngineFile>> {
    let manifest = read_verified(
        &directory.join(&engine.manifest.name),
        &engine.manifest,
        public_key,
    )?;
    let parsed = crate::engine_payload::parse_manifest(&manifest)
        .map_err(|error| invalid(error.to_string()))?;
    if parsed.schema_version == 2
        && (parsed.os.as_deref() != Some(expected_os)
            || parsed.arch.as_deref() != Some(expected_arch)
            || parsed.protocol_version != Some(PROTOCOL_VERSION)
            || parsed.engine_id.as_deref() != Some(engine.id.as_str()))
    {
        return Err(invalid(
            "Engine package manifest metadata does not match this host or update entry",
        ));
    }
    for part in &engine.parts {
        verify_file(&directory.join(&part.name), part, public_key)?;
    }
    Ok(crate::engine_payload::install_manifest_with_files(
        &manifest, directory, engine_dir,
    )?)
}

#[cfg(test)]
pub(crate) fn install_engine_package(
    directory: &Path,
    engine: &EngineUpdate,
    public_key: &str,
    engine_dir: &Path,
) -> AppResult<()> {
    install_engine_package_for(
        directory,
        engine,
        public_key,
        engine_dir,
        std::env::consts::OS,
        std::env::consts::ARCH,
    )
    .map(|_| ())
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
    require_self_update(&UpdateConfig::load())?;
    if state.engines.busy.load(Ordering::SeqCst) {
        return Err(invalid("An engine installation is already running"));
    }
    let _busy = acquire(&state.updates.busy)?;
    state.updates.cancel.store(false, Ordering::SeqCst);
    let (file, config, contract, engine_backend, engine_package, engine_unpacked_size) = {
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
            checked.engine_backend.clone(),
            checked.engine_package.clone(),
            checked.engine_unpacked_size,
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
    let engine_files = engine_package
        .iter()
        .filter(|engine| {
            engine_needs_package_for(&state.paths.next_root, &config, &engine.id, &engine_backend)
        })
        .flat_map(|engine| std::iter::once(&engine.manifest).chain(&engine.parts));
    let present = already_downloaded(&directory, std::iter::once(&file).chain(engine_files));
    let unpacked_need = contract.unpacked_size.max(engine_unpacked_size);
    let needed = status
        .size
        .saturating_sub(present)
        .checked_add(unpacked_need)
        .and_then(|s| s.checked_add(joining))
        .and_then(|s| s.checked_add(256 * 1024 * 1024))
        .ok_or_else(|| invalid("Update size overflow"))?;
    require_space(fs2::available_space(&directory)?, needed)?;
    status.stage = "downloading".into();
    status.downloaded = 0;
    publish(&state, &app, &mut status);
    let result = async {
        let mut progress = |count| {
            status.downloaded += count;
            publish(&state, &app, &mut status);
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
        if let Some(engine) = engine_package.filter(|e| {
            engine_needs_package_for(&state.paths.next_root, &config, &e.id, &engine_backend)
        }) {
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
    publish(&state, &app, &mut status);
    result
}
#[tauri::command]
pub fn cancel_update(state: State<'_, Arc<AppState>>) {
    state.updates.cancel.store(true, Ordering::SeqCst);
}
#[tauri::command]
pub fn discard_update(state: State<'_, Arc<AppState>>) -> AppResult<()> {
    crate::distribution::require_direct_updates()?;
    if state.engines.busy.load(Ordering::SeqCst) {
        return Err(invalid("An engine installation is already running"));
    }
    let _busy = acquire(&state.updates.busy)?;
    let directory = state.paths.next_root.join("updates");
    if directory.exists() {
        for entry in fs::read_dir(&directory)? {
            let entry = entry?;
            if entry.file_name() == "engine-cache" {
                continue;
            }
            let path = entry.path();
            if fs::symlink_metadata(&path)?.file_type().is_dir() {
                fs::remove_dir_all(&path)?;
            } else {
                fs::remove_file(&path)?;
            }
        }
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
        state.grants.reconcile_outputs(&settings.output_directory);
        state.grants.reconcile_models(
            &settings.custom_model_path,
            &settings.custom_models,
            &settings.custom_edit_model,
            &recipes,
        );
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
pub(crate) fn require_space(available: u64, needed: u64) -> AppResult<()> {
    if available < needed {
        return Err(invalid(
            "Not enough free disk space to download and install this update",
        ));
    }
    Ok(())
}
pub(crate) fn safe_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}

pub(crate) fn worker_executable_name() -> &'static str {
    if cfg!(windows) {
        "localsr-worker.exe"
    } else {
        "localsr-worker"
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub(crate) struct EngineMeta {
    pub id: String,
    pub backend: String,
    pub os: String,
    pub arch: String,
    pub protocol_version: u32,
    pub handshake_passed: bool,
    #[serde(default)]
    pub verified_install: bool,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub files: Vec<crate::engine_payload::InstalledEngineFile>,
}

fn plain_engine_metadata(path: &Path) -> io::Result<fs::Metadata> {
    let meta = fs::symlink_metadata(path)?;
    // On Windows, std reads the reparse tag (FileAttributeTagInfo) and reports
    // `is_symlink` exactly for name surrogates: symbolic links, junctions and
    // mount points. Compressed (WOF), cloud placeholder and deduplicated files
    // are reparse points too, but plain files or folders, and stay allowed.
    if meta.file_type().is_symlink() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Linked engine path",
        ));
    }
    Ok(meta)
}

pub(crate) fn real_engine_directory(path: &Path) -> io::Result<()> {
    if plain_engine_metadata(path)?.is_dir() {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Invalid engine folder",
        ))
    }
}

pub(crate) fn engine_directory_exists(path: &Path) -> AppResult<bool> {
    match real_engine_directory(path) {
        Ok(()) => Ok(true),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.into()),
    }
}

pub(crate) fn engine_install_root(next_root: &Path, id: &str) -> AppResult<PathBuf> {
    if !safe_id(id) {
        return Err(invalid("Invalid engine identity"));
    }
    let engines = next_root.join("engines");
    real_engine_directory(&engines)?;
    let root = engines.join(id);
    real_engine_directory(&root)?;
    Ok(root)
}

pub(crate) fn read_engine_meta(next_root: &Path, id: &str) -> Option<EngineMeta> {
    read_engine_meta_in(&engine_install_root(next_root, id).ok()?)
}

fn read_engine_meta_in(root: &Path) -> Option<EngineMeta> {
    real_engine_directory(root).ok()?;
    let path = root.join("meta.json");
    if !plain_engine_metadata(&path).ok()?.is_file() {
        return None;
    }
    let bytes = fs::read(path).ok()?;
    serde_json::from_slice(&bytes).ok()
}

pub(crate) fn write_engine_meta(next_root: &Path, meta: &EngineMeta) -> AppResult<()> {
    let root = engine_install_root(next_root, &meta.id)?;
    update_storage::write_atomic(&root.join("meta.json"), &serde_json::to_vec_pretty(meta)?)
}

pub(crate) fn mark_engine_needs_reinstall(next_root: &Path, id: &str) {
    if let Some(mut meta) = read_engine_meta(next_root, id) {
        if meta.verified_install {
            meta.verified_install = false;
            let _ = write_engine_meta(next_root, &meta);
        }
    }
}

fn valid_engine_file_list(files: &[crate::engine_payload::InstalledEngineFile]) -> bool {
    if files.is_empty() {
        return false;
    }
    let worker_name = worker_executable_name();
    let mut seen = HashSet::new();
    let mut has_worker = false;
    for entry in files {
        let path = Path::new(&entry.path);
        if !crate::engine_payload::safe_relative(path)
            || entry.path != crate::engine_payload::canonical_relative_path(path)
            || entry.sha256.len() != 64
            || !entry
                .sha256
                .bytes()
                .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
            || !seen.insert(entry.path.to_lowercase())
        {
            return false;
        }
        if entry.path == worker_name {
            has_worker = true;
        }
    }
    has_worker
}

fn inspect_engine_tree(
    root: &Path,
    mut inspect_file: impl FnMut(&Path, &fs::Metadata) -> AppResult<()>,
) -> AppResult<()> {
    let mut folders = vec![root.to_path_buf()];
    while let Some(directory) = folders.pop() {
        real_engine_directory(&directory)?;
        for entry in fs::read_dir(&directory)? {
            let path = entry?.path();
            let meta = plain_engine_metadata(&path)?;
            if meta.is_dir() {
                folders.push(path);
            } else if meta.is_file() {
                inspect_file(&path, &meta)?;
            } else {
                return Err(invalid(format!("Invalid engine file: {}", path.display())));
            }
        }
    }
    Ok(())
}

pub(crate) fn check_engine_tree_is_real(root: &Path) -> AppResult<()> {
    inspect_engine_tree(root, |_, _| Ok(()))
}

/// Read-only check of an installed engine: host metadata, no links and sizes.
pub(crate) fn installed_engine_looks_intact(next_root: &Path, id: &str) -> bool {
    read_engine_meta(next_root, id).is_some_and(|meta| {
        meta.id == id
            && engine_meta_valid_for_host(&meta, std::env::consts::OS, std::env::consts::ARCH)
            && engine_update_matches_host(&next_root.join("engines").join(id))
            && quick_verify_engine_files(next_root, id, &meta)
    })
}

pub(crate) fn quick_verify_engine_files(next_root: &Path, id: &str, meta: &EngineMeta) -> bool {
    check_engine_files(next_root, id, meta, false)
}

fn engine_code_file(relative: &str) -> bool {
    let path = Path::new(relative);
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    let name = name.to_ascii_lowercase();
    // The worker contains the PYZ archive. The bootloader also imports from
    // base_library.zip, and the frozen importer can load modules from disk.
    matches!(
        name.as_str(),
        "localsr-worker" | "localsr-worker.pkg" | "sd-cli" | "python" | "base_library.zip"
    ) || matches!(
        Path::new(&name).extension().and_then(|ext| ext.to_str()),
        Some("exe" | "dll" | "pyd" | "so" | "dylib" | "pyz" | "py" | "pyc" | "pth" | "manifest")
    ) || name.rsplit_once(".so.").is_some_and(|(_, version)| {
        version
            .split('.')
            .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit()))
    }) || path.ancestors().skip(1).any(|dir| {
        dir.extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("framework"))
            && dir
                .file_stem()
                .is_some_and(|stem| stem.eq_ignore_ascii_case(&name))
    })
}

/// Whether a file is a program by its permissions or contents: helpers such as
/// `localsr-media`, `torch_shm_manager` or `protoc` have no extension.
fn executable_content(path: &Path, stat: &fs::Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if stat.permissions().mode() & 0o111 != 0 {
            return true;
        }
    }
    #[cfg(not(unix))]
    let _ = stat;
    let mut magic = [0_u8; 4];
    let read = fs::File::open(path).and_then(|mut file| {
        let mut filled = 0;
        while filled < magic.len() {
            match file.read(&mut magic[filled..])? {
                0 => break,
                count => filled += count,
            }
        }
        Ok(filled)
    });
    let Ok(read) = read else {
        // Unreadable: hash it, which then fails.
        return true;
    };
    let magic = &magic[..read];
    magic.starts_with(b"MZ")
        || magic.starts_with(b"#!")
        || matches!(
            magic,
            [0x7f, b'E', b'L', b'F']
                | [0xfe, 0xed, 0xfa, 0xce]
                | [0xfe, 0xed, 0xfa, 0xcf]
                | [0xce, 0xfa, 0xed, 0xfe]
                | [0xcf, 0xfa, 0xed, 0xfe]
                | [0xca, 0xfe, 0xba, 0xbe]
                | [0xca, 0xfe, 0xba, 0xbf]
        )
}

#[derive(Clone, Copy)]
enum EngineCheck {
    Sizes,
    Code,
    Full,
}

fn check_engine_files(next_root: &Path, id: &str, meta: &EngineMeta, hash_code: bool) -> bool {
    engine_install_root(next_root, id)
        .is_ok_and(|root| check_engine_directory_files(&root.join("engine"), meta, hash_code))
}

pub(crate) fn check_engine_directory_files(
    engine_dir: &Path,
    meta: &EngineMeta,
    hash_code: bool,
) -> bool {
    let check = if hash_code {
        EngineCheck::Code
    } else {
        EngineCheck::Sizes
    };
    verify_engine_directory(engine_dir, &meta.files, check).is_ok()
}

pub(crate) fn full_verify_engine_files(
    next_root: &Path,
    id: &str,
    meta: &EngineMeta,
) -> AppResult<()> {
    let root = engine_install_root(next_root, id)?;
    verify_engine_directory(&root.join("engine"), &meta.files, EngineCheck::Full)
}

fn verify_engine_directory(
    engine_dir: &Path,
    files: &[crate::engine_payload::InstalledEngineFile],
    check: EngineCheck,
) -> AppResult<()> {
    if !valid_engine_file_list(files) {
        return Err(invalid(
            "Engine verification metadata is missing or invalid",
        ));
    }
    let mut expected: HashMap<_, _> = files
        .iter()
        .map(|entry| (entry.path.as_str(), entry))
        .collect();
    inspect_engine_tree(engine_dir, |path, stat| {
        let relative = path
            .strip_prefix(engine_dir)
            .expect("file is inside the engine");
        let relative = crate::engine_payload::canonical_relative_path(relative);
        let code = || engine_code_file(&relative) || executable_content(path, stat);
        let Some(entry) = expected.remove(relative.as_str()) else {
            return if code() {
                Err(invalid(format!(
                    "Engine code file has no checksum: {relative}"
                )))
            } else {
                Ok(())
            };
        };
        if stat.len() != entry.size {
            return Err(invalid(format!(
                "Engine file has the wrong size: {relative}"
            )));
        }
        if (matches!(check, EngineCheck::Full) || (matches!(check, EngineCheck::Code) && code()))
            && !crate::engine_payload::sha256_file(path)
                .is_ok_and(|(size, digest)| size == entry.size && digest == entry.sha256)
        {
            return Err(invalid(format!(
                "Engine file failed checksum verification: {relative}"
            )));
        }
        Ok(())
    })?;
    if let Some(path) = expected.keys().next() {
        return Err(invalid(format!("Engine file is missing: {path}")));
    }
    Ok(())
}

pub(crate) fn engine_meta_valid_for_host(meta: &EngineMeta, os: &str, arch: &str) -> bool {
    meta.handshake_passed
        && meta.verified_install
        && meta.os == os
        && meta.arch == arch
        && meta.protocol_version == PROTOCOL_VERSION
        && safe_id(&meta.id)
        && valid_engine_file_list(&meta.files)
}

#[cfg(test)]
pub(crate) fn test_engine_files(
    next_root: &Path,
    id: &str,
) -> Vec<crate::engine_payload::InstalledEngineFile> {
    fn walk(root: &Path, dir: &Path, out: &mut Vec<crate::engine_payload::InstalledEngineFile>) {
        let Ok(entries) = fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(root, &path, out);
            } else if path.is_file() {
                if let (Ok(rel), Ok((size, sha256))) = (
                    path.strip_prefix(root),
                    crate::engine_payload::sha256_file(&path),
                ) {
                    out.push(crate::engine_payload::InstalledEngineFile {
                        path: crate::engine_payload::canonical_relative_path(rel),
                        size,
                        sha256,
                    });
                }
            }
        }
    }
    let engine_dir = next_root.join("engines").join(id).join("engine");
    let mut files = Vec::new();
    walk(&engine_dir, &engine_dir, &mut files);
    files.sort_by(|a, b| a.path.cmp(&b.path));
    files
}

/// The files of an installed engine as its metadata lists them: every regular file, hashed.
/// Links and other special files are refused, as the payload installer refuses them.
fn hash_engine_directory(
    engine: &Path,
) -> AppResult<Vec<crate::engine_payload::InstalledEngineFile>> {
    fn walk(
        root: &Path,
        dir: &Path,
        out: &mut Vec<crate::engine_payload::InstalledEngineFile>,
    ) -> AppResult<()> {
        real_engine_directory(dir)?;
        for entry in fs::read_dir(dir)? {
            let path = entry?.path();
            let metadata = fs::symlink_metadata(&path)?;
            if metadata.file_type().is_symlink() {
                return Err(invalid("An installed engine contains a link"));
            }
            if metadata.is_dir() {
                walk(root, &path, out)?;
            } else if metadata.is_file() {
                let (size, sha256) = crate::engine_payload::sha256_file(&path)?;
                let relative = path
                    .strip_prefix(root)
                    .map_err(|_| invalid("An engine file lies outside its engine"))?;
                out.push(crate::engine_payload::InstalledEngineFile {
                    path: crate::engine_payload::canonical_relative_path(relative),
                    size,
                    sha256,
                });
            } else {
                return Err(invalid("An installed engine contains a special file"));
            }
        }
        Ok(())
    }
    let mut files = Vec::new();
    walk(engine, engine, &mut files)?;
    files.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(files)
}

/// Record an engine that an updater of LocalSR 0.4.0 to 0.5.0 installed.
///
/// Those updaters install the new release's engine package under its id, but they predate
/// `meta.json`, so a newer host would ignore the engine. On an edition without a bundled
/// engine (Windows CUDA) that leaves no engine at all. The engine whose id this host was
/// built for, installed from parts the old updater checked against the signed manifest,
/// is recorded once: its files hashed and a handshake passed, then the usual checks apply.
/// An engine with metadata, one being installed, or any other id is left alone.
pub(crate) fn adopt_engine_from_older_updater(
    next_root: &Path,
    compiled_id: &str,
    backend: &str,
    announce: impl FnOnce(),
    handshake: impl FnOnce(&Path) -> bool,
) -> bool {
    if compiled_id.is_empty() || !safe_id(compiled_id) {
        return false;
    }
    let Ok(root) = engine_install_root(next_root, compiled_id) else {
        return false;
    };
    match fs::symlink_metadata(root.join("meta.json")) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        _ => return false,
    }
    if pending_engine_start(&root).is_some() {
        return false;
    }
    let Some(executable) = engine_executable_path(next_root, compiled_id) else {
        return false;
    };
    announce();
    let Ok(files) = hash_engine_directory(&root.join("engine")) else {
        return false;
    };
    if !valid_engine_file_list(&files) || !handshake(&executable) {
        return false;
    }
    let meta = EngineMeta {
        id: compiled_id.to_owned(),
        backend: backend.to_owned(),
        os: std::env::consts::OS.into(),
        arch: std::env::consts::ARCH.into(),
        protocol_version: PROTOCOL_VERSION,
        handshake_passed: true,
        verified_install: true,
        version: env!("CARGO_PKG_VERSION").into(),
        files,
    };
    write_engine_meta(next_root, &meta).is_ok()
}

pub(crate) fn engine_executable_path(next_root: &Path, id: &str) -> Option<PathBuf> {
    let engine = engine_install_root(next_root, id).ok()?.join("engine");
    real_engine_directory(&engine).ok()?;
    let executable = engine.join(worker_executable_name());
    plain_engine_metadata(&executable)
        .ok()?
        .is_file()
        .then_some(executable)
}

pub(crate) fn deactivate_active_engine(paths: &crate::paths::AppPaths) -> AppResult<()> {
    match fs::remove_file(paths.next_root.join("active-engine.txt")) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

pub(crate) fn active_engine_id(
    paths: &crate::paths::AppPaths,
    managed_by_store: bool,
    bundled_engine_id: &str,
) -> Option<String> {
    active_engine_entry(
        paths,
        managed_by_store,
        bundled_engine_id,
        std::env::consts::OS,
        std::env::consts::ARCH,
    )
    .map(|(id, _)| id)
}

pub(crate) fn chosen_backend_for(
    paths: &crate::paths::AppPaths,
    config: &UpdateConfig,
    os: &str,
    arch: &str,
) -> String {
    if let Some((id, _)) = active_engine_entry(
        paths,
        crate::distribution::managed_by_store(),
        &config.engine_id,
        os,
        arch,
    ) {
        if let Some(meta) = read_engine_meta(&paths.next_root, &id) {
            if engine_meta_valid_for_host(&meta, os, arch) && !meta.backend.is_empty() {
                return meta.backend;
            }
        }
    }
    config.backend.clone()
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

/// Choose from already validated installed engines, excluding failures in this session.
fn choose_installed_engine(
    installed: &[(String, PathBuf)],
    active: Option<&str>,
    compiled_id: &str,
    failed: &HashSet<String>,
) -> Option<(String, PathBuf)> {
    [active, (!compiled_id.is_empty()).then_some(compiled_id)]
        .into_iter()
        .flatten()
        .filter(|id| !failed.contains(*id))
        .find_map(|id| {
            installed
                .iter()
                .find(|(candidate, _)| candidate == id)
                .cloned()
        })
}

fn installed_engine_candidates(
    paths: &crate::paths::AppPaths,
    managed_by_store: bool,
    bundled_engine_id: &str,
    os: &str,
    arch: &str,
) -> (Vec<(String, PathBuf)>, Option<String>) {
    if managed_by_store {
        return (Vec::new(), None);
    }
    let active = fs::read_to_string(paths.next_root.join("active-engine.txt"))
        .ok()
        .map(|raw| raw.trim().to_owned());
    let mut installed = Vec::new();
    for id in [active.as_deref(), Some(bundled_engine_id)]
        .into_iter()
        .flatten()
    {
        if !safe_id(id) {
            continue;
        }
        if let Some(executable) = engine_executable_path(&paths.next_root, id) {
            let valid = read_engine_meta(&paths.next_root, id).is_some_and(|meta| {
                engine_meta_valid_for_host(&meta, os, arch)
                    && meta.id == id
                    && engine_update_matches_host(&paths.next_root.join("engines").join(id))
                    && quick_verify_engine_files(&paths.next_root, id, &meta)
            });
            if valid {
                installed.push((id.to_owned(), executable));
            }
        }
    }
    (installed, active)
}

/// Select a worker from validated installed engines and the bundled executable.
fn choose_worker_executable(
    installed: &[(String, PathBuf)],
    active: Option<&str>,
    compiled_id: &str,
    failed: &HashSet<String>,
    bundled: Option<PathBuf>,
) -> Option<(PathBuf, Option<String>)> {
    let selected = choose_installed_engine(installed, active, compiled_id, failed);
    let selected_path = selected.as_ref().map(|(_, path)| path.clone());
    let path = choose_engine(compiled_id, bundled.clone(), selected_path)?;
    let id = selected
        .filter(|(_, candidate)| *candidate == path && bundled.as_ref() != Some(&path))
        .map(|(id, _)| id);
    Some((path, id))
}

fn active_engine_entry(
    paths: &crate::paths::AppPaths,
    managed_by_store: bool,
    bundled_engine_id: &str,
    os: &str,
    arch: &str,
) -> Option<(String, PathBuf)> {
    let (installed, active) =
        installed_engine_candidates(paths, managed_by_store, bundled_engine_id, os, arch);
    choose_installed_engine(
        &installed,
        active.as_deref(),
        bundled_engine_id,
        &HashSet::new(),
    )
}

pub(crate) fn worker_engine_choice(
    paths: &crate::paths::AppPaths,
    bundled: Option<PathBuf>,
    failed: &HashSet<String>,
) -> Option<(PathBuf, Option<String>)> {
    let compiled_id = option_env!("LOCALSR_ENGINE_ID").unwrap_or("");
    let (installed, active) = installed_engine_candidates(
        paths,
        crate::distribution::managed_by_store(),
        compiled_id,
        std::env::consts::OS,
        std::env::consts::ARCH,
    );
    choose_worker_executable(&installed, active.as_deref(), compiled_id, failed, bundled)
}

pub(crate) fn verified_worker_engine_choice(
    paths: &crate::paths::AppPaths,
    bundled: Option<PathBuf>,
    failed: &mut HashSet<String>,
) -> (Option<(PathBuf, Option<String>)>, bool) {
    let (installed, active, failed_id) =
        start_engine_candidates(paths, failed, std::env::consts::OS, std::env::consts::ARCH);
    let choice = choose_worker_executable(
        &installed,
        active.as_deref(),
        option_env!("LOCALSR_ENGINE_ID").unwrap_or(""),
        failed,
        bundled,
    );
    (choice, failed_id.is_some())
}

#[cfg(test)]
pub(crate) fn verify_active_engine_at_start(
    paths: &crate::paths::AppPaths,
    failed: &mut HashSet<String>,
) -> Option<String> {
    verify_active_engine_at_start_for(paths, failed, std::env::consts::OS, std::env::consts::ARCH)
}

#[cfg(test)]
pub(crate) fn verify_active_engine_at_start_for(
    paths: &crate::paths::AppPaths,
    failed: &mut HashSet<String>,
    os: &str,
    arch: &str,
) -> Option<String> {
    start_engine_candidates(paths, failed, os, arch).2
}

/// Check sizes and code hashes before returning any installed worker for launch.
/// Failed copies are deactivated and skipped for the rest of the session.
fn start_engine_candidates(
    paths: &crate::paths::AppPaths,
    failed: &mut HashSet<String>,
    os: &str,
    arch: &str,
) -> (Vec<(String, PathBuf)>, Option<String>, Option<String>) {
    if crate::distribution::managed_by_store() {
        return (Vec::new(), None, None);
    }
    let compiled_id = option_env!("LOCALSR_ENGINE_ID").unwrap_or("");
    let active = fs::read_to_string(paths.next_root.join("active-engine.txt"))
        .ok()
        .map(|raw| raw.trim().to_owned());
    let mut installed = Vec::new();
    let mut failed_id = None;
    for id in [
        active.as_deref(),
        (!compiled_id.is_empty() && active.as_deref() != Some(compiled_id)).then_some(compiled_id),
    ]
    .into_iter()
    .flatten()
    {
        if !safe_id(id) || failed.contains(id) {
            continue;
        }
        let next_root = &paths.next_root;
        // A recovery error concerns leftovers or the backup; the hash check
        // below decides whether the installed copy itself is good.
        let recovered = crate::engine_manager::recover_engine_copy_for(next_root, id, os, arch)
            .unwrap_or_else(|error| {
                eprintln!("LocalSR could not finish recovering engine {id}: {error}");
                false
            });
        let host_meta = || {
            read_engine_meta(next_root, id)
                .filter(|meta| meta.id == id && engine_meta_valid_for_host(meta, os, arch))
        };
        let Some(meta) = host_meta() else {
            continue;
        };
        let root = next_root.join("engines").join(id);
        let verified = engine_update_matches_host(&root)
            && (recovered || check_engine_files(next_root, id, &meta, true));
        if verified
            || (restore_verified_engine_backup_for(next_root, id, os, arch).unwrap_or(false)
                && host_meta().is_some())
        {
            if let Some(path) = engine_executable_path(next_root, id) {
                installed.push((id.to_owned(), path));
                break;
            }
        }
        mark_engine_needs_reinstall(&paths.next_root, id);
        if active.as_deref() == Some(id) {
            let _ = deactivate_active_engine(paths);
        }
        failed.insert(id.to_owned());
        if failed_id.is_none() {
            failed_id = Some(id.to_owned());
        }
    }
    (installed, active, failed_id)
}

/// The engine an in-app update or engine manager installed, if valid for this host.
///
/// An installed engine with verified host metadata (same OS/arch and passed handshake)
/// is used even when its id differs from `bundled_engine_id`. Without metadata, a host
/// with a compiled engine id selects only the matching installed engine.
#[cfg(test)]
pub(crate) fn active_engine_for_distribution(
    paths: &crate::paths::AppPaths,
    managed_by_store: bool,
    bundled_engine_id: &str,
) -> Option<PathBuf> {
    active_engine_entry(
        paths,
        managed_by_store,
        bundled_engine_id,
        std::env::consts::OS,
        std::env::consts::ARCH,
    )
    .map(|(_, path)| path)
}

#[cfg(test)]
pub(crate) fn active_engine_for_host(
    paths: &crate::paths::AppPaths,
    managed_by_store: bool,
    bundled_engine_id: &str,
    os: &str,
    arch: &str,
) -> Option<PathBuf> {
    active_engine_entry(paths, managed_by_store, bundled_engine_id, os, arch).map(|(_, path)| path)
}
#[tauri::command]
pub async fn install_update(state: State<'_, Arc<AppState>>, app: AppHandle) -> AppResult<()> {
    crate::distribution::require_direct_updates()?;
    require_self_update(&UpdateConfig::load())?;
    if state.engines.busy.load(Ordering::SeqCst) {
        return Err(invalid("An engine installation is already running"));
    }
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
            publish(&state, &app, &mut status);
        }
        result
    })
    .await
    .map_err(|e| invalid(e.to_string()))?
}

pub(crate) struct ResolvedUpdateEngine {
    pub backend: String,
    pub engine_id: String,
    pub engine: Option<EngineUpdate>,
    pub unpacked_size: u64,
}

pub(crate) fn resolve_update_engine_for_backend(
    raw_json: &serde_json::Value,
    config: &UpdateConfig,
    base_contract: &ReleaseContract,
    channel: &str,
    version: &str,
    installed_version: &str,
    paths: &crate::paths::AppPaths,
) -> AppResult<ResolvedUpdateEngine> {
    resolve_update_engine_for_backend_on(
        raw_json,
        config,
        base_contract,
        channel,
        version,
        installed_version,
        paths,
        std::env::consts::OS,
        std::env::consts::ARCH,
    )
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn resolve_update_engine_for_backend_on(
    raw_json: &serde_json::Value,
    config: &UpdateConfig,
    base_contract: &ReleaseContract,
    channel: &str,
    version: &str,
    installed_version: &str,
    paths: &crate::paths::AppPaths,
    os: &str,
    arch: &str,
) -> AppResult<ResolvedUpdateEngine> {
    let chosen = chosen_backend_for(paths, config, os, arch);
    if chosen.is_empty() || chosen == config.backend {
        return Ok(ResolvedUpdateEngine {
            backend: config.backend.clone(),
            engine_id: base_contract.engine_id.clone(),
            engine: base_contract.engine.clone(),
            unpacked_size: base_contract.unpacked_size,
        });
    }
    let target = platform_key(os, arch, &chosen, &config.kind);
    let chosen_value = &raw_json["platforms"][&target]["localsr"];
    if chosen_value.is_null() {
        return Err(invalid(format!(
            "This release does not include the {chosen} engine for this platform"
        )));
    }
    let chosen_contract: ReleaseContract = serde_json::from_value(chosen_value.clone())?;
    validate_contract_for_backend(config, &chosen, channel, version, &chosen_contract, false)?;
    verify_statement_for_entry(
        config,
        &target,
        &chosen,
        channel,
        version,
        installed_version,
        &chosen_contract,
        true,
    )?;
    if engine_needs_package_for_host(
        &paths.next_root,
        config,
        &chosen_contract.engine_id,
        &chosen,
        os,
        arch,
    ) && chosen_contract.engine.is_none()
    {
        return Err(invalid(format!(
            "The {chosen} release entry supplies no compatible engine package"
        )));
    }
    Ok(ResolvedUpdateEngine {
        backend: chosen,
        engine_id: chosen_contract.engine_id,
        engine: chosen_contract.engine,
        unpacked_size: base_contract
            .unpacked_size
            .max(chosen_contract.unpacked_size),
    })
}

/// Whether the update must bring the engine package. A different engine id always does.
/// The same id does not, unless an earlier update delivered this engine as a versioned
/// engine (`active-engine.txt` names it) and that engine is gone: the bundle may then
/// still hold the older engine, which the handshake cannot tell apart.
#[cfg(test)]
fn engine_needs_package(next_root: &Path, config: &UpdateConfig, engine_id: &str) -> bool {
    engine_needs_package_for(next_root, config, engine_id, &config.backend)
}

fn engine_needs_package_for(
    next_root: &Path,
    config: &UpdateConfig,
    engine_id: &str,
    engine_backend: &str,
) -> bool {
    engine_needs_package_for_host(
        next_root,
        config,
        engine_id,
        engine_backend,
        std::env::consts::OS,
        std::env::consts::ARCH,
    )
}

fn engine_needs_package_for_host(
    next_root: &Path,
    config: &UpdateConfig,
    engine_id: &str,
    engine_backend: &str,
    os: &str,
    arch: &str,
) -> bool {
    let installed_ok = |id: &str| {
        engine_executable_path(next_root, id).is_some()
            && read_engine_meta(next_root, id).is_some_and(|meta| {
                meta.id == id
                    && engine_meta_valid_for_host(&meta, os, arch)
                    && quick_verify_engine_files(next_root, id, &meta)
            })
    };
    if !engine_backend.is_empty() && engine_backend != config.backend {
        return !installed_ok(engine_id);
    }
    if engine_id != config.engine_id {
        return true;
    }
    let delivered = fs::read_to_string(next_root.join("active-engine.txt"))
        .is_ok_and(|active| active.trim() == engine_id);
    delivered && !installed_ok(engine_id)
}

#[cfg(test)]
fn prepare_update_engine(
    config: &UpdateConfig,
    contract: &ReleaseContract,
    directory: &Path,
    next_root: &Path,
    installed_worker: impl FnOnce() -> AppResult<PathBuf>,
) -> AppResult<PathBuf> {
    prepare_update_engine_for(
        config,
        &contract.engine_id,
        &contract.backend,
        contract.engine.as_ref(),
        directory,
        next_root,
        installed_worker,
        |_| Ok(()),
        |_| None,
        None,
        || Ok(()),
    )
    .map(|(executable, _)| executable)
}

const ENGINE_PENDING_START: &str = ".pending-start.json";

#[derive(Serialize, Deserialize)]
struct PendingEngineStart {
    token: String,
    app_version: Option<String>,
}

fn pending_engine_start(root: &Path) -> Option<PendingEngineStart> {
    let marker = root.join(ENGINE_PENDING_START);
    if !plain_engine_metadata(&marker).ok()?.is_file() {
        return None;
    }
    serde_json::from_slice(&fs::read(marker).ok()?).ok()
}

pub(crate) fn engine_update_matches_host(root: &Path) -> bool {
    pending_engine_start(root)
        .and_then(|pending| pending.app_version)
        .is_none_or(|version| version == env!("CARGO_PKG_VERSION"))
}

pub(crate) fn engine_backup_path(root: &Path, id: &str) -> PathBuf {
    root.join(format!(".previous-{id}"))
}

pub(crate) fn rename_engine_directory(from: &Path, to: &Path) -> AppResult<()> {
    let parent = from.parent().expect("engine directory has a parent");
    debug_assert_eq!(to.parent(), Some(parent));
    real_engine_directory(parent)?;
    real_engine_directory(from)?;
    if engine_directory_exists(to)? {
        return Err(invalid("The engine destination already exists"));
    }
    #[cfg(not(windows))]
    fs::rename(from, to)?;
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;

        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn MoveFileExW(from: *const u16, to: *const u16, flags: u32) -> i32;
        }

        const MOVEFILE_WRITE_THROUGH: u32 = 0x8;
        let absolute = fs::canonicalize(parent)?;
        let wide = |path: &Path| {
            absolute
                .join(path.file_name().expect("engine directory has a name"))
                .as_os_str()
                .encode_wide()
                .chain(Some(0))
                .collect::<Vec<_>>()
        };
        let from = wide(from);
        let to = wide(to);
        // Antivirus or the indexer can hold a file open for a moment.
        const ERROR_ACCESS_DENIED: i32 = 5;
        const ERROR_SHARING_VIOLATION: i32 = 32;
        const ERROR_LOCK_VIOLATION: i32 = 33;
        let mut attempt = 1;
        loop {
            // Both paths are null-terminated, remain live for the call and are on
            // the same volume. Write-through makes the rename durable on Windows.
            if unsafe { MoveFileExW(from.as_ptr(), to.as_ptr(), MOVEFILE_WRITE_THROUGH) } != 0 {
                break;
            }
            let error = std::io::Error::last_os_error();
            let transient = matches!(
                error.raw_os_error(),
                Some(ERROR_ACCESS_DENIED | ERROR_SHARING_VIOLATION | ERROR_LOCK_VIOLATION)
            );
            if !transient || attempt == 5 {
                return Err(error.into());
            }
            attempt += 1;
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
    }
    update_storage::sync_dir(parent)
}

fn engine_backup_verifies(backup: &Path, id: &str, os: &str, arch: &str) -> bool {
    read_engine_meta_in(backup).is_some_and(|meta| {
        meta.id == id
            && engine_meta_valid_for_host(&meta, os, arch)
            && engine_update_matches_host(backup)
            && verify_engine_directory(&backup.join("engine"), &meta.files, EngineCheck::Full)
                .is_ok()
    })
}

pub(crate) fn restore_verified_engine_backup(next_root: &Path, id: &str) -> AppResult<bool> {
    restore_verified_engine_backup_for(next_root, id, std::env::consts::OS, std::env::consts::ARCH)
}

pub(crate) fn restore_verified_engine_backup_for(
    next_root: &Path,
    id: &str,
    os: &str,
    arch: &str,
) -> AppResult<bool> {
    if !safe_id(id) {
        return Err(invalid("Invalid engine identity"));
    }
    let engines = next_root.join("engines");
    if !engine_directory_exists(&engines)? {
        return Ok(false);
    }
    let backup = engine_backup_path(&engines, id);
    if !engine_backup_verifies(&backup, id, os, arch) {
        return Ok(false);
    }
    EngineSwap {
        target: engines.join(id),
        backup,
        token: None,
        replaced: true,
    }
    .rollback()?;
    Ok(true)
}

pub(crate) fn pending_engine_token(next_root: &Path, id: &str) -> Option<String> {
    let root = engine_install_root(next_root, id).ok()?;
    pending_engine_start(&root).map(|pending| pending.token)
}

/// Accept the installation whose worker just completed its handshake. Returns the
/// retired backup, which the caller deletes outside the `engine_files` lock; a
/// crash before that finishes leaves it to the sweep at the next start.
pub(crate) fn confirm_engine_start(
    next_root: &Path,
    id: &str,
    token: &str,
) -> AppResult<Option<PathBuf>> {
    let engines = next_root.join("engines");
    let root = engine_install_root(next_root, id)?;
    // The worker that just became ready may belong to an earlier installation.
    if !pending_engine_start(&root).is_some_and(|pending| pending.token == token) {
        return Ok(None);
    }
    // Removing the marker is the commit point. Until then the backup stays where
    // recovery and rollback find it; a leftover backup after a crash here is
    // replaced at the next install.
    fs::remove_file(root.join(ENGINE_PENDING_START))?;
    update_storage::sync_dir(&root)?;
    let backup = engine_backup_path(&engines, id);
    if !engine_directory_exists(&backup)? {
        return Ok(None);
    }
    // Remove the backup from recovery before deleting any of its files.
    let retired = engines.join(format!(".retired-{}", uuid::Uuid::new_v4()));
    rename_engine_directory(&backup, &retired)?;
    Ok(Some(retired))
}

pub(crate) struct StagedEngine {
    staging: PathBuf,
    target: PathBuf,
    pub files: Vec<crate::engine_payload::InstalledEngineFile>,
}

pub(crate) fn remove_engine_tree_if_real(path: &Path) {
    if path
        .parent()
        .is_some_and(|parent| real_engine_directory(parent).is_ok())
        && check_engine_tree_is_real(path).is_ok()
    {
        let _ = fs::remove_dir_all(path);
    }
}

impl Drop for StagedEngine {
    fn drop(&mut self) {
        remove_engine_tree_if_real(&self.staging);
    }
}

pub(crate) struct EngineSwap {
    target: PathBuf,
    backup: PathBuf,
    token: Option<String>,
    /// The swap replaced an earlier copy, so a rollback needs its backup.
    replaced: bool,
}

impl EngineSwap {
    fn executable(&self) -> PathBuf {
        self.target.join("engine").join(worker_executable_name())
    }

    pub(crate) fn rollback(&self) -> AppResult<()> {
        real_engine_directory(self.target.parent().expect("engine has a parent"))?;
        let has_backup = engine_directory_exists(&self.backup)?;
        let retired = self
            .target
            .with_file_name(format!(".retired-{}", uuid::Uuid::new_v4()));
        if engine_directory_exists(&self.target)? {
            if self.token.as_ref().is_some_and(|token| {
                !pending_engine_start(&self.target).is_some_and(|pending| &pending.token == token)
            }) {
                // Recovery or a later install has already moved this copy out.
                return Ok(());
            }
            if self.replaced && !has_backup {
                // Without its backup, the installed copy is the only one left.
                return Err(invalid(
                    "The previous engine copy is missing; the installed one was kept",
                ));
            }
            rename_engine_directory(&self.target, &retired)?;
        }
        if has_backup {
            rename_engine_directory(&self.backup, &self.target)?;
        }
        remove_engine_tree_if_real(&retired);
        Ok(())
    }
}

impl StagedEngine {
    /// Publish the payload and its metadata together. Until the worker completes a
    /// handshake, the previous installation remains available for recovery.
    pub(crate) fn publish(
        self,
        meta: Option<&EngineMeta>,
        app_version: Option<&str>,
    ) -> AppResult<EngineSwap> {
        let parent = self.target.parent().expect("engine has a parent");
        let id = self
            .target
            .file_name()
            .and_then(|name| name.to_str())
            .expect("engine has a valid id");
        let pending = meta.map(|_| PendingEngineStart {
            token: uuid::Uuid::new_v4().to_string(),
            app_version: app_version.map(str::to_owned),
        });
        real_engine_directory(parent)?;
        real_engine_directory(&self.staging)?;
        let target_exists = engine_directory_exists(&self.target)?;
        let swap = EngineSwap {
            target: self.target.clone(),
            backup: engine_backup_path(parent, id),
            token: pending.as_ref().map(|pending| pending.token.clone()),
            replaced: target_exists,
        };
        let backup_exists = engine_directory_exists(&swap.backup)?;
        if target_exists {
            match plain_engine_metadata(&swap.target.join("meta.json")) {
                Ok(stat) if stat.is_file() => {}
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                _ => return Err(invalid("The installed engine metadata is invalid")),
            }
        }
        if let Some(meta) = meta {
            update_storage::write_atomic(
                &self.staging.join("meta.json"),
                &serde_json::to_vec_pretty(meta)?,
            )?;
        }
        if let Some(pending) = pending {
            update_storage::write_atomic(
                &self.staging.join(ENGINE_PENDING_START),
                &serde_json::to_vec(&pending)?,
            )?;
        }
        update_storage::sync_dir(&self.staging)?;
        update_storage::sync_dir(parent)?;
        let mut retired = None;
        if target_exists {
            // A second reinstall before the first has started keeps the last
            // working copy in the backup. A confirmed target, or a backup that
            // no longer verifies, makes the current copy the fallback instead.
            let keep_backup = backup_exists
                && pending_engine_start(&swap.target).is_some()
                && engine_backup_verifies(
                    &swap.backup,
                    id,
                    std::env::consts::OS,
                    std::env::consts::ARCH,
                );
            if keep_backup {
                let path = parent.join(format!(".retired-{}", uuid::Uuid::new_v4()));
                if let Err(error) = rename_engine_directory(&swap.target, &path) {
                    if engine_directory_exists(&path).unwrap_or(false) {
                        let _ = rename_engine_directory(&path, &swap.target);
                    }
                    return Err(error);
                }
                retired = Some(path);
            } else {
                if backup_exists {
                    let path = parent.join(format!(".retired-{}", uuid::Uuid::new_v4()));
                    rename_engine_directory(&swap.backup, &path)?;
                    retired = Some(path);
                }
                if let Err(error) = rename_engine_directory(&swap.target, &swap.backup) {
                    if engine_directory_exists(&swap.backup).unwrap_or(false) {
                        let _ = rename_engine_directory(&swap.backup, &swap.target);
                    }
                    if let Some(path) = &retired {
                        let _ = rename_engine_directory(path, &swap.backup);
                    }
                    return Err(error);
                }
            }
        }
        if let Err(error) = rename_engine_directory(&self.staging, &swap.target) {
            let _ = swap.rollback();
            return Err(error);
        }
        if let Some(retired) = retired {
            remove_engine_tree_if_real(&retired);
        }
        Ok(swap)
    }
}

pub(crate) fn stage_engine_with(
    next_root: &Path,
    engine_id: &str,
    unpack: impl FnOnce(&Path) -> AppResult<Vec<crate::engine_payload::InstalledEngineFile>>,
    handshake: impl FnOnce(&Path) -> AppResult<()>,
) -> AppResult<StagedEngine> {
    if !safe_id(engine_id) {
        return Err(invalid("Invalid engine identity"));
    }
    // Callers finish an interrupted install of this id under `engine_files` first.
    let root = next_root.join("engines");
    if !engine_directory_exists(&root)? {
        fs::create_dir(&root)?;
    }
    real_engine_directory(&root)?;
    let target = root.join(engine_id);
    if engine_directory_exists(&target)? {
        check_engine_tree_is_real(&target)?;
    }
    update_storage::sync_dir(next_root)?;
    let staging = root.join(format!(".import-{}", uuid::Uuid::new_v4()));
    let result = (|| -> AppResult<Vec<crate::engine_payload::InstalledEngineFile>> {
        fs::create_dir(&staging)?;
        let engine = staging.join("engine");
        let files = unpack(&engine)?;
        verify_engine_directory(&engine, &files, EngineCheck::Code)?;
        let executable = engine.join(worker_executable_name());
        handshake(&executable)?;
        Ok(files)
    })();
    match result {
        Ok(files) => Ok(StagedEngine {
            staging,
            target,
            files,
        }),
        Err(error) => {
            remove_engine_tree_if_real(&staging);
            Err(error)
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn stage_verified_engine(
    next_root: &Path,
    engine_id: &str,
    directory: &Path,
    engine: &EngineUpdate,
    public_key: &str,
    os: &str,
    arch: &str,
    handshake: impl FnOnce(&Path) -> AppResult<()>,
) -> AppResult<StagedEngine> {
    stage_engine_with(
        next_root,
        engine_id,
        |staged| install_engine_package_for(directory, engine, public_key, staged, os, arch),
        handshake,
    )
}

#[cfg(test)]
pub(crate) fn install_verified_engine(
    next_root: &Path,
    engine_id: &str,
    directory: &Path,
    engine: &EngineUpdate,
    public_key: &str,
    handshake: impl FnOnce(&Path) -> AppResult<()>,
) -> AppResult<PathBuf> {
    stage_verified_engine(
        next_root,
        engine_id,
        directory,
        engine,
        public_key,
        std::env::consts::OS,
        std::env::consts::ARCH,
        handshake,
    )?
    .publish(None, None)
    .map(|swap| swap.executable())
}

fn stop_running_engine_for_publish(
    state: &AppState,
    engine_id: &str,
    stopped: &mut bool,
) -> AppResult<()> {
    if !*stopped && worker::last_run_engine_id(state).as_deref() == Some(engine_id) {
        *stopped = true;
        worker::stop_for_engine_switch(state)?;
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn prepare_update_engine_for(
    config: &UpdateConfig,
    engine_id: &str,
    engine_backend: &str,
    engine_package: Option<&EngineUpdate>,
    directory: &Path,
    next_root: &Path,
    installed_worker: impl FnOnce() -> AppResult<PathBuf>,
    handshake: impl FnOnce(&Path) -> AppResult<()>,
    installed_meta: impl FnOnce(&[crate::engine_payload::InstalledEngineFile]) -> Option<EngineMeta>,
    app_version: Option<&str>,
    mut stop_before_publish: impl FnMut() -> AppResult<()>,
) -> AppResult<(PathBuf, Option<EngineSwap>)> {
    if !engine_needs_package_for(next_root, config, engine_id, engine_backend) {
        if engine_backend != config.backend && !engine_backend.is_empty() {
            if let Some(executable) = engine_executable_path(next_root, engine_id) {
                if !read_engine_meta(next_root, engine_id)
                    .is_some_and(|meta| check_engine_files(next_root, engine_id, &meta, true))
                {
                    mark_engine_needs_reinstall(next_root, engine_id);
                    return Err(invalid("The installed engine failed verification"));
                }
                handshake(&executable)?;
                return Ok((executable, None));
            }
        }
        // The new application carries the same engine. Keep the existing versioned
        // engine, or use the bundle; no payload, adoption or activation is needed.
        let executable = installed_worker()?;
        handshake(&executable)?;
        return Ok((executable, None));
    }
    let engine = engine_package.ok_or_else(|| invalid("The changed engine has no package"))?;
    // The caller holds `engine_files`. If a backup needs restoration over the target
    // folder, stop any worker running from it first so Windows does not lock it.
    if engine_directory_exists(&engine_backup_path(&next_root.join("engines"), engine_id))? {
        stop_before_publish()?;
    }
    crate::engine_manager::recover_interrupted_reimport(next_root, engine_id)?;
    let staged = stage_verified_engine(
        next_root,
        engine_id,
        directory,
        engine,
        &config.public_key,
        std::env::consts::OS,
        std::env::consts::ARCH,
        handshake,
    )?;
    stop_before_publish()?;
    let meta = installed_meta(&staged.files);
    let swap = staged.publish(meta.as_ref(), app_version)?;
    Ok((swap.executable(), Some(swap)))
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
        contract.unpacked_size.max(checked.engine_unpacked_size) + 256 * 1024 * 1024,
    )?;
    state.persist_settings()?;
    let backup = update_storage::backup_profile(&state.paths, "before-install")?;
    let mut status = lock(&state.updates.status)?.clone();
    status.stage = "installing".into();
    status.message = "Backing up data and preparing the verified update…".into();
    publish(state, app, &mut status);
    let engine_files = lock(&state.worker.engine_files)?;
    let mut stopped_for_engine = false;
    let prepared = prepare_update_engine_for(
        config,
        &checked.engine_id,
        &checked.engine_backend,
        checked.engine_package.as_ref(),
        &directory,
        &state.paths.next_root,
        || worker::installed_worker_path(state, app),
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
        |files| {
            Some(EngineMeta {
                id: checked.engine_id.clone(),
                backend: checked.engine_backend.clone(),
                os: std::env::consts::OS.into(),
                arch: std::env::consts::ARCH.into(),
                protocol_version: PROTOCOL_VERSION,
                handshake_passed: true,
                verified_install: true,
                version: checked.update.version.clone(),
                files: files.to_vec(),
            })
        },
        Some(&checked.update.version),
        || stop_running_engine_for_publish(state, &checked.engine_id, &mut stopped_for_engine),
    );
    let (_, engine_swap) = match prepared {
        Ok(prepared) => prepared,
        Err(error) => {
            drop(engine_files);
            if stopped_for_engine {
                if let Err(resume) = resume_after_failed_install(
                    state,
                    || Ok(()),
                    || worker::start_worker(state.clone(), app.clone()),
                ) {
                    eprintln!("LocalSR: could not resume after the failed update: {resume}");
                }
            }
            return Err(error);
        }
    };
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
        if checked.engine_id != config.engine_id || checked.engine_backend != config.backend {
            update_storage::write_atomic(&active_file, checked.engine_id.as_bytes())?;
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
        if let Some(swap) = engine_swap {
            if let Err(restore) = swap.rollback() {
                eprintln!("LocalSR: could not roll back the replaced engine: {restore}");
            }
        }
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
        drop(engine_files);
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
        publish(state, app, &mut status);
        return Err(error);
    }
    // The new application is in place and this process has stopped its worker and closed
    // its database: from here it only leaves, whatever the cleanup or restart says.
    // `app.exit` returns before the process ends: keep `installing` set so no engine
    // command restarts the worker against the engine published for the new version.
    std::mem::forget(_installing);
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
        if let Some(status) = tree.try_wait(&mut child)? {
            if !status.success() {
                return Err(invalid("Could not read the installed application version"));
            }
            break;
        }
        if start.elapsed() >= Duration::from_secs(10) {
            tree.terminate();
            let _ = child.kill();
            let _ = tree.wait(&mut child);
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
