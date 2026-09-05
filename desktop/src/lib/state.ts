import { writable } from 'svelte/store';
import { demoSnapshot } from './demo';
import type { AppSnapshot, CatalogModel, TaskKind, WorkerEnvelope } from './types';

export const appState = writable<AppSnapshot>(demoSnapshot());

export function modelsForTask(snapshot: AppSnapshot, task: TaskKind | ''): CatalogModel[] {
  if (task === 'upscale') {
    return snapshot.catalog.models.filter(
      (model) => model.native_scale > 1 && !model.purposes.includes('face')
    );
  }
  if (task === 'denoise') {
    return snapshot.catalog.models.filter((model) => model.native_scale === 1);
  }
  if (task === 'video') {
    return snapshot.catalog.models.filter(
      (model) => model.native_scale > 1 && !model.purposes.includes('face')
    );
  }
  return [];
}

export function choosePresetModel(
  snapshot: AppSnapshot,
  task: TaskKind,
  quality: 'quick' | 'best'
): CatalogModel | undefined {
  const candidates = modelsForTask(snapshot, task);
  return [...candidates].sort((left, right) => {
    if (quality === 'quick') {
      return (
        right.speed_tier - left.speed_tier ||
        right.speed_factor - left.speed_factor ||
        left.memory_factor - right.memory_factor ||
        left.size_bytes - right.size_bytes
      );
    }
    return (
      right.quality_tier - left.quality_tier ||
      right.size_bytes - left.size_bytes ||
      right.speed_tier - left.speed_tier
    );
  })[0];
}

export function resultPreviewForSelectedMedia(snapshot: AppSnapshot): string {
  if (!snapshot.runtime.result_preview_data_url) return '';
  // Progressive tiles and video-frame thumbnails are useful while a job is
  // running, but they are not a completed result and must never enable the
  // before/after comparison. This mirrors the released Slint UI's separate
  // `live_result_ready` and `result_ready` states.
  if (snapshot.runtime.active_job_id) return '';
  const selected = snapshot.media.find((media) => media.selected) ?? snapshot.media[0];
  if (!selected) return '';

  const owner = snapshot.jobs.find(
    (job) =>
      job.status === 'completed' &&
      Boolean(snapshot.runtime.last_output_path) &&
      job.output_path === snapshot.runtime.last_output_path
  );
  return owner?.media_id === selected.id ? snapshot.runtime.result_preview_data_url : '';
}

export function applyWorkerEnvelope(snapshot: AppSnapshot, envelope: WorkerEnvelope): AppSnapshot {
  // Tiles carry display-only pixels and coordinates. Returning the existing
  // snapshot avoids cloning media thumbnails, model metadata and job history
  // twice per tile; App.svelte composites the pixels on its bounded canvas.
  if (envelope.type === 'tile_update') return snapshot;

  // Progress events can be frequent as well, but only mutate runtime fields.
  // A shallow immutable update keeps Svelte reactive without copying the full
  // application state on every inference step.
  const next = [
    'progress',
    'video_frame_started',
    'video_frame_completed'
  ].includes(envelope.type)
    ? { ...snapshot, runtime: { ...snapshot.runtime } }
    : structuredClone(snapshot);
  const data = envelope.data;
  switch (envelope.type) {
    case 'worker_ready':
      next.runtime.worker = 'negotiating';
      next.runtime.status_title = 'Checking engine';
      next.runtime.status_detail = 'Negotiating a compatible inference protocol.';
      break;
    case 'engine_info':
      next.engine = data as unknown as AppSnapshot['engine'];
      next.runtime.worker = 'ready';
      break;
    case 'capabilities_info':
      next.capabilities = data as unknown as AppSnapshot['capabilities'];
      break;
    case 'job_started':
      next.runtime.active_job_id = String(data.job_id ?? '');
      next.runtime.status_title = 'Processing';
      next.runtime.status_detail = 'The model is preparing the first tile.';
      next.runtime.progress = 0;
      next.runtime.elapsed_seconds = 0;
      next.runtime.estimated_remaining_seconds = 0;
      next.runtime.throughput = 0;
      next.runtime.throughput_unit = 'tiles/s';
      next.runtime.active_tile_size = 0;
      next.runtime.result_preview_data_url = '';
      break;
    case 'stage_started': {
      const labels: Record<string, string> = {
        deblock: 'Removing JPEG artifacts',
        restore: 'Restoring',
        upscale: 'Upscaling',
        face_restore: 'Restoring faces'
      };
      const kind = String(data.stage_kind ?? '');
      next.runtime.status_title = `${labels[kind] ?? 'Processing'} · stage ${Number(data.stage_index ?? 0) + 1}/${Number(data.stage_count ?? 1)}`;
      next.runtime.status_detail = `Preparing ${String(data.model_id ?? 'model')}.`;
      break;
    }
    case 'stage_completed':
      next.runtime.status_detail = `${String(data.stage_kind ?? 'Processing').replace('_', ' ')} stage complete.`;
      break;
    case 'progress':
      next.runtime.progress = Number(data.percentage ?? 0);
      next.runtime.status_title = 'Enhancing';
      next.runtime.status_detail = `${Number(data.completed_tiles ?? 0)} of ${Number(data.total_tiles ?? 0)} tiles${Number(data.estimated_remaining_seconds ?? 0) > 0 ? ` · ETA ${formatDuration(Number(data.estimated_remaining_seconds))}` : ''}`;
      next.runtime.elapsed_seconds = Number(data.elapsed_seconds ?? 0);
      next.runtime.estimated_remaining_seconds = Number(data.estimated_remaining_seconds ?? 0);
      next.runtime.throughput = next.runtime.elapsed_seconds > 0
        ? Number(data.completed_tiles ?? 0) / next.runtime.elapsed_seconds
        : 0;
      next.runtime.throughput_unit = 'tiles/s';
      next.runtime.active_tile_size = Number(data.active_tile_size ?? 0);
      next.runtime.device_free_memory = Number(data.device_free_memory ?? 0);
      next.runtime.device_allocated_memory = Number(data.device_allocated_memory ?? 0);
      next.runtime.live_system_ram_available = Number(data.system_ram_available ?? 0);
      next.runtime.live_memory_pressure_percent = Number(data.system_memory_pressure_percent ?? 0);
      break;
    case 'video_frame_started':
      next.runtime.status_title = 'Enhancing video · Labs';
      next.runtime.status_detail = `Frame ${Number(data.frame_index ?? 0) + 1} of ${Number(data.total_frames ?? 0) || '?'}`;
      break;
    case 'video_frame_completed': {
      const done = Number(data.frames_processed ?? 0);
      const total = Number(data.total_frames ?? 0);
      if (done > 0 && total > 0) next.runtime.progress = (done / total) * 100;
      const elapsed = Number(data.elapsed_seconds ?? 0);
      const remaining = Number(data.estimated_remaining_seconds ?? 0);
      next.runtime.status_title = 'Enhancing video · Labs';
      next.runtime.status_detail = `Frame ${done} of ${total || '?'}${remaining > 0 ? ` · ETA ${formatDuration(remaining)}` : ''}`;
      if (done > 0 && elapsed > 0) {
        next.runtime.elapsed_seconds = elapsed;
        next.runtime.estimated_remaining_seconds = remaining;
        next.runtime.throughput = done / elapsed;
        next.runtime.throughput_unit = 'frames/s';
      }
      if (typeof data.jpeg_base64 === 'string' && data.jpeg_base64) {
        next.runtime.result_preview_data_url = `data:image/jpeg;base64,${data.jpeg_base64}`;
      }
      break;
    }
    case 'live_preview_frame':
      if (
        String(data.job_id ?? '') === next.runtime.active_job_id &&
        String(data.preview_kind ?? '') === 'video' &&
        typeof data.jpeg_base64 === 'string' &&
        data.jpeg_base64
      ) {
        next.runtime.result_preview_data_url = `data:image/jpeg;base64,${data.jpeg_base64}`;
      }
      break;
    case 'benchmark_started':
      next.runtime.active_job_id = String(data.job_id ?? '');
      next.runtime.status_title = 'Benchmark running';
      next.runtime.status_detail = String(data.workload_version ?? '').startsWith(
        'localsr-benchmark-v2'
      )
        ? `Multi-device benchmark · ${Number(data.measured_frame_count ?? 0)} phases · warming up each device`
        : `Warming up ${Number(data.warmup_count ?? 0)} iteration${Number(data.warmup_count ?? 0) === 1 ? '' : 's'} · then measuring ${Number(data.measured_frame_count ?? 0)} frames`;
      next.runtime.progress = 0;
      break;
    case 'benchmark_progress':
      next.runtime.progress = Number(data.percentage ?? 0);
      next.runtime.status_title = 'Benchmark running';
      next.runtime.status_detail = data.stage
        ? `Phase ${String(data.stage).replace(':', ' · ')} · ${Number(data.completed_frames ?? 0)} of ${Number(data.total_frames ?? 0)}`
        : `Measured ${Number(data.completed_frames ?? 0)} of ${Number(data.total_frames ?? 0)} frames`;
      break;
    case 'benchmark_stage_started':
    case 'benchmark_stage_progress':
    case 'benchmark_stage_completed': {
      const event = envelope.type.replace('benchmark_stage_', '');
      const stage = String(data.stage ?? '').replace(':', ' · ');
      const completed = Number(data.completed_units ?? 0);
      const total = Number(data.total_units ?? 0);
      next.runtime.progress = Number(data.percentage ?? 0);
      next.runtime.status_title = 'Benchmark running';
      next.runtime.status_detail =
        event === 'progress' && completed > 0
          ? `${stage} · iteration ${completed}${total > 0 ? ` of ${total}` : ''}`
          : `${stage} · ${event}`;
      break;
    }
    case 'benchmark_completed':
      next.runtime.active_job_id = '';
      next.runtime.progress = 100;
      next.runtime.status_title = 'Benchmark complete';
      if (typeof data.result === 'object' && data.result) {
        next.latest_benchmark = data.result as unknown as AppSnapshot['latest_benchmark'];
        const result = next.latest_benchmark;
        if (result?.workload_version.startsWith('localsr-benchmark-v2')) {
          next.runtime.status_detail = result.stable
            ? result.system_score != null
              ? `System score ${result.system_score.toFixed(2)} · CPU ${(result.cpu_score ?? 0).toFixed(2)} output MP/s · stable (${(result.cv_percent ?? 0).toFixed(1)}% spread)`
              : `CPU score ${(result.cpu_score ?? 0).toFixed(2)} output MP/s · stable (${(result.cv_percent ?? 0).toFixed(1)}% spread)`
            : `Unstable run (${(result.cv_percent ?? 0).toFixed(1)}% spread) — close background apps and retry`;
          next.runtime.elapsed_seconds =
            result.result_elapsed_seconds ?? result.total_elapsed_seconds;
          next.runtime.throughput = result.system_score ?? 0;
          next.runtime.throughput_unit = 'output MP/s';
          next.runtime.thermal_status = result.thermal_state ?? next.runtime.thermal_status;
        } else {
          next.runtime.status_detail = `Score ${result?.score.toFixed(2) ?? '—'} · local result saved`;
        }
      }
      break;
    case 'benchmark_cancelled':
      next.runtime.active_job_id = '';
      next.runtime.progress = 0;
      next.runtime.status_title = 'Benchmark cancelled';
      next.runtime.status_detail = 'No partial benchmark result was stored.';
      break;
    case 'benchmark_failed':
      next.runtime.active_job_id = '';
      next.runtime.progress = 0;
      next.runtime.status_title = 'Benchmark failed';
      next.runtime.status_detail = String(data.error_message ?? 'The benchmark could not finish.');
      break;
    case 'job_completed':
    case 'video_job_completed':
      next.runtime.active_job_id = '';
      // Drop a live video-frame thumbnail while the host decodes the final
      // output. This prevents a transient comparison slider over one frame.
      next.runtime.result_preview_data_url = '';
      next.runtime.progress = 100;
      next.runtime.status_title = 'Complete';
      next.runtime.status_detail = 'The output was written successfully.';
      next.runtime.last_output_path = String(data.output_path ?? '');
      next.runtime.elapsed_seconds = Math.max(
        Number(data.inference_seconds ?? 0),
        Number(data.elapsed_seconds ?? 0)
      );
      next.runtime.estimated_remaining_seconds = 0;
      break;
    case 'job_cancelled':
      next.runtime.active_job_id = '';
      next.runtime.status_title = 'Cancelled';
      next.runtime.status_detail = 'Temporary output was cleaned up.';
      break;
    case 'job_failed':
      next.runtime.active_job_id = '';
      next.runtime.status_title = 'Could not finish';
      next.runtime.status_detail = String(data.error_message ?? 'Unknown worker error.');
      break;
    case 'warning':
      next.runtime.status_title = 'Warning';
      next.runtime.status_detail = String(data.message ?? 'The engine reported a warning.');
      break;
    case 'protocol_error':
      next.runtime.worker = 'failed';
      next.runtime.status_title = 'Engine incompatible';
      next.runtime.status_detail = String(data.message ?? 'Worker protocol mismatch.');
      break;
    case 'download-started':
      next.runtime.download_model_id = String(data.model_id ?? '');
      next.runtime.download_progress = 0;
      break;
    case 'download-progress':
      next.runtime.download_model_id = String(data.model_id ?? '');
      next.runtime.download_progress = Number(data.progress ?? 0);
      break;
    case 'download-completed':
    case 'download-cancelled':
    case 'download-failed':
      next.runtime.download_model_id = '';
      break;
  }
  return next;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index >= 3 ? 1 : 0)} ${units[index]}`;
}

export function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return '—';
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return minutes ? `${minutes}:${rest.toString().padStart(2, '0')}` : `${rest}s`;
}
