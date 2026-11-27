import rawSteps from '../../src-tauri/resources/steps.json';
import type { FixKind } from './types';

export type FixStageKind = Exclude<FixKind, 'faces'>;

const fixStageSteps = [...rawSteps.steps]
  .filter((step) => step.is_fix_stage && step.fix_kind !== null)
  .sort((left, right) => left.order - right.order);

export interface StepParameter {
  name: string;
  kind: string;
  default: boolean | number | string | null;
  minimum: number | null;
  maximum: number | null;
  choices: readonly string[];
  int_choices: readonly number[];
  ui_minimum: number | null;
  ui_maximum: number | null;
  ui_default: number | null;
  nullable: boolean;
  required: boolean;
}

export interface StepDefinition {
  id: string;
  media: readonly string[];
  phase: string;
  order: number;
  needs_model: boolean;
  supports_mask: boolean;
  stage_kind: string | null;
  role: string | null;
  fix_kind: string | null;
  is_fix_stage: boolean;
  fix_chip_order: number | null;
  in_image_pipeline: boolean;
  in_recipe_stages: boolean;
  label: string;
  row_label: string;
  short_name: string;
  stage_label: string;
  required_feature: string | null;
  /** The engine lists this step's feature only when started with `LOCALSR_DEV_STEPS=1`. */
  dev_only: boolean;
  parameters: readonly StepParameter[];
}

/** Where a step a job adds by id runs: before the fixes, or after the main model and Faces. */
export type StepPlacement = 'before_fixes' | 'after_primary';

export const STEPS: readonly StepDefinition[] = rawSteps.steps;
export const PHASES = rawSteps.phases;
export const STEP_LIMITS = rawSteps.limits;
export const MULTI_FIX_FEATURE = rawSteps.limits.multi_fix_feature;
export const STAGE_PARAMS_FEATURE = rawSteps.limits.stage_params_feature;

/** The order a fix takes when it is turned on: deblur first, colour last. */
export const DEFAULT_FIX_ORDER: FixStageKind[] = fixStageSteps.map(
  (step) => step.fix_kind as FixStageKind,
);

export const FIX_LABELS: Record<FixKind, string> = Object.fromEntries(
  rawSteps.steps
    .filter((step) => step.fix_kind !== null)
    .map((step) => [step.fix_kind, step.label]),
) as Record<FixKind, string>;

export const FIX_ROW_LABELS: Record<FixStageKind, string> = Object.fromEntries(
  fixStageSteps.map((step) => [step.fix_kind, step.row_label]),
) as Record<FixStageKind, string>;

export const FIX_NAMES: Record<FixStageKind, string> = Object.fromEntries(
  fixStageSteps.map((step) => [step.fix_kind, step.short_name]),
) as Record<FixStageKind, string>;

export const STAGE_LABELS: Record<string, string> = Object.fromEntries(
  rawSteps.steps
    .filter((step) => step.stage_kind !== null && step.stage_label.length > 0)
    .map((step) => [step.stage_kind, step.stage_label]),
);

export function stepFeature(stepId: string): string {
  const feature = rawSteps.steps.find((step) => step.id === stepId)?.required_feature;
  if (!feature) throw new Error(`Step ${stepId} does not define a required_feature`);
  return feature;
}

export function fixDefaultStageKind(fix: FixStageKind): 'deblock' | 'restore' {
  const kind = fixStageSteps.find((step) => step.fix_kind === fix)?.stage_kind;
  return kind === 'deblock' ? 'deblock' : 'restore';
}

export function isFixStageKind(kind: string): boolean {
  return fixStageSteps.some((step) => step.stage_kind === kind);
}

export function isVideoOrUpscalePrimaryKind(kind: string): boolean {
  return rawSteps.steps.some(
    (step) =>
      step.in_recipe_stages &&
      step.role === 'primary' &&
      step.id !== 'restore' &&
      step.stage_kind === kind,
  );
}

/** A model-less step of the stage list that a job adds by id beside its fixes. */
export function stepStage(
  id: string,
  steps: readonly StepDefinition[] = STEPS,
): { step: StepDefinition; placement: StepPlacement } | undefined {
  const step = steps.find(
    (candidate) =>
      candidate.id === id &&
      candidate.in_image_pipeline &&
      !candidate.needs_model &&
      !candidate.is_fix_stage,
  );
  if (!step) return undefined;
  const phase = PHASES.indexOf(step.phase);
  if (phase === -1) return undefined;
  if (phase < PHASES.indexOf('primary')) return { step, placement: 'before_fixes' };
  if (phase > PHASES.indexOf('face')) return { step, placement: 'after_primary' };
  return undefined;
}

function validateParamValue(stepId: string, param: StepParameter, value: unknown): string | null {
  if (value === null) {
    return param.nullable ? null : `Parameter "${param.name}" for step "${stepId}" cannot be null.`;
  }
  switch (param.kind) {
    case 'bool':
      return typeof value === 'boolean'
        ? null
        : `Parameter "${param.name}" for step "${stepId}" must be a boolean.`;
    case 'str':
      return typeof value === 'string'
        ? null
        : `Parameter "${param.name}" for step "${stepId}" must be a string.`;
    case 'choice':
      return typeof value === 'string' && param.choices.includes(value)
        ? null
        : `Parameter "${param.name}" for step "${stepId}" must be one of ${param.choices.join(', ')}.`;
    case 'int_choice':
      return typeof value === 'number' &&
        Number.isInteger(value) &&
        param.int_choices.includes(value)
        ? null
        : `Parameter "${param.name}" for step "${stepId}" must be one of ${param.int_choices.join(', ')}.`;
    case 'int':
      if (typeof value !== 'number' || !Number.isInteger(value)) {
        return `Parameter "${param.name}" for step "${stepId}" must be an integer.`;
      }
      break;
    case 'number':
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        return `Parameter "${param.name}" for step "${stepId}" must be a finite number.`;
      }
      break;
    default:
      return `Unsupported parameter kind "${param.kind}" for "${stepId}.${param.name}".`;
  }
  if (param.minimum !== null && value < param.minimum) {
    return `Parameter "${param.name}" for step "${stepId}" must be >= ${param.minimum}.`;
  }
  if (param.maximum !== null && value > param.maximum) {
    return `Parameter "${param.name}" for step "${stepId}" must be <= ${param.maximum}.`;
  }
  return null;
}

export function validateStepParams(step: StepDefinition, rawParams: unknown): string | null {
  let params: Record<string, unknown>;
  if (rawParams === undefined || rawParams === null) {
    params = {};
  } else if (typeof rawParams === 'object' && !Array.isArray(rawParams)) {
    params = rawParams as Record<string, unknown>;
  } else {
    return `Parameters for step "${step.id}" must be an object.`;
  }

  const byName = new Map(step.parameters.map((param) => [param.name, param]));
  for (const key of Object.keys(params)) {
    if (!byName.has(key)) {
      return `Unknown parameter "${key}" for step "${step.id}".`;
    }
  }
  for (const param of step.parameters) {
    if (param.required && !Object.hasOwn(params, param.name)) {
      return `Missing required parameter "${param.name}" for step "${step.id}".`;
    }
  }
  for (const [key, value] of Object.entries(params)) {
    const param = byName.get(key);
    if (!param) continue;
    const error = validateParamValue(step.id, param, value);
    if (error !== null) return error;
  }
  return null;
}

export function validateStageParams(
  stepId: string,
  rawParams: unknown,
  steps: readonly StepDefinition[] = STEPS,
): string | null {
  const step = steps.find((candidate) => candidate.id === stepId);
  if (!step) return `Unknown step "${stepId}".`;
  return validateStepParams(step, rawParams);
}
