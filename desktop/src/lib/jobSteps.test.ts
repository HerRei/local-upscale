import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import {
  parseStepTarget,
  planMarks,
  resultStepChips,
  stepLabel,
  stepName,
  stepShowingLabel,
  validStepTarget,
} from './jobSteps';
import { resolvePlan } from './plan';
import { planLine } from './planLine';
import type { JobStep, LastSteps, UiSettings } from './types';

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

  it('names the added frames Slow motion with the flag and 2× frames without it', () => {
    const interpolate = step({ kind: 'interpolate' });
    const video = { ...settings, task: 'video' as const };
    expect(stepName(interpolate, { ...video, video_interpolation: 'x2' }, true)).toBe('2× frames');
    expect(
      stepName(interpolate, { ...video, video_interpolation: 'x2', video_slow_motion: true }, true),
    ).toBe('Slow motion 2×');
    expect(
      stepName(interpolate, { ...video, video_interpolation: 'x4', video_slow_motion: true }, true),
    ).toBe('Slow motion 4×');
    expect(
      stepLabel(
        [step({ kind: 'video', state: 'done' }), { ...interpolate, state: 'running' }],
        { ...video, output_scale: 2, video_interpolation: 'x2', video_slow_motion: true },
        true,
      ),
    ).toBe('Step 2 of 2 · Slow motion 2×');
  });

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

  it('builds step chips, labels, and target validation for last_steps', () => {
    const lastSteps: LastSteps = {
      job_id: 'job-42',
      media_id: 'photo-1',
      steps: [
        step({ kind: 'deblock', fix: 'jpeg', model_id: 'fbcnn_color', state: 'done', kept: true }),
        step({
          kind: 'restore',
          fix: 'blur',
          model_id: 'nafnet_gopro_deblur',
          state: 'skipped',
          note: 'Blur model did not improve this image.',
          kept: false,
        }),
        step({
          kind: 'restore',
          fix: 'noise',
          model_id: 'denoise_realplksr_1x',
          state: 'done',
          kept: true,
        }),
        step({
          kind: 'restore',
          fix: 'colour',
          model_id: 'ddcolor_modelscope',
          state: 'done',
          kept: false,
        }),
        step({ kind: 'upscale', fix: '', model_id: 'span_photo_x4', state: 'done', kept: false }),
      ],
    };

    const chips = resultStepChips(lastSteps, 'job-42', settings, 4);
    expect(chips.map((c) => c.label)).toEqual(['JPEG', 'Blur', 'Noise', 'Colour', '4×']);
    expect(chips.map((c) => c.id)).toEqual([
      'job-42#0',
      'job-42#1',
      'job-42#2',
      'job-42#3',
      'job-42',
    ]);
    expect(chips.map((c) => c.pressed)).toEqual([false, false, false, false, true]);
    expect(chips.map((c) => c.disabled)).toEqual([false, true, false, true, false]);
    expect(chips.map((c) => c.title)).toEqual([
      'The picture after the JPEG fix',
      'Blur model did not improve this image.',
      'The picture after the Noise fix',
      "This step's picture could not be kept.",
      'The finished result',
    ]);

    expect(stepShowingLabel('job-42#2', lastSteps, settings)).toBe('After Noise');
    expect(stepShowingLabel('job-42', lastSteps, settings)).toBe('');
    expect(parseStepTarget('job-42#2')).toEqual({ jobId: 'job-42', index: 2 });
    expect(parseStepTarget('job-42')).toBeNull();
    expect(validStepTarget('job-42#2', lastSteps, 'photo-1')).toBe(true);
    expect(validStepTarget('job-42#1', lastSteps, 'photo-1')).toBe(false);
    expect(validStepTarget('job-42#3', lastSteps, 'photo-1')).toBe(false);
    expect(validStepTarget('job-42#2', lastSteps, 'photo-2')).toBe(false);
    expect(validStepTarget('job-42#2', null, 'photo-1')).toBe(false);

    const restoreOnly: LastSteps = {
      job_id: 'job-99',
      media_id: 'photo-1',
      steps: [
        step({ kind: 'deblock', fix: 'jpeg', state: 'done', kept: true }),
        step({ kind: 'restore', fix: 'noise', state: 'done', kept: false }),
      ],
    };
    const restoreChips = resultStepChips(restoreOnly, 'job-99', settings);
    expect(restoreChips.map((c) => c.label)).toEqual(['JPEG', 'Noise']);
    expect(restoreChips[1].title).toBe('The finished result');
    expect(restoreChips[1].disabled).toBe(false);
    expect(restoreChips[1].pressed).toBe(true);
  });

  it('names the background step and titles its chip', () => {
    expect(stepName(step({ kind: 'remove_background' }), settings)).toBe('Background');
    const lastSteps: LastSteps = {
      job_id: 'job-7',
      media_id: 'photo-1',
      steps: [
        step({
          kind: 'remove_background',
          model_id: 'birefnet_general',
          state: 'done',
          kept: true,
        }),
        step({ kind: 'upscale', model_id: 'span_photo_x4', state: 'done', kept: false }),
      ],
    };
    const chips = resultStepChips(lastSteps, 'job-7', settings, 4);
    expect(chips.map((chip) => chip.label)).toEqual(['Background', '4×']);
    expect(chips[0].title).toBe('The picture with the background removed');
    expect(chips[1].title).toBe('The finished result');
  });
});
