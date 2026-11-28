import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import {
  fixRowOrder,
  fixRows,
  smoothingOf,
  smoothingStop,
  fixRowValue,
  splitFixRows,
  strengthLabel,
  strengthOf,
  takesStrength,
  withStrength,
} from './fixRows';
import type { PlanStage } from './plan';
import { STEPS, type StepDefinition } from './steps';
import type { FixStageSelection, ModelDownloadStatus } from './types';

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
    model_task: null,
    output_alpha: false,
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
    expect(fixRowOrder()).toEqual(['stabilise', 'blur', 'jpeg', 'noise', 'colour', 'deflicker']);
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
    const noiseRow = fixRows('image', ['multi_fix', 'stage_params_and_masks', 'fix_strength']).find(
      (row) => row.id === 'noise',
    )!;
    const model = snap.catalog.models.find((m) => m.model_id === 'denoise_realplksr_1x')!;
    const settings = {
      ...snap.settings,
      fix_stages: [{ fix: 'noise' as const, model_id: 'denoise_realplksr_1x' }],
    };
    const emptyDownloads = new Map<string, ModelDownloadStatus>();

    expect(fixRowValue(noiseRow, [], settings, [model], emptyDownloads)).toBe('RealPLKSR Denoise');

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
    expect(fixRowValue(noiseRow, [missingStage], settings, [model], emptyDownloads)).toBe(
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
    expect(fixRowValue(noiseRow, [missingStage], settings, [model], downloading)).toBe(
      'RealPLKSR Denoise',
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
    expect(fixRowValue(noiseRow, [missingStage], settings, [model], queued)).toBe(
      'RealPLKSR Denoise',
    );

    expect(
      fixRowValue(
        noiseRow,
        [{ ...missingStage, canDownload: false, needsFile: true }],
        settings,
        [model],
        emptyDownloads,
      ),
    ).toBe('Needs your file');

    expect(
      fixRowValue(
        noiseRow,
        [{ ...missingStage, model: undefined, missing: true }],
        settings,
        [model],
        emptyDownloads,
      ),
    ).toBe('No model for this fix');
  });

  describe('strength', () => {
    it('strengthOf reads a number from 0 to 1 and falls back to 1', () => {
      expect(strengthOf(undefined)).toBe(1);
      expect(strengthOf({})).toBe(1);
      expect(strengthOf({ params: { strength: 'low' } })).toBe(1);
      expect(strengthOf({ params: { strength: -1 } })).toBe(1);
      expect(strengthOf({ params: { strength: 2 } })).toBe(1);
      expect(strengthOf({ params: { strength: 0.4 } })).toBe(0.4);
    });

    it('withStrength sets the key and drops it, and an empty params, at 1', () => {
      const entry: FixStageSelection = { fix: 'noise', model_id: 'm' };
      expect(withStrength(entry, 0.4)).toEqual({
        fix: 'noise',
        model_id: 'm',
        params: { strength: 0.4 },
      });
      expect(withStrength({ params: { strength: 0.4 } }, 1)).toEqual({});
      expect(withStrength({ params: { strength: 0.4, other: 'x' } }, 1)).toEqual({
        params: { other: 'x' },
      });
      expect(withStrength({ params: { other: 'x' } }, 0.7)).toEqual({
        params: { other: 'x', strength: 0.7 },
      });
    });

    it('strengthLabel names the stops and prints other amounts', () => {
      expect(strengthLabel(0.4)).toBe('Light');
      expect(strengthLabel(0.7)).toBe('Medium');
      expect(strengthLabel(1)).toBe('Full');
      expect(strengthLabel(0.55)).toBe('55%');
    });

    it('the registry gives Blur, JPEG and Noise the choice, not Colour or De-flicker', () => {
      const rows = fixRows('video', ['multi_fix', 'stage_params_and_masks', 'fix_strength']);
      const withChoice = rows.filter((row) => row.choice).map((row) => row.id);
      expect(withChoice.sort()).toEqual(['blur', 'jpeg', 'noise']);
      expect(takesStrength('colour')).toBe(false);
      expect(takesStrength('deflicker')).toBe(false);
      expect(takesStrength('noise')).toBe(true);
    });

    it('fixRowValue shows the strength in place of the model name', () => {
      const snap = demoSnapshot();
      const model = snap.catalog.models.find((m) => m.model_id === 'denoise_realplksr_1x')!;
      const row = fixRows('image', ['multi_fix', 'stage_params_and_masks', 'fix_strength']).find(
        (entry) => entry.id === 'noise',
      )!;
      const settings = {
        ...snap.settings,
        fix_stages: [
          {
            fix: 'noise' as const,
            model_id: 'denoise_realplksr_1x',
            params: { strength: 0.4 },
          },
        ],
      };
      const none = new Map<string, ModelDownloadStatus>();
      const stage: PlanStage = {
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
      expect(fixRowValue(row, [], settings, [model], none, true)).toBe('Light');
      expect(fixRowValue(row, [stage], settings, [model], none, true)).toBe('Light · 29 MB');
      expect(fixRowValue(row, [stage], settings, [model], none, false)).toBe(
        'RealPLKSR Denoise · 29 MB',
      );
      expect(
        fixRowValue(
          row,
          [{ ...stage, canDownload: false, needsFile: true }],
          settings,
          [model],
          none,
          true,
        ),
      ).toBe('Needs your file');
      const colour = fixRows('image', ['multi_fix', 'stage_params_and_masks', 'fix_strength']).find(
        (entry) => entry.id === 'colour',
      )!;
      expect(
        fixRowValue(colour, [{ ...stage, fix: 'colour' }], settings, [model], none, true),
      ).toBe('RealPLKSR Denoise · 29 MB');
    });
  });
});

describe('fixRows, stabilise', () => {
  const features = ['video_stabilise'];

  it('is the first video row, a common Labs step_stage row with the Smoothing choice', () => {
    const rows = fixRows('video', features);
    expect(rows.map((row) => row.id)).toEqual([
      'stabilise',
      'blur',
      'jpeg',
      'noise',
      'colour',
      'deflicker',
    ]);
    const row = rows[0];
    expect(row).toMatchObject({
      label: 'Stabilise',
      source: 'step_stage',
      tag: 'Labs',
      common: true,
    });
    expect(row.choice).toMatchObject({
      param: 'smoothing',
      name: 'Smoothing',
      fallback: 'medium',
    });
    expect(row.choice?.options.map((option) => option.label)).toEqual([
      'Light',
      'Medium',
      'Strong',
    ]);
    expect(row.choice?.options.map((option) => option.value)).toEqual([
      'light',
      'medium',
      'strong',
    ]);
    expect(row.choice?.options[1].title).toBe(
      "Evens out the camera's path over one second · cuts 5 % off each edge",
    );
    expect(splitFixRows(rows, () => false, false).hiddenCount).toBe(0);
  });

  it('has no row on a photo or without the feature, unless the step is stored', () => {
    expect(fixRows('image', features).some((row) => row.id === 'stabilise')).toBe(false);
    expect(fixRows('video', []).some((row) => row.id === 'stabilise')).toBe(false);
    expect(fixRows('video', [], STEPS, undefined, ['stabilise'])[0].id).toBe('stabilise');
    expect(fixRows('image', [], STEPS, undefined, ['stabilise']).length).toBe(4);
  });

  it('reads the smoothing, Medium for anything unknown, with the engine crop', () => {
    expect(smoothingOf(undefined)).toBe('medium');
    expect(smoothingOf({ params: { smoothing: 'strong' } })).toBe('strong');
    expect(smoothingOf({ params: { smoothing: 'huge' } })).toBe('medium');
    expect([
      smoothingStop('light').crop,
      smoothingStop('medium').crop,
      smoothingStop('strong').crop,
    ]).toEqual([0.03, 0.05, 0.08]);
  });
});
