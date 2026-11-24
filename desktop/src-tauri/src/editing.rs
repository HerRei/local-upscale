//! Editing bundles share the normal verified downloader and single worker queue.
use std::{
    fs::File,
    io::{BufRead, BufReader, Read},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::mpsc,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};

use crate::{
    error::AppResult,
    paths::AppPaths,
    types::{CustomEditModel, ModelFile},
};

const EDIT_CATALOG: &str = include_str!("../../../src/localsr/core/edit_catalog.json");
pub const CUSTOM_EDIT_MODEL_ID: &str = "__custom__";
const GIB: u64 = 1024 * 1024 * 1024;
const MAC_TIERS: &[u64] = &[16, 24, 32, 36, 48, 64, 96, 128];
const GPU_TIERS: &[u64] = &[8, 12, 16, 20, 24, 32, 48];

#[derive(Clone, Copy, Debug)]
pub struct EditFamilySpec {
    pub id: &'static str,
    pub label: &'static str,
    pub needs_vision: bool,
    pub default_steps: u32,
    pub cfg_scale: f64,
    pub expected_sd_version: &'static str,
}

/// Families the pinned stable-diffusion.cpp revision supports for custom edit models.
pub const CUSTOM_EDIT_FAMILIES: &[EditFamilySpec] = &[
    EditFamilySpec {
        id: "qwen-image-edit-2511",
        label: "Qwen Image Edit 2511",
        needs_vision: true,
        default_steps: 40,
        cfg_scale: 2.5,
        expected_sd_version: "Qwen Image",
    },
    EditFamilySpec {
        id: "qwen-image-2.1",
        label: "Qwen Image 2.1",
        needs_vision: true,
        default_steps: 40,
        cfg_scale: 6.0,
        expected_sd_version: "Qwen Image 2.1",
    },
    EditFamilySpec {
        id: "flux2-klein",
        label: "FLUX.2 klein",
        needs_vision: false,
        default_steps: 4,
        cfg_scale: 1.0,
        expected_sd_version: "Flux.2 klein",
    },
    EditFamilySpec {
        id: "mage-flow-edit-turbo",
        label: "Mage-Flow-Edit-Turbo",
        needs_vision: true,
        default_steps: 4,
        cfg_scale: 1.0,
        expected_sd_version: "Mage Flow",
    },
];

pub fn find_family_spec(id: &str) -> Option<&'static EditFamilySpec> {
    CUSTOM_EDIT_FAMILIES.iter().find(|spec| spec.id == id)
}

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
    #[serde(default)]
    pub author: String,
    #[serde(default)]
    pub license_name: String,
    #[serde(default)]
    pub license_url: String,
    #[serde(default)]
    pub source_url: String,
    #[serde(default)]
    pub commercial_use_allowed: Option<bool>,
    #[serde(default)]
    pub attribution_required: bool,
    #[serde(default)]
    pub terms_acceptance_required: bool,
    #[serde(default)]
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
    #[serde(default)]
    pub custom_model: Option<EditModel>,
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

#[cfg(test)]
pub fn catalog(paths: &AppPaths) -> AppResult<Vec<EditModel>> {
    catalog_in(&paths.model_root)
}

pub fn catalog_in(model_root: &Path) -> AppResult<Vec<EditModel>> {
    let mut models: Vec<EditModel> = serde_json::from_str(EDIT_CATALOG)?;
    let root_present = model_root.is_dir();
    for model in &mut models {
        model.total_size_bytes = model.files.iter().map(|file| file.size_bytes).sum();
        model.installed = root_present
            && model.files.iter().all(|file| {
                model_root
                    .join(&model.family)
                    .join(&file.filename)
                    .metadata()
                    .is_ok_and(|info| info.is_file() && info.len() == file.size_bytes)
            });
    }
    Ok(models)
}

/// The name of an editing bundle, for labels of recorded Edit jobs.
pub fn model_name(model_id: &str) -> Option<String> {
    if model_id == CUSTOM_EDIT_MODEL_ID {
        return Some("Your edit model".into());
    }
    serde_json::from_str::<Vec<EditModel>>(EDIT_CATALOG)
        .ok()?
        .into_iter()
        .find(|model| model.model_id == model_id)
        .map(|model| model.name)
}

fn activation_memory(unified: bool, limit: u64) -> u64 {
    let base = if unified { 1.5 } else { 1.0 } * GIB as f64;
    let scale = (limit as f64 / 512.0).powi(2);
    (base * scale) as u64
}

fn reserve_for(total_ram: u64, unified: bool) -> u64 {
    let floor = (if unified { 2 } else { 4 }) * GIB;
    floor.max((total_ram as f64 * 0.10) as u64)
}

pub fn estimate_edit_memory(files: &[ModelFile], unified: bool, limit: u64) -> u64 {
    let size = |role: &str| -> u64 {
        files
            .iter()
            .filter(|file| file.role == role)
            .map(|file| file.size_bytes)
            .sum()
    };
    let conditioning = size("text_encoder") + size("vision");
    let sampling = size("vae")
        + activation_memory(unified, limit)
        + if unified { size("diffusion") } else { 0 };
    ((conditioning.max(sampling) as f64) * 1.1) as u64
}

pub fn custom_memory_tiers(files: &[ModelFile]) -> (u64, u64) {
    let size = |role: &str| -> u64 {
        files
            .iter()
            .filter(|file| file.role == role)
            .map(|file| file.size_bytes)
            .sum()
    };
    let diffusion = size("diffusion");
    let vae = size("vae");
    let needed = estimate_edit_memory(files, true, 512);
    let sampling = diffusion + vae + activation_memory(true, 512);
    let min_unified = MAC_TIERS
        .iter()
        .copied()
        .find(|&tier| {
            let bytes = tier * GIB;
            needed + reserve_for(bytes, true) <= (bytes as f64 * 0.70) as u64
                && sampling <= (bytes as f64 * 0.60) as u64
        })
        .unwrap_or_else(|| {
            let a = ((needed + 2 * GIB) as f64 / 0.70 / GIB as f64).ceil() as u64;
            let b = (sampling as f64 / 0.60 / GIB as f64).ceil() as u64;
            let c = (needed as f64 / 0.60 / GIB as f64).ceil() as u64;
            MAC_TIERS[MAC_TIERS.len() - 1].max(a).max(b).max(c)
        });
    let min_vram = GPU_TIERS
        .iter()
        .copied()
        .find(|&tier| diffusion <= (tier as f64 * GIB as f64 * 0.87) as u64)
        .unwrap_or_else(|| {
            let needed_gb = (diffusion as f64 / (0.87 * GIB as f64)).ceil() as u64;
            GPU_TIERS[GPU_TIERS.len() - 1].max(needed_gb)
        });
    (min_unified, min_vram)
}

fn custom_role_entries(
    spec: &EditFamilySpec,
    custom: &CustomEditModel,
) -> Vec<(&'static str, &'static str, String)> {
    let mut entries = vec![
        (
            "diffusion",
            "diffusion model",
            custom.diffusion_path.clone(),
        ),
        (
            "text_encoder",
            "text encoder",
            custom.text_encoder_path.clone(),
        ),
    ];
    if spec.needs_vision {
        entries.push(("vision", "vision projector", custom.vision_path.clone()));
    }
    entries.push(("vae", "VAE", custom.vae_path.clone()));
    entries
}

/// Build an EditModel view of the user's custom files using their real disk sizes.
pub fn custom_edit_model(custom: &CustomEditModel) -> Option<EditModel> {
    if custom.family.is_empty() && custom.non_empty_paths().next().is_none() {
        return None;
    }
    let spec = find_family_spec(&custom.family)?;
    let entries = custom_role_entries(spec, custom);
    let mut files = Vec::with_capacity(entries.len());
    let mut installed = true;
    for (role, _, path) in entries {
        let size_bytes = if path.is_empty() {
            installed = false;
            0
        } else {
            match std::fs::metadata(&path) {
                Ok(info) if info.is_file() && info.len() > 0 => info.len(),
                _ => {
                    installed = false;
                    0
                }
            }
        };
        files.push(ModelFile {
            role: role.into(),
            // Keep custom filenames out of snapshots and reports.
            filename: role.into(),
            size_bytes,
            sha256: String::new(),
            download_url: String::new(),
        });
    }
    let total_size_bytes = files.iter().map(|file| file.size_bytes).sum();
    let (min_unified_memory_gb, min_vram_gb) = custom_memory_tiers(&files);
    Some(EditModel {
        model_id: CUSTOM_EDIT_MODEL_ID.into(),
        name: "Your edit model".into(),
        family: spec.id.into(),
        quantization: "Custom".into(),
        files,
        min_unified_memory_gb,
        min_vram_gb,
        default_steps: spec.default_steps,
        cfg_scale: spec.cfg_scale,
        author: String::new(),
        license_name: "Your own file".into(),
        license_url: String::new(),
        source_url: String::new(),
        commercial_use_allowed: None,
        attribution_required: false,
        terms_acceptance_required: false,
        automated_download_allowed: false,
        installed,
        total_size_bytes,
    })
}

fn read_u32_le<R: Read>(reader: &mut R) -> std::io::Result<u32> {
    let mut buf = [0_u8; 4];
    reader.read_exact(&mut buf)?;
    Ok(u32::from_le_bytes(buf))
}

fn read_u64_le<R: Read>(reader: &mut R) -> std::io::Result<u64> {
    let mut buf = [0_u8; 8];
    reader.read_exact(&mut buf)?;
    Ok(u64::from_le_bytes(buf))
}

fn read_gguf_string<R: Read>(reader: &mut R) -> std::io::Result<String> {
    let len = read_u64_le(reader)?;
    if len > 65_536 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "string too long",
        ));
    }
    let mut buf = vec![0_u8; len as usize];
    reader.read_exact(&mut buf)?;
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

fn skip_gguf_value<R: Read>(reader: &mut R, value_type: u32, depth: u8) -> std::io::Result<()> {
    if depth > 4 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "nested array too deep",
        ));
    }
    match value_type {
        0 | 1 | 7 => {
            let mut b = [0_u8; 1];
            reader.read_exact(&mut b)
        }
        2 | 3 => {
            let mut b = [0_u8; 2];
            reader.read_exact(&mut b)
        }
        4..=6 => {
            let mut b = [0_u8; 4];
            reader.read_exact(&mut b)
        }
        10..=12 => {
            let mut b = [0_u8; 8];
            reader.read_exact(&mut b)
        }
        8 => {
            let _ = read_gguf_string(reader)?;
            Ok(())
        }
        9 => {
            let elem_type = read_u32_le(reader)?;
            let count = read_u64_le(reader)?;
            if count > 100_000 {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    "array too long",
                ));
            }
            for _ in 0..count {
                skip_gguf_value(reader, elem_type, depth + 1)?;
            }
            Ok(())
        }
        _ => Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "unknown gguf value type",
        )),
    }
}

/// Inspect GGUF metadata and tensor headers to identify the editing model family.
pub fn detect_gguf_edit_family(path: &Path) -> Option<&'static str> {
    let file = File::open(path).ok()?;
    detect_gguf_edit_family_reader(BufReader::new(file))
}

pub fn detect_gguf_edit_family_bytes(bytes: &[u8]) -> Option<&'static str> {
    detect_gguf_edit_family_reader(std::io::Cursor::new(bytes))
}

fn detect_gguf_edit_family_reader<R: Read>(mut reader: R) -> Option<&'static str> {
    let mut magic = [0_u8; 4];
    reader.read_exact(&mut magic).ok()?;
    if &magic != b"GGUF" {
        return None;
    }
    let version = read_u32_le(&mut reader).ok()?;
    if !matches!(version, 2 | 3) {
        return None;
    }
    let tensor_count = read_u64_le(&mut reader).ok()?.min(16_384);
    let kv_count = read_u64_le(&mut reader).ok()?.min(4_096);
    let mut meta_hint: Option<&'static str> = None;
    for _ in 0..kv_count {
        let key = read_gguf_string(&mut reader).ok()?;
        let value_type = read_u32_le(&mut reader).ok()?;
        if value_type == 8
            && matches!(
                key.as_str(),
                "general.name" | "general.basename" | "general.architecture"
            )
        {
            let value = read_gguf_string(&mut reader).ok()?.to_lowercase();
            if value.contains("qwen-image-2.1")
                || value.contains("qwen_image_2.1")
                || value.contains("qwen_image_2_1")
            {
                meta_hint = Some("qwen-image-2.1");
            } else if value.contains("qwen-image-edit-2511")
                || value.contains("qwen_image_edit_2511")
            {
                meta_hint = Some("qwen-image-edit-2511");
            } else if value.contains("flux-2-klein")
                || value.contains("flux.2-klein")
                || value.contains("flux2-klein")
            {
                meta_hint = Some("flux2-klein");
            } else if value.contains("mage-flow") || value.contains("mage_flow") {
                meta_hint = Some("mage-flow-edit-turbo");
            }
        } else {
            skip_gguf_value(&mut reader, value_type, 0).ok()?;
        }
    }
    let mut has_qwen21 = false;
    let mut has_qwen = false;
    let mut has_block_11 = false;
    let mut has_block_12 = false;
    let mut has_flux2 = false;
    let mut has_flux2_dev = false;
    for _ in 0..tensor_count {
        let name = read_gguf_string(&mut reader).ok()?;
        let n_dims = read_u32_le(&mut reader).ok()?;
        if n_dims > 8 {
            return meta_hint;
        }
        for _ in 0..n_dims {
            let _ = read_u64_le(&mut reader).ok()?;
        }
        let _tensor_type = read_u32_le(&mut reader).ok()?;
        let _offset = read_u64_le(&mut reader).ok()?;
        if name == "model.diffusion_model.txt_in.text_norm.weight"
            || name == "txt_in.text_norm.weight"
        {
            has_qwen21 = true;
        } else if name.contains("transformer_blocks.0.img_mod.1.weight") {
            has_qwen = true;
        } else if name.contains("transformer_blocks.11.") {
            has_block_11 = true;
        } else if name.contains("transformer_blocks.12.") {
            has_block_12 = true;
        } else if name.contains("double_stream_modulation_img.lin.weight") {
            has_flux2 = true;
        } else if name.contains("single_blocks.47.") {
            has_flux2_dev = true;
        }
    }
    if has_qwen21 {
        Some("qwen-image-2.1")
    } else if has_qwen && has_block_11 && !has_block_12 {
        Some("mage-flow-edit-turbo")
    } else if has_qwen {
        Some("qwen-image-edit-2511")
    } else if has_flux2 && !has_flux2_dev {
        Some("flux2-klein")
    } else {
        meta_hint
    }
}

fn sanitize_load_error(lines: &[String], custom: &CustomEditModel) -> String {
    let mut detail = lines
        .iter()
        .filter(|line| {
            let lower = line.to_ascii_lowercase();
            lower.contains("[error") || lower.contains("failed") || lower.contains("invalid")
        })
        .cloned()
        .collect::<Vec<_>>();
    if detail.is_empty() {
        detail = lines.iter().rev().take(3).cloned().collect();
        detail.reverse();
    }
    let mut joined = detail.join(" ");
    for (path, label) in [
        (&custom.diffusion_path, "the diffusion model"),
        (&custom.text_encoder_path, "the text encoder"),
        (&custom.vision_path, "the vision projector"),
        (&custom.vae_path, "the VAE"),
    ] {
        if !path.is_empty() {
            joined = joined.replace(path, label);
            if let Some(name) = Path::new(path).file_name().and_then(|n| n.to_str()) {
                if !name.is_empty() {
                    joined = joined.replace(name, label);
                }
            }
        }
    }
    let cleaned = crate::crash_reports::redact(&joined).trim().to_owned();
    if cleaned.is_empty() {
        "sd-cli could not load these model files. Check that each file matches the chosen family."
            .into()
    } else {
        format!("sd-cli could not load these model files: {cleaned}")
    }
}

/// Run a quick metadata/tensor load check with sd-cli before saving a custom edit model.
pub fn verify_custom_edit_load(runtime: &Path, custom: &CustomEditModel) -> Result<(), String> {
    let spec = find_family_spec(&custom.family)
        .ok_or_else(|| "Choose a supported editing model family.".to_owned())?;
    for (_, label, path) in custom_role_entries(spec, custom) {
        if path.trim().is_empty() {
            return Err(format!("Choose the {label} file first."));
        }
        let meta = std::fs::metadata(&path)
            .map_err(|_| format!("The chosen {label} file is unavailable."))?;
        if !meta.is_file() || meta.len() == 0 {
            return Err(format!("The chosen {label} file is invalid or empty."));
        }
    }

    let mut command = Command::new(runtime);
    command.args([
        "-M",
        "img_gen",
        "-v",
        "--diffusion-model",
        &custom.diffusion_path,
        "--llm",
        &custom.text_encoder_path,
    ]);
    if spec.needs_vision {
        command.args(["--llm_vision", &custom.vision_path]);
    }
    command.args([
        "--vae",
        &custom.vae_path,
        "-p",
        "check",
        "--steps",
        "1",
        "-W",
        "32",
        "-H",
        "32",
        "--offload-to-cpu",
        "--mmap",
        "--disable-prefetch",
        "--backend",
        "cpu",
    ]);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }

    let mut child = command
        .spawn()
        .map_err(|error| format!("Could not start sd-cli: {error}"))?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let (tx, rx) = mpsc::channel::<String>();
    let spawn_reader = |stream: Option<Box<dyn Read + Send>>, sender: mpsc::Sender<String>| {
        if let Some(stream) = stream {
            std::thread::spawn(move || {
                let reader = BufReader::new(stream);
                for line in reader.lines().map_while(Result::ok) {
                    if sender.send(line).is_err() {
                        break;
                    }
                }
            });
        }
    };
    spawn_reader(
        stdout.map(|s| Box::new(s) as Box<dyn Read + Send>),
        tx.clone(),
    );
    spawn_reader(stderr.map(|s| Box::new(s) as Box<dyn Read + Send>), tx);

    let deadline = Instant::now() + Duration::from_secs(30);
    let mut lines = Vec::new();
    let mut detected_version: Option<String> = None;
    let mut validated = false;
    let mut timed_out = false;
    loop {
        let now = Instant::now();
        if now >= deadline {
            timed_out = true;
            let _ = child.kill();
            break;
        }
        match rx.recv_timeout(deadline - now) {
            Ok(line) => {
                if let Some((_, rest)) = line.split_once("Version:") {
                    let version = rest.trim();
                    if !version.is_empty() {
                        detected_version = Some(version.to_owned());
                    }
                }
                if line.contains("model metadata validated")
                    || line.contains("total params memory size =")
                {
                    validated = true;
                    let _ = child.kill();
                    break;
                }
                lines.push(line);
                if lines.len() > 80 {
                    lines.remove(0);
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                timed_out = true;
                let _ = child.kill();
                break;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    let status = child
        .wait()
        .map_err(|error| format!("Could not finish sd-cli check: {error}"))?;
    if timed_out {
        return Err("Checking the editing model with sd-cli timed out.".into());
    }
    if let Some(ref version) = detected_version {
        if !version.eq_ignore_ascii_case(spec.expected_sd_version) {
            return Err(format!(
                "The diffusion model is {version}, which does not match {}. Choose the matching model family.",
                spec.label
            ));
        }
    }
    if validated || status.success() {
        return Ok(());
    }
    Err(sanitize_load_error(&lines, custom))
}

/// Delete a bundle's files, keeping components that another installed bundle
/// of the same family still uses. Returns false for an unknown model.
#[cfg(test)]
pub fn remove_bundle(paths: &AppPaths, model_id: &str) -> AppResult<bool> {
    remove_bundle_in(&paths.model_root, model_id)
}

pub fn remove_bundle_in(model_root: &Path, model_id: &str) -> AppResult<bool> {
    let models = catalog_in(model_root)?;
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
    let directory = model_root.join(&model.family);
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

fn resolve_runtime_path(
    explicit: Option<&Path>,
    engine_root: Option<&Path>,
    development: Option<&Path>,
    available: &[PathBuf],
    name: &str,
) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(path) = explicit {
        candidates.push(path.to_path_buf());
    }
    if let Some(root) = engine_root {
        candidates.push(root.join("_internal/edit").join(name));
        candidates.push(root.join("edit").join(name));
    }
    if let Some(root) = development {
        candidates.push(root.join(name));
    }
    candidates.into_iter().find(|path| available.contains(path))
}

pub fn runtime_path(paths: &AppPaths, engine_root: Option<&Path>) -> String {
    let name = if cfg!(windows) {
        "sd-cli.exe"
    } else {
        "sd-cli"
    };
    let explicit = std::env::var_os("LOCALSR_EDIT_RUNTIME").map(PathBuf::from);
    #[cfg(debug_assertions)]
    let development = Some(paths.repo_root.join(".cache/edit-runtime"));
    #[cfg(not(debug_assertions))]
    let development: Option<PathBuf> = {
        let _ = paths;
        None
    };
    let mut available = Vec::new();
    if let Some(path) = explicit.as_ref().filter(|path| path.is_file()) {
        available.push(path.clone());
    }
    if let Some(root) = engine_root {
        for directory in ["_internal/edit", "edit"] {
            let path = root.join(directory).join(name);
            if path.is_file() {
                available.push(path);
            }
        }
    }
    if let Some(root) = development.as_ref() {
        let path = root.join(name);
        if path.is_file() {
            available.push(path);
        }
    }
    resolve_runtime_path(
        explicit.as_deref(),
        engine_root,
        development.as_deref(),
        &available,
        name,
    )
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
    #[test]
    fn runtime_follows_engine_and_explicit_choice_wins() {
        use super::*;
        let cpu = Path::new("/engines/cpu");
        let cuda = Path::new("/engines/cuda");
        let old = cpu.join("_internal/edit/sd-cli");
        let new = cuda.join("_internal/edit/sd-cli");
        let chosen = PathBuf::from("/chosen/sd-cli");
        let available = vec![old.clone(), new.clone(), chosen.clone()];
        assert_eq!(
            resolve_runtime_path(None, Some(cpu), None, &available, "sd-cli"),
            Some(old.clone())
        );
        assert_eq!(
            resolve_runtime_path(None, Some(cuda), None, &available, "sd-cli"),
            Some(new.clone())
        );
        assert_eq!(
            resolve_runtime_path(None, Some(cuda), None, &[old], "sd-cli"),
            None
        );
        assert_eq!(
            resolve_runtime_path(Some(&chosen), Some(cuda), None, &available, "sd-cli"),
            Some(chosen)
        );
    }

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
    fn recorded_edit_jobs_are_labelled_with_the_bundle_name() {
        let models: Vec<EditModel> = serde_json::from_str(EDIT_CATALOG).unwrap();
        assert_eq!(
            model_name(&models[0].model_id).as_deref(),
            Some(models[0].name.as_str())
        );
        assert_eq!(model_name("no-such-bundle"), None);
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
        assert_eq!(models.len(), 9);
        for model in models {
            // Qwen and Mage-Flow bundles carry a vision projector; FLUX.2 klein edits without one.
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
            assert!(matches!(model.license_name.as_str(), "Apache-2.0" | "MIT"));
            assert!(!model.author.is_empty());
            assert!(model.source_url.starts_with("https://"));
            assert!(model.license_url.starts_with("https://"));
            assert_eq!(model.commercial_use_allowed, Some(true));
            assert_eq!(model.attribution_required, model.license_name != "MIT");
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

    fn write_gguf_header(path: &Path, tensor_names: &[&str], model_name_meta: Option<&str>) {
        use std::io::Write;
        let mut f = File::create(path).unwrap();
        f.write_all(b"GGUF").unwrap();
        f.write_all(&3_u32.to_le_bytes()).unwrap();
        f.write_all(&(tensor_names.len() as u64).to_le_bytes())
            .unwrap();
        let kv_count: u64 = u64::from(model_name_meta.is_some());
        f.write_all(&kv_count.to_le_bytes()).unwrap();
        if let Some(name) = model_name_meta {
            let key = b"general.name";
            f.write_all(&(key.len() as u64).to_le_bytes()).unwrap();
            f.write_all(key).unwrap();
            f.write_all(&8_u32.to_le_bytes()).unwrap();
            f.write_all(&(name.len() as u64).to_le_bytes()).unwrap();
            f.write_all(name.as_bytes()).unwrap();
        }
        for name in tensor_names {
            f.write_all(&(name.len() as u64).to_le_bytes()).unwrap();
            f.write_all(name.as_bytes()).unwrap();
            f.write_all(&1_u32.to_le_bytes()).unwrap();
            f.write_all(&4_u64.to_le_bytes()).unwrap();
            f.write_all(&0_u32.to_le_bytes()).unwrap();
            f.write_all(&0_u64.to_le_bytes()).unwrap();
        }
    }

    #[test]
    fn custom_edit_model_uses_real_file_sizes_and_never_exposes_paths() {
        let dir = tempfile::tempdir().unwrap();
        let diff = dir.path().join("my-secret-qwen21.gguf");
        let te = dir.path().join("my-secret-te.gguf");
        let vis = dir.path().join("my-secret-mmproj.gguf");
        let vae = dir.path().join("my-secret-vae.safetensors");
        for (path, size) in [
            (&diff, 14 * GIB),
            (&te, 5 * GIB),
            (&vis, GIB),
            (&vae, 256 * 1024 * 1024),
        ] {
            File::create(path).unwrap().set_len(size).unwrap();
        }
        let custom = CustomEditModel {
            family: "qwen-image-2.1".into(),
            diffusion_path: diff.to_string_lossy().into_owned(),
            text_encoder_path: te.to_string_lossy().into_owned(),
            vision_path: vis.to_string_lossy().into_owned(),
            vae_path: vae.to_string_lossy().into_owned(),
        };
        let model = custom_edit_model(&custom).unwrap();
        assert_eq!(model.model_id, CUSTOM_EDIT_MODEL_ID);
        assert_eq!(model.default_steps, 40);
        assert_eq!(model.cfg_scale, 6.0);
        assert!(model.installed);
        assert_eq!(model.files.len(), 4);
        assert_eq!(model.total_size_bytes, 20 * GIB + 256 * 1024 * 1024);
        assert!(model.min_unified_memory_gb >= 32);
        assert!(model.min_vram_gb >= 16);
        let serialized = serde_json::to_string(&model).unwrap();
        assert!(!serialized.contains("my-secret"));

        // Removing a file marks the custom model incomplete without crashing.
        std::fs::remove_file(&vae).unwrap();
        assert!(!custom_edit_model(&custom).unwrap().installed);

        // FLUX.2 klein uses 3 roles (no vision projector) and defaults to 4 steps / CFG 1.0.
        let klein = custom_edit_model(&CustomEditModel {
            family: "flux2-klein".into(),
            diffusion_path: diff.to_string_lossy().into_owned(),
            text_encoder_path: te.to_string_lossy().into_owned(),
            vision_path: String::new(),
            vae_path: vis.to_string_lossy().into_owned(),
        })
        .unwrap();
        assert_eq!(klein.files.len(), 3);
        assert_eq!(klein.default_steps, 4);
        assert_eq!(klein.cfg_scale, 1.0);

        let mage = custom_edit_model(&CustomEditModel {
            family: "mage-flow-edit-turbo".into(),
            diffusion_path: diff.to_string_lossy().into_owned(),
            text_encoder_path: te.to_string_lossy().into_owned(),
            vision_path: vis.to_string_lossy().into_owned(),
            vae_path: vis.to_string_lossy().into_owned(),
        })
        .unwrap();
        assert_eq!(mage.files.len(), 4);
        assert_eq!(mage.default_steps, 4);
        assert_eq!(mage.cfg_scale, 1.0);
    }

    #[test]
    fn detects_edit_family_from_gguf_headers() {
        let dir = tempfile::tempdir().unwrap();
        let q21 = dir.path().join("q21.gguf");
        write_gguf_header(
            &q21,
            &["model.diffusion_model.txt_in.text_norm.weight"],
            None,
        );
        assert_eq!(detect_gguf_edit_family(&q21), Some("qwen-image-2.1"));

        let q2511 = dir.path().join("q2511.gguf");
        write_gguf_header(
            &q2511,
            &["model.diffusion_model.transformer_blocks.0.img_mod.1.weight"],
            None,
        );
        assert_eq!(
            detect_gguf_edit_family(&q2511),
            Some("qwen-image-edit-2511")
        );

        let mage = dir.path().join("mage.gguf");
        write_gguf_header(
            &mage,
            &[
                "model.diffusion_model.transformer_blocks.0.img_mod.1.weight",
                "model.diffusion_model.transformer_blocks.11.img_mod.1.weight",
            ],
            None,
        );
        assert_eq!(detect_gguf_edit_family(&mage), Some("mage-flow-edit-turbo"));

        let klein = dir.path().join("klein.gguf");
        write_gguf_header(
            &klein,
            &["model.diffusion_model.double_stream_modulation_img.lin.weight"],
            None,
        );
        assert_eq!(detect_gguf_edit_family(&klein), Some("flux2-klein"));

        let not_gguf = dir.path().join("other.safetensors");
        std::fs::write(&not_gguf, b"not gguf").unwrap();
        assert_eq!(detect_gguf_edit_family(&not_gguf), None);
    }

    #[cfg(unix)]
    #[test]
    fn load_check_validates_with_sd_cli_and_refuses_mismatch_or_error_without_leaking_names() {
        use std::os::unix::fs::PermissionsExt;

        let dir = tempfile::tempdir().unwrap();
        let diff = dir.path().join("private-qwen21.gguf");
        let te = dir.path().join("private-te.gguf");
        let vis = dir.path().join("private-mmproj.gguf");
        let vae = dir.path().join("private-vae.safetensors");
        for path in [&diff, &te, &vis, &vae] {
            std::fs::write(path, b"weights").unwrap();
        }
        let custom = CustomEditModel {
            family: "qwen-image-2.1".into(),
            diffusion_path: diff.to_string_lossy().into_owned(),
            text_encoder_path: te.to_string_lossy().into_owned(),
            vision_path: vis.to_string_lossy().into_owned(),
            vae_path: vae.to_string_lossy().into_owned(),
        };

        let make_script = |name: &str, body: &str| {
            let script = dir.path().join(name);
            std::fs::write(&script, format!("#!/bin/sh\n{body}\n")).unwrap();
            let mut perms = std::fs::metadata(&script).unwrap().permissions();
            perms.set_mode(0o755);
            std::fs::set_permissions(&script, perms).unwrap();
            script
        };

        let ok_cli = make_script(
            "sd-ok",
            "echo '[INFO] Version: Qwen Image 2.1 '\necho '[VERBOSE] model metadata validated; weights will be prepared lazily'",
        );
        assert!(verify_custom_edit_load(&ok_cli, &custom).is_ok());

        let mismatch_cli = make_script(
            "sd-mismatch",
            "echo '[INFO] Version: Flux.2 klein '\necho '[VERBOSE] model metadata validated'",
        );
        let mismatch = verify_custom_edit_load(&mismatch_cli, &custom).unwrap_err();
        assert!(mismatch.contains("Flux.2 klein"));
        assert!(!mismatch.contains("private-"));

        let fail_cli = make_script(
            "sd-fail",
            &format!(
                "echo '[ERROR] loading tensors from \"{}\" failed' >&2\nexit 1",
                vae.display()
            ),
        );
        let err = verify_custom_edit_load(&fail_cli, &custom).unwrap_err();
        assert!(err.contains("the VAE"));
        assert!(!err.contains("private-"));
    }

    proptest::proptest! {
        #[test]
        fn gguf_family_parser_never_panics_on_arbitrary_bytes(data in proptest::collection::vec(proptest::arbitrary::any::<u8>(), 0..512)) {
            let _ = detect_gguf_edit_family_bytes(&data);
        }

        #[test]
        fn gguf_family_parser_handles_mutated_headers(
            tail in proptest::collection::vec(proptest::arbitrary::any::<u8>(), 0..256),
            version in 0_u32..6,
            tensors in 0_u64..16,
            kvs in 0_u64..16,
        ) {
            let mut bytes = Vec::with_capacity(24 + tail.len());
            bytes.extend_from_slice(b"GGUF");
            bytes.extend_from_slice(&version.to_le_bytes());
            bytes.extend_from_slice(&tensors.to_le_bytes());
            bytes.extend_from_slice(&kvs.to_le_bytes());
            bytes.extend_from_slice(&tail);
            let _ = detect_gguf_edit_family_bytes(&bytes);
        }
    }
}
