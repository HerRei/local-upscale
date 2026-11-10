import { formatDuration } from './format';
import type { AppSnapshot, WorkerEnvelope } from './types';
export function resultPreviewForSelectedMedia(snapshot: AppSnapshot): string {
  const selected = snapshot.media.find((media) => media.selected) ?? snapshot.media[0];
  if (!selected) return '';
  if (
    snapshot.jobs.some(
      (job) => job.id === snapshot.runtime.active_job_id && job.media_id === selected.id,
    )
  )
    return '';
  const completed = snapshot.jobs.find(
    (job) => job.media_id === selected.id && job.status === 'completed' && job.output_path,
  );
  if (
    completed &&
    snapshot.runtime.comparison_media_id === selected.id &&
    snapshot.runtime.comparison_output_path === completed.output_path
  ) {
    return snapshot.runtime.comparison_preview_data_url ?? '';
  }
  if (!snapshot.runtime.result_preview_data_url) return '';
  // Progressive tiles and video-frame thumbnails are useful while a job is
  // running, but they are not a completed result and must never enable the
  // before/after comparison.
  if (snapshot.runtime.active_job_id) return '';
  const owner = snapshot.jobs.find(
    (job) =>
      job.status === 'completed' &&
      Boolean(snapshot.runtime.last_output_path) &&
      job.output_path === snapshot.runtime.last_output_path,
  );
  return owner?.media_id === selected.id ? snapshot.runtime.result_preview_data_url : '';
}

const TERMINAL_JOB_EVENTS = ['job_completed', 'job_failed', 'job_cancelled'];

/**
 * A photo batch is one queue item for the engine but several jobs here. The window
 * shows the photo that started last; the others (saving, or not started yet) only
 * change their own job row, which the next refresh brings. Mirrors the native host.
 */
export function applyWorkerEnvelope(snapshot: AppSnapshot, envelope: WorkerEnvelope): AppSnapshot {
  const id = String(envelope.data.job_id ?? '');
  const batch = snapshot.runtime.active_batch_job_ids ?? [];
  if (!id || !batch.includes(id)) return applyJobEnvelope(snapshot, envelope);
  const terminal = TERMINAL_JOB_EVENTS.includes(envelope.type);
  let next: AppSnapshot;
  if (id !== snapshot.runtime.active_job_id && envelope.type !== 'job_started') {
    if (!terminal) return snapshot;
    next = { ...snapshot, runtime: { ...snapshot.runtime } };
    if (envelope.type === 'job_completed') {
      // The result shown after the batch belongs to last_output_path.
      next.runtime.last_output_path = String(envelope.data.output_path ?? '');
      next.runtime.result_preview_data_url = '';
    }
  } else {
    next = applyJobEnvelope(snapshot, envelope);
  }
  if (terminal) leaveBatch(next.runtime, id, envelope.type === 'job_failed');
  return next;
}

/** While other photos remain the batch stays busy, showing the next one. */
function leaveBatch(runtime: AppSnapshot['runtime'], id: string, failed: boolean): void {
  const remaining = (runtime.active_batch_job_ids ?? []).filter((member) => member !== id);
  runtime.active_batch_job_ids = remaining;
  if (!remaining.length || (runtime.active_job_id && runtime.active_job_id !== id)) return;
  runtime.active_job_id = remaining[0];
  runtime.progress = 0;
  runtime.elapsed_seconds = 0;
  runtime.estimated_remaining_seconds = 0;
  runtime.result_preview_data_url = '';
  // A failure stays on the status line until the next photo starts.
  if (failed) return;
  runtime.status_title = 'Processing';
  runtime.status_detail = 'Finishing the other photos of this batch.';
}

function applyJobEnvelope(snapshot: AppSnapshot, envelope: WorkerEnvelope): AppSnapshot {
  // Tiles carry display-only pixels and coordinates. Returning the existing
  // snapshot avoids cloning media thumbnails, model metadata and job history
  // twice per tile; App.svelte composites the pixels on its bounded canvas.
  if (envelope.type === 'tile_update') return snapshot;

  // Progress events can be frequent as well, but only mutate runtime fields.
  // A shallow immutable update keeps Svelte reactive without copying the full
  // application state on every inference step.
  const next = [
    'progress',
    'video_tile_progress',
    'video_stage_progress',
    'video_memory',
    'video_frame_started',
    'video_frame_completed',
  ].includes(envelope.type)
    ? { ...snapshot, runtime: { ...snapshot.runtime } }
    : structuredClone(snapshot);
  const data = envelope.data;
  if (
    [
      'progress',
      'video_tile_progress',
      'video_frame_started',
      'video_frame_completed',
      'video_stage_progress',
    ].includes(envelope.type) &&
    next.runtime.hardware_note.includes(' s so far;')
  )
    next.runtime.hardware_note = '';
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
    case 'media_info': {
      const media = next.media.find((item) => item.path === data.media_path);
      if (media) {
        media.width = Number(data.width ?? 0);
        media.height = Number(data.height ?? 0);
        media.frame_count = Number(data.frame_count ?? 0);
        media.fps = Number(data.fps ?? 0);
        media.duration_seconds = Number(data.duration_seconds ?? 0);
        media.hdr_format =
          data.hdr_format === 'HLG' || data.hdr_format === 'PQ' ? data.hdr_format : '';
        media.audio_warning = String(data.audio_warning ?? '');
        media.preview_data_url = data.jpeg_base64
          ? `data:image/jpeg;base64,${data.jpeg_base64}`
          : '';
        media.probe_status = 'ready';
        media.probe_stage = undefined;
        media.probe_started_at = undefined;
        media.error = '';
      }
      break;
    }
    case 'media_probe_progress': {
      const media = next.media.find((item) => item.path === data.media_path);
      if (media) {
        media.probe_started_at ??= Date.now();
        media.probe_stage = String(data.stage ?? 'opening');
        media.probe_status = 'pending';
        media.error = '';
      }
      break;
    }
    case 'media_probe_failed': {
      const media = next.media.find((item) => item.path === data.media_path);
      if (media) {
        media.probe_status = 'failed';
        media.probe_stage = undefined;
        media.probe_started_at = undefined;
        media.error = String(data.error_message ?? 'The media could not be opened.');
      }
      break;
    }
    case 'job_started':
      next.runtime.active_job_id = String(data.job_id ?? '');
      next.runtime.video_memory = undefined;
      next.runtime.status_title = 'Processing';
      next.runtime.status_detail = 'Preparing the selected model.';
      next.runtime.progress = 0;
      next.runtime.elapsed_seconds = 0;
      next.runtime.estimated_remaining_seconds = 0;
      next.runtime.throughput = 0;
      next.runtime.throughput_unit = 'tiles/s';
      next.runtime.active_tile_size = 0;
      next.runtime.result_preview_data_url = '';
      next.runtime.hardware_note = '';
      break;
    case 'stage_started': {
      const labels: Record<string, string> = {
        deblock: 'Removing JPEG artifacts',
        restore: 'Restoring',
        upscale: 'Upscaling',
        face_restore: 'Restoring faces',
        edit: 'Preparing edit',
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
      // In a job of several stages the title keeps which one runs (two fixes can both be
      // "Restoring"); a one-stage job reads "Enhancing" as before.
      if (data.unit === 'steps') next.runtime.status_title = 'Editing';
      else if (
        !next.runtime.status_title.includes(' · stage ') ||
        next.runtime.status_title.endsWith(' 1/1')
      )
        next.runtime.status_title = 'Enhancing';
      // Editing names its phase; most of an edit happens outside the steps.
      next.runtime.status_detail = data.detail
        ? String(data.detail)
        : `${Number(data.completed_tiles ?? 0)} of ${Number(data.total_tiles ?? 0)} ${data.unit === 'steps' ? 'steps' : 'tiles'}${Number(data.estimated_remaining_seconds ?? 0) > 0 ? ` · ETA ${formatDuration(Number(data.estimated_remaining_seconds))}` : ''}`;
      next.runtime.elapsed_seconds = Number(data.elapsed_seconds ?? 0);
      next.runtime.estimated_remaining_seconds = Number(data.estimated_remaining_seconds ?? 0);
      next.runtime.throughput =
        next.runtime.elapsed_seconds > 0
          ? Number(data.completed_tiles ?? 0) / next.runtime.elapsed_seconds
          : 0;
      next.runtime.throughput_unit = data.unit === 'steps' ? 'steps/s' : 'tiles/s';
      next.runtime.active_tile_size = Number(data.active_tile_size ?? 0);
      next.runtime.device_free_memory = Number(data.device_free_memory ?? 0);
      next.runtime.device_allocated_memory = Number(data.device_allocated_memory ?? 0);
      next.runtime.live_system_ram_available = Number(data.system_ram_available ?? 0);
      next.runtime.live_memory_pressure_percent = Number(data.system_memory_pressure_percent ?? 0);
      break;
    case 'video_tile_progress': {
      const frame = Number(data.frame_index ?? 0);
      const done = Number(data.completed_tiles ?? 0);
      const count = Number(data.total_tiles ?? 0);
      const total = Number(data.total_frames ?? 0);
      const fraction = frame + (count > 0 ? done / count : 0);
      const remaining = Number(data.estimated_remaining_seconds ?? 0);
      next.runtime.progress = total > 0 ? (fraction / total) * 100 : 0;
      next.runtime.status_title = 'Enhancing video';
      next.runtime.status_detail = `Frame ${frame + 1} of ${total || '?'}${count > 1 && done > 0 ? ` · Tile ${Math.min(done, count)} / ${count}` : ''} · ${remaining > 0 ? `ETA ≈ ${formatDuration(remaining)}` : fraction > 0 ? 'Finishing frame' : 'ETA: measuring first tile…'}`;
      next.runtime.elapsed_seconds = Number(data.elapsed_seconds ?? 0);
      next.runtime.estimated_remaining_seconds = remaining;
      next.runtime.active_tile_size = Number(data.active_tile_size ?? 0);
      next.runtime.throughput_unit = 'frames/s';
      break;
    }
    case 'video_memory':
      if (String(data.job_id ?? '') !== next.runtime.active_job_id) break;
      next.runtime.video_memory = data as unknown as AppSnapshot['runtime']['video_memory'];
      if (data.gpu_sample_available) {
        next.runtime.device_free_memory = Number(data.device_free_memory ?? 0);
        next.runtime.device_allocated_memory = Number(data.device_allocated_memory ?? 0);
      }
      next.runtime.live_system_ram_available = Number(data.system_ram_available ?? 0);
      break;
    case 'video_stage_progress': {
      if (String(data.job_id ?? '') !== next.runtime.active_job_id) break;
      const labels: Record<string, string> = {
        verifying_model: 'Checking model files',
        external_decode: 'Converting the source with your FFmpeg',
        external_encode: 'Encoding with your FFmpeg',
        loading_model: 'Loading video model',
        reading_frames: 'Reading video frames',
        preparing_clip: 'Preparing clip',
        encoding: 'Encoding clip',
        enhancing: 'Enhancing clip',
        decoding: 'Decoding enhanced clip',
        finishing: 'Finishing clip',
        saving: 'Saving video',
      };
      next.runtime.status_title = labels[String(data.stage)] ?? 'Processing video';
      const count = Number(data.total ?? 0);
      const totalFrames = Number(data.total_frames ?? 0);
      // A newer phase can coalesce away the preceding frame-completed pulse.
      // Carry its measured ETA and frame count in every temporal phase event.
      if (data.frames_processed !== undefined && totalFrames > 0)
        next.runtime.progress = (Number(data.frames_processed) / totalFrames) * 100;
      if (data.estimated_remaining_seconds !== undefined)
        next.runtime.estimated_remaining_seconds = Number(data.estimated_remaining_seconds);
      const steps = count > 0 ? `${Number(data.completed ?? 0)} / ${count} · ` : '';
      const frames =
        totalFrames > 0
          ? `From frame ${Number(data.frame_index ?? 0) + 1} of ${totalFrames} · `
          : '';
      next.runtime.status_detail = `${steps}${frames}${next.runtime.estimated_remaining_seconds > 0 ? `ETA ≈ ${formatDuration(next.runtime.estimated_remaining_seconds)}` : 'ETA: measuring first clip…'}`;
      if (data.stage === 'saving')
        next.runtime.status_detail = 'Finalizing video timing and audio…';
      next.runtime.elapsed_seconds = Number(data.elapsed_seconds ?? 0);
      next.runtime.throughput_unit = 'frames/s';
      break;
    }
    case 'video_frame_started':
      next.runtime.status_title = 'Enhancing video';
      next.runtime.status_detail = `Frame ${Number(data.frame_index ?? 0) + 1} of ${Number(data.total_frames ?? 0) || '?'} · ${next.runtime.estimated_remaining_seconds > 0 ? `ETA ≈ ${formatDuration(next.runtime.estimated_remaining_seconds)}` : 'ETA: measuring first tile…'}`;
      break;
    case 'video_frame_completed': {
      const done = Number(data.frames_processed ?? 0);
      const total = Number(data.total_frames ?? 0);
      if (done > 0 && total > 0) next.runtime.progress = (done / total) * 100;
      const elapsed = Number(data.elapsed_seconds ?? 0);
      const remaining = Number(data.estimated_remaining_seconds ?? 0);
      next.runtime.status_title = 'Enhancing video';
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
      // A route note from an ended job (one a killed engine never finished) is not this one's.
      next.runtime.hardware_note = '';
      next.runtime.video_memory = undefined;
      next.runtime.status_title = 'Benchmark running';
      next.runtime.status_detail = String(data.workload_version ?? '').startsWith(
        'localsr-benchmark-v2',
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
          const old = snapshot.latest_benchmark;
          const history =
            old?.workload_version === result.workload_version
              ? old.device_history?.length
                ? old.device_history
                : (old.device_results ?? [])
              : [];
          const current = result.device_results ?? [];
          result.device_history = [
            ...history.filter((device) => !current.some((item) => item.device === device.device)),
            ...current,
          ];
          next.runtime.status_detail =
            result.cv_percent == null
              ? 'Measurements saved. Too few repetitions to assess consistency on this device.'
              : result.stable
                ? result.system_score != null
                  ? `GPU score ${result.system_score.toFixed(2)} output MP/s · stable (${(result.cv_percent ?? 0).toFixed(1)}% spread)`
                  : `CPU score ${(result.cpu_score ?? 0).toFixed(2)} output MP/s · stable (${(result.cv_percent ?? 0).toFixed(1)}% spread)`
                : `Unstable run (${(result.cv_percent ?? 0).toFixed(1)}% spread) — close background apps and retry`;
          next.runtime.elapsed_seconds =
            result.result_elapsed_seconds ?? result.total_elapsed_seconds;
          next.runtime.throughput = result.system_score ?? result.cpu_score ?? 0;
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
      next.runtime.hardware_note = '';
      // Drop a live video-frame thumbnail while the host decodes the final
      // output. This prevents a transient comparison slider over one frame.
      next.runtime.result_preview_data_url = '';
      next.runtime.progress = 100;
      next.runtime.status_title = 'Complete';
      next.runtime.status_detail = String(data.notes || 'The output was written successfully.');
      next.runtime.last_output_path = String(data.output_path ?? '');
      next.runtime.elapsed_seconds = Math.max(
        Number(data.inference_seconds ?? 0),
        Number(data.elapsed_seconds ?? 0),
      );
      next.runtime.estimated_remaining_seconds = 0;
      break;
    case 'job_cancelled':
      next.runtime.active_job_id = '';
      next.runtime.hardware_note = '';
      next.runtime.status_title = 'Cancelled';
      next.runtime.status_detail = 'Temporary output was cleaned up.';
      break;
    case 'job_failed':
      next.runtime.active_job_id = '';
      next.runtime.hardware_note = '';
      next.runtime.status_title = 'Could not finish';
      next.runtime.status_detail = String(data.error_message ?? 'Unknown worker error.');
      break;
    case 'engine_status':
      if (String(data.job_id ?? '') !== next.runtime.active_job_id) break;
      next.runtime.status_detail = String(data.message ?? '');
      // The next progress tick replaces the detail line; the route note (a check, a GPU
      // slower than the CPU, part of a model on the CPU) stays until the job ends.
      next.runtime.hardware_note = next.runtime.status_detail;
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
  // A bar is drawn from these as a percentage of its track.
  next.runtime.progress = clampPercent(next.runtime.progress);
  next.runtime.download_progress = clampPercent(next.runtime.download_progress);
  return next;
}

function clampPercent(value: number): number {
  return Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0;
}
