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
  installed: boolean;
  installed_path?: string;
}

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

export interface CapabilityInfo {
  system_ram_total: number;
  system_ram_available: number;
  system_memory_pressure_percent: number;
  system_memory_pressure_level: string;
  system_compressed_memory: number;
  system_swap_total: number;
  system_swap_used: number;
  devices: DeviceInfo[];
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
  output_format?: 'png' | 'jpg' | 'tif';
  preserve_metadata?: boolean;
  jpeg_quality?: number;
  deflicker?: boolean;
  deflicker_window?: number;
  video_container?: 'mp4' | 'mkv';
  video_crf?: number;
}

export interface UiSettings {
  interface_scale: 100 | 110 | 125;
  batch_mode: boolean;
  task: TaskKind | '';
  selected_model_id: string;
  selected_video_model_id: string;
  custom_model_path: string;
  output_scale: number;
  output_directory: string;
  output_format: 'png' | 'jpg' | 'tif';
  preserve_metadata: boolean;
  jpeg_quality: number;
  device_id: string;
  tile_size: number;
  halo: number;
  precision: string;
  safe_memory: boolean;
  deflicker: boolean;
  deflicker_window: number;
  video_container: 'mp4' | 'mkv';
  video_crf: number;
  enable_face_model: boolean;
  allow_unsafe_pickle_model: boolean;
}

export interface RuntimeStatus {
  worker: 'starting' | 'negotiating' | 'ready' | 'unavailable' | 'failed';
  active_job_id: string;
  status_title: string;
  status_detail: string;
  progress: number;
  last_output_path: string;
  result_preview_data_url: string;
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
  video_container: string;
  video_crf: number;
  enable_face_model: boolean;
  allow_unsafe_pickle_model: boolean;
}

export interface DownloadProgress {
  model_id: string;
  downloaded_bytes: number;
  total_bytes: number;
  progress: number;
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
