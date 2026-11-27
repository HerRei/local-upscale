import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { planMarks, stepLabel, stepName } from './jobSteps';
import { resolvePlan } from './plan';
import { planLine } from './planLine';
import type { JobStep, UiSettings } from './types';

function step(overrides: Partial<JobStep> = {}): JobStep {
  return {
    kind: 'restore',
    fix: '',
    model_id: '',
    state: 'waiting',
    note: '',
    preview_url: '',
    kept: false,
    ...overrides,
  };
}

describe('jobSteps', () => {
  const settings: Pick<
    UiSettings,
    'task' | 'output_scale' | 'video_target_resolution' | 'video_interpolation'
  > = {
    task: 'upscale',
    output_scale: 4,
    video_target_resolution: 0,
    video_interpolation: 'off',
  };

  it('formats the running step label and plan marks for a four-step job', () => {
    const steps: JobStep[] = [
      step({ kind: 'deblock', fix: 'jpeg', model_id: 'fbcnn_color', state: 'done' }),
      step({ kind: 'restore', fix: 'colour', model_id: 'ddcolor_modelscope', state: 'running' }),
      step({ kind: 'restore', fix: 'noise', model_id: 'denoise_realplksr_1x', state: 'waiting' }),
      step({ kind: 'upscale', fix: '', model_id: 'span_photo_x4', state: 'waiting' }),
    ];
    const names = steps.map((entry) => stepName(entry, settings, false));
    expect(names).toEqual(['JPEG', 'Colour', 'Noise', 'Upscale 4×']);
    expect(stepLabel(steps, settings, false)).toBe('Step 2 of 4 · Colour');
    expect(planMarks(steps, names)).toEqual([
      { name: 'JPEG', state: 'done' },
      { name: 'Colour', state: 'running' },
      { name: 'Noise', state: 'waiting' },
      { name: 'Upscale 4×', state: 'waiting' },
    ]);
  });

  it('formats a skipped first step', () => {
    const steps: JobStep[] = [
      step({
        kind: 'restore',
        fix: 'blur',
        model_id: 'nafnet_gopro_deblur',
        state: 'skipped',
        note: 'Blur model did not improve this image.',
      }),
      step({ kind: 'deblock', fix: 'jpeg', model_id: 'fbcnn_color', state: 'waiting' }),
      step({ kind: 'restore', fix: 'noise', model_id: 'denoise_realplksr_1x', state: 'waiting' }),
      step({ kind: 'upscale', fix: '', model_id: 'span_photo_x4', state: 'waiting' }),
    ];
    expect(stepLabel(steps, settings, false)).toBe('Step 1 of 4 · Blur skipped');
  });

  it('returns no label and no marks for one step', () => {
    const steps: JobStep[] = [
      step({ kind: 'upscale', fix: '', model_id: 'span_photo_x4', state: 'running' }),
    ];
    expect(stepLabel(steps, settings, false)).toBe('');
    expect(planMarks(steps, ['Upscale 4×'])).toEqual([]);
  });

  it('returns no marks when plan names do not match job_steps length', () => {
    const steps: JobStep[] = [
      step({ kind: 'restore', fix: 'noise', state: 'done' }),
      step({ kind: 'upscale', state: 'running' }),
    ];
    expect(planMarks(steps, ['Noise', 'Colour', 'Upscale 4×'])).toEqual([]);
  });

  it('matches planLine names for photo and video stages', () => {
    const snapshot = demoSnapshot();
    const photoSettings: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      output_scale: 4,
      fix_stages: [
        { fix: 'blur', model_id: 'nafnet_gopro_deblur' },
        { fix: 'jpeg', model_id: 'fbcnn_color' },
        { fix: 'noise', model_id: 'denoise_realplksr_1x' },
        { fix: 'colour', model_id: 'ddcolor_modelscope' },
      ],
      selected_model_id: 'span_photo_x4',
    };
    const photoPlan = resolvePlan(snapshot, photoSettings, {
      faceEnabled: false,
      usingTemporalVideo: false,
    });
    const photoSteps = photoPlan.map((stage) =>
      step({ kind: stage.kind, fix: stage.fix ?? '', model_id: stage.model?.model_id ?? '' }),
    );
    expect(photoSteps.map((s) => stepName(s, photoSettings, false))).toEqual(
      planLine(photoPlan, photoSettings, { kind: 'image' }),
    );

    const restoreSettings: UiSettings = {
      ...snapshot.settings,
      task: 'denoise',
      output_scale: 1,
      fix_stages: [],
      custom_model_path: '/custom/fix.safetensors',
    };
    const restorePlan = resolvePlan(snapshot, restoreSettings, {
      faceEnabled: false,
      usingTemporalVideo: false,
    });
    const restoreSteps = restorePlan.map((stage) =>
      step({ kind: stage.kind, fix: stage.fix ?? '' }),
    );
    expect(restoreSteps.map((s) => stepName(s, restoreSettings, false))).toEqual(
      planLine(restorePlan, restoreSettings, { kind: 'image' }),
    );

    expect(
      stepName(step({ kind: 'deblock', fix: 'jpeg' }), { ...settings, task: 'video' }, true),
    ).toBe('Compression');
    expect(
      stepName(
        step({ kind: 'video' }),
        { ...settings, task: 'video', video_target_resolution: 1080 },
        true,
      ),
    ).toBe('Upscale to 1080 px');
  });
});
