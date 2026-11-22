use super::*;
use base64::Engine;
use std::io::Read;

fn release_fixture() -> serde_json::Value {
    serde_json::from_str(include_str!("../../tests/fixtures/update-release.json")).unwrap()
}

fn fixture_engine_files(
    base_url: &str,
) -> (
    UpdateConfig,
    String,
    Vec<u8>,
    String,
    Vec<u8>,
    EngineUpdate,
    ReleaseContract,
) {
    let value = release_fixture();
    let public_key = value["public_key"].as_str().unwrap().to_owned();
    let engine = &value["engine"];
    let manifest_text = engine["manifest"].as_str().unwrap();
    let manifest_bytes = manifest_text.as_bytes().to_vec();
    let part = &engine["parts"][0];
    let part_name = part["name"].as_str().unwrap().to_owned();
    let part_bytes = base64::engine::general_purpose::STANDARD
        .decode(part["data"].as_str().unwrap())
        .unwrap();

    let engine_id = "linux-x86_64-rocm-0123456789ab".to_owned();
    let manifest_file = DownloadFile {
        name: "engine-payload.json".into(),
        url: format!("{base_url}/engine-payload.json"),
        size: manifest_bytes.len() as u64,
        sha256: engine["manifest_sha256"].as_str().unwrap().into(),
        signature: engine["manifest_signature"].as_str().unwrap().into(),
    };
    let part_file = DownloadFile {
        name: part_name.clone(),
        url: format!("{base_url}/{part_name}"),
        size: part_bytes.len() as u64,
        sha256: part["sha256"].as_str().unwrap().into(),
        signature: part["signature"].as_str().unwrap().into(),
    };
    let engine_update = EngineUpdate {
        id: engine_id.clone(),
        manifest: manifest_file,
        parts: vec![part_file],
    };
    let contract = ReleaseContract {
        channel: "beta".into(),
        backend: "rocm".into(),
        kind: "native".into(),
        protocol: PROTOCOL_VERSION,
        engine_id: engine_id.clone(),
        size: 21,
        unpacked_size: 1024,
        sha256: value["package"]["sha256"].as_str().unwrap().into(),
        parts: Vec::new(),
        statement_v2: value["statement_v2"].as_str().unwrap().into(),
        statement_v2_signature: value["statement_v2_signature"].as_str().unwrap().into(),
        engine: Some(engine_update.clone()),
    };
    let config = UpdateConfig {
        public_key,
        feed: base_url.to_owned(),
        backend: "cpu".into(),
        engine_id: "linux-x86_64-cpu-000000000000".into(),
        kind: "native".into(),
    };
    (
        config,
        manifest_bytes
            .iter()
            .map(|b| *b as char)
            .collect::<String>(),
        manifest_bytes,
        part_name,
        part_bytes,
        engine_update,
        contract,
    )
}

fn feed_json_with_rocm(version: &str, contract: &ReleaseContract) -> serde_json::Value {
    serde_json::json!({
        "version": version,
        "notes": "Test release",
        "platforms": {
            "linux-x86_64-rocm-native": {
                "url": "http://127.0.0.1:1/rocm.AppImage",
                "signature": "unused",
                "localsr": serde_json::to_value(contract).unwrap(),
            },
            "darwin-aarch64-mps-native": {
                "url": "http://127.0.0.1:1/mac.app.tar.gz",
                "signature": "unused",
                "localsr": serde_json::to_value(contract).unwrap(),
            }
        }
    })
}

/// Serve files with HTTP Range (206 Partial Content) support and record `Range:` headers.
fn serve_with_ranges(
    files: Vec<(String, Vec<u8>)>,
    max_requests: usize,
) -> (String, Arc<Mutex<Vec<String>>>, std::thread::JoinHandle<()>) {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let seen_ranges = Arc::new(Mutex::new(Vec::new()));
    let ranges_clone = seen_ranges.clone();
    let server = std::thread::spawn(move || {
        let mut handled = 0;
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while handled < max_requests && std::time::Instant::now() < deadline {
            let mut socket = match listener.accept() {
                Ok((socket, _)) => socket,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(10));
                    continue;
                }
                Err(error) => panic!("{error}"),
            };
            socket.set_nonblocking(false).unwrap();
            handled += 1;
            let mut request = Vec::new();
            let mut buffer = [0u8; 2048];
            while !request.windows(4).any(|w| w == b"\r\n\r\n") {
                let count = socket.read(&mut buffer).unwrap();
                if count == 0 {
                    break;
                }
                request.extend_from_slice(&buffer[..count]);
            }
            let req_text = String::from_utf8_lossy(&request);
            let path = req_text
                .lines()
                .next()
                .and_then(|line| line.split_whitespace().nth(1))
                .unwrap_or("/")
                .trim_start_matches('/');
            let range_header = req_text.lines().find_map(|line| {
                let (name, val) = line.split_once(':')?;
                name.eq_ignore_ascii_case("range")
                    .then(|| val.trim().to_owned())
            });
            if let Some(ref r) = range_header {
                ranges_clone.lock().unwrap().push(r.clone());
            }
            let Some((_, body)) = files.iter().find(|(name, _)| name == path) else {
                let _ = write!(
                    socket,
                    "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                );
                continue;
            };
            if let Some(offset) = range_header
                .as_deref()
                .and_then(|r| r.strip_prefix("bytes="))
                .and_then(|r| r.strip_suffix('-'))
                .and_then(|s| s.parse::<usize>().ok())
            {
                if offset < body.len() {
                    let slice = &body[offset..];
                    let end = body.len() - 1;
                    let total = body.len();
                    write!(
                        socket,
                        "HTTP/1.1 206 Partial Content\r\nContent-Range: bytes {offset}-{end}/{total}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        slice.len()
                    )
                    .unwrap();
                    let _ = socket.write_all(slice);
                } else {
                    let _ = write!(
                        socket,
                        "HTTP/1.1 416 Range Not Satisfiable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    );
                }
            } else {
                write!(
                    socket,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )
                .unwrap();
                let _ = socket.write_all(body);
            }
        }
    });
    (base, seen_ranges, server)
}

#[test]
fn catalog_lists_all_backends_for_current_os_and_arch_while_app_updates_keep_compiled_backend() {
    let (config, _, _, _, _, _, contract) = fixture_engine_files("http://127.0.0.1:1");
    let feed = feed_json_with_rocm("0.3.7-beta", &contract);

    // Compiled backend is "cpu", yet engine catalog on linux/x86_64 lists "rocm" for the current version.
    let entries =
        parse_feed_engines_for(&feed, &config, "beta", "0.3.7-beta", "linux", "x86_64").unwrap();
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].backend, "rocm");
    assert_eq!(entries[0].engine_id, "linux-x86_64-rocm-0123456789ab");
    assert_eq!(entries[0].size, 308 + 195);
    assert_eq!(
        entries[0].manifest_url,
        "http://127.0.0.1:1/engine-payload.json"
    );

    // App update validation STILL rejects the entry whose backend differs from config.backend.
    assert!(updates::validate_contract(&config, "beta", "0.3.7-beta", &contract).is_err());

    // Other OS or arch does not see the linux-x86_64 entry.
    assert!(
        parse_feed_engines_for(&feed, &config, "beta", "0.3.7-beta", "windows", "x86_64")
            .unwrap()
            .is_empty()
    );
    assert!(
        parse_feed_engines_for(&feed, &config, "beta", "0.3.7-beta", "linux", "aarch64")
            .unwrap()
            .is_empty()
    );

    // Older feed version than installed is rejected (rollback protection).
    assert!(parse_feed_engines_for(&feed, &config, "beta", "0.4.0", "linux", "x86_64").is_err());

    // Tampered manifest size or forged statement is rejected.
    let mut tampered = contract.clone();
    tampered.engine.as_mut().unwrap().manifest.size += 1;
    let tampered_feed = feed_json_with_rocm("0.3.7-beta", &tampered);
    assert!(parse_feed_engines_for(
        &tampered_feed,
        &config,
        "beta",
        "0.3.7-beta",
        "linux",
        "x86_64"
    )
    .is_err());
}

#[test]
fn host_runs_active_installed_engine_with_different_id_when_os_arch_and_handshake_match() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let id = "windows-x86_64-cuda-9999";
    let exe = paths
        .next_root
        .join("engines")
        .join(id)
        .join("engine")
        .join(worker_executable_name());
    fs::create_dir_all(exe.parent().unwrap()).unwrap();
    fs::write(&exe, b"cuda-worker").unwrap();
    fs::write(paths.next_root.join("active-engine.txt"), id).unwrap();

    // Without handshake metadata, a host compiled for cpu ignores the different id.
    assert_eq!(
        updates::active_engine_for_host(
            &paths,
            false,
            "windows-x86_64-cpu-1111",
            "windows",
            "x86_64"
        ),
        None
    );

    // Even an id matching the bundle cannot make an unverified directory trusted.
    assert_eq!(
        updates::active_engine_for_host(&paths, false, id, "windows", "x86_64"),
        None
    );

    // Once handshake metadata confirms same OS/arch and protocol, the host runs it.
    let mut meta = EngineMeta {
        id: id.into(),
        backend: "cuda".into(),
        os: "windows".into(),
        arch: "x86_64".into(),
        protocol_version: PROTOCOL_VERSION,
        handshake_passed: true,
        version: "0.5.0-beta".into(),
    };
    write_engine_meta(&paths.next_root, &meta).unwrap();
    assert_eq!(
        updates::active_engine_for_host(
            &paths,
            false,
            "windows-x86_64-cpu-1111",
            "windows",
            "x86_64"
        ),
        Some(exe.clone())
    );

    // Wrong OS, wrong arch, failed handshake, or Store distribution is rejected.
    assert_eq!(
        updates::active_engine_for_host(
            &paths,
            false,
            "windows-x86_64-cpu-1111",
            "linux",
            "x86_64"
        ),
        None
    );
    assert_eq!(
        updates::active_engine_for_host(
            &paths,
            false,
            "windows-x86_64-cpu-1111",
            "windows",
            "aarch64"
        ),
        None
    );
    assert_eq!(
        updates::active_engine_for_host(
            &paths,
            true,
            "windows-x86_64-cpu-1111",
            "windows",
            "x86_64"
        ),
        None
    );
    meta.handshake_passed = false;
    write_engine_meta(&paths.next_root, &meta).unwrap();
    assert_eq!(
        updates::active_engine_for_host(
            &paths,
            false,
            "windows-x86_64-cpu-1111",
            "windows",
            "x86_64"
        ),
        None
    );

    // Bundled CPU engine stays the fallback when active installed engine is missing/deactivated.
    let bundled = Some(PathBuf::from("/app/engine/localsr-worker"));
    assert_eq!(
        updates::choose_engine("windows-x86_64-cpu-1111", bundled.clone(), None),
        bundled
    );
    updates::deactivate_active_engine(&paths).unwrap();
    assert!(!paths.next_root.join("active-engine.txt").exists());
}

#[test]
fn install_engine_resumes_from_partial_file_verifies_signature_and_activates() {
    let (_, _, manifest_bytes, part_name, part_bytes, _, mut contract) =
        fixture_engine_files("http://127.0.0.1:1");

    // Spin up a server knowing its exact base URL for beta.json + engine files.
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    contract.engine.as_mut().unwrap().manifest.url = format!("{base}/engine-payload.json");
    contract.engine.as_mut().unwrap().parts[0].url = format!("{base}/{part_name}");
    let feed_bytes = serde_json::to_vec(&feed_json_with_rocm("0.3.7-beta", &contract)).unwrap();
    let (mut config, _, _, _, _, _, _) = fixture_engine_files(&base);
    config.feed = base.clone();

    let seen_ranges = Arc::new(Mutex::new(Vec::new()));
    let ranges_clone = seen_ranges.clone();
    let files = [
        ("beta.json".to_owned(), feed_bytes),
        ("engine-payload.json".to_owned(), manifest_bytes),
        (part_name.clone(), part_bytes.clone()),
    ];
    let server = std::thread::spawn(move || {
        let mut handled = 0;
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while handled < 3 && std::time::Instant::now() < deadline {
            let mut socket = match listener.accept() {
                Ok((s, _)) => s,
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(10));
                    continue;
                }
                Err(e) => panic!("{e}"),
            };
            socket.set_nonblocking(false).unwrap();
            handled += 1;
            let mut req = Vec::new();
            let mut buf = [0u8; 2048];
            while !req.windows(4).any(|w| w == b"\r\n\r\n") {
                let n = socket.read(&mut buf).unwrap();
                if n == 0 {
                    break;
                }
                req.extend_from_slice(&buf[..n]);
            }
            let text = String::from_utf8_lossy(&req);
            let path = text
                .lines()
                .next()
                .and_then(|l| l.split_whitespace().nth(1))
                .unwrap_or("/")
                .trim_start_matches('/');
            let range = text.lines().find_map(|l| {
                let (k, v) = l.split_once(':')?;
                k.eq_ignore_ascii_case("range").then(|| v.trim().to_owned())
            });
            if let Some(ref r) = range {
                ranges_clone.lock().unwrap().push(r.clone());
            }
            let (_, body) = files.iter().find(|(n, _)| n == path).unwrap();
            if let Some(offset) = range
                .as_deref()
                .and_then(|r| r.strip_prefix("bytes="))
                .and_then(|r| r.strip_suffix('-'))
                .and_then(|s| s.parse::<usize>().ok())
            {
                let slice = &body[offset..];
                let end = body.len() - 1;
                let total = body.len();
                write!(
                    socket,
                    "HTTP/1.1 206 Partial Content\r\nContent-Range: bytes {offset}-{end}/{total}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    slice.len()
                )
                .unwrap();
                socket.write_all(slice).unwrap();
            } else {
                write!(
                    socket,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )
                .unwrap();
                socket.write_all(body).unwrap();
            }
        }
    });

    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    lock(&state.settings).unwrap().privacy_choice_made = true;

    // Seed a partial .part file in updates storage as if a prior download stopped after 80 bytes.
    let cache_dir = paths
        .next_root
        .join("updates")
        .join("engine-cache")
        .join("linux-x86_64-rocm-0123456789ab");
    fs::create_dir_all(&cache_dir).unwrap();
    fs::write(
        cache_dir.join(format!("{part_name}.part")),
        &part_bytes[..80],
    )
    .unwrap();

    let previous = paths
        .next_root
        .join("engines/linux-x86_64-rocm-0123456789ab/engine");
    fs::create_dir_all(&previous).unwrap();
    fs::write(
        previous.join(worker_executable_name()),
        b"unverified leftover",
    )
    .unwrap();
    let previous_during_handshake = previous.clone();

    tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        "rocm",
        "0.3.7-beta",
        "linux",
        "x86_64",
        move |exe| {
            assert_eq!(fs::read(exe).unwrap(), b"worker");
            assert_eq!(
                fs::read(previous_during_handshake.join(worker_executable_name())).unwrap(),
                b"unverified leftover"
            );
            Ok(())
        },
    ))
    .unwrap();
    server.join().unwrap();
    assert_eq!(seen_ranges.lock().unwrap().as_slice(), &["bytes=80-"]);
    assert_eq!(
        fs::read_to_string(paths.next_root.join("active-engine.txt")).unwrap(),
        "linux-x86_64-rocm-0123456789ab"
    );
    assert_eq!(
        fs::read(
            paths
                .next_root
                .join("engines/linux-x86_64-rocm-0123456789ab/engine/localsr-worker")
        )
        .unwrap(),
        b"worker"
    );
    assert!(!cache_dir.exists());
    assert_eq!(state.engines.snapshot_status().stage, "installed");
}

#[test]
fn download_file_resumable_refuses_wrong_hash_and_preserves_partial_on_cancel() {
    let (mut config, _, _, part_name, mut part_bytes, engine, _) =
        fixture_engine_files("http://127.0.0.1:1");
    let bad_part = {
        let mut corrupted = part_bytes.clone();
        corrupted[100] ^= 0xff;
        corrupted
    };
    let (base, _, server) = serve_with_ranges(vec![(part_name.clone(), bad_part)], 1);
    config.feed = base.clone();
    let mut part = engine.parts[0].clone();
    part.url = format!("{base}/{part_name}");

    let root = tempfile::tempdir().unwrap();
    let partial = root.path().join(format!("{part_name}.part"));
    fs::write(&partial, &part_bytes[..50]).unwrap();
    let cancel = AtomicBool::new(false);
    let err = tauri::async_runtime::block_on(download_file_resumable(
        &part,
        root.path(),
        &config,
        &cancel,
        &mut |_| {},
    ))
    .unwrap_err();
    server.join().unwrap();
    assert!(err.to_string().contains("checksum") || err.to_string().contains("signature"));
    // Untrusted complete file with bad hash must be removed, not kept.
    assert!(!partial.exists());
    assert!(!root.path().join(&part_name).exists());

    // Cancellation before/during transfer keeps .part for later resume.
    part_bytes.truncate(part_bytes.len());
    let (base2, _, server2) = serve_with_ranges(vec![(part_name.clone(), part_bytes.clone())], 1);
    config.feed = base2.clone();
    part.url = format!("{base2}/{part_name}");
    fs::write(&partial, &part_bytes[..40]).unwrap();
    let cancel_now = AtomicBool::new(true);
    let err = tauri::async_runtime::block_on(download_file_resumable(
        &part,
        root.path(),
        &config,
        &cancel_now,
        &mut |_| {},
    ))
    .unwrap_err();
    drop(server2);
    assert!(err.to_string().contains("cancelled"));
    assert_eq!(fs::read(&partial).unwrap(), &part_bytes[..40]);
}

#[test]
fn install_engine_refuses_privacy_unset_running_job_low_space_and_failed_handshake() {
    let (mut config, _, manifest_bytes, part_name, part_bytes, _, mut contract) =
        fixture_engine_files("http://127.0.0.1:1");
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());

    // 1. Before privacy_choice_made -> refused.
    let err = tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        "rocm",
        "0.3.7-beta",
        "linux",
        "x86_64",
        |_| Ok(()),
    ))
    .unwrap_err();
    assert!(err.to_string().contains("privacy"));

    lock(&state.settings).unwrap().privacy_choice_made = true;

    // 2. While a job runs -> refused.
    lock(&state.runtime).unwrap().active_job_id = "job-1".into();
    let err = tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        "rocm",
        "0.3.7-beta",
        "linux",
        "x86_64",
        |_| Ok(()),
    ))
    .unwrap_err();
    assert!(err.to_string().contains("active jobs"));
    lock(&state.runtime).unwrap().active_job_id.clear();

    // Pre-populate verified catalog so disk space and handshake tests don't need extra feed fetch.
    let (base, _, server) = serve_with_ranges(
        vec![
            ("engine-payload.json".into(), manifest_bytes),
            (part_name.clone(), part_bytes),
        ],
        2,
    );
    config.feed = base.clone();
    contract.engine.as_mut().unwrap().manifest.url = format!("{base}/engine-payload.json");
    contract.engine.as_mut().unwrap().parts[0].url = format!("{base}/{part_name}");
    let entries = parse_feed_engines_for(
        &feed_json_with_rocm("0.3.7-beta", &contract),
        &config,
        "beta",
        "0.3.7-beta",
        "linux",
        "x86_64",
    )
    .unwrap();
    *lock(&state.engines.catalog).unwrap() = entries;

    // 3. Too little disk space -> refused before download.
    FREE_SPACE_OVERRIDE.with(|cell| cell.set(Some(1024)));
    let err = tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        "rocm",
        "0.3.7-beta",
        "linux",
        "x86_64",
        |_| Ok(()),
    ))
    .unwrap_err();
    FREE_SPACE_OVERRIDE.with(|cell| cell.set(None));
    assert!(err.to_string().contains("disk space"));

    // 4. Failed handshake keeps previous active engine and removes broken install.
    fs::write(paths.next_root.join("active-engine.txt"), b"previous-id").unwrap();
    let err = tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        "rocm",
        "0.3.7-beta",
        "linux",
        "x86_64",
        |_| Err(AppError::Worker("handshake failed".into())),
    ))
    .unwrap_err();
    server.join().unwrap();
    assert!(err.to_string().contains("handshake failed"));
    assert_eq!(
        fs::read_to_string(paths.next_root.join("active-engine.txt")).unwrap(),
        "previous-id"
    );
    assert!(!paths
        .next_root
        .join("engines/linux-x86_64-rocm-0123456789ab")
        .exists());
}

#[test]
fn use_engine_and_remove_engine_switch_protect_active_and_remove_inactive() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let config = UpdateConfig {
        public_key: "KEY".into(),
        feed: "https://example.test/updates".into(),
        backend: "cpu".into(),
        engine_id: "linux-x86_64-cpu-v1".into(),
        kind: "native".into(),
    };

    let rocm_id = "linux-x86_64-rocm-v1";
    let rocm_exe = paths
        .next_root
        .join("engines")
        .join(rocm_id)
        .join("engine")
        .join(worker_executable_name());
    fs::create_dir_all(rocm_exe.parent().unwrap()).unwrap();
    fs::write(&rocm_exe, b"rocm").unwrap();
    write_engine_meta(
        &paths.next_root,
        &EngineMeta {
            id: rocm_id.into(),
            backend: "rocm".into(),
            os: "linux".into(),
            arch: "x86_64".into(),
            protocol_version: PROTOCOL_VERSION,
            handshake_passed: true,
            version: "0.5.0-beta".into(),
        },
    )
    .unwrap();

    // Switch to installed ROCm engine.
    use_engine_for(&state, None, &config, "rocm", "linux", "x86_64", |_| Ok(())).unwrap();
    assert_eq!(
        fs::read_to_string(paths.next_root.join("active-engine.txt")).unwrap(),
        rocm_id
    );

    // Cannot remove the active engine or "bundled" or missing engine.
    assert!(remove_engine_in(&state, None, &config, rocm_id).is_err());
    assert!(remove_engine_in(&state, None, &config, "bundled").is_err());
    assert!(remove_engine_in(&state, None, &config, "../escape").is_err());

    // Cannot switch to a backend that is not installed.
    assert!(use_engine_for(&state, None, &config, "cuda", "linux", "x86_64", |_| Ok(())).is_err());

    // Switch back to bundled CPU engine.
    use_engine_for(&state, None, &config, "cpu", "linux", "x86_64", |_| Ok(())).unwrap();
    assert!(!paths.next_root.join("active-engine.txt").exists());

    // Now the inactive ROCm engine can be removed.
    remove_engine_in(&state, None, &config, rocm_id).unwrap();
    assert!(!paths.next_root.join("engines").join(rocm_id).exists());
}

#[test]
fn import_engine_verifies_signatures_reuses_install_manifest_and_enforces_grants() {
    let (config, _, manifest_bytes, part_name, part_bytes, engine, _) =
        fixture_engine_files("http://127.0.0.1:1");
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());

    let import_dir = root.path().join("offline-download");
    fs::create_dir_all(&import_dir).unwrap();
    let manifest_path = import_dir.join("LocalSR-test.engine-payload.json");
    let manifest_sig_path = import_dir.join("LocalSR-test.engine-payload.json.sig");
    let part_path = import_dir.join(&part_name);
    let part_sig_path = import_dir.join(format!("{part_name}.sig"));

    fs::write(&manifest_path, &manifest_bytes).unwrap();
    fs::write(&manifest_sig_path, &engine.manifest.signature).unwrap();
    fs::write(&part_path, &part_bytes).unwrap();
    fs::write(&part_sig_path, &engine.parts[0].signature).unwrap();

    // Grants check: ungranted manifest path is refused, granted path succeeds.
    assert!(state.grants.check_engine_manifest(&manifest_path).is_err());
    state.grants.grant_engine_manifest(&manifest_path);
    assert!(state.grants.check_engine_manifest(&manifest_path).is_ok());

    // Wrong OS (importing ROCM on Windows) is refused.
    assert!(import_engine_for(
        &state,
        None,
        &config,
        &manifest_path,
        "windows",
        "x86_64",
        |_| Ok(())
    )
    .is_err());

    // Valid import on Linux installs via install_manifest, passes handshake, and activates.
    let meta = import_engine_for(
        &state,
        None,
        &config,
        &manifest_path,
        "linux",
        "x86_64",
        |exe| {
            assert_eq!(fs::read(exe).unwrap(), b"worker");
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(meta.backend, "rocm");
    assert_eq!(
        fs::read_to_string(paths.next_root.join("active-engine.txt")).unwrap(),
        meta.id
    );

    // Tampered part or missing .sig is refused.
    fs::write(&part_path, b"corrupted").unwrap();
    assert!(import_engine_for(
        &state,
        None,
        &config,
        &manifest_path,
        "linux",
        "x86_64",
        |_| Ok(())
    )
    .is_err());
    fs::write(&part_path, &part_bytes).unwrap();
    fs::remove_file(&manifest_sig_path).unwrap();
    assert!(import_engine_for(
        &state,
        None,
        &config,
        &manifest_path,
        "linux",
        "x86_64",
        |_| Ok(())
    )
    .is_err());
}

fn check_install_failure_status(failure: &'static str) {
    let (config, _, manifest_bytes, part_name, part_bytes, _, contract) =
        fixture_engine_files("http://127.0.0.1:1");
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    lock(&state.settings).unwrap().privacy_choice_made = true;
    let entries = parse_feed_engines_for(
        &feed_json_with_rocm("0.3.7-beta", &contract),
        &config,
        "beta",
        "0.3.7-beta",
        "linux",
        "x86_64",
    )
    .unwrap();
    let meta_path = paths
        .next_root
        .join("engines")
        .join(&entries[0].engine_id)
        .join("meta.json");
    let cache = paths
        .next_root
        .join("updates/engine-cache")
        .join(&entries[0].engine_id);
    fs::create_dir_all(&cache).unwrap();
    fs::write(cache.join("engine-payload.json"), manifest_bytes).unwrap();
    fs::write(cache.join(part_name), part_bytes).unwrap();
    *lock(&state.engines.catalog).unwrap() = entries;
    let during = state.clone();
    let result = tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        "rocm",
        "0.3.7-beta",
        "linux",
        "x86_64",
        move |_| {
            match failure {
                "job" => lock(&during.runtime).unwrap().active_job_id = "job".into(),
                "metadata" => fs::create_dir(meta_path).unwrap(),
                "panic" => panic!("handshake task failed"),
                "late_cancel" => {
                    during.engines.cancel.store(true, Ordering::SeqCst);
                    return Err(AppError::Worker("injected handshake failure".into()));
                }
                _ => unreachable!(),
            }
            Ok(())
        },
    ));
    assert!(result.is_err());
    let events = lock(&state.engines.terminal_events).unwrap();
    assert_eq!(events.len(), 1, "expected one terminal event for {failure}");
    assert_eq!(events[0].stage, "error");
    if failure == "late_cancel" {
        assert!(events[0]
            .error
            .as_deref()
            .unwrap()
            .contains("injected handshake failure"));
    }
}

#[test]
fn install_reports_error_when_job_starts_during_handshake() {
    check_install_failure_status("job");
}

#[test]
fn install_reports_metadata_and_handshake_task_errors() {
    check_install_failure_status("metadata");
    check_install_failure_status("panic");
}

#[test]
fn late_cancel_does_not_reclassify_handshake_failure() {
    check_install_failure_status("late_cancel");
}

#[test]
fn reimport_handshake_failure_preserves_active_engine() {
    let (config, _, manifest_bytes, part_name, part_bytes, engine, _) =
        fixture_engine_files("http://127.0.0.1:1");
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let import_dir = root.path().join("import");
    fs::create_dir_all(&import_dir).unwrap();
    let manifest = import_dir.join("payload.json");
    fs::write(&manifest, &manifest_bytes).unwrap();
    fs::write(
        import_dir.join("payload.json.sig"),
        &engine.manifest.signature,
    )
    .unwrap();
    fs::write(import_dir.join(&part_name), &part_bytes).unwrap();
    fs::write(
        import_dir.join(format!("{part_name}.sig")),
        &engine.parts[0].signature,
    )
    .unwrap();
    let meta = import_engine_for(&state, None, &config, &manifest, "linux", "x86_64", |_| {
        Ok(())
    })
    .unwrap();
    let exe = paths
        .next_root
        .join("engines")
        .join(&meta.id)
        .join("engine")
        .join(worker_executable_name());
    assert!(import_engine_for(
        &state,
        None,
        &config,
        &manifest,
        "linux",
        "x86_64",
        |_| Err(AppError::Worker("failed".into()))
    )
    .is_err());
    assert_eq!(fs::read(exe).unwrap(), b"worker");
    assert_eq!(
        fs::read_to_string(paths.next_root.join("active-engine.txt")).unwrap(),
        meta.id
    );
    lock(&state.worker.failed_engines)
        .unwrap()
        .insert(meta.id.clone());
    import_engine_for(&state, None, &config, &manifest, "linux", "x86_64", |_| {
        Ok(())
    })
    .unwrap();
    assert!(!lock(&state.worker.failed_engines)
        .unwrap()
        .contains(&meta.id));
}

#[test]
fn app_updates_keep_the_users_chosen_backend() {
    let (config, _, _, _, _, _, rocm_contract) = fixture_engine_files("http://127.0.0.1:1");
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());

    // Pretend the user installed an older ROCm engine on a Linux host whose compiled backend is "cpu".
    let old_rocm = "linux-x86_64-rocm-old";
    let old_exe = paths
        .next_root
        .join("engines")
        .join(old_rocm)
        .join("engine")
        .join(worker_executable_name());
    fs::create_dir_all(old_exe.parent().unwrap()).unwrap();
    fs::write(&old_exe, b"old-rocm").unwrap();
    write_engine_meta(
        &paths.next_root,
        &EngineMeta {
            id: old_rocm.into(),
            backend: "rocm".into(),
            os: "linux".into(),
            arch: "x86_64".into(),
            protocol_version: PROTOCOL_VERSION,
            handshake_passed: true,
            version: "0.3.6-beta".into(),
        },
    )
    .unwrap();
    fs::write(paths.next_root.join("active-engine.txt"), old_rocm).unwrap();
    assert_eq!(
        updates::chosen_backend_for(&paths, &config, "linux", "x86_64"),
        "rocm"
    );

    // A new release feed has both cpu and rocm entries; the update resolves the rocm engine.
    let mut cpu_contract = rocm_contract.clone();
    cpu_contract.backend = "cpu".into();
    cpu_contract.engine_id = "linux-x86_64-cpu-new".into();
    let feed = serde_json::json!({
        "version": "0.3.7-beta",
        "platforms": {
            updates::platform_key("linux", "x86_64", "cpu", "native"): {
                "localsr": serde_json::to_value(&cpu_contract).unwrap(),
            },
            updates::platform_key("linux", "x86_64", "rocm", "native"): {
                "localsr": serde_json::to_value(&rocm_contract).unwrap(),
            }
        }
    });

    let resolved = updates::resolve_update_engine_for_backend_on(
        &feed,
        &config,
        &cpu_contract,
        "beta",
        "0.3.7-beta",
        "0.3.6-beta",
        &paths,
        "linux",
        "x86_64",
    )
    .unwrap();
    assert_eq!(resolved.backend, "rocm");
    assert_eq!(resolved.engine_id, "linux-x86_64-rocm-0123456789ab");

    // Switching back to bundled CPU makes updates follow "cpu" again.
    use_engine_for(&state, None, &config, "cpu", "linux", "x86_64", |_| Ok(())).unwrap();
    assert_eq!(
        updates::chosen_backend_for(&paths, &config, "linux", "x86_64"),
        "cpu"
    );
    let resolved_cpu = updates::resolve_update_engine_for_backend_on(
        &feed,
        &config,
        &cpu_contract,
        "beta",
        "0.3.7-beta",
        "0.3.6-beta",
        &paths,
        "linux",
        "x86_64",
    )
    .unwrap();
    assert_eq!(resolved_cpu.backend, "cpu");
    assert_eq!(resolved_cpu.engine_id, "linux-x86_64-cpu-new");
}

#[cfg(unix)]
#[test]
fn real_worker_handshake_passes_and_fails_with_executable_script() {
    use std::os::unix::fs::PermissionsExt;

    let root = tempfile::tempdir().unwrap();
    let good_worker = root.path().join("good-worker");
    fs::write(
        &good_worker,
        r#"#!/bin/sh
printf '{"type":"worker_ready","data":{}}\n'
IFS= read -r _handshake
printf '{"type":"engine_info","data":{"protocol_version":1,"minimum_protocol_version":1,"engine_id":"localsr.pytorch-spandrel","engine_version":"0.5.0","features":[],"model_formats":[],"video_engines":[]}}\n'
IFS= read -r _shutdown
exit 0
"#,
    )
    .unwrap();
    fs::set_permissions(&good_worker, fs::Permissions::from_mode(0o755)).unwrap();
    let info =
        crate::headless_smoke::run_worker_handshake(&good_worker, Duration::from_secs(10), true)
            .unwrap();
    assert_eq!(info.engine_id, "localsr.pytorch-spandrel");

    let bad_worker = root.path().join("bad-worker");
    fs::write(&bad_worker, "#!/bin/sh\nexit 1\n").unwrap();
    fs::set_permissions(&bad_worker, fs::Permissions::from_mode(0o755)).unwrap();
    assert!(crate::headless_smoke::run_worker_handshake(
        &bad_worker,
        Duration::from_secs(10),
        true
    )
    .is_err());
}

#[test]
fn snapshot_exposes_engine_manager_state_with_snake_case_wire_names() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = AppState::from_paths(paths).unwrap();
    let snapshot = state.snapshot().unwrap();
    let json = serde_json::to_value(&snapshot).unwrap();
    let engines = json.get("engines").unwrap();
    for field in [
        "supported",
        "managed_by_store",
        "configured",
        "active_backend",
        "active_engine_id",
        "bundled_backend",
        "installed",
        "recommendation",
        "catalog",
        "download",
    ] {
        assert!(engines.get(field).is_some(), "missing engines.{field}");
    }
    assert!(engines["recommendation"].get("backend").is_some());
    assert_eq!(engines["download"]["stage"], "idle");
}

#[test]
fn fallback_marks_bundled_active_and_allows_removing_failed_compiled_engine() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = AppState::from_paths(paths.clone()).unwrap();
    let (mut config, ..) = fixture_engine_files("http://127.0.0.1:1");
    config.engine_id = "compiled".into();
    let worker = paths
        .next_root
        .join("engines/compiled/engine")
        .join(worker_executable_name());
    fs::create_dir_all(worker.parent().unwrap()).unwrap();
    fs::write(&worker, b"worker").unwrap();
    state
        .worker
        .use_bundled_fallback
        .store(true, Ordering::SeqCst);
    let snapshot = snapshot_for(&state, &config, EngineRecommendation::default());
    assert!(snapshot
        .installed
        .iter()
        .any(|engine| engine.id == "compiled" && !engine.active));
    assert!(snapshot
        .installed
        .iter()
        .any(|engine| engine.bundled && engine.active));
    remove_engine_in(&state, None, &config, "compiled").unwrap();
    assert!(!worker.exists());
}

#[test]
fn explicit_use_allows_previously_failed_engine() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let (config, ..) = fixture_engine_files("http://127.0.0.1:1");
    let id = "windows-x86_64-cuda-repaired";
    let exe = paths
        .next_root
        .join("engines")
        .join(id)
        .join("engine")
        .join(worker_executable_name());
    fs::create_dir_all(exe.parent().unwrap()).unwrap();
    fs::write(&exe, b"worker").unwrap();
    write_engine_meta(
        &paths.next_root,
        &EngineMeta {
            id: id.into(),
            backend: "cuda".into(),
            os: "windows".into(),
            arch: "x86_64".into(),
            protocol_version: PROTOCOL_VERSION,
            handshake_passed: true,
            version: String::new(),
        },
    )
    .unwrap();
    lock(&state.worker.failed_engines)
        .unwrap()
        .insert(id.into());
    use_engine_for(&state, None, &config, "cuda", "windows", "x86_64", |_| {
        Ok(())
    })
    .unwrap();
    assert!(!lock(&state.worker.failed_engines).unwrap().contains(id));
}

#[test]
fn interrupted_reimport_restores_active_engine_at_startup_and_retry() {
    let (config, _, manifest_bytes, part_name, part_bytes, engine, _) =
        fixture_engine_files("http://127.0.0.1:1");
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let import_dir = root.path().join("import");
    fs::create_dir_all(&import_dir).unwrap();
    let manifest = import_dir.join("payload.json");
    fs::write(&manifest, &manifest_bytes).unwrap();
    fs::write(
        import_dir.join("payload.json.sig"),
        &engine.manifest.signature,
    )
    .unwrap();
    fs::write(import_dir.join(&part_name), &part_bytes).unwrap();
    fs::write(
        import_dir.join(format!("{part_name}.sig")),
        &engine.parts[0].signature,
    )
    .unwrap();
    let meta = import_engine_for(&state, None, &config, &manifest, "linux", "x86_64", |_| {
        Ok(())
    })
    .unwrap();
    let engine_root = paths.next_root.join("engines").join(&meta.id);
    let engine_dir = engine_root.join("engine");
    fs::rename(
        &engine_dir,
        engine_root.join(".backup-123e4567-e89b-42d3-a456-426614174000"),
    )
    .unwrap();
    drop(state);
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    assert_eq!(
        fs::read(engine_dir.join(worker_executable_name())).unwrap(),
        b"worker"
    );
    fs::rename(
        &engine_dir,
        engine_root.join(".backup-123e4567-e89b-42d3-a456-426614174000"),
    )
    .unwrap();
    assert!(import_engine_for(
        &state,
        None,
        &config,
        &manifest,
        "linux",
        "x86_64",
        |_| Err(AppError::Worker("handshake failed".into()))
    )
    .is_err());
    assert_eq!(
        fs::read(engine_dir.join(worker_executable_name())).unwrap(),
        b"worker"
    );
}

#[test]
fn snapshot_reports_worker_choice_when_active_id_is_excluded() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let (mut config, ..) = fixture_engine_files("http://127.0.0.1:1");
    config.engine_id.clear();
    config.backend = "cpu".into();
    let id = "failed-engine";
    let exe = paths
        .next_root
        .join("engines")
        .join(id)
        .join("engine")
        .join(worker_executable_name());
    fs::create_dir_all(exe.parent().unwrap()).unwrap();
    fs::write(&exe, b"worker").unwrap();
    write_engine_meta(
        &paths.next_root,
        &EngineMeta {
            id: id.into(),
            backend: "cuda".into(),
            os: std::env::consts::OS.into(),
            arch: std::env::consts::ARCH.into(),
            protocol_version: PROTOCOL_VERSION,
            handshake_passed: true,
            version: String::new(),
        },
    )
    .unwrap();
    fs::write(paths.next_root.join("active-engine.txt"), id).unwrap();
    let bundled = root.path().join("bundled-worker");
    fs::write(&bundled, b"bundled").unwrap();
    *lock(&state.bundled_engine_path).unwrap() = Some(bundled.clone());
    lock(&state.worker.failed_engines)
        .unwrap()
        .insert(id.into());
    let selected = updates::worker_engine_choice(
        &paths,
        Some(bundled.clone()),
        &lock(&state.worker.failed_engines).unwrap(),
    )
    .unwrap();
    assert!(selected.1.is_none());
    let snapshot = snapshot_for(&state, &config, EngineRecommendation::default());
    assert_eq!(snapshot.active_backend, "cpu");
    assert!(snapshot
        .installed
        .iter()
        .any(|engine| engine.bundled && engine.active));
    use_engine_for(
        &state,
        None,
        &config,
        "cuda",
        std::env::consts::OS,
        std::env::consts::ARCH,
        |_| Ok(()),
    )
    .unwrap();
    let selected = updates::worker_engine_choice(
        &paths,
        Some(bundled),
        &lock(&state.worker.failed_engines).unwrap(),
    )
    .unwrap();
    let expected_backend = if selected.1.is_some() { "cuda" } else { "cpu" };
    assert_eq!(
        snapshot_for(&state, &config, EngineRecommendation::default()).active_backend,
        expected_backend
    );
    *lock(&state.bundled_engine_path).unwrap() = None;
    let selected =
        updates::worker_engine_choice(&paths, None, &lock(&state.worker.failed_engines).unwrap())
            .unwrap();
    assert_eq!(selected.1.as_deref(), Some(id));
    assert_eq!(
        snapshot_for(&state, &config, EngineRecommendation::default()).active_engine_id,
        id
    );
}

#[test]
fn reimport_recovery_keeps_ambiguous_backups_and_removes_obsolete_ones() {
    let root = tempfile::tempdir().unwrap();
    let engine_root = root.path().join("engines/example");
    let engine = engine_root.join("engine");
    let first = engine_root.join(".backup-123e4567-e89b-42d3-a456-426614174000");
    let second = engine_root.join(".backup-123e4567-e89b-42d3-a456-426614174001");
    fs::create_dir_all(&engine).unwrap();
    fs::create_dir_all(&first).unwrap();
    recover_interrupted_reimport(root.path(), "example").unwrap();
    assert!(engine.is_dir());
    assert!(!first.exists());
    fs::remove_dir(&engine).unwrap();
    fs::create_dir_all(&first).unwrap();
    fs::create_dir_all(&second).unwrap();
    assert!(recover_interrupted_reimport(root.path(), "example").is_err());
    assert!(first.is_dir() && second.is_dir());
}
