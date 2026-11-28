//! The user's approvals of their own pickle checkpoints (`.pth`, `.pt`, `.ckpt`).
//!
//! The settings keep the approved SHA-256 digests (`trusted_checkpoint_sha256`), as
//! before. Each approval made in LocalSR also gets a seal: an HMAC-SHA256 of the digest
//! under a random key that the operating system keeps for LocalSR (see `key_store`). The
//! seals live in `checkpoint-approvals.json` beside the settings. A digest counts as
//! approved only while it is in the settings and its seal verifies; one without a valid
//! seal is asked about again, and nothing is deleted.
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    fs,
    path::{Path, PathBuf},
    sync::{LazyLock, Mutex},
};

use serde::{Deserialize, Serialize};

use crate::{
    error::{AppError, AppResult},
    paths::AppPaths,
};

mod key_store;

const SEALS_FILE: &str = "checkpoint-approvals.json";
const SCHEMA_VERSION: u32 = 1;
const SEAL_CONTEXT: &[u8] = b"localsr checkpoint approval v1\0";

#[derive(Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Seals {
    schema_version: u32,
    seals: BTreeMap<String, String>,
}

/// The key, once read or made in this process, per profile folder.
static KEYS: LazyLock<Mutex<HashMap<PathBuf, [u8; 32]>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));
/// Approvals made while no key was available: they last until LocalSR quits.
static SESSION: LazyLock<Mutex<HashSet<(PathBuf, String)>>> =
    LazyLock::new(|| Mutex::new(HashSet::new()));

/// The key and whether this call made it, the first time the profile needed one.
fn key(next_root: &Path) -> Option<([u8; 32], bool)> {
    if let Some(key) = KEYS.lock().ok()?.get(next_root) {
        return Some((*key, false));
    }
    let (key, created) = match key_store::load_or_create(next_root) {
        Ok(key_store::Loaded::Existing(key)) => (key, false),
        Ok(key_store::Loaded::Created(key)) => (key, true),
        Err(error) => {
            eprintln!("LocalSR cannot use its key for model approvals: {error}");
            return None;
        }
    };
    KEYS.lock().ok()?.insert(next_root.to_path_buf(), key);
    Some((key, created))
}

fn hmac_key(key: &[u8; 32]) -> ring::hmac::Key {
    ring::hmac::Key::new(ring::hmac::HMAC_SHA256, key)
}

fn message(digest: &str) -> Vec<u8> {
    [SEAL_CONTEXT, digest.as_bytes()].concat()
}

fn seal(key: &[u8; 32], digest: &str) -> String {
    hex(ring::hmac::sign(&hmac_key(key), &message(digest)).as_ref())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn unhex(text: &str) -> Option<Vec<u8>> {
    if text.len() % 2 != 0 {
        return None;
    }
    (0..text.len())
        .step_by(2)
        .map(|index| u8::from_str_radix(text.get(index..index + 2)?, 16).ok())
        .collect()
}

fn seals_path(paths: &AppPaths) -> PathBuf {
    paths.next_root.join(SEALS_FILE)
}

/// The seals file, or None when there is none (a profile from before seals).
fn read_seals(paths: &AppPaths) -> Option<Seals> {
    let path = seals_path(paths);
    let metadata = fs::symlink_metadata(&path).ok()?;
    if !metadata.file_type().is_file() || metadata.len() > 4 * 1024 * 1024 {
        return Some(Seals::default());
    }
    Some(
        fs::read(&path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Seals>(&bytes).ok())
            .filter(|seals| seals.schema_version == SCHEMA_VERSION)
            .unwrap_or_default(),
    )
}

fn write_seals(paths: &AppPaths, seals: &BTreeMap<String, String>) -> AppResult<()> {
    fs::create_dir_all(&paths.next_root)?;
    let document = Seals {
        schema_version: SCHEMA_VERSION,
        seals: seals.clone(),
    };
    crate::update_storage::write_atomic(&seals_path(paths), &serde_json::to_vec_pretty(&document)?)
}

/// Whether `digest`, which the settings list as approved, was approved in LocalSR.
pub(crate) fn sealed(paths: &AppPaths, digest: &str) -> bool {
    if SESSION
        .lock()
        .is_ok_and(|session| session.contains(&(paths.next_root.clone(), digest.to_owned())))
    {
        return true;
    }
    let Some(tag) = read_seals(paths)
        .and_then(|seals| seals.seals.get(digest).cloned())
        .and_then(|tag| unhex(&tag))
    else {
        return false;
    };
    key(&paths.next_root)
        .is_some_and(|(key, _)| ring::hmac::verify(&hmac_key(&key), &message(digest), &tag).is_ok())
}

/// Record that the user approved (`allow`) or withdrew `digest` in LocalSR. The caller
/// changes the settings list; a seal without a list entry approves nothing.
pub(crate) fn record(paths: &AppPaths, digest: &str, allow: bool) -> AppResult<()> {
    let entry = (paths.next_root.clone(), digest.to_owned());
    if let Ok(mut session) = SESSION.lock() {
        session.remove(&entry);
    }
    let mut seals = read_seals(paths).unwrap_or_default().seals;
    if !allow {
        if seals.remove(digest).is_some() {
            write_seals(paths, &seals)?;
        }
        return Ok(());
    }
    let Some((key, created)) = key(&paths.next_root) else {
        // Kept for this session only; LocalSR asks again after a restart.
        SESSION
            .lock()
            .map_err(|_| AppError::Config("the approval list is unavailable".into()))?
            .insert(entry);
        return Ok(());
    };
    if created {
        // A new key cannot verify seals made with another: they are dropped, so the
        // approvals they stood for are asked again.
        seals.clear();
    }
    seals.insert(digest.to_owned(), seal(&key, digest));
    write_seals(paths, &seals)
}

/// At start, until the profile has a seals file. The first start of this version makes
/// the key; the approvals the settings list then were made in LocalSR before seals
/// existed, so they are sealed with it. A later start that finds no seals file but the
/// key in place seals nothing: the file is written empty and those approvals are asked
/// again. Without a key (a locked keychain) nothing changes until a later start.
pub(crate) fn prepare_at_start(paths: &AppPaths, digests: &[String]) {
    if read_seals(paths).is_some() {
        return;
    }
    let Some((key, created)) = key(&paths.next_root) else {
        return;
    };
    let seals = if created {
        digests
            .iter()
            .map(|digest| (digest.clone(), seal(&key, digest)))
            .collect()
    } else {
        BTreeMap::new()
    };
    if let Err(error) = write_seals(paths, &seals) {
        eprintln!("LocalSR could not keep the existing model approvals: {error}");
    }
}

#[cfg(test)]
pub(crate) use key_store::test_store;

#[cfg(test)]
mod tests;
