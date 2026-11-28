import { strengthOf, withStrength } from './fixRows';
import { choosePresetModel, fixesOf } from './plan';
import { isFixStageKind, isVideoOrUpscalePrimaryKind } from './steps';
import type {
  AppSnapshot,
  Recipe,
  FixKind,
  FixStageSelection,
  TaskKind,
  UiSettings,
} from './types';

/** Build the UiSettings patch applied when activating a saved recipe. */
export function recipeSettingsPatch(
  recipe: Recipe,
  snapshot: Pick<AppSnapshot, 'catalog'>,
  current: UiSettings,
): Partial<UiSettings> {
  const fix_stages = recipeFixStages(recipe, snapshot.catalog);
  const recipeModel = snapshot.catalog.models.find((model) => model.model_id === recipe.model_id);
  const targetRestoring = isRestoreRecipe(recipe);
  const targetTask: TaskKind =
    recipe.task === 'upscale' && targetRestoring ? 'denoise' : recipe.task;
  const targetVideoTask =
    targetTask === 'video' ? (targetRestoring ? 'denoise' : 'upscale') : 'upscale';
  const migratedFromUpscale1x =
    targetRestoring &&
    recipe.task !== 'denoise' &&
    !(recipe.task === 'video' && recipe.video_task === 'denoise');
  const recipeQuality: 'quick' | 'best' =
    recipe.quality === 'quick' || recipe.quality === 'best' ? recipe.quality : 'best';
  const targetModelId = migratedFromUpscale1x
    ? (fix_stages.at(-1)?.model_id ??
      (targetTask === 'video'
        ? ''
        : recipeModel?.native_scale === 1
          ? recipe.model_id
          : (choosePresetModel(
              snapshot as AppSnapshot,
              'denoise',
              recipeQuality,
              recipe.content || current.content,
              current.preset_pins,
            )?.model_id ?? '')))
    : recipe.model_id;

  return {
    task: targetTask,
    ...(targetTask === 'video' ? { video_task: targetVideoTask } : {}),
    selected_model_id: targetModelId,
    selected_video_model_id: targetRestoring
      ? 'frame_by_frame'
      : recipe.video_model_id || 'frame_by_frame',
    fix_stages,
    custom_model_path: recipe.custom_model_path ?? '',
    output_scale: targetRestoring
      ? 1
      : Math.min(recipeModel?.native_scale ?? recipe.output_scale, recipe.output_scale),
    output_format: recipe.output_format ?? current.output_format,
    output_bit_depth: recipe.output_bit_depth ?? current.output_bit_depth,
    preserve_metadata: recipe.preserve_metadata ?? current.preserve_metadata,
    jpeg_quality: recipe.jpeg_quality ?? current.jpeg_quality,
    tile_size: recipe.tile_size,
    halo: recipe.halo,
    precision: recipe.precision,
    safe_memory: recipe.safe_memory,
    deflicker: recipe.deflicker ?? current.deflicker,
    deflicker_window: recipe.deflicker_window ?? current.deflicker_window,
    video_container: recipe.video_container || current.video_container,
    video_codec: recipe.video_codec || current.video_codec || 'auto',
    external_ffmpeg_path: current.external_ffmpeg_path ?? '',
    video_hdr_mode: recipe.video_hdr_mode ?? 'tone_map',
    video_target_resolution: recipe.video_target_resolution ?? 0,
    video_low_memory: recipe.video_low_memory ?? true,
    video_crf: recipe.video_crf ?? current.video_crf,
    // Recipes saved before 0.3.7 carry no interpolation, so they run without it.
    video_interpolation: recipe.video_interpolation || 'off',
    video_interpolation_order: recipe.video_interpolation_order || 'after_upscale',
    video_slow_motion: recipe.video_slow_motion ?? false,
    video_clean_audio: recipe.video_clean_audio ?? false,
    video_audio_model: recipe.video_audio_model ?? '',
    enable_face_model: false,
    face_fidelity: recipe.face_fidelity ?? current.face_fidelity,
    allow_unsafe_pickle_model: false,
    quality: recipe.quality || 'custom',
    content: recipe.content || current.content,
    fixes: fix_stages.map((selection) => selection.fix),
  };
}

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
    : stages.findIndex((stage) => isVideoOrUpscalePrimaryKind(stage.kind));
  const result: FixStageSelection[] = [];
  stages.forEach((stage, index) => {
    if (!isFixStageKind(stage.kind)) return;
    if (index > primary) return;
    const model = catalog.models.find((entry) => entry.model_id === stage.model_id);
    const fixes = model
      ? fixesOf(model).filter((fix): fix is Exclude<FixKind, 'faces'> => fix !== 'faces')
      : [];
    const fix = stage.fix ?? (chip && (!model || fixes.includes(chip)) ? chip : fixes[0]);
    if (fix && !result.some((selection) => selection.fix === fix)) {
      const entry: FixStageSelection = { fix, model_id: stage.model_id };
      const strength = stage.params?.strength;
      result.push(
        typeof strength === 'number' && strength >= 0 && strength < 1
          ? withStrength(entry, strength)
          : entry,
      );
    }
  });
  return result;
}

export function sameFixStages(left: FixStageSelection[], right: FixStageSelection[]): boolean {
  return (
    left.length === right.length &&
    left.every(
      (selection, index) =>
        selection.fix === right[index].fix &&
        selection.model_id === right[index].model_id &&
        strengthOf(selection) === strengthOf(right[index]),
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
    // Older recipes did not record the output; they match whatever it is.
    (!recipe.output_format || current.output_format === recipe.output_format) &&
    (recipe.output_bit_depth === undefined ||
      (current.output_bit_depth ?? 8) === recipe.output_bit_depth) &&
    sameFixStages(current.fix_stages, fixes) &&
    !current.enable_face_model &&
    (current.quality || 'custom') === (recipe.quality || 'custom') &&
    (effectiveTask !== 'video' ||
      ((current.video_interpolation || 'off') === (recipe.video_interpolation || 'off') &&
        (current.video_interpolation_order || 'after_upscale') ===
          (recipe.video_interpolation_order || 'after_upscale') &&
        Boolean(current.video_slow_motion) === Boolean(recipe.video_slow_motion) &&
        Boolean(current.video_clean_audio) === Boolean(recipe.video_clean_audio) &&
        (!recipe.video_clean_audio ||
          (current.video_audio_model || defaultAudioId) ===
            (recipe.video_audio_model || defaultAudioId))))
  );
}
