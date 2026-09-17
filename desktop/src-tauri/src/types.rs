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
    pub automated_download_allowed: bool,
    pub redistribution_allowed: bool,
    pub attribution_required: bool,
    pub terms_acceptance_required: bool,
    pub engine_id: String,
    pub support_tier: String,
    // Model-library fields (catalog schema 2). Defaults keep an older
    // manifest loadable; `support_tier` stays the validation axis while
    // `rights_status` says what the checkpoint's terms allow.
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
    pub license_name: String,
    pub license_url: String,
    pub author: String,
    pub source_url: String,
    pub min_unified_memory_gb: u64,
    pub min_vram_gb: u64,
    pub temporal_window: u32,
    pub temporal_overlap: u32,
    pub commercial_use_allowed: Option<bool>,
    pub automated_download_allowed: bool,
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
    pub error: String,
    pub selected: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct JobRecord {
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
    #[serde(default = "default_low_memory")]
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
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RecipeStage {
    pub kind: String,
    pub model_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fidelity: Option<f64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default)]
pub struct UiSettings {
    pub interface_scale: u32,
    pub batch_mode: bool,
    pub task: String,
    pub selected_model_id: String,
    pub selected_video_model_id: String,
    pub preprocess_model_id: String,
    pub custom_model_path: String,
    pub output_scale: u32,
    pub output_directory: String,
    pub output_format: String,
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
    // An FFmpeg executable the user installed; LocalSR never bundles one.
    #[serde(default)]
    pub external_ffmpeg_path: String,
    #[serde(default = "default_hdr_mode")]
    pub video_hdr_mode: String,
    #[serde(default)]
    pub video_target_resolution: u32,
    #[serde(default = "default_low_memory")]
    pub video_low_memory: bool,
    pub video_crf: u32,
    // Direct builds send version, platform and channel after an update check.
    #[serde(default = "default_anonymous_update_count")]
    pub anonymous_update_count: bool,
    pub enable_face_model: bool,
    pub face_fidelity: u32,
    pub enable_live_preview: bool,
    pub allow_unsafe_pickle_model: bool,
    // Model-library state: which recipe is active ("quick", "best", "custom"
    // or empty), what the image contains, which problems to fix first, and
    // the checkpoint each preset slot is pinned to ("upscale/photo/best").
    pub quality: String,
    pub content: String,
    pub fixes: Vec<String>,
    pub preset_pins: BTreeMap<String, String>,
}

fn default_anonymous_update_count() -> bool {
    true
}

fn default_low_memory() -> bool {
    true
}

fn default_hdr_mode() -> String {
    "tone_map".into()
}

pub(crate) fn default_video_codec() -> String {
    "av1".into()
}

impl Default for UiSettings {
    fn default() -> Self {
        Self {
            interface_scale: 100,
            batch_mode: false,
            task: String::new(),
            selected_model_id: String::new(),
            selected_video_model_id: "frame_by_frame".into(),
            preprocess_model_id: String::new(),
            custom_model_path: String::new(),
            output_scale: 4,
            output_directory: String::new(),
            output_format: "png".into(),
            preserve_metadata: true,
            jpeg_quality: 98,
            device_id: "cpu".into(),
            tile_size: 256,
            halo: 32,
            precision: "fp32".into(),
            safe_memory: false,
            deflicker: false,
            deflicker_window: 3,
            video_container: "mp4".into(),
            video_codec: default_video_codec(),
            external_ffmpeg_path: String::new(),
            video_hdr_mode: default_hdr_mode(),
            video_target_resolution: 0,
            video_low_memory: true,
            video_crf: 18,
            anonymous_update_count: true,
            enable_face_model: false,
            face_fidelity: 70,
            enable_live_preview: true,
            allow_unsafe_pickle_model: false,
            quality: String::new(),
            content: "photo".into(),
            fixes: Vec::new(),
            preset_pins: BTreeMap::new(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct RuntimeStatus {
    #[serde(default)]
    pub video_memory: Option<serde_json::Value>,
    pub worker: String,
    pub active_job_id: String,
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
    pub scenes: Vec<BenchmarkSceneResult>,
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
}

impl BenchmarkResult {
    pub fn is_v2(&self) -> bool {
        self.workload_version.starts_with("localsr-benchmark-v2")
    }

    pub fn retain_device_scores(&mut self, previous: Option<&Self>, completed_at: u64) {
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

impl Default for RuntimeStatus {
    fn default() -> Self {
        Self {
            worker: "starting".into(),
            active_job_id: String::new(),
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
        }
    }
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
}

#[derive(Clone, Debug, Deserialize)]
pub struct StartBenchmarkInput {
    pub device: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct VideoComparisonSources {
    pub playback_note: String,
    pub original_path: String,
    pub enhanced_path: String,
    pub original_url: Option<String>,
    pub enhanced_url: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct StartBatchInput {
    pub media_ids: Vec<String>,
    pub batch_mode: bool,
    pub task: String,
    pub model_id: String,
    pub video_model_id: String,
    pub preprocess_model_id: String,
    pub custom_model_path: String,
    pub output_directory: String,
    pub output_format: String,
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
    #[serde(default = "default_low_memory")]
    pub video_low_memory: bool,
    pub video_crf: u32,
    pub enable_face_model: bool,
    pub face_fidelity: u32,
    pub enable_live_preview: bool,
    pub allow_unsafe_pickle_model: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WorkerEnvelope {
    #[serde(rename = "type")]
    pub message_type: String,
    #[serde(default)]
    pub data: serde_json::Value,
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
