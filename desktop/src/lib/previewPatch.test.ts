import { describe, expect, it } from 'vitest';
import {
  PATCH_SIZE,
  movePatch,
  patchChips,
  patchPlanEntries,
  patchRequestKey,
  pickPatchRegion,
  shownStep,
  snapRegion,
} from './previewPatch';
import { demoSnapshot } from './demo';
import { resolvePlan } from './plan';
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

  it('patchRequestKey changes when the photo is painted again', () => {
    const request = {
      mediaId: 'p1',
      slot: 'a' as const,
      region: { x: 0, y: 0, width: 384, height: 384 },
      input: { step_stages: [{ step: 'repair' }], output_scale: 1 } as never,
    };
    expect(patchRequestKey({ ...request, repairRev: 0 })).toBe(patchRequestKey(request));
    expect(patchRequestKey({ ...request, repairRev: 2 })).not.toBe(
      patchRequestKey({ ...request, repairRev: 1 }),
    );
  });
});

describe('Patch, background', () => {
  it('keeps the background step as an entry named Background', () => {
    const stage = (kind: string, role: string, label: string) =>
      ({ kind, role, label, installed: true }) as never;
    const entries = patchPlanEntries(
      [
        stage('remove_background', 'step', 'Remove background'),
        stage('upscale', 'primary', 'Upscale ×4'),
      ],
      { task: 'upscale', output_scale: 4, video_target_resolution: 0, video_interpolation: 'off' },
    );
    expect(entries.map((entry) => entry.name)).toEqual(['Background', '4×']);
  });

  it('leaves a deinterlace stage out: one decoded frame has no field order to show', () => {
    const stage = (kind: string, role: string, label: string) =>
      ({ kind, role, label, installed: true }) as never;
    const entries = patchPlanEntries(
      [stage('deinterlace', 'step', 'Deinterlace'), stage('upscale', 'primary', 'Upscale ×4')],
      { task: 'upscale', output_scale: 4, video_target_resolution: 0, video_interpolation: 'off' },
    );
    expect(entries.map((entry) => entry.name)).toEqual(['4×']);
  });

  it('leaves a stabilise stage out: one frame cannot show movement', () => {
    const stage = (kind: string, role: string, label: string) =>
      ({ kind, role, label, installed: true }) as never;
    const entries = patchPlanEntries(
      [stage('stabilise', 'step', 'Stabilise'), stage('upscale', 'primary', 'Upscale ×4')],
      { task: 'upscale', output_scale: 4, video_target_resolution: 0, video_interpolation: 'off' },
    );
    expect(entries.map((entry) => entry.name)).toEqual(['4×']);
  });
});

describe('Patch, Dust & scratches model', () => {
  it('names MI-GAN on the repair entry, and a missing one gives the download chip', () => {
    const snapshot = demoSnapshot();
    const settings = {
      ...snapshot.settings,
      task: 'upscale' as const,
      selected_model_id: 'span_photo_x4',
      output_scale: 4,
      fix_stages: [],
      step_stages: [{ step: 'repair' }],
    };
    const plan = resolvePlan(snapshot, settings, {
      faceEnabled: false,
      usingTemporalVideo: false,
      maskedSteps: ['repair'],
      features: ['image_repair', 'image_repair_model'],
    });
    const entries = patchPlanEntries(plan, settings);
    expect(entries.map((entry) => entry.name)).toEqual(['Dust & scratches', '4×']);
    expect(entries[0].modelName).toBe('MI-GAN');
    const chips = patchChips(
      state([step({ kind: 'repair', model_id: 'migan', state: 'missing' }), step({})]),
      entries,
    );
    expect(chips[1].label).toBe('↓ Dust & scratches');
    expect(chips[1].title).toBe('MI-GAN is not downloaded.');
    // The other steps still show.
    expect(chips[2].label).toBe('4×');
    expect(chips[2].state).toBe('waiting');
  });
});

describe('Patch, video', () => {
  it('names a frame-by-frame video plan without De-flicker, added frames and audio', () => {
    const stage = (kind: string, role: string, label: string, fix?: string) =>
      ({ kind, role, label, fix, installed: true }) as never;
    const entries = patchPlanEntries(
      [
        stage('audio_denoise', 'audio_denoise', 'Clean up audio'),
        stage('restore', 'fix', 'Noise', 'noise'),
        stage('upscale', 'primary', 'Upscale ×2'),
        stage('deflicker', 'step', 'De-flicker'),
        stage('interpolate', 'interpolate', 'Interpolate 2×'),
      ],
      {
        task: 'video',
        output_scale: 2,
        video_target_resolution: 0,
        video_interpolation: 'x2',
      },
    );
    expect(entries.map((entry) => entry.name)).toEqual(['Noise', '2×']);
  });
});

describe('Patch, strength', () => {
  it('keeps the chip names bare and changes the request key with the strength', () => {
    const stages = [
      { kind: 'restore', role: 'fix', label: 'Noise', fix: 'noise', installed: true },
      { kind: 'upscale', role: 'primary', label: 'Upscale ×4', installed: true },
    ] as never;
    const settings = {
      task: 'upscale' as const,
      output_scale: 4,
      video_target_resolution: 0,
      video_interpolation: 'off' as const,
      fix_stages: [{ fix: 'noise', model_id: 'm', params: { strength: 0.4 } }],
    };
    expect(patchPlanEntries(stages, settings).map((entry) => entry.name)).toEqual(['Noise', '4×']);

    const region = { x: 0, y: 0, width: 384, height: 384 };
    const key = (strength: number) =>
      patchRequestKey({
        mediaId: 'p1',
        slot: 'a',
        region,
        input: { fix_stages: [{ fix: 'noise', params: { strength } }], output_scale: 4 } as never,
      });
    expect(key(0.4)).not.toBe(key(0.7));
  });
});
