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
fn a_host_without_an_engine_id_runs_the_engine_it_ships_before_an_installed_one() {
    let shipped = Some(PathBuf::from("/app/engine/localsr-worker"));
    let installed = Some(PathBuf::from("/data/engines/old/engine/localsr-worker"));
    assert_eq!(
        choose_engine("", shipped.clone(), installed.clone()),
        shipped
    );
    assert_eq!(choose_engine("", None, installed.clone()), installed);
    assert_eq!(choose_engine("", shipped.clone(), None), shipped);
    assert_eq!(choose_engine("", None, None), None);
    // A host built for an engine id keeps running the installed engine of that id.
    assert_eq!(
        choose_engine("macos-arm64-mps-new", shipped.clone(), installed.clone()),
        installed
    );
    assert_eq!(
        choose_engine("macos-arm64-mps-new", shipped.clone(), None),
        shipped
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
        serde_json::from_str(include_str!("../../tests/fixtures/update-signature.json")).unwrap();
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
        serde_json::from_str(include_str!("../../tests/fixtures/update-parts.json")).unwrap();
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
    serde_json::from_str(include_str!("../../tests/fixtures/update-release.json")).unwrap()
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

// the bytes installed are the bytes verified, also when the download
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

// a planted link at a temporary name never redirects a write.
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

// a second installation never interleaves with the first.
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

// the working image keeps its name, and an interrupted swap is rolled
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

// Windows records success only once the new version runs.
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
        fs::read_to_string(f.backup.join("installed-version.txt")).ok() == Some("0.3.8-beta".into())
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
                    serde_json::from_slice::<PendingInstall>(&fs::read(&marker).unwrap()).is_ok(),
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

// the feed's own fields count only when the signed statement says so.
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
    let artifact = PathBuf::from(std::env::var_os("LOCALSR_SIGNED_ACCEPTANCE_ARTIFACT").unwrap());
    let public_key = include_str!("../../../../packaging/updates/production.pub").trim();
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

// a cancelled installer leaves the next engine's id in the shared
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

// every engine file the feed names is a plain, distinct name that
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

// a link in $APPIMAGE is followed, so the swap replaces the image
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

// the process of an interrupted swap exits after its rollback,
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

// once the new application is in place, a failed service restart
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

// a failed install resumes the worker only when the old one ended,
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

// the portable swap reads the installed version at the path it
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

// a retry is not charged again for what is already downloaded.
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
