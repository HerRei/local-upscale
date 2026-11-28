import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import type { Recipe, UiSettings } from './types';
import { applyWorkerEnvelope } from './workerEvents';
import { chooseFixModel } from './fixStages';
import {
  choosePresetModel,
  displayName,
  fitFor,
  frameRateChange,
  isSlowMotionResult,
  resultFps,
  interpolationLabel,
  pendingDownloads,
  presetDownload,
  resolvePlan,
  skipsUpscaleModel,
  supportsHdrPreservation,
} from './plan';
import { recipeFixStages, recipeMatches } from './recipes';
import { buildStartInput } from './startInput';

describe('isSlowMotionResult', () => {
  it('takes a finished result at least 1.5 times as long as its original', () => {
    const original = { duration_seconds: 10 };
    expect(isSlowMotionResult({ output_duration_seconds: 20 }, original)).toBe(true);
    expect(isSlowMotionResult({ output_duration_seconds: 15 }, original)).toBe(true);
    expect(isSlowMotionResult({ output_duration_seconds: 14.9 }, original)).toBe(false);
    expect(isSlowMotionResult({ output_duration_seconds: 20, partial: true }, original)).toBe(
      false,
    );
    expect(isSlowMotionResult({ output_duration_seconds: 20 }, { duration_seconds: 0 })).toBe(
      false,
    );
    expect(isSlowMotionResult(undefined, original)).toBe(false);
  });
});

describe('model library resolution', () => {
  it('resolves Quick and Best by content and honours a pinned slot', () => {
    const snapshot = demoSnapshot();
    expect(choosePresetModel(snapshot, 'upscale', 'best', 'illustration')?.model_id).toBe(
      'esrgan_hfa2k_x4',
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
    for (const fix of ['noise', 'jpeg', 'blur', 'colour'] as const) {
      for (const quality of ['quick', 'best'] as const) {
        expect(chooseFixModel(snapshot, fix, quality)).toBeTruthy();
      }
    }
  });

  it('picks the expected bundled defaults for every fix in Quick and Best', () => {
    const snapshot = demoSnapshot();
    expect(chooseFixModel(snapshot, 'noise', 'quick')?.model_id).toBe('denoise_realplksr_1x');
    expect(chooseFixModel(snapshot, 'noise', 'best')?.model_id).toBe('nafnet_sidd_width64');
    for (const quality of ['quick', 'best'] as const) {
      expect(chooseFixModel(snapshot, 'blur', quality)?.model_id).toBe('nafnet_gopro_deblur');
      expect(chooseFixModel(snapshot, 'jpeg', quality)?.model_id).toBe('fbcnn_color');
      expect(chooseFixModel(snapshot, 'colour', quality)?.model_id).toBe('ddcolor_modelscope');
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
    // At Same size the primary row is named after its last fix; the active chip marks it remembered.
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
      'Fix noise',
      true,
    ]);
  });

  it('counts fix downloads in presetDownload and drops the hidden default at Same size', () => {
    const snapshot = demoSnapshot();
    const ui: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      content: 'photo',
      output_scale: 4,
      fix_stages: [
        { fix: 'noise', model_id: 'denoise_realplksr_1x' },
        { fix: 'blur', model_id: 'nafnet_gopro_deblur' },
      ],
      fixes: ['noise', 'blur'],
      preset_pins: {},
    };
    expect(presetDownload('quick', snapshot, ui)).toBe('304 MB download');

    snapshot.catalog.models.find((m) => m.model_id === 'localsr_quick_x4_v1')!.installed = true;
    expect(presetDownload('quick', snapshot, ui)).toBe('301 MB download');
    expect(presetDownload('best', snapshot, ui)).toBe('766 MB download');

    const sameSize = { ...ui, task: 'denoise' as const, output_scale: 1 };
    expect(presetDownload('quick', snapshot, sameSize)).toBe('301 MB download');
    expect(presetDownload('best', snapshot, sameSize)).toBe('736 MB download');

    const noFixSameSize = { ...sameSize, fix_stages: [], fixes: [] };
    expect(presetDownload('quick', snapshot, noFixSameSize)).toBe('');
    expect(presetDownload('best', snapshot, noFixSameSize)).toBe('');

    const pinnedNoise = {
      ...sameSize,
      fix_stages: [{ fix: 'noise' as const, model_id: 'scunet_color_real_psnr' }],
      fixes: ['noise' as const],
      preset_pins: { 'fix/noise/quick': 'scunet_color_real_psnr' },
    };
    expect(presetDownload('quick', snapshot, pinnedNoise)).toBe('72 MB download');
    expect(presetDownload('best', snapshot, pinnedNoise)).toBe('464 MB download');
  });

  it('labels Same size plan rows by fix, Fix for own file, Colour for colour, and omits a catalog primary without fixes', () => {
    const snapshot = demoSnapshot();
    const options = { faceEnabled: false, usingTemporalVideo: false };
    const twoFixes = resolvePlan(
      snapshot,
      {
        ...snapshot.settings,
        task: 'denoise',
        output_scale: 1,
        selected_model_id: 'denoise_realplksr_1x',
        fix_stages: [
          { fix: 'blur', model_id: 'nafnet_gopro_deblur' },
          { fix: 'noise', model_id: 'denoise_realplksr_1x' },
        ],
        fixes: ['blur', 'noise'],
      },
      options,
    );
    expect(twoFixes.map((s) => [s.label, s.role, s.kind])).toEqual([
      ['Fix blur', 'fix', 'restore'],
      ['Fix noise', 'primary', 'restore'],
    ]);
    expect(twoFixes.some((s) => s.label === 'Restore')).toBe(false);

    const ownFile = resolvePlan(
      snapshot,
      {
        ...snapshot.settings,
        task: 'denoise',
        output_scale: 1,
        selected_model_id: '__custom__',
        custom_model_path: '/models/own.safetensors',
        fix_stages: [],
        fixes: [],
      },
      options,
    );
    expect(ownFile.map((s) => [s.label, s.role, s.kind])).toEqual([['Fix', 'primary', 'restore']]);

    const noFixCatalog = resolvePlan(
      snapshot,
      {
        ...snapshot.settings,
        task: 'denoise',
        output_scale: 1,
        selected_model_id: 'denoise_realplksr_1x',
        fix_stages: [],
        fixes: [],
      },
      options,
    );
    expect(noFixCatalog).toEqual([]);

    const colourLast = resolvePlan(
      snapshot,
      {
        ...snapshot.settings,
        task: 'denoise',
        output_scale: 1,
        selected_model_id: 'ddcolor_modelscope',
        fix_stages: [{ fix: 'colour', model_id: 'ddcolor_modelscope' }],
        fixes: ['colour'],
      },
      options,
    );
    expect(colourLast.map((s) => [s.label, s.role])).toEqual([['Colour', 'primary']]);
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
    expect(resultFps('x2', 24)).toBe(48);
    expect(resultFps('fps60', 59.94)).toBe(59.94);
    expect(resultFps('fps60', 0)).toBeNull();
    expect(frameRateChange('x2', 29.97)).toBe('29.97 → 59.94 fps');
    expect(frameRateChange('fps60', 23.976)).toBe('23.976 → 60 fps');
    expect(frameRateChange('fps60', 59.94)).toBe('Already 59.94 fps · kept as it is');
    expect(frameRateChange('fps60', 0)).toBe('');
  });

  it('leaves Clean up audio out of the plan and the start input for slow motion', () => {
    const snapshot = demoSnapshot();
    const settings: UiSettings = {
      ...snapshot.settings,
      task: 'video',
      selected_model_id: 'span_photo_x4',
      selected_video_model_id: 'frame_by_frame',
      video_interpolation: 'x2',
      video_slow_motion: true,
      video_clean_audio: true,
    };
    const options = { faceEnabled: false, usingTemporalVideo: false, hasAudio: true };
    const start = { mediaIds: ['m'], task: 'video' as const, batchMode: false };
    expect(resolvePlan(snapshot, settings, options).map((stage) => stage.kind)).toEqual([
      'upscale',
      'interpolate',
    ]);
    const input = buildStartInput(settings, start);
    expect(input.video_slow_motion).toBe(true);
    expect(input.video_clean_audio).toBe(false);

    // Only 2x and 4x count; any other target runs at normal speed with its sound.
    const sixty = { ...settings, video_interpolation: 'fps60' as const };
    expect(resolvePlan(snapshot, sixty, options).map((stage) => stage.kind)).toEqual([
      'audio_denoise',
      'upscale',
      'interpolate',
    ]);
    expect(buildStartInput(sixty, start).video_slow_motion).toBe(false);
    expect(buildStartInput(sixty, start).video_clean_audio).toBe(true);
    expect(
      buildStartInput({ ...settings, video_slow_motion: false }, start).video_clean_audio,
    ).toBe(true);
  });

  it('adds Clean up audio with its pending download only for videos with audio', () => {
    const snapshot = demoSnapshot();
    const audioModel = snapshot.catalog.audio_models?.[0];
    expect(audioModel?.model_id).toBe('deepfilternet3');
    if (audioModel) audioModel.installed = false;
    const upscaler = snapshot.catalog.models.find((m) => m.model_id === 'span_photo_x4');
    if (upscaler) upscaler.installed = true;
    const settings: UiSettings = {
      ...snapshot.settings,
      task: 'video',
      selected_model_id: 'span_photo_x4',
      selected_video_model_id: 'frame_by_frame',
      video_clean_audio: true,
    };
    const options = { faceEnabled: false, usingTemporalVideo: false, hasAudio: true };

    const plan = resolvePlan(snapshot, settings, options);
    expect(plan.map((stage) => stage.kind)).toEqual(['audio_denoise', 'upscale']);
    const audioStage = plan[0];
    expect(audioStage.audioModel?.model_id).toBe('deepfilternet3');
    expect(audioStage.canDownload).toBe(true);
    expect(audioStage.sizeBytes).toBe(7_983_136);
    expect(pendingDownloads(plan).stages).toEqual([audioStage]);

    expect(
      resolvePlan(snapshot, settings, { ...options, hasAudio: false }).map((s) => s.kind),
    ).toEqual(['upscale']);
    expect(
      resolvePlan(snapshot, { ...settings, video_clean_audio: false }, options).map((s) => s.kind),
    ).toEqual(['upscale']);
    expect(
      resolvePlan(snapshot, { ...settings, task: 'upscale' }, options).map((s) => s.kind),
    ).toEqual(['upscale']);

    // Selecting DPDFNet drives the audio_denoise stage and its pending download.
    const dpdf = snapshot.catalog.audio_models?.find((m) => m.model_id === 'dpdfnet8_48khz_hr');
    expect(dpdf).toBeDefined();
    if (dpdf) dpdf.installed = false;
    const dpdfPlan = resolvePlan(
      snapshot,
      { ...settings, video_audio_model: 'dpdfnet8_48khz_hr' },
      options,
    );
    expect(dpdfPlan[0].audioModel?.model_id).toBe('dpdfnet8_48khz_hr');
    expect(dpdfPlan[0].sizeBytes).toBe(14_857_107);
    expect(pendingDownloads(dpdfPlan).stages).toEqual([dpdfPlan[0]]);

    // Unknown ids fall back to the catalog default.
    expect(
      resolvePlan(snapshot, { ...settings, video_audio_model: 'unknown_id' }, options)[0].audioModel
        ?.model_id,
    ).toBe('deepfilternet3');
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

  it('includes the Colour fix for video Restore and Restore first', () => {
    const snapshot = demoSnapshot();
    const settings = {
      ...snapshot.settings,
      task: 'video' as const,
      video_task: 'denoise' as const,
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: 'ddcolor_modelscope',
      fix_stages: [{ fix: 'colour' as const, model_id: 'ddcolor_modelscope' }],
      fixes: ['colour' as const],
    };
    const options = { faceEnabled: false, usingTemporalVideo: false };
    const recipe: Recipe = {
      id: 'colour-video',
      name: 'Colour video',
      task: 'video',
      video_task: 'denoise',
      quality: 'custom',
      model_id: 'ddcolor_modelscope',
      video_model_id: 'frame_by_frame',
      output_scale: 1,
      fixes: ['colour'],
      stages: [{ kind: 'restore', model_id: 'ddcolor_modelscope', fix: 'colour' }],
    } as Recipe;
    expect(recipeFixStages(recipe, snapshot.catalog)).toEqual(settings.fix_stages);
    expect(resolvePlan(snapshot, settings, options).some((stage) => stage.fix === 'colour')).toBe(
      true,
    );
    expect(
      resolvePlan(
        snapshot,
        { ...settings, video_task: 'upscale', selected_model_id: 'span_photo_x4' },
        options,
      ).some((stage) => stage.fix === 'colour'),
    ).toBe(true);
  });

  it('resolves video restore, video fix-first stages, and video recipes', () => {
    const snapshot = demoSnapshot();
    const restoreSettings = {
      ...snapshot.settings,
      task: 'video' as const,
      video_task: 'denoise' as const,
      selected_video_model_id: 'frame_by_frame',
      output_scale: 1,
      halo: 16,
      precision: 'fp32' as const,
      safe_memory: true,
      enable_face_model: false,
      quality: 'custom' as const,
      selected_model_id: 'scunet_color_real_psnr',
      fix_stages: [
        { fix: 'jpeg' as const, model_id: 'fbcnn_color' },
        { fix: 'noise' as const, model_id: 'scunet_color_real_psnr' },
      ],
      fixes: ['jpeg' as const, 'noise' as const],
    };
    const options = { faceEnabled: false, usingTemporalVideo: false };
    const plan = resolvePlan(snapshot, restoreSettings, options);
    expect(plan.map((s) => [s.kind, s.role, s.model?.model_id])).toEqual([
      ['deblock', 'fix', 'fbcnn_color'],
      ['restore', 'primary', 'scunet_color_real_psnr'],
    ]);
    expect(supportsHdrPreservation(restoreSettings, snapshot.catalog)).toBe(false);

    const upscaleWithFixes = {
      ...snapshot.settings,
      task: 'video' as const,
      video_task: 'upscale' as const,
      selected_model_id: 'span_photo_x4',
      fix_stages: [{ fix: 'blur' as const, model_id: 'nafnet_gopro_deblur' }],
      fixes: ['blur' as const],
      video_interpolation: 'x2' as const,
      video_interpolation_order: 'after_upscale' as const,
    };
    expect(resolvePlan(snapshot, upscaleWithFixes, options).map((s) => s.kind)).toEqual([
      'restore',
      'upscale',
      'interpolate',
    ]);

    const videoModel = snapshot.catalog.video_models.find((m) => m.model_id === 'flashvsr_v11')!;
    const temporalSettings = {
      ...snapshot.settings,
      task: 'video' as const,
      video_task: 'upscale' as const,
      selected_video_model_id: videoModel.model_id,
      output_scale: 4,
      video_target_resolution: 0,
      fix_stages: [{ fix: 'blur' as const, model_id: 'nafnet_gopro_deblur' }],
      fixes: ['blur' as const],
    };
    const temporalPlan = resolvePlan(snapshot, temporalSettings, {
      faceEnabled: false,
      usingTemporalVideo: true,
      selectedVideoModel: videoModel,
    });
    expect(temporalPlan.map((s) => [s.kind, s.role, s.label])).toEqual([
      ['video', 'primary', 'Upscale ×4'],
    ]);
    expect(
      resolvePlan(
        snapshot,
        { ...temporalSettings, video_target_resolution: 720 },
        { faceEnabled: false, usingTemporalVideo: true, selectedVideoModel: videoModel },
      ).map((s) => [s.kind, s.role, s.label]),
    ).toEqual([['video', 'primary', 'Upscale']]);

    const restoreRecipe: Recipe = {
      id: 'vr1',
      name: 'Clean video',
      task: 'video' as const,
      video_task: 'denoise' as const,
      quality: 'custom' as const,
      model_id: 'scunet_color_real_psnr',
      video_model_id: 'frame_by_frame',
      output_scale: 1,
      tile_size: restoreSettings.tile_size,
      halo: 16,
      precision: 'fp32' as const,
      safe_memory: true,
      enable_face_model: false,
      fixes: ['jpeg' as const, 'noise' as const],
      stages: [
        { kind: 'deblock', model_id: 'fbcnn_color', fix: 'jpeg' as const },
        { kind: 'restore', model_id: 'scunet_color_real_psnr', fix: 'noise' as const },
      ],
      output_format: 'png',
    };
    expect(recipeFixStages(restoreRecipe, snapshot.catalog)).toEqual([
      { fix: 'jpeg', model_id: 'fbcnn_color' },
      { fix: 'noise', model_id: 'scunet_color_real_psnr' },
    ]);
    expect(recipeMatches(restoreRecipe, restoreSettings, snapshot.catalog)).toBe(true);
    // A non-Restore recipe saved at 1× is treated as Restore.
    expect(
      recipeMatches({ ...restoreRecipe, video_task: 'upscale' }, restoreSettings, snapshot.catalog),
    ).toBe(true);
    expect(
      recipeMatches(
        { ...restoreRecipe, video_task: 'upscale', output_scale: 4 },
        restoreSettings,
        snapshot.catalog,
      ),
    ).toBe(false);
    // Recipes with Faces in fixes/stages still match when applied without Faces.
    expect(
      recipeMatches(
        {
          id: 'r-face',
          name: 'Portrait',
          task: 'upscale',
          quality: 'custom',
          model_id: 'hat_s_x4',
          output_scale: 4,
          tile_size: 256,
          halo: 32,
          precision: 'fp32',
          safe_memory: true,
          enable_face_model: true,
          fixes: ['faces'],
          stages: [
            { kind: 'upscale', model_id: 'hat_s_x4' },
            { kind: 'face_restore', model_id: 'hat_s_x4_face', fidelity: 0.7 },
          ],
        },
        {
          ...snapshot.settings,
          task: 'upscale',
          quality: 'custom',
          selected_model_id: 'hat_s_x4',
          output_scale: 4,
          tile_size: 256,
          halo: 32,
          precision: 'fp32',
          safe_memory: true,
          enable_face_model: false,
          fixes: [],
          fix_stages: [],
        },
        snapshot.catalog,
      ),
    ).toBe(true);
  });

  it('omits the restore stage in no-chip video Restore and removes 1× from Upscale', () => {
    const snapshot = demoSnapshot();
    const upscaleAt1: UiSettings = {
      ...snapshot.settings,
      task: 'video',
      video_task: 'upscale',
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: 'hat_l_x4_imagenet',
      output_scale: 1,
    };
    expect(skipsUpscaleModel(upscaleAt1)).toBe(false);

    const noChipVideoRestore: UiSettings = {
      ...snapshot.settings,
      task: 'video',
      video_task: 'denoise',
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: '',
      output_scale: 1,
      fix_stages: [],
      fixes: [],
      deflicker: true,
      video_interpolation: 'x2',
      video_clean_audio: true,
    };
    expect(choosePresetModel(snapshot, 'video', 'quick', 'photo', {}, 'denoise')).toBeUndefined();
    expect(choosePresetModel(snapshot, 'denoise', 'quick', 'photo')).toBeTruthy();

    const plan = resolvePlan(snapshot, noChipVideoRestore, {
      faceEnabled: false,
      usingTemporalVideo: false,
      hasAudio: true,
    });
    expect(plan.map((stage) => stage.kind)).toEqual(['audio_denoise', 'deflicker', 'interpolate']);
  });

  it('places the deflicker stage after primary and before after-upscale interpolation, and omits it on SeedVR2 or photos', () => {
    const snapshot = demoSnapshot();
    const baseVideo: UiSettings = {
      ...snapshot.settings,
      task: 'video',
      video_task: 'upscale',
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: 'span_photo_x4',
      output_scale: 4,
      deflicker: true,
      video_interpolation: 'x2',
      video_interpolation_order: 'after_upscale',
    };
    const options = { faceEnabled: false, usingTemporalVideo: false };
    const afterPlan = resolvePlan(snapshot, baseVideo, options);
    expect(afterPlan.map((stage) => stage.kind)).toEqual(['upscale', 'deflicker', 'interpolate']);
    expect(afterPlan[1]).toEqual({
      kind: 'deflicker',
      role: 'step',
      label: 'De-flicker',
      installed: true,
      canDownload: false,
      needsFile: false,
      sizeBytes: 0,
    });
    expect(pendingDownloads(afterPlan).stages.some((stage) => stage.kind === 'deflicker')).toBe(
      false,
    );

    const beforePlan = resolvePlan(
      snapshot,
      { ...baseVideo, video_interpolation_order: 'before_upscale' },
      options,
    );
    expect(beforePlan.map((stage) => stage.kind)).toEqual(['interpolate', 'upscale', 'deflicker']);

    const seedvr2 = snapshot.catalog.video_models.find((m) => m.model_id === 'seedvr2_3b')!;
    const temporalPlan = resolvePlan(
      snapshot,
      { ...baseVideo, selected_video_model_id: 'seedvr2_3b' },
      { faceEnabled: false, usingTemporalVideo: true, selectedVideoModel: seedvr2 },
    );
    expect(temporalPlan.some((stage) => stage.kind === 'deflicker')).toBe(false);

    const photoPlan = resolvePlan(snapshot, { ...baseVideo, task: 'upscale' }, options);
    expect(photoPlan.some((stage) => stage.kind === 'deflicker')).toBe(false);
  });
});
