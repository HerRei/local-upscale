import { withListedDevice } from './neuralEngine';
import { effectiveVideoTask, isRestoreTask } from './plan';
import { recipeMatches } from './recipes';
import type {
  CapabilityInfo,
  CatalogManifest,
  FixStageSelection,
  Recipe,
  StartBatchInput,
  TaskKind,
  UiSettings,
} from './types';

export interface StartInputFlags {
  videoTask: 'upscale' | 'denoise';
  restoring: boolean;
  usingTemporalVideo: boolean;
  imageFixes: FixStageSelection[];
  noRestoreModel: boolean;
  audioDisabled: boolean;
}

/** Pure derivation of the start flags that App.svelte reacts on before queuing a batch. */
export function deriveStartFlags(settings: UiSettings, hasAudio = true): StartInputFlags {
  const videoTask = effectiveVideoTask(settings);
  const restoring = isRestoreTask(settings);
  const usingTemporalVideo =
    settings.task === 'video' &&
    !restoring &&
    settings.selected_video_model_id !== 'frame_by_frame';
  const imageFixes =
    !usingTemporalVideo && settings.task !== 'edit' && Boolean(settings.task)
      ? settings.fix_stages
      : [];
  const noRestoreModel =
    restoring && imageFixes.length === 0 && settings.selected_model_id !== '__custom__';
  const audioDisabled = settings.task === 'video' && !hasAudio;
  return {
    videoTask,
    restoring,
    usingTemporalVideo,
    imageFixes,
    noRestoreModel,
    audioDisabled,
  };
}

export interface BuildStartInputOptions {
  mediaIds: string[];
  task: TaskKind | '';
  batchMode: boolean;
  hasAudio?: boolean;
  recipeName?: string;
  recipes?: Recipe[];
  catalog?: CatalogManifest;
  capabilities?: CapabilityInfo;
}

/** Pure builder for the `StartBatchInput` payload sent to `start_jobs`. */
export function buildStartInput(
  rawSettings: UiSettings,
  options: BuildStartInputOptions,
): StartBatchInput {
  const settings = options.capabilities
    ? withListedDevice(rawSettings, options.capabilities)
    : rawSettings;
  const { videoTask, usingTemporalVideo, imageFixes, noRestoreModel, audioDisabled } =
    deriveStartFlags(settings, options.hasAudio !== false);
  const recipeName =
    options.recipeName ??
    (options.recipes && options.catalog
      ? (options.recipes.find((recipe) => recipeMatches(recipe, settings, options.catalog!))
          ?.name ?? '')
      : '');
  return {
    media_ids: options.mediaIds,
    // What the plan card showed, kept with the job for the result history.
    ui: {
      quality: settings.quality,
      content: settings.content,
      recipe_name: recipeName,
      fixes: [...settings.fixes],
    },
    batch_mode: options.batchMode,
    task: options.task as TaskKind,
    video_task: videoTask,
    model_id: noRestoreModel ? '' : settings.selected_model_id,
    video_model_id: settings.selected_video_model_id,
    fix_stages: usingTemporalVideo ? [] : imageFixes,
    custom_model_path: settings.custom_model_path,
    output_directory: settings.output_directory,
    output_format: settings.output_format,
    output_scale: settings.output_scale,
    preserve_metadata: settings.preserve_metadata,
    jpeg_quality: settings.jpeg_quality,
    device: settings.device_id,
    tile_size: settings.tile_size,
    halo: settings.halo,
    precision: settings.precision,
    safe_memory: settings.safe_memory,
    neural_engine: settings.neural_engine ?? true,
    deflicker: usingTemporalVideo ? false : settings.deflicker,
    deflicker_window: settings.deflicker_window,
    video_container: settings.video_container,
    video_codec: settings.video_codec ?? 'auto',
    external_ffmpeg_path: settings.external_ffmpeg_path ?? '',
    video_hdr_mode: usingTemporalVideo ? 'tone_map' : (settings.video_hdr_mode ?? 'tone_map'),
    video_target_resolution: settings.video_target_resolution ?? 0,
    video_low_memory: settings.video_low_memory ?? true,
    video_crf: settings.video_crf,
    enable_face_model: false,
    face_fidelity: settings.face_fidelity,
    enable_live_preview: settings.enable_live_preview,
    allow_unsafe_pickle_model: settings.allow_unsafe_pickle_model,
    // Frame interpolation is a frame-by-frame video stage.
    video_interpolation:
      settings.task === 'video' && !usingTemporalVideo
        ? (settings.video_interpolation ?? 'off')
        : 'off',
    video_interpolation_order: settings.video_interpolation_order ?? 'after_upscale',
    video_clean_audio:
      settings.task === 'video' && !audioDisabled && Boolean(settings.video_clean_audio),
    video_audio_model: settings.video_audio_model ?? '',
  };
}
