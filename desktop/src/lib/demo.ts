import rawCatalog from '../../src-tauri/resources/model-catalog.json';
import type { AppSnapshot, CatalogManifest } from './types';

const catalog = structuredClone(rawCatalog) as unknown as CatalogManifest;
for (const model of catalog.models) {
  model.installed = false;
}
for (const model of catalog.video_models) {
  model.total_size_bytes = model.files.reduce((total, file) => total + file.size_bytes, 0);
  model.installed = false;
}

export function demoSnapshot(): AppSnapshot {
  return {
    app_version: '0.0.11-alpha · Next Preview',
    protocol_version: 1,
    catalog,
    media: [],
    jobs: [],
    settings: {
      interface_scale: 100,
      batch_mode: false,
      task: '',
      selected_model_id: '',
      selected_video_model_id: 'frame_by_frame',
      preprocess_model_id: '',
      custom_model_path: '',
      output_scale: 4,
      output_directory: '',
      output_format: 'png',
      preserve_metadata: true,
      jpeg_quality: 98,
      device_id: 'cpu',
      tile_size: 256,
      halo: 32,
      precision: 'fp32',
      safe_memory: false,
      deflicker: false,
      deflicker_window: 3,
      video_container: 'mp4',
      video_codec: 'av1',
      external_ffmpeg_path: '',
      video_hdr_mode: 'tone_map',
      video_crf: 18,
      enable_face_model: false,
      face_fidelity: 70,
      enable_live_preview: true,
      allow_unsafe_pickle_model: false,
      quality: '',
      content: 'photo',
      fixes: [],
      preset_pins: {},
    },
    recipes: [],
    capabilities: {
      system_ram_total: 0,
      system_ram_available: 0,
      system_memory_pressure_percent: 0,
      system_memory_pressure_level: 'unknown',
      system_compressed_memory: 0,
      system_swap_total: 0,
      system_swap_used: 0,
      devices: [
        {
          id: 'cpu',
          type: 'cpu',
          name: 'CPU · launch with Tauri to detect hardware',
          total_memory: 0,
          free_memory: 0,
          supports_fp16: false,
          is_integrated: false,
          recommended_tile_sizes: [64, 128, 192, 256],
        },
      ],
    },
    engine: null,
    runtime: {
      worker: 'unavailable',
      active_job_id: '',
      status_title: 'Interface preview',
      status_detail: 'Run npm run tauri dev to connect the inference worker.',
      progress: 0,
      last_output_path: '',
      result_preview_data_url: '',
      download_model_id: '',
      download_progress: 0,
      elapsed_seconds: 0,
      estimated_remaining_seconds: 0,
      throughput: 0,
      throughput_unit: '',
      active_tile_size: 0,
      device_free_memory: 0,
      device_allocated_memory: 0,
      live_system_ram_available: 0,
      live_memory_pressure_percent: 0,
      thermal_status: 'Not exposed by this backend',
    },
    latest_benchmark: null,
  };
}
