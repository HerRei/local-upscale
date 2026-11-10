import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { applyWorkerEnvelope } from './workerEvents';
import { chooseFixModel } from './fixStages';
import {
  choosePresetModel,
  displayName,
  fitFor,
  frameRateChange,
  interpolationLabel,
  pendingDownloads,
  resolvePlan,
} from './plan';

describe('model library resolution', () => {
  it('resolves Quick and Best by content and honours a pinned slot', () => {
    const snapshot = demoSnapshot();
    expect(choosePresetModel(snapshot, 'upscale', 'best', 'illustration')?.model_id).toBe(
      'realplksr_hfa2k_anime_x4',
    );
    expect(choosePresetModel(snapshot, 'upscale', 'quick', 'illustration')?.model_id).toBe(
      'realesrgan_x4plus_anime_6b',
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
