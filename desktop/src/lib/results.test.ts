import { describe, expect, it } from 'vitest';
import {
  ORIGINAL,
  alignedFraming,
  chooseTarget,
  describeSide,
  effectiveChoice,
  framingRounding,
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
    prompt: '',
    max_dimension: 0,
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
    // An edit is not an enlargement: its label gives the pixels the file really has.
    const edited = result('e', { kind: 'edit', scale: 0, model_label: 'Qwen Edit' });
    expect(scaleLabel(edited, { width: 512, height: 320 }, source)).toBe('512 × 320');
    expect(sideLabel(edited, { width: 512, height: 320 }, source)).toBe('Qwen Edit · 512 × 320');
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

  describe('an Edit result, which the engine sizes down in steps of 32', () => {
    // edit_dimensions in image_edit.py: the long edge to the chosen size, each side
    // rounded down to a multiple of 32, so a 3:2 photo comes back as 512 x 320.
    const editSize = (width: number, height: number, limit: number) => {
      const ratio = Math.min(1, limit / Math.max(width, height));
      return {
        width: Math.max(32, Math.floor((width * ratio) / 32) * 32),
        height: Math.max(32, Math.floor((height * ratio) / 32) * 32),
      };
    };
    const camera = { width: 4896, height: 3264 };
    const edit = framingRounding(result('e', { kind: 'edit', scale: 0 }));

    it('is rounded in steps of 32 for an edit and by a pixel for everything else', () => {
      expect(edit).toBe(32);
      expect(framingRounding(result('u'))).toBe(1);
      expect(framingRounding(result('r', { kind: 'restore' }))).toBe(1);
      expect(framingRounding(undefined)).toBe(1);
    });

    it.each([
      [512, { width: 512, height: 320 }],
      [768, { width: 768, height: 512 }],
      [1024, { width: 1024, height: 672 }],
    ])('shares a frame with the original at %i px', (limit, expected) => {
      const size = editSize(camera.width, camera.height, limit);
      expect(size).toEqual(expected);
      expect(alignedFraming(size, camera, edit)).toBe(true);
      // Only 768 x 512 is exactly 3:2; the others were refused by the one-pixel rule.
      expect(alignedFraming(size, camera)).toBe(limit === 768);
    });

    it('shares a frame in portrait, for a square photo and for an unchanged size', () => {
      expect(alignedFraming(editSize(3264, 4896, 512), { width: 3264, height: 4896 }, edit)).toBe(
        true,
      );
      expect(alignedFraming(editSize(3000, 3000, 768), { width: 3000, height: 3000 }, edit)).toBe(
        true,
      );
      expect(alignedFraming({ width: 576, height: 384 }, { width: 600, height: 400 }, edit)).toBe(
        true,
      );
    });

    it('stays alone when the shape is truly different, a crop or another format', () => {
      expect(alignedFraming({ width: 512, height: 512 }, camera, edit)).toBe(false);
      expect(alignedFraming({ width: 512, height: 384 }, camera, edit)).toBe(false);
      expect(alignedFraming({ width: 320, height: 512 }, camera, edit)).toBe(false);
      expect(alignedFraming({ width: 1024, height: 576 }, camera, edit)).toBe(false);
    });

    it('never lets rounding alone exceed the tolerance, whatever the photo', () => {
      for (let width = 700; width <= 6000; width += 137) {
        for (let height = 700; height <= 6000; height += 211) {
          for (const limit of [512, 768, 1024]) {
            expect(alignedFraming(editSize(width, height, limit), { width, height }, edit)).toBe(
              true,
            );
          }
        }
      }
    });
  });
});
