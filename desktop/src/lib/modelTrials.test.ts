import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { keptTrialSettings, trialIncompatibility, trialTime } from './modelTrials';

describe('model trials', () => {
  it('gives a total only when every model was measured', () => {
    expect(
      trialTime([
        { model_id: 'a', model_label: 'A', seconds: 20 },
        { model_id: 'b', model_label: 'B', seconds: 40 },
      ]),
    ).toBe('≈ 1:00 in total');
    expect(
      trialTime([
        { model_id: 'a', model_label: 'A', seconds: 20 },
        { model_id: 'b', model_label: 'B', seconds: null },
      ]),
    ).toBe('≈ 20s measured · not yet measured for B');
    expect(trialTime([{ model_id: 'b', model_label: 'B', seconds: null }])).toBe(
      'Time not yet measured for B',
    );
  });

  it('refuses models that cannot keep the shown scale, fix or Faces', () => {
    const snapshot = demoSnapshot();
    const models = snapshot.catalog.models.map((model) => ({ ...model, installed: true }));
    const settings = { ...snapshot.settings, task: 'upscale' as const, output_scale: 4 };
    const two = models.find((m) => m.native_scale === 2 && !m.purposes.includes('face'));
    const four = models.find((m) => m.native_scale === 4 && !m.purposes.includes('face'));
    expect(two && trialIncompatibility(two, settings, models)).toBe('Cannot keep 4×');
    expect(four && trialIncompatibility(four, settings, models)).toBe('');
    expect(four && trialIncompatibility({ ...four, installed: false }, settings, models)).toBe(
      'Not installed',
    );
    const faces = { ...settings, enable_face_model: true };
    expect(four && trialIncompatibility({ ...four, pair_with: '' }, faces, models)).toBe(
      'Cannot keep Faces',
    );
    const restore = {
      ...settings,
      task: 'denoise' as const,
      fixes: ['blur' as const],
      fix_stages: [{ fix: 'blur' as const, model_id: 'x' }],
    };
    const denoiser = models.find(
      (m) =>
        m.native_scale === 1 && m.purposes.includes('denoise') && !m.purposes.includes('deblur'),
    );
    if (denoiser && !denoiser.fixes?.includes('blur'))
      expect(trialIncompatibility(denoiser, restore, models)).toBe('Does not fix Blur');
    // With several fixes the last one is Restore's own: a candidate must fix that one.
    const several = {
      ...restore,
      fixes: ['jpeg' as const, 'blur' as const],
      fix_stages: [
        { fix: 'jpeg' as const, model_id: 'a' },
        { fix: 'blur' as const, model_id: 'b' },
      ],
    };
    if (denoiser && !denoiser.fixes?.includes('blur'))
      expect(trialIncompatibility(denoiser, several, models)).toBe('Does not fix Blur');
    expect(keptTrialSettings({ ...settings, fixes: ['jpeg', 'faces'] }, true)).toBe(
      '4×, JPEG artifacts fix, Faces, ' + settings.output_format.toUpperCase(),
    );
  });
});
