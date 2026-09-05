//! Install the existing frozen worker from verified, adjacent NSIS payloads.
//! This does not download packages or manage a Python environment.
use std::{
    collections::HashSet,
    fs::{self, File},
    io::{self, Read},
    path::{Component, Path},
};

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

fn safe_relative(path: &Path) -> bool {
    !path.as_os_str().is_empty()
        && path
            .components()
            .all(|part| matches!(part, Component::Normal(_)))
        && !path.to_string_lossy().contains(['\\', ':'])
}

struct PartsReader {
    files: std::vec::IntoIter<File>,
    current: Option<File>,
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

pub fn install(manifest_path: &Path, source: &Path, destination: &Path) -> io::Result<()> {
    let manifest: Manifest = serde_json::from_slice(&fs::read(manifest_path)?)
        .map_err(|error| invalid(error.to_string()))?;
    if manifest.schema_version != 1
        || manifest.format != "tar.gz.parts"
        || manifest.backend != "CUDA"
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
    if fs2::available_space(parent)? < manifest.unpacked_bytes + 16 * 1024 * 1024 {
        return Err(invalid("not enough free space to install the CUDA engine"));
    }
    let mut names = HashSet::new();
    let mut files = Vec::new();
    for part in &manifest.parts {
        let name = Path::new(&part.filename);
        if !safe_relative(name)
            || name.components().count() != 1
            || !names.insert(part.filename.clone())
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
        let mut file = File::open(source.join(name))?;
        if file.metadata()?.len() != part.size {
            return Err(invalid(format!("incomplete payload: {}", part.filename)));
        }
        let mut digest = Sha256::new();
        let mut buffer = vec![0u8; 1024 * 1024];
        loop {
            let count = file.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            digest.update(&buffer[..count]);
        }
        let actual: String = digest
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        if actual != part.sha256 {
            return Err(invalid(format!(
                "payload checksum mismatch: {}",
                part.filename
            )));
        }
        // Retain the same open file handles used for verification.
        use std::io::{Seek, SeekFrom};
        file.seek(SeekFrom::Start(0))?;
        files.push(file);
    }
    let staging = parent.join(format!("engine.installing-{}", std::process::id()));
    let backup = parent.join(format!("engine.previous-{}", std::process::id()));
    if backup.exists() {
        return Err(invalid("a previous engine installation requires recovery"));
    }
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
        for entry in archive.entries()? {
            let mut entry = entry?;
            let relative = entry.path()?.into_owned();
            if !safe_relative(&relative)
                || !entry.header().entry_type().is_file()
                || !paths.insert(relative.to_string_lossy().to_lowercase())
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
            if !entry.unpack_in(&staging)? {
                return Err(invalid("engine archive escaped its destination"));
            }
        }
        // Finish decoding to verify the gzip checksum, with a bounded padding allowance.
        let mut tail = archive.into_inner().take(1024 * 1024 + 1);
        if io::copy(&mut tail, &mut io::sink())? > 1024 * 1024 {
            return Err(invalid("excess trailing engine archive data"));
        }
        if count != manifest.file_count
            || bytes != manifest.unpacked_bytes
            || !staging.join("localsr-worker.exe").is_file()
        {
            return Err(invalid("engine archive is incomplete"));
        }
        if destination.exists() {
            fs::rename(destination, &backup)?;
        }
        if let Err(error) = fs::rename(&staging, destination) {
            if backup.exists() {
                fs::rename(&backup, destination)?;
            }
            return Err(error);
        }
        if backup.exists() {
            fs::remove_dir_all(&backup)?;
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
}
