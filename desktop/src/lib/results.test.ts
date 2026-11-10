import { describe, expect, it } from 'vitest';
import {
  ORIGINAL,
  alignedFraming,
  chooseTarget,
  describeSide,
  effectiveChoice,
  scaleLabel,
  sideLabel,
  slotFor,
  stepShowing,
} from './results';
import type { MediaResults, ResultSummary } from './types';

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
});
