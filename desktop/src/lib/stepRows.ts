import { servesStep } from './plan';
import { STEPS } from './steps';
import type { CatalogModel, StepStageSelection, UiSettings } from './types';

/** The registry id of the step the Backgr. row stores. */
export const BACKGROUND_STEP = 'remove_background';

/** `stages` with the entry of `step` replaced by `entry`, appended, or removed (`null`). */
export function withStepStage(
  stages: readonly StepStageSelection[] | undefined,
  step: string,
  entry: StepStageSelection | null,
): StepStageSelection[] {
  const current = stages ?? [];
  const index = current.findIndex((selection) => selection.step === step);
  if (index < 0) return entry ? [...current, entry] : [...current];
  if (!entry) return current.filter((_, at) => at !== index);
  return current.map((selection, at) => (at === index ? entry : selection));
}

/** The model the Backgr. row chooses: the first catalog model that serves the step. */
export function backgroundModel(models: readonly CatalogModel[]): CatalogModel | undefined {
  const step = STEPS.find((candidate) => candidate.id === BACKGROUND_STEP);
  return step ? models.find((model) => servesStep(model, step)) : undefined;
}

/** Whether the background is set to be removed. */
export function backgroundRemoved(settings: Pick<UiSettings, 'step_stages'>): boolean {
  return (settings.step_stages ?? []).some((selection) => selection.step === BACKGROUND_STEP);
}

/** Whether the Backgr. row is drawn: a photo task, the engine feature and a model, or a stored choice. */
export function backgroundRowShown(o: {
  task: UiSettings['task'];
  features: readonly string[];
  model: CatalogModel | undefined;
  removed: boolean;
}): boolean {
  if (o.task !== 'upscale' && o.task !== 'denoise') return false;
  if (o.removed) return true;
  return o.features.includes('image_background_removal') && o.model !== undefined;
}
