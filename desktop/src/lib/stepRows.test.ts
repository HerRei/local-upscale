import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { backgroundModel, backgroundRemoved, backgroundRowShown, withStepStage } from './stepRows';
import type { StepStageSelection } from './types';

const grain: StepStageSelection = { step: 'grain', params: { strength: 40 } };
const cutout: StepStageSelection = { step: 'remove_background', model_id: 'birefnet_general' };

describe('stepRows', () => {
  it('withStepStage replaces in place, appends and removes', () => {
    const other: StepStageSelection = { step: 'deinterlace', params: { mode: 'field' } };
    const replaced = { step: 'remove_background', model_id: 'other' };
    expect(withStepStage([cutout, grain], 'remove_background', replaced)).toEqual([
      replaced,
      grain,
    ]);
    expect(withStepStage([grain], 'remove_background', cutout)).toEqual([grain, cutout]);
    expect(withStepStage([cutout, grain, other], 'remove_background', null)).toEqual([
      grain,
      other,
    ]);
    expect(withStepStage([grain], 'remove_background', null)).toEqual([grain]);
    expect(withStepStage(undefined, 'remove_background', cutout)).toEqual([cutout]);
    expect(withStepStage(undefined, 'remove_background', null)).toEqual([]);
  });

  it('backgroundModel finds the real catalog model, or nothing', () => {
    const { models } = demoSnapshot().catalog;
    expect(backgroundModel(models)?.model_id).toBe('birefnet_general');
    expect(backgroundModel(models.filter((model) => !model.purposes.includes('background')))).toBe(
      undefined,
    );
  });

  it('backgroundRemoved reads the stored step', () => {
    expect(backgroundRemoved({ step_stages: [grain, cutout] })).toBe(true);
    expect(backgroundRemoved({ step_stages: [grain] })).toBe(false);
    expect(backgroundRemoved({})).toBe(false);
  });

  it('backgroundRowShown follows the task, the feature and the model', () => {
    const model = backgroundModel(demoSnapshot().catalog.models);
    const base = { features: ['image_background_removal'], model, removed: false };
    expect(backgroundRowShown({ ...base, task: 'upscale' })).toBe(true);
    expect(backgroundRowShown({ ...base, task: 'denoise' })).toBe(true);
    for (const task of ['video', 'edit', ''] as const) {
      expect(backgroundRowShown({ ...base, task })).toBe(false);
    }
    expect(backgroundRowShown({ ...base, task: 'upscale', features: [] })).toBe(false);
    expect(backgroundRowShown({ ...base, task: 'upscale', model: undefined })).toBe(false);
    expect(backgroundRowShown({ ...base, task: 'upscale', features: [], removed: true })).toBe(
      true,
    );
  });
});
