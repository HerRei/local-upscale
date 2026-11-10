import { formatDuration } from './format';
import { describe, expect, it } from 'vitest';
import presetPicks from '../../../tests/fixtures/preset-picks.json';
import { demoSnapshot } from './demo';
import type { AppSnapshot } from './types';
import { applyWorkerEnvelope, resultPreviewForSelectedMedia } from './workerEvents';
import {
  choosePresetModel,
  libraryModelsForTask,
  modelsAvailableBeyondSelection,
  modelsForTask,
} from './plan';

describe('desktop state', () => {
  it('filters library choices to the current task, content and fix', () => {
    const snapshot = demoSnapshot();
    const candidates = libraryModelsForTask(snapshot.catalog.models, 'upscale', 'illustration');
    expect(candidates.length).toBeGreaterThan(0);
    expect(
      candidates.every((model) => model.native_scale > 1 && !model.purposes.includes('face')),
    ).toBe(true);
    expect(
      candidates.some(
        (model) => model.purposes.includes('illustration') || model.purposes.includes('anime'),
      ),
    ).toBe(true);
    expect(
      libraryModelsForTask(snapshot.catalog.models, 'denoise', 'photo', 'blur').every(
        (model) => model.native_scale === 1 && model.fixes?.includes('blur'),
      ),
    ).toBe(true);
  });

  it('lists fix models, not upscalers, for a fix stage inside an Upscale plan', () => {
    const models = demoSnapshot().catalog.models;
    const listed = libraryModelsForTask(models, 'upscale', 'photo', 'blur');
    expect(listed.every((model) => model.native_scale === 1 && model.fixes?.includes('blur'))).toBe(
      true,
    );
  });

  it('counts compatible models that are not installed or are not the current selection', () => {
    const models = demoSnapshot()
      .catalog.models.slice(0, 3)
      .map((model, index) => ({
        ...model,
        installed: index !== 0,
      }));
    expect(modelsAvailableBeyondSelection(models, models[1].model_id)).toBe(2);
    expect(modelsAvailableBeyondSelection([], '')).toBe(0);
  });

  it('compares the selected completed image while another batch item is running', () => {
    const snapshot = demoSnapshot();
    snapshot.media = ['first', 'second'].map((id, index) => ({
      id,
      name: `${id}.png`,
      path: `/${id}.png`,
      kind: 'image',
      width: 100,
      height: 100,
      frame_count: 1,
      fps: 0,
      duration_seconds: 0,
      preview_data_url: 'source',
      probe_status: 'ready',
      error: '',
      selected: index === 0,
    }));
    snapshot.jobs = ['first', 'second'].map((id, index) => ({
      id: `job-${id}`,
      media_id: id,
      media_name: `${id}.png`,
      media_kind: 'image',
      status: index ? 'running' : 'completed',
      output_path: index ? '' : '/results/first-denoised.png',
      progress: index ? 20 : 100,
      error: '',
      created_at: index,
    }));
    snapshot.runtime.active_job_id = 'job-second';
    snapshot.runtime.comparison_media_id = 'first';
    snapshot.runtime.comparison_output_path = '/results/first-denoised.png';
    snapshot.runtime.comparison_preview_data_url = 'complete-first';
    snapshot.runtime.result_preview_data_url = 'in-progress-second';
    expect(resultPreviewForSelectedMedia(snapshot)).toBe('complete-first');
    snapshot.media[0].selected = false;
    snapshot.media[1].selected = true;
    expect(resultPreviewForSelectedMedia(snapshot)).toBe('');
    snapshot.media[0].selected = true;
    snapshot.media[1].selected = false;
    snapshot.runtime.comparison_output_path = '/results/stale-first.png';
    expect(resultPreviewForSelectedMedia(snapshot)).toBe('');
  });

  it('keeps face-only models out of primary tasks', () => {
    const snapshot = demoSnapshot();
    for (const task of ['upscale', 'video'] as const) {
      expect(modelsForTask(snapshot, task).every((model) => !model.purposes.includes('face'))).toBe(
        true,
      );
    }
  });

  it('uses the catalog ranking for Quick and Best', () => {
    const snapshot = demoSnapshot();
    expect(choosePresetModel(snapshot, 'upscale', 'quick')?.model_id).toBe('span_photo_x4');
    expect(choosePresetModel(snapshot, 'upscale', 'best')?.model_id).toBe(
      'realplksr_nomoswebphoto_x4',
    );
  });

  it('picks what the shared fixture expects, as presets.py does', () => {
    // tests/test_preset_picks_fixture.py asserts the same file against the Python ranking.
    const template = demoSnapshot().catalog.models[0];
    for (const pick of presetPicks.cases) {
      const base = demoSnapshot();
      const models = presetPicks.models
        .filter((model) => !pick.without.includes(model.model_id))
        .map((model) => ({ ...template, ...model }));
      const snapshot = { ...base, catalog: { ...base.catalog, models } };
      expect(
        choosePresetModel(
          snapshot,
          'upscale',
          pick.quality as 'quick' | 'best',
          pick.content as 'photo' | 'illustration',
        )?.model_id,
        JSON.stringify(pick),
      ).toBe(pick.expected);
    }
  });

  it('never lets a slow specialist become a preset, however it ranks', () => {
    const snapshot = demoSnapshot();
    const best = snapshot.catalog.models.find(
      (model) => model.model_id === 'realplksr_nomoswebphoto_x4',
    )!;
    // Stronger and larger than Best: it would win the Best ranking if presets could pick it.
    snapshot.catalog.models.push({
      ...best,
      model_id: 'slow_specialist_x4',
      quality_tier: best.quality_tier + 1,
      size_bytes: best.size_bytes * 10,
      preset_eligible: false,
    });
    expect(choosePresetModel(snapshot, 'upscale', 'best')?.model_id).toBe(
      'realplksr_nomoswebphoto_x4',
    );
    // A pin is the user's own choice and still works.
    expect(
      choosePresetModel(snapshot, 'upscale', 'best', 'photo', {
        'upscale/photo/best': 'slow_specialist_x4',
      })?.model_id,
    ).toBe('slow_specialist_x4');
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
        video_engines: [],
      },
    });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_started',
      data: { job_id: 'job-1' },
    });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'progress',
      data: {
        job_id: 'job-1',
        completed_tiles: 3,
        total_tiles: 12,
        percentage: 25,
        elapsed_seconds: 5,
        estimated_remaining_seconds: 65,
      },
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
        jpeg_base64: 'live-frame',
      },
    });
    expect(snapshot.runtime.worker).toBe('ready');
    expect(snapshot.runtime.progress).toBe(25);
    expect(snapshot.runtime.status_detail).toBe('Frame 12 of 48 · ETA 1:30');
    expect(snapshot.runtime.result_preview_data_url).toContain('live-frame');
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'video_job_completed',
      data: { job_id: 'job-1', output_path: '/output/result.mp4' },
    });
    expect(snapshot.runtime.active_job_id).toBe('');
    expect(snapshot.runtime.last_output_path).toBe('/output/result.mp4');
    expect(snapshot.runtime.result_preview_data_url).toBe('');
  });

  it('shows which engine runs the active job', () => {
    let snapshot = demoSnapshot();
    snapshot = applyWorkerEnvelope(snapshot, { type: 'job_started', data: { job_id: 'job-1' } });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'engine_status',
      data: { job_id: 'job-1', message: 'SPAN runs on the Neural Engine.' },
    });
    expect(snapshot.runtime.status_title).toBe('Processing');
    expect(snapshot.runtime.status_detail).toBe('SPAN runs on the Neural Engine.');
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'engine_status',
      data: { job_id: 'stale-job', message: 'Preparing HAT for Core ML.' },
    });
    expect(snapshot.runtime.status_detail).toBe('SPAN runs on the Neural Engine.');
  });

  it('keeps the route note after a progress tick replaces the detail line', () => {
    let snapshot = demoSnapshot();
    snapshot = applyWorkerEnvelope(snapshot, { type: 'job_started', data: { job_id: 'job-1' } });
    const note =
      'Checking that Intel UHD Graphics 620 (DirectML) gives the same result as the CPU.';
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'engine_status',
      data: { job_id: 'job-1', message: note },
    });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'progress',
      data: { percentage: 12, completed_tiles: 3, total_tiles: 25 },
    });
    expect(snapshot.runtime.status_detail).toBe('3 of 25 tiles');
    expect(snapshot.runtime.hardware_note).toBe(note);
    // A note for another job leaves it alone.
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'engine_status',
      data: { job_id: 'stale-job', message: 'HAT runs on the GPU.' },
    });
    expect(snapshot.runtime.hardware_note).toBe(note);
    for (const end of ['job_completed', 'job_cancelled', 'job_failed'] as const) {
      const ended = applyWorkerEnvelope(snapshot, { type: end, data: { output_path: 'x' } });
      expect(ended.runtime.hardware_note).toBe('');
    }
    // A new job starts without the last one's note.
    expect(
      applyWorkerEnvelope(snapshot, { type: 'job_started', data: { job_id: 'job-2' } }).runtime
        .hardware_note,
    ).toBe('');
  });

  it('shows temporal phases without inventing a tile count or first-clip ETA', () => {
    let snapshot = applyWorkerEnvelope(demoSnapshot(), {
      type: 'job_started',
      data: { job_id: 'seed' },
    });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'video_stage_progress',
      data: {
        job_id: 'seed',
        stage: 'encoding',
        completed: 0,
        total: 1,
        frame_index: 0,
        total_frames: 24,
        elapsed_seconds: 3,
      },
    });
    expect(snapshot.runtime.status_title).toBe('Encoding clip');
    expect(snapshot.runtime.status_detail).toBe(
      '0 / 1 · From frame 1 of 24 · ETA: measuring first clip…',
    );
    expect(snapshot.runtime.progress).toBe(0);
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'video_stage_progress',
      data: {
        job_id: 'seed',
        stage: 'enhancing',
        frame_index: 9,
        total_frames: 24,
        frames_processed: 9,
        estimated_remaining_seconds: 120,
      },
    });
    expect(snapshot.runtime.status_detail).toContain('ETA ≈ 2:00');
    expect(snapshot.runtime.progress).toBe(37.5);
    const late = applyWorkerEnvelope(snapshot, {
      type: 'video_stage_progress',
      data: { job_id: 'old', stage: 'decoding' },
    });
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
        selected: true,
      },
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
        created_at: 1,
      },
    ];
    snapshot.runtime.active_job_id = 'job-1';
    snapshot.runtime.result_preview_data_url = 'data:image/jpeg;base64,old-result';

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'tile_update',
      data: { job_id: 'job-1', phase: 'completed', jpeg_base64: 'one-tile' },
    });

    expect(snapshot.runtime.result_preview_data_url).toBe('data:image/jpeg;base64,old-result');
    expect(resultPreviewForSelectedMedia(snapshot)).toBe('');

    snapshot.runtime.active_job_id = '';
    snapshot.runtime.last_output_path = '/output.png';
    snapshot.jobs[0].status = 'completed';
    snapshot.jobs[0].output_path = '/output.png';
    snapshot.runtime.result_preview_data_url = 'data:image/jpeg;base64,complete';
    expect(resultPreviewForSelectedMedia(snapshot)).toBe('data:image/jpeg;base64,complete');
  });

  it('tracks benchmark v2 stages and stable output throughput', () => {
    let snapshot = demoSnapshot();
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'benchmark_started',
      data: {
        job_id: 'benchmark-v2',
        workload_version: 'localsr-benchmark-v2',
        measured_frame_count: 10,
      },
    });
    expect(snapshot.runtime.status_detail).toContain('10 phases');

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'benchmark_stage_progress',
      data: {
        job_id: 'benchmark-v2',
        stage: 'mps:s1-classroom',
        completed_units: 8,
        total_units: 0,
        percentage: 14.5,
      },
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
          thermal_state: 'nominal',
        },
      },
    });
    expect(snapshot.runtime.status_detail).toContain('GPU score 21.35');
    expect(snapshot.runtime.throughput).toBe(21.35);
    expect(snapshot.runtime.throughput_unit).toBe('output MP/s');
    expect(snapshot.runtime.thermal_status).toBe('nominal');
  });

  it('keeps every progress value between 0 and 100 percent, whatever the engine reports', () => {
    const running = () => {
      const snapshot = demoSnapshot();
      snapshot.runtime.active_job_id = 'job-1';
      return snapshot;
    };
    for (const [type, data] of [
      ['progress', { job_id: 'job-1', percentage: 150 }],
      ['benchmark_progress', { job_id: 'job-1', percentage: 150 }],
      ['benchmark_stage_progress', { job_id: 'job-1', percentage: 150 }],
      ['video_frame_completed', { job_id: 'job-1', frames_processed: 15, total_frames: 10 }],
    ] as const) {
      expect(applyWorkerEnvelope(running(), { type, data }).runtime.progress, type).toBe(100);
    }
    expect(
      applyWorkerEnvelope(running(), {
        type: 'progress',
        data: { job_id: 'job-1', percentage: -5 },
      }).runtime.progress,
    ).toBe(0);
    const download = (progress: number) =>
      applyWorkerEnvelope(demoSnapshot(), {
        type: 'download-progress',
        data: { model_id: 'span_photo_x4', progress },
      }).runtime.download_progress;
    expect(download(140)).toBe(100);
    expect(download(-3)).toBe(0);
  });

  it('finishes a slow benchmark with unmeasured consistency and unlocks the queue', () => {
    const before = demoSnapshot();
    before.runtime.active_job_id = 'benchmark-slow';
    const after = applyWorkerEnvelope(before, {
      type: 'benchmark_completed',
      data: {
        job_id: 'benchmark-slow',
        result: {
          workload_version: 'localsr-benchmark-v2',
          stable: false,
          cv_percent: null,
          system_score: null,
          cpu_score: null,
          total_elapsed_seconds: 600,
          score: 0,
        },
      },
    });
    expect(after.runtime.active_job_id).toBe('');
    expect(after.runtime.status_title).toBe('Benchmark complete');
    expect(after.runtime.status_detail).toContain('Too few repetitions');
    expect(after.runtime.status_detail).not.toContain('0.0%');
  });
});

it('waits for the first tile measurement before showing a long-duration frame ETA', () => {
  let snapshot = demoSnapshot();
  snapshot.runtime.active_job_id = 'video-1';
  const data = {
    job_id: 'video-1',
    frame_index: 0,
    total_frames: 1000,
    completed_tiles: 0,
    total_tiles: 20,
    elapsed_seconds: 1,
    estimated_remaining_seconds: null,
    active_tile_size: 256,
  };
  snapshot = applyWorkerEnvelope(snapshot, { type: 'video_tile_progress', data });
  expect(snapshot.runtime.status_detail).toContain('ETA: measuring first tile');
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'video_tile_progress',
    data: { ...data, completed_tiles: 1, estimated_remaining_seconds: 180000 },
  });
  expect(snapshot.runtime.status_detail).toContain('Tile 1 / 20 · ETA ≈ 2d 2h');
  expect(snapshot.runtime.progress).toBeCloseTo(0.005);
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'video_frame_started',
    data: { ...data, frame_index: 1 },
  });
  expect(snapshot.runtime.status_detail).toContain('ETA ≈ 2d 2h');
  expect(formatDuration(3599.9)).toBe('1h 0m');
});

it('keeps temporal memory evidence separate from progress and rejects other jobs', () => {
  let snapshot = applyWorkerEnvelope(demoSnapshot(), {
    type: 'job_started',
    data: { job_id: 'seed' },
  });
  snapshot.runtime.status_title = 'Decoding enhanced clip';
  snapshot.runtime.progress = 42;
  const data = {
    job_id: 'seed',
    stage: 'decoding',
    gpu_sample_available: true,
    device_allocated_memory: 123,
    oom: true,
  };
  snapshot = applyWorkerEnvelope(snapshot, { type: 'video_memory', data });
  expect(snapshot.runtime.video_memory?.oom).toBe(true);
  expect(snapshot.runtime.status_title).toBe('Decoding enhanced clip');
  expect(snapshot.runtime.progress).toBe(42);
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'video_memory',
    data: { ...data, job_id: 'old', oom: false },
  });
  expect(snapshot.runtime.video_memory?.job_id).toBe('seed');
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'job_failed',
    data: { job_id: 'seed', error_message: 'Out of memory' },
  });
  expect(snapshot.runtime.video_memory?.oom).toBe(true);
  snapshot = applyWorkerEnvelope(snapshot, { type: 'job_started', data: { job_id: 'next' } });
  expect(snapshot.runtime.video_memory).toBeUndefined();
});

describe('photo batches', () => {
  function batch(): AppSnapshot {
    const snapshot = demoSnapshot();
    snapshot.runtime = {
      ...snapshot.runtime,
      active_job_id: 'a',
      active_batch_job_ids: ['a', 'b', 'c'],
    };
    return snapshot;
  }

  it('shows the photo that started last while earlier ones finish off screen', () => {
    let snapshot = applyWorkerEnvelope(batch(), { type: 'job_started', data: { job_id: 'a' } });
    snapshot = applyWorkerEnvelope(snapshot, { type: 'job_started', data: { job_id: 'b' } });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'progress',
      data: { job_id: 'b', percentage: 30, completed_tiles: 3, total_tiles: 10 },
    });
    expect(snapshot.runtime.active_job_id).toBe('b');
    const shown = snapshot;
    expect(
      applyWorkerEnvelope(shown, { type: 'progress', data: { job_id: 'a', percentage: 100 } }),
    ).toBe(shown);
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_completed',
      data: { job_id: 'a', output_path: '/out/a.png' },
    });
    expect(snapshot.runtime).toMatchObject({
      active_job_id: 'b',
      active_batch_job_ids: ['b', 'c'],
      progress: 30,
      status_title: 'Enhancing',
      last_output_path: '/out/a.png',
    });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_failed',
      data: { job_id: 'c', error_message: 'cannot identify image file' },
    });
    expect(snapshot.runtime.active_job_id).toBe('b');
    expect(snapshot.runtime.active_batch_job_ids).toEqual(['b']);
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_completed',
      data: { job_id: 'b', output_path: '/out/b.png' },
    });
    expect(snapshot.runtime).toMatchObject({
      active_job_id: '',
      active_batch_job_ids: [],
      status_title: 'Complete',
      last_output_path: '/out/b.png',
    });
  });

  it('stays busy when the shown photo ends before the next one starts', () => {
    let snapshot = applyWorkerEnvelope(batch(), { type: 'job_started', data: { job_id: 'a' } });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_failed',
      data: { job_id: 'a', error_message: 'disk full' },
    });
    // The failure stays on the status line until the next photo starts.
    expect(snapshot.runtime).toMatchObject({
      active_job_id: 'b',
      active_batch_job_ids: ['b', 'c'],
      status_title: 'Could not finish',
      status_detail: 'disk full',
      result_preview_data_url: '',
    });
    snapshot = applyWorkerEnvelope(snapshot, { type: 'job_started', data: { job_id: 'b' } });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_completed',
      data: { job_id: 'b', output_path: '/out/b.png' },
    });
    expect(snapshot.runtime.status_title).toBe('Processing');
  });

  it('never shows the pixels of the failed photo as the result of one saved off screen', () => {
    let snapshot = batch();
    snapshot.runtime.active_batch_job_ids = ['b', 'a'];
    snapshot.media = ['a', 'b'].map((id) => ({
      id,
      path: `/in/${id}.png`,
      name: `${id}.png`,
      kind: 'image',
      width: 64,
      height: 48,
      frame_count: 1,
      fps: 0,
      duration_seconds: 0,
      preview_data_url: 'source',
      probe_status: 'ready',
      error: '',
      selected: id === 'b',
    }));
    snapshot.jobs = ['a', 'b'].map((id, index) => ({
      id,
      media_id: id,
      media_name: `${id}.png`,
      media_kind: 'image',
      status: 'running',
      output_path: '',
      progress: 0,
      error: '',
      created_at: index,
    }));
    snapshot = applyWorkerEnvelope(snapshot, { type: 'job_started', data: { job_id: 'b' } });
    snapshot = applyWorkerEnvelope(snapshot, { type: 'job_started', data: { job_id: 'a' } });
    snapshot.runtime.result_preview_data_url = 'pixels-of-a';
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_completed',
      data: { job_id: 'b', output_path: '/out/b.png' },
    });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_failed',
      data: { job_id: 'a', error_message: 'disk full' },
    });
    // The next refresh brings b's finished row.
    snapshot.jobs[1] = { ...snapshot.jobs[1], status: 'completed', output_path: '/out/b.png' };
    snapshot.jobs[0] = { ...snapshot.jobs[0], status: 'failed' };
    expect(snapshot.runtime.last_output_path).toBe('/out/b.png');
    expect(resultPreviewForSelectedMedia(snapshot)).toBe('');
  });
});
