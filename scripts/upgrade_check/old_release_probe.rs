//! Injected into an older release's `updates.rs` by `scripts/upgrade_check.py`.
//!
//! Runs that release's own update code against a new release's feed entry and files, as
//! the old app does once it has downloaded them: the contract check, the signed release
//! statement, and the engine package installation. Written against the API that 0.4.0,
//! 0.5.0 and 0.5.2 share; a later release that changes it needs its own probe.
use super::*;

fn setting(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("{name} is not set"))
}

#[test]
fn upgrade_probe() {
    if std::env::var_os("LSR_PROBE_FEED").is_none() {
        return;
    }
    let feed: serde_json::Value =
        serde_json::from_slice(&fs::read(setting("LSR_PROBE_FEED")).unwrap()).unwrap();
    let key = setting("LSR_PROBE_KEY");
    let version = feed["version"].as_str().unwrap().to_owned();
    let contract: ReleaseContract =
        serde_json::from_value(feed["platforms"][&key]["localsr"].clone())
            .unwrap_or_else(|error| panic!("PROBE FAILED: the feed entry: {error}"));
    let config = UpdateConfig {
        public_key: setting("LSR_PROBE_PUBLIC_KEY"),
        feed: String::new(),
        backend: contract.backend.clone(),
        engine_id: "engine-of-the-installed-release".into(),
        kind: contract.kind.clone(),
    };
    validate_contract(&config, &contract.channel, &version, &contract)
        .unwrap_or_else(|error| panic!("PROBE FAILED: validate_contract: {error}"));
    verify_statement(
        &config,
        &key,
        &contract.channel,
        &version,
        &setting("LSR_PROBE_INSTALLED"),
        &contract,
    )
    .unwrap_or_else(|error| panic!("PROBE FAILED: verify_statement: {error}"));
    let next_root = std::path::PathBuf::from(setting("LSR_PROBE_NEXT_ROOT"));
    fs::create_dir_all(&next_root).unwrap();
    let worker = prepare_update_engine(
        &config,
        &contract,
        Path::new(&setting("LSR_PROBE_FILES")),
        &next_root,
        || panic!("PROBE FAILED: the old updater wanted the bundled engine instead"),
    )
    .unwrap_or_else(|error| panic!("PROBE FAILED: prepare_update_engine: {error}"));
    println!("LSR_PROBE_WORKER={}", worker.display());
}
