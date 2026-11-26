import rawSteps from '../../src-tauri/resources/steps.json';
import type { FixKind } from './types';

export type FixStageKind = Exclude<FixKind, 'faces'>;

const fixStageSteps = [...rawSteps.steps]
  .filter((step) => step.is_fix_stage && step.fix_kind !== null)
  .sort((left, right) => left.order - right.order);

const fixChipOrderSteps = [...fixStageSteps].sort(
  (left, right) => (left.fix_chip_order ?? left.order) - (right.fix_chip_order ?? right.order),
);

export const STEPS = rawSteps.steps;
export const PHASES = rawSteps.phases;
export const STEP_LIMITS = rawSteps.limits;
export const MULTI_FIX_FEATURE = rawSteps.limits.multi_fix_feature;

/** The order a fix takes when it is turned on: deblur first, colour last. */
export const DEFAULT_FIX_ORDER: FixStageKind[] = fixStageSteps.map(
  (step) => step.fix_kind as FixStageKind,
);

/** The order Fix chips appear in the inspector. */
export const FIX_CHIPS: FixStageKind[] = fixChipOrderSteps.map(
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
