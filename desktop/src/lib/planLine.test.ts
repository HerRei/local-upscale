import { describe, expect, it } from 'vitest';
import type { PlanStage } from './plan';
import { planLine } from './planLine';
import type { MediaItem, UiSettings } from './types';

function stage(overrides: Partial<PlanStage>): PlanStage {
  return {
    kind: 'upscale',
    role: 'primary',
    label: 'Upscale ×4',
    installed: true,
    canDownload: false,
    needsFile: false,
    sizeBytes: 0,
    ...overrides,
  };
}

const baseSettings: Pick<
  UiSettings,
  'task' | 'output_scale' | 'video_target_resolution' | 'video_interpolation'
> = {
  task: 'upscale',
  output_scale: 4,
  video_target_resolution: 0,
  video_interpolation: 'off',
};

const photo: Pick<MediaItem, 'kind'> = { kind: 'image' };
const video: Pick<MediaItem, 'kind'> = { kind: 'video' };

describe('planLine', () => {
  it('returns an empty list for an empty plan', () => {
    expect(planLine([], baseSettings, photo)).toEqual([]);
  });

  it('formats fix stages and preserves a moved order', () => {
    const stages: PlanStage[] = [
      stage({ kind: 'restore', role: 'fix', fix: 'noise', label: 'Fix noise' }),
      stage({ kind: 'restore', role: 'fix', fix: 'blur', label: 'Fix blur' }),
      stage({ kind: 'deblock', role: 'fix', fix: 'jpeg', label: 'Fix JPEG' }),
      stage({ kind: 'restore', role: 'fix', fix: 'colour', label: 'Colour' }),
      stage({ kind: 'upscale', role: 'primary', label: 'Upscale ×4' }),
    ];
    expect(planLine(stages, baseSettings, photo)).toEqual([
      'Noise',
      'Blur',
      'JPEG',
      'Colour',
      'Upscale 4×',
    ]);
  });

  it('names jpeg Compression on video and uses primary fix label when restoring', () => {
    const stages: PlanStage[] = [
      stage({ kind: 'deblock', role: 'primary', fix: 'jpeg', label: 'Fix JPEG' }),
    ];
    expect(planLine(stages, { ...baseSettings, task: 'video', output_scale: 1 }, video)).toEqual([
      'Compression',
    ]);
  });

  it('formats upscale with output_scale and video_target_resolution', () => {
    expect(
      planLine(
        [stage({ kind: 'upscale', role: 'primary', label: 'Upscale ×2' })],
        { ...baseSettings, output_scale: 2 },
        photo,
      ),
    ).toEqual(['Upscale 2×']);

    expect(
      planLine(
        [stage({ kind: 'video', role: 'primary', label: 'Upscale' })],
        { ...baseSettings, task: 'video', video_target_resolution: 1080 },
        video,
      ),
    ).toEqual(['Upscale to 1080 px']);
  });

  it('formats own-file restore without a fix as Fix', () => {
    expect(
      planLine(
        [stage({ kind: 'restore', role: 'primary', label: 'Fix', custom: '/mine.safetensors' })],
        { ...baseSettings, task: 'denoise', output_scale: 1 },
        photo,
      ),
    ).toEqual(['Fix']);
  });

  it('formats edit, deflicker, interpolate targets, audio_denoise, face_restore, and unknown kinds', () => {
    expect(
      planLine(
        [
          stage({ kind: 'audio_denoise', role: 'audio_denoise', label: 'Clean up audio' }),
          stage({ kind: 'edit', role: 'primary', label: 'Edit' }),
          stage({ kind: 'deflicker', role: 'step', label: 'De-flicker' }),
          stage({ kind: 'face_restore', role: 'face', label: 'Faces' }),
          stage({
            kind: 'future_step' as PlanStage['kind'],
            role: 'step',
            label: 'Future step',
          }),
        ],
        baseSettings,
        video,
      ),
    ).toEqual(['Clean audio', 'Edit', 'De-flicker', 'Faces', 'Future step']);

    for (const [target, expected] of [
      ['x2', '2× frames'],
      ['x4', '4× frames'],
      ['fps60', '60 fps'],
      ['fps120', '120 fps'],
    ] as const) {
      expect(
        planLine(
          [stage({ kind: 'interpolate', role: 'interpolate', label: 'Interpolate' })],
          { ...baseSettings, task: 'video', video_interpolation: target },
          video,
        ),
      ).toEqual([expected]);
    }
  });

  it('names slow motion, and only with 2x or 4x added frames', () => {
    const interpolate = [stage({ kind: 'interpolate', role: 'interpolate', label: 'Interpolate' })];
    for (const [target, expected] of [
      ['x2', 'Slow motion 2×'],
      ['x4', 'Slow motion 4×'],
      ['fps60', '60 fps'],
    ] as const) {
      expect(
        planLine(
          interpolate,
          { ...baseSettings, task: 'video', video_interpolation: target, video_slow_motion: true },
          video,
        ),
      ).toEqual([expected]);
    }
    expect(
      planLine(
        interpolate,
        { ...baseSettings, task: 'video', video_interpolation: 'x2', video_slow_motion: false },
        video,
      ),
    ).toEqual(['2× frames']);
  });

  it('names the background step Background', () => {
    expect(
      planLine(
        [stage({ kind: 'remove_background', role: 'step', label: 'Remove background' })],
        baseSettings,
        photo,
      ),
    ).toEqual(['Background']);
  });

  it('names the deinterlace step from the Scan choice and the rate', () => {
    const scan = (choice: [string, string], fps: number) =>
      planLine(
        [stage({ kind: 'deinterlace', role: 'step', label: 'Deinterlace' })],
        {
          ...baseSettings,
          task: 'video',
          step_stages: [{ step: 'deinterlace', params: { mode: choice[0], parity: choice[1] } }],
        },
        { kind: 'video', fps },
      );
    expect(scan(['field', 'auto'], 25)).toEqual(['Deinterlace to 50 fps']);
    expect(scan(['field', 'auto'], 29.97)).toEqual(['Deinterlace to 59.94 fps']);
    expect(scan(['field', 'auto'], 0)).toEqual(['Deinterlace 2×']);
    expect(scan(['frame', 'tff'], 25)).toEqual(['Deinterlace']);
  });
});

describe('planLine, strength', () => {
  const entry = (fix: 'noise' | 'jpeg', strength?: number) => ({
    fix,
    model_id: 'm',
    ...(strength === undefined ? {} : { params: { strength } }),
  });

  it('adds the strength in lower case, and nothing at Full', () => {
    const stages: PlanStage[] = [
      stage({ kind: 'restore', role: 'fix', fix: 'noise', label: 'Fix noise' }),
      stage({ kind: 'upscale', role: 'primary', label: 'Upscale ×4' }),
    ];
    const line = (fixStages: ReturnType<typeof entry>[]) =>
      planLine(stages, { ...baseSettings, fix_stages: fixStages }, photo);
    expect(line([entry('noise', 0.4)])).toEqual(['Noise (light)', 'Upscale 4×']);
    expect(line([entry('noise', 0.55)])).toEqual(['Noise (55%)', 'Upscale 4×']);
    expect(line([entry('noise')])).toEqual(['Noise', 'Upscale 4×']);
    expect(line([entry('noise', 1)])).toEqual(['Noise', 'Upscale 4×']);
  });

  it('names Compression (medium) on video and drops amounts on request', () => {
    const stages: PlanStage[] = [
      stage({ kind: 'deblock', role: 'fix', fix: 'jpeg', label: 'Fix JPEG' }),
    ];
    const settings = {
      ...baseSettings,
      task: 'video' as const,
      fix_stages: [entry('jpeg', 0.7)],
    };
    expect(planLine(stages, settings, video)).toEqual(['Compression (medium)']);
    expect(planLine(stages, settings, video, { amounts: false })).toEqual(['Compression']);
  });
});
