use super::*;
use crate::engine_payload::InstalledEngineFile;

fn files() -> Vec<InstalledEngineFile> {
    vec![InstalledEngineFile {
        path: crate::updates::worker_executable_name().into(),
        size: 6,
        sha256: "0".repeat(64),
    }]
}

fn parts() -> Vec<ManifestPart> {
    vec![ManifestPart {
        filename: "engine.tar.gz.part-0001".into(),
        size: 10,
        sha256: "1".repeat(64),
    }]
}

fn host() -> (&'static str, &'static str) {
    (std::env::consts::OS, std::env::consts::ARCH)
}

#[test]
fn the_test_signer_hashes_like_blake2b_and_signs_with_the_release_tools_test_key() {
    let hex: String = test_signing::blake2b_512(b"abc")
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    assert_eq!(
        hex,
        "ba80a53f981c4d0d6a2797b69f12f6e94c212f14685ac4b74b12bb6fdbffa2d1\
         7d87c5392aab792dc252d5de4533cc9518d38aa8dbf1925ab92386edd4009923"
    );
    let long = vec![7u8; 1000];
    let key = crate::updates::key(&test_signing::public_key()).unwrap();
    for data in [&b""[..], b"x", &long] {
        let signature = crate::updates::signature(&test_signing::sign(data)).unwrap();
        assert!(key.verify(data, &signature, false).is_ok());
    }
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("../../tests/fixtures/update-release.json")).unwrap();
    assert_eq!(fixture["public_key"], test_signing::public_key());
}

#[test]
fn a_list_signed_by_the_release_key_is_read_and_names_its_engine() {
    let (os, arch) = host();
    let (bytes, signature) = test_signing::signed_list("gpu", os, arch, "CUDA", &parts(), &files());
    let list = EngineFileList::verify(&bytes, &signature, &test_signing::public_key()).unwrap();
    assert_eq!(list.files, files());
    assert!(list.names("gpu", os, arch));
    assert!(!list.names("other", os, arch));
    assert!(!list.names("gpu", "elsewhere", arch));
    assert!(!list.names("gpu", os, "elsewhere"));
}

#[test]
fn a_list_with_another_signature_or_changed_bytes_is_refused() {
    let (os, arch) = host();
    let key = test_signing::public_key();
    let (bytes, _) = test_signing::signed_list("gpu", os, arch, "CUDA", &parts(), &files());
    let foreign = test_signing::sign_with_another_key(&bytes);
    assert!(EngineFileList::verify(&bytes, &foreign, &key).is_err());

    let (bytes, signature) = test_signing::signed_list("gpu", os, arch, "CUDA", &parts(), &files());
    let changed = String::from_utf8(bytes.clone())
        .unwrap()
        .replace(&"0".repeat(64), &"2".repeat(64))
        .into_bytes();
    assert!(EngineFileList::verify(&changed, &signature, &key).is_err());
    assert!(EngineFileList::verify(&bytes, &signature, "").is_err());
}

#[test]
fn a_signed_list_of_another_shape_is_refused() {
    let (os, arch) = host();
    let key = test_signing::public_key();
    let (bytes, _) = test_signing::signed_list("gpu", os, arch, "CUDA", &parts(), &files());
    let mut document: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    for change in [
        serde_json::json!({"schema_version": 2}),
        serde_json::json!({"engine_id": "../gpu"}),
        serde_json::json!({"extra": true}),
        serde_json::json!({"files": []}),
        serde_json::json!({"files": [{"path": "../worker", "size": 1, "sha256": "0".repeat(64)}]}),
    ] {
        let mut changed = document.clone();
        for (field, value) in change.as_object().unwrap() {
            changed[field] = value.clone();
        }
        let bytes = serde_json::to_vec(&changed).unwrap();
        let signature = test_signing::sign(&bytes);
        assert!(
            EngineFileList::verify(&bytes, &signature, &key).is_err(),
            "{change}"
        );
    }
    document["files"][0]["mode"] = serde_json::json!(493);
    let bytes = serde_json::to_vec(&document).unwrap();
    assert!(EngineFileList::verify(&bytes, &test_signing::sign(&bytes), &key).is_err());
}

#[test]
fn an_installed_list_is_missing_signed_or_invalid() {
    let (os, arch) = host();
    let key = test_signing::public_key();
    let root = tempfile::tempdir().unwrap();
    assert!(matches!(
        installed(root.path(), "gpu", os, arch, &key),
        Installed::Missing
    ));
    let (bytes, signature) = test_signing::signed_list("gpu", os, arch, "CUDA", &parts(), &files());
    let list = EngineFileList::verify(&bytes, &signature, &key).unwrap();
    list.write_into(root.path()).unwrap();
    assert!(matches!(
        installed(root.path(), "gpu", os, arch, &key),
        Installed::Signed(found) if found.files == files()
    ));
    // Kept for another engine id or host: not this engine's list.
    assert!(matches!(
        installed(root.path(), "other", os, arch, &key),
        Installed::Invalid
    ));
    // A changed document under the old signature.
    let path = root.path().join(SIGNED_LIST_FILE);
    let stored = fs::read_to_string(&path).unwrap();
    fs::write(&path, stored.replace(&"0".repeat(64), &"3".repeat(64))).unwrap();
    assert!(matches!(
        installed(root.path(), "gpu", os, arch, &key),
        Installed::Invalid
    ));
    fs::write(&path, b"not json").unwrap();
    assert!(matches!(
        installed(root.path(), "gpu", os, arch, &key),
        Installed::Invalid
    ));
}
