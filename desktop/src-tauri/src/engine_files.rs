//! The release-signed list of the files an engine package unpacks.
//!
//! The release writes it beside each engine package
//! (`scripts/prepare_engine_payload.py`, `engine_files_document`) and signs it with the
//! update key. An installation keeps the list and its signature in the engine's folder,
//! and every later check of that engine compares its files with this list after checking
//! the signature with the key compiled into the host. The file list in `meta.json` is
//! still written for older versions of LocalSR, but no check here reads it.
use std::{fs, path::Path};

use serde::{Deserialize, Serialize};

use crate::{
    engine_payload::{InstalledEngineFile, ManifestPart, ParsedManifest},
    error::AppResult,
    types::PROTOCOL_VERSION,
    updates::{invalid, key, safe_id, signature},
};

/// The list and its signature in an engine's folder, beside `meta.json`.
pub(crate) const SIGNED_LIST_FILE: &str = "engine-files.signed.json";
/// A CUDA engine lists about ten thousand files in well under this.
const MAX_LIST_BYTES: usize = 32 * 1024 * 1024;
const SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ListedFile {
    path: String,
    size: u64,
    sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    schema_version: u32,
    engine_id: String,
    version: String,
    os: String,
    arch: String,
    backend: String,
    protocol_version: u32,
    parts: Vec<ManifestPart>,
    files: Vec<ListedFile>,
}

/// How the list is kept in an engine's folder: the signed text exactly as published, so
/// its signature can be checked again at every start.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Stored {
    document: String,
    signature: String,
}

/// A file list whose signature verified with the release key.
#[derive(Clone, Debug)]
pub(crate) struct EngineFileList {
    pub engine_id: String,
    pub version: String,
    pub os: String,
    pub arch: String,
    /// As the payload manifest names it: `CUDA`, `ROCM`, `CPU`, `MPS` or `DIRECTML`.
    pub backend: String,
    pub protocol_version: u32,
    pub parts: Vec<ManifestPart>,
    /// Sorted by path.
    pub files: Vec<InstalledEngineFile>,
    document: String,
    signature: String,
}

/// What an installed engine's folder holds in place of a list.
pub(crate) enum Installed {
    /// A list whose signature verified and that names this engine on this host.
    Signed(Box<EngineFileList>),
    /// No list: installed by a version of LocalSR before signed lists.
    Missing,
    /// A list that is unreadable, not signed with the release key or for another engine.
    Invalid,
}

impl EngineFileList {
    /// Check `bytes` against `signature_text` (minisign, as `tauri signer` writes it) with
    /// `public_key`, then read them.
    pub(crate) fn verify(bytes: &[u8], signature_text: &str, public_key: &str) -> AppResult<Self> {
        let refused = || invalid("The engine file list is not signed by this release's key");
        if bytes.len() > MAX_LIST_BYTES || public_key.is_empty() {
            return Err(refused());
        }
        key(public_key)?
            .verify(bytes, &signature(signature_text)?, false)
            .map_err(|_| refused())?;
        let document = std::str::from_utf8(bytes).map_err(|_| refused())?;
        let parsed: Document = serde_json::from_str(document)
            .map_err(|error| invalid(format!("The engine file list is invalid: {error}")))?;
        let mut files: Vec<InstalledEngineFile> = parsed
            .files
            .into_iter()
            .map(|file| InstalledEngineFile {
                path: file.path,
                size: file.size,
                sha256: file.sha256,
            })
            .collect();
        files.sort_by(|a, b| a.path.cmp(&b.path));
        let valid = parsed.schema_version == SCHEMA_VERSION
            && safe_id(&parsed.engine_id)
            && semver::Version::parse(&parsed.version).is_ok()
            && matches!(parsed.os.as_str(), "windows" | "linux" | "macos")
            && !parsed.arch.is_empty()
            && parsed
                .arch
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
            && parsed.protocol_version > 0
            && !parsed.parts.is_empty()
            && crate::updates::valid_engine_file_list(&files);
        if !valid {
            return Err(invalid("The engine file list is invalid"));
        }
        Ok(Self {
            engine_id: parsed.engine_id,
            version: parsed.version,
            os: parsed.os,
            arch: parsed.arch,
            backend: parsed.backend,
            protocol_version: parsed.protocol_version,
            parts: parsed.parts,
            files,
            document: document.to_owned(),
            signature: signature_text.trim().to_owned(),
        })
    }

    /// Whether the list names engine `id` for this host and worker protocol.
    pub(crate) fn names(&self, id: &str, os: &str, arch: &str) -> bool {
        self.engine_id == id
            && self.os == os
            && self.arch == arch
            && self.protocol_version == PROTOCOL_VERSION
    }

    /// Whether the list belongs to the package `manifest` describes: the same parts,
    /// files and unpacked size.
    pub(crate) fn describes(&self, manifest: &ParsedManifest) -> bool {
        let total = self
            .files
            .iter()
            .try_fold(0u64, |total, file| total.checked_add(file.size));
        self.parts.len() == manifest.parts.len()
            && self
                .parts
                .iter()
                .zip(&manifest.parts)
                .all(|(listed, part)| {
                    listed.filename == part.filename
                        && listed.size == part.size
                        && listed.sha256 == part.sha256
                })
            && self.files.len() as u64 == manifest.file_count
            && total == Some(manifest.unpacked_bytes)
            && manifest.backend == self.backend
            && manifest
                .engine_id
                .as_deref()
                .is_none_or(|id| id == self.engine_id)
    }

    /// Keep the list and its signature in an engine folder (the folder that holds
    /// `meta.json`).
    pub(crate) fn write_into(&self, root: &Path) -> AppResult<()> {
        let stored = Stored {
            document: self.document.clone(),
            signature: self.signature.clone(),
        };
        crate::update_storage::write_atomic(
            &root.join(SIGNED_LIST_FILE),
            &serde_json::to_vec(&stored)?,
        )
    }
}

/// The list in the engine folder `root`, checked with `public_key`, for engine `id` on
/// this host. A linked or special file counts as an invalid list.
pub(crate) fn installed(
    root: &Path,
    id: &str,
    os: &str,
    arch: &str,
    public_key: &str,
) -> Installed {
    let path = root.join(SIGNED_LIST_FILE);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Installed::Missing,
        Err(_) => return Installed::Invalid,
    };
    if !metadata.file_type().is_file() || metadata.len() > 2 * MAX_LIST_BYTES as u64 {
        return Installed::Invalid;
    }
    let Ok(bytes) = fs::read(&path) else {
        return Installed::Invalid;
    };
    let Ok(stored) = serde_json::from_slice::<Stored>(&bytes) else {
        return Installed::Invalid;
    };
    match EngineFileList::verify(stored.document.as_bytes(), &stored.signature, public_key) {
        Ok(list) if list.names(id, os, arch) => Installed::Signed(Box::new(list)),
        _ => Installed::Invalid,
    }
}

#[cfg(test)]
pub(crate) mod test_signing;

#[cfg(test)]
mod tests;
