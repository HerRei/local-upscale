import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import {
  ORIGINAL,
  alignedFraming,
  chooseTarget,
  choosersVisible,
  configuredStages,
  describeSide,
  effectiveChoice,
  keptTrialSettings,
  queueModelState,
  resultTitle,
  scaleLabel,
  settingsLabel,
  sideLabel,
  slotFor,
  stepShowing,
  trialIncompatibility,
  trialTime,
  whenLabel,
} from './results';
import type { JobRecord, MediaResults, ResultSummary } from './types';

function result(id: string, overrides: Partial<ResultSummary> = {}): ResultSummary {
  return {
    job_id: id,
    completed_at: 100,
    status: 'completed',
    kind: 'upscale',
    output_path: `/${id}.png`,
    output_name: `${id}.png`,
    available: true,
    model_id: id,
    model_label: id,
    scale: 4,
    stages: [],
    notes: '',
    elapsed_seconds: 14,
    ui: null,
    ...overrides,
  };
}

function history(results: ResultSummary[]): MediaResults {
  const available = results.filter((r) => r.status === 'completed' && r.available);
  return {
    media_id: 'photo',
    results,
    total_count: results.length,
    completed_count: results.filter((r) => r.status === 'completed').length,
    available_count: available.length,
    default_result_id: available[0]?.job_id ?? null,
    next_before: null,
  };
}

const source = { width: 400, height: 300, preview_data_url: 'data:source' };

describe('the chosen pair', () => {
  const rows = [
    result('new'),
    result('missing', { available: false }),
    result('failed', { status: 'failed', available: false }),
    result('old'),
  ];

  it('follows the newest saved result against the Original until a choice pins it', () => {
    expect(effectiveChoice(history(rows), undefined)).toEqual({
      showing: 'new',
      against: ORIGINAL,
      pinned: false,
    });
    const pinned = { showing: 'old', against: 'new', pinned: true };
    expect(effectiveChoice(history([result('later'), ...rows]), pinned)).toEqual(pinned);
    // A pinned result whose file disappeared falls back to the default.
    expect(effectiveChoice(history(rows), { ...pinned, against: 'missing' }).pinned).toBe(false);
  });

  it('shows the choosers only for two saved results with files', () => {
    expect(choosersVisible(history([result('a'), result('b', { available: false })]))).toBe(false);
    expect(choosersVisible(history([result('a'), result('b')]))).toBe(true);
    expect(choosersVisible(undefined)).toBe(false);
  });

  it('swaps the sides when one side is given what the other holds', () => {
    const choice = { showing: 'new', against: 'old', pinned: false };
    expect(chooseTarget(choice, 'against', 'new')).toEqual({
      showing: 'old',
      against: 'new',
      pinned: true,
    });
    expect(chooseTarget(choice, 'showing', ORIGINAL)).toEqual({
      showing: ORIGINAL,
      against: 'old',
      pinned: true,
    });
  });

  it('steps Showing older and newer, skipping missing files and the reference, and stops at the ends', () => {
    const many = [result('a'), result('gone', { available: false }), result('b'), result('c')];
    const choice = { showing: 'a', against: 'b', pinned: true };
    expect(stepShowing(many, choice, 'older')).toBe('c');
    expect(stepShowing(many, { ...choice, showing: 'c' }, 'older')).toBeNull();
    expect(stepShowing(many, { ...choice, showing: 'c' }, 'newer')).toBe('a');
    expect(stepShowing(many, choice, 'newer')).toBeNull();
    expect(
      stepShowing(many, { showing: ORIGINAL, against: ORIGINAL, pinned: false }, 'older'),
    ).toBe('a');
  });

  it('never uses a slot that belongs to another image or another target', () => {
    const pair = {
      media_id: 'photo',
      generation: 2,
      showing: { target_id: 'new', preview_data_url: 'new' },
      against: { target_id: 'old', preview_data_url: 'old' },
    };
    expect(slotFor(pair, 'photo', 'showing', 'new')?.preview_data_url).toBe('new');
    expect(slotFor(pair, 'photo', 'showing', 'old')).toBeUndefined();
    expect(slotFor(pair, 'other', 'against', 'old')).toBeUndefined();
  });
});

describe('sides and labels', () => {
  it('labels the output scale from the decoded file, never a preview as enlarged', () => {
    const swinir = result('s', { model_label: 'SwinIR-M', scale: 2 });
    expect(sideLabel(swinir, { width: 800, height: 600 }, source)).toBe('SwinIR-M · 2× output');
    expect(sideLabel(swinir)).toBe('SwinIR-M · 2× output');
    expect(sideLabel(swinir, { width: 1600, height: 1200 }, source)).toBe('SwinIR-M · 4× output');
    expect(scaleLabel(result('r', { scale: 1 }), { width: 400, height: 300 }, source)).toBe(
      '1× output',
    );
    expect(scaleLabel(result('e', { kind: 'edit', scale: 0 }))).toBe('Edit');
    expect(sideLabel(undefined)).toBe('Original');
  });

  it('draws the Original from the probe thumbnail and a result only from its own slot', () => {
    expect(describeSide(ORIGINAL, undefined, undefined, source)).toMatchObject({
      src: 'data:source',
      state: 'ready',
      width: 400,
      height: 300,
    });
    const row = result('a', { model_label: 'HAT' });
    expect(describeSide('a', row, undefined, source)).toMatchObject({ src: '', state: 'loading' });
    expect(
      describeSide('a', row, { target_id: 'a', preview_data_url: '', error: 'bad file' }, source),
    ).toMatchObject({ src: '', state: 'error', error: 'bad file' });
    expect(
      describeSide(
        'a',
        row,
        { target_id: 'a', preview_data_url: 'data:a', width: 1600, height: 1200 },
        source,
      ),
    ).toMatchObject({ src: 'data:a', state: 'ready', label: 'HAT · 4× output' });
    // The completion preview of the same job bridges the decode.
    expect(describeSide('a', row, undefined, source, 'data:auto')).toMatchObject({
      src: 'data:auto',
      state: 'ready',
    });
  });

  it('tells matching framing from a different one, in rounding of the saved size', () => {
    expect(alignedFraming({ width: 1601, height: 1201 }, source)).toBe(true);
    expect(alignedFraming({ width: 801, height: 600 }, { width: 401, height: 300 })).toBe(true);
    expect(alignedFraming({ width: 1024, height: 1024 }, source)).toBe(false);
    expect(alignedFraming({}, source)).toBe(true);
  });

  it('reads recorded settings, and says when none were recorded', () => {
    expect(settingsLabel(result('a'))).toBe('Settings unavailable');
    expect(
      settingsLabel(result('a', { ui: { quality: 'best', content: 'photo', recipe_name: '' } })),
    ).toBe('Best · Photo');
    expect(
      settingsLabel(
        result('a', { ui: { quality: 'custom', content: 'illustration', recipe_name: 'Scans' } }),
      ),
    ).toBe('Scans · Illustration');
    expect(resultTitle(result('a', { kind: 'edit', model_label: 'Qwen' }))).toMatch(/^Edit · Qwen/);
  });

  it('lists the configured stages besides the primary model', () => {
    const row = result('up', {
      model_id: 'swinir',
      stages: [
        { kind: 'restore', model_id: 'nafnet', model_label: 'NAFNet' },
        { kind: 'upscale', model_id: 'swinir', model_label: 'SwinIR' },
        { kind: 'face_restore', model_id: 'gfpgan', model_label: 'GFPGAN' },
      ],
    });
    expect(configuredStages(row)).toEqual(['Fix · NAFNet', 'Faces · GFPGAN']);
  });

  it('dates rows like the rest of the app', () => {
    const now = new Date(2026, 10, 9, 15, 0).getTime();
    expect(whenLabel(new Date(2026, 10, 9, 14, 2).getTime(), now)).toBe('today 14:02');
    expect(whenLabel(new Date(2026, 10, 9 - 1, 9, 5).getTime(), now)).toBe('Sun 09:05');
    expect(whenLabel(new Date(2026, 10, 2, 9, 5).getTime(), now)).toBe('2 Nov');
  });
});

describe('the queue and Try others', () => {
  function job(id: string, status: string, model: string): JobRecord {
    return {
      id,
      media_id: 'photo',
      media_name: 'photo.png',
      media_kind: 'image',
      status,
      progress: 0,
      output_path: '',
      error: '',
      created_at: 0,
      model_label: model,
    } as JobRecord;
  }

  it('names the running model first, then the image runs still queued', () => {
    const jobs = [
      job('c', 'queued', 'DAT2'),
      job('b', 'queued', 'HAT'),
      job('a', 'running', 'SwinIR'),
      job('done', 'completed', 'Old'),
    ];
    expect(queueModelState(jobs, 'photo', 'a')).toBe('Running SwinIR · 2 queued');
    expect(queueModelState(jobs.slice(0, 2), 'photo', '')).toBe('2 queued');
    expect(queueModelState([job('x', 'queued', 'HAT')], 'photo', '')).toBe('queued');
    expect(queueModelState([job('a', 'cancelling', 'SwinIR')], 'photo', 'a')).toBe(
      'Cancelling SwinIR',
    );
  });

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
    const restore = { ...settings, task: 'denoise' as const, fixes: ['blur' as const] };
    const denoiser = models.find(
      (m) =>
        m.native_scale === 1 && m.purposes.includes('denoise') && !m.purposes.includes('deblur'),
    );
    if (denoiser && !denoiser.fixes?.includes('blur'))
      expect(trialIncompatibility(denoiser, restore, models)).toBe('Does not fix Blur');
    expect(keptTrialSettings({ ...settings, fixes: ['jpeg', 'faces'] }, true)).toBe(
      '4×, JPEG artifacts fix, Faces, ' + settings.output_format.toUpperCase(),
    );
  });
});
