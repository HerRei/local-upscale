import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { outputSummary } from './enhancePanel';
import {
  ALPHA_FORMAT_NOTE,
  activeStepStages,
  outputFormatKeepingAlpha,
  pendingDownloads,
  resolvePlan,
  stepStagesReady,
} from './plan';
import { buildStartInput, deriveStartFlags } from './startInput';
import { STEPS, type StepDefinition } from './steps';
import type { AppSnapshot, CatalogModel, StepStageSelection, UiSettings } from './types';

const grain: StepStageSelection = {
  step: 'grain',
  params: { strength: 40, seed: 7 },
  mask_path: '/in/mask.png',
};

function settingsWith(patch: Partial<UiSettings>): UiSettings {
  return {
    ...demoSnapshot().settings,
    selected_model_id: 'span_photo_x4',
    output_scale: 4,
    fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
    step_stages: [{ step: 'tool_from_a_newer_version' }, grain],
    ...patch,
  };
}

describe('step stages', () => {
  it('run after the main model and Faces on photos, as a plan row and in the start input', () => {
    const snapshot = demoSnapshot();
    const settings = settingsWith({ task: 'upscale' });
    const faceModel = snapshot.catalog.models.find((model) => model.model_id === 'hat_l_x4_face');
    const plan = resolvePlan(snapshot, settings, {
      faceModel,
      faceEnabled: true,
      usingTemporalVideo: false,
    });
    expect(plan.map((stage) => stage.kind)).toEqual([
      'restore',
      'upscale',
      'face_restore',
      'grain',
    ]);
    expect(plan.at(-1)).toEqual({
      kind: 'grain',
      role: 'step',
      label: 'Film grain',
      installed: true,
      canDownload: false,
      needsFile: false,
      sizeBytes: 0,
    });
    const input = buildStartInput(settings, { mediaIds: ['m'], task: 'upscale', batchMode: false });
    expect(input.step_stages).toEqual([grain]);

    const restore = settingsWith({
      task: 'denoise',
      selected_model_id: 'denoise_realplksr_1x',
      output_scale: 1,
    });
    expect(
      resolvePlan(snapshot, restore, { faceEnabled: false, usingTemporalVideo: false }).map(
        (stage) => stage.kind,
      ),
    ).toEqual(['restore', 'grain']);
  });

  it('are left out where their media does not run, and the start input omits an empty list', () => {
    const snapshot = demoSnapshot();
    for (const [settings, usingTemporalVideo] of [
      [settingsWith({ task: 'video', selected_video_model_id: 'frame_by_frame' }), false],
      [settingsWith({ task: 'video', selected_video_model_id: 'seedvr2_3b' }), true],
      [settingsWith({ task: 'edit' }), false],
    ] as const) {
      const plan = resolvePlan(snapshot, settings, { faceEnabled: false, usingTemporalVideo });
      expect(plan.some((stage) => stage.kind === 'grain')).toBe(false);
      const input = buildStartInput(settings, {
        mediaIds: ['m'],
        task: settings.task,
        batchMode: false,
      });
      expect(input).not.toHaveProperty('step_stages');
    }
    const plain = buildStartInput(settingsWith({ task: 'upscale', step_stages: undefined }), {
      mediaIds: ['m'],
      task: 'upscale',
      batchMode: false,
    });
    expect(plain).not.toHaveProperty('step_stages');
  });
});

describe('step stages with a model or a matte', () => {
  const grainStep = STEPS.find((step) => step.id === 'grain')!;
  const cutoutStep: StepDefinition = {
    ...grainStep,
    id: 'test_cutout',
    phase: 'cleanup',
    needs_model: true,
    supports_mask: false,
    label: 'Test cut-out',
    stage_label: '',
    required_feature: null,
    dev_only: false,
    parameters: [],
    model_task: 'test_cutout',
    output_alpha: true,
  };
  const steps = [...STEPS, cutoutStep];

  function withCutoutModels(): AppSnapshot {
    const snapshot = demoSnapshot();
    const shipped = snapshot.catalog.models.find(
      (model) => model.model_id === 'denoise_realplksr_1x',
    )!;
    const model = (modelId: string, installed: boolean, downloadable: boolean): CatalogModel => ({
      ...structuredClone(shipped),
      model_id: modelId,
      filename: `${modelId}.safetensors`,
      purposes: ['test_cutout'],
      fixes: [],
      size_bytes: 5000,
      installed,
      automated_download_allowed: downloadable,
    });
    return {
      ...snapshot,
      catalog: {
        ...snapshot.catalog,
        models: [
          ...snapshot.catalog.models.map((entry) => ({
            ...entry,
            installed: entry.model_id === 'span_photo_x4',
          })),
          model('cutout_ready', true, true),
          model('cutout_download', false, true),
        ],
      },
    };
  }

  const photo = (patch: Partial<UiSettings>): UiSettings =>
    settingsWith({
      task: 'upscale',
      fix_stages: [],
      output_format: 'jpg',
      output_directory: '/Users/hermes/Desktop',
      ...patch,
    });

  it('waits for the step model like a fix model: a download row, then ready', () => {
    const snapshot = withCutoutModels();
    const options = { faceEnabled: false, usingTemporalVideo: false, steps };
    const settings = photo({ step_stages: [{ step: 'test_cutout', model_id: 'cutout_download' }] });
    const plan = resolvePlan(snapshot, settings, options);
    expect(plan.map((stage) => stage.kind)).toEqual(['test_cutout', 'upscale']);
    expect(plan[0]).toMatchObject({
      role: 'step',
      label: 'Test cut-out',
      installed: false,
      canDownload: true,
      needsFile: false,
      sizeBytes: 5000,
    });
    expect(plan[0].model?.model_id).toBe('cutout_download');
    expect(stepStagesReady(plan)).toBe(false);
    const downloads = pendingDownloads(plan, { cutout_download: 1200 });
    expect(downloads.stages).toEqual([plan[0]]);
    expect(downloads.left).toEqual({ cutout_download: 3800 });

    const ready = resolvePlan(
      snapshot,
      photo({ step_stages: [{ step: 'test_cutout', model_id: 'cutout_ready' }] }),
      options,
    );
    expect(ready[0]).toMatchObject({ installed: true, canDownload: false, sizeBytes: 0 });
    expect(stepStagesReady(ready)).toBe(true);
    expect(pendingDownloads(ready).stages).toEqual([]);

    for (const selection of [
      { step: 'test_cutout' },
      { step: 'test_cutout', model_id: 'unknown' },
      { step: 'test_cutout', model_id: 'span_photo_x4' },
    ]) {
      const missing = resolvePlan(snapshot, photo({ step_stages: [selection] }), options);
      expect(missing[0]).toMatchObject({ role: 'step', missing: true, installed: false });
      expect(missing[0].model).toBeUndefined();
      expect(stepStagesReady(missing)).toBe(false);
      expect(pendingDownloads(missing).stages).toEqual([]);
    }
    // A model-less step never waits.
    expect(stepStagesReady(resolvePlan(snapshot, photo({ step_stages: [grain] }), options))).toBe(
      true,
    );
  });

  it('saves as PNG, and says so on the output line, unless the format keeps alpha', () => {
    const stepStages: StepStageSelection[] = [{ step: 'test_cutout', model_id: 'cutout_ready' }];
    for (const [format, saved] of [
      ['jpg', 'png'],
      ['heic', 'png'],
      ['png', 'png'],
      ['tif', 'tif'],
      ['webp', 'webp'],
    ] as const) {
      const settings = photo({ output_format: format, step_stages: stepStages });
      const input = buildStartInput(settings, {
        mediaIds: ['m'],
        task: 'upscale',
        batchMode: false,
        steps,
      });
      expect(input.output_format).toBe(saved);
      expect(outputFormatKeepingAlpha(stepStages, format, steps)).toBe(saved);
    }
    expect(outputSummary(photo({ step_stages: stepStages }), { steps })).toBe(
      `PNG · Desktop · ${ALPHA_FORMAT_NOTE}`,
    );
    expect(outputSummary(photo({ output_format: 'tif', step_stages: stepStages }), { steps })).toBe(
      'TIFF · Desktop',
    );
    expect(ALPHA_FORMAT_NOTE).toBe('Saved as PNG to keep the transparent background.');
    // Without the step, or in the shipped registry where it does not exist, nothing changes.
    expect(outputSummary(photo({ step_stages: [grain] }), { steps })).toBe('JPEG 98 · Desktop');
    expect(outputSummary(photo({ step_stages: stepStages }))).toBe('JPEG 98 · Desktop');
    expect(
      buildStartInput(photo({ step_stages: stepStages }), {
        mediaIds: ['m'],
        task: 'upscale',
        batchMode: false,
      }).output_format,
    ).toBe('jpg');
  });

  it('runs alone in Restore with nothing picked', () => {
    const snapshot = withCutoutModels();
    const settings = photo({
      task: 'denoise',
      selected_model_id: '',
      output_scale: 1,
      step_stages: [{ step: 'test_cutout', model_id: 'cutout_ready' }],
    });
    const plan = resolvePlan(snapshot, settings, {
      faceEnabled: false,
      usingTemporalVideo: false,
      steps,
    });
    expect(plan.map((stage) => stage.kind)).toEqual(['test_cutout']);
    expect(deriveStartFlags(settings).noRestoreModel).toBe(true);
    const input = buildStartInput(settings, {
      mediaIds: ['m'],
      task: 'denoise',
      batchMode: false,
      steps,
    });
    expect(input).toMatchObject({ model_id: '', fix_stages: [], output_format: 'png' });
    expect(input.step_stages).toEqual([{ step: 'test_cutout', model_id: 'cutout_ready' }]);
    expect(activeStepStages(settings, false, steps).map(({ step }) => step.id)).toEqual([
      'test_cutout',
    ]);
  });
});
