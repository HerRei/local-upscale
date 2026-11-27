import { describe, expect, it } from 'vitest';
import {
  PATCH_SIZE,
  movePatch,
  patchChips,
  patchRequestKey,
  pickPatchRegion,
  shownStep,
  snapRegion,
} from './previewPatch';
import type { PatchState, PatchStep } from './types';

function step(partial: Partial<PatchStep>): PatchStep {
  return {
    kind: 'restore',
    fix: '',
    model_id: 'm',
    state: 'waiting',
    url: '',
    width: 0,
    height: 0,
    seconds: 0,
    note: '',
    ...partial,
  };
}

function state(steps: PatchStep[]): PatchState {
  return {
    state: 'running',
    media_id: 'p1',
    region: null,
    estimate_seconds: 0,
    error: '',
    steps,
  };
}

function checker(width: number, height: number, cell: { x: number; y: number; size: number }) {
  const data = new Uint8ClampedArray(width * height * 4).fill(128);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const inside = x >= cell.x && x < cell.x + cell.size && y >= cell.y && y < cell.y + cell.size;
      if (inside && (x + y) % 2 === 0) {
        const i = 4 * (y * width + x);
        data[i] = data[i + 1] = data[i + 2] = 255;
      }
    }
  }
  return { width, height, data };
}

describe('previewPatch', () => {
  it('pickPatchRegion finds the textured cell off-centre', () => {
    const image = { width: 3200, height: 2400 };
    // The preview is 1/8 of the picture; the patch is 48 preview px wide.
    const preview = checker(400, 300, { x: 40, y: 200, size: 48 });
    const region = pickPatchRegion(preview, image);
    expect(region.width).toBe(PATCH_SIZE);
    expect(Math.abs(region.x - 320)).toBeLessThanOrEqual(96);
    expect(Math.abs(region.y - 1600)).toBeLessThanOrEqual(96);
  });

  it('pickPatchRegion takes the centre on a flat picture or without a preview', () => {
    const image = { width: 3200, height: 2400 };
    const flat = { width: 400, height: 300, data: new Uint8ClampedArray(400 * 300 * 4).fill(90) };
    const centre = { x: 1408, y: 1008, width: 384, height: 384 };
    expect(pickPatchRegion(flat, image)).toEqual(centre);
    expect(pickPatchRegion(null, image)).toEqual(centre);
  });

  it('snapRegion snaps to 16 and stays inside the picture', () => {
    const image = { width: 1600, height: 1067 };
    const region = snapRegion({ x: 1001, y: 2000 }, image);
    expect(region.x % 16).toBe(0);
    expect(region.y % 16).toBe(0);
    expect(region.x + region.width).toBeLessThanOrEqual(1600);
    expect(region.y + region.height).toBeLessThanOrEqual(1067);
    expect(snapRegion({ x: -50, y: -9 }, image)).toMatchObject({ x: 0, y: 0 });
  });

  it('snapRegion gives a 300 x 200 picture a 200 x 200 region', () => {
    const region = snapRegion({ x: 90, y: 0 }, { width: 300, height: 200 });
    expect(region).toMatchObject({ width: 200, height: 200, y: 0 });
    expect(region.x % 16).toBe(0);
    expect(region.x + 200).toBeLessThanOrEqual(300);
  });

  it('movePatch moves 32 px, 128 px with Shift, and clamps', () => {
    const image = { width: 1600, height: 1067 };
    const start = { x: 512, y: 256, width: 384, height: 384 };
    expect(movePatch(start, 'ArrowRight', false, image).x).toBe(544);
    expect(movePatch(start, 'ArrowRight', true, image).x).toBe(640);
    expect(movePatch(start, 'ArrowUp', true, image).y).toBe(128);
    expect(movePatch(start, 'ArrowLeft', true, image).x).toBe(384);
    const edge = { x: 1216, y: 0, width: 384, height: 384 };
    expect(movePatch(edge, 'ArrowRight', true, image).x).toBe(1216);
    expect(movePatch(edge, 'ArrowUp', true, image).y).toBe(0);
  });

  it('patchChips for a plan with a missing second model', () => {
    const chips = patchChips(
      state([
        step({ fix: 'jpeg', state: 'done', seconds: 0.8 }),
        step({ fix: 'noise', state: 'missing' }),
        step({ kind: 'upscale', state: 'waiting' }),
      ]),
      [{ name: 'JPEG' }, { name: 'Noise', modelName: 'RealPLKSR Denoise' }, { name: '4×' }],
    );
    expect(chips.map((chip) => chip.label)).toEqual(['Original', 'JPEG', '↓ Noise', '4×']);
    expect(chips.map((chip) => chip.state)).toEqual(['original', 'done', 'missing', 'waiting']);
    expect(chips[1].title).toBe('After JPEG · 0.8 s');
    expect(chips[2].title).toBe('RealPLKSR Denoise is not downloaded.');
  });

  it('patchChips disables a skipped step and uses its note', () => {
    const chips = patchChips(
      state([step({ fix: 'noise', state: 'skipped', note: 'Too small.' })]),
      [{ name: 'Noise' }],
    );
    expect(chips[1]).toMatchObject({ disabled: true, title: 'Too small.' });
  });

  it('shownStep follows the newest done step, a pick, and falls back when the pick leaves', () => {
    const steps = [
      step({ fix: 'jpeg', state: 'done' }),
      step({ fix: 'noise', state: 'done' }),
      step({ kind: 'upscale', state: 'running' }),
    ];
    expect(shownStep(steps, null)).toBe(1);
    expect(shownStep(steps, 'jpeg')).toBe(0);
    expect(shownStep(steps.slice(1), 'jpeg')).toBe(0);
    expect(shownStep([steps[0], steps[2]], 'noise')).toBe(0);
    expect(shownStep([step({ state: 'waiting' })], null)).toBe(-1);
  });

  it('patchRequestKey changes with the plan and the position, not with unrelated fields', () => {
    const region = { x: 0, y: 0, width: 384, height: 384 };
    const input = { fix_stages: [], output_scale: 4, device: 'cpu' } as never;
    const key = (r = region, i = input) =>
      patchRequestKey({ mediaId: 'p1', slot: 'a', region: r, input: i });
    expect(key()).toBe(key());
    expect(key({ ...region, x: 16 })).not.toBe(key());
    expect(key(region, { fix_stages: [], output_scale: 2, device: 'cpu' } as never)).not.toBe(
      key(),
    );
    expect(key(region, { ...(input as object), output_directory: '/x' } as never)).toBe(key());
  });
});
