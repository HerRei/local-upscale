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
    let Some(parent) = destination.parent() else {
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
    let parent = destination
        .parent()
        .ok_or_else(|| invalid("missing engine parent"))?;
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
        if count != manifest.file_count
            || bytes != manifest.unpacked_bytes
            || !(staging.join("localsr-worker.exe").is_file()
                || staging.join("localsr-worker").is_file())
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
                eprintln!("CUDA engine installation failed: {error}");
                1
            }
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use flate2::{write::GzEncoder, Compression};
    use std::io::Write;

    fn payload(root: &Path, bad_path: bool, link: bool) -> std::path::PathBuf {
        let mut archive = tar::Builder::new(Vec::new());
        let mut header = tar::Header::new_gnu();
        header.set_path("localsr-worker.exe").unwrap();
        header.set_mode(0o755);
        header.set_size(3);
        if bad_path {
            header.as_mut_bytes()[..100].fill(0);
            header.as_mut_bytes()[..11].copy_from_slice(b"../escaped!");
        }
        if link {
            header.set_entry_type(tar::EntryType::Symlink);
            header.set_link_name("../outside").unwrap();
        }
        header.set_cksum();
        archive.append(&header, &b"exe"[..]).unwrap();
        let mut gzip = GzEncoder::new(Vec::new(), Compression::fast());
        gzip.write_all(&archive.into_inner().unwrap()).unwrap();
        let compressed = gzip.finish().unwrap();
        let mut parts = Vec::new();
        for (index, bytes) in compressed.chunks(29).enumerate() {
            let filename = format!("cuda.part-{index:04}");
            fs::write(root.join(&filename), bytes).unwrap();
            let digest: String = Sha256::digest(bytes)
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
            parts.push(
                serde_json::json!({"filename": filename, "size": bytes.len(), "sha256": digest}),
            );
        }
        let manifest = root.join("engine-payload.json");
        fs::write(
            &manifest,
            serde_json::to_vec(&serde_json::json!({
                "schema_version": 1, "format": "tar.gz.parts", "backend": "CUDA",
                "file_count": 1, "unpacked_bytes": 3, "parts": parts,
            }))
            .unwrap(),
        )
        .unwrap();
        manifest
    }

    /// A payload of several files, one of them in a folder.
    fn payload_files(root: &Path, files: &[(&str, &[u8])]) -> std::path::PathBuf {
        let mut archive = tar::Builder::new(Vec::new());
        for (name, content) in files {
            let mut header = tar::Header::new_gnu();
            header.set_path(name).unwrap();
            header.set_mode(0o755);
            header.set_size(content.len() as u64);
            header.set_cksum();
            archive.append(&header, *content).unwrap();
        }
        let mut gzip = GzEncoder::new(Vec::new(), Compression::fast());
        gzip.write_all(&archive.into_inner().unwrap()).unwrap();
        let compressed = gzip.finish().unwrap();
        let mut parts = Vec::new();
        for (index, bytes) in compressed.chunks(29).enumerate() {
            let filename = format!("cuda.part-{index:04}");
            fs::write(root.join(&filename), bytes).unwrap();
            let digest: String = Sha256::digest(bytes)
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
            parts.push(
                serde_json::json!({"filename": filename, "size": bytes.len(), "sha256": digest}),
            );
        }
        let manifest = root.join("engine-payload.json");
        fs::write(
            &manifest,
            serde_json::to_vec(&serde_json::json!({
                "schema_version": 1, "format": "tar.gz.parts", "backend": "CUDA",
                "file_count": files.len(),
                "unpacked_bytes": files.iter().map(|(_, c)| c.len()).sum::<usize>(),
                "parts": parts,
            }))
            .unwrap(),
        )
        .unwrap();
        manifest
    }

    #[test]
    fn mutated_engine_parts_are_checked_as_they_are_extracted() {
        let root = tempfile::tempdir().unwrap();
        let manifest_path = payload(root.path(), false, false);
        let manifest: serde_json::Value =
            serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
        let parts: Vec<_> = manifest["parts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| {
                (
                    root.path().join(p["filename"].as_str().unwrap()),
                    p["size"].as_u64().unwrap() as usize,
                )
            })
            .collect();
        let compressed: Vec<_> = parts
            .iter()
            .flat_map(|(path, _)| fs::read(path).unwrap())
            .collect();
        let mut archive = Vec::new();
        GzDecoder::new(compressed.as_slice())
            .read_to_end(&mut archive)
            .unwrap();
        archive[512..515].copy_from_slice(b"bad");
        let mut encoder = GzEncoder::new(Vec::new(), Compression::fast());
        encoder.write_all(&archive).unwrap();
        let replacement = encoder.finish().unwrap();
        AFTER_PART_VERIFICATION.with(|hook| {
            *hook.borrow_mut() = Some(Box::new(move || {
                let mut remaining = replacement.as_slice();
                let last = parts.len() - 1;
                for (index, (path, size)) in parts.into_iter().enumerate() {
                    let count = if index == last {
                        remaining.len()
                    } else {
                        size.min(remaining.len())
                    };
                    // Truncate/write the same inode: held handles are not a snapshot.
                    fs::write(path, &remaining[..count]).unwrap();
                    remaining = &remaining[count..];
                }
            }))
        });
        let destination = root.path().join("engine");
        fs::create_dir(&destination).unwrap();
        fs::write(destination.join("old"), b"keep").unwrap();
        assert!(install(&manifest_path, root.path(), &destination).is_err());
        assert_eq!(fs::read(destination.join("old")).unwrap(), b"keep");
    }

    #[test]
    fn installs_multiple_verified_parts_and_replaces_previous_engine() {
        let root = tempfile::tempdir().unwrap();
        let manifest = payload(root.path(), false, false);
        let destination = root.path().join("engine");
        fs::create_dir(&destination).unwrap();
        fs::write(destination.join("old.exe"), b"old").unwrap();
        install(&manifest, root.path(), &destination).unwrap();
        assert_eq!(
            fs::read(destination.join("localsr-worker.exe")).unwrap(),
            b"exe"
        );
        assert!(!destination.join("old.exe").exists());
    }

    /// The engine folder after a failure or a crash: missing, or a whole engine; never
    /// the unpacked half of one. Returns the worker's content.
    fn engine_content(folder: &Path) -> Option<Vec<u8>> {
        if !folder.exists() {
            return None;
        }
        let worker =
            fs::read(folder.join("localsr-worker.exe")).expect("engine without its worker");
        // A whole new engine has all its files; the old one has only its worker.
        if worker == b"exe" {
            assert_eq!(fs::read(folder.join("lib/data.bin")).unwrap(), b"data");
            assert_eq!(fs::read(folder.join("z.dll")).unwrap(), b"z");
        }
        Some(worker)
    }

    fn engine_leftovers(root: &Path) -> Vec<String> {
        let mut names: Vec<String> = fs::read_dir(root)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().into_string().unwrap())
            .filter(|name| name.starts_with("engine."))
            .collect();
        names.sort();
        names
    }

    /// A payload of three files, the worker among them.
    fn engine_payload(root: &Path) -> std::path::PathBuf {
        payload_files(
            root,
            &[
                ("lib/data.bin", b"data"),
                ("localsr-worker.exe", b"exe"),
                ("z.dll", b"z"),
            ],
        )
    }

    // A failure or a crash at any step of the engine install never leaves a partial
    // engine, and the next install attempt restores or replaces the engine and clears
    // every staging and backup folder.
    #[test]
    fn the_engine_install_survives_a_failure_or_a_crash_at_every_step() {
        use update_storage::fault::{self, Mode};
        for had_engine in [true, false] {
            let fresh = |root: &Path| {
                let manifest = engine_payload(root);
                let destination = root.join("engine");
                if had_engine {
                    fs::create_dir(&destination).unwrap();
                    fs::write(destination.join("localsr-worker.exe"), b"old").unwrap();
                }
                (manifest, destination)
            };
            let root = tempfile::tempdir().unwrap();
            let (manifest, destination) = fresh(root.path());
            fault::arm(None);
            install(&manifest, root.path(), &destination).unwrap();
            let steps = fault::seen();
            for expected in [
                "staging-create",
                "extract-entry",
                "install-rename",
                "backup-remove",
            ] {
                assert!(steps.contains(&expected), "{steps:?} lacks {expected}");
            }
            // One extraction per archive entry: a crash after some files were unpacked.
            assert_eq!(
                steps.iter().filter(|s| **s == "extract-entry").count(),
                3,
                "{steps:?}"
            );
            let installed_at = steps.iter().position(|s| *s == "install-rename").unwrap();
            for (index, step) in steps.iter().enumerate() {
                for mode in [Mode::Fail, Mode::Crash] {
                    let why = format!("{step} #{index} {mode:?} had_engine={had_engine}");
                    let root = tempfile::tempdir().unwrap();
                    let (manifest, destination) = fresh(root.path());
                    fault::arm(Some((index, mode)));
                    let _ = fault::crashed(|| install(&manifest, root.path(), &destination));
                    fault::arm(None);
                    let new_in_place = index > installed_at;
                    // Before any recovery: the engine is missing only for a first install
                    // (nothing was there yet), or while the old one is moved aside, which
                    // a crash can leave and a failure restores. It is never partial.
                    let moved_aside = had_engine && mode == Mode::Crash && index == installed_at;
                    match engine_content(&destination) {
                        Some(content) if new_in_place => assert_eq!(content, b"exe", "{why}"),
                        Some(content) => assert_eq!(content, b"old", "{why}"),
                        None => assert!(moved_aside || (!had_engine && !new_in_place), "{why}"),
                    }
                    // Once the old engine is being deleted it no longer has the name that
                    // recovery would restore.
                    if step == &"backup-delete" {
                        assert_eq!(
                            engine_leftovers(root.path()),
                            [format!("engine.deleting-{}", std::process::id())],
                            "{why}"
                        );
                    }
                    recover_install(&destination).unwrap();
                    let expected = if new_in_place {
                        Some(b"exe".to_vec())
                    } else {
                        had_engine.then(|| b"old".to_vec())
                    };
                    assert_eq!(engine_content(&destination), expected, "{why}");
                    assert_eq!(engine_leftovers(root.path()), Vec::<String>::new(), "{why}");
                    // And an install after the crash completes.
                    install(&manifest, root.path(), &destination).unwrap();
                    assert_eq!(engine_content(&destination), Some(b"exe".to_vec()), "{why}");
                    assert_eq!(engine_leftovers(root.path()), Vec::<String>::new(), "{why}");
                }
            }
        }
    }

    /// A folder with the engine's worker in it, and optionally more.
    fn engine_folder(path: &Path, worker: Option<&[u8]>, other: &[&str]) {
        fs::create_dir(path).unwrap();
        if let Some(content) = worker {
            fs::write(path.join("localsr-worker.exe"), content).unwrap();
        }
        for name in other {
            fs::write(path.join(name), b"x").unwrap();
        }
    }

    // A backup that a killed removal left half deleted is never put in place of the
    // engine, whichever of the leftovers the folder lists first.
    #[test]
    fn a_half_removed_backup_is_never_restored_over_a_whole_one() {
        for (partial, whole) in [
            ("engine.previous-1", "engine.previous-2"),
            ("engine.previous-2", "engine.previous-1"),
        ] {
            let root = tempfile::tempdir().unwrap();
            engine_folder(&root.path().join(partial), None, &["data.bin"]);
            engine_folder(&root.path().join(whole), Some(b"old"), &[]);
            let destination = root.path().join("engine");
            recover_install(&destination).unwrap();
            assert_eq!(engine_content(&destination), Some(b"old".to_vec()));
            assert_eq!(engine_leftovers(root.path()), Vec::<String>::new());
        }
    }

    // Recovery clears only the folders an installation names (`engine.<kind>-<pid>`),
    // never another entry that starts with the same words, and does not stop at a plain
    // file or a link under one of those names.
    #[cfg(unix)]
    #[test]
    fn recovery_removes_only_what_an_installation_leaves() {
        let root = tempfile::tempdir().unwrap();
        let parent = root.path();
        let destination = parent.join("engine");
        engine_folder(&destination, Some(b"old"), &[]);
        // The user's.
        engine_folder(&parent.join("engine.previous-backup"), Some(b"mine"), &[]);
        fs::write(parent.join("engine.installing-notes.txt"), b"mine").unwrap();
        fs::write(parent.join("engine.previous-7.zip"), b"mine").unwrap();
        // Left by installations: a plain file, a link to a folder elsewhere, a folder.
        fs::write(parent.join("engine.previous-7"), b"stale").unwrap();
        let outside = parent.join("outside");
        engine_folder(&outside, Some(b"outside"), &[]);
        std::os::unix::fs::symlink(&outside, parent.join("engine.installing-9")).unwrap();
        engine_folder(&parent.join("engine.installing-10"), None, &["half"]);
        engine_folder(&parent.join("engine.deleting-11"), None, &["half"]);
        recover_install(&destination).unwrap();
        assert_eq!(
            engine_leftovers(parent),
            [
                "engine.installing-notes.txt",
                "engine.previous-7.zip",
                "engine.previous-backup"
            ]
        );
        assert_eq!(
            fs::read(outside.join("localsr-worker.exe")).unwrap(),
            b"outside"
        );
        assert_eq!(engine_content(&destination), Some(b"old".to_vec()));
    }

    // A killed installation between the two renames leaves no engine at its name. The
    // next start of the application, not only the next installation, puts it back.
    #[test]
    fn the_next_start_restores_an_engine_that_a_killed_install_moved_aside() {
        let root = tempfile::tempdir().unwrap();
        engine_folder(&root.path().join("engine.previous-5"), Some(b"old"), &[]);
        engine_folder(&root.path().join("engine.installing-5"), Some(b"exe"), &[]);
        recover_beside(&root.path().join("localsr-next.exe"));
        assert_eq!(
            engine_content(&root.path().join("engine")),
            Some(b"old".to_vec())
        );
        assert_eq!(engine_leftovers(root.path()), Vec::<String>::new());
    }

    // The unpacked files and the folders that hold them are durable before the swap
    // renames them into place, and the renames are durable before the old engine is
    // deleted: otherwise power loss could keep the renames and the deletion, but not the
    // new files' content.
    #[test]
    fn the_new_engine_is_durable_before_the_old_one_is_deleted() {
        use update_storage::fault;
        let root = tempfile::tempdir().unwrap();
        let manifest = engine_payload(root.path());
        let destination = root.path().join("engine");
        engine_folder(&destination, Some(b"old"), &[]);
        fault::arm(None);
        install(&manifest, root.path(), &destination).unwrap();
        let steps = fault::seen();
        let synced = fault::synced();
        let renamed = steps.iter().position(|s| *s == "install-rename").unwrap();
        let deleting = steps.iter().position(|s| *s == "backup-remove").unwrap();
        let before_rename = |name: &str| {
            synced
                .iter()
                .any(|(passed, path)| *passed <= renamed && path.ends_with(name))
        };
        for name in ["localsr-worker.exe", "lib/data.bin", "z.dll", "lib"] {
            assert!(
                before_rename(name),
                "{name} not synced before the rename: {synced:?}"
            );
        }
        assert!(
            synced.iter().any(|(passed, path)| *passed <= renamed
                && path
                    .file_name()
                    .is_some_and(|n| n.to_string_lossy().starts_with("engine.installing-"))),
            "the staging folder was not synced: {synced:?}"
        );
        assert!(
            synced.iter().any(|(passed, path)| *passed > renamed
                && *passed <= deleting
                && path == root.path()),
            "the renames were not synced before the old engine was deleted: {synced:?}"
        );
    }

    #[test]
    fn installs_the_engine_package_of_every_edition() {
        for (backend, accepted) in [
            ("CUDA", true),
            ("ROCM", true),
            ("CPU", true),
            ("MPS", true),
            ("DIRECTML", true),
            ("XPU", false),
        ] {
            let root = tempfile::tempdir().unwrap();
            let manifest = payload(root.path(), false, false);
            let mut value: serde_json::Value =
                serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
            value["backend"] = backend.into();
            fs::write(&manifest, serde_json::to_vec(&value).unwrap()).unwrap();
            let destination = root.path().join("engine");
            assert_eq!(
                install(&manifest, root.path(), &destination).is_ok(),
                accepted,
                "{backend}"
            );
        }
    }

    #[test]
    fn rejects_corrupt_or_missing_parts_without_touching_previous_engine() {
        for missing in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let manifest = payload(root.path(), false, false);
            let destination = root.path().join("engine");
            fs::create_dir(&destination).unwrap();
            fs::write(destination.join("old.exe"), b"keep").unwrap();
            let part = root.path().join("cuda.part-0000");
            if missing {
                fs::remove_file(part).unwrap();
            } else {
                fs::write(part, [0u8; 29]).unwrap();
            }
            assert!(install(&manifest, root.path(), &destination).is_err());
            assert_eq!(fs::read(destination.join("old.exe")).unwrap(), b"keep");
        }
    }

    #[test]
    fn rejects_archive_traversal_and_symlinks() {
        for (bad_path, link) in [(true, false), (false, true)] {
            let root = tempfile::tempdir().unwrap();
            let manifest = payload(root.path(), bad_path, link);
            assert!(install(&manifest, root.path(), &root.path().join("engine")).is_err());
            assert!(!root.path().join("escaped!").exists());
            assert!(!root.path().join("engine").exists());
        }
    }

    #[test]
    fn rejects_wrong_expanded_size() {
        let root = tempfile::tempdir().unwrap();
        let manifest = payload(root.path(), false, false);
        let mut value: serde_json::Value =
            serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
        value["unpacked_bytes"] = 2.into();
        fs::write(&manifest, serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(install(&manifest, root.path(), &root.path().join("engine")).is_err());
        assert!(!root.path().join("engine").exists());
    }

    /// A payload of one part (or `chunk`-byte parts) made from raw tar bytes, with a
    /// manifest that declares `count` files and `bytes` unpacked bytes.
    fn payload_from_tar(
        root: &Path,
        tar_bytes: &[u8],
        count: u64,
        bytes: u64,
        chunk: usize,
    ) -> std::path::PathBuf {
        let mut gzip = GzEncoder::new(Vec::new(), Compression::fast());
        gzip.write_all(tar_bytes).unwrap();
        let compressed = gzip.finish().unwrap();
        let mut parts = Vec::new();
        for (index, part) in compressed.chunks(chunk).enumerate() {
            let filename = format!("cuda.part-{index:04}");
            fs::write(root.join(&filename), part).unwrap();
            let digest: String = Sha256::digest(part)
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
            parts.push(
                serde_json::json!({"filename": filename, "size": part.len(), "sha256": digest}),
            );
        }
        let manifest = root.join("engine-payload.json");
        fs::write(
            &manifest,
            serde_json::to_vec(&serde_json::json!({
                "schema_version": 1, "format": "tar.gz.parts", "backend": "CUDA",
                "file_count": count, "unpacked_bytes": bytes, "parts": parts,
            }))
            .unwrap(),
        )
        .unwrap();
        manifest
    }

    type Entry<'a> = (&'a str, tar::EntryType, &'a [u8]);

    /// Tar bytes of the given entries; a name is written into the header as it is.
    fn tar_of(entries: &[Entry]) -> Vec<u8> {
        let mut archive = tar::Builder::new(Vec::new());
        for (name, kind, content) in entries {
            let mut header = tar::Header::new_gnu();
            header.set_entry_type(*kind);
            header.set_mode(0o755);
            header.set_size(content.len() as u64);
            if matches!(kind, tar::EntryType::Link | tar::EntryType::Symlink) {
                header.set_link_name("localsr-worker.exe").unwrap();
            }
            header.as_mut_bytes()[..100].fill(0);
            header.as_mut_bytes()[..name.len()].copy_from_slice(name.as_bytes());
            header.set_cksum();
            archive.append(&header, *content).unwrap();
        }
        archive.into_inner().unwrap()
    }

    /// An engine folder holding the previous engine, to see that a refusal leaves it be.
    fn previous_engine(root: &Path) -> std::path::PathBuf {
        let destination = root.join("engine");
        fs::create_dir(&destination).unwrap();
        fs::write(destination.join("old.exe"), b"keep").unwrap();
        destination
    }

    /// The install fails with a message containing `expected`, and the previous engine
    /// is as it was, with nothing else left in the folder.
    fn assert_refused(manifest: &Path, root: &Path, expected: &str) {
        let destination = previous_engine(root);
        let error = format!(
            "{:?}",
            install(manifest, root, &destination)
                .expect_err("the install should have been refused")
        );
        assert!(error.contains(expected), "{error:?} lacks {expected:?}");
        assert_eq!(fs::read(destination.join("old.exe")).unwrap(), b"keep");
        assert_eq!(engine_leftovers(root), Vec::<String>::new());
    }

    #[test]
    fn only_plain_relative_paths_are_safe() {
        for good in ["a", "a/b", "localsr-worker.exe", "lib/data.bin", "a b"] {
            assert!(safe_relative(Path::new(good)), "{good}");
        }
        for bad in [
            "", "..", "../x", "a/../b", "/abs", "a\\b", "c:x", "a:b", "a/b:c",
        ] {
            assert!(!safe_relative(Path::new(bad)), "{bad:?}");
        }
    }

    /// Each manifest field breaks one rule at a time; the message says it was the
    /// manifest, so a rule that is missing is not hidden by a later failure.
    #[test]
    fn a_manifest_that_breaks_one_rule_is_refused_before_anything_is_touched() {
        const MANIFEST: &str = "unsupported or invalid engine payload manifest";
        let part = |index: usize| {
            serde_json::json!({
                "filename": format!("cuda.part-{index:04}"), "size": 10, "sha256": "0".repeat(64),
            })
        };
        let many = |count: usize| (0..count).map(part).collect::<Vec<_>>();
        let gib = 1024u64.pow(3);
        let breaks: Vec<(&str, serde_json::Value, bool)> = vec![
            ("schema_version", 2.into(), true),
            ("schema_version", 0.into(), true),
            ("format", "zip".into(), true),
            ("backend", "XPU".into(), true),
            ("backend", "cuda".into(), true),
            ("parts", serde_json::json!([]), true),
            ("parts", many(65).into(), true),
            ("file_count", 0.into(), true),
            ("file_count", 100_001.into(), true),
            ("unpacked_bytes", 0.into(), true),
            ("unpacked_bytes", (100 * gib + 1).into(), true),
            // The limits themselves are allowed by the manifest rules.
            ("parts", many(64).into(), false),
            ("file_count", 100_000.into(), false),
            ("unpacked_bytes", (100 * gib).into(), false),
        ];
        for (field, value, refused) in breaks {
            let root = tempfile::tempdir().unwrap();
            let manifest = payload(root.path(), false, false);
            let mut json: serde_json::Value =
                serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
            json[field] = value.clone();
            fs::write(&manifest, serde_json::to_vec(&json).unwrap()).unwrap();
            let destination = previous_engine(root.path());
            let error = install(&manifest, root.path(), &destination)
                .expect_err("only well-formed payloads install")
                .to_string();
            assert_eq!(
                error.contains(MANIFEST),
                refused,
                "{field}={value}: {error}"
            );
            assert_eq!(fs::read(destination.join("old.exe")).unwrap(), b"keep");
        }
    }

    /// The free space needed is the unpacked size plus 16 MiB: one byte less is refused
    /// before anything is unpacked, exactly that much or more goes on.
    #[test]
    fn the_install_needs_its_free_space_margin() {
        const SPACE: &str = "not enough free space";
        let margin = 16 * 1024 * 1024u64;
        for (free, refused) in [
            (0, true),
            (3, true),
            (3 + margin - 1, true),
            (3 + margin, false),
            (3 + margin + 1, false),
            (u64::MAX, false),
        ] {
            let root = tempfile::tempdir().unwrap();
            let manifest = payload(root.path(), false, false);
            let destination = previous_engine(root.path());
            FREE_SPACE.with(|bytes| bytes.set(Some(free)));
            let result = install(&manifest, root.path(), &destination);
            FREE_SPACE.with(|bytes| bytes.set(None));
            if refused {
                let error = result.expect_err("too little space").to_string();
                assert!(error.contains(SPACE), "{free}: {error}");
                assert_eq!(fs::read(destination.join("old.exe")).unwrap(), b"keep");
                assert_eq!(engine_leftovers(root.path()), Vec::<String>::new());
            } else {
                result.unwrap_or_else(|error| panic!("{free}: {error}"));
                assert_eq!(
                    fs::read(destination.join("localsr-worker.exe")).unwrap(),
                    b"exe"
                );
            }
        }
    }

    /// One field of one part at a time is wrong.
    #[test]
    fn a_part_with_invalid_metadata_is_refused() {
        const PART: &str = "invalid or duplicate engine payload part";
        let sha = "ab".repeat(32);
        let cases: Vec<(&str, serde_json::Value)> = vec![
            ("filename", "dir/cuda.part-0000".into()),
            ("filename", "../cuda.part-0000".into()),
            ("filename", "cuda\\part".into()),
            ("filename", "c:part".into()),
            ("filename", "".into()),
            ("size", 0.into()),
            ("size", (2 * 1024u64.pow(3)).into()),
            ("size", (2 * 1024u64.pow(3) + 1).into()),
            ("sha256", sha[..63].into()),
            ("sha256", format!("{sha}a").into()),
            ("sha256", "".into()),
            ("sha256", sha.to_uppercase().into()),
            ("sha256", format!("{}A", &sha[..63]).into()),
            ("sha256", format!("{}g", &sha[..63]).into()),
            ("sha256", format!("{}-", &sha[..63]).into()),
        ];
        for (field, value) in cases {
            let root = tempfile::tempdir().unwrap();
            let manifest = payload(root.path(), false, false);
            let mut json: serde_json::Value =
                serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
            json["parts"][0][field] = value.clone();
            fs::write(&manifest, serde_json::to_vec(&json).unwrap()).unwrap();
            assert_refused_with(&manifest, root.path(), PART, &format!("{field}={value}"));
        }
        // The same name twice.
        let root = tempfile::tempdir().unwrap();
        let manifest = payload(root.path(), false, false);
        let mut json: serde_json::Value =
            serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
        let first = json["parts"][0].clone();
        json["parts"] = serde_json::json!([first, first]);
        fs::write(&manifest, serde_json::to_vec(&json).unwrap()).unwrap();
        assert_refused_with(&manifest, root.path(), PART, "duplicate");
        // The same name in another letter case is the same file on a case-insensitive
        // volume.
        let root = tempfile::tempdir().unwrap();
        let manifest = payload(root.path(), false, false);
        let mut json: serde_json::Value =
            serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
        let first = json["parts"][0].clone();
        let mut shouted = first.clone();
        shouted["filename"] = first["filename"].as_str().unwrap().to_uppercase().into();
        json["parts"] = serde_json::json!([first, shouted]);
        fs::write(&manifest, serde_json::to_vec(&json).unwrap()).unwrap();
        assert_refused_with(&manifest, root.path(), PART, "duplicate in another case");
    }

    fn assert_refused_with(manifest: &Path, root: &Path, expected: &str, why: &str) {
        let destination = previous_engine(root);
        let error = format!(
            "{:?}",
            install(manifest, root, &destination).expect_err(why)
        );
        assert!(error.contains(expected), "{why}: {error:?}");
        assert_eq!(
            fs::read(destination.join("old.exe")).unwrap(),
            b"keep",
            "{why}"
        );
        assert_eq!(engine_leftovers(root), Vec::<String>::new(), "{why}");
    }

    #[test]
    fn entries_with_the_same_components_are_one_file_whatever_the_spelling() {
        let key = |name: &str| entry_key(Path::new(name));
        assert_eq!(key("d/f"), key("d//f"));
        assert_eq!(key("d/f"), key("d/./f"));
        assert_eq!(key("d/f"), key("D/F"));
        assert_ne!(key("d/f"), key("d/g"));
        assert_ne!(key("d/f"), key("df"));
    }

    /// A part whose size is right but whose bytes are not the ones the manifest names.
    #[test]
    fn a_part_with_the_right_size_and_the_wrong_checksum_is_refused() {
        let root = tempfile::tempdir().unwrap();
        let manifest = payload(root.path(), false, false);
        let mut json: serde_json::Value =
            serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
        json["parts"][0]["sha256"] = "0".repeat(64).into();
        fs::write(&manifest, serde_json::to_vec(&json).unwrap()).unwrap();
        assert_refused(&manifest, root.path(), "checksum mismatch");
    }

    /// A part that grows after its size was checked is refused as exceeding the manifest.
    #[test]
    fn a_part_that_grows_while_it_is_read_is_refused() {
        let root = tempfile::tempdir().unwrap();
        let manifest = payload(root.path(), false, false);
        let last = fs::read_dir(root.path())
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .filter(|path| path.to_string_lossy().contains("cuda.part-"))
            .max()
            .unwrap();
        AFTER_PART_VERIFICATION.with(|hook| {
            *hook.borrow_mut() = Some(Box::new(move || {
                let mut file = fs::OpenOptions::new().append(true).open(last).unwrap();
                file.write_all(b"extra").unwrap();
            }))
        });
        assert_refused(&manifest, root.path(), "exceeds its manifest");
    }

    /// Entries an engine archive may not contain, each beside a good worker.
    #[test]
    fn an_archive_entry_that_is_not_a_plain_new_file_is_refused() {
        use tar::EntryType::{Directory, Fifo, Link, Regular, Symlink};
        const ENTRY: &str = "unsafe, duplicate, or non-file engine archive entry";
        let worker = ("localsr-worker.exe", Regular, &b"exe"[..]);
        let cases: Vec<(&str, Vec<Entry>)> = vec![
            ("hard link", vec![worker, ("link", Link, b"")]),
            ("symlink", vec![worker, ("link", Symlink, b"")]),
            ("folder", vec![worker, ("folder/", Directory, b"")]),
            ("fifo", vec![worker, ("fifo", Fifo, b"")]),
            ("same name twice", vec![worker, worker]),
            (
                "names that differ in case",
                vec![worker, ("LocalSR-Worker.EXE", Regular, b"exe")],
            ),
            ("backslash", vec![worker, ("a\\b", Regular, b"x")]),
            ("colon", vec![worker, ("a:b", Regular, b"x")]),
            ("traversal", vec![worker, ("../escaped", Regular, b"x")]),
            ("absolute", vec![worker, ("/escaped", Regular, b"x")]),
        ];
        for (why, entries) in cases {
            let root = tempfile::tempdir().unwrap();
            let bytes: u64 = entries.iter().map(|(_, _, c)| c.len() as u64).sum();
            let manifest = payload_from_tar(
                root.path(),
                &tar_of(&entries),
                entries.len() as u64,
                bytes,
                4096,
            );
            assert_refused_with(&manifest, root.path(), ENTRY, why);
            assert!(!root.path().join("escaped").exists(), "{why}");
        }
    }

    /// More files or more bytes than the manifest declares stop the unpacking; fewer, or
    /// no worker, is an incomplete engine. Each rule on its own.
    #[test]
    fn an_archive_that_does_not_match_its_manifest_is_refused() {
        use tar::EntryType::Regular;
        let worker = ("localsr-worker.exe", Regular, &b"exe"[..]);
        let extra = ("z.dll", Regular, &b"zz"[..]);
        // (entries, declared files, declared bytes, message)
        let cases: Vec<(Vec<Entry>, u64, u64, &str)> = vec![
            (vec![worker, extra], 1, 5, "exceeds its manifest"),
            (vec![worker, extra], 2, 4, "exceeds its manifest"),
            (vec![worker, extra], 3, 5, "incomplete"),
            (vec![worker, extra], 2, 6, "incomplete"),
            (vec![extra], 1, 2, "incomplete"),
            (
                vec![("localsr-worker.exe/", tar::EntryType::Directory, b"")],
                1,
                1,
                "unsafe",
            ),
        ];
        for (index, (entries, count, bytes, expected)) in cases.into_iter().enumerate() {
            let root = tempfile::tempdir().unwrap();
            let manifest = payload_from_tar(root.path(), &tar_of(&entries), count, bytes, 4096);
            assert_refused_with(&manifest, root.path(), expected, &format!("case {index}"));
        }
        // Either spelling of the worker completes an engine.
        for name in ["localsr-worker", "localsr-worker.exe"] {
            let root = tempfile::tempdir().unwrap();
            let manifest =
                payload_from_tar(root.path(), &tar_of(&[(name, Regular, b"exe")]), 1, 3, 4096);
            let destination = root.path().join("engine");
            install(&manifest, root.path(), &destination).unwrap();
            assert_eq!(fs::read(destination.join(name)).unwrap(), b"exe");
        }
    }

    /// A gzip stream may end in up to 1 MiB of padding after the last entry, and not one
    /// byte more.
    #[test]
    fn trailing_archive_padding_is_allowed_up_to_one_mebibyte() {
        let tar = tar_of(&[("localsr-worker.exe", tar::EntryType::Regular, b"exe")]);
        // The tar reader stops after the first of the two zero blocks that end an archive,
        // so one block of them is already part of the tail.
        let limit = 1024 * 1024 - 512;
        for (padding, accepted) in [
            (0, true),
            (limit / 2, true),
            (limit - 1, true),
            (limit, true),
            (limit + 1, false),
            (2 * limit, false),
        ] {
            let root = tempfile::tempdir().unwrap();
            let mut bytes = tar.clone();
            bytes.resize(bytes.len() + padding, 0);
            let manifest = payload_from_tar(root.path(), &bytes, 1, 3, 1 << 20);
            let destination = root.path().join("engine");
            let result = install(&manifest, root.path(), &destination);
            if accepted {
                result.unwrap_or_else(|error| panic!("{padding}: {error}"));
                assert_eq!(
                    fs::read(destination.join("localsr-worker.exe")).unwrap(),
                    b"exe"
                );
            } else {
                let error = result.expect_err("too much padding").to_string();
                assert!(error.contains("excess trailing"), "{padding}: {error}");
                assert!(!destination.exists(), "{padding}");
            }
        }
    }

    #[test]
    fn the_install_command_checks_its_arguments() {
        use std::ffi::OsString;
        let args = |list: &[&str]| list.iter().map(OsString::from).collect::<Vec<_>>();
        assert_eq!(run_arguments(&[]), None);
        assert_eq!(run_arguments(&args(&["app"])), None);
        assert_eq!(
            run_arguments(&args(&["app", "--other", "a", "b", "c"])),
            None
        );
        assert_eq!(
            run_arguments(&args(&["app", "install-engine", "a", "b", "c"])),
            None
        );
        // Too few or too many arguments are an error, not a panic and not an install.
        let root = tempfile::tempdir().unwrap();
        let manifest = payload(root.path(), false, false);
        let destination = root.path().join("engine");
        let m = manifest.to_string_lossy().into_owned();
        let r = root.path().to_string_lossy().into_owned();
        let d = destination.to_string_lossy().into_owned();
        for list in [
            vec!["app", "--install-engine"],
            vec!["app", "--install-engine", &m],
            vec!["app", "--install-engine", &m, &r],
            vec!["app", "--install-engine", &m, &r, &d, "extra"],
        ] {
            assert_eq!(run_arguments(&args(&list)), Some(1), "{list:?}");
            assert!(!destination.exists(), "{list:?}");
        }
        // The complete command installs.
        assert_eq!(
            run_arguments(&args(&["app", "--install-engine", &m, &r, &d])),
            Some(0)
        );
        assert_eq!(
            fs::read(destination.join("localsr-worker.exe")).unwrap(),
            b"exe"
        );
    }
}
