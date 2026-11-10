import { fixesOf } from './plan';
import type { AppSnapshot, Recipe, FixKind, FixStageSelection, UiSettings } from './types';

/** Resolve recipe fix stages in order, including recipes that name fixes only in chips. */
export function recipeFixStages(
  recipe: Recipe,
  catalog: AppSnapshot['catalog'],
): FixStageSelection[] {
  if (recipe.task !== 'upscale' && recipe.task !== 'denoise') return [];
  const stages = recipe.stages ?? [];
  const chip = recipe.fixes?.find((fix): fix is Exclude<FixKind, 'faces'> => fix !== 'faces');
  const primary =
    recipe.task === 'denoise'
      ? stages.length - 1
      : stages.findIndex((stage) => stage.kind === 'upscale');
  const result: FixStageSelection[] = [];
  stages.forEach((stage, index) => {
    if (stage.kind !== 'restore' && stage.kind !== 'deblock') return;
    if (index > primary) return;
    const model = catalog.models.find((entry) => entry.model_id === stage.model_id);
    const fixes = model
      ? fixesOf(model).filter((fix): fix is Exclude<FixKind, 'faces'> => fix !== 'faces')
      : [];
    const fix =
      stage.fix ??
      (index === primary
        ? chip && fixes.includes(chip)
          ? chip
          : undefined
        : chip && (!model || fixes.includes(chip))
          ? chip
          : fixes[0]);
    if (fix && !result.some((selection) => selection.fix === fix))
      result.push({ fix, model_id: stage.model_id });
  });
  return result;
}

export function sameFixStages(left: FixStageSelection[], right: FixStageSelection[]): boolean {
  return (
    left.length === right.length &&
    left.every(
      (selection, index) =>
        selection.fix === right[index].fix && selection.model_id === right[index].model_id,
    )
  );
}

/** A saved recipe is selected while the current settings match what it applies. */
export function recipeMatches(
  recipe: Recipe,
  current: UiSettings,
  catalog: AppSnapshot['catalog'],
): boolean {
  if (current.task !== recipe.task) return false;
  if (
    recipe.task === 'video' &&
    (recipe.video_model_id || 'frame_by_frame') !== current.selected_video_model_id
  )
    return false;
  const recipeModel = catalog.models.find((model) => model.model_id === recipe.model_id);
  const scale =
    recipe.task === 'denoise'
      ? 1
      : Math.min(recipeModel?.native_scale ?? recipe.output_scale, recipe.output_scale);
  const fixes = recipeFixStages(recipe, catalog);
  const face =
    Boolean(recipe.stages?.some((stage) => stage.kind === 'face_restore')) ||
    (recipe.enable_face_model ?? false);
  return (
    current.selected_model_id === recipe.model_id &&
    current.output_scale === scale &&
    current.precision === recipe.precision &&
    current.tile_size === recipe.tile_size &&
    current.halo === recipe.halo &&
    current.safe_memory === recipe.safe_memory &&
    sameFixStages(current.fix_stages, fixes) &&
    current.enable_face_model === face &&
    (current.quality || 'custom') === (recipe.quality || 'custom') &&
    (recipe.task !== 'video' ||
      ((current.video_interpolation || 'off') === (recipe.video_interpolation || 'off') &&
        (current.video_interpolation_order || 'after_upscale') ===
          (recipe.video_interpolation_order || 'after_upscale')))
  );
}
