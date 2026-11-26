import { describe, expect, it } from 'vitest';
import {
  DEFAULT_FIX_ORDER,
  FIX_CHIPS,
  FIX_LABELS,
  FIX_NAMES,
  FIX_ROW_LABELS,
  MULTI_FIX_FEATURE,
  PHASES,
  STAGE_LABELS,
  STEP_LIMITS,
  STEPS,
  fixDefaultStageKind,
  isFixStageKind,
  isVideoOrUpscalePrimaryKind,
  stepFeature,
} from './steps';
import type { PlanStage } from './plan';
import type { FixKind, RecipeStage, StageKind } from './types';

describe('step registry', () => {
  it('derives fix ordering, labels, stage kinds and feature gates from steps.json', () => {
    expect(PHASES.length).toBeGreaterThan(0);
    expect(DEFAULT_FIX_ORDER).toEqual(['blur', 'jpeg', 'noise', 'colour']);
    expect(FIX_CHIPS).toEqual(['noise', 'blur', 'jpeg', 'colour']);
    expect(FIX_LABELS).toEqual({
      noise: 'Noise',
      jpeg: 'JPEG artifacts',
      blur: 'Blur',
      colour: 'Colour',
      faces: 'Faces',
    });
    expect(FIX_ROW_LABELS).toEqual({
      noise: 'Fix noise',
      jpeg: 'Fix JPEG',
      blur: 'Fix blur',
      colour: 'Colour',
    });
    expect(FIX_NAMES).toEqual({
      blur: 'blur',
      jpeg: 'JPEG',
      noise: 'noise',
      colour: 'colour',
    });
    expect(STAGE_LABELS).toEqual({
      deblock: 'Removing JPEG artifacts',
      restore: 'Restoring',
      upscale: 'Upscaling',
      face_restore: 'Restoring faces',
      edit: 'Preparing edit',
    });
    expect(MULTI_FIX_FEATURE).toBe('image_multi_fix');
    expect(stepFeature('edit')).toBe('image_edit');
    expect(stepFeature('interpolate')).toBe('video_interpolation');
    expect(stepFeature('audio_denoise')).toBe('video_audio_denoise');
    expect(STEP_LIMITS.max_fix_stages).toBe(4);
    expect(STEP_LIMITS.max_fix_chips).toBe(5);
    expect(STEP_LIMITS.max_recipe_stages).toBe(6);
    expect(fixDefaultStageKind('jpeg')).toBe('deblock');
    expect(fixDefaultStageKind('blur')).toBe('restore');
    expect(isFixStageKind('deblock')).toBe(true);
    expect(isFixStageKind('restore')).toBe(true);
    expect(isFixStageKind('upscale')).toBe(false);
    expect(isVideoOrUpscalePrimaryKind('upscale')).toBe(true);
    expect(isVideoOrUpscalePrimaryKind('video')).toBe(true);
    expect(isVideoOrUpscalePrimaryKind('restore')).toBe(false);
  });

  it('keeps TypeScript union types aligned with the exported registry', () => {
    const fixKinds = STEPS.map((step) => step.fix_kind).filter(
      (fix): fix is FixKind => fix !== null,
    );
    expect(new Set(fixKinds)).toEqual(new Set(['noise', 'jpeg', 'blur', 'colour', 'faces']));

    const imageStages = STEPS.filter((step) => step.in_image_pipeline && step.stage_kind !== null)
      .map((step) => step.stage_kind)
      .filter((kind): kind is StageKind => kind !== null);
    expect(new Set(imageStages)).toEqual(
      new Set(['deblock', 'restore', 'upscale', 'face_restore']),
    );

    const recipeStages = STEPS.filter((step) => step.in_recipe_stages && step.stage_kind !== null)
      .map((step) => step.stage_kind)
      .filter((kind): kind is RecipeStage['kind'] => kind !== null);
    expect(new Set(recipeStages)).toEqual(
      new Set(['deblock', 'restore', 'upscale', 'face_restore', 'video']),
    );

    const planStages = STEPS.map((step) => step.stage_kind).filter(
      (kind): kind is PlanStage['kind'] => kind !== null,
    );
    expect(new Set(planStages)).toEqual(
      new Set([
        'deblock',
        'restore',
        'upscale',
        'face_restore',
        'video',
        'edit',
        'interpolate',
        'audio_denoise',
      ]),
    );
  });
});
