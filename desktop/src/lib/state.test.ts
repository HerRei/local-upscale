import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import {
  applyWorkerEnvelope,
  choosePresetModel,
  formatBytes,
  formatDuration,
  modelsForTask
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
      type: 'video_frame_completed',
      data: { job_id: 'job-1', frames_processed: 12, total_frames: 48 }
    });
    expect(snapshot.runtime.worker).toBe('ready');
    expect(snapshot.runtime.progress).toBe(25);
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'video_job_completed',
      data: { job_id: 'job-1', output_path: '/output/result.mp4' }
    });
    expect(snapshot.runtime.active_job_id).toBe('');
    expect(snapshot.runtime.last_output_path).toBe('/output/result.mp4');
  });

  it('formats bounded human-readable values', () => {
    expect(formatBytes(1024 ** 3)).toBe('1.0 GB');
    expect(formatDuration(125)).toBe('2:05');
  });
});
