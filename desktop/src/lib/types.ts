export type TaskKind = 'upscale' | 'denoise' | 'edit' | 'video';
export type SettingsSection = 'general' | 'appearance' | 'performance' | 'storage' | 'privacy';
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
  /** False: the library offers it, but no preset picks it on its own. */
  preset_eligible?: boolean;
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
export type FixKind = 'noise' | 'jpeg' | 'blur' | 'colour' | 'faces';
export type StageKind = 'upscale' | 'deblock' | 'restore' | 'face_restore';
export type RightsStatus = 'verified' | 'attribution' | 'unresolved' | 'non_commercial';

export interface ModelFile {
  role: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  download_url: string;
}

/** How an edit samples (the engine's words): `lightning` is Quick, `full` is Best. */
export type EditSpeed = 'full' | 'lightning';

/** An editing bundle, or with `kind` a speed add-on of its family (`EditSetup.addons`). */
export interface EditModel {
  model_id: string;
  /** Set on a speed add-on only; an add-on is never a model to edit with. */
  kind?: 'speed_addon';
  /** The speed an add-on gives. */
  speed?: EditSpeed;
  name: string;
  family: string;
  quantization: string;
  files: ModelFile[];
  min_unified_memory_gb: number;
  min_vram_gb: number;
  default_steps: number;
  cfg_scale: number;
  author?: string;
  license_name: string;
  license_url: string;
  source_url: string;
  commercial_use_allowed?: boolean | null;
  attribution_required?: boolean;
  terms_acceptance_required: boolean;
  automated_download_allowed: boolean;
  installed: boolean;
  total_size_bytes: number;
}

export interface CustomEditModel {
  family: string;
  diffusion_path: string;
  text_encoder_path: string;
  vision_path: string;
  vae_path: string;
}

export interface EditSetup {
  models: EditModel[];
  /** Speed add-ons (the Lightning LoRA) with their install state; absent from older hosts. */
  addons?: EditModel[];
  runtime_path: string;
  custom_model?: EditModel | null;
}

export type EditPlacement = 'auto' | 'gpu' | 'cpu';

export interface StartEditInput {
  media_id: string;
  model_id: string;
  prompt: string;
  runtime_path: string;
  device: string;
  max_dimension: number;
  steps: number;
  seed: number;
  accepted_terms: boolean;
  output_directory: string;
  edit_text_encoder?: EditPlacement;
  edit_vae?: EditPlacement;
  edit_vram_limit_mb?: number;
  edit_allow_any_size?: boolean;
  /** `full` when absent. */
  speed?: EditSpeed;
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
  summary?: string;
  /** The library's dots, rated for video models only: quality 1–4, speed 0–3. */
  quality_tier?: number;
  speed_tier?: number;
  commercial_use_allowed: boolean | null;
  automated_download_allowed: boolean;
  redistribution_allowed?: boolean;
  attribution_required?: boolean;
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
  /** Optional frame-interpolation stages of a video job; never video engines. */
  interpolation_models: CatalogVideoModel[];
  /** Optional speech audio denoising stages. */
  audio_models?: CatalogVideoModel[];
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
  has_audio?: boolean;
  /** What the probe says about interlacing; empty for a photo and for a file probed by an older build. */
  field_order?: '' | 'progressive' | 'tff' | 'bff' | 'unknown';
  /** Strokes of Dust & scratches painted on this photo that paint; the strokes stay in the host. */
  repair_marks?: number;
  /** Counts each save of the paint, so a view can tell when it changed. */
  repair_rev?: number;
  probe_stage?: string;
  probe_started_at?: number;
  error: string;
  selected: boolean;
}

export interface JobRecord {
  /** The primary model of the recorded request, and its catalog name. */
  model_id?: string;
  model_label?: string;
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
  /** What one work unit typically took in comparable earlier runs; 0 or missing until two ran. */
  seconds_per_unit?: number;
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
  /** Apple GPU entries: supported models can also run through Core ML on the Neural Engine. */
  neural_engine?: boolean;
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
  /** Still-image formats the system writes beyond PNG, JPEG, TIFF and WebP (HEIC on macOS). */
  image_output?: string[];
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
  gpu_suggestion?: { name: string; download: 'DirectML' | 'CUDA' | 'ROCm' | 'intel-driver' } | null;
  /** An Intel NPU the machine has but cannot use yet (its compiler is missing). */
  npu_hint?: { name: string; missing: 'compiler' } | null;
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
  video_task?: 'upscale' | 'denoise' | '';
  model_id: string;
  output_scale: number;
  tile_size: number;
  halo: number;
  precision: string;
  safe_memory: boolean;
  video_model_id?: string;
  custom_model_path?: string;
  output_format?: 'png' | 'jpg' | 'tif' | 'webp' | 'heic';
  output_bit_depth?: 8 | 16;
  preserve_metadata?: boolean;
  jpeg_quality?: number;
  deflicker?: boolean;
  deflicker_window?: number;
  video_hdr_mode?: 'tone_map' | 'preserve';
  video_target_resolution?: number;
  video_low_memory?: boolean;
  video_container?: 'mp4' | 'mkv' | 'mov';
  video_codec?: VideoCodec | '';
  video_crf?: number;
  enable_face_model?: boolean;
  face_fidelity?: number;
  stages?: RecipeStage[];
  quality?: Quality | '';
  content?: Exclude<ContentKind, 'face'> | '';
  fixes?: FixKind[];
  video_interpolation?: InterpolationTarget | '';
  video_interpolation_order?: InterpolationOrder | '';
  video_slow_motion?: boolean;
  video_clean_audio?: boolean;
  video_audio_model?: string;
}

/** AV1/VP9/FFV1 are bundled and royalty-free; H.264/HEVC use the user's own FFmpeg. */
export type VideoCodec = 'auto' | 'av1' | 'vp9' | 'ffv1' | 'h264' | 'hevc' | 'prores';

/** One fix in run order; in Restore the last one is the model itself. */
export interface FixStageSelection {
  fix: Exclude<FixKind, 'faces'>;
  /** Empty while no catalog model fixes this problem; Start then waits. */
  model_id: string;
  params?: Record<string, unknown>;
  mask_path?: string;
}

/** A registry step the job adds by id beside its fixes (`steps.ts` `stepStage`). */
export interface StepStageSelection {
  step: string;
  params?: Record<string, unknown>;
  mask_path?: string;
  /** The catalog model of a step that needs one; absent while none is chosen. */
  model_id?: string;
}

/** Frame interpolation: a frame-rate multiplier or a fixed output rate. */
export type InterpolationTarget = 'off' | 'x2' | 'x4' | 'fps60' | 'fps120';
export type InterpolationOrder = 'after_upscale' | 'before_upscale';

export interface RecipeStage {
  /** The problem a restoration stage fixes; recipes saved before several fixes omit it. */
  fix?: Exclude<FixKind, 'faces'>;
  kind: 'deblock' | 'restore' | 'upscale' | 'face_restore' | 'video';
  model_id: string;
  fidelity?: number;
  params?: Record<string, unknown>;
  mask_path?: string;
}

export interface UiSettings {
  appearance: 'dark' | 'light' | 'system';
  colour_blind_palette: boolean;
  contrast?: 'system' | 'standard' | 'high';
  viewer_background?: 'checker' | 'black' | 'grey' | 'white';
  /** When the preview patch updates; host-owned like `viewer_background`. */
  preview_patch?: PreviewPatchSetting;
  /** The remembered on/off of the viewer's Preview button. */
  preview_patch_shown?: boolean;
  accent?: 'system' | 'localsr';
  interface_scale: 100 | 110 | 125;
  batch_mode: boolean;
  task: TaskKind | '';
  video_task?: 'upscale' | 'denoise';
  selected_model_id: string;
  selected_video_model_id: string;
  fix_stages: FixStageSelection[];
  step_stages?: StepStageSelection[];
  custom_model_path: string;
  custom_models?: { path: string; name: string }[];
  trusted_checkpoint_sha256?: string[];
  output_scale: number;
  output_directory: string;
  output_format: 'png' | 'jpg' | 'tif' | 'webp' | 'heic';
  output_bit_depth?: 8 | 16;
  preserve_metadata: boolean;
  jpeg_quality: number;
  device_id: string;
  tile_size: number;
  halo: number;
  precision: string;
  safe_memory: boolean;
  neural_engine?: boolean;
  deflicker: boolean;
  deflicker_window: number;
  video_hdr_mode?: 'tone_map' | 'preserve';
  video_target_resolution?: number;
  video_low_memory?: boolean;
  video_container: 'mp4' | 'mkv' | 'mov';
  video_codec: VideoCodec;
  /** Set once the old AV1 default moved to 'auto'; kept so it is not repeated. */
  automatic_video_format?: boolean;
  external_ffmpeg_path: string;
  video_crf: number;
  /** Off by default; the host fills both in for settings saved before 0.3.7. */
  video_interpolation?: InterpolationTarget;
  video_interpolation_order?: InterpolationOrder;
  /** Plays the added frames over 2 or 4 times the length, without sound. */
  video_slow_motion?: boolean;
  video_clean_audio?: boolean;
  video_audio_model?: string;
  /** Direct builds send version, platform and channel after an update check. Off unless turned on. */
  anonymous_update_count?: boolean;
  completion_sound?: boolean;
  check_updates_at_launch?: boolean;
  privacy_choice_made?: boolean;
  crash_reports?: boolean;
  skipped_update_version?: string;
  enable_face_model: boolean;
  face_fidelity: number;
  enable_live_preview: boolean;
  low_priority?: boolean;
  allow_unsafe_pickle_model: boolean;
  // Model-library state. `quality` is the active recipe ('' before any choice,
  // 'custom' after a manual pick); `preset_pins` maps a slot such as
  // "upscale/photo/best" to the checkpoint that slot is pinned to.
  quality: Quality | '';
  content: Exclude<ContentKind, 'face'>;
  fixes: FixKind[];
  preset_pins: Record<string, string>;
  recipe_pins?: string[];
  recipe_recent?: string[];
  // Image editing: the chosen Qwen bundle, the longest output edge in pixels
  // and the sampler settings. The prompt is kept in memory only.
  edit_model_id: string;
  edit_max_dimension: number;
  edit_steps: number;
  edit_seed: number;
  edit_text_encoder?: EditPlacement;
  edit_vae?: EditPlacement;
  edit_vram_limit_mb?: number;
  edit_allow_any_size?: boolean;
  /** The quality the user chose for Qwen edits; absent until then (see `editSpeed`). */
  edit_speed?: EditSpeed;
  custom_edit_model?: CustomEditModel;
  model_directory?: string;
  gpu_download_notice_dismissed?: boolean;
  linux_menu_prompt_dismissed?: boolean;
  show_in_menu_bar?: boolean;
  notify_on_finish?: boolean;
  menu_bar_icon_monochrome?: boolean;
  auto_cache_cleanup?: boolean;
  keep_awake?: boolean;
  stopped_job_result?: 'ask' | 'keep' | 'discard';
}

export interface ModelMoveProgress {
  moved_bytes: number;
  total_bytes: number;
  moved_files: number;
  total_files: number;
  progress: number;
  current_file: string;
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

export interface ModelDownloadStatus {
  model_id: string;
  status: 'queued' | 'downloading';
  progress: number;
  downloaded_bytes?: number;
  total_bytes?: number;
  /** Sent by 0.7.0 hosts; older hosts leave it out. */
  phase?: 'downloading' | 'verifying';
  /** Bytes that arrived over the network in this run, without a resumed part. */
  received_bytes?: number;
}

/** One step of the running photo job, as the host fills it (`types.rs`, `JobStep`). */
export interface JobStep {
  kind: string;
  fix: string;
  model_id: string;
  state: 'waiting' | 'running' | 'done' | 'skipped';
  /** Why the engine skipped the step. */
  note: string;
  /** The step's finished picture; empty until it arrived. */
  preview_url: string;
  /** A full-size file of the step's picture exists. */
  kept: boolean;
}

export interface LastSteps {
  job_id: string;
  media_id: string;
  steps: JobStep[];
}

export type PreviewPatchSetting = 'auto' | 'live' | 'manual' | 'off';

/** A square of the source picture, in source pixels. */
export interface PatchRegion {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PatchStep {
  kind: string;
  fix: string;
  model_id: string;
  state: 'waiting' | 'running' | 'done' | 'skipped' | 'missing';
  /** The picture's address in the preview folder; empty until the step is done. */
  url: string;
  width: number;
  height: number;
  seconds: number;
  /** The engine's reason for a skipped step. */
  note: string;
}

export interface PatchState {
  state: 'idle' | 'running' | 'ready' | 'stale' | 'busy' | 'failed';
  media_id: string;
  region: PatchRegion | null;
  /** 0 when unknown. */
  estimate_seconds: number;
  error: string;
  steps: PatchStep[];
}

/**
 * Variant A is the plan as it stands; B swaps one slot's model for comparison.
 * @public Sent by the two-model comparison (task P7).
 */
export interface PatchReplace {
  slot: 'a' | 'b';
  fix: string;
  model_id: string;
}

export interface RuntimeStatus {
  /** Sent when the engine lists `patch_preview`. */
  patches?: { a: PatchState; b: PatchState };
  comparison_pair?: ComparisonPair;
  preview_original?: PreviewOriginalRegion;
  compare?: CompareState;
  video_memory?: VideoMemoryStatus;
  worker: 'starting' | 'negotiating' | 'ready' | 'unavailable' | 'failed';
  /** The host gave up restarting the engine; `retryEngine` starts it again. */
  engine_retry?: boolean;
  active_job_id: string;
  active_batch_job_ids?: string[];
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
  downloads?: ModelDownloadStatus[];
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
  /** What the engine said about the hardware route during this job; outlives progress ticks
   *  (worker.rs keeps it in the host's snapshot until the job ends). */
  hardware_note: string;
  /** How long a cancelled job may take to stop before the engine is restarted. */
  cancel_grace_seconds?: number;
  queue_paused?: boolean;
  frames_processed?: number;
  total_frames?: number;
  /** Tiles of the running photo stage that are finished, counted here from its progress. */
  tiles_completed?: number;
  tiles_total?: number;
  can_keep_partial?: boolean;
  /** The running photo job's steps; empty outside a job of two or more steps. */
  job_steps?: JobStep[];
  last_steps?: LastSteps | null;
}

export interface BenchmarkRender {
  device: string;
  scene_id: string;
  model_name?: string;
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
  seconds_per_input_mp?: number | null;
  encode_ms?: number | null;
  preview?: BenchmarkRender | null;
}

export interface BenchmarkDeviceResult {
  completed_at_unix?: number;
  device: string;
  device_type: string;
  device_name: string;
  preset?: string;
  model_id?: string;
  model_name?: string;
  thermal_state: string;
  warmup_iterations: number;
  peak_memory_bytes?: number | null;
  peak_device_memory_bytes?: number | null;
  stable: boolean;
  cv_percent: number | null;
  score: number;
  seconds_per_input_mp?: number | null;
  matched_cpu?: boolean | null;
  psnr_db?: number | null;
  status?: string;
  status_note?: string | null;
  recommended?: boolean;
  scenes: BenchmarkSceneResult[];
}

export interface CpuThreadBenchmark {
  physical_cores: number;
  logical_cores: number;
  recommended_threads: number;
  tested?: boolean;
  physical_seconds_per_input_mp?: number | null;
  logical_seconds_per_input_mp?: number | null;
  model_id?: string | null;
  model_name?: string | null;
}

export interface MissingPresetModel {
  preset: string;
  preset_label: string;
  model_id: string;
  model_name: string;
}

export interface BenchmarkedModelSummary {
  preset: string;
  preset_label: string;
  model_id: string;
  model_name: string;
  scale: number;
  recommended_device?: string | null;
  recommended_device_name?: string | null;
  recommended_seconds_per_input_mp?: number | null;
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
  recommended_device?: string | null;
  recommended_device_name?: string | null;
  recommended_device_seconds_per_input_mp?: number | null;
  recommended_cpu_threads?: number | null;
  cpu_threads?: CpuThreadBenchmark | null;
  cpu_avx2?: boolean | null;
  cpu_memory_format?: string | null;
  missing_presets?: MissingPresetModel[];
  benchmarked_models?: BenchmarkedModelSummary[];
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
  /** Empty while the video has no finished result; only the source plays. */
  enhanced_url: string;
}

export interface CompareVideoSlot {
  x: number;
  y: number;
  width: number;
  height: number;
  fps: number;
  duration: number;
  frames: number;
  audio_url: string;
}

export interface CompareVideoSources {
  playback_note?: string;
  video_url: string;
  width: number;
  height: number;
  duration: number;
  slots: CompareVideoSlot[];
}

export interface InstalledEngine {
  id: string;
  backend: string;
  bundled: boolean;
  active: boolean;
  path: string;
  needs_reinstall?: boolean;
}

export interface EngineRecommendation {
  backend: string;
  adapter_name?: string | null;
}

export interface EngineCatalogEntry {
  backend: string;
  engine_id: string;
  version: string;
  size: number;
  manifest_url: string;
  parts_count: number;
  installed: boolean;
  active: boolean;
}

export interface EngineDownloadStatus {
  stage: string;
  backend: string;
  engine_id: string;
  downloaded: number;
  total: number;
  error?: string | null;
}

export interface EngineManagerSnapshot {
  supported: boolean;
  managed_by_store: boolean;
  configured: boolean;
  active_backend: string;
  active_engine_id: string;
  bundled_backend: string;
  installed: InstalledEngine[];
  recommendation: EngineRecommendation;
  catalog: EngineCatalogEntry[];
  catalog_error?: string | null;
  download: EngineDownloadStatus;
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
  edit: EditSetup;
  default_model_directory?: string;
  model_directory_missing?: boolean;
  gpu_download_notice?: string | null;
  linux_menu_offer?: boolean;
  linux_menu_installed?: boolean;
  linux_menu_available?: boolean;
  engines?: EngineManagerSnapshot;
}

export interface WorkerEnvelope {
  type: string;
  data: Record<string, unknown>;
}

export interface StartBatchInput {
  ui?: ResultUi;
  media_ids: string[];
  batch_mode: boolean;
  task: TaskKind;
  video_task?: 'upscale' | 'denoise';
  model_id: string;
  video_model_id: string;
  fix_stages: FixStageSelection[];
  step_stages?: StepStageSelection[];
  custom_model_path: string;
  output_directory: string;
  output_format: string;
  output_bit_depth?: 8 | 16;
  output_scale: number;
  preserve_metadata: boolean;
  jpeg_quality: number;
  device: string;
  tile_size: number;
  halo: number;
  precision: string;
  safe_memory: boolean;
  neural_engine?: boolean;
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
  video_interpolation?: InterpolationTarget;
  video_interpolation_order?: InterpolationOrder;
  /** Plays the added frames over 2 or 4 times the length, without sound. */
  video_slow_motion?: boolean;
  video_clean_audio?: boolean;
  video_audio_model?: string;
}

/** The plan card's labels when a job was queued; host-only, never sent to the worker. */
export interface ResultUi {
  quality: string;
  content: string;
  recipe_name: string;
  fixes?: string[];
}
export interface ResultStage {
  kind: string;
  model_id: string;
  model_label: string;
}
/** One finished attempt of a media item, as `list_media_results` returns it. */
export interface ResultSummary {
  job_id: string;
  completed_at: number;
  status: 'completed' | 'failed' | 'cancelled' | 'interrupted' | string;
  kind: 'upscale' | 'restore' | 'edit' | 'video' | string;
  output_path: string;
  output_name: string;
  /** Current file size in bytes, or 0 when unavailable. */
  size_bytes?: number;
  output_duration_seconds?: number;
  output_fps?: number;
  /** Stopped before the end and kept: a video's first frames, or a photo's finished tiles. */
  partial?: boolean;
  /** Video: seconds kept (0.074 for two frames). */
  kept_seconds?: number;
  /** Video: frames kept. */
  kept_frames?: number;
  /** Photo: the finished tiles as [x, y, width, height] in pixels of the saved file. */
  finished_tiles?: [number, number, number, number][];
  /** Photo: the tiles the job had. */
  total_tiles?: number;
  /** Completed and its file exists. */
  available: boolean;
  model_id: string;
  model_label: string;
  scale: number;
  /** The written instruction of an Edit; empty for other kinds. */
  prompt: string;
  /** The longest output edge an Edit was asked for; 0 for other kinds. */
  max_dimension: number;
  stages: ResultStage[];
  notes: string;
  /** Run time including loading and saving; 0 when unknown. */
  elapsed_seconds: number;
  /** Requested device and Neural Engine preference, if recorded. */
  device?: string;
  neural_engine?: boolean;
  ui: ResultUi | null;
}
export interface MediaResults {
  media_id: string;
  /** Newest first, up to the requested page size. */
  results: ResultSummary[];
  total_count: number;
  completed_count: number;
  available_count: number;
  default_result_id: string | null;
  next_before: string | null;
}
export type ComparePick =
  { kind: 'original'; media_id: string } | { kind: 'output'; media_id: string; job_id: string };
export interface CompareSlot {
  pick: ComparePick;
  media_kind?: string;
  status: 'loading' | 'ready' | 'error' | 'missing';
  preview_data_url: string;
  error: string;
  width: number;
  height: number;
  preview_width: number;
  preview_height: number;
  region?: import('./compareRegions').RegionResult | null;
}
export interface CompareState {
  generation: number;
  slots: CompareSlot[];
}
/** "original" or the job id of a saved result. */
export type ResultTarget = string;
export interface PairChoice {
  showing: ResultTarget;
  against: ResultTarget;
  /** A manual choice; it holds through later completions for this session. */
  pinned: boolean;
}
export interface PreviewOriginalRegion {
  media_id: string;
  region?: import('./compareRegions').RegionResult | null;
}
export interface ComparisonSlot {
  target_id: string;
  preview_data_url: string;
  error?: string;
  width?: number;
  height?: number;
  preview_width?: number;
  preview_height?: number;
  region?: import('./compareRegions').RegionResult | null;
}
export interface ComparisonPair {
  media_id: string;
  generation: number;
  showing: ComparisonSlot;
  against: ComparisonSlot;
}
export interface TrialEstimate {
  model_id: string;
  model_label: string;
  seconds: number | null;
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
