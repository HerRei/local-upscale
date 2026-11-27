import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { fixRowOrder, fixRows, fixRowValue, splitFixRows } from './fixRows';
import type { PlanStage } from './plan';
import { STEPS, type StepDefinition } from './steps';
import type { ModelDownloadStatus } from './types';

function extraStep(
  id: string,
  label: string,
  overrides: Partial<StepDefinition> = {},
): StepDefinition {
  return {
    id,
    media: ['image', 'video'],
    phase: 'cleanup',
    order: 50,
    needs_model: true,
    supports_mask: false,
    stage_kind: 'restore',
    role: 'fix',
    fix_kind: id,
    is_fix_stage: true,
    fix_chip_order: null,
    in_image_pipeline: true,
    in_recipe_stages: true,
    label,
    row_label: label,
    short_name: id,
    stage_label: '',
    required_feature: null,
    dev_only: false,
    parameters: [],
    ...overrides,
  };
}

describe('fixRows', () => {
  it('returns photo rows in engine order with expected labels and no hidden link', () => {
    const rows = fixRows('image', []);
    expect(rows.map((row) => row.id)).toEqual(['blur', 'jpeg', 'noise', 'colour']);
    expect(rows.map((row) => row.label)).toEqual(['Blur', 'JPEG artifacts', 'Noise', 'Colour']);
    expect(rows.every((row) => row.tag === '')).toBe(true);
    expect(fixRowOrder()).toEqual(['blur', 'jpeg', 'noise', 'colour', 'deflicker']);
    expect(
      rows.some((row) => ['face_restore', 'interpolate', 'audio_denoise'].includes(row.id)),
    ).toBe(false);
  });

  it('returns video rows with Compression and De-flicker (Labs) last', () => {
    const rows = fixRows('video', []);
    expect(rows.map((row) => row.id)).toEqual(['blur', 'jpeg', 'noise', 'colour', 'deflicker']);
    expect(rows.map((row) => row.label)).toEqual([
      'Blur',
      'Compression',
      'Noise',
      'Colour',
      'De-flicker',
    ]);
    expect(rows.find((row) => row.id === 'deflicker')?.tag).toBe('Labs');
    expect(rows.find((row) => row.id === 'deflicker')?.source).toBe('setting');
    expect(
      rows.some((row) => ['face_restore', 'interpolate', 'audio_denoise'].includes(row.id)),
    ).toBe(false);
  });

  it('gates rows by required_feature', () => {
    const custom = [...STEPS, extraStep('exposure', 'Exposure', { required_feature: 'x' })];
    expect(fixRows('image', [], custom).some((row) => row.id === 'exposure')).toBe(false);
    expect(fixRows('image', ['x'], custom).some((row) => row.id === 'exposure')).toBe(true);
  });

  it('hides non-common unticked rows behind More fixes only when 2+ would be hidden', () => {
    const commonOnly = STEPS.filter((step) => step.id !== 'colour');
    const oneAdded = fixRows('image', [], [...commonOnly, extraStep('dust', 'Dust')]);
    expect(splitFixRows(oneAdded, () => false, false)).toEqual({
      shown: oneAdded,
      hiddenCount: 0,
    });

    const twoAdded = fixRows(
      'image',
      [],
      [
        ...commonOnly,
        extraStep('dust', 'Dust', { order: 10 }),
        extraStep('scratches', 'Scratches', { order: 20 }),
      ],
    );
    const collapsed = splitFixRows(twoAdded, () => false, false);
    expect(collapsed.hiddenCount).toBe(2);
    expect(collapsed.shown.map((row) => row.id)).toEqual(['blur', 'jpeg', 'noise']);

    const oneTicked = splitFixRows(twoAdded, (row) => row.id === 'dust', false);
    expect(oneTicked.hiddenCount).toBe(0);
    expect(oneTicked.shown.map((row) => row.id)).toEqual([
      'dust',
      'scratches',
      'blur',
      'jpeg',
      'noise',
    ]);

    const expanded = splitFixRows(twoAdded, () => false, true);
    expect(expanded.hiddenCount).toBe(2);
    expect(expanded.shown.map((row) => row.id)).toEqual([
      'dust',
      'scratches',
      'blur',
      'jpeg',
      'noise',
    ]);
  });

  it('formats ticked-row values from the plan or settings fallback', () => {
    const snap = demoSnapshot();
    const model = snap.catalog.models.find((m) => m.model_id === 'denoise_realplksr_1x')!;
    const settings = {
      ...snap.settings,
      fix_stages: [{ fix: 'noise' as const, model_id: 'denoise_realplksr_1x' }],
    };
    const emptyDownloads = new Map<string, ModelDownloadStatus>();

    expect(fixRowValue('noise', [], settings, [model], emptyDownloads)).toBe('RealPLKSR Denoise');

    const missingStage: PlanStage = {
      kind: 'restore',
      role: 'fix',
      fix: 'noise',
      label: 'Fix noise',
      model,
      installed: false,
      canDownload: true,
      needsFile: false,
      sizeBytes: 28 * 1024 * 1024,
    };
    expect(fixRowValue('noise', [missingStage], settings, [model], emptyDownloads)).toBe(
      'RealPLKSR Denoise · 29 MB',
    );

    const downloading = new Map<string, ModelDownloadStatus>([
      [
        'denoise_realplksr_1x',
        {
          model_id: 'denoise_realplksr_1x',
          status: 'downloading',
          progress: 42.4,
          downloaded_bytes: 12,
          total_bytes: 28,
        },
      ],
    ]);
    expect(fixRowValue('noise', [missingStage], settings, [model], downloading)).toBe(
      'RealPLKSR Denoise · 42%',
    );

    const queued = new Map<string, ModelDownloadStatus>([
      [
        'denoise_realplksr_1x',
        {
          model_id: 'denoise_realplksr_1x',
          status: 'queued',
          progress: 0,
          downloaded_bytes: 0,
          total_bytes: 28,
        },
      ],
    ]);
    expect(fixRowValue('noise', [missingStage], settings, [model], queued)).toBe(
      'RealPLKSR Denoise · Waiting',
    );

    expect(
      fixRowValue(
        'noise',
        [{ ...missingStage, canDownload: false, needsFile: true }],
        settings,
        [model],
        emptyDownloads,
      ),
    ).toBe('Needs your file');

    expect(
      fixRowValue(
        'noise',
        [{ ...missingStage, model: undefined, missing: true }],
        settings,
        [model],
        emptyDownloads,
      ),
    ).toBe('No model for this fix');
  });
});
