use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

pub const PROTOCOL_VERSION: u32 = 1;
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CatalogManifest {
    pub schema_version: u32,
    pub catalog_revision: String,
    pub models: Vec<CatalogModel>,
    pub video_models: Vec<CatalogVideoModel>,
    // Catalog schema 3: optional frame-interpolation stages of a video job. They share
    // the bundle layout of video models but are never offered as video engines.
    #[serde(default)]
    pub interpolation_models: Vec<CatalogVideoModel>,
    // Optional speech audio denoising stages (ONNX bundles).
    #[serde(default)]
    pub audio_models: Vec<CatalogVideoModel>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CatalogModel {
    pub model_id: String,
    pub name: String,
    pub filename: String,
    pub description: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub download_url: String,
    pub architecture: String,
    pub native_scale: u32,
    pub purposes: Vec<String>,
    pub quality_tier: u32,
    pub speed_tier: u32,
    pub recommended_halo: u32,
    pub source_url: String,
    pub license_name: String,
    pub license_url: String,
    pub author: String,
    pub memory_factor: f64,
    pub time_factor: f64,
    pub speed_factor: f64,
    pub vram_estimate_mb: u64,
    pub pair_with: String,
    pub commercial_use_status: String,
    pub commercial_use_allowed: Option<bool>,
    // False: the library offers it, but no preset picks it on its own.
    #[serde(default = "preset_eligible_default")]
    pub preset_eligible: bool,
    pub automated_download_allowed: bool,
    pub redistribution_allowed: bool,
    pub attribution_required: bool,
    pub terms_acceptance_required: bool,
    pub engine_id: String,
    pub support_tier: String,
    // `support_tier` controls validation; `rights_status` describes checkpoint terms.
    #[serde(default)]
    pub rights_status: String,
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub stage: String,
    #[serde(default)]
    pub fixes: Vec<String>,
    #[serde(default)]
    pub content: Vec<String>,
    #[serde(default)]
    pub installed: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub installed_path: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ModelFile {
    pub role: String,
    pub filename: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub download_url: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CatalogVideoModel {
    pub model_id: String,
    pub name: String,
    pub description: String,
    pub family: String,
    pub engine_kind: String,
    pub files: Vec<ModelFile>,
    #[serde(default)]
    pub license_name: String,
    #[serde(default)]
    pub license_url: String,
    #[serde(default)]
    pub author: String,
    #[serde(default)]
    pub source_url: String,
    pub min_unified_memory_gb: u64,
    pub min_vram_gb: u64,
    pub temporal_window: u32,
    pub temporal_overlap: u32,
    #[serde(default)]
    pub summary: String,
    /// The library's dots for video models; 0 where a model is not rated.
    #[serde(default)]
    pub quality_tier: u32,
    #[serde(default)]
    pub speed_tier: u32,
    #[serde(default)]
    pub commercial_use_allowed: Option<bool>,
    #[serde(default)]
    pub automated_download_allowed: bool,
    #[serde(default)]
    pub redistribution_allowed: bool,
    #[serde(default)]
    pub attribution_required: bool,
    #[serde(default)]
    pub terms_acceptance_required: bool,
    pub engine_id: String,
    pub support_tier: String,
    #[serde(default)]
    pub total_size_bytes: u64,
    #[serde(default)]
    pub installed: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub installed_path: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MediaItem {
    pub id: String,
    pub path: String,
    pub name: String,
    pub kind: String,
    pub width: u32,
    pub height: u32,
    pub frame_count: u64,
    pub fps: f64,
    pub duration_seconds: f64,
    pub preview_data_url: String,
    pub probe_status: String,
    #[serde(default)]
    pub hdr_format: String,
    #[serde(default)]
    pub audio_warning: String,
    #[serde(default = "default_true")]
    pub has_audio: bool,
    pub error: String,
    pub selected: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct JobRecord {
    /// The primary model of the recorded request, and its catalog name.
    #[serde(default)]
    pub model_id: String,
    #[serde(default)]
    pub model_label: String,
    pub id: String,
    pub media_id: String,
    pub media_name: String,
    pub media_kind: String,
    pub status: String,
    pub progress: f64,
    pub output_path: String,
    pub error: String,
    pub created_at: i64,
    #[serde(default)]
    pub work_key: String,
    #[serde(default)]
    pub work_units: f64,
    #[serde(default)]
    pub elapsed_seconds: f64,
    /// What one work unit typically took in earlier runs of this workload; 0 until two
    /// comparable runs were measured.
    #[serde(default)]
    pub seconds_per_unit: f64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct DeviceInfo {
    pub id: String,
    #[serde(rename = "type")]
    pub device_type: String,
    pub name: String,
    pub total_memory: u64,
    pub free_memory: u64,
    pub supports_fp16: bool,
    #[serde(default)]
    pub is_integrated: bool,
    pub recommended_tile_sizes: Vec<u32>,
    // Apple GPU: supported models can also run through Core ML on the Neural Engine.
    #[serde(default)]
    pub neural_engine: bool,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct CapabilityInfo {
    pub system_ram_total: u64,
    pub system_ram_available: u64,
    pub system_memory_pressure_percent: f64,
    pub system_memory_pressure_level: String,
    pub system_compressed_memory: u64,
    pub system_swap_total: u64,
    pub system_swap_used: u64,
    pub devices: Vec<DeviceInfo>,
    /// Which programs open and write the formats LocalSR's runtime omits
    /// (system codecs, a found FFmpeg); the worker reports it, the UI reads it.
    #[serde(default)]
    pub media: serde_json::Value,
    /// A GPU this edition cannot use and the download that would (or the missing driver).
    #[serde(default)]
    pub gpu_suggestion: serde_json::Value,
    /// An Intel NPU the machine has but cannot use yet (its compiler is missing).
    #[serde(default)]
    pub npu_hint: serde_json::Value,
}

impl CapabilityInfo {
    pub fn detecting() -> Self {
        Self {
            system_memory_pressure_level: "unknown".into(),
            devices: vec![DeviceInfo {
                id: "cpu".into(),
                device_type: "cpu".into(),
                name: "CPU · detecting hardware".into(),
                recommended_tile_sizes: vec![64, 128, 192, 256],
                ..DeviceInfo::default()
            }],
            ..Self::default()
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct EngineInfo {
    pub protocol_version: u32,
    pub minimum_protocol_version: u32,
    pub engine_id: String,
    pub engine_version: String,
    pub features: Vec<String>,
    pub model_formats: Vec<String>,
    pub video_engines: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Recipe {
    pub id: String,
    pub name: String,
    pub task: String,
    #[serde(default)]
    pub video_task: String,
    pub model_id: String,
    pub output_scale: u32,
    pub tile_size: u32,
    pub halo: u32,
    pub precision: String,
    pub safe_memory: bool,
    #[serde(default)]
    pub video_model_id: String,
    #[serde(default)]
    pub custom_model_path: String,
    #[serde(default)]
    pub output_format: String,
    #[serde(default)]
    pub output_bit_depth: Option<u8>,
    #[serde(default)]
    pub preserve_metadata: Option<bool>,
    #[serde(default)]
    pub jpeg_quality: Option<u32>,
    #[serde(default)]
    pub deflicker: Option<bool>,
    #[serde(default)]
    pub deflicker_window: Option<u32>,
    #[serde(default)]
    pub video_container: String,
    #[serde(default)]
    pub video_codec: String,
    #[serde(default = "default_hdr_mode")]
    pub video_hdr_mode: String,
    #[serde(default)]
    pub video_target_resolution: u32,
    #[serde(default = "default_true")]
    pub video_low_memory: bool,
    #[serde(default)]
    pub video_crf: Option<u32>,
    #[serde(default)]
    pub enable_face_model: Option<bool>,
    #[serde(default)]
    pub face_fidelity: Option<u32>,
    #[serde(default)]
    pub stages: Vec<RecipeStage>,
    // Model-library state captured with the recipe so it re-applies the same
    // Quality/Content/Fix choices, not only the resolved checkpoints.
    #[serde(default)]
    pub quality: String,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub fixes: Vec<String>,
    // Empty in recipes saved before frame interpolation existed.
    #[serde(default)]
    pub video_interpolation: String,
    #[serde(default)]
    pub video_interpolation_order: String,
    #[serde(default)]
    pub video_clean_audio: bool,
    #[serde(default)]
    pub video_audio_model: String,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct RecipeStage {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fix: Option<String>,
    pub kind: String,
    #[serde(default)]
    pub model_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fidelity: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mask_path: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct FixStageSelection {
    pub fix: String,
    #[serde(default)]
    pub model_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mask_path: Option<String>,
}

/// A registry step the job adds by id beside its fixes (`steps.rs` `step_stage`).
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct StepStageSelection {
    pub step: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mask_path: Option<String>,
    /// The catalog model of a step that needs one; absent while none is chosen.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_id: Option<String>,
}

impl StepStageSelection {
    /// The chosen model id; an empty one is no choice.
    pub fn chosen_model(&self) -> Option<&str> {
        self.model_id.as_deref().filter(|id| !id.is_empty())
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct CustomEditModel {
    #[serde(default)]
    pub family: String,
    #[serde(default)]
    pub diffusion_path: String,
    #[serde(default)]
    pub text_encoder_path: String,
    #[serde(default)]
    pub vision_path: String,
    #[serde(default)]
    pub vae_path: String,
}

impl CustomEditModel {
    pub fn non_empty_paths(&self) -> impl Iterator<Item = &str> {
        [
            self.diffusion_path.as_str(),
            self.text_encoder_path.as_str(),
            self.vision_path.as_str(),
            self.vae_path.as_str(),
        ]
        .into_iter()
        .filter(|path| !path.is_empty())
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct CustomModel {
    pub path: String,
    pub name: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default)]
pub struct UiSettings {
    #[serde(
        default = "default_appearance",
        deserialize_with = "deserialize_appearance"
    )]
    pub appearance: String,
    pub colour_blind_palette: bool,
    #[serde(
        default = "default_contrast",
        deserialize_with = "deserialize_contrast"
    )]
    pub contrast: String,
    #[serde(
        default = "default_viewer_background",
        deserialize_with = "deserialize_viewer_background"
    )]
    pub viewer_background: String,
    #[serde(default = "default_accent", deserialize_with = "deserialize_accent")]
    pub accent: String,
    pub interface_scale: u32,
    pub batch_mode: bool,
    pub task: String,
    #[serde(default = "default_video_task")]
    pub video_task: String,
    pub selected_model_id: String,
    pub selected_video_model_id: String,
    pub fix_stages: Vec<FixStageSelection>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub step_stages: Vec<StepStageSelection>,
    pub custom_model_path: String,
    pub custom_models: Vec<CustomModel>,
    pub trusted_checkpoint_sha256: Vec<String>,
    pub output_scale: u32,
    pub output_directory: String,
    pub output_format: String,
    #[serde(default = "default_output_bit_depth")]
    pub output_bit_depth: u8,
    pub preserve_metadata: bool,
    pub jpeg_quality: u32,
    pub device_id: String,
    pub tile_size: u32,
    pub halo: u32,
    pub precision: String,
    pub safe_memory: bool,
    pub deflicker: bool,
    pub deflicker_window: u32,
    pub video_container: String,
    // Royalty-free AV1/VP9/FFV1 are bundled; H.264/HEVC use the user's FFmpeg.
    #[serde(default = "default_video_codec")]
    pub video_codec: String,
    // Set once the old AV1 default has moved to "auto"; false in older files.
    #[serde(default)]
    pub automatic_video_format: bool,
    // An FFmpeg executable the user installed; LocalSR never bundles one.
    #[serde(default)]
    pub external_ffmpeg_path: String,
    #[serde(default = "default_hdr_mode")]
    pub video_hdr_mode: String,
    #[serde(default)]
    pub video_target_resolution: u32,
    #[serde(default = "default_true")]
    pub video_low_memory: bool,
    pub video_crf: u32,
    // Optional frame interpolation: off, x2, x4, fps60 or fps120, before or after the upscale.
    #[serde(default = "default_interpolation")]
    pub video_interpolation: String,
    #[serde(default = "default_interpolation_order")]
    pub video_interpolation_order: String,
    #[serde(default)]
    pub video_clean_audio: bool,
    #[serde(default)]
    pub video_audio_model: String,
    // Apple silicon: run supported models through Core ML on the Neural Engine.
    #[serde(default = "default_true")]
    pub neural_engine: bool,
    // Direct builds send version, platform and channel after an update check. New installs
    // start with it on and confirm it in the first-launch setup; a saved choice is kept.
    pub anonymous_update_count: bool,
    pub completion_sound: bool,
    // Direct builds look for an update once at launch, silently.
    #[serde(default = "default_true")]
    pub check_updates_at_launch: bool,
    #[serde(default = "default_true")]
    pub privacy_choice_made: bool,
    // On for new installs (the first-launch setup shows it); settings files from before
    // crash reports existed keep it off, since their users never saw the choice.
    #[serde(default)]
    pub crash_reports: bool,
    #[serde(default)]
    pub skipped_update_version: String,
    pub enable_face_model: bool,
    pub face_fidelity: u32,
    pub enable_live_preview: bool,
    pub low_priority: bool,
    pub allow_unsafe_pickle_model: bool,
    // Model-library state: which recipe is active ("quick", "best", "custom"
    // or empty), what the image contains, which problems to fix first, and
    // the checkpoint each preset slot is pinned to ("upscale/photo/best").
    pub quality: String,
    pub content: String,
    pub fixes: Vec<String>,
    pub preset_pins: BTreeMap<String, String>,
    // Image editing: the chosen Qwen bundle, the longest output edge and the
    // sampler settings. The prompt itself is never persisted.
    #[serde(default)]
    pub edit_model_id: String,
    #[serde(default = "default_edit_dimension")]
    pub edit_max_dimension: u32,
    #[serde(default = "default_edit_steps")]
    pub edit_steps: u32,
    #[serde(default = "default_edit_seed")]
    pub edit_seed: u32,
    #[serde(default = "default_edit_placement")]
    pub edit_text_encoder: String,
    #[serde(default = "default_edit_placement")]
    pub edit_vae: String,
    #[serde(default)]
    pub edit_vram_limit_mb: u64,
    #[serde(default)]
    pub edit_allow_any_size: bool,
    #[serde(default)]
    pub custom_edit_model: CustomEditModel,
    pub model_directory: String,
    #[serde(default)]
    pub gpu_download_notice_dismissed: bool,
    #[serde(default)]
    pub linux_menu_prompt_dismissed: bool,
    // Menu bar / tray icon and per-file completion notification.
    #[serde(default = "default_true")]
    pub show_in_menu_bar: bool,
    #[serde(default = "default_true")]
    pub notify_on_finish: bool,
    // macOS: a template (black or white) icon like the system's; false shows the app icon.
    #[serde(default = "default_true")]
    pub menu_bar_icon_monochrome: bool,
    // Remove unused compiled model caches (older than 30 days or over 5 GB) on startup.
    #[serde(default = "default_true")]
    pub auto_cache_cleanup: bool,
    // Keep the computer from going to idle sleep while jobs are queued or running.
    #[serde(default = "default_true")]
    pub keep_awake: bool,
    // What happens when stopping a running video job: ask, keep, or discard.
    #[serde(
        default = "default_stopped_job_result",
        deserialize_with = "deserialize_stopped_job_result"
    )]
    pub stopped_job_result: String,
}

fn default_true() -> bool {
    true
}

fn default_edit_dimension() -> u32 {
    crate::steps::registry().param_default_u32("edit", "max_dimension")
}

fn default_edit_steps() -> u32 {
    crate::steps::registry().param_default_u32("edit", "steps")
}

fn default_edit_seed() -> u32 {
    crate::steps::registry().param_default_u32("edit", "seed")
}

fn default_edit_placement() -> String {
    "auto".into()
}

fn default_hdr_mode() -> String {
    "tone_map".into()
}

pub(crate) fn default_interpolation() -> String {
    crate::steps::registry().param_default_str("interpolate", "interpolation")
}

pub(crate) fn default_interpolation_order() -> String {
    crate::steps::registry().param_default_str("interpolate", "interpolation_order")
}

pub(crate) fn default_video_task() -> String {
    "upscale".into()
}

pub(crate) fn default_video_codec() -> String {
    // The worker picks the fastest format the computer can write.
    "auto".into()
}

fn default_appearance() -> String {
    "dark".into()
}

fn default_contrast() -> String {
    "system".into()
}

fn default_viewer_background() -> String {
    "checker".into()
}

fn default_accent() -> String {
    "system".into()
}

fn default_stopped_job_result() -> String {
    "ask".into()
}

fn default_output_bit_depth() -> u8 {
    8
}

fn deserialize_stopped_job_result<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<String, D::Error> {
    let value = String::deserialize(deserializer)?;
    Ok(if matches!(value.as_str(), "ask" | "keep" | "discard") {
        value
    } else {
        default_stopped_job_result()
    })
}

fn deserialize_appearance<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<String, D::Error> {
    let value = String::deserialize(deserializer)?;
    Ok(if matches!(value.as_str(), "dark" | "light" | "system") {
        value
    } else {
        default_appearance()
    })
}

fn deserialize_contrast<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<String, D::Error> {
    let value = String::deserialize(deserializer)?;
    Ok(
        if matches!(value.as_str(), "system" | "standard" | "high") {
            value
        } else {
            default_contrast()
        },
    )
}

fn deserialize_viewer_background<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<String, D::Error> {
    let value = String::deserialize(deserializer)?;
    Ok(
        if matches!(value.as_str(), "checker" | "black" | "grey" | "white") {
            value
        } else {
            default_viewer_background()
        },
    )
}

fn deserialize_accent<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<String, D::Error> {
    let value = String::deserialize(deserializer)?;
    Ok(if matches!(value.as_str(), "system" | "localsr") {
        value
    } else {
        default_accent()
    })
}

impl Default for UiSettings {
    fn default() -> Self {
        let steps = crate::steps::registry();
        Self {
            appearance: default_appearance(),
            colour_blind_palette: false,
            contrast: default_contrast(),
            viewer_background: default_viewer_background(),
            accent: default_accent(),
            interface_scale: 100,
            batch_mode: false,
            task: String::new(),
            video_task: default_video_task(),
            selected_model_id: String::new(),
            selected_video_model_id: "frame_by_frame".into(),
            fix_stages: Vec::new(),
            step_stages: Vec::new(),
            custom_model_path: String::new(),
            custom_models: Vec::new(),
            trusted_checkpoint_sha256: Vec::new(),
            output_scale: steps.param_default_u32("upscale", "output_scale"),
            output_directory: String::new(),
            output_format: "png".into(),
            output_bit_depth: default_output_bit_depth(),
            preserve_metadata: true,
            jpeg_quality: 98,
            device_id: "cpu".into(),
            tile_size: 256,
            halo: 32,
            precision: "auto".into(),
            safe_memory: false,
            deflicker: steps.param_default_bool("deflicker", "deflicker"),
            deflicker_window: steps.param_default_u32("deflicker", "deflicker_window"),
            video_container: "mp4".into(),
            video_codec: default_video_codec(),
            automatic_video_format: true,
            external_ffmpeg_path: String::new(),
            video_hdr_mode: default_hdr_mode(),
            video_target_resolution: steps.param_default_u32("video", "target_resolution"),
            video_low_memory: steps.param_default_bool("video", "video_low_memory"),
            video_crf: 18,
            video_interpolation: default_interpolation(),
            video_interpolation_order: default_interpolation_order(),
            video_clean_audio: false,
            video_audio_model: String::new(),
            neural_engine: true,
            anonymous_update_count: true,
            completion_sound: false,
            check_updates_at_launch: true,
            privacy_choice_made: false,
            crash_reports: true,
            skipped_update_version: String::new(),
            enable_face_model: false,
            face_fidelity: steps.param_default_u32("face_restore", "face_fidelity"),
            enable_live_preview: true,
            low_priority: false,
            allow_unsafe_pickle_model: false,
            quality: String::new(),
            content: "photo".into(),
            fixes: Vec::new(),
            preset_pins: BTreeMap::new(),
            edit_model_id: String::new(),
            edit_max_dimension: default_edit_dimension(),
            edit_steps: default_edit_steps(),
            edit_seed: default_edit_seed(),
            edit_text_encoder: default_edit_placement(),
            edit_vae: default_edit_placement(),
            edit_vram_limit_mb: 0,
            edit_allow_any_size: false,
            custom_edit_model: CustomEditModel::default(),
            model_directory: String::new(),
            gpu_download_notice_dismissed: false,
            linux_menu_prompt_dismissed: false,
            show_in_menu_bar: true,
            notify_on_finish: true,
            menu_bar_icon_monochrome: true,
            auto_cache_cleanup: true,
            keep_awake: true,
            stopped_job_result: default_stopped_job_result(),
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct ModelDownloadStatus {
    pub model_id: String,
    pub status: String,
    pub progress: f64,
    #[serde(default)]
    pub downloaded_bytes: u64,
    #[serde(default)]
    pub total_bytes: u64,
    #[serde(default = "default_download_phase")]
    pub phase: String,
    #[serde(default)]
    pub received_bytes: u64,
}

fn default_download_phase() -> String {
    "downloading".into()
}

/// One step of the running photo job, in the order the engine runs them.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct JobStep {
    /// The request stage's kind: `restore`, `deblock`, `upscale`, `face_restore`, a step's.
    pub kind: String,
    /// `blur`, `jpeg`, `noise`, `colour`, or empty for a stage that fixes nothing.
    pub fix: String,
    pub model_id: String,
    /// `waiting`, `running`, `done` or `skipped`.
    pub state: String,
    /// Why the engine skipped the step.
    pub note: String,
    /// The step's finished picture in the preview folder; empty until it arrived.
    pub preview_url: String,
    /// A full-size file of the step's picture exists.
    pub kept: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RuntimeStatus {
    #[serde(default)]
    pub compare: crate::compare::CompareState,
    #[serde(default)]
    pub comparison_pair: ComparisonPair,
    #[serde(default)]
    pub preview_original: PreviewOriginalRegion,
    #[serde(default)]
    pub video_memory: Option<serde_json::Value>,
    pub worker: String,
    pub active_job_id: String,
    #[serde(default)]
    pub active_batch_job_ids: Vec<String>,
    pub status_title: String,
    pub status_detail: String,
    pub progress: f64,
    pub last_output_path: String,
    pub result_preview_data_url: String,
    #[serde(default)]
    pub comparison_media_id: String,
    #[serde(default)]
    pub comparison_output_path: String,
    #[serde(default)]
    pub comparison_preview_data_url: String,
    #[serde(default)]
    pub comparison_error: String,
    pub download_model_id: String,
    pub download_progress: f64,
    #[serde(default)]
    pub downloads: Vec<ModelDownloadStatus>,
    pub elapsed_seconds: f64,
    pub estimated_remaining_seconds: f64,
    pub throughput: f64,
    pub throughput_unit: String,
    pub active_tile_size: u32,
    pub device_free_memory: u64,
    pub device_allocated_memory: u64,
    pub live_system_ram_available: u64,
    pub live_memory_pressure_percent: f64,
    pub thermal_status: String,
    /// What the engine said about the hardware route during the running job (a check, a
    /// GPU slower than the CPU, a model the NPU does not run). Progress replaces
    /// `status_detail`; this stays until the job ends.
    #[serde(default)]
    pub hardware_note: String,
    /// The engine stopped too often and the host gave up restarting it; `retry_engine`
    /// may start it again. A protocol mismatch or any other failure never sets this.
    #[serde(default)]
    pub engine_retry: bool,
    /// How long a cancelled job may take to stop before the engine is restarted.
    #[serde(default)]
    pub cancel_grace_seconds: u64,
    #[serde(default)]
    pub queue_paused: bool,
    #[serde(default)]
    pub frames_processed: u64,
    #[serde(default)]
    pub total_frames: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub can_keep_partial: Option<bool>,
    /// Tiles finished of the running photo stage, for the stop question.
    #[serde(default)]
    pub tiles_completed: u64,
    #[serde(default)]
    pub tiles_total: u64,
    /// The running photo job's steps; empty outside a job of two or more steps.
    #[serde(default)]
    pub job_steps: Vec<JobStep>,
    /// The plan line's name of each of `job_steps`, for the status title.
    #[serde(skip)]
    pub job_step_names: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct BenchmarkSceneResult {
    pub scene_id: String,
    pub purpose: String,
    pub input_width: u32,
    pub input_height: u32,
    pub output_width: u32,
    pub output_height: u32,
    pub iterations: u32,
    pub median_ms: f64,
    pub p05_ms: f64,
    pub p95_ms: f64,
    pub cv_percent: Option<f64>,
    pub megapixels_per_second: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seconds_per_input_mp: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub encode_ms: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub preview: Option<serde_json::Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct BenchmarkDeviceResult {
    #[serde(default)]
    pub completed_at_unix: u64,
    pub device: String,
    pub device_type: String,
    pub device_name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub preset: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_name: Option<String>,
    pub thermal_state: String,
    pub warmup_iterations: u32,
    #[serde(default)]
    pub peak_memory_bytes: Option<u64>,
    #[serde(default)]
    pub peak_device_memory_bytes: Option<u64>,
    pub stable: bool,
    pub cv_percent: Option<f64>,
    #[serde(default)]
    pub score: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seconds_per_input_mp: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub matched_cpu: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub psnr_db: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status_note: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommended: Option<bool>,
    pub scenes: Vec<BenchmarkSceneResult>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CpuThreadBenchmark {
    pub physical_cores: u32,
    pub logical_cores: u32,
    pub recommended_threads: u32,
    #[serde(default)]
    pub tested: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub physical_seconds_per_input_mp: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub logical_seconds_per_input_mp: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_name: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MissingPresetModel {
    pub preset: String,
    pub preset_label: String,
    pub model_id: String,
    pub model_name: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct BenchmarkedModelSummary {
    pub preset: String,
    pub preset_label: String,
    pub model_id: String,
    pub model_name: String,
    pub scale: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommended_device: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommended_device_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommended_seconds_per_input_mp: Option<f64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct BenchmarkResult {
    pub workload_version: String,
    pub backend: String,
    pub device: String,
    pub model_id: String,
    pub model_name: String,
    pub scale: u32,
    pub input_width: u32,
    pub input_height: u32,
    pub warmup_count: u32,
    pub measured_frame_count: u32,
    pub median_inference_ms: f64,
    pub p95_inference_ms: f64,
    pub end_to_end_fps: f64,
    pub processed_megapixels_per_second: f64,
    pub total_elapsed_seconds: f64,
    pub peak_memory_bytes: Option<u64>,
    pub score: f64,
    // ---- LocalSR Benchmark v2 (localsr-benchmark-v2) ----
    // v2 results are multi-device/multi-scene payloads. The v1 fields above
    // stay mandatory so an old saved result keeps deserializing; v2 fills
    // them with the fastest accelerator's headline scene for continuity.
    #[serde(default)]
    pub device_results: Vec<BenchmarkDeviceResult>,
    /// Most recent completed run per device, from this same workload version.
    #[serde(default)]
    pub device_history: Vec<BenchmarkDeviceResult>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub system_score: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cpu_score: Option<f64>,
    #[serde(default)]
    pub stable: bool,
    #[serde(default)]
    pub cv_percent: Option<f64>,
    #[serde(default)]
    pub result_elapsed_seconds: f64,
    #[serde(default)]
    pub thermal_state: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reference_label: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reference_ratio: Option<f64>,
    // ---- LocalSR Benchmark v3 (localsr-benchmark-v3) ----
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommended_device: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommended_device_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommended_device_seconds_per_input_mp: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommended_cpu_threads: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cpu_threads: Option<CpuThreadBenchmark>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cpu_avx2: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cpu_memory_format: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub missing_presets: Vec<MissingPresetModel>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub benchmarked_models: Vec<BenchmarkedModelSummary>,
}

impl BenchmarkResult {
    pub fn is_v2(&self) -> bool {
        self.workload_version.starts_with("localsr-benchmark-v2")
    }

    pub fn is_v3(&self) -> bool {
        self.workload_version.starts_with("localsr-benchmark-v3")
    }

    pub fn retain_device_scores(&mut self, previous: Option<&Self>, completed_at: u64) {
        if self.is_v3() {
            for device in &mut self.device_results {
                device.completed_at_unix = completed_at;
            }
            self.device_history = self.device_results.clone();
            return;
        }
        let mut history = previous
            .filter(|old| old.workload_version == self.workload_version)
            .map(|old| {
                if old.device_history.is_empty() {
                    old.device_results.clone()
                } else {
                    old.device_history.clone()
                }
            })
            .unwrap_or_default();
        for device in &mut self.device_results {
            device.completed_at_unix = completed_at;
            history.retain(|old| old.device != device.device);
            history.push(device.clone());
        }
        history.sort_by(|a, b| a.device.cmp(&b.device));
        self.device_history = history;
    }
}

impl RuntimeStatus {
    /// Makes `id` the running job or benchmark; the route note belonged to the one before.
    pub fn begin_active_job(&mut self, id: String) {
        if self.active_job_id != id {
            self.frames_processed = 0;
            self.total_frames = 0;
            // Handing over within the same batch keeps the batch's partial-save capability.
            if !self.active_batch_job_ids.iter().any(|member| member == &id) {
                self.can_keep_partial = None;
            }
            self.tiles_completed = 0;
            self.tiles_total = 0;
        }
        self.active_job_id = id;
        self.hardware_note.clear();
    }

    /// Ends or abandons the running job, whatever ended it (its own message, a failed
    /// send, a killed or crashed engine), and returns its id. Its route note goes with it.
    pub fn take_active_job(&mut self) -> String {
        self.hardware_note.clear();
        self.frames_processed = 0;
        self.total_frames = 0;
        self.can_keep_partial = None;
        self.tiles_completed = 0;
        self.tiles_total = 0;
        std::mem::take(&mut self.active_job_id)
    }

    /// Ends the steps of the job that ran; returns the step pictures for the caller to
    /// release from the preview folder.
    pub fn take_job_steps(&mut self) -> Vec<String> {
        self.job_step_names.clear();
        std::mem::take(&mut self.job_steps)
            .into_iter()
            .map(|step| step.preview_url)
            .filter(|url| !url.is_empty())
            .collect()
    }
}

impl Default for RuntimeStatus {
    fn default() -> Self {
        Self {
            compare: crate::compare::CompareState::default(),
            comparison_pair: ComparisonPair::default(),
            preview_original: PreviewOriginalRegion::default(),
            worker: "starting".into(),
            active_job_id: String::new(),
            active_batch_job_ids: Vec::new(),
            status_title: "Starting".into(),
            status_detail: "Opening the isolated inference engine.".into(),
            progress: 0.0,
            last_output_path: String::new(),
            result_preview_data_url: String::new(),
            comparison_media_id: String::new(),
            comparison_output_path: String::new(),
            comparison_preview_data_url: String::new(),
            comparison_error: String::new(),
            download_model_id: String::new(),
            download_progress: 0.0,
            downloads: Vec::new(),
            elapsed_seconds: 0.0,
            estimated_remaining_seconds: 0.0,
            throughput: 0.0,
            throughput_unit: String::new(),
            active_tile_size: 0,
            video_memory: None,
            device_free_memory: 0,
            device_allocated_memory: 0,
            live_system_ram_available: 0,
            live_memory_pressure_percent: 0.0,
            thermal_status: "Not exposed by this backend".into(),
            hardware_note: String::new(),
            engine_retry: false,
            cancel_grace_seconds: 0,
            queue_paused: false,
            frames_processed: 0,
            total_frames: 0,
            can_keep_partial: None,
            tiles_completed: 0,
            tiles_total: 0,
            job_steps: Vec::new(),
            job_step_names: Vec::new(),
        }
    }
}

/// The two saved results the preview compares. The host numbers each request;
/// a worker answer updates a slot only when its slot, generation and target
/// match, so a late answer for an earlier pair or media is dropped.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct ComparisonPair {
    pub media_id: String,
    pub generation: u64,
    pub showing: ComparisonSlot,
    pub against: ComparisonSlot,
    #[serde(skip)]
    pub region_request_id: u64,
}

/// Full-resolution crop state for the Original layer in the main preview.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct PreviewOriginalRegion {
    pub media_id: String,
    #[serde(skip)]
    pub generation: u64,
    #[serde(default)]
    pub region: Option<crate::compare::RegionResult>,
    #[serde(skip)]
    pub requested_region: Option<crate::compare::Region>,
    #[serde(skip)]
    pub path: String,
    #[serde(skip)]
    pub file: Option<crate::compare::FileIdentity>,
    #[serde(skip)]
    pub request_id: u64,
}

/// One side of the pair. The Original is drawn from the probe thumbnail the
/// media already carries, so its slot holds only the target and dimensions.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct ComparisonSlot {
    /// "original" or a job id.
    pub target_id: String,
    #[serde(skip)]
    pub output_path: String,
    pub preview_data_url: String,
    pub error: String,
    /// The saved file's dimensions, and the bounded preview's.
    pub width: u32,
    pub height: u32,
    pub preview_width: u32,
    pub preview_height: u32,
    #[serde(default)]
    pub region: Option<crate::compare::RegionResult>,
    #[serde(skip)]
    pub requested_region: Option<crate::compare::Region>,
    #[serde(skip)]
    pub file: Option<crate::compare::FileIdentity>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct InstalledEngine {
    pub id: String,
    pub backend: String,
    pub bundled: bool,
    pub active: bool,
    pub path: String,
    pub needs_reinstall: bool,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct EngineRecommendation {
    pub backend: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub adapter_name: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct EngineCatalogEntry {
    pub backend: String,
    pub engine_id: String,
    pub version: String,
    pub size: u64,
    pub manifest_url: String,
    pub parts_count: usize,
    pub installed: bool,
    pub active: bool,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct EngineDownloadStatus {
    pub stage: String,
    pub backend: String,
    pub engine_id: String,
    pub downloaded: u64,
    pub total: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct EngineManagerSnapshot {
    pub supported: bool,
    pub managed_by_store: bool,
    pub configured: bool,
    pub active_backend: String,
    pub active_engine_id: String,
    pub bundled_backend: String,
    pub installed: Vec<InstalledEngine>,
    pub recommendation: EngineRecommendation,
    pub catalog: Vec<EngineCatalogEntry>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub catalog_error: Option<String>,
    pub download: EngineDownloadStatus,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AppSnapshot {
    pub app_version: String,
    pub protocol_version: u32,
    pub catalog: CatalogManifest,
    pub media: Vec<MediaItem>,
    pub jobs: Vec<JobRecord>,
    pub settings: UiSettings,
    pub recipes: Vec<Recipe>,
    pub capabilities: CapabilityInfo,
    pub engine: Option<EngineInfo>,
    pub runtime: RuntimeStatus,
    pub latest_benchmark: Option<BenchmarkResult>,
    pub edit: crate::editing::EditSetup,
    #[serde(default)]
    pub default_model_directory: String,
    #[serde(default)]
    pub model_directory_missing: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gpu_download_notice: Option<String>,
    #[serde(default)]
    pub linux_menu_offer: bool,
    #[serde(default)]
    pub linux_menu_installed: bool,
    #[serde(default)]
    pub linux_menu_available: bool,
    #[serde(default)]
    pub engines: EngineManagerSnapshot,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct StartBenchmarkInput {
    #[serde(default)]
    pub device: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct VideoComparisonSources {
    pub playback_note: String,
    pub original_path: String,
    pub enhanced_path: String,
    pub original_url: Option<String>,
    pub enhanced_url: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CompareVideoSlot {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
    pub fps: f64,
    pub duration: f64,
    pub frames: u64,
    pub audio_path: String,
    pub audio_url: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CompareVideoSources {
    pub playback_note: String,
    pub video_path: String,
    pub video_url: Option<String>,
    pub width: u32,
    pub height: u32,
    pub duration: f64,
    pub slots: Vec<CompareVideoSlot>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct StartBatchInput {
    /// Host-only: "Try others…" names each output after its model. The UI
    /// cannot set it, so Start, Finder and the CLI keep their names.
    #[serde(skip)]
    pub name_by_model: bool,
    /// The quality, content and recipe the plan card showed, kept with the job
    /// (`jobs.ui_json`) and never sent to the worker.
    #[serde(default)]
    pub ui: Option<crate::results::ResultUi>,
    pub media_ids: Vec<String>,
    pub batch_mode: bool,
    pub task: String,
    #[serde(default = "default_video_task")]
    pub video_task: String,
    pub model_id: String,
    pub video_model_id: String,
    pub fix_stages: Vec<FixStageSelection>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub step_stages: Vec<StepStageSelection>,
    pub custom_model_path: String,
    pub output_directory: String,
    pub output_format: String,
    #[serde(default = "default_output_bit_depth")]
    pub output_bit_depth: u8,
    pub output_scale: u32,
    pub preserve_metadata: bool,
    pub jpeg_quality: u32,
    pub device: String,
    pub tile_size: u32,
    pub halo: u32,
    pub precision: String,
    pub safe_memory: bool,
    pub deflicker: bool,
    pub deflicker_window: u32,
    pub video_container: String,
    #[serde(default = "default_video_codec")]
    pub video_codec: String,
    #[serde(default)]
    pub external_ffmpeg_path: String,
    #[serde(default = "default_hdr_mode")]
    pub video_hdr_mode: String,
    #[serde(default)]
    pub video_target_resolution: u32,
    #[serde(default = "default_true")]
    pub video_low_memory: bool,
    pub video_crf: u32,
    #[serde(default = "default_true")]
    pub neural_engine: bool,
    pub enable_face_model: bool,
    pub face_fidelity: u32,
    pub enable_live_preview: bool,
    pub allow_unsafe_pickle_model: bool,
    #[serde(default = "default_interpolation")]
    pub video_interpolation: String,
    #[serde(default = "default_interpolation_order")]
    pub video_interpolation_order: String,
    #[serde(default)]
    pub video_clean_audio: bool,
    #[serde(default)]
    pub video_audio_model: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WorkerEnvelope {
    #[serde(rename = "type")]
    pub message_type: String,
    #[serde(default)]
    pub data: serde_json::Value,
}

fn preset_eligible_default() -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_models_without_the_preset_flag_stay_preset_eligible() {
        let catalog: serde_json::Value =
            serde_json::from_str(include_str!("../resources/model-catalog.json")).unwrap();
        for entry in catalog["models"].as_array().unwrap() {
            let model: CatalogModel = serde_json::from_value(entry.clone()).unwrap();
            let excluded = model.model_id.contains("dat2") || model.model_id == "birefnet_general";
            assert_eq!(model.preset_eligible, !excluded, "{}", model.model_id);
            let mut older = entry.clone();
            older.as_object_mut().unwrap().remove("preset_eligible");
            let older: CatalogModel = serde_json::from_value(older).unwrap();
            assert!(older.preset_eligible, "{}", model.model_id);
        }
    }

    #[test]
    fn video_interpolation_and_audio_models_deserialize_attribution_and_defaults() {
        let catalog: serde_json::Value =
            serde_json::from_str(include_str!("../resources/model-catalog.json")).unwrap();
        for section in ["video_models", "interpolation_models", "audio_models"] {
            let entries = catalog[section].as_array().unwrap();
            assert!(!entries.is_empty(), "{section} must not be empty");
            for entry in entries {
                let model: CatalogVideoModel = serde_json::from_value(entry.clone()).unwrap();
                assert!(!model.author.is_empty(), "{}", model.model_id);
                assert!(
                    model.source_url.starts_with("https://"),
                    "{}",
                    model.model_id
                );
                assert!(!model.license_name.is_empty(), "{}", model.model_id);
                assert!(
                    model.license_url.starts_with("https://"),
                    "{}",
                    model.model_id
                );
                assert_eq!(
                    model.attribution_required,
                    !matches!(model.license_name.as_str(), "MIT" | "MIT OR Apache-2.0"),
                    "{}",
                    model.model_id
                );

                let mut minimal = entry.clone();
                let obj = minimal.as_object_mut().unwrap();
                for key in [
                    "author",
                    "source_url",
                    "license_name",
                    "license_url",
                    "commercial_use_allowed",
                    "automated_download_allowed",
                    "redistribution_allowed",
                    "attribution_required",
                    "terms_acceptance_required",
                ] {
                    obj.remove(key);
                }
                let fallback: CatalogVideoModel = serde_json::from_value(minimal).unwrap();
                assert_eq!(fallback.author, "");
                assert!(!fallback.attribution_required);
                assert!(!fallback.redistribution_allowed);
            }
        }
    }

    #[test]
    fn v1_benchmark_results_keep_deserializing_after_v2_fields_were_added() {
        let legacy = serde_json::json!({
            "workload_version": "localsr-benchmark-v1",
            "backend": "mps",
            "device": "mps",
            "model_id": "span_photo_x4",
            "model_name": "Quick",
            "scale": 4,
            "input_width": 128,
            "input_height": 128,
            "warmup_count": 1,
            "measured_frame_count": 5,
            "median_inference_ms": 40.0,
            "p95_inference_ms": 48.0,
            "end_to_end_fps": 25.0,
            "processed_megapixels_per_second": 0.4096,
            "total_elapsed_seconds": 0.2,
            "peak_memory_bytes": 1234,
            "score": 409.6
        });
        let result: BenchmarkResult = serde_json::from_value(legacy).unwrap();
        assert!(!result.is_v2());
        assert_eq!(result.score, 409.6);
        assert!(result.device_results.is_empty());
        assert!(!result.stable);
    }

    #[test]
    fn v2_benchmark_results_carry_the_full_device_table() {
        let scene = serde_json::json!({
            "scene_id": "s1-classroom",
            "purpose": "compute",
            "input_width": 512,
            "input_height": 512,
            "output_width": 2048,
            "output_height": 2048,
            "iterations": 9,
            "median_ms": 400.0,
            "p05_ms": 390.0,
            "p95_ms": 430.0,
            "cv_percent": 3.1,
            "megapixels_per_second": 1.28,
            "encode_ms": null
        });
        let device = serde_json::json!({
            "device": "mps",
            "device_type": "mps",
            "device_name": "Apple GPU (Metal)",
            "thermal_state": "nominal",
            "warmup_iterations": 6,
            "peak_memory_bytes": 8_000_000,
            "peak_device_memory_bytes": 4_000_000,
            "stable": true,
            "cv_percent": 3.1,
            "score": 1.28,
            "scenes": [scene]
        });
        let mut v2 = serde_json::json!({
            "workload_version": "localsr-benchmark-v2",
            "backend": "mps",
            "device": "mps",
            "model_id": "span_photo_x4",
            "model_name": "Quick",
            "scale": 4,
            "input_width": 512,
            "input_height": 512,
            "warmup_count": 6,
            "measured_frame_count": 15,
            "median_inference_ms": 400.0,
            "p95_inference_ms": 430.0,
            "end_to_end_fps": 2.5,
            "processed_megapixels_per_second": 0.2621,
            "total_elapsed_seconds": 180.0,
            "peak_memory_bytes": 8_000_000,
            "score": 1.41,
            "system_score": 1.41,
            "cpu_score": 0.06,
            "stable": true,
            "cv_percent": 3.1,
            "result_elapsed_seconds": 180.0,
            "thermal_state": "nominal",
            "reference_label": "Apple M1 (8-core GPU)",
            "reference_ratio": 1.08
        });
        v2["device_results"] = serde_json::Value::Array(vec![device]);
        let result: BenchmarkResult = serde_json::from_value(v2).unwrap();
        assert!(result.is_v2());
        assert_eq!(result.device_results.len(), 1);
        assert_eq!(result.device_results[0].scenes[0].scene_id, "s1-classroom");
        assert_eq!(result.device_results[0].scenes[0].encode_ms, None);
        assert_eq!(result.system_score, Some(1.41));
        assert_eq!(result.cpu_score, Some(0.06));
        assert_eq!(
            result.reference_label.as_deref(),
            Some("Apple M1 (8-core GPU)")
        );
        assert_eq!(result.reference_ratio, Some(1.08));
        assert!(result.stable);
        let mut cpu = result.clone();
        cpu.device_results[0].device = "cpu".into();
        cpu.device_results[0].device_type = "cpu".into();
        cpu.device_results[0].score = 0.25;
        cpu.retain_device_scores(Some(&result), 1234);
        assert_eq!(cpu.device_results.len(), 1);
        assert_eq!(cpu.device_history.len(), 2);
        let saved: BenchmarkResult =
            serde_json::from_slice(&serde_json::to_vec(&cpu).unwrap()).unwrap();
        let mut gpu = result.clone();
        gpu.device_results[0].score = 2.0;
        gpu.retain_device_scores(Some(&saved), 2345);
        assert_eq!(gpu.device_history.len(), 2);
        assert_eq!(gpu.device_history[0].device, "cpu");
        assert_eq!(gpu.device_history[0].score, 0.25);
        assert_eq!(gpu.device_history[0].completed_at_unix, 1234);
        assert_eq!(gpu.device_history[1].score, 2.0);
        assert_eq!(gpu.device_history[1].completed_at_unix, 2345);
        gpu.workload_version = "future-incompatible-workload".into();
        gpu.retain_device_scores(Some(&saved), 3456);
        assert_eq!(gpu.device_history.len(), 1);
    }

    #[test]
    fn v3_benchmark_results_keep_all_devices_and_models_in_history() {
        let devices = serde_json::json!([
            {
                "device": "cpu",
                "device_type": "cpu",
                "device_name": "CPU",
                "preset": "quick",
                "model_id": "localsr_quick_x4_v1",
                "model_name": "LocalSR Photo S",
                "thermal_state": "nominal",
                "warmup_iterations": 2,
                "stable": true,
                "cv_percent": 1.2,
                "score": 0.3,
                "seconds_per_input_mp": 9.1,
                "matched_cpu": true,
                "recommended": true,
                "scenes": []
            },
            {
                "device": "cpu",
                "device_type": "cpu",
                "device_name": "CPU",
                "preset": "best",
                "model_id": "realplksr_nomoswebphoto_x4",
                "model_name": "RealPLKSR 4x NomosWebPhoto — Best",
                "thermal_state": "nominal",
                "warmup_iterations": 2,
                "stable": true,
                "cv_percent": 1.5,
                "score": 0.05,
                "seconds_per_input_mp": 200.8,
                "matched_cpu": true,
                "recommended": true,
                "scenes": []
            }
        ]);
        let mut v3 = serde_json::json!({
            "workload_version": "localsr-benchmark-v3",
            "backend": "cpu",
            "device": "cpu",
            "model_id": "localsr_quick_x4_v1",
            "model_name": "LocalSR Photo S",
            "scale": 4,
            "input_width": 256,
            "input_height": 256,
            "warmup_count": 3,
            "measured_frame_count": 10,
            "median_inference_ms": 200.0,
            "p95_inference_ms": 220.0,
            "end_to_end_fps": 5.0,
            "processed_megapixels_per_second": 0.3,
            "total_elapsed_seconds": 30.0,
            "peak_memory_bytes": 1024,
            "score": 0.3,
            "recommended_device": "cpu",
            "recommended_device_name": "CPU",
            "recommended_device_seconds_per_input_mp": 9.1,
            "recommended_cpu_threads": 4,
            "cpu_avx2": false,
            "cpu_memory_format": "nchw"
        });
        v3["device_results"] = devices;
        let mut result: BenchmarkResult = serde_json::from_value(v3).unwrap();
        assert!(result.is_v3());
        assert!(!result.is_v2());
        assert_eq!(result.recommended_cpu_threads, Some(4));
        assert_eq!(result.cpu_memory_format.as_deref(), Some("nchw"));
        result.retain_device_scores(None, 5555);
        assert_eq!(result.device_history.len(), 2);
        assert_eq!(result.device_history[0].completed_at_unix, 5555);
    }
}
