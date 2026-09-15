export type TaskKind = 'upscale' | 'denoise' | 'video';
export type MediaKind = 'image' | 'video' | 'unknown';
export type JobStatus =
  | 'queued'
  | 'starting'
  | 'running'
  | 'cancelling'
  | 'completed'
  | 'cancelled'
  | 'failed'
  | 'interrupted';

export interface CatalogModel {
  model_id: string;
  name: string;
  filename: string;
  description: string;
  size_bytes: number;
  sha256: string;
  download_url: string;
  architecture: string;
  native_scale: number;
  purposes: string[];
  quality_tier: number;
  speed_tier: number;
  recommended_halo: number;
  source_url: string;
  license_name: string;
  license_url: string;
  author: string;
  memory_factor: number;
  time_factor: number;
  speed_factor: number;
  vram_estimate_mb: number;
  pair_with: string;
  commercial_use_status: string;
  commercial_use_allowed: boolean | null;
  automated_download_allowed: boolean;
  redistribution_allowed: boolean;
  attribution_required: boolean;
  terms_acceptance_required: boolean;
  engine_id: string;
  support_tier: string;
  // Model-library fields (catalog schema 2). Optional so an older manifest or
  // a hand-built test model still type-checks; helpers derive fallbacks.
  rights_status?: RightsStatus;
  display_name?: string;
  role?: string;
  stage?: StageKind;
  fixes?: FixKind[];
  content?: ContentKind[];
  installed: boolean;
  installed_path?: string;
}

export type Quality = 'quick' | 'best' | 'custom';
export type ContentKind = 'photo' | 'illustration' | 'face';
export type FixKind = 'noise' | 'jpeg' | 'blur' | 'faces';
export type StageKind = 'upscale' | 'deblock' | 'restore' | 'face_restore';
export type RightsStatus = 'verified' | 'attribution' | 'unresolved' | 'non_commercial';

export interface ModelFile {
  role: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  download_url: string;
}

export interface CatalogVideoModel {
  model_id: string;
  name: string;
  description: string;
  family: string;
  engine_kind: string;
  files: ModelFile[];
  license_name: string;
  license_url: string;
  author: string;
  source_url: string;
  min_unified_memory_gb: number;
  min_vram_gb: number;
  temporal_window: number;
  temporal_overlap: number;
  commercial_use_allowed: boolean | null;
  automated_download_allowed: boolean;
  terms_acceptance_required: boolean;
  engine_id: string;
  support_tier: string;
  total_size_bytes: number;
  installed: boolean;
  installed_path?: string;
}

export interface CatalogManifest {
  schema_version: number;
  catalog_revision: string;
  models: CatalogModel[];
  video_models: CatalogVideoModel[];
}

export interface MediaItem {
  id: string;
  path: string;
  name: string;
  kind: MediaKind;
  width: number;
  height: number;
  frame_count: number;
  fps: number;
  duration_seconds: number;
  preview_data_url: string;
  probe_status: 'pending' | 'ready' | 'failed';
  hdr_format?: '' | 'HLG' | 'PQ';
  audio_warning?: string;
  probe_stage?: string;
  probe_started_at?: number;
  error: string;
  selected: boolean;
}

export interface JobRecord {
  id: string;
  media_id: string;
  media_name: string;
  media_kind: MediaKind;
  status: JobStatus;
  progress: number;
  output_path: string;
  error: string;
  created_at: number;
  work_key?: string;
  work_units?: number;
  elapsed_seconds?: number;
}

export interface DeviceInfo {
  id: string;
  type: string;
  name: string;
  total_memory: number;
  free_memory: number;
  supports_fp16: boolean;
  is_integrated: boolean;
  recommended_tile_sizes: number[];
}

/** Programs that open and write the formats LocalSR's own runtime omits. */
export interface MediaCapabilities {
  /** The operating system's codecs (macOS), reached through the localsr-media helper. */
  system_codecs?: {
    label: string;
    decode: string[];
    encode: string[];
    containers: string[];
  } | null;
  /** An FFmpeg found on this computer, used without being selected. */
  external_ffmpeg?: { detected: string[]; candidates: string[] };
}

export interface CapabilityInfo {
  system_ram_total: number;
  system_ram_available: number;
  system_memory_pressure_percent: number;
  system_memory_pressure_level: string;
  system_compressed_memory: number;
  system_swap_total: number;
  system_swap_used: number;
  devices: DeviceInfo[];
  media?: MediaCapabilities | null;
}

export interface EngineInfo {
  protocol_version: number;
  minimum_protocol_version: number;
  engine_id: string;
  engine_version: string;
  features: string[];
  model_formats: string[];
  video_engines: string[];
}

export interface Recipe {
  id: string;
  name: string;
  task: TaskKind;
  model_id: string;
  output_scale: number;
  tile_size: number;
  halo: number;
  precision: string;
  safe_memory: boolean;
  video_model_id?: string;
  custom_model_path?: string;
  output_format?: 'png' | 'jpg' | 'tif' | 'webp';
  preserve_metadata?: boolean;
  jpeg_quality?: number;
  deflicker?: boolean;
  deflicker_window?: number;
  video_hdr_mode?: 'tone_map' | 'preserve';
  video_target_resolution?: number;
  video_low_memory?: boolean;
  video_container?: 'mp4' | 'mkv';
  video_codec?: VideoCodec | '';
  video_crf?: number;
  enable_face_model?: boolean;
  face_fidelity?: number;
  stages?: RecipeStage[];
  quality?: Quality | '';
  content?: Exclude<ContentKind, 'face'> | '';
  fixes?: FixKind[];
}

/** AV1/VP9/FFV1 are bundled and royalty-free; H.264/HEVC use the user's own FFmpeg. */
export type VideoCodec = 'av1' | 'vp9' | 'ffv1' | 'h264' | 'hevc';

export interface RecipeStage {
  kind: 'deblock' | 'restore' | 'upscale' | 'face_restore' | 'video';
  model_id: string;
  fidelity?: number;
}

export interface UiSettings {
  interface_scale: 100 | 110 | 125;
  batch_mode: boolean;
  task: TaskKind | '';
  selected_model_id: string;
  selected_video_model_id: string;
  preprocess_model_id: string;
  custom_model_path: string;
  output_scale: number;
  output_directory: string;
  output_format: 'png' | 'jpg' | 'tif' | 'webp';
  preserve_metadata: boolean;
  jpeg_quality: number;
  device_id: string;
  tile_size: number;
  halo: number;
  precision: string;
  safe_memory: boolean;
  deflicker: boolean;
  deflicker_window: number;
  video_hdr_mode?: 'tone_map' | 'preserve';
  video_target_resolution?: number;
  video_low_memory?: boolean;
  video_container: 'mp4' | 'mkv';
  video_codec: VideoCodec;
  external_ffmpeg_path: string;
  video_crf: number;
  /** Direct builds send version, platform and channel after an update check. Defaults to true. */
  anonymous_update_count?: boolean;
  enable_face_model: boolean;
  face_fidelity: number;
  enable_live_preview: boolean;
  allow_unsafe_pickle_model: boolean;
  // Model-library state. `quality` is the active recipe ('' before any choice,
  // 'custom' after a manual pick); `preset_pins` maps a slot such as
  // "upscale/photo/best" to the checkpoint that slot is pinned to.
  quality: Quality | '';
  content: Exclude<ContentKind, 'face'>;
  fixes: FixKind[];
  preset_pins: Record<string, string>;
}

export interface VideoMemoryStatus {
  job_id: string;
  device: string;
  stage: string;
  low_memory: boolean;
  clip_frames: number;
  vae_tile_size: number;
  blocks_to_swap: number;
  offload_tensors: boolean;
  output_width: number;
  output_height: number;
  gpu_sample_available: boolean;
  shared_memory: boolean;
  device_total_memory: number;
  device_free_memory: number;
  device_allocated_memory: number;
  device_reserved_memory: number;
  device_peak_memory: number;
  system_ram_available: number;
  process_ram: number;
  elapsed_seconds: number;
  oom: boolean;
}

export interface RuntimeStatus {
  video_memory?: VideoMemoryStatus;
  worker: 'starting' | 'negotiating' | 'ready' | 'unavailable' | 'failed';
  active_job_id: string;
  status_title: string;
  status_detail: string;
  progress: number;
  last_output_path: string;
  result_preview_data_url: string;
  comparison_media_id?: string;
  comparison_output_path?: string;
  comparison_preview_data_url?: string;
  comparison_error?: string;
  download_model_id: string;
  download_progress: number;
  elapsed_seconds: number;
  estimated_remaining_seconds: number;
  throughput: number;
  throughput_unit: string;
  active_tile_size: number;
  device_free_memory: number;
  device_allocated_memory: number;
  live_system_ram_available: number;
  live_memory_pressure_percent: number;
  thermal_status: string;
}

export interface BenchmarkRender {
  device: string;
  scene_id: string;
  input_width: number;
  input_height: number;
  output_width: number;
  output_height: number;
  input_data_url: string;
  output_data_url: string;
}

export interface BenchmarkSceneResult {
  scene_id: string;
  purpose: string;
  input_width: number;
  input_height: number;
  output_width: number;
  output_height: number;
  iterations: number;
  median_ms: number;
  p05_ms: number;
  p95_ms: number;
  cv_percent: number | null;
  megapixels_per_second: number;
  encode_ms?: number | null;
  preview?: BenchmarkRender | null;
}

export interface BenchmarkDeviceResult {
  completed_at_unix?: number;
  device: string;
  device_type: string;
  device_name: string;
  thermal_state: string;
  warmup_iterations: number;
  peak_memory_bytes?: number | null;
  peak_device_memory_bytes?: number | null;
  stable: boolean;
  cv_percent: number | null;
  score: number;
  scenes: BenchmarkSceneResult[];
}

export interface BenchmarkResult {
  workload_version: string;
  backend: string;
  device: string;
  model_id: string;
  model_name: string;
  scale: number;
  input_width: number;
  input_height: number;
  warmup_count: number;
  measured_frame_count: number;
  median_inference_ms: number;
  p95_inference_ms: number;
  end_to_end_fps: number;
  processed_megapixels_per_second: number;
  total_elapsed_seconds: number;
  peak_memory_bytes: number | null;
  score: number;
  device_results?: BenchmarkDeviceResult[];
  device_history?: BenchmarkDeviceResult[];
  system_score?: number | null;
  cpu_score?: number | null;
  stable?: boolean;
  cv_percent?: number | null;
  result_elapsed_seconds?: number;
  thermal_state?: string;
  reference_label?: string | null;
  reference_ratio?: number | null;
}

export interface VideoComparisonProgress {
  request_id: string;
  media_id: string;
  stage: string;
  frame: number;
  total: number;
  elapsed_seconds: number;
}

export interface VideoComparisonSources {
  playback_note?: string;
  original_url: string;
  enhanced_url: string;
}

export interface AppSnapshot {
  app_version: string;
  protocol_version: number;
  catalog: CatalogManifest;
  media: MediaItem[];
  jobs: JobRecord[];
  settings: UiSettings;
  recipes: Recipe[];
  capabilities: CapabilityInfo;
  engine: EngineInfo | null;
  runtime: RuntimeStatus;
  latest_benchmark: BenchmarkResult | null;
}

export interface WorkerEnvelope {
  type: string;
  data: Record<string, unknown>;
}

export interface StartBatchInput {
  media_ids: string[];
  batch_mode: boolean;
  task: TaskKind;
  model_id: string;
  video_model_id: string;
  preprocess_model_id: string;
  custom_model_path: string;
  output_directory: string;
  output_format: string;
  output_scale: number;
  preserve_metadata: boolean;
  jpeg_quality: number;
  device: string;
  tile_size: number;
  halo: number;
  precision: string;
  safe_memory: boolean;
  deflicker: boolean;
  deflicker_window: number;
  video_hdr_mode?: 'tone_map' | 'preserve';
  video_target_resolution?: number;
  video_low_memory?: boolean;
  video_container: string;
  video_codec: string;
  external_ffmpeg_path: string;
  video_crf: number;
  enable_face_model: boolean;
  face_fidelity: number;
  enable_live_preview: boolean;
  allow_unsafe_pickle_model: boolean;
}

export interface LaunchIntent {
  files: string[];
  preset: 'quick' | 'best' | null;
  recipe: string | null;
  auto_start: boolean;
}

export interface IntegrationStatus {
  platform: string;
  installed: boolean;
  summary: string;
  command_name: string;
}
