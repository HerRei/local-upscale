import { fixesOf, presetMayPick, rankForQuality, rightsAllowPreset } from './plan';
import type { AppSnapshot, CatalogModel, FixKind, FixStageSelection, Quality } from './types';
/**
 * Fix models that can produce unstable results. The engine skips these before an upscale.
 */
export const UNSTABLE_FIX_MODELS = new Set(['nafnet_gopro_deblur']);

export const FIX_ROW_LABELS: Record<Exclude<FixKind, 'faces'>, string> = {
  noise: 'Fix noise',
  jpeg: 'Fix JPEG',
  blur: 'Fix blur',
};

/**
 * Where the user's own fix model is remembered. One choice per problem and quality,
 * shared by Upscale and Restore and by Photo and Illustration; it cannot collide with
 * `presetSlot`, whose first part is a task.
 */
export function fixSlot(fix: Exclude<FixKind, 'faces'>, quality: 'quick' | 'best'): string {
  return `fix/${fix}/${quality}`;
}

/** The quality whose fix a job uses. A hand-picked upscaler uses Best's. */
export function fixQuality(quality: Quality | ''): 'quick' | 'best' {
  return quality === 'quick' ? 'quick' : 'best';
}

/** A 1× model that fixes this problem; the host accepts nothing else as a fix. */
export function fitsFix(model: CatalogModel, fix: Exclude<FixKind, 'faces'>): boolean {
  return (
    model.native_scale === 1 && !model.purposes.includes('face') && fixesOf(model).includes(fix)
  );
}

/** The order a fix takes when it is turned on: deblur first, noise last. */
export const DEFAULT_FIX_ORDER: Exclude<FixKind, 'faces'>[] = ['blur', 'jpeg', 'noise'];

/**
 * Turn a fix off, or on at its place in the default order: before the first fix that
 * comes after it there, so an order the user changed stays as it is. A new fix has no
 * model yet; resolving the recipe gives it one.
 */
export function toggleFixStage(
  stages: FixStageSelection[],
  fix: Exclude<FixKind, 'faces'>,
): FixStageSelection[] {
  if (stages.some((stage) => stage.fix === fix)) return stages.filter((stage) => stage.fix !== fix);
  const rank = DEFAULT_FIX_ORDER.indexOf(fix);
  const at = stages.findIndex((stage) => DEFAULT_FIX_ORDER.indexOf(stage.fix) > rank);
  const next = [...stages];
  next.splice(at < 0 ? next.length : at, 0, { fix, model_id: '' });
  return next;
}

/** Swap a fix with its neighbour; the same list comes back when it cannot move. */
export function moveFixStage(
  stages: FixStageSelection[],
  fix: Exclude<FixKind, 'faces'>,
  offset: -1 | 1,
): FixStageSelection[] {
  const index = stages.findIndex((stage) => stage.fix === fix);
  const other = index + offset;
  if (index < 0 || other < 0 || other >= stages.length) return stages;
  const next = [...stages];
  [next[index], next[other]] = [next[other], next[index]];
  return next;
}

/** How a fix stage is named in the plan, the recipe and the job (the host agrees). */
export function fixStageKind(
  model: CatalogModel | undefined,
  fix: Exclude<FixKind, 'faces'>,
): 'deblock' | 'restore' {
  return model?.stage === 'deblock' || fix === 'jpeg' ? 'deblock' : 'restore';
}

/**
 * The remembered fix model, checked on every read: a pin left behind by a catalog
 * change, or edited by hand, must still fix that problem and allow a preset.
 */
export function rememberedFixModel(
  snapshot: AppSnapshot,
  fix: Exclude<FixKind, 'faces'>,
  quality: 'quick' | 'best',
  pins: Record<string, string> = {},
): CatalogModel | undefined {
  const id = pins[fixSlot(fix, quality)];
  const model = id ? snapshot.catalog.models.find((entry) => entry.model_id === id) : undefined;
  return model && fitsFix(model, fix) && rightsAllowPreset(model) ? model : undefined;
}

/** The 1× checkpoint a Fix chip resolves to: the user's own choice, else by quality. */
export function chooseFixModel(
  snapshot: AppSnapshot,
  fix: Exclude<FixKind, 'faces'>,
  quality: 'quick' | 'best',
  pins: Record<string, string> = {},
): CatalogModel | undefined {
  const remembered = rememberedFixModel(snapshot, fix, quality, pins);
  if (remembered) return remembered;
  const candidates = snapshot.catalog.models.filter(
    (model) => fitsFix(model, fix) && presetMayPick(model),
  );
  return rankForQuality(candidates, quality)[0];
}
