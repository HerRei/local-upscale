use super::*;
use crate::updates::{worker_executable_name, write_engine_meta};
use base64::Engine;
use std::io::Read;

#[test]
fn use_rejects_engine_without_verified_install_metadata() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let (mut config, ..) = fixture_engine_files("http://127.0.0.1:1");
    config.engine_id.clear();
    let executable = paths
        .next_root
        .join("engines/linux-x86_64-rocm-test/engine")
        .join(worker_executable_name());
    fs::create_dir_all(executable.parent().unwrap()).unwrap();
    fs::write(&executable, b"unverified").unwrap();
    let installed = list_installed_engines_for(&paths, &config, None, "linux", "x86_64");
    assert!(installed
        .iter()
        .any(|engine| engine.backend == "rocm" && engine.needs_reinstall));
    let result = use_engine_for(&state, None, &config, "rocm", "linux", "x86_64", |_| {
        panic!("an unverified worker must never be launched")
    });
    assert!(result.is_err());
    assert!(!paths.next_root.join("active-engine.txt").exists());
}

#[test]
fn restoring_backup_invalidates_metadata_for_replaced_engine() {
    let root = tempfile::tempdir().unwrap();
    let engine_root = root.path().join("engines/example");
    let backup = engine_root.join(".backup-123e4567-e89b-42d3-a456-426614174000");
    fs::create_dir_all(&backup).unwrap();
    fs::write(backup.join(worker_executable_name()), b"old worker").unwrap();
    fs::write(engine_root.join("meta.json"), b"metadata for replacement").unwrap();
    recover_interrupted_reimport(root.path(), "example").unwrap();
    assert!(engine_root.join("engine").is_dir());
    assert!(!engine_root.join("meta.json").exists());
}

fn write_replacement_fixture(next_root: &Path, id: &str, content: &[u8]) -> EngineMeta {
    let engine = next_root.join("engines").join(id).join("engine");
    fs::create_dir_all(&engine).unwrap();
    fs::write(engine.join(worker_executable_name()), content).unwrap();
    let meta = EngineMeta {
        id: id.into(),
        backend: "rocm".into(),
        os: std::env::consts::OS.into(),
        arch: std::env::consts::ARCH.into(),
        protocol_version: PROTOCOL_VERSION,
        handshake_passed: true,
        verified_install: true,
        version: String::from_utf8(content.to_vec()).unwrap(),
        files: updates::test_engine_files(next_root, id),
    };
    write_engine_meta(next_root, &meta).unwrap();
    crate::engine_files::test_signing::write_list_for_installed(next_root, id);
    meta
}

#[test]
fn engine_replacement_keeps_the_complete_old_install_until_start() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let id = "gpu";
    let old_meta = write_replacement_fixture(&paths.next_root, id, b"old");
    let source = tempfile::tempdir().unwrap();
    let new_meta = write_replacement_fixture(source.path(), id, b"new");
    let staged = updates::stage_engine_with(
        &paths.next_root,
        id,
        |destination| {
            fs::create_dir(destination)?;
            fs::write(destination.join(worker_executable_name()), b"new")?;
            Ok((
                new_meta.files.clone(),
                crate::engine_files::test_signing::list_of(id, &new_meta.files.clone()),
            ))
        },
        |_| Ok(()),
    )
    .unwrap();
    commit_installed_engine(&state, None, staged, &new_meta).unwrap();
    assert_eq!(read_engine_meta(&paths.next_root, id), Some(new_meta));
    let backup = paths.next_root.join("engines/.previous-gpu");
    assert_eq!(
        fs::read(backup.join("engine").join(worker_executable_name())).unwrap(),
        b"old"
    );
    assert_eq!(
        serde_json::from_slice::<EngineMeta>(&fs::read(backup.join("meta.json")).unwrap()).unwrap(),
        old_meta
    );
    let token = updates::pending_engine_token(&paths.next_root, id).unwrap();
    updates::confirm_engine_start(&paths.next_root, id, "earlier-install").unwrap();
    assert!(backup.is_dir());
    updates::confirm_engine_start(&paths.next_root, id, &token).unwrap();
    assert!(!backup.exists());
    assert!(updates::pending_engine_token(&paths.next_root, id).is_none());
}

#[test]
fn engine_replacement_rollback_leaves_an_already_restored_copy_in_place() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    let id = "gpu";
    let old = write_replacement_fixture(&paths.next_root, id, b"old");
    let source = tempfile::tempdir().unwrap();
    let new = write_replacement_fixture(source.path(), id, b"new");
    let staged = updates::stage_engine_with(
        &paths.next_root,
        id,
        |destination| {
            fs::create_dir(destination)?;
            fs::write(destination.join(worker_executable_name()), b"new")?;
            Ok((
                new.files.clone(),
                crate::engine_files::test_signing::list_of(id, &new.files.clone()),
            ))
        },
        |_| Ok(()),
    )
    .unwrap();
    let swap = staged.publish(Some(&new), Some("99.0.0")).unwrap();
    // An app-update smoke start can restore the old engine before the
    // updater rolls back the app itself.
    assert!(updates::restore_verified_engine_backup(&paths.next_root, id).unwrap());
    swap.rollback().unwrap();
    assert_eq!(read_engine_meta(&paths.next_root, id), Some(old));
}

#[test]
fn engine_replacement_recovers_verified_files_and_metadata_at_each_step() {
    for (step, expected) in [
        ("partial-stage", b"old"),
        ("complete-stage", b"old"),
        ("old-renamed", b"old"),
        ("new-renamed", b"new"),
        ("new-incomplete", b"old"),
        ("new-same-size-corruption", b"old"),
        ("backup-retired", b"new"),
        ("backup-deleted", b"new"),
        ("app-update-rolled-back", b"old"),
    ] {
        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        let id = "gpu";
        let engines = paths.next_root.join("engines");
        let target = engines.join(id);
        let backup = engines.join(".previous-gpu");
        let retired = engines.join(".retired-123e4567-e89b-42d3-a456-426614174000");
        let staging = engines.join(".import-123e4567-e89b-42d3-a456-426614174000");
        let old = write_replacement_fixture(&paths.next_root, id, b"old");
        let new = write_replacement_fixture(&paths.next_root, "staging", b"new");
        fs::rename(engines.join("staging"), &staging).unwrap();
        let new = EngineMeta {
            id: id.into(),
            ..new
        };
        fs::write(staging.join("meta.json"), serde_json::to_vec(&new).unwrap()).unwrap();
        crate::engine_files::test_signing::list_of(id, &new.files)
            .write_into(&staging)
            .unwrap();
        if step == "partial-stage" {
            fs::remove_file(staging.join("engine").join(worker_executable_name())).unwrap();
        }
        if !matches!(step, "partial-stage" | "complete-stage") {
            fs::rename(&target, &backup).unwrap();
        }
        if !matches!(step, "partial-stage" | "complete-stage" | "old-renamed") {
            fs::rename(&staging, &target).unwrap();
        }
        let worker = target.join("engine").join(worker_executable_name());
        match step {
            "new-incomplete" => fs::remove_file(&worker).unwrap(),
            "new-same-size-corruption" => fs::write(&worker, b"bad").unwrap(),
            "backup-retired" => fs::rename(&backup, &retired).unwrap(),
            "backup-deleted" => fs::remove_dir_all(&backup).unwrap(),
            "app-update-rolled-back" => fs::write(
                target.join(".pending-start.json"),
                br#"{"token":"replacement","app_version":"99.0.0"}"#,
            )
            .unwrap(),
            _ => {}
        }
        fs::write(paths.next_root.join("active-engine.txt"), id).unwrap();
        recover_interrupted_engine_installs(&paths.next_root);
        let mut failed = std::collections::HashSet::new();
        let (choice, verification_failed) =
            updates::verified_worker_engine_choice(&paths, None, &mut failed);
        assert_eq!(choice, Some((worker.clone(), Some(id.into()))), "{step}");
        assert!(!verification_failed, "{step}");
        assert_eq!(fs::read(&worker).unwrap(), expected, "{step}");
        let expected_meta = if expected == b"old" { old } else { new };
        assert_eq!(
            read_engine_meta(&paths.next_root, id),
            Some(expected_meta),
            "{step}"
        );
        assert_eq!(backup.is_dir(), step == "new-renamed", "{step}");
        assert!(!retired.exists(), "{step}");
    }
}

fn assert_linked_engine_folders_are_refused(link: impl Fn(&Path, &Path)) {
    for relative in [
        "engines",
        "engines/gpu",
        "engines/gpu/engine",
        "engines/gpu/engine/unused",
    ] {
        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
        let (config, ..) = fixture_engine_files("http://127.0.0.1:1");
        let id = "gpu";
        let old_meta = write_replacement_fixture(&paths.next_root, id, b"old");
        fs::create_dir(paths.next_root.join("engines/gpu/engine/unused")).unwrap();
        let linked = paths.next_root.join(relative);
        let external = root.path().join("external");
        fs::rename(&linked, &external).unwrap();
        fs::write(external.join("sentinel"), b"outside engine").unwrap();
        link(&external, &linked);

        let os = std::env::consts::OS;
        let arch = std::env::consts::ARCH;
        assert!(
            !list_installed_engines_for(&paths, &config, None, os, arch)
                .iter()
                .any(|engine| engine.id == id && !engine.needs_reinstall),
            "{relative}"
        );
        let pointer = paths.next_root.join("active-engine.txt");
        fs::write(&pointer, id).unwrap();
        let bundled = root.path().join("bundled-worker");
        let mut failed = std::collections::HashSet::new();
        assert_eq!(
            updates::verified_worker_engine_choice(&paths, Some(bundled.clone()), &mut failed).0,
            Some((bundled, None)),
            "{relative}"
        );
        assert!(
            verify_engine_for(&state, None, &config, id, os, arch).is_err(),
            "{relative}"
        );
        assert!(
            use_engine_for(&state, None, &config, "rocm", os, arch, |_| {
                panic!("linked engine must not be launched")
            })
            .is_err(),
            "{relative}"
        );
        assert!(
            updates::stage_engine_with(
                &paths.next_root,
                id,
                |_| panic!("linked engine must be refused before unpacking"),
                |_| panic!("linked engine must not be launched"),
            )
            .is_err(),
            "{relative}"
        );
        assert!(
            remove_engine_in(&state, None, &config, id).is_err(),
            "{relative}"
        );
        assert_eq!(
            fs::read(external.join("sentinel")).unwrap(),
            b"outside engine"
        );
        if matches!(relative, "engines" | "engines/gpu") {
            let external_meta = if relative == "engines" {
                external.join(id)
            } else {
                external
            };
            assert_eq!(
                serde_json::from_slice::<EngineMeta>(
                    &fs::read(external_meta.join("meta.json")).unwrap()
                )
                .unwrap(),
                old_meta
            );
        }
    }
}

#[cfg(unix)]
#[test]
fn linked_engine_folders_are_refused_on_unix() {
    assert_linked_engine_folders_are_refused(|target, link| {
        std::os::unix::fs::symlink(target, link).unwrap();
    });
}

#[cfg(windows)]
#[test]
fn linked_engine_folders_are_refused_on_windows() {
    assert_linked_engine_folders_are_refused(|target, link| {
        // cmd reads a forward slash as a switch ("engines/gpu" -> "/gpu"); mklink needs
        // backslashes.
        let windows_path = |path: &std::path::Path| path.to_string_lossy().replace('/', "\\");
        let output = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(windows_path(link))
            .arg(windows_path(target))
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    });
}

#[test]
fn legacy_handshake_metadata_does_not_authorize_engine() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let id = "linux-x86_64-rocm-old";
    let engine = paths.next_root.join("engines").join(id);
    fs::create_dir_all(engine.join("engine")).unwrap();
    fs::write(
        engine.join("engine").join(worker_executable_name()),
        b"worker",
    )
    .unwrap();
    fs::write(
        engine.join("meta.json"),
        serde_json::to_vec(&serde_json::json!({
            "id": id, "backend": "rocm", "os": "linux", "arch": "x86_64",
            "protocol_version": PROTOCOL_VERSION, "handshake_passed": true, "version": "0.5.1-beta"
        }))
        .unwrap(),
    )
    .unwrap();
    fs::write(paths.next_root.join("active-engine.txt"), id).unwrap();
    assert!(updates::active_engine_for_host(&paths, false, id, "linux", "x86_64").is_none());
}

fn release_fixture() -> serde_json::Value {
    serde_json::from_str(include_str!("../../tests/fixtures/update-release.json")).unwrap()
}

fn host_release_fixture() -> serde_json::Value {
    #[cfg(windows)]
    {
        serde_json::from_str(include_str!(
            "../../tests/fixtures/update-release-windows.json"
        ))
        .unwrap()
    }
    #[cfg(not(windows))]
    {
        release_fixture()
    }
}

fn host_test_os() -> &'static str {
    if cfg!(windows) {
        "windows"
    } else {
        "linux"
    }
}

fn host_test_backend() -> &'static str {
    if cfg!(windows) {
        "cuda"
    } else {
        "rocm"
    }
}

fn host_test_engine_id() -> &'static str {
    if cfg!(windows) {
        "windows-x86_64-cuda-0123456789ab"
    } else {
        "linux-x86_64-rocm-0123456789ab"
    }
}

/// The engine fixture's signed file list, as the tests' cache folders hold it.
fn fixture_list_bytes(
    manifest: &[u8],
    part_name: &str,
    part: &[u8],
    id: &str,
    os: &str,
) -> Vec<u8> {
    crate::engine_files::test_signing::package_list_from(
        manifest,
        &[(part_name.to_owned(), part.to_vec())],
        id,
        (os, "x86_64"),
        "",
        "engine-files.json",
    )
    .0
}

fn fixture_engine_files_from(
    value: serde_json::Value,
    base_url: &str,
    backend: &str,
    engine_id: &str,
    config_engine_id: &str,
    list_os: &str,
) -> (
    UpdateConfig,
    String,
    Vec<u8>,
    String,
    Vec<u8>,
    EngineUpdate,
    ReleaseContract,
) {
    let public_key = value["public_key"].as_str().unwrap().to_owned();
    let engine = &value["engine"];
    let manifest_text = engine["manifest"].as_str().unwrap();
    let manifest_bytes = manifest_text.as_bytes().to_vec();
    let part = &engine["parts"][0];
    let part_name = part["name"].as_str().unwrap().to_owned();
    let part_bytes = base64::engine::general_purpose::STANDARD
        .decode(part["data"].as_str().unwrap())
        .unwrap();

    let engine_id = engine_id.to_owned();
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
    let (_, list_file) = crate::engine_files::test_signing::package_list_from(
        &manifest_bytes,
        &[(part_name.clone(), part_bytes.clone())],
        &engine_id,
        (list_os, "x86_64"),
        base_url,
        "engine-files.json",
    );
    let engine_update = EngineUpdate {
        id: engine_id.clone(),
        manifest: manifest_file,
        parts: vec![part_file],
        files: Some(list_file),
    };
    let contract = ReleaseContract {
        channel: "beta".into(),
        backend: backend.into(),
        kind: "native".into(),
        protocol: PROTOCOL_VERSION,
        engine_id,
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
        engine_id: config_engine_id.into(),
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
    fixture_engine_files_from(
        release_fixture(),
        base_url,
        "rocm",
        "linux-x86_64-rocm-0123456789ab",
        "linux-x86_64-cpu-000000000000",
        "linux",
    )
}

fn host_fixture_engine_files(
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
    let config_engine_id = if cfg!(windows) {
        "windows-x86_64-cpu-000000000000"
    } else {
        "linux-x86_64-cpu-000000000000"
    };
    fixture_engine_files_from(
        host_release_fixture(),
        base_url,
        host_test_backend(),
        host_test_engine_id(),
        config_engine_id,
        host_test_os(),
    )
}

fn feed_json_with_host_backend(version: &str, contract: &ReleaseContract) -> serde_json::Value {
    let key = updates::platform_key(host_test_os(), "x86_64", host_test_backend(), "native");
    let package_url = if cfg!(windows) {
        "http://127.0.0.1:1/cuda.exe"
    } else {
        "http://127.0.0.1:1/rocm.AppImage"
    };
    serde_json::json!({
        "version": version,
        "notes": "Test release",
        "platforms": {
            key: {
                "url": package_url,
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
fn a_deb_install_downloads_the_engines_of_the_native_feed_entries() {
    // The .deb has no feed entry of its own (apt installs its updates), but GPU engines
    // still come from the signed native entries, into the user's app-data folder.
    let (config, _, _, _, _, _, contract) = fixture_engine_files("http://127.0.0.1:1");
    let feed = feed_json_with_rocm("0.3.7-beta", &contract);
    let deb = UpdateConfig {
        kind: "deb".into(),
        ..config.clone()
    };
    let entries =
        parse_feed_engines_for(&feed, &deb, "beta", "0.3.7-beta", "linux", "x86_64").unwrap();
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].backend, "rocm");
    assert_eq!(entries[0].engine_id, "linux-x86_64-rocm-0123456789ab");
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
    let listed = contract.engine.as_ref().unwrap().files.as_ref().unwrap();
    assert_eq!(entries[0].engine.files.as_ref().unwrap().name, listed.name);
    assert_eq!(entries[0].size, 308 + 195 + listed.size);
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
        verified_install: true,
        version: "0.5.0-beta".into(),
        files: updates::test_engine_files(&paths.next_root, id),
    };
    write_engine_meta(&paths.next_root, &meta).unwrap();
    crate::engine_files::test_signing::write_list_on(
        &paths.next_root,
        id,
        &meta.files,
        "windows",
        "x86_64",
    );
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
        host_fixture_engine_files("http://127.0.0.1:1");

    // Spin up a server knowing its exact base URL for beta.json + engine files.
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    contract.engine.as_mut().unwrap().manifest.url = format!("{base}/engine-payload.json");
    contract.engine.as_mut().unwrap().parts[0].url = format!("{base}/{part_name}");
    contract
        .engine
        .as_mut()
        .unwrap()
        .files
        .as_mut()
        .unwrap()
        .url = format!("{base}/engine-files.json");
    let feed_bytes =
        serde_json::to_vec(&feed_json_with_host_backend("0.3.7-beta", &contract)).unwrap();
    let (mut config, _, _, _, _, _, _) = host_fixture_engine_files(&base);
    config.feed = base.clone();

    let seen_ranges = Arc::new(Mutex::new(Vec::new()));
    let ranges_clone = seen_ranges.clone();
    let list_bytes = fixture_list_bytes(
        &manifest_bytes,
        &part_name,
        &part_bytes,
        host_test_engine_id(),
        host_test_os(),
    );
    let files = [
        ("beta.json".to_owned(), feed_bytes),
        ("engine-payload.json".to_owned(), manifest_bytes),
        ("engine-files.json".to_owned(), list_bytes),
        (part_name.clone(), part_bytes.clone()),
    ];
    let server = std::thread::spawn(move || {
        let mut handled = 0;
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while handled < 4 && std::time::Instant::now() < deadline {
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

    let engine_id = host_test_engine_id();
    // Seed a partial .part file in updates storage as if a prior download stopped after 80 bytes.
    let cache_dir = paths
        .next_root
        .join("updates")
        .join("engine-cache")
        .join(engine_id);
    fs::create_dir_all(&cache_dir).unwrap();
    fs::write(
        cache_dir.join(format!("{part_name}.part")),
        &part_bytes[..80],
    )
    .unwrap();

    let previous = paths
        .next_root
        .join("engines")
        .join(engine_id)
        .join("engine");
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
        host_test_backend(),
        "0.3.7-beta",
        host_test_os(),
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
        engine_id
    );
    assert_eq!(
        fs::read(
            paths
                .next_root
                .join("engines")
                .join(engine_id)
                .join("engine")
                .join(worker_executable_name())
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
        host_fixture_engine_files("http://127.0.0.1:1");
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());

    // 1. Before privacy_choice_made -> refused.
    let err = tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        host_test_backend(),
        "0.3.7-beta",
        host_test_os(),
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
        host_test_backend(),
        "0.3.7-beta",
        host_test_os(),
        "x86_64",
        |_| Ok(()),
    ))
    .unwrap_err();
    assert!(err.to_string().contains("active jobs"));
    lock(&state.runtime).unwrap().active_job_id.clear();

    // Pre-populate verified catalog so disk space and handshake tests don't need extra feed fetch.
    let list_bytes = fixture_list_bytes(
        &manifest_bytes,
        &part_name,
        &part_bytes,
        host_test_engine_id(),
        host_test_os(),
    );
    let (base, _, server) = serve_with_ranges(
        vec![
            ("engine-payload.json".into(), manifest_bytes),
            ("engine-files.json".into(), list_bytes),
            (part_name.clone(), part_bytes),
        ],
        3,
    );
    config.feed = base.clone();
    contract.engine.as_mut().unwrap().manifest.url = format!("{base}/engine-payload.json");
    contract.engine.as_mut().unwrap().parts[0].url = format!("{base}/{part_name}");
    contract
        .engine
        .as_mut()
        .unwrap()
        .files
        .as_mut()
        .unwrap()
        .url = format!("{base}/engine-files.json");
    let entries = parse_feed_engines_for(
        &feed_json_with_host_backend("0.3.7-beta", &contract),
        &config,
        "beta",
        "0.3.7-beta",
        host_test_os(),
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
        host_test_backend(),
        "0.3.7-beta",
        host_test_os(),
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
        host_test_backend(),
        "0.3.7-beta",
        host_test_os(),
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
        .join("engines")
        .join(host_test_engine_id())
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
            verified_install: true,
            version: "0.5.0-beta".into(),
            files: updates::test_engine_files(&paths.next_root, rocm_id),
        },
    )
    .unwrap();
    crate::engine_files::test_signing::write_list_on(
        &paths.next_root,
        rocm_id,
        &updates::test_engine_files(&paths.next_root, rocm_id),
        "linux",
        "x86_64",
    );

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
        host_fixture_engine_files("http://127.0.0.1:1");
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
    write_import_list(&manifest_path, host_test_engine_id(), host_test_os());

    // Grants check: ungranted manifest path is refused, granted path succeeds.
    assert!(state.grants.check_engine_manifest(&manifest_path).is_err());
    state.grants.grant_engine_manifest(&manifest_path);
    assert!(state.grants.check_engine_manifest(&manifest_path).is_ok());

    // Unsupported backend on the other OS (e.g. ROCM on Windows, or CUDA on macOS;
    // on Windows our CUDA fixture is refused on macOS, or we pass "macos" as wrong OS).
    assert!(import_engine_for(
        &state,
        None,
        &config,
        &manifest_path,
        "0.5.1-beta",
        "macos",
        "x86_64",
        |_| Ok(())
    )
    .is_err());

    // Valid import on the host OS installs via install_manifest, passes handshake, and activates.
    let meta = import_engine_for(
        &state,
        None,
        &config,
        &manifest_path,
        "0.5.1-beta",
        host_test_os(),
        "x86_64",
        |exe| {
            assert_eq!(fs::read(exe).unwrap(), b"worker");
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(meta.backend, host_test_backend());
    assert_eq!(
        fs::read_to_string(paths.next_root.join("active-engine.txt")).unwrap(),
        meta.id
    );

    // Without its signed file list, or with one signed by another key, it is refused.
    let list_path = import_dir.join("LocalSR-test.engine-files.json");
    let list_sig_path = import_dir.join("LocalSR-test.engine-files.json.sig");
    let list_signature = fs::read_to_string(&list_sig_path).unwrap();
    let import = || {
        import_engine_for(
            &state,
            None,
            &config,
            &manifest_path,
            "0.5.1-beta",
            host_test_os(),
            "x86_64",
            |_| Ok(()),
        )
    };
    fs::write(
        &list_sig_path,
        crate::engine_files::test_signing::sign_with_another_key(&fs::read(&list_path).unwrap()),
    )
    .unwrap();
    assert!(import().is_err());
    fs::remove_file(&list_sig_path).unwrap();
    assert!(import().is_err());
    fs::write(&list_sig_path, &list_signature).unwrap();
    fs::remove_file(&list_path).unwrap();
    assert!(import().unwrap_err().to_string().contains("file list"));
    write_import_list(&manifest_path, host_test_engine_id(), host_test_os());
    assert!(import().is_ok());

    // Tampered part or missing .sig is refused.
    fs::write(&part_path, b"corrupted").unwrap();
    assert!(import_engine_for(
        &state,
        None,
        &config,
        &manifest_path,
        "0.5.1-beta",
        host_test_os(),
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
        "0.5.1-beta",
        host_test_os(),
        "x86_64",
        |_| Ok(())
    )
    .is_err());
}

fn check_install_failure_status(failure: &'static str) {
    let (config, _, manifest_bytes, part_name, part_bytes, _, contract) =
        host_fixture_engine_files("http://127.0.0.1:1");
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    lock(&state.settings).unwrap().privacy_choice_made = true;
    let entries = parse_feed_engines_for(
        &feed_json_with_host_backend("0.3.7-beta", &contract),
        &config,
        "beta",
        "0.3.7-beta",
        host_test_os(),
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
    fs::write(
        cache.join("engine-files.json"),
        fixture_list_bytes(
            &manifest_bytes,
            &part_name,
            &part_bytes,
            &entries[0].engine_id,
            host_test_os(),
        ),
    )
    .unwrap();
    fs::write(cache.join("engine-payload.json"), manifest_bytes).unwrap();
    fs::write(cache.join(part_name), part_bytes).unwrap();
    *lock(&state.engines.catalog).unwrap() = entries;
    let during = state.clone();
    let result = tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        host_test_backend(),
        "0.3.7-beta",
        host_test_os(),
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
        host_fixture_engine_files("http://127.0.0.1:1");
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
    write_import_list(&manifest, host_test_engine_id(), host_test_os());
    let meta = import_engine_for(
        &state,
        None,
        &config,
        &manifest,
        "0.5.1-beta",
        host_test_os(),
        "x86_64",
        |_| Ok(()),
    )
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
        "0.5.1-beta",
        host_test_os(),
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
    import_engine_for(
        &state,
        None,
        &config,
        &manifest,
        "0.5.1-beta",
        host_test_os(),
        "x86_64",
        |_| Ok(()),
    )
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
            verified_install: true,
            version: "0.3.6-beta".into(),
            files: updates::test_engine_files(&paths.next_root, old_rocm),
        },
    )
    .unwrap();
    crate::engine_files::test_signing::write_list_on(
        &paths.next_root,
        old_rocm,
        &updates::test_engine_files(&paths.next_root, old_rocm),
        "linux",
        "x86_64",
    );
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
    let info = crate::headless_smoke::run_worker_handshake(
        &good_worker,
        Duration::from_secs(10),
        true,
        root.path(),
    )
    .unwrap();
    assert_eq!(info.engine_id, "localsr.pytorch-spandrel");

    let bad_worker = root.path().join("bad-worker");
    fs::write(&bad_worker, "#!/bin/sh\nexit 1\n").unwrap();
    fs::set_permissions(&bad_worker, fs::Permissions::from_mode(0o755)).unwrap();
    assert!(crate::headless_smoke::run_worker_handshake(
        &bad_worker,
        Duration::from_secs(10),
        true,
        root.path()
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
            verified_install: true,
            version: String::new(),
            files: updates::test_engine_files(&paths.next_root, id),
        },
    )
    .unwrap();
    crate::engine_files::test_signing::write_list_on(
        &paths.next_root,
        id,
        &updates::test_engine_files(&paths.next_root, id),
        "windows",
        "x86_64",
    );
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
        host_fixture_engine_files("http://127.0.0.1:1");
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
    write_import_list(&manifest, host_test_engine_id(), host_test_os());
    let meta = import_engine_for(
        &state,
        None,
        &config,
        &manifest,
        "0.5.1-beta",
        host_test_os(),
        "x86_64",
        |_| Ok(()),
    )
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
    let mut failed = std::collections::HashSet::new();
    assert_eq!(
        updates::verify_active_engine_at_start_for(&paths, &mut failed, host_test_os(), "x86_64"),
        None
    );
    assert_eq!(read_engine_meta(&paths.next_root, &meta.id), Some(meta));
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
        "0.5.1-beta",
        host_test_os(),
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
            verified_install: true,
            version: String::new(),
            files: updates::test_engine_files(&paths.next_root, id),
        },
    )
    .unwrap();
    crate::engine_files::test_signing::write_list_on(
        &paths.next_root,
        id,
        &updates::test_engine_files(&paths.next_root, id),
        std::env::consts::OS,
        std::env::consts::ARCH,
    );
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

/// The signed file list an offline download keeps beside `manifest_path`, for engine
/// `id` on `os` unless the manifest names its platform and engine.
fn write_import_list(manifest_path: &Path, id: &str, os: &str) {
    let manifest = fs::read(manifest_path).unwrap();
    let parsed = engine_payload::parse_manifest(&manifest).unwrap();
    let directory = manifest_path.parent().unwrap();
    let parts: Vec<(String, Vec<u8>)> = parsed
        .parts
        .iter()
        .map(|part| {
            (
                part.filename.clone(),
                fs::read(directory.join(&part.filename)).unwrap(),
            )
        })
        .collect();
    let id = parsed.engine_id.as_deref().unwrap_or(id);
    let os = parsed.os.as_deref().unwrap_or(os);
    let arch = parsed.arch.as_deref().unwrap_or("x86_64");
    let (bytes, file) = crate::engine_files::test_signing::package_list_from(
        &manifest,
        &parts,
        id,
        (os, arch),
        "",
        "engine-files.json",
    );
    let list = imported_list_path(manifest_path);
    fs::write(&list, bytes).unwrap();
    fs::write(format!("{}.sig", list.display()), file.signature).unwrap();
}

fn schema2_fixture() -> serde_json::Value {
    #[cfg(windows)]
    {
        serde_json::from_str(include_str!(
            "../../tests/fixtures/engine-schema2-windows.json"
        ))
        .unwrap()
    }
    #[cfg(not(windows))]
    {
        serde_json::from_str(include_str!("../../tests/fixtures/engine-schema2.json")).unwrap()
    }
}

fn write_schema2_case(dir: &Path, fixture: &serde_json::Value, case: &str) -> PathBuf {
    let part = &fixture["part"];
    let part_name = part["name"].as_str().unwrap();
    let part_bytes = base64::engine::general_purpose::STANDARD
        .decode(part["data"].as_str().unwrap())
        .unwrap();
    let manifest_path = dir.join(format!("{case}.engine-payload.json"));
    let entry = &fixture["cases"][case];
    fs::write(
        &manifest_path,
        entry["manifest"].as_str().unwrap().as_bytes(),
    )
    .unwrap();
    fs::write(
        dir.join(format!("{case}.engine-payload.json.sig")),
        entry["manifest_signature"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(dir.join(part_name), part_bytes).unwrap();
    fs::write(
        dir.join(format!("{part_name}.sig")),
        part["signature"].as_str().unwrap(),
    )
    .unwrap();
    write_import_list(&manifest_path, host_test_engine_id(), host_test_os());
    manifest_path
}

#[test]
fn import_schema2_checks_os_arch_protocol_and_refuses_older_versions() {
    let fixture = schema2_fixture();
    let (mut config, ..) = host_fixture_engine_files("http://127.0.0.1:1");
    config.public_key = fixture["public_key"].as_str().unwrap().to_owned();
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let import_dir = root.path().join("import");
    fs::create_dir_all(&import_dir).unwrap();
    let os = host_test_os();
    let (v2_case, older_case, newer_case, expected_id) = if cfg!(windows) {
        ("cuda_052", "cuda_051", "cuda_053", "windows-x86_64-cuda-v2")
    } else {
        ("rocm_052", "rocm_051", "rocm_053", "linux-x86_64-rocm-v2")
    };

    // Wrong OS, arch, and protocol are refused.
    for case in ["wrong_os", "wrong_arch", "wrong_protocol"] {
        let manifest = write_schema2_case(&import_dir, &fixture, case);
        assert!(
            import_engine_for(
                &state,
                None,
                &config,
                &manifest,
                "0.5.1-beta",
                os,
                "x86_64",
                |_| Ok(()),
            )
            .is_err(),
            "expected {case} to be refused"
        );
    }

    // Importing an engine older than the bundled CPU version is refused.
    let old_cpu = write_schema2_case(&import_dir, &fixture, "cpu_051");
    assert!(import_engine_for(
        &state,
        None,
        &config,
        &old_cpu,
        "0.5.2-beta",
        os,
        "x86_64",
        |_| Ok(()),
    )
    .is_err());

    // Valid schema 2 import succeeds, sets version/id/files, and rejects older 0.5.1-beta.
    let v2_manifest = write_schema2_case(&import_dir, &fixture, v2_case);
    let meta = import_engine_for(
        &state,
        None,
        &config,
        &v2_manifest,
        "0.5.2-beta",
        os,
        "x86_64",
        |_| Ok(()),
    )
    .unwrap();
    assert_eq!(meta.id, expected_id);
    assert_eq!(meta.version, "0.5.2-beta");
    assert!(!meta.files.is_empty());

    let older_gpu = write_schema2_case(&import_dir, &fixture, older_case);
    assert!(import_engine_for(
        &state,
        None,
        &config,
        &older_gpu,
        "0.5.2-beta",
        os,
        "x86_64",
        |_| Ok(()),
    )
    .is_err());

    // Same version (0.5.2-beta) and newer version (0.5.3-beta) are allowed.
    let meta_same = import_engine_for(
        &state,
        None,
        &config,
        &v2_manifest,
        "0.5.2-beta",
        os,
        "x86_64",
        |_| Ok(()),
    )
    .unwrap();
    assert_eq!(meta_same.version, "0.5.2-beta");
    let newer_gpu = write_schema2_case(&import_dir, &fixture, newer_case);
    let meta_newer = import_engine_for(
        &state,
        None,
        &config,
        &newer_gpu,
        "0.5.2-beta",
        os,
        "x86_64",
        |_| Ok(()),
    )
    .unwrap();
    assert_eq!(meta_newer.version, "0.5.3-beta");
}

#[test]
fn recorded_file_list_catches_size_change_at_start_and_hash_change_on_verify() {
    let (config, _, manifest_bytes, part_name, part_bytes, engine, _) =
        host_fixture_engine_files("http://127.0.0.1:1");
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
    write_import_list(&manifest, host_test_engine_id(), host_test_os());
    let os = host_test_os();
    let meta = import_engine_for(
        &state,
        None,
        &config,
        &manifest,
        "0.5.2-beta",
        os,
        "x86_64",
        |_| Ok(()),
    )
    .unwrap();
    assert!(meta
        .files
        .iter()
        .any(|f| f.path == worker_executable_name()));

    // Clean full verification passes.
    verify_engine_for(&state, None, &config, &meta.id, os, "x86_64").unwrap();

    // Same-size byte corruption passes quick size check, but fails full verify_engine_for,
    // marks the engine needs_reinstall, deactivates it, and falls back to bundled.
    let worker_file = paths
        .next_root
        .join("engines")
        .join(&meta.id)
        .join("engine")
        .join(worker_executable_name());
    let original = fs::read(&worker_file).unwrap();
    let mut tampered = original.clone();
    tampered[0] ^= 0xff;
    assert_eq!(tampered.len(), original.len());
    fs::write(&worker_file, &tampered).unwrap();
    assert!(verify_engine_for(&state, None, &config, &meta.id, os, "x86_64").is_err());
    assert!(!paths.next_root.join("active-engine.txt").exists());
    assert!(state.worker.use_bundled_fallback.load(Ordering::SeqCst));
    let listed = list_installed_engines_for(&paths, &config, None, os, "x86_64");
    assert!(listed
        .iter()
        .any(|item| item.id == meta.id && item.needs_reinstall && !item.active));
    assert!(listed.iter().any(|item| item.bundled && item.active));

    // Reimport restores valid files, then changing file size is caught at worker start quick check.
    state
        .worker
        .use_bundled_fallback
        .store(false, Ordering::SeqCst);
    import_engine_for(
        &state,
        None,
        &config,
        &manifest,
        "0.5.2-beta",
        os,
        "x86_64",
        |_| Ok(()),
    )
    .unwrap();
    fs::write(&worker_file, b"short").unwrap();
    let mut failed = lock(&state.worker.failed_engines).unwrap();
    let quick_failed =
        updates::verify_active_engine_at_start_for(&paths, &mut failed, os, "x86_64");
    drop(failed);
    assert_eq!(quick_failed.as_deref(), Some(meta.id.as_str()));
    assert!(!paths.next_root.join("active-engine.txt").exists());
    let listed_after_quick = list_installed_engines_for(&paths, &config, None, os, "x86_64");
    assert!(listed_after_quick
        .iter()
        .any(|item| item.id == meta.id && item.needs_reinstall && !item.active));
    let bundled = root.path().join("bundled-worker");
    fs::write(&bundled, b"bundled").unwrap();
    let choice = updates::worker_engine_choice(
        &paths,
        Some(bundled.clone()),
        &lock(&state.worker.failed_engines).unwrap(),
    );
    assert_eq!(choice, Some((bundled, None)));
}

#[test]
fn same_id_reinstall_keeps_backup_until_activation_and_holds_scheduler_lock() {
    let (config, _, manifest_bytes, part_name, part_bytes, _, contract) =
        host_fixture_engine_files("http://127.0.0.1:1");
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    lock(&state.settings).unwrap().privacy_choice_made = true;
    let os = host_test_os();
    let backend = host_test_backend();
    let entries = parse_feed_engines_for(
        &feed_json_with_host_backend("0.3.7-beta", &contract),
        &config,
        "beta",
        "0.3.7-beta",
        os,
        "x86_64",
    )
    .unwrap();
    let id = entries[0].engine_id.clone();
    let cache = paths.next_root.join("updates/engine-cache").join(&id);
    fs::create_dir_all(&cache).unwrap();
    fs::write(cache.join("engine-payload.json"), &manifest_bytes).unwrap();
    fs::write(cache.join(&part_name), &part_bytes).unwrap();
    fs::write(
        cache.join("engine-files.json"),
        fixture_list_bytes(&manifest_bytes, &part_name, &part_bytes, &id, os),
    )
    .unwrap();
    *lock(&state.engines.catalog).unwrap() = entries.clone();

    // Install initial copy of `id`, then replace its worker bytes with a sentinel.
    tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        backend,
        "0.3.7-beta",
        os,
        "x86_64",
        |_| Ok(()),
    ))
    .unwrap();
    let engine_root = paths.next_root.join("engines").join(&id);
    let worker_file = engine_root.join("engine").join(worker_executable_name());
    fs::write(&worker_file, b"previous-copy").unwrap();
    let mut old_meta = read_engine_meta(&paths.next_root, &id).unwrap();
    old_meta.version = "0.3.6-beta".into();
    old_meta.files = updates::test_engine_files(&paths.next_root, &id);
    write_engine_meta(&paths.next_root, &old_meta).unwrap();

    // Repopulate cache and block activation by making meta.json a directory.
    // The backup must keep and restore the previous copy when activation fails.
    fs::create_dir_all(&cache).unwrap();
    fs::write(cache.join("engine-payload.json"), &manifest_bytes).unwrap();
    fs::write(cache.join(&part_name), &part_bytes).unwrap();
    fs::write(
        cache.join("engine-files.json"),
        fixture_list_bytes(&manifest_bytes, &part_name, &part_bytes, &id, os),
    )
    .unwrap();
    let meta_path = engine_root.join("meta.json");
    fs::remove_file(&meta_path).unwrap();
    fs::create_dir(&meta_path).unwrap();
    let failed = tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        backend,
        "0.3.7-beta",
        os,
        "x86_64",
        |_| Ok(()),
    ));
    assert!(failed.is_err());
    assert_eq!(fs::read(&worker_file).unwrap(), b"previous-copy");
    assert_eq!(
        fs::read_to_string(paths.next_root.join("active-engine.txt")).unwrap(),
        id
    );
    fs::remove_dir(&meta_path).unwrap();
    write_engine_meta(&paths.next_root, &old_meta).unwrap();

    // A job starting while the scheduler lock is held cannot race mid-swap; if a job
    // becomes active before swap starts, reinstall aborts and leaves the previous copy intact.
    fs::create_dir_all(&cache).unwrap();
    fs::write(cache.join("engine-payload.json"), &manifest_bytes).unwrap();
    fs::write(cache.join(&part_name), &part_bytes).unwrap();
    fs::write(
        cache.join("engine-files.json"),
        fixture_list_bytes(&manifest_bytes, &part_name, &part_bytes, &id, os),
    )
    .unwrap();
    let state_for_job = state.clone();
    let during_handshake_res = tauri::async_runtime::block_on(install_engine_for(
        &state,
        None,
        &config,
        backend,
        "0.3.7-beta",
        os,
        "x86_64",
        move |_| {
            let _scheduler = lock(&state_for_job.scheduler)?;
            lock(&state_for_job.runtime)?.active_job_id = "job-1".into();
            Ok(())
        },
    ));
    assert!(during_handshake_res.is_err());
    assert_eq!(fs::read(&worker_file).unwrap(), b"previous-copy");
    assert_eq!(
        read_engine_meta(&paths.next_root, &id).unwrap().version,
        "0.3.6-beta"
    );
}

const LEFTOVER: &str = "123e4567-e89b-42d3-a456-426614174000";

fn engine_worker(next_root: &Path, id: &str) -> PathBuf {
    next_root
        .join("engines")
        .join(id)
        .join("engine")
        .join(worker_executable_name())
}

fn stage_fixture(
    next_root: &Path,
    id: &str,
    content: &'static [u8],
) -> (updates::StagedEngine, EngineMeta) {
    let source = tempfile::tempdir().unwrap();
    let meta = write_replacement_fixture(source.path(), id, content);
    let files = meta.files.clone();
    let staged = updates::stage_engine_with(
        next_root,
        id,
        |destination| {
            fs::create_dir(destination)?;
            fs::write(destination.join(worker_executable_name()), content)?;
            let list = crate::engine_files::test_signing::list_of(id, &files);
            Ok((files, list))
        },
        |_| Ok(()),
    )
    .unwrap();
    (staged, meta)
}

fn retired_folders(next_root: &Path) -> Vec<PathBuf> {
    fs::read_dir(next_root.join("engines"))
        .unwrap()
        .flatten()
        .filter(|entry| entry.file_name().to_string_lossy().starts_with(".retired-"))
        .map(|entry| entry.path())
        .collect()
}

#[test]
fn same_size_swap_of_an_extensionless_executable_is_rejected_at_start() {
    let mut cases = vec![
        ("localsr-media", b"\x7fELF media 1".to_vec()),
        (
            "_internal/torch/bin/torch_shm_manager",
            b"\xcf\xfa\xed\xfe shm 1".to_vec(),
        ),
        ("_internal/helper", b"MZ helper 1".to_vec()),
        ("_internal/universal", b"\xca\xfe\xba\xbe fat 1".to_vec()),
    ];
    if cfg!(unix) {
        cases.push(("_internal/protoc", b"#!/bin/sh\necho 1\n".to_vec()));
    }
    for swapped in [None, Some(0), Some(1), Some(2), Some(3), Some(4)] {
        if swapped.is_some_and(|index| index >= cases.len()) {
            continue;
        }
        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        let id = "gpu";
        let engine = paths.next_root.join("engines").join(id).join("engine");
        fs::create_dir_all(&engine).unwrap();
        fs::write(engine.join(worker_executable_name()), b"worker").unwrap();
        for (relative, content) in &cases {
            let path = engine.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, content).unwrap();
            #[cfg(unix)]
            if relative.ends_with("protoc") {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
            }
        }
        write_engine_meta(
            &paths.next_root,
            &EngineMeta {
                id: id.into(),
                backend: "rocm".into(),
                os: std::env::consts::OS.into(),
                arch: std::env::consts::ARCH.into(),
                protocol_version: PROTOCOL_VERSION,
                handshake_passed: true,
                verified_install: true,
                version: "1".into(),
                files: updates::test_engine_files(&paths.next_root, id),
            },
        )
        .unwrap();
        crate::engine_files::test_signing::write_list_on(
            &paths.next_root,
            id,
            &updates::test_engine_files(&paths.next_root, id),
            std::env::consts::OS,
            std::env::consts::ARCH,
        );
        fs::write(paths.next_root.join("active-engine.txt"), id).unwrap();
        if let Some(index) = swapped {
            let (relative, content) = &cases[index];
            let mut changed = content.clone();
            *changed
                .iter_mut()
                .rev()
                .find(|byte| **byte == b'1')
                .unwrap() = b'2';
            // Writing keeps the executable bit of the existing file.
            fs::write(engine.join(relative), changed).unwrap();
        }
        let mut failed = std::collections::HashSet::new();
        let (choice, verification_failed) =
            updates::verified_worker_engine_choice(&paths, None, &mut failed);
        if let Some(index) = swapped {
            assert_eq!(choice, None, "{}", cases[index].0);
            assert!(verification_failed, "{}", cases[index].0);
        } else {
            assert_eq!(
                choice,
                Some((engine.join(worker_executable_name()), Some(id.into())))
            );
            assert!(!verification_failed);
        }
    }
}

#[test]
fn verification_waits_for_engine_file_changes_before_marking() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let (config, ..) = fixture_engine_files("http://127.0.0.1:1");
    let id = "gpu";
    write_replacement_fixture(&paths.next_root, id, b"good");
    let pointer = paths.next_root.join("active-engine.txt");
    fs::write(&pointer, id).unwrap();
    let worker = engine_worker(&paths.next_root, id);
    fs::write(&worker, b"bad!").unwrap();
    // A start check that restores the good copy holds `engine_files`.
    let engine_files = lock(&state.worker.engine_files).unwrap();
    let verifying = {
        let state = state.clone();
        std::thread::spawn(move || {
            verify_engine_for(
                &state,
                None,
                &config,
                id,
                std::env::consts::OS,
                std::env::consts::ARCH,
            )
        })
    };
    std::thread::sleep(Duration::from_millis(300));
    fs::write(&worker, b"good").unwrap();
    drop(engine_files);
    verifying.join().unwrap().unwrap();
    assert!(
        read_engine_meta(&paths.next_root, id)
            .unwrap()
            .verified_install
    );
    assert!(pointer.is_file());
    assert!(lock(&state.worker.failed_engines).unwrap().is_empty());
}

#[cfg(unix)]
#[test]
fn an_undeletable_legacy_backup_does_not_mark_a_verified_engine() {
    use std::os::unix::fs::PermissionsExt;
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let id = "gpu";
    write_replacement_fixture(&paths.next_root, id, b"good");
    let pointer = paths.next_root.join("active-engine.txt");
    fs::write(&pointer, id).unwrap();
    let leftover = paths
        .next_root
        .join("engines")
        .join(id)
        .join(format!(".backup-{LEFTOVER}"));
    let locked = leftover.join("locked");
    fs::create_dir_all(&locked).unwrap();
    fs::write(locked.join("file"), b"left by 0.5.x").unwrap();
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o555)).unwrap();
    let mut failed = std::collections::HashSet::new();
    let (choice, verification_failed) =
        updates::verified_worker_engine_choice(&paths, None, &mut failed);
    let meta = read_engine_meta(&paths.next_root, id);
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o755)).unwrap();
    assert_eq!(
        choice,
        Some((engine_worker(&paths.next_root, id), Some(id.into())))
    );
    assert!(!verification_failed);
    assert!(meta.unwrap().verified_install);
    assert!(pointer.is_file());
    assert!(leftover.is_dir());
    // The next start retries the cleanup.
    let (choice, _) = updates::verified_worker_engine_choice(&paths, None, &mut failed);
    assert!(choice.is_some());
    assert!(!leftover.exists());
}

#[test]
fn startup_removes_abandoned_import_staging_unless_an_install_runs() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let engines = paths.next_root.join("engines");
    let staging = engines.join(format!(".import-{LEFTOVER}"));
    fs::create_dir_all(staging.join("engine/_internal")).unwrap();
    fs::write(staging.join("engine/_internal/part"), b"partial").unwrap();
    let not_staging = engines.join(".import-notes");
    fs::create_dir_all(&not_staging).unwrap();
    #[cfg(unix)]
    let (linked, external) = {
        let external = root.path().join("external");
        fs::create_dir_all(&external).unwrap();
        fs::write(external.join("sentinel"), b"outside").unwrap();
        let linked = engines.join(".import-223e4567-e89b-42d3-a456-426614174000");
        std::os::unix::fs::symlink(&external, &linked).unwrap();
        (linked, external)
    };
    for flag in [&state.engines.busy, &state.updates.busy] {
        let _busy = updates::acquire(flag).unwrap();
        recover_engine_files_at_startup(&state);
        assert!(staging.is_dir());
    }
    recover_engine_files_at_startup(&state);
    assert!(!staging.exists());
    assert!(not_staging.is_dir());
    #[cfg(unix)]
    {
        assert!(fs::symlink_metadata(&linked)
            .unwrap()
            .file_type()
            .is_symlink());
        assert_eq!(fs::read(external.join("sentinel")).unwrap(), b"outside");
    }
}

#[test]
fn a_second_install_keeps_the_confirmed_copy_or_a_verified_backup_as_the_fallback() {
    for case in ["confirmed-target", "damaged-backup", "verified-backup"] {
        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        let id = "gpu";
        let engines = paths.next_root.join("engines");
        let target = engines.join(id);
        let backup = updates::engine_backup_path(&engines, id);
        write_replacement_fixture(&paths.next_root, id, b"cur");
        let previous = write_replacement_fixture(&paths.next_root, "prev", b"old");
        fs::rename(engines.join("prev"), &backup).unwrap();
        let previous = EngineMeta {
            id: id.into(),
            ..previous
        };
        fs::write(
            backup.join("meta.json"),
            serde_json::to_vec(&previous).unwrap(),
        )
        .unwrap();
        crate::engine_files::test_signing::list_of(id, &previous.files)
            .write_into(&backup)
            .unwrap();
        if case != "confirmed-target" {
            fs::write(
                target.join(".pending-start.json"),
                br#"{"token":"first","app_version":null}"#,
            )
            .unwrap();
        }
        if case == "damaged-backup" {
            fs::remove_file(backup.join("engine").join(worker_executable_name())).unwrap();
        }
        let (staged, new) = stage_fixture(&paths.next_root, id, b"new");
        staged.publish(Some(&new), None).unwrap();
        let expected: &[u8] = if case == "verified-backup" {
            b"old"
        } else {
            b"cur"
        };
        assert_eq!(
            fs::read(backup.join("engine").join(worker_executable_name())).unwrap(),
            expected,
            "{case}"
        );
        assert_eq!(
            fs::read(engine_worker(&paths.next_root, id)).unwrap(),
            b"new",
            "{case}"
        );
        assert!(retired_folders(&paths.next_root).is_empty(), "{case}");
    }
}

#[test]
fn reinstalling_the_running_engine_stops_the_worker_before_moving_its_folder() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let id = "gpu";
    write_replacement_fixture(&paths.next_root, id, b"old");
    let (staged, new) = stage_fixture(&paths.next_root, id, b"new");
    let worker = engine_worker(&paths.next_root, id);
    *lock(&state.worker.verified_run).unwrap() = Some(worker::VerifiedRun::for_test(
        worker.clone(),
        Some(id.into()),
        None,
    ));
    state.worker.pid.store(4242, Ordering::SeqCst);
    let observer = {
        let state = state.clone();
        let worker = worker.clone();
        std::thread::spawn(move || {
            let deadline = std::time::Instant::now() + Duration::from_secs(5);
            while !state.worker.shutting_down.load(Ordering::SeqCst) {
                if std::time::Instant::now() > deadline {
                    return None;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            // The worker still runs from the old folder when it is asked to stop.
            let seen = fs::read(&worker).ok();
            state.worker.pid.store(0, Ordering::SeqCst);
            seen
        })
    };
    commit_installed_engine(&state, None, staged, &new).unwrap();
    assert_eq!(observer.join().unwrap(), Some(b"old".to_vec()));
    assert_eq!(fs::read(&worker).unwrap(), b"new");
}

#[test]
fn confirming_a_start_leaves_backup_deletion_to_another_thread_and_the_next_start() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let id = "gpu";
    write_replacement_fixture(&paths.next_root, id, b"old");
    let (staged, new) = stage_fixture(&paths.next_root, id, b"new");
    staged.publish(Some(&new), None).unwrap();
    let token = updates::pending_engine_token(&paths.next_root, id).unwrap();
    let _ = updates::confirm_engine_start(&paths.next_root, id, &token).unwrap();
    assert!(!updates::engine_backup_path(&paths.next_root.join("engines"), id).exists());
    assert!(updates::pending_engine_token(&paths.next_root, id).is_none());
    let retired = retired_folders(&paths.next_root);
    assert_eq!(retired.len(), 1);
    // A crash in the middle of the deletion leaves part of the tree behind.
    fs::remove_file(retired[0].join("meta.json")).unwrap();
    recover_engine_files_at_startup(&state);
    assert!(!retired[0].exists());
    assert_eq!(
        fs::read(engine_worker(&paths.next_root, id)).unwrap(),
        b"new"
    );
}

fn published_replacement(
    next_root: &Path,
    id: &str,
) -> (updates::EngineSwap, EngineMeta, EngineMeta) {
    let old = write_replacement_fixture(next_root, id, b"old");
    let (staged, new) = stage_fixture(next_root, id, b"new");
    let swap = staged.publish(Some(&new), None).unwrap();
    (swap, old, new)
}

#[cfg(unix)]
#[test]
fn confirming_a_start_keeps_the_backup_until_the_new_copy_is_committed() {
    use std::os::unix::fs::PermissionsExt;
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let id = "gpu";
    published_replacement(&paths.next_root, id);
    let token = updates::pending_engine_token(&paths.next_root, id).unwrap();
    // The marker cannot be removed: the commit point is not reached.
    let target = paths.next_root.join("engines").join(id);
    fs::set_permissions(&target, fs::Permissions::from_mode(0o555)).unwrap();
    let result = updates::confirm_engine_start(&paths.next_root, id, &token);
    fs::set_permissions(&target, fs::Permissions::from_mode(0o755)).unwrap();
    assert!(result.is_err());
    assert!(updates::pending_engine_token(&paths.next_root, id).is_some());
    assert!(updates::engine_backup_path(&paths.next_root.join("engines"), id).is_dir());
}

#[test]
fn rollback_keeps_the_installed_copy_when_its_backup_is_gone() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let id = "gpu";
    let (swap, ..) = published_replacement(&paths.next_root, id);
    fs::remove_dir_all(updates::engine_backup_path(
        &paths.next_root.join("engines"),
        id,
    ))
    .unwrap();
    assert!(swap.rollback().is_err());
    assert_eq!(
        fs::read(engine_worker(&paths.next_root, id)).unwrap(),
        b"new"
    );
}

#[test]
fn an_install_whose_engine_does_not_start_restores_the_previous_selection() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    write_replacement_fixture(&paths.next_root, "other", b"other");
    let pointer = paths.next_root.join("active-engine.txt");
    fs::write(&pointer, "other").unwrap();
    let id = "gpu";
    let (staged, new) = stage_fixture(&paths.next_root, id, b"new");
    let starts = std::sync::atomic::AtomicUsize::new(0);
    let bundled = root.path().join("bundled-worker");
    // The worker start falls back to the bundled engine and still reports success.
    let restart = || -> AppResult<()> {
        let selected = fs::read_to_string(&pointer).ok();
        let started = if starts.fetch_add(1, Ordering::SeqCst) == 0 {
            worker::VerifiedRun::for_test(bundled.clone(), None, None)
        } else {
            let id = selected.unwrap();
            worker::VerifiedRun::for_test(engine_worker(&paths.next_root, &id), Some(id), None)
        };
        *lock(&state.worker.verified_run)? = Some(started);
        Ok(())
    };
    assert!(commit_installed_engine_with(&state, staged, &new, Some(&restart)).is_err());
    assert_eq!(fs::read_to_string(&pointer).unwrap(), "other");
    assert_eq!(starts.load(Ordering::SeqCst), 2);
    assert!(!paths.next_root.join("engines").join(id).exists());
}

#[test]
fn backups_for_another_host_are_not_restored() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let id = "gpu";
    let (_, old, _) = published_replacement(&paths.next_root, id);
    let backup = updates::engine_backup_path(&paths.next_root.join("engines"), id);
    let foreign = EngineMeta {
        os: "plan9".into(),
        ..old
    };
    fs::write(
        backup.join("meta.json"),
        serde_json::to_vec(&foreign).unwrap(),
    )
    .unwrap();
    fs::write(engine_worker(&paths.next_root, id), b"bad").unwrap();
    assert!(!updates::restore_verified_engine_backup(&paths.next_root, id).unwrap());
    assert_eq!(
        fs::read(engine_worker(&paths.next_root, id)).unwrap(),
        b"bad"
    );
}

#[test]
fn failed_verification_of_an_unconfirmed_replacement_restores_the_verified_backup() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let (config, ..) = fixture_engine_files("http://127.0.0.1:1");
    let id = "gpu";
    let (_, old, _) = published_replacement(&paths.next_root, id);
    let pointer = paths.next_root.join("active-engine.txt");
    fs::write(&pointer, id).unwrap();
    fs::write(engine_worker(&paths.next_root, id), b"bad").unwrap();
    let os = std::env::consts::OS;
    let arch = std::env::consts::ARCH;
    assert!(verify_engine_for(&state, None, &config, id, os, arch).is_err());
    assert_eq!(
        fs::read(engine_worker(&paths.next_root, id)).unwrap(),
        b"old"
    );
    assert_eq!(read_engine_meta(&paths.next_root, id), Some(old));
    assert_eq!(fs::read_to_string(&pointer).unwrap(), id);
    assert!(!state.worker.use_bundled_fallback.load(Ordering::SeqCst));
}

#[test]
fn path_files_fat_binaries_and_scripts_are_hashed_at_start() {
    for (relative, content) in [
        (
            "_internal/site-packages/startup.pth",
            b"import os # 1".as_slice(),
        ),
        (
            "_internal/universal64",
            b"\xca\xfe\xba\xbf fat 1".as_slice(),
        ),
        ("_internal/launcher", b"#!/bin/sh\necho 1\n".as_slice()),
    ] {
        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        let id = "gpu";
        let engine = paths.next_root.join("engines").join(id).join("engine");
        fs::create_dir_all(engine.join("_internal/site-packages")).unwrap();
        fs::write(engine.join(worker_executable_name()), b"worker").unwrap();
        fs::write(engine.join(relative), content).unwrap();
        write_engine_meta(
            &paths.next_root,
            &EngineMeta {
                id: id.into(),
                backend: "rocm".into(),
                os: std::env::consts::OS.into(),
                arch: std::env::consts::ARCH.into(),
                protocol_version: PROTOCOL_VERSION,
                handshake_passed: true,
                verified_install: true,
                version: "1".into(),
                files: updates::test_engine_files(&paths.next_root, id),
            },
        )
        .unwrap();
        crate::engine_files::test_signing::write_list_on(
            &paths.next_root,
            id,
            &updates::test_engine_files(&paths.next_root, id),
            std::env::consts::OS,
            std::env::consts::ARCH,
        );
        fs::write(paths.next_root.join("active-engine.txt"), id).unwrap();
        let mut changed = content.to_vec();
        *changed
            .iter_mut()
            .rev()
            .find(|byte| **byte == b'1')
            .unwrap() = b'2';
        fs::write(engine.join(relative), changed).unwrap();
        let mut failed = std::collections::HashSet::new();
        let (choice, verification_failed) =
            updates::verified_worker_engine_choice(&paths, None, &mut failed);
        assert_eq!(choice, None, "{relative}");
        assert!(verification_failed, "{relative}");
    }
}

#[test]
fn a_worker_started_just_before_a_reinstall_is_stopped_before_the_swap() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let id = "gpu";
    write_replacement_fixture(&paths.next_root, id, b"old");
    let (staged, new) = stage_fixture(&paths.next_root, id, b"new");
    let worker = engine_worker(&paths.next_root, id);
    // The supervisor is between two starts of this engine: no process yet.
    *lock(&state.worker.verified_run).unwrap() = Some(worker::VerifiedRun::for_test(
        worker.clone(),
        Some(id.into()),
        None,
    ));
    let engine_files = lock(&state.worker.engine_files).unwrap();
    let committing = {
        let state = state.clone();
        std::thread::spawn(move || commit_installed_engine(&state, None, staged, &new))
    };
    std::thread::sleep(Duration::from_millis(200));
    // The supervisor starts it again under engine_files while the install waits.
    state.worker.pid.store(4242, Ordering::SeqCst);
    drop(engine_files);
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    let mut seen = None;
    while std::time::Instant::now() < deadline {
        if state.worker.shutting_down.load(Ordering::SeqCst) {
            seen = fs::read(&worker).ok();
            state.worker.pid.store(0, Ordering::SeqCst);
            break;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    state.worker.pid.store(0, Ordering::SeqCst);
    committing.join().unwrap().unwrap();
    assert_eq!(seen, Some(b"old".to_vec()));
}

#[test]
fn staging_leaves_recovery_to_callers_that_hold_engine_files() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let id = "gpu";
    published_replacement(&paths.next_root, id);
    // The new copy is damaged, so recovery would restore the backup.
    fs::write(engine_worker(&paths.next_root, id), b"short!").unwrap();
    let _engine_files = lock(&state.worker.engine_files).unwrap();
    let (staged, _) = stage_fixture(&paths.next_root, id, b"next");
    assert_eq!(
        fs::read(engine_worker(&paths.next_root, id)).unwrap(),
        b"short!"
    );
    assert!(updates::engine_backup_path(&paths.next_root.join("engines"), id).is_dir());
    drop(staged);
}

/// An engine as 0.5.2 to 0.6 leave it: files, meta.json, active-engine.txt, no list.
fn engine_from_an_earlier_version(next_root: &Path, id: &str, os: &str) -> PathBuf {
    let engine = next_root.join("engines").join(id).join("engine");
    fs::create_dir_all(engine.join("_internal")).unwrap();
    fs::write(engine.join(worker_executable_name()), b"worker").unwrap();
    fs::write(engine.join("_internal/library.so"), b"library").unwrap();
    write_engine_meta(
        next_root,
        &EngineMeta {
            id: id.into(),
            backend: "rocm".into(),
            os: os.into(),
            arch: "x86_64".into(),
            protocol_version: PROTOCOL_VERSION,
            handshake_passed: true,
            verified_install: true,
            version: "0.6.3-beta".into(),
            files: updates::test_engine_files(next_root, id),
        },
    )
    .unwrap();
    fs::write(next_root.join("active-engine.txt"), id).unwrap();
    engine.join(worker_executable_name())
}

fn catalog_entry_with_list(id: &str, list: DownloadFile) -> VerifiedCatalogEntry {
    let unused = DownloadFile {
        name: "unused.engine-update.json".into(),
        url: "https://example.test/unused".into(),
        size: 1,
        sha256: "0".repeat(64),
        signature: String::new(),
    };
    VerifiedCatalogEntry {
        backend: "rocm".into(),
        engine_id: id.into(),
        version: "0.7.0-beta".into(),
        size: 1,
        manifest_url: unused.url.clone(),
        engine: EngineUpdate {
            id: id.into(),
            manifest: unused,
            parts: Vec::new(),
            files: Some(list),
        },
    }
}

fn served_list(
    bytes: Vec<u8>,
    signature: String,
) -> (DownloadFile, std::thread::JoinHandle<()>, String) {
    let digest = sha256_hex(&bytes);
    let size = bytes.len() as u64;
    let (base, _, server) = serve_with_ranges(vec![("engine-files.json".into(), bytes)], 1);
    let file = DownloadFile {
        name: "engine-files.json".into(),
        url: format!("{base}/engine-files.json"),
        size,
        sha256: digest,
        signature,
    };
    (file, server, base)
}

#[test]
fn an_engine_an_earlier_version_installed_takes_its_signed_list_from_the_feed() {
    use crate::engine_files::test_signing;
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let os = host_test_os();
    let id = "linux-x86_64-rocm-070";
    let worker = engine_from_an_earlier_version(&paths.next_root, id, os);
    // It waits for its list: not chosen, not marked.
    assert_eq!(
        updates::active_engine_for_host(&paths, false, "compiled", os, "x86_64"),
        None
    );
    assert_eq!(
        engines_without_file_list(&paths.next_root, "compiled", os, "x86_64"),
        vec![id.to_owned()]
    );
    let part = engine_payload::ManifestPart {
        filename: "engine.tar.gz.part-0001".into(),
        size: 1,
        sha256: "0".repeat(64),
    };
    let files = updates::test_engine_files(&paths.next_root, id);
    let (bytes, signature) = test_signing::signed_list(id, os, "x86_64", "ROCM", &[part], &files);
    let anchor = |file: DownloadFile, base: &str| {
        let config = UpdateConfig {
            public_key: test_signing::public_key(),
            feed: base.to_owned(),
            backend: "cpu".into(),
            engine_id: "compiled".into(),
            kind: "native".into(),
        };
        tauri::async_runtime::block_on(anchor_installed_engines(
            &state,
            None,
            &config,
            &[catalog_entry_with_list(id, file)],
            vec![id.to_owned()],
            os,
            "x86_64",
            |_| panic!("an engine with metadata needs no handshake"),
        ))
    };

    // A list signed by another key is not kept.
    let (file, server, base) =
        served_list(bytes.clone(), test_signing::sign_with_another_key(&bytes));
    assert!(anchor(file, &base).is_empty());
    server.join().unwrap();
    assert!(updates::engine_lacks_file_list(&paths.next_root, id));

    // The release's list, while a file differs from it, is not kept either.
    fs::write(
        worker.parent().unwrap().join("_internal/library.so"),
        b"LIBRARY",
    )
    .unwrap();
    let (file, server, base) = served_list(bytes.clone(), signature.clone());
    assert!(anchor(file, &base).is_empty());
    server.join().unwrap();
    assert!(updates::engine_lacks_file_list(&paths.next_root, id));

    // With the files it names, the engine keeps it and is started again.
    fs::write(
        worker.parent().unwrap().join("_internal/library.so"),
        b"library",
    )
    .unwrap();
    let (file, server, base) = served_list(bytes, signature);
    assert_eq!(anchor(file, &base), vec![id.to_owned()]);
    server.join().unwrap();
    assert!(!updates::engine_lacks_file_list(&paths.next_root, id));
    assert!(!paths
        .next_root
        .join("updates/engine-cache")
        .join(id)
        .exists());
    assert_eq!(
        updates::active_engine_for_host(&paths, false, "compiled", os, "x86_64"),
        Some(worker)
    );
    assert!(engines_without_file_list(&paths.next_root, "compiled", os, "x86_64").is_empty());
}

#[test]
fn an_engine_waiting_for_its_list_is_named_at_start_while_launch_checks_are_off() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = AppState::from_paths(paths.clone()).unwrap();
    let os = host_test_os();
    let config = UpdateConfig {
        public_key: crate::engine_files::test_signing::public_key(),
        feed: "https://example.test/feed.json".into(),
        backend: "cpu".into(),
        engine_id: "compiled".into(),
        kind: "native".into(),
    };
    let notice = || engine_waiting_notice(&state, &config, os, "x86_64");
    // Nothing waits.
    lock(&state.settings).unwrap().privacy_choice_made = true;
    assert_eq!(notice(), None);

    engine_from_an_earlier_version(&paths.next_root, "linux-x86_64-rocm-070", os);
    lock(&state.settings).unwrap().check_updates_at_launch = false;
    assert!(notice().is_some_and(|text| text.contains("Check for Updates")));
    // The start reads the feed for it itself.
    lock(&state.settings).unwrap().check_updates_at_launch = true;
    assert_eq!(notice(), None);
    // The first start asks about the feed before anything else.
    let mut settings = lock(&state.settings).unwrap();
    settings.check_updates_at_launch = false;
    settings.privacy_choice_made = false;
    drop(settings);
    assert_eq!(notice(), None);
}

#[test]
fn a_verify_request_leaves_an_engine_without_a_list_as_it_is() {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let (config, ..) = fixture_engine_files("http://127.0.0.1:1");
    let id = "linux-x86_64-rocm-070";
    engine_from_an_earlier_version(&paths.next_root, id, "linux");
    let error = verify_engine_for(&state, None, &config, id, "linux", "x86_64").unwrap_err();
    assert!(error.to_string().contains("earlier version"), "{error}");
    let meta = read_engine_meta(&paths.next_root, id).unwrap();
    assert!(meta.verified_install);
    assert!(paths.next_root.join("active-engine.txt").exists());
}
