use std::{
    fmt::Write as _,
    fs::File,
    io::Read,
    path::{Component, Path},
};

use sha2::{Digest, Sha256};

use crate::{
    error::{AppError, AppResult},
    paths::AppPaths,
    types::CatalogManifest,
};

const CATALOG_JSON: &str = include_str!("../resources/model-catalog.json");

pub fn load_catalog(paths: &AppPaths) -> AppResult<CatalogManifest> {
    let mut catalog: CatalogManifest = serde_json::from_str(CATALOG_JSON)?;
    refresh_install_state(&mut catalog, paths);
    Ok(catalog)
}

pub fn refresh_install_state(catalog: &mut CatalogManifest, paths: &AppPaths) {
    for model in &mut catalog.models {
        let path = paths.model_root.join(&model.filename);
        model.installed = verified_file(&path, model.size_bytes, &model.sha256);
        model.installed_path = model.installed.then(|| path.to_string_lossy().into_owned());
    }
    for model in &mut catalog.video_models {
        model.total_size_bytes = model.files.iter().map(|file| file.size_bytes).sum();
        let bundle = paths.model_root.join(&model.family);
        model.installed = model
            .files
            .iter()
            .all(|file| verified_file(&bundle.join(&file.filename), file.size_bytes, &file.sha256));
        model.installed_path = model
            .installed
            .then(|| bundle.to_string_lossy().into_owned());
    }
}

pub fn verified_file(path: &Path, expected_size: u64, expected_sha256: &str) -> bool {
    let Ok(metadata) = path.metadata() else {
        return false;
    };
    if !metadata.is_file() || metadata.len() != expected_size {
        return false;
    }
    sha256_file(path)
        .map(|digest| digest.eq_ignore_ascii_case(expected_sha256))
        .unwrap_or(false)
}

pub fn sha256_file(path: &Path) -> AppResult<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    let digest = digest.finalize();
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        write!(&mut encoded, "{byte:02x}").expect("writing to a String cannot fail");
    }
    Ok(encoded)
}

pub fn safe_model_filename(filename: &str) -> AppResult<&str> {
    let candidate = Path::new(filename);
    let mut components = candidate.components();
    let safe = matches!(components.next(), Some(Component::Normal(_)))
        && components.next().is_none()
        && candidate.file_name().and_then(|name| name.to_str()) == Some(filename);
    if !safe {
        return Err(AppError::Validation(
            "model catalog contains an unsafe filename".into(),
        ));
    }
    Ok(filename)
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    use super::*;

    #[test]
    fn catalog_parses_and_has_unique_ids() {
        let catalog: CatalogManifest = serde_json::from_str(CATALOG_JSON).unwrap();
        let mut ids = std::collections::HashSet::new();
        for id in catalog
            .models
            .iter()
            .map(|model| &model.model_id)
            .chain(catalog.video_models.iter().map(|model| &model.model_id))
        {
            assert!(ids.insert(id), "duplicate catalog id: {id}");
        }
    }

    #[test]
    fn checksum_and_size_are_both_required() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("model.safetensors");
        File::create(&path).unwrap().write_all(b"trusted").unwrap();
        assert!(verified_file(
            &path,
            7,
            "a9a089195c68d2adeee23beaa2c3a93b1d4cdf09046e7a9e520b3b166dff3e6a"
        ));
        assert!(!verified_file(
            &path,
            8,
            "a9a089195c68d2adeee23beaa2c3a93b1d4cdf09046e7a9e520b3b166dff3e6a"
        ));
    }

    #[test]
    fn rejects_catalog_path_traversal() {
        assert!(safe_model_filename("../model.pth").is_err());
        assert!(safe_model_filename("..").is_err());
        assert!(safe_model_filename(".").is_err());
        assert!(safe_model_filename("").is_err());
        assert_eq!(safe_model_filename("model.pth").unwrap(), "model.pth");
    }
}
