//! Signs like `tauri signer` (minisign, prehashed) with the disposable test key of the
//! release tooling (`tests/test_tauri_release_tools.py`, `TEST_KEY`): seed 0x01 repeated,
//! key id `localsr1`. Test fixtures signed by the Python tooling verify with the same key.
use base64::Engine;

const SEED: [u8; 32] = [1; 32];
const KEY_ID: &[u8; 8] = b"localsr1";

fn pair() -> ring::signature::Ed25519KeyPair {
    ring::signature::Ed25519KeyPair::from_seed_unchecked(&SEED).expect("a valid seed")
}

fn base64(bytes: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

/// The public key as the host is given it (`LOCALSR_UPDATE_PUBLIC_KEY`).
pub(crate) fn public_key() -> String {
    use ring::signature::KeyPair;
    let mut raw = b"Ed".to_vec();
    raw.extend_from_slice(KEY_ID);
    raw.extend_from_slice(pair().public_key().as_ref());
    let hex: String = KEY_ID.iter().map(|byte| format!("{byte:02X}")).collect();
    base64(
        format!(
            "untrusted comment: minisign public key: {hex}\n{}\n",
            base64(&raw)
        )
        .as_bytes(),
    )
}

/// The signature of `data` as `<file>.sig` holds it.
pub(crate) fn sign(data: &[u8]) -> String {
    sign_with(&pair(), data)
}

/// The same signature by another key: well formed, but not the release key's.
pub(crate) fn sign_with_another_key(data: &[u8]) -> String {
    sign_with(
        &ring::signature::Ed25519KeyPair::from_seed_unchecked(&[2; 32]).unwrap(),
        data,
    )
}

fn sign_with(pair: &ring::signature::Ed25519KeyPair, data: &[u8]) -> String {
    let signature = pair.sign(&blake2b_512(data));
    let mut blob = b"ED".to_vec();
    blob.extend_from_slice(KEY_ID);
    blob.extend_from_slice(signature.as_ref());
    let trusted = "timestamp:0\tfile:file";
    let mut global = signature.as_ref().to_vec();
    global.extend_from_slice(trusted.as_bytes());
    let text = format!(
        "untrusted comment: signature from tauri secret key\n{}\ntrusted comment: {trusted}\n{}\n",
        base64(&blob),
        base64(pair.sign(&global).as_ref())
    );
    base64(text.as_bytes())
}

/// The minisign document for engine `id` on `os`/`arch` listing `files`, signed.
pub(crate) fn signed_list(
    id: &str,
    os: &str,
    arch: &str,
    backend: &str,
    parts: &[crate::engine_payload::ManifestPart],
    files: &[crate::engine_payload::InstalledEngineFile],
) -> (Vec<u8>, String) {
    let document = serde_json::json!({
        "schema_version": 1,
        "engine_id": id,
        "version": "1.2.3-beta",
        "os": os,
        "arch": arch,
        "backend": backend,
        "protocol_version": crate::types::PROTOCOL_VERSION,
        "parts": parts.iter().map(|part| serde_json::json!({
            "filename": part.filename, "size": part.size, "sha256": part.sha256,
        })).collect::<Vec<_>>(),
        "files": files,
    });
    let bytes = serde_json::to_vec(&document).unwrap();
    let signature = sign(&bytes);
    (bytes, signature)
}

const IV: [u64; 8] = [
    0x6a09e667f3bcc908,
    0xbb67ae8584caa73b,
    0x3c6ef372fe94f82b,
    0xa54ff53a5f1d36f1,
    0x510e527fade682d1,
    0x9b05688c2b3e6c1f,
    0x1f83d9abfb41bd6b,
    0x5be0cd19137e2179,
];
const SIGMA: [[usize; 16]; 10] = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
    [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
    [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
    [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
    [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
    [12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11],
    [13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10],
    [6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5],
    [10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0],
];

fn compress(state: &mut [u64; 8], block: &[u8; 128], counter: u128, last: bool) {
    let mut v = [0u64; 16];
    v[..8].copy_from_slice(state);
    v[8..].copy_from_slice(&IV);
    v[12] ^= counter as u64;
    v[13] ^= (counter >> 64) as u64;
    if last {
        v[14] = !v[14];
    }
    let mut m = [0u64; 16];
    for (word, chunk) in m.iter_mut().zip(block.chunks_exact(8)) {
        *word = u64::from_le_bytes(chunk.try_into().unwrap());
    }
    let mut mix = |a: usize, b: usize, c: usize, d: usize, x: u64, y: u64| {
        v[a] = v[a].wrapping_add(v[b]).wrapping_add(x);
        v[d] = (v[d] ^ v[a]).rotate_right(32);
        v[c] = v[c].wrapping_add(v[d]);
        v[b] = (v[b] ^ v[c]).rotate_right(24);
        v[a] = v[a].wrapping_add(v[b]).wrapping_add(y);
        v[d] = (v[d] ^ v[a]).rotate_right(16);
        v[c] = v[c].wrapping_add(v[d]);
        v[b] = (v[b] ^ v[c]).rotate_right(63);
    };
    for round in 0..12 {
        let s = &SIGMA[round % 10];
        mix(0, 4, 8, 12, m[s[0]], m[s[1]]);
        mix(1, 5, 9, 13, m[s[2]], m[s[3]]);
        mix(2, 6, 10, 14, m[s[4]], m[s[5]]);
        mix(3, 7, 11, 15, m[s[6]], m[s[7]]);
        mix(0, 5, 10, 15, m[s[8]], m[s[9]]);
        mix(1, 6, 11, 12, m[s[10]], m[s[11]]);
        mix(2, 7, 8, 13, m[s[12]], m[s[13]]);
        mix(3, 4, 9, 14, m[s[14]], m[s[15]]);
    }
    for i in 0..8 {
        state[i] ^= v[i] ^ v[i + 8];
    }
}

/// BLAKE2b with a 64-byte digest and no key (RFC 7693), which minisign prehashes with.
pub(crate) fn blake2b_512(data: &[u8]) -> [u8; 64] {
    let mut state = IV;
    state[0] ^= 0x0101_0000 ^ 64;
    let mut counter: u128 = 0;
    let mut offset = 0;
    while data.len() - offset > 128 {
        counter += 128;
        compress(
            &mut state,
            data[offset..offset + 128].try_into().unwrap(),
            counter,
            false,
        );
        offset += 128;
    }
    let mut last = [0u8; 128];
    last[..data.len() - offset].copy_from_slice(&data[offset..]);
    counter += (data.len() - offset) as u128;
    compress(&mut state, &last, counter, true);
    let mut out = [0u8; 64];
    for (chunk, word) in out.chunks_exact_mut(8).zip(state) {
        chunk.copy_from_slice(&word.to_le_bytes());
    }
    out
}

/// A list for engine `id` on this host naming `files`, signed with the test key.
pub(crate) fn list_of(
    id: &str,
    files: &[crate::engine_payload::InstalledEngineFile],
) -> super::EngineFileList {
    let part = crate::engine_payload::ManifestPart {
        filename: "engine.tar.gz.part-0001".into(),
        size: 1,
        sha256: "0".repeat(64),
    };
    let (bytes, signature) = signed_list(
        id,
        std::env::consts::OS,
        std::env::consts::ARCH,
        "CPU",
        &[part],
        files,
    );
    super::EngineFileList::verify(&bytes, &signature, &public_key()).unwrap()
}

/// Keep a signed list of the files now in engine `id`'s folder, as an installation of a
/// release package does.
pub(crate) fn write_list_for_installed(next_root: &std::path::Path, id: &str) {
    write_list(
        next_root,
        id,
        &crate::updates::test_engine_files(next_root, id),
    );
}

/// Keep a signed list naming `files` with engine `id`.
pub(crate) fn write_list(
    next_root: &std::path::Path,
    id: &str,
    files: &[crate::engine_payload::InstalledEngineFile],
) {
    write_list_on(
        next_root,
        id,
        files,
        std::env::consts::OS,
        std::env::consts::ARCH,
    );
}

/// Keep a signed list naming `files` with engine `id`, for the host `os`/`arch`.
pub(crate) fn write_list_on(
    next_root: &std::path::Path,
    id: &str,
    files: &[crate::engine_payload::InstalledEngineFile],
    os: &str,
    arch: &str,
) {
    let part = crate::engine_payload::ManifestPart {
        filename: "engine.tar.gz.part-0001".into(),
        size: 1,
        sha256: "0".repeat(64),
    };
    let (bytes, signature) = signed_list(id, os, arch, "CPU", &[part], files);
    super::EngineFileList::verify(&bytes, &signature, &public_key())
        .unwrap()
        .write_into(&next_root.join("engines").join(id))
        .unwrap();
}

/// The signed list of the engine package `manifest` describes, made from its parts in
/// `directory` as the release tooling makes it, written there as `name`.
pub(crate) fn package_list(
    directory: &std::path::Path,
    manifest: &[u8],
    id: &str,
    name: &str,
) -> crate::updates::DownloadFile {
    let parsed = crate::engine_payload::parse_manifest(manifest).unwrap();
    let parts: Vec<(String, Vec<u8>)> = parsed
        .parts
        .iter()
        .map(|part| {
            (
                part.filename.clone(),
                std::fs::read(directory.join(&part.filename)).unwrap(),
            )
        })
        .collect();
    let (bytes, file) = package_list_from(
        manifest,
        &parts,
        id,
        (std::env::consts::OS, std::env::consts::ARCH),
        "https://example.test",
        name,
    );
    std::fs::write(directory.join(name), &bytes).unwrap();
    file
}

/// The signed list of the engine package `manifest` describes, from its `parts`, for the
/// host `os`/`arch`, and the feed's description of it at `base_url`.
pub(crate) fn package_list_from(
    manifest: &[u8],
    parts: &[(String, Vec<u8>)],
    id: &str,
    (os, arch): (&str, &str),
    base_url: &str,
    name: &str,
) -> (Vec<u8>, crate::updates::DownloadFile) {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    let parsed = crate::engine_payload::parse_manifest(manifest).unwrap();
    let mut joined = Vec::new();
    for part in &parsed.parts {
        let (_, data) = parts
            .iter()
            .find(|(name, _)| *name == part.filename)
            .unwrap();
        joined.extend_from_slice(data);
    }
    let mut archive = tar::Archive::new(flate2::read::GzDecoder::new(joined.as_slice()));
    let mut files = Vec::new();
    for entry in archive.entries().unwrap() {
        let mut entry = entry.unwrap();
        let path = crate::engine_payload::canonical_relative_path(&entry.path().unwrap());
        let mut data = Vec::new();
        entry.read_to_end(&mut data).unwrap();
        files.push(crate::engine_payload::InstalledEngineFile {
            path,
            size: data.len() as u64,
            sha256: hex(&Sha256::digest(&data)),
        });
    }
    files.sort_by(|a, b| a.path.cmp(&b.path));
    let (bytes, signature) = signed_list(id, os, arch, &parsed.backend, &parsed.parts, &files);
    let file = crate::updates::DownloadFile {
        name: name.into(),
        url: format!("{base_url}/{name}"),
        size: bytes.len() as u64,
        sha256: hex(&Sha256::digest(&bytes)),
        signature,
    };
    (bytes, file)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
