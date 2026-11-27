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

  it('excludes background models from primary and fix choices', () => {
    const snapshot = demoSnapshot();
    const background = {
      ...snapshot.catalog.models[0],
      model_id: 'background',
      native_scale: 1,
      purposes: ['background'],
      fixes: [],
    };
    snapshot.catalog.models.push(background);
    for (const task of ['denoise', 'upscale', 'video'] as const) {
      expect(modelsForTask(snapshot, task)).not.toContain(background);
      expect(libraryModelsForTask(snapshot.catalog.models, task, 'photo')).not.toContain(
        background,
      );
    }
    expect(libraryModelsForTask(snapshot.catalog.models, 'upscale', 'photo', 'blur')).not.toContain(
      background,
    );
    expect(modelsForTask(snapshot, 'video', 'denoise')).not.toContain(background);
    background.native_scale = 4;
    expect(modelsForTask(snapshot, 'upscale')).not.toContain(background);
    expect(libraryModelsForTask(snapshot.catalog.models, 'upscale', 'photo')).not.toContain(
      background,
    );
  });

  it('uses the catalog ranking for Quick and Best', () => {
    const snapshot = demoSnapshot();
    expect(choosePresetModel(snapshot, 'upscale', 'quick')?.model_id).toBe('localsr_quick_x4_v1');
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

  it('shows that a stopped video kept a result', () => {
    const snapshot = applyWorkerEnvelope(demoSnapshot(), {
      type: 'video_job_completed',
      data: {
        job_id: 'job-1',
        output_path: '/output/result_partial_0m42s.mp4',
        partial: true,
        kept_seconds: 42.5,
      },
    });
    expect(snapshot.runtime.status_title).toBe('Stopped, kept 0:42');
    expect(snapshot.runtime.last_output_path).toBe('/output/result_partial_0m42s.mp4');
  });

  it('counts a kept part of under a second in frames, never as 0:00', () => {
    const stopped = (data: Record<string, unknown>) =>
      applyWorkerEnvelope(demoSnapshot(), {
        type: 'video_job_completed',
        data: { job_id: 'job-1', output_path: '/output/clip_partial.mp4', partial: true, ...data },
      }).runtime.status_title;
    expect(stopped({ kept_seconds: 0.074, kept_frames: 2 })).toBe('Stopped, kept 2 frames');
    expect(stopped({ kept_seconds: 0.033, kept_frames: 1 })).toBe('Stopped, kept 1 frame');
    // Without a frame count the frame rate of the result gives it.
    expect(stopped({ kept_seconds: 0.5, output_fps: 24 })).toBe('Stopped, kept 12 frames');
    expect(stopped({ kept_seconds: 0.5 })).toBe('Stopped, kept under a second');
    expect(stopped({})).toBe('Stopped, kept the finished part');
  });

  it('says how many tiles a stopped photo kept', () => {
    const stopped = (data: Record<string, unknown>) =>
      applyWorkerEnvelope(demoSnapshot(), {
        type: 'job_completed',
        data: { job_id: 'job-1', output_path: '/output/photo_partial.png', partial: true, ...data },
      }).runtime.status_title;
    expect(
      stopped({
        finished_tiles: [
          [0, 0, 512, 512],
          [512, 0, 512, 512],
        ],
        total_tiles: 12,
      }),
    ).toBe('Stopped, kept 2 of 12 tiles');
    expect(stopped({})).toBe('Stopped, kept the finished part');
  });

  it('counts the finished tiles of a photo stage, and none for the steps of an edit', () => {
    let snapshot = applyWorkerEnvelope(demoSnapshot(), {
      type: 'job_started',
      data: { job_id: 'job-1', can_keep_partial: true },
    });
    const progress = (data: Record<string, unknown>) =>
      (snapshot = applyWorkerEnvelope(snapshot, {
        type: 'progress',
        data: { job_id: 'job-1', percentage: 10, ...data },
      }));
    progress({ completed_tiles: 3, total_tiles: 40 });
    expect(snapshot.runtime.tiles_completed).toBe(3);
    expect(snapshot.runtime.tiles_total).toBe(40);
    // The next stage has no finished tile until it reports one.
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'stage_started',
      data: { job_id: 'job-1', stage_kind: 'upscale', stage_index: 1, stage_count: 2 },
    });
    expect(snapshot.runtime.tiles_completed).toBe(0);
    progress({ completed_tiles: 7, total_tiles: 40, unit: 'steps' });
    expect(snapshot.runtime.tiles_completed).toBe(0);
    progress({ completed_tiles: 5, total_tiles: 40 });
    // A message that only names a phase carries no count and must not take the count away.
    progress({ detail: 'Saving the result' });
    expect(snapshot.runtime.tiles_completed).toBe(5);
    expect(snapshot.runtime.tiles_total).toBe(40);
    snapshot = applyWorkerEnvelope(snapshot, { type: 'job_cancelled', data: { job_id: 'job-1' } });
    expect(snapshot.runtime.tiles_completed).toBe(0);
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

  it('tracks benchmark v3 recommendations in seconds per input megapixel', () => {
    let snapshot = demoSnapshot();
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'benchmark_started',
      data: {
        job_id: 'benchmark-v3',
        workload_version: 'localsr-benchmark-v3',
        measured_frame_count: 16,
      },
    });
    expect(snapshot.runtime.status_detail).toContain('16 phases');

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'benchmark_completed',
      data: {
        job_id: 'benchmark-v3',
        result: {
          workload_version: 'localsr-benchmark-v3',
          recommended_device: 'cpu',
          recommended_device_name: 'CPU',
          recommended_device_seconds_per_input_mp: 9.1,
          recommended_cpu_threads: 4,
          stable: true,
          cv_percent: 1.5,
          total_elapsed_seconds: 42.0,
          thermal_state: 'nominal',
          device_results: [],
          score: 0.5,
        },
      },
    });
    expect(snapshot.runtime.status_detail).toBe('Recommended: CPU (9.1 s / input MP)');
    expect(snapshot.runtime.throughput).toBe(9.1);
    expect(snapshot.runtime.throughput_unit).toBe('s / input MP');
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

  it('starts the next photo without the finished one’s elapsed time', () => {
    let snapshot = applyWorkerEnvelope(batch(), { type: 'job_started', data: { job_id: 'a' } });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'progress',
      data: { job_id: 'a', percentage: 90, elapsed_seconds: 40, estimated_remaining_seconds: 5 },
    });
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_completed',
      data: { job_id: 'a', output_path: '/out/a.png', elapsed_seconds: 41 },
    });
    expect(snapshot.runtime.active_job_id).toBe('b');
    expect(snapshot.runtime.elapsed_seconds).toBe(0);
    expect(snapshot.runtime.estimated_remaining_seconds).toBe(0);
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

it('clears preparation notices on progress and hides whole-frame tiles', () => {
  let snapshot = demoSnapshot();
  snapshot.runtime.active_job_id = 'j';
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'engine_status',
    data: {
      job_id: 'j',
      message: 'Reading model. 0 s so far; the job stops in 180 s if it is not ready.',
    },
  });
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'video_tile_progress',
    data: { job_id: 'j', frame_index: 11, total_frames: 246, completed_tiles: 0, total_tiles: 1 },
  });
  expect(snapshot.runtime.hardware_note).toBe('');
  expect(snapshot.runtime.status_detail).toContain('Frame 12 of 246');
  expect(snapshot.runtime.status_detail).not.toContain('Tile');
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'video_tile_progress',
    data: { job_id: 'j', frame_index: 11, total_frames: 246, completed_tiles: 2, total_tiles: 8 },
  });
  expect(snapshot.runtime.status_detail).toContain('Tile 2 / 8');
});

it('tracks multiple active and queued model downloads independently', () => {
  let snapshot = demoSnapshot();
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-started',
    data: { model_id: 'm1', total_bytes: 1000 },
  });
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-started',
    data: { model_id: 'm2', total_bytes: 2000 },
  });
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-queued',
    data: { model_id: 'm4', total_bytes: 4000 },
  });
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-progress',
    data: { model_id: 'm1', downloaded_bytes: 250, total_bytes: 1000, progress: 25 },
  });
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-progress',
    data: { model_id: 'm2', downloaded_bytes: 1200, total_bytes: 2000, progress: 60 },
  });

  expect(snapshot.runtime.downloads).toEqual([
    {
      model_id: 'm1',
      status: 'downloading',
      progress: 25,
      downloaded_bytes: 250,
      total_bytes: 1000,
    },
    {
      model_id: 'm2',
      status: 'downloading',
      progress: 60,
      downloaded_bytes: 1200,
      total_bytes: 2000,
    },
    { model_id: 'm4', status: 'queued', progress: 0, downloaded_bytes: 0, total_bytes: 4000 },
  ]);
  expect(snapshot.runtime.download_model_id).toBe('m1');
  expect(snapshot.runtime.download_progress).toBe(25);

  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-cancelled',
    data: { model_id: 'm1' },
  });
  expect(snapshot.runtime.downloads).toEqual([
    {
      model_id: 'm2',
      status: 'downloading',
      progress: 60,
      downloaded_bytes: 1200,
      total_bytes: 2000,
    },
    { model_id: 'm4', status: 'queued', progress: 0, downloaded_bytes: 0, total_bytes: 4000 },
  ]);
  expect(snapshot.runtime.download_model_id).toBe('m2');
  expect(snapshot.runtime.download_progress).toBe(60);

  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-failed',
    data: { model_id: 'm2', error_message: 'checksum mismatch' },
  });
  expect(snapshot.runtime.downloads).toEqual([
    { model_id: 'm4', status: 'queued', progress: 0, downloaded_bytes: 0, total_bytes: 4000 },
  ]);
  expect(snapshot.runtime.download_model_id).toBe('m4');

  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-completed',
    data: { model_id: 'm4' },
  });
  expect(snapshot.runtime.downloads).toEqual([]);
  expect(snapshot.runtime.download_model_id).toBe('');
  expect(snapshot.runtime.download_progress).toBe(0);
});

it('keeps phase and received_bytes when sent on download-progress and zeroes received_bytes on start', () => {
  let snapshot = demoSnapshot();
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-started',
    data: { model_id: 'm1', total_bytes: 1000 },
  });
  expect(snapshot.runtime.downloads?.[0]).toMatchObject({
    model_id: 'm1',
    received_bytes: 0,
  });

  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-progress',
    data: {
      model_id: 'm1',
      downloaded_bytes: 500,
      total_bytes: 1000,
      progress: 50,
      phase: 'verifying',
      received_bytes: 300,
    },
  });
  expect(snapshot.runtime.downloads?.[0]).toMatchObject({
    model_id: 'm1',
    phase: 'verifying',
    received_bytes: 300,
  });

  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'download-progress',
    data: {
      model_id: 'm1',
      downloaded_bytes: 600,
      total_bytes: 1000,
      progress: 60,
    },
  });
  expect(snapshot.runtime.downloads?.[0]).not.toHaveProperty('phase');
  expect(snapshot.runtime.downloads?.[0]).not.toHaveProperty('received_bytes');
});

it('tracks the audio cleanup phase title, chunk progress and ETA', () => {
  let snapshot = demoSnapshot();
  snapshot.runtime.active_job_id = 'vid-audio';
  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'video_stage_progress',
    data: { job_id: 'vid-audio', stage: 'loading_audio_denoise', total_frames: 0 },
  });
  expect(snapshot.runtime.status_title).toBe('Loading audio model');

  snapshot = applyWorkerEnvelope(snapshot, {
    type: 'video_stage_progress',
    data: {
      job_id: 'vid-audio',
      stage: 'audio_denoise',
      completed: 2,
      total: 4,
      frames_processed: 0,
      total_frames: 0,
      elapsed_seconds: 1.5,
      estimated_remaining_seconds: 1.5,
    },
  });
  expect(snapshot.runtime.status_title).toBe('Audio cleanup');
  expect(snapshot.runtime.progress).toBe(50);
  expect(snapshot.runtime.status_detail).toContain('Chunk 2 of 4');
  expect(snapshot.runtime.status_detail).toContain('ETA');
  expect(snapshot.runtime.throughput_unit).toBe('');
});

describe('stopping a video', () => {
  it('takes the host decision about keeping from job_started and counts frames before the current one', () => {
    let snapshot = demoSnapshot();
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_started',
      data: { job_id: 'v1', can_keep_partial: false },
    });
    expect(snapshot.runtime.can_keep_partial).toBe(false);
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'video_frame_started',
      data: { job_id: 'v1', frame_index: 4, total_frames: 10 },
    });
    expect(snapshot.runtime.frames_processed).toBe(4);
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_started',
      data: { job_id: 'v2', can_keep_partial: true },
    });
    expect(snapshot.runtime.can_keep_partial).toBe(true);
    expect(snapshot.runtime.frames_processed).toBe(0);
  });

  it('names de-flickering and keeps the progress', () => {
    let snapshot = demoSnapshot();
    snapshot = applyWorkerEnvelope(snapshot, { type: 'job_started', data: { job_id: 'v1' } });
    snapshot.runtime.progress = 40;
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'video_stage_progress',
      data: { job_id: 'v1', stage: 'deflickering', completed: 6, total: 52 },
    });
    expect(snapshot.runtime.status_title).toBe('De-flickering');
    expect(snapshot.runtime.status_detail).toContain('Frame 6 of 52');
    expect(snapshot.runtime.progress).toBe(40);
  });
});

describe('structural sharing', () => {
  const twoMedia = () => {
    const snapshot = demoSnapshot();
    snapshot.media = [
      {
        id: 'm1',
        path: '/first.png',
        name: 'first.png',
        kind: 'image',
        width: 100,
        height: 100,
        frame_count: 1,
        fps: 0,
        duration_seconds: 0,
        preview_data_url: 'data:image/jpeg;base64,large-first',
        probe_status: 'ready',
        error: '',
        selected: true,
      },
      {
        id: 'm2',
        path: '/second.png',
        name: 'second.png',
        kind: 'image',
        width: 200,
        height: 200,
        frame_count: 1,
        fps: 0,
        duration_seconds: 0,
        preview_data_url: 'data:image/jpeg;base64,large-second',
        probe_status: 'pending',
        error: '',
        selected: false,
      },
    ];
    return snapshot;
  };

  it('keeps media and unrelated snapshot branch identities on runtime events', () => {
    const before = twoMedia();
    const afterStart = applyWorkerEnvelope(before, {
      type: 'job_started',
      data: { job_id: 'job-1' },
    });
    expect(afterStart).not.toBe(before);
    expect(afterStart.runtime).not.toBe(before.runtime);
    expect(afterStart.media).toBe(before.media);
    expect(afterStart.media[0]).toBe(before.media[0]);
    expect(afterStart.media[1]).toBe(before.media[1]);
    expect(afterStart.catalog).toBe(before.catalog);
    expect(afterStart.jobs).toBe(before.jobs);
    expect(afterStart.settings).toBe(before.settings);

    const afterComplete = applyWorkerEnvelope(afterStart, {
      type: 'job_completed',
      data: { job_id: 'job-1', output_path: '/out/first.png' },
    });
    expect(afterComplete.media).toBe(before.media);
    expect(afterComplete.media[0]).toBe(before.media[0]);
    expect(afterComplete.media[1]).toBe(before.media[1]);
    expect(afterComplete.runtime.last_output_path).toBe('/out/first.png');
  });

  it('copies only the matching media item on media events', () => {
    const before = twoMedia();

    const afterProgress = applyWorkerEnvelope(before, {
      type: 'media_probe_progress',
      data: { media_path: '/second.png', stage: 'decoding' },
    });
    expect(afterProgress).not.toBe(before);
    expect(afterProgress.runtime).toBe(before.runtime);
    expect(afterProgress.media).not.toBe(before.media);
    expect(afterProgress.media[0]).toBe(before.media[0]);
    expect(afterProgress.media[1]).not.toBe(before.media[1]);
    expect(afterProgress.media[1].probe_stage).toBe('decoding');
    expect(before.media[1].probe_stage).toBeUndefined();

    const afterInfo = applyWorkerEnvelope(afterProgress, {
      type: 'media_info',
      data: {
        media_path: '/second.png',
        width: 400,
        height: 300,
        frame_count: 1,
        fps: 0,
        duration_seconds: 0,
        jpeg_base64: 'updated-second',
      },
    });
    expect(afterInfo.media[0]).toBe(before.media[0]);
    expect(afterInfo.media[1].probe_status).toBe('ready');
    expect(afterInfo.media[1].preview_data_url).toBe('data:image/jpeg;base64,updated-second');

    const afterFail = applyWorkerEnvelope(afterInfo, {
      type: 'media_probe_failed',
      data: { media_path: '/second.png', error_message: 'corrupt file' },
    });
    expect(afterFail.media[0]).toBe(before.media[0]);
    expect(afterFail.media[1].probe_status).toBe('failed');
    expect(afterFail.media[1].error).toBe('corrupt file');
  });

  it('uses host-provided preview_url on media_info, video_frame_completed and live_preview_frame', () => {
    const before = twoMedia();
    const assetThumb = 'asset://localhost/%2Fcache%2Fpreviews%2Fthumb.jpg';
    const afterInfo = applyWorkerEnvelope(before, {
      type: 'media_info',
      data: {
        media_path: '/second.png',
        width: 400,
        height: 300,
        frame_count: 1,
        fps: 0,
        duration_seconds: 0,
        preview_url: assetThumb,
      },
    });
    expect(afterInfo.media[1].preview_data_url).toBe(assetThumb);

    const started = applyWorkerEnvelope(afterInfo, {
      type: 'job_started',
      data: { job_id: 'job-v' },
    });
    const frameUrl = 'asset://localhost/%2Fcache%2Fpreviews%2Fframe.jpg';
    const afterFrame = applyWorkerEnvelope(started, {
      type: 'video_frame_completed',
      data: {
        job_id: 'job-v',
        frames_processed: 1,
        total_frames: 10,
        preview_url: frameUrl,
      },
    });
    expect(afterFrame.runtime.result_preview_data_url).toBe(frameUrl);

    const liveUrl = 'asset://localhost/%2Fcache%2Fpreviews%2Flive.jpg';
    const afterLive = applyWorkerEnvelope(started, {
      type: 'live_preview_frame',
      data: {
        job_id: 'job-v',
        preview_kind: 'video',
        preview_url: liveUrl,
      },
    });
    expect(afterLive.runtime.result_preview_data_url).toBe(liveUrl);
  });

  it('returns the exact same snapshot for unhandled event types', () => {
    const before = twoMedia();
    const after = applyWorkerEnvelope(before, {
      type: 'unhandled_event_type',
      data: { job_id: 'unknown', extra: 123 },
    });
    expect(after).toBe(before);
  });

  it('tracks job_steps states, preview URLs, skipped steps, and step names across a multi-step job', () => {
    let snapshot = demoSnapshot();
    snapshot.settings = { ...snapshot.settings, task: 'upscale', output_scale: 4 };
    snapshot.runtime.active_job_id = 'job-steps-1';
    snapshot.runtime.job_steps = [
      {
        kind: 'deblock',
        fix: 'jpeg',
        model_id: 'fbcnn_color',
        state: 'waiting',
        note: '',
        preview_url: '',
        kept: false,
      },
      {
        kind: 'restore',
        fix: 'colour',
        model_id: 'ddcolor_modelscope',
        state: 'waiting',
        note: '',
        preview_url: '',
        kept: false,
      },
      {
        kind: 'restore',
        fix: 'noise',
        model_id: 'denoise_realplksr_1x',
        state: 'waiting',
        note: '',
        preview_url: '',
        kept: false,
      },
      {
        kind: 'upscale',
        fix: '',
        model_id: 'span_photo_x4',
        state: 'waiting',
        note: '',
        preview_url: '',
        kept: false,
      },
    ];

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'stage_started',
      data: {
        job_id: 'job-steps-1',
        stage_index: 0,
        stage_count: 4,
        stage_kind: 'deblock',
        step: 'deblock',
        fix: 'jpeg',
        model_id: 'fbcnn_color',
      },
    });
    expect(snapshot.runtime.job_steps?.[0].state).toBe('running');
    expect(snapshot.runtime.status_title).toBe('Step 1 of 4 · JPEG');
    expect(snapshot.runtime.status_detail).toBe('Preparing FBCNN Color.');

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'stage_result',
      data: {
        job_id: 'job-steps-1',
        stage_index: 0,
        stage_count: 4,
        step: 'deblock',
        fix: 'jpeg',
        skipped: true,
        note: 'No JPEG blocks found.',
        image_path: '',
      },
    });
    expect(snapshot.runtime.job_steps?.[0]).toMatchObject({
      state: 'skipped',
      note: 'No JPEG blocks found.',
      preview_url: '',
      kept: false,
    });

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'stage_started',
      data: {
        job_id: 'job-steps-1',
        stage_index: 1,
        stage_count: 4,
        stage_kind: 'restore',
        step: 'restore',
        fix: 'colour',
        model_id: 'ddcolor_modelscope',
      },
    });
    expect(snapshot.runtime.job_steps?.[0].state).toBe('skipped');
    expect(snapshot.runtime.job_steps?.[1].state).toBe('running');
    expect(snapshot.runtime.status_title).toBe('Step 2 of 4 · Colour');
    expect(snapshot.runtime.status_detail).toBe('Preparing DDColor.');

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'progress',
      data: { job_id: 'job-steps-1', percentage: 31, completed_tiles: 0, total_tiles: 1 },
    });
    expect(snapshot.runtime.status_title).toBe('Step 2 of 4 · Colour');

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'stage_completed',
      data: {
        job_id: 'job-steps-1',
        stage_index: 1,
        stage_count: 4,
        stage_kind: 'restore',
        step: 'restore',
        fix: 'colour',
        model_id: 'ddcolor_modelscope',
      },
    });
    expect(snapshot.runtime.status_detail).toBe('Colour done.');
    expect(snapshot.runtime.job_steps?.[1].state).toBe('done');

    const stepUrl = 'asset://localhost/%2Fcache%2Fpreviews%2Fstep1.jpg';
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'stage_result',
      data: {
        job_id: 'job-steps-1',
        stage_index: 1,
        stage_count: 4,
        step: 'restore',
        fix: 'colour',
        skipped: false,
        note: '',
        preview_url: stepUrl,
        image_path: '/work/steps/job-steps-1/1.png',
      },
    });
    expect(snapshot.runtime.job_steps?.[1]).toMatchObject({
      state: 'done',
      preview_url: stepUrl,
      kept: true,
    });

    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_completed',
      data: { job_id: 'job-steps-1', output_path: '/out/final.png' },
    });
    expect(snapshot.runtime.job_steps).toEqual([]);
  });
});
