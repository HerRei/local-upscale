import { describe, expect, it } from 'vitest';
import presetPicks from '../../../tests/fixtures/preset-picks.json';
import { demoSnapshot } from './demo';
import type { AppSnapshot } from './types';
import {
  applyWorkerEnvelope,
  chooseFixModel,
  choosePresetModel,
  displayName,
  fitFor,
  formatBytes,
  formatDuration,
  frameRateChange,
  interpolationLabel,
  libraryModelsForTask,
  modelsAvailableBeyondSelection,
  modelsForTask,
  moveFixStage,
  pendingDownloads,
  resolvePlan,
  resultPreviewForSelectedMedia,
  toggleFixStage,
} from './state';

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

  it('formats bounded human-readable values', () => {
    expect(formatBytes(1024 ** 3)).toBe('1.0 GB');
    expect(formatDuration(125)).toBe('2:05');
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

describe('model library resolution', () => {
  it('resolves Quick and Best by content and honours a pinned slot', () => {
    const snapshot = demoSnapshot();
    expect(choosePresetModel(snapshot, 'upscale', 'best', 'illustration')?.model_id).toBe(
      'realplksr_nomoswebphoto_x4',
    );
    expect(choosePresetModel(snapshot, 'upscale', 'quick', 'illustration')?.model_id).toBe(
      'span_photo_x4',
    );
    expect(
      choosePresetModel(snapshot, 'upscale', 'best', 'illustration', {
        'upscale/illustration/best': 'realplksr_hfa2k_anime_x4',
      })?.model_id,
    ).toBe('realplksr_hfa2k_anime_x4');
    expect(
      choosePresetModel(snapshot, 'upscale', 'best', 'photo', {
        'upscale/photo/best': 'hat_l_x4_imagenet',
      })?.model_id,
    ).toBe('hat_l_x4_imagenet');
    // A pin naming a model that is not a candidate for the task is ignored.
    expect(
      choosePresetModel(snapshot, 'upscale', 'best', 'photo', {
        'upscale/photo/best': 'fbcnn_color',
      })?.model_id,
    ).toBe('realplksr_nomoswebphoto_x4');
  });

  it('never lets Quick or Best pick a checkpoint with unresolved rights', () => {
    const snapshot = demoSnapshot();
    const best = snapshot.catalog.models.find(
      (model) => model.model_id === 'realplksr_nomoswebphoto_x4',
    )!;
    snapshot.catalog.models.push({
      ...best,
      model_id: 'tempting_unverified_x4',
      quality_tier: 4,
      size_bytes: best.size_bytes + 1,
      commercial_use_allowed: null,
      rights_status: 'unresolved',
      support_tier: 'labs',
    });
    expect(choosePresetModel(snapshot, 'upscale', 'best')?.model_id).toBe(
      'realplksr_nomoswebphoto_x4',
    );
  });

  it('maps fix chips to restoration checkpoints by quality', () => {
    const snapshot = demoSnapshot();
    expect(chooseFixModel(snapshot, 'jpeg', 'best')?.model_id).toBe('fbcnn_color');
    expect(chooseFixModel(snapshot, 'blur', 'quick')?.model_id).toBe('nafnet_gopro_deblur');
    expect(chooseFixModel(snapshot, 'noise', 'quick')?.model_id).toBe('denoise_realplksr_1x');
    expect(chooseFixModel(snapshot, 'noise', 'best')?.model_id).toBe('nafnet_sidd_width64');
  });

  it('reads a remembered fix model first, but only one that fits that problem', () => {
    const snapshot = demoSnapshot();
    const pins = { 'fix/noise/best': 'scunet_color_real_psnr' };
    expect(chooseFixModel(snapshot, 'noise', 'best', pins)?.model_id).toBe(
      'scunet_color_real_psnr',
    );
    // Quick keeps its own default; the choice was made for Best.
    expect(chooseFixModel(snapshot, 'noise', 'quick', pins)?.model_id).toBe('denoise_realplksr_1x');
    // A pin naming no model, a model for another problem, or an upscaler falls back.
    for (const id of ['gone', 'fbcnn_color', 'span_photo_x4']) {
      expect(chooseFixModel(snapshot, 'noise', 'best', { 'fix/noise/best': id })?.model_id).toBe(
        'nafnet_sidd_width64',
      );
    }
    const scunet = snapshot.catalog.models.find(
      (model) => model.model_id === 'scunet_color_real_psnr',
    )!;
    scunet.commercial_use_allowed = false;
    expect(chooseFixModel(snapshot, 'noise', 'best', pins)?.model_id).toBe('nafnet_sidd_width64');
    // Kept out of the automatic choice, but the user chose it.
    scunet.commercial_use_allowed = true;
    scunet.preset_eligible = false;
    expect(chooseFixModel(snapshot, 'noise', 'best', pins)?.model_id).toBe(
      'scunet_color_real_psnr',
    );
  });

  it('every fix chip has a model in the bundled catalog for Quick and Best', () => {
    // The plan has no row for a fix without a model, so the catalog must never lose one.
    const snapshot = demoSnapshot();
    for (const fix of ['noise', 'jpeg', 'blur'] as const) {
      for (const quality of ['quick', 'best'] as const) {
        expect(chooseFixModel(snapshot, fix, quality)).toBeTruthy();
      }
    }
  });

  it('no fix model needs a licence acceptance or the library would offer no way to accept it', () => {
    const snapshot = demoSnapshot();
    const gated = snapshot.catalog.models.filter(
      (model) =>
        model.terms_acceptance_required &&
        (model.native_scale === 1 || model.automated_download_allowed),
    );
    expect(gated.map((model) => model.model_id)).toEqual([]);
  });

  it('names the fix row after the problem and marks a remembered choice', () => {
    const snapshot = demoSnapshot();
    const settings = {
      ...snapshot.settings,
      task: 'upscale' as const,
      quality: 'best' as const,
      fixes: ['noise' as const],
      selected_model_id: 'span_photo_x4',
      fix_stages: [{ fix: 'noise' as const, model_id: 'scunet_color_real_psnr' }],
      preset_pins: {},
    };
    const options = { faceEnabled: false, usingTemporalVideo: false };
    let [fix, upscale] = resolvePlan(snapshot, settings, options);
    expect([fix.role, fix.fix, fix.label, fix.remembered]).toEqual([
      'fix',
      'noise',
      'Fix noise',
      false,
    ]);
    expect(upscale.role).toBe('primary');
    [fix] = resolvePlan(
      snapshot,
      { ...settings, preset_pins: { 'fix/noise/best': 'scunet_color_real_psnr' } },
      options,
    );
    expect(fix.remembered).toBe(true);
    // The pin is for Quick, this job is Best: not this job's remembered choice.
    [fix] = resolvePlan(
      snapshot,
      { ...settings, preset_pins: { 'fix/noise/quick': 'scunet_color_real_psnr' } },
      options,
    );
    expect(fix.remembered).toBe(false);
    // A recipe saved before chips existed: the model fixes one problem, so that is the label.
    [fix] = resolvePlan(
      snapshot,
      { ...settings, fixes: [], fix_stages: [{ fix: 'jpeg' as const, model_id: 'fbcnn_color' }] },
      options,
    );
    expect([fix.fix, fix.label]).toEqual(['jpeg', 'Fix JPEG']);
    // In Restore the model is the primary; the active chip still names its problem.
    const [restore] = resolvePlan(
      snapshot,
      {
        ...settings,
        task: 'denoise',
        selected_model_id: 'scunet_color_real_psnr',
        fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }],
        preset_pins: { 'fix/noise/best': 'scunet_color_real_psnr' },
      },
      options,
    );
    expect([restore.role, restore.fix, restore.label, restore.remembered]).toEqual([
      'primary',
      'noise',
      'Restore',
      true,
    ]);
  });

  it("keeps a finished job's note, such as a skipped fix, on the status line", () => {
    let snapshot = demoSnapshot();
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_completed',
      data: {
        job_id: 'job',
        output_path: '/out.png',
        notes: 'The fix before upscaling was skipped.',
      },
    });
    expect(snapshot.runtime.status_title).toBe('Complete');
    expect(snapshot.runtime.status_detail).toBe('The fix before upscaling was skipped.');
    snapshot = applyWorkerEnvelope(snapshot, {
      type: 'job_completed',
      data: { job_id: 'job', output_path: '/out.png' },
    });
    expect(snapshot.runtime.status_detail).toBe('The output was written successfully.');
  });

  it('never picks a fix model that is kept out of the presets', () => {
    const snapshot = demoSnapshot();
    snapshot.catalog.models.find((model) => model.model_id === 'fbcnn_color')!.preset_eligible =
      false;
    expect(chooseFixModel(snapshot, 'jpeg', 'best')?.model_id).not.toBe('fbcnn_color');
  });

  it('lists the stages a job will run and what still needs downloading', () => {
    const snapshot = demoSnapshot();
    snapshot.settings.task = 'upscale';
    snapshot.settings.fix_stages = [{ fix: 'jpeg' as const, model_id: 'fbcnn_color' }];
    snapshot.settings.selected_model_id = 'realplksr_nomoswebphoto_x4';
    snapshot.catalog.models.find(
      (model) => model.model_id === 'realplksr_nomoswebphoto_x4',
    )!.installed = true;
    const face = snapshot.catalog.models.find((model) => model.model_id === 'hat_l_x4_face');
    const plan = resolvePlan(snapshot, snapshot.settings, {
      faceModel: face,
      faceEnabled: true,
      usingTemporalVideo: false,
    });
    expect(plan.map((stage) => stage.kind)).toEqual(['deblock', 'upscale', 'face_restore']);
    expect(plan[0].canDownload).toBe(true);
    expect(plan[1].installed).toBe(true);
    expect(plan[2].needsFile).toBe(true);
    const pending = pendingDownloads(plan);
    expect(pending.stages.map((stage) => stage.model?.model_id)).toEqual(['fbcnn_color']);
    expect(pending.bytes).toBe(plan[0].model!.size_bytes);
  });

  it('adds frame interpolation to a frame-by-frame video plan in the order it runs', () => {
    const snapshot = demoSnapshot();
    const settings = {
      ...snapshot.settings,
      task: 'video' as const,
      selected_model_id: 'span_photo_x4',
      video_interpolation: 'fps60' as const,
      video_interpolation_order: 'after_upscale' as const,
    };
    const options = { faceEnabled: false, usingTemporalVideo: false };
    let plan = resolvePlan(snapshot, settings, options);
    expect(plan.map((stage) => stage.kind)).toEqual(['upscale', 'interpolate']);
    const interpolate = plan[1];
    expect(interpolate.label).toBe('Interpolate to 60 fps');
    expect(interpolate.interpolationModel?.model_id).toBe('rife_4_26');
    expect(interpolate.canDownload).toBe(true);
    expect(interpolate.sizeBytes).toBe(24_636_301);
    expect(pendingDownloads(plan).stages).toContain(interpolate);

    plan = resolvePlan(
      snapshot,
      { ...settings, video_interpolation_order: 'before_upscale' },
      options,
    );
    expect(plan.map((stage) => stage.kind)).toEqual(['interpolate', 'upscale']);
    for (const changed of [
      { video_interpolation: 'off' as const },
      { video_interpolation: undefined },
      { task: 'upscale' as const },
    ]) {
      expect(
        resolvePlan(snapshot, { ...settings, ...changed }, options).map((s) => s.kind),
      ).toEqual(['upscale']);
    }
    expect(
      resolvePlan(snapshot, settings, { ...options, usingTemporalVideo: true }).some(
        (stage) => stage.kind === 'interpolate',
      ),
    ).toBe(false);
  });

  it('names interpolation targets and the frame rate they produce', () => {
    expect(interpolationLabel('x2')).toBe('Interpolate 2×');
    expect(interpolationLabel('x4')).toBe('Interpolate 4×');
    expect(interpolationLabel('fps120')).toBe('Interpolate to 120 fps');
    expect(frameRateChange('x2', 29.97)).toBe('29.97 → 59.94 fps');
    expect(frameRateChange('fps60', 23.976)).toBe('23.976 → 60 fps');
    expect(frameRateChange('fps60', 59.94)).toBe('Already 59.94 fps · kept as it is');
    expect(frameRateChange('fps60', 0)).toBe('');
  });

  it('judges hardware fit against the device memory', () => {
    const device = {
      id: 'mps',
      type: 'mps',
      name: 'Apple GPU',
      total_memory: 8 * 1024 ** 3,
      free_memory: 4 * 1024 ** 3,
      supports_fp16: true,
      is_integrated: true,
      recommended_tile_sizes: [64],
    };
    expect(fitFor({ vram_estimate_mb: 1200 }, device)).toBe('runs');
    expect(fitFor({ vram_estimate_mb: 6000 }, device)).toBe('heavy');
    expect(fitFor({ vram_estimate_mb: 9000 }, device)).toBe('too_heavy');
    expect(fitFor({ vram_estimate_mb: 0 }, device)).toBe('unknown');
    expect(fitFor({ vram_estimate_mb: 1200 }, undefined)).toBe('unknown');
  });

  it('shortens catalog names for the library rows', () => {
    expect(displayName({ name: 'RealPLKSR 4x NomosWebPhoto — Best' })).toBe(
      'RealPLKSR ×4 NomosWebPhoto',
    );
    expect(displayName({ name: 'HAT-L ×4 ImageNet — Large' })).toBe('HAT-L ×4 ImageNet');
    expect(displayName({ name: 'x', display_name: 'Given' })).toBe('Given');
  });
});

describe('several fixes in one pass', () => {
  const blur = { fix: 'blur' as const, model_id: 'nafnet_gopro_deblur' };
  const jpeg = { fix: 'jpeg' as const, model_id: 'fbcnn_color' };
  const noise = { fix: 'noise' as const, model_id: 'scunet_color_real_psnr' };

  it('turns a fix on at its place in the order blur, JPEG, noise and off again', () => {
    expect(toggleFixStage([], 'noise').map((stage) => stage.fix)).toEqual(['noise']);
    expect(toggleFixStage([noise], 'blur').map((stage) => stage.fix)).toEqual(['blur', 'noise']);
    expect(toggleFixStage([blur, noise], 'jpeg').map((stage) => stage.fix)).toEqual([
      'blur',
      'jpeg',
      'noise',
    ]);
    // A new fix has no model yet; resolving the recipe gives it one.
    expect(toggleFixStage([blur], 'jpeg')[1]).toEqual({ fix: 'jpeg', model_id: '' });
    // An order the user changed stays; the new fix goes before the first that follows it.
    expect(toggleFixStage([noise, blur], 'jpeg').map((stage) => stage.fix)).toEqual([
      'jpeg',
      'noise',
      'blur',
    ]);
    expect(toggleFixStage([blur, jpeg, noise], 'jpeg')).toEqual([blur, noise]);
  });

  it('moves one fix earlier or later and leaves the ends alone', () => {
    expect(moveFixStage([blur, jpeg, noise], 'noise', -1)).toEqual([blur, noise, jpeg]);
    expect(moveFixStage([blur, jpeg, noise], 'blur', 1)).toEqual([jpeg, blur, noise]);
    const stages = [blur, noise];
    expect(moveFixStage(stages, 'blur', -1)).toBe(stages);
    expect(moveFixStage(stages, 'noise', 1)).toBe(stages);
    expect(moveFixStage(stages, 'jpeg', 1)).toBe(stages);
  });

  it('keeps every ordered fix row visible, deduplicates downloads, and identifies the Restore primary', () => {
    const snapshot = demoSnapshot();
    const settings = {
      ...snapshot.settings,
      task: 'upscale' as const,
      selected_model_id: 'span_photo_x4',
      fix_stages: [blur, { fix: 'jpeg' as const, model_id: 'missing' }, noise],
    };
    const options = { faceEnabled: false, usingTemporalVideo: false };
    const plan = resolvePlan(snapshot, settings, options);
    expect(plan.map((stage) => [stage.role, stage.fix, stage.kind])).toEqual([
      ['fix', 'blur', 'restore'],
      ['fix', 'jpeg', 'deblock'],
      ['fix', 'noise', 'restore'],
      ['primary', undefined, 'upscale'],
    ]);
    // A fix without a model stays in the plan and blocks Start; it needs no file.
    expect(plan[1]).toMatchObject({
      label: 'Fix JPEG',
      missing: true,
      installed: false,
      canDownload: false,
      needsFile: false,
    });
    // A model that does not fix the problem it is listed for is no model for it.
    const misfit = resolvePlan(
      snapshot,
      { ...settings, fix_stages: [{ fix: 'blur' as const, model_id: 'fbcnn_color' }] },
      options,
    );
    expect(misfit[0]).toMatchObject({ fix: 'blur', missing: true });
    const downloads = pendingDownloads([...plan, plan[0]]);
    expect(downloads.stages.filter((stage) => stage.fix === 'blur')).toHaveLength(1);
    expect(downloads.bytes).toBe(plan[0].sizeBytes + plan[2].sizeBytes + plan[3].sizeBytes);
    const restore = resolvePlan(
      snapshot,
      { ...settings, task: 'denoise', selected_model_id: 'scunet_color_real_psnr' },
      options,
    );
    expect(restore.map((stage) => [stage.role, stage.fix, stage.label])).toEqual([
      ['fix', 'blur', 'Fix blur'],
      ['fix', 'jpeg', 'Fix JPEG'],
      ['primary', 'noise', 'Restore'],
    ]);
  });

  it('counts a model used by two stages once', () => {
    const snapshot = demoSnapshot();
    const model = snapshot.catalog.models.find((entry) => entry.model_id === 'fbcnn_color')!;
    model.installed = false;
    const plan = resolvePlan(
      snapshot,
      {
        ...snapshot.settings,
        task: 'denoise',
        selected_model_id: 'fbcnn_color',
        fix_stages: [jpeg],
      },
      { faceEnabled: false, usingTemporalVideo: false },
    );
    expect(pendingDownloads([...plan, ...plan]).bytes).toBe(model.size_bytes);
  });

  it('keeps the running stage in the title while tiles progress in a job of several stages', () => {
    const title = (index: number, count: number): string => {
      let snapshot = applyWorkerEnvelope(demoSnapshot(), {
        type: 'stage_started',
        data: {
          job_id: 'j',
          stage_index: index,
          stage_count: count,
          stage_kind: 'restore',
          model_id: 'noise',
        },
      });
      snapshot = applyWorkerEnvelope(snapshot, {
        type: 'progress',
        data: { job_id: 'j', percentage: 45, completed_tiles: 1, total_tiles: 4 },
      });
      return snapshot.runtime.status_title;
    };
    expect(title(2, 5)).toBe('Restoring · stage 3/5');
    // A one-stage job reads as before.
    expect(title(0, 1)).toBe('Enhancing');
  });
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
