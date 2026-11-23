import { fixesOf } from './plan';
import type { AppSnapshot, Recipe, FixKind, FixStageSelection, UiSettings } from './types';

/** True when a recipe runs Restore (including old non-Restore recipes saved at 1×). */
export function isRestoreRecipe(
  recipe: Pick<Recipe, 'task' | 'video_task' | 'output_scale'>,
): boolean {
  return (
    recipe.task === 'denoise' ||
    (recipe.task === 'video' && recipe.video_task === 'denoise') ||
    ((recipe.task === 'upscale' || recipe.task === 'video') && recipe.output_scale === 1)
  );
}

/** Resolve recipe fix stages in order, including recipes that name fixes only in chips. */
export function recipeFixStages(
  recipe: Recipe,
  catalog: AppSnapshot['catalog'],
): FixStageSelection[] {
  if (recipe.task !== 'upscale' && recipe.task !== 'denoise' && recipe.task !== 'video') return [];
  const stages = recipe.stages ?? [];
  const chip = recipe.fixes?.find((fix): fix is Exclude<FixKind, 'faces'> => fix !== 'faces');
  const restoring = isRestoreRecipe(recipe);
  const primary = restoring
    ? stages.length - 1
    : stages.findIndex((stage) => stage.kind === 'upscale' || stage.kind === 'video');
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
  const restoring = isRestoreRecipe(recipe);
  const effectiveTask = recipe.task === 'upscale' && restoring ? 'denoise' : recipe.task;
  if (current.task !== effectiveTask) return false;
  if (
    effectiveTask === 'video' &&
    ((restoring ? 'denoise' : recipe.video_task || 'upscale') !==
      (current.video_task || 'upscale') ||
      (restoring ? 'frame_by_frame' : recipe.video_model_id || 'frame_by_frame') !==
        current.selected_video_model_id)
  )
    return false;
  const recipeModel = catalog.models.find((model) => model.model_id === recipe.model_id);
  const scale = restoring
    ? 1
    : Math.min(recipeModel?.native_scale ?? recipe.output_scale, recipe.output_scale);
  const fixes = recipeFixStages(recipe, catalog);
  const migratedFromUpscale1x =
    restoring &&
    recipe.task !== 'denoise' &&
    !(recipe.task === 'video' && recipe.video_task === 'denoise');
  const expectedModelId = migratedFromUpscale1x
    ? (fixes.at(-1)?.model_id ??
      (effectiveTask === 'video'
        ? ''
        : recipeModel?.native_scale === 1
          ? recipe.model_id
          : current.selected_model_id))
    : recipe.model_id;
  const defaultAudioId = catalog.audio_models?.[0]?.model_id ?? '';
  return (
    current.selected_model_id === expectedModelId &&
    current.output_scale === scale &&
    current.precision === recipe.precision &&
    current.tile_size === recipe.tile_size &&
    current.halo === recipe.halo &&
    current.safe_memory === recipe.safe_memory &&
    sameFixStages(current.fix_stages, fixes) &&
    !current.enable_face_model &&
    (current.quality || 'custom') === (recipe.quality || 'custom') &&
    (effectiveTask !== 'video' ||
      ((current.video_interpolation || 'off') === (recipe.video_interpolation || 'off') &&
        (current.video_interpolation_order || 'after_upscale') ===
          (recipe.video_interpolation_order || 'after_upscale') &&
        Boolean(current.video_clean_audio) === Boolean(recipe.video_clean_audio) &&
        (!recipe.video_clean_audio ||
          (current.video_audio_model || defaultAudioId) ===
            (recipe.video_audio_model || defaultAudioId))))
  );
}
