import { describe, expect, it } from 'vitest';
import {
  DEFAULT_FIX_ORDER,
  FIX_LABELS,
  FIX_NAMES,
  FIX_ROW_LABELS,
  MULTI_FIX_FEATURE,
  PHASES,
  STAGE_LABELS,
  STAGE_PARAMS_FEATURE,
  STEP_LIMITS,
  STEPS,
  fixDefaultStageKind,
  isFixStageKind,
  isVideoOrUpscalePrimaryKind,
  stepFeature,
  stepStage,
  validateStageParams,
  validateStepParams,
  type StepDefinition,
} from './steps';
import type { PlanStage } from './plan';
import type { FixKind, RecipeStage, StageKind } from './types';

describe('step registry', () => {
  it('derives fix ordering, labels, stage kinds and feature gates from steps.json', () => {
    expect(PHASES.length).toBeGreaterThan(0);
    expect(DEFAULT_FIX_ORDER).toEqual(['blur', 'jpeg', 'noise', 'colour']);
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

  it('names grain a step a job adds by id, after the main model, for developers only', () => {
    expect(stepStage('grain')).toEqual({
      step: STEPS.find((step) => step.id === 'grain'),
      placement: 'after_primary',
    });
    const grain = stepStage('grain')!.step;
    expect(grain.dev_only).toBe(true);
    expect(grain.media).toEqual(['image']);
    expect(grain.supports_mask).toBe(true);
    expect(grain.required_feature).toBe('image_grain');
    expect(STEPS.filter((step) => step.dev_only).map((step) => step.id)).toEqual([
      'deinterlace',
      'stabilise',
      'remove_background',
      'grain',
    ]);
    expect(STEP_LIMITS.max_step_stages).toBe(4);
    for (const id of ['upscale', 'noise', 'face_restore', 'deflicker', 'edit', 'missing']) {
      expect(stepStage(id)).toBeUndefined();
    }
    expect(validateStageParams('grain', { strength: 100, seed: 2147483647 })).toBeNull();
    for (const bad of [{ strength: 101 }, { strength: -1 }, { strength: 2.5 }, { seed: -1 }]) {
      expect(validateStageParams('grain', bad)).not.toBeNull();
    }
  });

  it('places a step by its phase and ignores steps outside the stage list', () => {
    const base = STEPS.find((step) => step.id === 'grain')!;
    const level: StepDefinition = { ...base, id: 'level', phase: 'geometry' };
    const besideFace: StepDefinition = { ...base, id: 'beside_face', phase: 'face' };
    const notListed: StepDefinition = { ...base, id: 'not_listed', in_image_pipeline: false };
    const steps = [level, besideFace, notListed];
    expect(stepStage('level', steps)?.placement).toBe('before_fixes');
    expect(stepStage('beside_face', steps)).toBeUndefined();
    expect(stepStage('not_listed', steps)).toBeUndefined();
  });

  it('takes a step with a model only when it names the catalog task of its models', () => {
    const base = STEPS.find((step) => step.id === 'grain')!;
    const withTask: StepDefinition = {
      ...base,
      id: 'with_task',
      phase: 'cleanup',
      needs_model: true,
      model_task: 'matte',
      output_alpha: true,
    };
    const withoutTask: StepDefinition = { ...withTask, id: 'without_task', model_task: null };
    const fix: StepDefinition = { ...withTask, id: 'fix_with_task', is_fix_stage: true };
    const steps = [withTask, withoutTask, fix];
    expect(stepStage('with_task', steps)).toEqual({ step: withTask, placement: 'before_fixes' });
    expect(stepStage('without_task', steps)).toBeUndefined();
    expect(stepStage('fix_with_task', steps)).toBeUndefined();
    const background = stepStage('remove_background');
    expect(background?.placement).toBe('before_fixes');
    expect(background?.step.model_task).toBe('background');
    expect(background?.step.output_alpha).toBe(true);
  });

  it('validates stage params generically from a step definition', () => {
    expect(STAGE_PARAMS_FEATURE).toBe('stage_params_and_masks');
    const fixtureStep: StepDefinition = {
      id: 'test_tint',
      media: ['image'],
      phase: 'grade',
      order: 10,
      needs_model: false,
      supports_mask: true,
      stage_kind: 'test_tint',
      role: null,
      fix_kind: null,
      is_fix_stage: false,
      fix_chip_order: null,
      in_image_pipeline: true,
      in_recipe_stages: false,
      label: 'Test tint',
      row_label: '',
      short_name: '',
      stage_label: '',
      required_feature: null,
      dev_only: false,
      model_task: null,
      output_alpha: false,
      parameters: [
        {
          name: 'amount',
          kind: 'number',
          default: null,
          minimum: 0,
          maximum: 1,
          choices: [],
          int_choices: [],
          ui_minimum: null,
          ui_maximum: null,
          ui_default: null,
          nullable: false,
          required: true,
        },
        {
          name: 'passes',
          kind: 'int',
          default: 1,
          minimum: 1,
          maximum: 4,
          choices: [],
          int_choices: [],
          ui_minimum: null,
          ui_maximum: null,
          ui_default: null,
          nullable: false,
          required: false,
        },
        {
          name: 'mode',
          kind: 'choice',
          default: 'add',
          minimum: null,
          maximum: null,
          choices: ['add', 'multiply'],
          int_choices: [],
          ui_minimum: null,
          ui_maximum: null,
          ui_default: null,
          nullable: false,
          required: false,
        },
        {
          name: 'bucket',
          kind: 'int_choice',
          default: 16,
          minimum: null,
          maximum: null,
          choices: [],
          int_choices: [8, 16, 32],
          ui_minimum: null,
          ui_maximum: null,
          ui_default: null,
          nullable: false,
          required: false,
        },
        {
          name: 'clamp',
          kind: 'bool',
          default: true,
          minimum: null,
          maximum: null,
          choices: [],
          int_choices: [],
          ui_minimum: null,
          ui_maximum: null,
          ui_default: null,
          nullable: false,
          required: false,
        },
        {
          name: 'label',
          kind: 'str',
          default: null,
          minimum: null,
          maximum: null,
          choices: [],
          int_choices: [],
          ui_minimum: null,
          ui_maximum: null,
          ui_default: null,
          nullable: true,
          required: false,
        },
      ],
    };

    expect(
      validateStepParams(fixtureStep, {
        amount: 0.5,
        passes: 2,
        mode: 'multiply',
        bucket: 32,
        clamp: false,
        label: null,
      }),
    ).toBeNull();

    expect(validateStageParams('test_tint', { amount: 0.5 }, [fixtureStep])).toBeNull();
    expect(validateStageParams('missing_step', {}, [fixtureStep])).not.toBeNull();

    for (const bad of [
      undefined,
      null,
      {},
      { amount: null },
      { amount: -0.1 },
      { amount: 1.1 },
      { amount: Number.NaN },
      { amount: true },
      { amount: '0.5' },
      { amount: 0.5, passes: 0 },
      { amount: 0.5, passes: 5 },
      { amount: 0.5, passes: 1.5 },
      { amount: 0.5, mode: 'screen' },
      { amount: 0.5, bucket: 12 },
      { amount: 0.5, clamp: 'yes' },
      { amount: 0.5, label: 42 },
      { amount: 0.5, unknown: 1 },
    ]) {
      expect(validateStepParams(fixtureStep, bad as Record<string, unknown>)).not.toBeNull();
    }
  });
});
