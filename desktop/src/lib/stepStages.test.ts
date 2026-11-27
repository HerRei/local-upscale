import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { resolvePlan } from './plan';
import { buildStartInput } from './startInput';
import type { StepStageSelection, UiSettings } from './types';

const grain: StepStageSelection = {
  step: 'grain',
  params: { strength: 40, seed: 7 },
  mask_path: '/in/mask.png',
};

function settingsWith(patch: Partial<UiSettings>): UiSettings {
  return {
    ...demoSnapshot().settings,
    selected_model_id: 'span_photo_x4',
    output_scale: 4,
    fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
    step_stages: [{ step: 'tool_from_a_newer_version' }, grain],
    ...patch,
  };
}

describe('step stages', () => {
  it('run after the main model and Faces on photos, as a plan row and in the start input', () => {
    const snapshot = demoSnapshot();
    const settings = settingsWith({ task: 'upscale' });
    const faceModel = snapshot.catalog.models.find((model) => model.model_id === 'hat_l_x4_face');
    const plan = resolvePlan(snapshot, settings, {
      faceModel,
      faceEnabled: true,
      usingTemporalVideo: false,
    });
    expect(plan.map((stage) => stage.kind)).toEqual([
      'restore',
      'upscale',
      'face_restore',
      'grain',
    ]);
    expect(plan.at(-1)).toEqual({
      kind: 'grain',
      role: 'step',
      label: 'Film grain',
      installed: true,
      canDownload: false,
      needsFile: false,
      sizeBytes: 0,
    });
    const input = buildStartInput(settings, { mediaIds: ['m'], task: 'upscale', batchMode: false });
    expect(input.step_stages).toEqual([grain]);

    const restore = settingsWith({
      task: 'denoise',
      selected_model_id: 'denoise_realplksr_1x',
      output_scale: 1,
    });
    expect(
      resolvePlan(snapshot, restore, { faceEnabled: false, usingTemporalVideo: false }).map(
        (stage) => stage.kind,
      ),
    ).toEqual(['restore', 'grain']);
  });

  it('are left out where their media does not run, and the start input omits an empty list', () => {
    const snapshot = demoSnapshot();
    for (const [settings, usingTemporalVideo] of [
      [settingsWith({ task: 'video', selected_video_model_id: 'frame_by_frame' }), false],
      [settingsWith({ task: 'video', selected_video_model_id: 'seedvr2_3b' }), true],
      [settingsWith({ task: 'edit' }), false],
    ] as const) {
      const plan = resolvePlan(snapshot, settings, { faceEnabled: false, usingTemporalVideo });
      expect(plan.some((stage) => stage.kind === 'grain')).toBe(false);
      const input = buildStartInput(settings, {
        mediaIds: ['m'],
        task: settings.task,
        batchMode: false,
      });
      expect(input).not.toHaveProperty('step_stages');
    }
    const plain = buildStartInput(settingsWith({ task: 'upscale', step_stages: undefined }), {
      mediaIds: ['m'],
      task: 'upscale',
      batchMode: false,
    });
    expect(plain).not.toHaveProperty('step_stages');
  });
});
