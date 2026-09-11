import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import {
  applyWorkerEnvelope,
  choosePresetModel,
  formatBytes,
  formatDuration,
  modelsForTask,
  resultPreviewForSelectedMedia
} from './state';

describe('desktop state', () => {
  it('keeps face-only models out of primary tasks', () => {
    const snapshot = demoSnapshot();
    for (const task of ['upscale', 'video'] as const) {
      expect(modelsForTask(snapshot, task).every((model) => !model.purposes.includes('face'))).toBe(true);
    }
  });

  it('uses the catalog ranking for Quick and Best', () => {
    const snapshot = demoSnapshot();
    expect(choosePresetModel(snapshot, 'upscale', 'quick')?.model_id).toBe('span_photo_x4');
    expect(choosePresetModel(snapshot, 'upscale', 'best')?.model_id).toBe(
      'realplksr_nomoswebphoto_x4'
    );
  });

  it('tracks image and video progress without losing worker state', () => {
    let snapshot = demoSnapshot();
    snapshot = applyWorkerEnvelope(snapshot, { type: 'worker_ready', data: {} });
    expect(snapshot.runtime.worker).toBe('negotiating');
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'engine_info',
      data: {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: 'test',
        engine_version: 'test',
        features: [],
        model_formats: [],
        video_engines: []
      }
    });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_started',
      data: { job_id: 'job-1' }
    });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'progress',
      data: {
        job_id: 'job-1',
        completed_tiles: 3,
        total_tiles: 12,
        percentage: 25,
        elapsed_seconds: 5,
        estimated_remaining_seconds: 65
      }
    });
    expect(snapshot.runtime.status_detail).toBe('3 of 12 tiles · ETA 1:05');
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'video_frame_completed',
      data: {
        job_id: 'job-1',
        frames_processed: 12,
        total_frames: 48,
        elapsed_seconds: 30,
        estimated_remaining_seconds: 90,
        jpeg_base64: 'live-frame'
      }
    });
    expect(snapshot.runtime.worker).toBe('ready');
    expect(snapshot.runtime.progress).toBe(25);
    expect(snapshot.runtime.status_detail).toBe('Frame 12 of 48 · ETA 1:30');
    expect(snapshot.runtime.result_preview_data_url).toContain('live-frame');
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'video_job_completed',
      data: { job_id: 'job-1', output_path: '/output/result.mp4' }
    });
    expect(snapshot.runtime.active_job_id).toBe('');
    expect(snapshot.runtime.last_output_path).toBe('/output/result.mp4');
    expect(snapshot.runtime.result_preview_data_url).toBe('');
  });

  it('shows temporal phases without inventing a tile count or first-clip ETA', () => {
    let snapshot = applyWorkerEnvelope(demoSnapshot(), { type: 'job_started', data: { job_id: 'seed' } });
    snapshot = applyWorkerEnvelope(snapshot, { type: 'video_stage_progress', data: {
      job_id: 'seed', stage: 'encoding', completed: 0, total: 1, frame_index: 0,
      total_frames: 24, elapsed_seconds: 3
    } });
    expect(snapshot.runtime.status_title).toBe('Encoding clip');
    expect(snapshot.runtime.status_detail).toBe('0 / 1 · From frame 1 of 24 · ETA: measuring first clip…');
    expect(snapshot.runtime.progress).toBe(0);
    snapshot = applyWorkerEnvelope(snapshot, { type: 'video_stage_progress', data: {
      job_id: 'seed', stage: 'enhancing', frame_index: 9, total_frames: 24,
      frames_processed: 9, estimated_remaining_seconds: 120
    } });
    expect(snapshot.runtime.status_detail).toContain('ETA ≈ 2:00');
    expect(snapshot.runtime.progress).toBe(37.5);
    const late = applyWorkerEnvelope(snapshot, { type: 'video_stage_progress', data: { job_id: 'old', stage: 'decoding' } });
    expect(late.runtime.status_title).toBe('Enhancing clip');
  });

  it('keeps progressive tiles out of the completed comparison state', () => {
    let snapshot = demoSnapshot();
    snapshot.media = [
      {
        id: 'media-1',
        path: '/input.png',
        name: 'input.png',
        kind: 'image',
        width: 100,
        height: 100,
        frame_count: 1,
        fps: 0,
        duration_seconds: 0,
        preview_data_url: 'data:image/jpeg;base64,source',
        probe_status: 'ready',
        error: '',
        selected: true
      }
    ];
    snapshot.jobs = [
      {
        id: 'job-1',
        media_id: 'media-1',
        media_name: 'input.png',
        media_kind: 'image',
        status: 'running',
        progress: 10,
        output_path: '',
        error: '',
        created_at: 1
      }
    ];
    snapshot.runtime.active_job_id = 'job-1';
    snapshot.runtime.result_preview_data_url = 'data:image/jpeg;base64,old-result';

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'tile_update',
      data: { job_id: 'job-1', phase: 'completed', jpeg_base64: 'one-tile' }
    });

    expect(snapshot.runtime.result_preview_data_url).toBe('data:image/jpeg;base64,old-result');
    expect(resultPreviewForSelectedMedia(snapshot)).toBe('');

    snapshot.runtime.active_job_id = '';
    snapshot.runtime.last_output_path = '/output.png';
    snapshot.jobs[0].status = 'completed';
    snapshot.jobs[0].output_path = '/output.png';
    snapshot.runtime.result_preview_data_url = 'data:image/jpeg;base64,complete';
    expect(resultPreviewForSelectedMedia(snapshot)).toBe(
      'data:image/jpeg;base64,complete'
    );
  });

  it('tracks benchmark v2 stages and stable output throughput', () => {
    let snapshot = demoSnapshot();
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'benchmark_started',
      data: {
        job_id: 'benchmark-v2',
        workload_version: 'localsr-benchmark-v2',
        measured_frame_count: 10
      }
    });
    expect(snapshot.runtime.status_detail).toContain('10 phases');

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'benchmark_stage_progress',
      data: {
        job_id: 'benchmark-v2',
        stage: 'mps:s1-classroom',
        completed_units: 8,
        total_units: 0,
        percentage: 14.5
      }
    });
    expect(snapshot.runtime.progress).toBe(14.5);
    expect(snapshot.runtime.status_detail).toBe('mps · s1-classroom · iteration 8');

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'benchmark_completed',
      data: {
        job_id: 'benchmark-v2',
        result: {
          workload_version: 'localsr-benchmark-v2',
          score: 21.35,
          system_score: 21.35,
          cpu_score: 3.85,
          stable: true,
          cv_percent: 2.18,
          result_elapsed_seconds: 198.3,
          total_elapsed_seconds: 198.3,
          thermal_state: 'nominal'
        }
      }
    });
    expect(snapshot.runtime.status_detail).toContain('GPU score 21.35');
    expect(snapshot.runtime.throughput).toBe(21.35);
    expect(snapshot.runtime.throughput_unit).toBe('output MP/s');
    expect(snapshot.runtime.thermal_status).toBe('nominal');
  });

  it('formats bounded human-readable values', () => {
    expect(formatBytes(1024 ** 3)).toBe('1.0 GB');
    expect(formatDuration(125)).toBe('2:05');
  });
});

it('shows an honest first-tile wait, then a first-frame ETA including long durations', () => {
  let snapshot = demoSnapshot();
  snapshot.runtime.active_job_id = 'video-1';
  const data = { job_id: 'video-1', frame_index: 0, total_frames: 1000, completed_tiles: 0, total_tiles: 20, elapsed_seconds: 1, estimated_remaining_seconds: null, active_tile_size: 256 };
  snapshot = applyWorkerEnvelope(snapshot, { type: 'video_tile_progress', data });
  expect(snapshot.runtime.status_detail).toContain('ETA: measuring first tile');
  snapshot = applyWorkerEnvelope(snapshot, { type: 'video_tile_progress', data: { ...data, completed_tiles: 1, estimated_remaining_seconds: 180000 } });
  expect(snapshot.runtime.status_detail).toContain('Tile 1 / 20 · ETA ≈ 2d 2h');
  expect(snapshot.runtime.progress).toBeCloseTo(.005);
  snapshot = applyWorkerEnvelope(snapshot, { type: 'video_frame_started', data: { ...data, frame_index: 1 } });
  expect(snapshot.runtime.status_detail).toContain('ETA ≈ 2d 2h');
  expect(formatDuration(3599.9)).toBe('1h 0m');
});

it('keeps temporal memory evidence separate from progress and rejects other jobs', () => {
  let snapshot = applyWorkerEnvelope(demoSnapshot(), { type: 'job_started', data: { job_id: 'seed' } });
  snapshot.runtime.status_title = 'Decoding enhanced clip';
  snapshot.runtime.progress = 42;
  const data = { job_id: 'seed', stage: 'decoding', gpu_sample_available: true, device_allocated_memory: 123, oom: true };
  snapshot = applyWorkerEnvelope(snapshot, { type: 'video_memory', data });
  expect(snapshot.runtime.video_memory?.oom).toBe(true);
  expect(snapshot.runtime.status_title).toBe('Decoding enhanced clip');
  expect(snapshot.runtime.progress).toBe(42);
  snapshot = applyWorkerEnvelope(snapshot, { type: 'video_memory', data: { ...data, job_id: 'old', oom: false } });
  expect(snapshot.runtime.video_memory?.job_id).toBe('seed');
  snapshot = applyWorkerEnvelope(snapshot, { type: 'job_failed', data: { job_id: 'seed', error_message: 'Out of memory' } });
  expect(snapshot.runtime.video_memory?.oom).toBe(true);
  snapshot = applyWorkerEnvelope(snapshot, { type: 'job_started', data: { job_id: 'next' } });
  expect(snapshot.runtime.video_memory).toBeUndefined();
});
