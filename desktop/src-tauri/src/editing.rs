//! Editing bundles share the normal verified downloader and single worker queue.
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

use crate::{error::AppResult, paths::AppPaths, types::ModelFile};

const EDIT_CATALOG: &str = include_str!("../../../src/localsr/core/edit_catalog.json");

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct EditModel {
    pub model_id: String,
    pub name: String,
    pub family: String,
    pub quantization: String,
    pub files: Vec<ModelFile>,
    pub min_unified_memory_gb: u64,
    pub min_vram_gb: u64,
    #[serde(default = "default_steps")]
    pub default_steps: u32,
    #[serde(default = "default_cfg_scale")]
    pub cfg_scale: f64,
    pub license_name: String,
    pub license_url: String,
    pub source_url: String,
    pub terms_acceptance_required: bool,
    pub automated_download_allowed: bool,
    #[serde(default)]
    pub installed: bool,
    #[serde(default)]
    pub total_size_bytes: u64,
}

fn default_steps() -> u32 {
    40
}

fn default_cfg_scale() -> f64 {
    2.5
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct EditSetup {
    pub models: Vec<EditModel>,
    pub runtime_path: String,
}

#[derive(Deserialize)]
pub struct StartEditInput {
    pub media_id: String,
    pub model_id: String,
    pub prompt: String,
    pub runtime_path: String,
    pub device: String,
    pub max_dimension: u32,
    pub steps: u32,
    pub seed: u32,
    pub accepted_terms: bool,
    pub output_directory: String,
}

pub fn catalog(paths: &AppPaths) -> AppResult<Vec<EditModel>> {
    let mut models: Vec<EditModel> = serde_json::from_str(EDIT_CATALOG)?;
    for model in &mut models {
        model.total_size_bytes = model.files.iter().map(|file| file.size_bytes).sum();
        model.installed = model.files.iter().all(|file| {
            paths
                .model_root
                .join(&model.family)
                .join(&file.filename)
                .metadata()
                .is_ok_and(|info| info.is_file() && info.len() == file.size_bytes)
        });
    }
    Ok(models)
}

/// Delete a bundle's files, keeping components that another installed bundle
/// of the same family still uses. Returns false for an unknown model.
pub fn remove_bundle(paths: &AppPaths, model_id: &str) -> AppResult<bool> {
    let models = catalog(paths)?;
    let Some(model) = models.iter().find(|model| model.model_id == model_id) else {
        return Ok(false);
    };
    let shared: Vec<&str> = models
        .iter()
        .filter(|other| {
            other.installed && other.family == model.family && other.model_id != model.model_id
        })
        .flat_map(|other| other.files.iter().map(|file| file.filename.as_str()))
        .collect();
    let directory = paths.model_root.join(&model.family);
    for file in &model.files {
        if shared.contains(&file.filename.as_str()) {
            continue;
        }
        let path = directory.join(&file.filename);
        if path.is_file() {
            std::fs::remove_file(&path)?;
        }
    }
    Ok(true)
}

pub fn runtime_path(app: &AppHandle, paths: &AppPaths) -> String {
    let name = if cfg!(windows) {
        "sd-cli.exe"
    } else {
        "sd-cli"
    };
    let mut candidates = Vec::<PathBuf>::new();
    if let Some(path) = std::env::var_os("LOCALSR_EDIT_RUNTIME") {
        candidates.push(path.into());
    }
    if let Some(engine) = crate::updates::active_engine(paths) {
        if let Some(root) = engine.parent() {
            candidates.push(root.join("_internal/edit").join(name));
            candidates.push(root.join("edit").join(name));
        }
    }
    if let Ok(root) = app.path().resource_dir() {
        candidates.push(root.join("engine/_internal/edit").join(name));
        candidates.push(root.join("engine/edit").join(name));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(root) = exe.parent() {
            candidates.push(root.join("engine/_internal/edit").join(name));
        }
    }
    #[cfg(debug_assertions)]
    candidates.push(paths.repo_root.join(".cache/edit-runtime").join(name));
    candidates
        .into_iter()
        .find(|path| path.is_file())
        .map(|path| path.to_string_lossy().into_owned())
        .unwrap_or_default()
}

pub fn validate(input: &StartEditInput, model: &EditModel) -> Result<(), String> {
    if input.prompt.trim().is_empty()
        || input.prompt.chars().count() > 4000
        || input.prompt.contains('\0')
    {
        return Err("Describe the edit in 1–4000 characters.".into());
    }
    if !matches!(input.max_dimension, 512 | 768 | 1024)
        || !(1..=60).contains(&input.steps)
        || input.seed > i32::MAX as u32
    {
        return Err("Invalid edit size, step count, or seed.".into());
    }
    if !(input.device == "mps" || input.device == "cuda" || input.device.starts_with("cuda:")) {
        return Err("Select Apple Silicon Metal or a CUDA/ROCm GPU for editing.".into());
    }
    if model.terms_acceptance_required && !input.accepted_terms {
        return Err("Review and accept the editing model's license first.".into());
    }
    if !model.installed {
        return Err("Download all components of the editing model first.".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn install(paths: &AppPaths, model: &EditModel) {
        let directory = paths.model_root.join(&model.family);
        std::fs::create_dir_all(&directory).unwrap();
        for file in &model.files {
            let handle = std::fs::File::create(directory.join(&file.filename)).unwrap();
            handle.set_len(file.size_bytes).unwrap();
        }
    }

    #[test]
    fn removing_a_bundle_keeps_components_shared_with_an_installed_sibling() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        let models: Vec<EditModel> = serde_json::from_str(EDIT_CATALOG).unwrap();
        let (q2, q3) = (&models[0], &models[1]);
        assert_eq!(q2.family, q3.family);
        install(&paths, q2);
        install(&paths, q3);
        let installed = |id: &str| {
            catalog(&paths)
                .unwrap()
                .iter()
                .any(|m| m.model_id == id && m.installed)
        };
        assert!(installed(&q2.model_id) && installed(&q3.model_id));

        assert!(remove_bundle(&paths, &q2.model_id).unwrap());
        assert!(!installed(&q2.model_id));
        assert!(
            installed(&q3.model_id),
            "shared encoder and VAE must survive"
        );

        assert!(remove_bundle(&paths, &q3.model_id).unwrap());
        assert!(!installed(&q3.model_id));
        assert!(std::fs::read_dir(paths.model_root.join(&q3.family))
            .unwrap()
            .next()
            .is_none());
        assert!(!remove_bundle(&paths, "not-a-model").unwrap());
    }

    #[test]
    fn all_edit_bundles_pin_each_component_under_an_open_license() {
        let models: Vec<EditModel> = serde_json::from_str(EDIT_CATALOG).unwrap();
        assert_eq!(models.len(), 8);
        for model in models {
            // Qwen bundles carry a vision projector; FLUX.2 klein edits without one.
            assert_eq!(
                model.files.len(),
                if model.family.starts_with("flux2") {
                    3
                } else {
                    4
                }
            );
            assert!((1..=60).contains(&model.default_steps) && model.cfg_scale >= 1.0);
            for file in &model.files {
                assert_eq!(file.sha256.len(), 64);
                assert!(file.download_url.starts_with("https://huggingface.co/"));
                assert!(!file.download_url.contains("/resolve/main/"));
            }
            // Only openly licensed bundles are offered.
            assert_eq!(model.license_name, "Apache-2.0");
            assert!(!model.terms_acceptance_required);
        }
    }

    #[test]
    fn edit_validation_requires_a_complete_bundle_and_explicit_license_acceptance() {
        let mut models: Vec<EditModel> = serde_json::from_str(EDIT_CATALOG).unwrap();
        let model = &mut models[0];
        model.terms_acceptance_required = true;
        let mut input = StartEditInput {
            media_id: "image".into(),
            model_id: model.model_id.clone(),
            prompt: "Replace the background.".into(),
            runtime_path: "sd-cli".into(),
            device: "mps".into(),
            max_dimension: 512,
            steps: 40,
            seed: 42,
            accepted_terms: false,
            output_directory: String::new(),
        };
        assert!(validate(&input, model).unwrap_err().contains("license"));
        input.accepted_terms = true;
        assert!(validate(&input, model).unwrap_err().contains("Download"));
        model.installed = true;
        assert!(validate(&input, model).is_ok());
        input.prompt = " ".into();
        assert!(validate(&input, model).is_err());
        input.prompt = "Edit".into();
        input.device = "cpu".into();
        assert!(validate(&input, model).is_err());
    }
}
