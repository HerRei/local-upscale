//! Typed worker job-request protocol messages matching `protocol/worker-protocol.schema.json`.

use std::{collections::BTreeMap, str::FromStr};

use serde::{Deserialize, Serialize};

use crate::error::AppError;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Precision {
    Fp32,
    Fp16,
    Bf16,
    Auto,
}

impl FromStr for Precision {
    type Err = AppError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "fp32" => Ok(Self::Fp32),
            "fp16" => Ok(Self::Fp16),
            "bf16" => Ok(Self::Bf16),
            "auto" => Ok(Self::Auto),
            _ => Err(AppError::Validation(format!(
                "unsupported precision: {value}"
            ))),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum OutputFormat {
    Png,
    Jpg,
    Tif,
    Webp,
    Heic,
}

impl FromStr for OutputFormat {
    type Err = AppError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "png" => Ok(Self::Png),
            "jpg" => Ok(Self::Jpg),
            "tif" => Ok(Self::Tif),
            "webp" => Ok(Self::Webp),
            "heic" => Ok(Self::Heic),
            _ => Err(AppError::Validation(format!(
                "unsupported output format: {value}"
            ))),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum VideoContainer {
    Mp4,
    Mkv,
    Mov,
}

impl FromStr for VideoContainer {
    type Err = AppError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "mp4" => Ok(Self::Mp4),
            "mkv" => Ok(Self::Mkv),
            "mov" => Ok(Self::Mov),
            _ => Err(AppError::Validation(format!(
                "unsupported video container: {value}"
            ))),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum VideoCodec {
    Auto,
    Av1,
    Vp9,
    Ffv1,
    H264,
    Hevc,
    Prores,
}

impl FromStr for VideoCodec {
    type Err = AppError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "auto" => Ok(Self::Auto),
            "av1" => Ok(Self::Av1),
            "vp9" => Ok(Self::Vp9),
            "ffv1" => Ok(Self::Ffv1),
            "h264" => Ok(Self::H264),
            "hevc" => Ok(Self::Hevc),
            "prores" => Ok(Self::Prores),
            _ => Err(AppError::Validation(format!(
                "unsupported video codec: {value}"
            ))),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum HdrMode {
    Reject,
    ToneMap,
    Preserve,
}

impl FromStr for HdrMode {
    type Err = AppError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "reject" => Ok(Self::Reject),
            "tone_map" => Ok(Self::ToneMap),
            "preserve" => Ok(Self::Preserve),
            _ => Err(AppError::Validation(format!(
                "unsupported HDR mode: {value}"
            ))),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum VideoModelKind {
    SpandrelImage,
    Seedvr2,
    Flashvsr,
}

impl FromStr for VideoModelKind {
    type Err = AppError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "spandrel_image" => Ok(Self::SpandrelImage),
            "seedvr2" => Ok(Self::Seedvr2),
            "flashvsr" => Ok(Self::Flashvsr),
            _ => Err(AppError::Validation(format!(
                "unsupported video model kind: {value}"
            ))),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum InterpolationMode {
    Off,
    X2,
    X4,
    Fps60,
    Fps120,
}

impl FromStr for InterpolationMode {
    type Err = AppError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "off" => Ok(Self::Off),
            "x2" => Ok(Self::X2),
            "x4" => Ok(Self::X4),
            "fps60" => Ok(Self::Fps60),
            "fps120" => Ok(Self::Fps120),
            _ => Err(AppError::Validation(format!(
                "unsupported interpolation mode: {value}"
            ))),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum InterpolationOrder {
    AfterUpscale,
    BeforeUpscale,
}

impl FromStr for InterpolationOrder {
    type Err = AppError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "after_upscale" => Ok(Self::AfterUpscale),
            "before_upscale" => Ok(Self::BeforeUpscale),
            _ => Err(AppError::Validation(format!(
                "unsupported interpolation order: {value}"
            ))),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub enum StageExecution {
    #[serde(rename = "sequential")]
    Sequential,
    #[serde(rename = "fused-with-upscale")]
    FusedWithUpscale,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct PipelineStageRequest {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub step: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub role: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fix: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fidelity: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub execution: Option<StageExecution>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mask_path: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct ImageJobRequest {
    pub job_id: String,
    pub image_path: String,
    pub model_path: String,
    pub output_path: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scratch_directory: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_temporary_directory: Option<String>,
    pub output_format: OutputFormat,
    #[serde(
        default = "default_output_bit_depth",
        skip_serializing_if = "is_default_output_bit_depth"
    )]
    pub output_bit_depth: u8,
    pub device: String,
    #[serde(default = "default_tile_size")]
    pub tile_size: u32,
    #[serde(default = "default_halo")]
    pub halo: u32,
    #[serde(default = "default_precision")]
    pub precision: Precision,
    #[serde(default = "default_jpeg_quality")]
    pub jpeg_quality: u32,
    #[serde(default = "default_true")]
    pub preserve_metadata: bool,
    #[serde(default)]
    pub safe_memory: bool,
    #[serde(default = "default_true")]
    pub neural_engine: bool,
    #[serde(default = "default_true")]
    pub preview_enabled: bool,
    #[serde(default = "default_preview_max_fps")]
    pub preview_max_fps: f64,
    #[serde(default = "default_preview_max_dimension")]
    pub preview_max_dimension: u32,
    /// Set when the job is sent, only for an engine with the `stage_results` feature:
    /// an older engine refuses a field it does not know.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stage_picture_max_dimension: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_scale: Option<u32>,
    #[serde(default)]
    pub face_model_path: Option<String>,
    #[serde(default = "default_face_fidelity")]
    pub face_fidelity: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stages: Option<Vec<PipelineStageRequest>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub allow_unverified_checkpoint: Option<bool>,
    #[serde(default)]
    pub approved_checkpoint_sha256: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct VideoJobRequest {
    pub job_id: String,
    pub video_path: String,
    #[serde(default = "default_hdr_mode")]
    pub hdr_mode: HdrMode,
    pub model_path: String,
    pub output_video_path: String,
    #[serde(default = "default_container")]
    pub container: VideoContainer,
    #[serde(default = "default_video_codec")]
    pub video_codec: VideoCodec,
    #[serde(default)]
    pub external_ffmpeg: Option<String>,
    #[serde(default = "default_crf")]
    pub crf: u32,
    #[serde(default)]
    pub fps: Option<f64>,
    pub device: String,
    #[serde(default = "default_tile_size")]
    pub tile_size: u32,
    #[serde(default = "default_halo")]
    pub halo: u32,
    #[serde(default = "default_precision")]
    pub precision: Precision,
    #[serde(default)]
    pub safe_memory: bool,
    #[serde(default = "default_true")]
    pub neural_engine: bool,
    #[serde(default)]
    pub keyframe_interval: Option<u32>,
    #[serde(default)]
    pub start_frame: Option<u32>,
    #[serde(default)]
    pub end_frame: Option<u32>,
    #[serde(default)]
    pub face_model_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub face_fidelity: Option<f64>,
    #[serde(default)]
    pub deflicker: bool,
    #[serde(default = "default_deflicker_window")]
    pub deflicker_window: u32,
    #[serde(default = "default_model_kind")]
    pub model_kind: VideoModelKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub video_model_id: Option<String>,
    #[serde(default)]
    pub bundle_dir: Option<String>,
    #[serde(default)]
    pub temporal_window: u32,
    #[serde(default)]
    pub temporal_overlap: u32,
    #[serde(default)]
    pub target_resolution: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub video_low_memory: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_scale: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stages: Option<Vec<PipelineStageRequest>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub allow_unverified_checkpoint: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approved_checkpoint_sha256: Option<String>,
    #[serde(default = "default_true")]
    pub preview_enabled: bool,
    #[serde(default = "default_preview_max_fps")]
    pub preview_max_fps: f64,
    #[serde(default = "default_preview_max_dimension")]
    pub preview_max_dimension: u32,
    #[serde(default = "default_interpolation")]
    pub interpolation: InterpolationMode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interpolation_order: Option<InterpolationOrder>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interpolation_model_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interpolation_bundle_dir: Option<String>,
    #[serde(default)]
    pub audio_denoise: bool,
    #[serde(default)]
    pub audio_denoise_model_id: Option<String>,
    #[serde(default)]
    pub audio_denoise_bundle_dir: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scratch_directory: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_temporary_directory: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temporary_directory: Option<String>,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EditPlacement {
    #[default]
    Auto,
    Gpu,
    Cpu,
}

impl EditPlacement {
    pub fn is_auto(&self) -> bool {
        *self == Self::Auto
    }
}

impl FromStr for EditPlacement {
    type Err = AppError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "auto" => Ok(Self::Auto),
            "gpu" => Ok(Self::Gpu),
            "cpu" => Ok(Self::Cpu),
            _ => Err(AppError::Validation(format!(
                "unsupported edit placement: {value}"
            ))),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct EditJobRequest {
    pub job_id: String,
    pub image_path: String,
    pub output_path: String,
    pub model_id: String,
    pub bundle_dir: String,
    pub runtime_path: String,
    pub prompt: String,
    pub device: String,
    #[serde(default = "default_edit_max_dimension")]
    pub max_dimension: u32,
    #[serde(default = "default_edit_steps")]
    pub steps: u32,
    #[serde(default = "default_edit_seed")]
    pub seed: u32,
    #[serde(default)]
    pub accepted_terms: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scratch_directory: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_temporary_directory: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub family: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub custom_files: Option<BTreeMap<String, String>>,
    #[serde(default, skip_serializing_if = "EditPlacement::is_auto")]
    pub edit_text_encoder: EditPlacement,
    #[serde(default, skip_serializing_if = "EditPlacement::is_auto")]
    pub edit_vae: EditPlacement,
    #[serde(default, skip_serializing_if = "is_zero_u64")]
    pub edit_vram_limit_mb: u64,
    #[serde(default, skip_serializing_if = "is_false")]
    pub edit_allow_any_size: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", content = "data")]
pub enum WorkerJobRequest {
    #[serde(rename = "job_request")]
    Job(ImageJobRequest),
    #[serde(rename = "video_job_request")]
    Video(Box<VideoJobRequest>),
    #[serde(rename = "edit_job_request")]
    Edit(EditJobRequest),
}

fn default_true() -> bool {
    true
}

fn is_zero_u64(value: &u64) -> bool {
    *value == 0
}

fn is_false(value: &bool) -> bool {
    !*value
}

fn default_tile_size() -> u32 {
    256
}

fn default_halo() -> u32 {
    32
}

fn default_precision() -> Precision {
    Precision::Fp32
}

fn default_jpeg_quality() -> u32 {
    98
}

fn default_output_bit_depth() -> u8 {
    8
}

fn is_default_output_bit_depth(depth: &u8) -> bool {
    *depth == 8
}

fn default_preview_max_fps() -> f64 {
    2.0
}

fn default_preview_max_dimension() -> u32 {
    320
}

fn default_face_fidelity() -> f64 {
    0.7
}

fn default_hdr_mode() -> HdrMode {
    HdrMode::Reject
}

fn default_container() -> VideoContainer {
    VideoContainer::Mp4
}

fn default_video_codec() -> VideoCodec {
    VideoCodec::Av1
}

fn default_crf() -> u32 {
    18
}

fn default_deflicker_window() -> u32 {
    3
}

fn default_model_kind() -> VideoModelKind {
    VideoModelKind::SpandrelImage
}

fn default_interpolation() -> InterpolationMode {
    InterpolationMode::Off
}

fn default_edit_max_dimension() -> u32 {
    512
}

fn default_edit_steps() -> u32 {
    40
}

fn default_edit_seed() -> u32 {
    42
}

#[cfg(test)]
mod tests {
    use serde_json::{json, Value};

    use super::*;

    fn schema_validator() -> jsonschema::Validator {
        let schema: Value = serde_json::from_str(include_str!(
            "../../../protocol/worker-protocol.schema.json"
        ))
        .expect("worker protocol schema must be valid JSON");
        jsonschema::validator_for(&schema).expect("worker protocol schema must compile")
    }

    #[test]
    fn schema_rejects_unknown_fields_and_wrong_field_types() {
        let validator = schema_validator();
        let samples: BTreeMap<String, Value> = serde_json::from_str(include_str!(
            "../../../tests/fixtures/job_request_samples.json"
        ))
        .unwrap();

        let mut bad_type = samples["image_upscale_fixes_and_face"].clone();
        bad_type["data"]["tile_size"] = json!("256");
        assert!(!validator.is_valid(&bad_type));

        let mut bad_extra = samples["image_upscale_fixes_and_face"].clone();
        bad_extra["data"]["unknown_field"] = json!(true);
        assert!(!validator.is_valid(&bad_extra));

        let mut bad_video = samples["video_temporal_model"].clone();
        bad_video["data"]["crf"] = json!("high");
        assert!(!validator.is_valid(&bad_video));

        let mut bad_edit = samples["edit_catalog_model"].clone();
        bad_edit["data"]["steps"] = json!("forty");
        assert!(!validator.is_valid(&bad_edit));
    }

    #[test]
    fn old_0_6_1_requests_deserialize_into_typed_requests_and_validate() {
        let validator = schema_validator();
        let image_061 = json!({
            "type": "job_request",
            "data": {
                "job_id": "061-img",
                "image_path": "/in/photo.png",
                "model_path": "/models/span.pth",
                "output_path": "/out/photo_upscaled_4x.png",
                "scratch_directory": "/work/061",
                "output_format": "png",
                "device": "mps",
                "tile_size": 256,
                "halo": 32,
                "precision": "fp32",
                "jpeg_quality": 98,
                "preserve_metadata": true,
                "safe_memory": false,
                "neural_engine": true,
                "preview_enabled": true,
                "preview_max_fps": 2.0,
                "preview_max_dimension": 320,
                "output_scale": 4,
                "face_model_path": null,
                "face_fidelity": 0.7,
                "stages": [
                    {
                        "kind": "deblock",
                        "role": "fix",
                        "fix": "jpeg",
                        "model_id": "fbcnn_color",
                        "model_path": "/models/fbcnn.pth"
                    },
                    {
                        "kind": "upscale",
                        "role": "primary",
                        "fix": null,
                        "model_id": "span_photo_x4",
                        "model_path": "/models/span.pth"
                    }
                ],
                "allow_unverified_checkpoint": false,
                "approved_checkpoint_sha256": null,
                "run_alone": true
            }
        });
        let video_061: Value = serde_json::from_str(
            r#"{
                "type": "video_job_request",
                "data": {
                    "job_id": "061-vid",
                    "video_path": "/in/clip.mp4",
                    "hdr_mode": "tone_map",
                    "model_path": "/models/span.pth",
                    "output_video_path": "/out/clip_video_4x.mp4",
                    "container": "mp4",
                    "video_codec": "av1",
                    "external_ffmpeg": "",
                    "crf": 18,
                    "fps": null,
                    "device": "cpu",
                    "tile_size": 256,
                    "halo": 32,
                    "precision": "fp32",
                    "safe_memory": false,
                    "neural_engine": true,
                    "keyframe_interval": null,
                    "start_frame": null,
                    "end_frame": null,
                    "face_model_path": null,
                    "face_fidelity": 0.7,
                    "deflicker": false,
                    "deflicker_window": 3,
                    "model_kind": "spandrel_image",
                    "bundle_dir": null,
                    "temporal_window": 0,
                    "temporal_overlap": 0,
                    "target_resolution": 0,
                    "output_scale": 4,
                    "stages": [
                        {
                            "kind": "upscale",
                            "role": "primary",
                            "fix": null,
                            "model_id": "span_photo_x4",
                            "model_path": "/models/span.pth"
                        }
                    ],
                    "allow_unverified_checkpoint": false,
                    "approved_checkpoint_sha256": null,
                    "preview_enabled": true,
                    "preview_max_fps": 2.0,
                    "preview_max_dimension": 320,
                    "interpolation": "off",
                    "interpolation_order": "after_upscale",
                    "interpolation_model_id": null,
                    "interpolation_bundle_dir": null,
                    "audio_denoise": false,
                    "audio_denoise_model_id": null,
                    "audio_denoise_bundle_dir": null
                }
            }"#,
        )
        .unwrap();
        let edit_061 = json!({
            "type": "edit_job_request",
            "data": {
                "job_id": "061-edit",
                "image_path": "/in/photo.png",
                "output_path": "/out/edit.png",
                "model_id": "qwen_edit_2511_q2_k",
                "bundle_dir": "/models/qwen",
                "runtime_path": "/bin/sd-cli",
                "prompt": "Make the sky dramatic.",
                "device": "mps",
                "max_dimension": 512,
                "steps": 40,
                "seed": 42,
                "accepted_terms": false
            }
        });

        for raw in [&image_061, &video_061, &edit_061] {
            let typed: WorkerJobRequest = serde_json::from_value(raw.clone()).unwrap();
            let reserialized = serde_json::to_value(&typed).unwrap();
            assert!(validator.is_valid(&reserialized), "invalid: {reserialized}");
        }
    }

    #[test]
    fn edit_placement_fields_omit_auto_and_validate_overrides() {
        let validator = schema_validator();
        let mut edit: EditJobRequest = serde_json::from_value(json!({
            "job_id": "edit-place",
            "image_path": "/in/photo.png",
            "output_path": "/out/edit.png",
            "model_id": "qwen_edit_2511_q2_k",
            "bundle_dir": "/models/qwen",
            "runtime_path": "/bin/sd-cli",
            "prompt": "Brighten",
            "device": "cuda:0"
        }))
        .unwrap();
        assert_eq!(edit.edit_text_encoder, EditPlacement::Auto);
        assert_eq!(edit.edit_vae, EditPlacement::Auto);
        let default_wire = serde_json::to_value(WorkerJobRequest::Edit(edit.clone())).unwrap();
        assert!(default_wire["data"].get("edit_text_encoder").is_none());
        assert!(default_wire["data"].get("edit_vae").is_none());
        assert!(validator.is_valid(&default_wire));

        edit.edit_text_encoder = EditPlacement::Cpu;
        edit.edit_vae = EditPlacement::Gpu;
        let override_wire = serde_json::to_value(WorkerJobRequest::Edit(edit)).unwrap();
        assert_eq!(override_wire["data"]["edit_text_encoder"], "cpu");
        assert_eq!(override_wire["data"]["edit_vae"], "gpu");
        assert!(validator.is_valid(&override_wire));
    }

    #[test]
    fn edit_expert_fields_omit_defaults_and_validate_overrides() {
        let validator = schema_validator();
        let mut edit: EditJobRequest = serde_json::from_value(json!({
            "job_id": "edit-expert",
            "image_path": "/in/photo.png",
            "output_path": "/out/edit.png",
            "model_id": "flux2_klein_4b_q4_0",
            "bundle_dir": "/models/flux",
            "runtime_path": "/bin/sd-cli",
            "prompt": "Brighten",
            "device": "cuda:0"
        }))
        .unwrap();
        assert_eq!(edit.edit_vram_limit_mb, 0);
        assert!(!edit.edit_allow_any_size);
        let default_wire = serde_json::to_value(WorkerJobRequest::Edit(edit.clone())).unwrap();
        assert!(default_wire["data"].get("edit_vram_limit_mb").is_none());
        assert!(default_wire["data"].get("edit_allow_any_size").is_none());
        assert!(validator.is_valid(&default_wire));

        edit.edit_vram_limit_mb = 8192;
        edit.edit_allow_any_size = true;
        let override_wire = serde_json::to_value(WorkerJobRequest::Edit(edit)).unwrap();
        assert_eq!(override_wire["data"]["edit_vram_limit_mb"], 8192);
        assert_eq!(override_wire["data"]["edit_allow_any_size"], true);
        assert!(validator.is_valid(&override_wire));
    }

    #[test]
    fn precision_auto_round_trips_and_validates_against_schema() {
        let validator = schema_validator();
        let precision: Precision = "auto".parse().unwrap();
        assert_eq!(precision, Precision::Auto);
        let samples: BTreeMap<String, Value> = serde_json::from_str(include_str!(
            "../../../tests/fixtures/job_request_samples.json"
        ))
        .unwrap();
        let mut image_auto = samples["image_upscale_fixes_and_face"].clone();
        image_auto["data"]["precision"] = json!("auto");
        let parsed: WorkerJobRequest = serde_json::from_value(image_auto).unwrap();
        let wire = serde_json::to_value(&parsed).unwrap();
        assert_eq!(wire["data"]["precision"], "auto");
        assert!(validator.is_valid(&wire));
    }
}
