import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import {
  backgroundModel,
  backgroundRemoved,
  backgroundRowShown,
  scanApplies,
  scanChoice,
  scanOptions,
  scanRate,
  scanRowShown,
  scanStage,
  withStepStage,
  type ScanChoice,
} from './stepRows';
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

describe('stepRows, scan', () => {
  const six: ScanChoice[] = [
    'auto',
    'field:auto',
    'frame:tff',
    'field:tff',
    'frame:bff',
    'field:bff',
  ];

  it('scanChoice and scanStage round-trip every choice', () => {
    for (const choice of six) {
      const stage = scanStage(choice);
      expect(scanChoice({ step_stages: stage ? [grain, stage] : [grain] })).toBe(choice);
    }
    expect(scanStage('auto')).toBeNull();
    expect(scanStage('field:bff')).toEqual({
      step: 'deinterlace',
      params: { mode: 'field', parity: 'bff' },
    });
    expect(scanChoice({})).toBe('auto');
    expect(scanChoice({ step_stages: [{ step: 'deinterlace', params: { mode: 'field' } }] })).toBe(
      'field:auto',
    );
    expect(scanChoice({ step_stages: [{ step: 'deinterlace', params: { mode: 'frame' } }] })).toBe(
      'frame:auto',
    );
  });

  it('scanApplies is false only for auto parity on a progressive file', () => {
    expect(scanApplies('field:auto', 'progressive')).toBe(false);
    expect(scanApplies('frame:auto', 'progressive')).toBe(false);
    expect(scanApplies('field:tff', 'progressive')).toBe(true);
    expect(scanApplies('field:auto', 'bff')).toBe(true);
    expect(scanApplies('field:auto', undefined)).toBe(true);
    expect(scanApplies('auto', 'bff')).toBe(false);
  });

  it('scanRate doubles a field choice that applies', () => {
    expect(scanRate('field:auto', 'bff', 25)).toBe(50);
    expect(scanRate('field:auto', 'progressive', 25)).toBe(25);
    expect(scanRate('frame:tff', 'tff', 25)).toBe(25);
    expect(scanRate('field:bff', 'tff', 29.97)).toBe(59.94);
    expect(scanRate('auto', 'bff', 25)).toBe(25);
  });

  it('scanRowShown follows the file, the engine and a stored choice', () => {
    const base = {
      task: 'video' as const,
      usingTemporalVideo: false,
      features: ['video_deinterlace'],
      fieldOrder: 'bff' as string | undefined,
      choice: 'auto' as ScanChoice,
    };
    for (const fieldOrder of ['tff', 'bff', 'unknown']) {
      expect(scanRowShown({ ...base, fieldOrder })).toBe(true);
    }
    for (const fieldOrder of ['progressive', '', undefined]) {
      expect(scanRowShown({ ...base, fieldOrder })).toBe(false);
    }
    expect(scanRowShown({ ...base, usingTemporalVideo: true })).toBe(false);
    expect(scanRowShown({ ...base, task: 'upscale' })).toBe(false);
    expect(scanRowShown({ ...base, features: [] })).toBe(false);
    expect(scanRowShown({ ...base, fieldOrder: 'progressive', choice: 'field:tff' })).toBe(true);
    expect(scanRowShown({ ...base, features: [], choice: 'field:auto' })).toBe(true);
  });

  it('scanOptions writes the rates the Copy table names', () => {
    expect(scanOptions('bff', 25, 'auto').map((option) => option.label)).toEqual([
      'Automatic · interlaced',
      'Double rate · 50 fps',
      'Top field first · 25 fps',
      'Top field first · 50 fps',
      'Bottom field first · 25 fps',
      'Bottom field first · 50 fps',
    ]);
    expect(scanOptions('bff', 25, 'auto').map((option) => option.group)).toEqual([
      '',
      '',
      'hand',
      'hand',
      'hand',
      'hand',
    ]);
    expect(scanOptions('unknown', 25, 'auto')[0].label).toBe('Automatic');
    expect(scanOptions('progressive', 25, 'field:auto')[1].label).toBe('Double rate');
    expect(scanOptions('bff', 29.97, 'auto')[1].label).toBe('Double rate · 59.94 fps');
    expect(scanOptions('bff', 0, 'auto').map((option) => option.label)).toEqual([
      'Automatic · interlaced',
      'Double rate',
      'Top field first',
      'Top field first · double rate',
      'Bottom field first',
      'Bottom field first · double rate',
    ]);
    const stored = scanOptions('bff', 25, 'frame:auto');
    expect(stored).toHaveLength(7);
    expect(stored[6].label).toBe('Deinterlace · 25 fps');
  });
});
