//! Install a frozen worker from verified payload parts: the parts beside the Windows CUDA
//! installer, or an updater's engine package on any edition. This does not download
//! packages or manage a Python environment.
use std::{
    collections::HashSet,
    fs::{self, File},
    io::{self, Read},
    path::{Component, Path},
};

use crate::update_storage;
use flate2::read::GzDecoder;
use serde::Deserialize;
use sha2::{Digest, Sha256};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Part {
    filename: String,
    size: u64,
    sha256: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema_version: u32,
    format: String,
    backend: String,
    file_count: u64,
    unpacked_bytes: u64,
    parts: Vec<Part>,
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

/// The name an archive entry is known by when unpacked: its components in lower case,
/// so `d//f`, `d/./f` and `D/F` are one file and a later entry cannot replace an
/// earlier one under another spelling.
fn entry_key(path: &Path) -> String {
    path.components()
        .map(|part| part.as_os_str().to_string_lossy().to_lowercase())
        .collect::<Vec<_>>()
        .join("/")
}

fn safe_relative(path: &Path) -> bool {
    !path.as_os_str().is_empty()
        && path
            .components()
            .all(|part| matches!(part, Component::Normal(_)))
        && !path.to_string_lossy().contains(['\\', ':'])
}

// Hash exactly the bytes supplied to gzip/tar, and finish every part before
// publishing extraction. An open descriptor alone does not stop in-place writes.
struct VerifiedPart {
    file: File,
    expected: Part,
    digest: Sha256,
    bytes: u64,
}
impl Read for VerifiedPart {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if buffer.is_empty() {
            return Ok(0);
        }
        let count = self.file.read(buffer)?;
        self.digest.update(&buffer[..count]);
        self.bytes += count as u64;
        if self.bytes > self.expected.size {
            return Err(invalid("engine payload exceeds its manifest"));
        }
        if count == 0 {
            let actual: String = self
                .digest
                .clone()
                .finalize()
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect();
            if self.bytes != self.expected.size || actual != self.expected.sha256 {
                return Err(invalid(
                    "engine payload checksum mismatch during extraction",
                ));
            }
        }
        Ok(count)
    }
}
struct PartsReader {
    files: std::vec::IntoIter<VerifiedPart>,
    current: Option<VerifiedPart>,
}

impl Read for PartsReader {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if buffer.is_empty() {
            return Ok(0);
        }
        loop {
            if self.current.is_none() {
                self.current = self.files.next();
            }
            let Some(current) = self.current.as_mut() else {
                return Ok(0);
            };
            let count = current.read(buffer)?;
            if count != 0 {
                return Ok(count);
            }
            self.current = None;
        }
    }
}

#[cfg(test)]
thread_local! {
    static AFTER_PART_VERIFICATION: std::cell::RefCell<Option<Box<dyn FnOnce()>>> = const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
thread_local! {
    /// Pretend the engine's volume has this much free space.
    static FREE_SPACE: std::cell::Cell<Option<u64>> = const { std::cell::Cell::new(None) };
}

fn free_space(folder: &Path) -> io::Result<u64> {
    #[cfg(test)]
    if let Some(bytes) = FREE_SPACE.with(|bytes| bytes.get()) {
        return Ok(bytes);
    }
    fs2::available_space(folder)
}

/// Whether `name` is `<prefix><process id>`, a folder an installation names.
fn installation_folder(name: &str, kind: &str) -> bool {
    name.strip_prefix(&format!("engine.{kind}-"))
        .is_some_and(|pid| !pid.is_empty() && pid.bytes().all(|byte| byte.is_ascii_digit()))
}

/// The folder an engine folder sits in. For a bare name such as `engine` that is the
/// current folder: `Path::parent` gives an empty path there, which no call accepts.
fn parent_folder(destination: &Path) -> Option<&Path> {
    destination.parent().map(|parent| {
        if parent.as_os_str().is_empty() {
            Path::new(".")
        } else {
            parent
        }
    })
}

/// Whether `folder` holds the worker of a whole engine.
fn has_worker(folder: &Path) -> bool {
    folder.join("localsr-worker.exe").is_file() || folder.join("localsr-worker").is_file()
}

/// Undo what a killed installation left in the engine's folder: a half-unpacked
/// `engine.installing-*` and a half-deleted `engine.deleting-*` go, and an engine that
/// was moved aside but not replaced (`engine.previous-*` with no `engine`) takes its
/// place again. Only folders of those names are touched, and the old engine is deleted
/// under the name `deleting`, so a `previous` is always a whole engine; one without its
/// worker (left by an earlier version's removal) is never put in place.
pub fn recover_install(destination: &Path) -> io::Result<()> {
    let Some(parent) = parent_folder(destination) else {
        return Ok(());
    };
    let mut previous = Vec::new();
    for entry in fs::read_dir(parent)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let kind = if installation_folder(&name, "previous") {
            "previous"
        } else if installation_folder(&name, "installing") || installation_folder(&name, "deleting")
        {
            "leftover"
        } else {
            continue;
        };
        let path = entry.path();
        // A plain file or a link under such a name is removed itself, never followed.
        if !fs::symlink_metadata(&path)?.file_type().is_dir() {
            fs::remove_file(&path)?;
        } else if kind == "previous" {
            previous.push(path);
        } else {
            fs::remove_dir_all(&path)?;
        }
    }
    let mut restored = destination.exists();
    for path in previous {
        if !restored && has_worker(&path) {
            fs::rename(path, destination)?;
            restored = true;
        } else {
            fs::remove_dir_all(path)?;
        }
    }
    Ok(())
}

/// At start: put back the engine that a killed installation moved aside beside the
/// executable (the Windows installer's `engine` folder), so the application never starts
/// without one until the next installation.
pub fn recover_beside(executable: &Path) {
    if let Some(parent) = executable.parent() {
        let _ = recover_install(&parent.join("engine"));
    }
}

pub fn install(manifest_path: &Path, source: &Path, destination: &Path) -> io::Result<()> {
    install_manifest(&fs::read(manifest_path)?, source, destination)
}

/// Install from manifest bytes the caller has verified, so a manifest file replaced
/// after verification is never read.
pub fn install_manifest(manifest: &[u8], source: &Path, destination: &Path) -> io::Result<()> {
    let manifest: Manifest =
        serde_json::from_slice(manifest).map_err(|error| invalid(error.to_string()))?;
    if manifest.schema_version != 1
        || manifest.format != "tar.gz.parts"
        // scripts/prepare_engine_payload.py BACKENDS: one per edition's engine.
        || !matches!(
            manifest.backend.as_str(),
            "CUDA" | "ROCM" | "CPU" | "MPS" | "DIRECTML"
        )
        || manifest.parts.is_empty()
        || manifest.parts.len() > 64
        || manifest.file_count == 0
        || manifest.file_count > 100_000
        || manifest.unpacked_bytes == 0
        || manifest.unpacked_bytes > 100 * 1024u64.pow(3)
    {
        return Err(invalid("unsupported or invalid engine payload manifest"));
    }
    let parent = parent_folder(destination).ok_or_else(|| invalid("missing engine parent"))?;
    if destination.file_name().is_none_or(|name| name != "engine") {
        return Err(invalid("engine destination must be an engine directory"));
    }
    if free_space(parent)? < manifest.unpacked_bytes + 16 * 1024 * 1024 {
        return Err(invalid(
            "not enough free space to install the inference engine",
        ));
    }
    let mut names = HashSet::new();
    let mut files = Vec::new();
    for part in &manifest.parts {
        let name = Path::new(&part.filename);
        if !safe_relative(name)
            || name.components().count() != 1
            // Case-insensitive volumes read two spellings as one file.
            || !names.insert(part.filename.to_lowercase())
            || part.size == 0
            || part.size >= 2 * 1024u64.pow(3)
            || part.sha256.len() != 64
            || !part
                .sha256
                .bytes()
                .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
        {
            return Err(invalid("invalid or duplicate engine payload part"));
        }
        println!("Verifying {}", part.filename);
        let file = File::open(source.join(name))?;
        if file.metadata()?.len() != part.size {
            return Err(invalid(format!("incomplete payload: {}", part.filename)));
        }
        files.push(VerifiedPart {
            file,
            expected: Part {
                filename: part.filename.clone(),
                size: part.size,
                sha256: part.sha256.clone(),
            },
            digest: Sha256::new(),
            bytes: 0,
        });
    }
    #[cfg(test)]
    AFTER_PART_VERIFICATION.with(|hook| {
        if let Some(hook) = hook.borrow_mut().take() {
            hook();
        }
    });
    recover_install(destination)?;
    let staging = parent.join(format!("engine.installing-{}", std::process::id()));
    let backup = parent.join(format!("engine.previous-{}", std::process::id()));
    let deleting = parent.join(format!("engine.deleting-{}", std::process::id()));
    update_storage::fault("staging-create")?;
    fs::create_dir(&staging)?;
    let result = (|| {
        let reader = PartsReader {
            files: files.into_iter(),
            current: None,
        };
        let mut archive = tar::Archive::new(GzDecoder::new(reader));
        let mut count = 0u64;
        let mut bytes = 0u64;
        let mut paths = HashSet::new();
        // The folders that now hold unpacked files, to be made durable with them.
        let mut folders = HashSet::from([staging.clone()]);
        for entry in archive.entries()? {
            let mut entry = entry?;
            let relative = entry.path()?.into_owned();
            if !safe_relative(&relative)
                || !entry.header().entry_type().is_file()
                || !paths.insert(entry_key(&relative))
            {
                return Err(invalid(
                    "unsafe, duplicate, or non-file engine archive entry",
                ));
            }
            bytes = bytes
                .checked_add(entry.size())
                .ok_or_else(|| invalid("engine size overflow"))?;
            count += 1;
            if bytes > manifest.unpacked_bytes || count > manifest.file_count {
                return Err(invalid("engine archive exceeds its manifest"));
            }
            update_storage::fault("extract-entry")?;
            if !entry.unpack_in(&staging)? {
                return Err(invalid("engine archive escaped its destination"));
            }
            let unpacked = staging.join(&relative);
            update_storage::sync_file(&unpacked)?;
            let mut folder = unpacked.parent();
            while let Some(path) = folder.filter(|path| path.starts_with(&staging)) {
                if !folders.insert(path.to_path_buf()) {
                    break;
                }
                folder = path.parent();
            }
        }
        // Finish decoding to verify the gzip checksum, with a bounded padding allowance.
        let mut decoded = archive.into_inner();
        let mut tail = (&mut decoded).take(1024 * 1024 + 1);
        if io::copy(&mut tail, &mut io::sink())? > 1024 * 1024 {
            return Err(invalid("excess trailing engine archive data"));
        }
        // gzip can finish without another underlying read: explicitly reach EOF on
        // the compressed parts so the final part's digest is checked as well.
        io::copy(&mut decoded.into_inner(), &mut io::sink())?;
        if count != manifest.file_count || bytes != manifest.unpacked_bytes || !has_worker(&staging)
        {
            return Err(invalid("engine archive is incomplete"));
        }
        // The new engine is on disk before it is renamed into place and the old one goes.
        for folder in &folders {
            update_storage::sync_dir(folder)
                .map_err(|error| io::Error::other(error.to_string()))?;
        }
        if destination.exists() {
            update_storage::fault("backup-rename")?;
            fs::rename(destination, &backup)?;
        }
        let installed = update_storage::fault("install-rename")
            .and_then(|()| fs::rename(&staging, destination));
        if let Err(error) = installed {
            if backup.exists() {
                fs::rename(&backup, destination)?;
            }
            return Err(error);
        }
        // Both renames are durable before the old engine goes.
        update_storage::sync_dir(parent).map_err(|error| io::Error::other(error.to_string()))?;
        update_storage::fault("backup-remove")?;
        if backup.exists() {
            // Deleting is not atomic: under its own name a half-deleted engine is never
            // mistaken for the engine that was moved aside.
            fs::rename(&backup, &deleting)?;
            update_storage::fault("backup-delete")?;
            fs::remove_dir_all(&deleting)?;
        }
        Ok(())
    })();
    if staging.exists() {
        let _ = fs::remove_dir_all(&staging);
    }
    result
}

pub fn run_arguments(arguments: &[std::ffi::OsString]) -> Option<i32> {
    if arguments
        .get(1)
        .is_none_or(|argument| argument != "--install-engine")
    {
        return None;
    }
    if let Err(error) = crate::distribution::require_direct_updates() {
        eprintln!("{error}");
        return Some(1);
    }
    if arguments.len() != 5 {
        eprintln!("expected --install-engine MANIFEST PAYLOAD_DIRECTORY ENGINE_DIRECTORY");
        return Some(1);
    }
    Some(
        match install(
            Path::new(&arguments[2]),
            Path::new(&arguments[3]),
            Path::new(&arguments[4]),
        ) {
            Ok(()) => 0,
            Err(error) => {
                eprintln!("Engine installation failed: {error}");
                1
            }
        },
    )
}

#[cfg(test)]
mod tests;
