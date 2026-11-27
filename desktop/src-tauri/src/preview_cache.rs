use std::{
    collections::HashSet,
    fs,
    io::Write,
    path::{Component, Path, PathBuf},
};

use base64::Engine;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{
    database::Database,
    error::{AppError, AppResult},
    state::{lock, AppState},
    types::RuntimeStatus,
};

const PREVIEW_EXTENSION: &str = ".jpg";
const ASSET_PREFIXES: [&str; 3] = [
    "asset://localhost/",
    "http://asset.localhost/",
    "https://asset.localhost/",
];

pub(crate) fn save_jpeg_base64(preview_root: &Path, encoded: &str) -> AppResult<String> {
    if encoded.is_empty() {
        return Ok(String::new());
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .map_err(|error| AppError::Validation(format!("Invalid preview image data: {error}")))?;
    save_jpeg_bytes(preview_root, &bytes)
}

pub(crate) fn save_jpeg_bytes(preview_root: &Path, bytes: &[u8]) -> AppResult<String> {
    if bytes.is_empty() {
        return Ok(String::new());
    }
    let root = ensure_real_directory(preview_root)?;
    let digest = Sha256::digest(bytes);
    let mut name = String::with_capacity(64 + PREVIEW_EXTENSION.len());
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(&mut name, "{byte:02x}");
    }
    name.push_str(PREVIEW_EXTENSION);
    let destination = root.join(&name);
    match fs::symlink_metadata(&destination) {
        Ok(meta) if meta.file_type().is_symlink() || !meta.is_file() => {
            return Err(AppError::Validation(format!(
                "Unsafe preview cache entry: {}",
                destination.display()
            )));
        }
        Ok(meta) if meta.len() == bytes.len() as u64 => {
            return asset_url_for_file(&destination);
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }

    let temporary = root.join(format!(".tmp-{}{PREVIEW_EXTENSION}", Uuid::new_v4()));
    let write_result = (|| -> AppResult<()> {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, &destination)?;
        Ok(())
    })();
    if write_result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    write_result?;
    asset_url_for_file(&destination)
}

/// Resolves a preview URL to a single `.jpg` file directly inside `preview_root`,
/// rejecting `data:` URLs, non-asset schemes, traversal, subdirectories, and symlinks.
pub(crate) fn confined_preview_path(preview_root: &Path, url: &str) -> Option<PathBuf> {
    if url.is_empty() || url.starts_with("data:") || url.contains(['?', '#', '\0']) {
        return None;
    }
    let rest = ASSET_PREFIXES
        .iter()
        .find_map(|prefix| url.strip_prefix(prefix))?;
    let decoded = decode_uri_component(rest)?;
    if decoded.contains('\0') {
        return None;
    }
    let candidate = Path::new(&decoded);
    if !candidate.is_absolute()
        || candidate
            .components()
            .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
    {
        return None;
    }
    let file_name = candidate.file_name()?.to_str()?;
    if file_name.is_empty()
        || file_name.starts_with('.')
        || !file_name.ends_with(PREVIEW_EXTENSION)
        || file_name.contains(['/', '\\'])
    {
        return None;
    }
    let root = canonical_preview_root(preview_root)?;
    let root_simplified = simplify_path(&root);
    let parent = candidate.parent()?;
    let parent_matches = simplify_path(parent) == root_simplified
        || fs::canonicalize(parent)
            .ok()
            .is_some_and(|p| simplify_path(&p) == root_simplified);
    if !parent_matches {
        return None;
    }
    let resolved = root.join(file_name);
    if let Ok(meta) = fs::symlink_metadata(&resolved) {
        if meta.file_type().is_symlink() || !meta.is_file() {
            return None;
        }
    }
    Some(resolved)
}

pub(crate) fn runtime_preview_urls(runtime: &RuntimeStatus) -> Vec<&str> {
    let mut urls = Vec::with_capacity(4 + runtime.compare.slots.len() + runtime.job_steps.len());
    urls.push(runtime.result_preview_data_url.as_str());
    urls.push(runtime.comparison_preview_data_url.as_str());
    urls.push(runtime.comparison_pair.showing.preview_data_url.as_str());
    urls.push(runtime.comparison_pair.against.preview_data_url.as_str());
    for slot in &runtime.compare.slots {
        urls.push(slot.preview_data_url.as_str());
    }
    for step in &runtime.job_steps {
        urls.push(step.preview_url.as_str());
    }
    urls
}

pub(crate) fn referenced_paths(
    preview_root: &Path,
    db: &Database,
    runtime: Option<&RuntimeStatus>,
) -> AppResult<HashSet<PathBuf>> {
    let mut referenced = HashSet::new();
    for (_, url) in db.list_media_previews()? {
        if let Some(path) = confined_preview_path(preview_root, &url) {
            referenced.insert(path);
        }
    }
    if let Some(runtime) = runtime {
        for url in runtime_preview_urls(runtime) {
            if let Some(path) = confined_preview_path(preview_root, url) {
                referenced.insert(path);
            }
        }
    }
    Ok(referenced)
}

pub(crate) fn remove_if_unreferenced(
    preview_root: &Path,
    db: &Database,
    runtime: Option<&RuntimeStatus>,
    urls: &[&str],
) -> AppResult<()> {
    if urls.is_empty() {
        return Ok(());
    }
    let referenced = referenced_paths(preview_root, db, runtime)?;
    for url in urls {
        if let Some(path) = confined_preview_path(preview_root, url) {
            if !referenced.contains(&path) {
                remove_regular_file(&path)?;
            }
        }
    }
    Ok(())
}

pub(crate) fn sweep_unreferenced(
    preview_root: &Path,
    db: &Database,
    runtime: Option<&RuntimeStatus>,
) -> AppResult<usize> {
    match fs::symlink_metadata(preview_root) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(0),
        Err(error) => return Err(error.into()),
        Ok(meta) if meta.file_type().is_symlink() || !meta.is_dir() => {
            return Err(AppError::Validation(format!(
                "Unsafe preview folder: {}",
                preview_root.display()
            )));
        }
        Ok(_) => {}
    }
    let root = fs::canonicalize(preview_root)?;
    let referenced = referenced_paths(&root, db, runtime)?;
    let mut removed = 0;
    for entry in fs::read_dir(&root)? {
        let entry = entry?;
        let path = root.join(entry.file_name());
        let meta = match fs::symlink_metadata(&path) {
            Ok(meta) => meta,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error.into()),
        };
        if meta.file_type().is_symlink() || !meta.is_file() {
            continue;
        }
        if !referenced.contains(&path) {
            remove_regular_file(&path)?;
            removed += 1;
        }
    }
    Ok(removed)
}

pub(crate) fn prune_missing_references(state: &AppState) -> AppResult<()> {
    let preview_root = &state.paths.preview_root;
    let database = lock(&state.database)?;
    let mut runtime = lock(&state.runtime)?;
    for (id, url) in database.list_media_previews()? {
        if let Some(path) = confined_preview_path(preview_root, &url) {
            if !path.is_file() {
                database.clear_media_preview(&id)?;
            }
        }
    }
    let clear_if_missing = |url: &mut String| {
        if let Some(path) = confined_preview_path(preview_root, url) {
            if !path.is_file() {
                url.clear();
                return true;
            }
        }
        false
    };
    clear_if_missing(&mut runtime.result_preview_data_url);
    clear_if_missing(&mut runtime.comparison_preview_data_url);
    clear_if_missing(&mut runtime.comparison_pair.showing.preview_data_url);
    clear_if_missing(&mut runtime.comparison_pair.against.preview_data_url);
    for step in &mut runtime.job_steps {
        clear_if_missing(&mut step.preview_url);
    }
    for slot in &mut runtime.compare.slots {
        if clear_if_missing(&mut slot.preview_data_url) {
            slot.status = crate::compare::CompareStatus::Error;
            slot.error = "Preview cache was cleared.".into();
        }
    }
    Ok(())
}

fn ensure_real_directory(dir: &Path) -> AppResult<PathBuf> {
    fs::create_dir_all(dir)?;
    let meta = fs::symlink_metadata(dir)?;
    if meta.file_type().is_symlink() || !meta.is_dir() {
        return Err(AppError::Validation(format!(
            "Unsafe preview folder: {}",
            dir.display()
        )));
    }
    Ok(fs::canonicalize(dir)?)
}

fn canonical_preview_root(preview_root: &Path) -> Option<PathBuf> {
    match fs::symlink_metadata(preview_root) {
        Ok(meta) if meta.file_type().is_symlink() || !meta.is_dir() => None,
        Ok(_) => fs::canonicalize(preview_root).ok(),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            Some(preview_root.to_path_buf())
        }
        Err(_) => None,
    }
}

fn remove_regular_file(path: &Path) -> AppResult<()> {
    match fs::symlink_metadata(path) {
        Ok(meta) if !meta.file_type().is_symlink() && meta.is_file() => {
            match fs::remove_file(path) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
                Err(error) => Err(error.into()),
            }
        }
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

fn simplify_path(path: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        if let Some(s) = path.to_str() {
            if let Some(stripped) = s.strip_prefix(r"\\?\") {
                if !stripped.starts_with(r"UNC\") {
                    return PathBuf::from(stripped);
                }
            }
        }
    }
    path.to_path_buf()
}

fn asset_url_for_file(path: &Path) -> AppResult<String> {
    let simplified = simplify_path(path);
    let raw = simplified
        .to_str()
        .ok_or_else(|| AppError::Validation("preview path is not valid UTF-8".into()))?;
    let origin = if cfg!(windows) || cfg!(target_os = "android") {
        "http://asset.localhost"
    } else {
        "asset://localhost"
    };
    Ok(format!("{origin}/{}", encode_uri_component(raw)))
}

fn is_unreserved_uri_component(byte: u8) -> bool {
    byte.is_ascii_alphanumeric()
        || matches!(
            byte,
            b'-' | b'_' | b'.' | b'!' | b'~' | b'*' | b'\'' | b'(' | b')'
        )
}

fn encode_uri_component(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    for byte in input.as_bytes() {
        if is_unreserved_uri_component(*byte) {
            out.push(*byte as char);
        } else {
            use std::fmt::Write as _;
            let _ = write!(&mut out, "%{byte:02X}");
        }
    }
    out
}

fn decode_uri_component(input: &str) -> Option<String> {
    let bytes = input.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            let hi = hex_nibble(*bytes.get(i + 1)?)?;
            let lo = hex_nibble(*bytes.get(i + 2)?)?;
            decoded.push((hi << 4) | lo);
            i += 3;
        } else {
            decoded.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(decoded).ok()
}

fn hex_nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saves_by_content_hash_and_deduplicates_identical_jpegs() {
        let dir = tempfile::tempdir().unwrap();
        let preview_root = dir.path().join("previews");
        let bytes_a = b"\xff\xd8\xff\xe0jpeg-a";
        let bytes_b = b"\xff\xd8\xff\xe0jpeg-b";

        let url_a1 = save_jpeg_bytes(&preview_root, bytes_a).unwrap();
        let url_a2 = save_jpeg_bytes(&preview_root, bytes_a).unwrap();
        let url_b = save_jpeg_bytes(&preview_root, bytes_b).unwrap();

        assert_eq!(url_a1, url_a2);
        assert_ne!(url_a1, url_b);
        let path_a = confined_preview_path(&preview_root, &url_a1).unwrap();
        let path_b = confined_preview_path(&preview_root, &url_b).unwrap();
        assert_eq!(fs::read(&path_a).unwrap(), bytes_a);
        assert_eq!(fs::read(&path_b).unwrap(), bytes_b);
        let files: Vec<_> = fs::read_dir(&preview_root).unwrap().collect();
        assert_eq!(files.len(), 2);
    }

    #[test]
    fn rejects_urls_outside_preview_folder_and_symlinks() {
        let dir = tempfile::tempdir().unwrap();
        let preview_root = dir.path().join("previews");
        fs::create_dir_all(&preview_root).unwrap();
        let valid_url = save_jpeg_bytes(&preview_root, b"valid-jpeg").unwrap();
        assert!(confined_preview_path(&preview_root, &valid_url).is_some());

        let outside = dir.path().join("secret.jpg");
        fs::write(&outside, b"secret").unwrap();
        let sub = preview_root.join("nested");
        fs::create_dir_all(&sub).unwrap();
        let nested_file = sub.join("a.jpg");
        fs::write(&nested_file, b"nested").unwrap();

        let root_str = fs::canonicalize(&preview_root)
            .unwrap()
            .to_string_lossy()
            .into_owned();
        let traversal = format!(
            "asset://localhost/{}",
            encode_uri_component(&format!("{root_str}/../secret.jpg"))
        );
        let outside_url = asset_url_for_file(&outside).unwrap();
        let nested_url = asset_url_for_file(&nested_file).unwrap();

        for rejected in [
            "",
            "data:image/jpeg;base64,abc",
            "/etc/passwd",
            "file:///etc/passwd",
            "http://evil.localhost/etc/passwd",
            "asset://localhost/%2Fetc%2Fpasswd",
            "http://asset.localhost/%2Fetc%2Fpasswd",
            &outside_url,
            &traversal,
            &nested_url,
            &format!("{valid_url}?foo=1"),
            &format!("{valid_url}#frag"),
            "asset://localhost/%2Ftmp%00bad.jpg",
        ] {
            assert!(
                confined_preview_path(&preview_root, rejected).is_none(),
                "expected URL to be rejected: {rejected}"
            );
        }

        #[cfg(unix)]
        {
            let link = preview_root.join("escape.jpg");
            std::os::unix::fs::symlink(&outside, &link).unwrap();
            let link_url = asset_url_for_file(&link).unwrap();
            assert!(confined_preview_path(&preview_root, &link_url).is_none());
        }
    }

    #[test]
    fn the_running_job_s_step_pictures_are_referenced() {
        let dir = tempfile::tempdir().unwrap();
        let preview_root = dir.path().join("previews");
        let db = Database::open(&dir.path().join("localsr.sqlite3")).unwrap();
        let step_url = save_jpeg_bytes(&preview_root, b"\xff\xd8step").unwrap();
        let other_url = save_jpeg_bytes(&preview_root, b"\xff\xd8other").unwrap();
        let mut runtime = RuntimeStatus::default();
        runtime.job_steps.push(crate::types::JobStep {
            preview_url: step_url.clone(),
            ..Default::default()
        });
        assert_eq!(
            sweep_unreferenced(&preview_root, &db, Some(&runtime)).unwrap(),
            1
        );
        assert!(confined_preview_path(&preview_root, &step_url).is_some_and(|p| p.is_file()));
        assert!(!confined_preview_path(&preview_root, &other_url).is_some_and(|p| p.is_file()));
        // Once the job ends, nothing holds the picture.
        let released = runtime.take_job_steps();
        assert_eq!(released, vec![step_url.clone()]);
        remove_if_unreferenced(&preview_root, &db, Some(&runtime), &[&step_url]).unwrap();
        assert!(!confined_preview_path(&preview_root, &step_url).is_some_and(|p| p.is_file()));
    }
}
