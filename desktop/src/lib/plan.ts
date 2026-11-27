import { formatBytes } from './format';
import {
  FIX_ROW_LABELS,
  chooseFixModel,
  fixQuality,
  fixStageKind,
  fitsFix,
  rememberedFixModel,
} from './fixStages';
import { FIX_LABELS, STEPS, stepStage, type StepDefinition, type StepPlacement } from './steps';
import type {
  AppSnapshot,
  CatalogModel,
  CatalogVideoModel,
  ContentKind,
  DeviceInfo,
  EditModel,
  FixKind,
  InterpolationTarget,
  Quality,
  StepStageSelection,
  TaskKind,
  UiSettings,
} from './types';
export { FIX_LABELS };

/** Catalog name without its role suffix, with `4x` written `×4`. */
export function displayName(model: { name: string; display_name?: string }): string {
  if (model.display_name) return model.display_name;
  return model.name
    .split(' — ')[0]
    .replace(' 4x ', ' ×4 ')
    .replace(' 2x ', ' ×2 ')
    .replace(' 1x ', ' ×1 ');
}

/** Problems a 1× checkpoint fixes, derived from purposes when the catalog omits it. */
export function fixesOf(model: CatalogModel): FixKind[] {
  if (model.fixes?.length) return model.fixes;
  if (model.native_scale !== 1 || model.purposes.includes('face')) return [];
  const found: FixKind[] = [];
  if (model.purposes.includes('denoise')) found.push('noise');
  if (model.purposes.includes('deblur')) found.push('blur');
  if (model.purposes.includes('colorize')) found.push('colour');
  if (model.purposes.includes('restoration') && !found.length) found.push('jpeg');
  return found;
}

/**
 * Mirrors `presets.py`: Quick and Best never pick a checkpoint whose rights are
 * unresolved or non-commercial. Labs is a separate axis (validation), so a Labs
 * model with verified rights stays eligible.
 */
export function rightsAllowPreset(model: CatalogModel): boolean {
  return model.commercial_use_allowed === true;
}

/** Mirrors `presets.py`: a slow specialist the library offers is never picked by a preset. */
export function presetMayPick(model: CatalogModel): boolean {
  return rightsAllowPreset(model) && model.preset_eligible !== false;
}

function contentMatch(model: CatalogModel, content: ContentKind): number {
  if (content === 'illustration') {
    if (model.purposes.includes('illustration') || model.purposes.includes('anime')) return 2;
    return model.purposes.includes('general') ? 1 : 0;
  }
  if (model.purposes.includes('photo')) return 2;
  return model.purposes.includes('general') ? 1 : 0;
}

export function rankForQuality(models: CatalogModel[], quality: 'quick' | 'best'): CatalogModel[] {
  return [...models].sort((left, right) => {
    if (quality === 'quick') {
      return (
        right.speed_tier - left.speed_tier ||
        right.speed_factor - left.speed_factor ||
        left.memory_factor - right.memory_factor ||
        left.size_bytes - right.size_bytes ||
        left.model_id.localeCompare(right.model_id)
      );
    }
    return (
      right.quality_tier - left.quality_tier ||
      right.size_bytes - left.size_bytes ||
      right.speed_tier - left.speed_tier ||
      left.model_id.localeCompare(right.model_id)
    );
  });
}

export function presetSlot(task: TaskKind, content: ContentKind, quality: Quality): string {
  return `${task}/${content}/${quality}`;
}

export function effectiveVideoTask(
  settings: Pick<UiSettings, 'video_task'>,
): 'upscale' | 'denoise' {
  return settings.video_task === 'denoise' ? 'denoise' : 'upscale';
}

export function isRestoreTask(settings: Pick<UiSettings, 'task' | 'video_task'>): boolean {
  return (
    settings.task === 'denoise' || (settings.task === 'video' && settings.video_task === 'denoise')
  );
}

/** Upscale always starts at 2×; kept for caller compatibility. */
export function skipsUpscaleModel(
  _settings: Pick<
    UiSettings,
    'task' | 'video_task' | 'output_scale' | 'selected_video_model_id' | 'video_target_resolution'
  >,
): boolean {
  return false;
}
export type Fit = 'runs' | 'heavy' | 'too_heavy' | 'unknown';

/**
 * Hardware fit from the catalog's memory estimate against the device's total
 * memory: below two-thirds runs well, above it needs safe-memory mode, above
 * the total it will not fit. Shared Apple memory includes the OS, so this is
 * deliberately conservative.
 */
export function fitFor(model: { vram_estimate_mb: number }, device?: DeviceInfo): Fit {
  if (!device || !device.total_memory || !model.vram_estimate_mb) return 'unknown';
  const needed = model.vram_estimate_mb * 1024 * 1024;
  if (needed > device.total_memory) return 'too_heavy';
  if (needed > (device.total_memory * 2) / 3) return 'heavy';
  return 'runs';
}

export const FIT_LABELS: Record<Fit, string> = {
  runs: 'Runs well here',
  heavy: 'Heavy here · safe-memory mode',
  too_heavy: 'Too heavy for this device',
  unknown: '',
};

export interface PlanStage {
  kind:
    | 'deblock'
    | 'restore'
    | 'upscale'
    | 'face_restore'
    | 'video'
    | 'edit'
    | 'deflicker'
    | 'interpolate'
    | 'audio_denoise'
    | 'grain';
  /**
   * What the row's Change opens: the fix before the upscaler, the main model, or Faces.
   * Frame interpolation and audio cleanup are set directly and have no Change.
   */
  role: 'fix' | 'primary' | 'face' | 'step' | 'interpolate' | 'audio_denoise';
  /** The problem this row's model fixes, when it fixes one. */
  fix?: Exclude<FixKind, 'faces'>;
  /** The model is the user's remembered choice for that problem and quality. */
  remembered?: boolean;
  /** A fix no catalog model resolves to: the row stays and Start waits for a choice. */
  missing?: boolean;
  label: string;
  model?: CatalogModel;
  videoModel?: CatalogVideoModel;
  interpolationModel?: CatalogVideoModel;
  audioModel?: CatalogVideoModel;
  editModel?: EditModel;
  custom?: string;
  installed: boolean;
  /** Missing, and the catalog allows LocalSR to fetch it. */
  canDownload: boolean;
  /** Missing, and only a user-supplied copy is accepted. */
  needsFile: boolean;
  sizeBytes: number;
}

/** The media a task's step stages run on: photos, or frame-by-frame video frames. */
function stepStageMedia(task: UiSettings['task'], usingTemporalVideo: boolean): string | null {
  if (task === 'upscale' || task === 'denoise') return 'image';
  if (task === 'video' && !usingTemporalVideo) return 'video';
  return null;
}

/**
 * The step stages that run for these settings, in their order, with where each runs.
 * Steps this version does not know and steps for another media are left out.
 */
export function activeStepStages(
  settings: Pick<UiSettings, 'task' | 'step_stages'>,
  usingTemporalVideo: boolean,
  steps: readonly StepDefinition[] = STEPS,
): { selection: StepStageSelection; step: StepDefinition; placement: StepPlacement }[] {
  const media = stepStageMedia(settings.task, usingTemporalVideo);
  if (!media) return [];
  return (settings.step_stages ?? []).flatMap((selection) => {
    const found = stepStage(selection.step, steps);
    return found && found.step.media.includes(media) ? [{ selection, ...found }] : [];
  });
}

/** Whether a catalog model is one of the models a step stage takes (its `model_task`). */
export function servesStep(model: CatalogModel, step: StepDefinition): boolean {
  return step.model_task !== null && model.purposes.includes(step.model_task);
}

/** The catalog model a step stage runs: the chosen one, when it serves the step's task. */
export function stepStageModel(
  models: CatalogModel[],
  step: StepDefinition,
  selection: StepStageSelection,
): CatalogModel | undefined {
  if (!selection.model_id) return undefined;
  return models.find((model) => model.model_id === selection.model_id && servesStep(model, step));
}

/** Whether every step row of a plan is ready: a step with a model waits for it as a fix does. */
export function stepStagesReady(stages: readonly PlanStage[]): boolean {
  return stages.every((stage) => stage.role !== 'step' || stage.installed);
}

/** What the output line adds when a step's transparent background forces PNG. */
export const ALPHA_FORMAT_NOTE = 'Saved as PNG to keep the transparent background.';

/**
 * The format a job saves in: one that keeps the alpha a step sets (PNG, TIFF, WebP), or
 * PNG in place of any other.
 */
export function outputFormatKeepingAlpha(
  stepStages: readonly StepStageSelection[],
  format: UiSettings['output_format'],
  steps: readonly StepDefinition[] = STEPS,
): UiSettings['output_format'] {
  const setsAlpha = stepStages.some(
    (selection) => stepStage(selection.step, steps)?.step.output_alpha === true,
  );
  return setsAlpha && !['png', 'tif', 'webp'].includes(format) ? 'png' : format;
}

/**
 * The stages the current settings will run, in order. This reads what is
 * selected (it never re-resolves), so the plan card always tells the truth
 * about the next Start.
 */
export function resolvePlan(
  snapshot: AppSnapshot,
  settings: UiSettings,
  options: {
    faceModel?: CatalogModel;
    faceEnabled: boolean;
    usingTemporalVideo: boolean;
    selectedVideoModel?: CatalogVideoModel;
    editModel?: EditModel;
    hasAudio?: boolean;
    /** The step registry; tests add steps that exist only in them. */
    steps?: readonly StepDefinition[];
  },
): PlanStage[] {
  const stages: PlanStage[] = [];
  if (settings.task === 'edit') {
    // One native Qwen pass; the bundle is a set of files that download together.
    const model = options.editModel;
    if (model) {
      stages.push({
        kind: 'edit',
        role: 'primary',
        label: 'Edit',
        editModel: model,
        installed: model.installed,
        canDownload: !model.installed && model.automated_download_allowed,
        needsFile: !model.installed && !model.automated_download_allowed,
        sizeBytes: model.installed ? 0 : model.total_size_bytes,
      });
    }
    return stages;
  }
  const byId = (id: string): CatalogModel | undefined =>
    snapshot.catalog.models.find((model) => model.model_id === id);
  const remembered = (model: CatalogModel, fix?: Exclude<FixKind, 'faces'>): boolean =>
    Boolean(fix) &&
    rememberedFixModel(snapshot, fix!, fixQuality(settings.quality), settings.preset_pins)
      ?.model_id === model.model_id;
  const stageFor = (
    kind: PlanStage['kind'],
    role: PlanStage['role'],
    label: string,
    model: CatalogModel | undefined,
    fix?: Exclude<FixKind, 'faces'>,
  ): PlanStage | undefined =>
    model && {
      kind,
      role,
      fix,
      remembered: remembered(model, fix),
      label,
      model,
      installed: model.installed,
      canDownload: !model.installed && model.automated_download_allowed,
      needsFile: !model.installed && !model.automated_download_allowed,
      sizeBytes: model.installed ? 0 : model.size_bytes,
    };

  const missingStage = (
    kind: PlanStage['kind'],
    role: PlanStage['role'],
    label: string,
    fix?: Exclude<FixKind, 'faces'>,
  ): PlanStage => ({
    kind,
    role,
    fix,
    label,
    missing: true,
    installed: false,
    canDownload: false,
    needsFile: false,
    sizeBytes: 0,
  });
  const stepStageRow = (step: StepDefinition, selection: StepStageSelection): PlanStage => {
    const kind = step.id as PlanStage['kind'];
    if (step.needs_model) {
      // Like a fix: the chosen model's row, or a row that waits for one.
      const model = stepStageModel(snapshot.catalog.models, step, selection);
      return stageFor(kind, 'step', step.label, model) ?? missingStage(kind, 'step', step.label);
    }
    return {
      kind,
      role: 'step',
      label: step.label,
      installed: true,
      canDownload: false,
      needsFile: false,
      sizeBytes: 0,
    };
  };

  const stepStages = activeStepStages(settings, options.usingTemporalVideo, options.steps);
  for (const { step, selection, placement } of stepStages) {
    if (placement === 'before_fixes') stages.push(stepStageRow(step, selection));
  }
  const restoring = isRestoreTask(settings);
  const fixStages =
    !options.usingTemporalVideo &&
    (settings.task === 'upscale' || settings.task === 'video' || restoring)
      ? settings.fix_stages
      : [];
  // Same size's model is its last fix; the ones before it run first, as before an upscale.
  const chip = restoring ? fixStages.at(-1)?.fix : undefined;
  const noRestoreModel = restoring && !chip && settings.selected_model_id !== '__custom__';
  for (const selection of restoring ? fixStages.slice(0, -1) : fixStages) {
    const found = byId(selection.model_id);
    const model = found && fitsFix(found, selection.fix) ? found : undefined;
    const kind = fixStageKind(model, selection.fix);
    const label = FIX_ROW_LABELS[selection.fix];
    stages.push(
      stageFor(kind, 'fix', label, model, selection.fix) ??
        missingStage(kind, 'fix', label, selection.fix),
    );
  }

  if (noRestoreModel) {
    // Same size with no chip selected runs only video options (De-flicker, Frame rate, Clean up audio).
  } else if (options.usingTemporalVideo && options.selectedVideoModel) {
    const video = options.selectedVideoModel;
    stages.push({
      kind: 'video',
      role: 'primary',
      label: settings.video_target_resolution ? 'Upscale' : `Upscale ×${settings.output_scale}`,
      videoModel: video,
      installed: video.installed,
      canDownload: !video.installed && video.automated_download_allowed,
      needsFile: !video.installed && !video.automated_download_allowed,
      sizeBytes: video.installed ? 0 : video.total_size_bytes,
    });
  } else if (settings.selected_model_id === '__custom__') {
    stages.push({
      kind: restoring ? 'restore' : 'upscale',
      role: 'primary',
      label: restoring ? 'Fix' : `Upscale ×${settings.output_scale}`,
      custom: settings.custom_model_path,
      installed: Boolean(settings.custom_model_path),
      canDownload: false,
      needsFile: !settings.custom_model_path,
      sizeBytes: 0,
    });
  } else {
    const primary = byId(settings.selected_model_id);
    const restoreLabel = chip
      ? FIX_ROW_LABELS[chip]
      : primary?.purposes.includes('colorize')
        ? FIX_ROW_LABELS.colour
        : 'Fix';
    const stage = stageFor(
      restoring ? 'restore' : 'upscale',
      'primary',
      restoring ? restoreLabel : `Upscale ×${settings.output_scale}`,
      primary,
      restoring && primary && chip && fixesOf(primary).includes(chip) ? chip : undefined,
    );
    if (stage) stages.push(stage);
    else if (chip) stages.push(missingStage('restore', 'primary', FIX_ROW_LABELS[chip], chip));
  }

  if (options.faceEnabled && options.faceModel && !restoring && !noRestoreModel) {
    const stage = stageFor('face_restore', 'face', 'Faces', options.faceModel);
    if (stage) stages.push(stage);
  }

  if (settings.task === 'video' && !options.usingTemporalVideo && settings.deflicker) {
    stages.push({
      kind: 'deflicker',
      role: 'step',
      label: 'De-flicker',
      installed: true,
      canDownload: false,
      needsFile: false,
      sizeBytes: 0,
    });
  }

  // Frame interpolation is a frame-by-frame video stage; the plan lists it where it runs.
  const target = settings.video_interpolation ?? 'off';
  const interpolation = snapshot.catalog.interpolation_models?.[0];
  if (
    settings.task === 'video' &&
    !options.usingTemporalVideo &&
    target !== 'off' &&
    interpolation
  ) {
    const stage: PlanStage = {
      kind: 'interpolate',
      role: 'interpolate',
      label: interpolationLabel(target),
      interpolationModel: interpolation,
      installed: interpolation.installed,
      canDownload: !interpolation.installed && interpolation.automated_download_allowed,
      needsFile: !interpolation.installed && !interpolation.automated_download_allowed,
      sizeBytes: interpolation.installed ? 0 : interpolation.total_size_bytes,
    };
    if (settings.video_interpolation_order === 'before_upscale') stages.unshift(stage);
    else stages.push(stage);
  }

  for (const { step, selection, placement } of stepStages) {
    if (placement === 'after_primary') stages.push(stepStageRow(step, selection));
  }

  // Audio cleanup runs before video processing when the source has an audio track.
  const audioModel = resolveAudioModel(snapshot.catalog.audio_models, settings.video_audio_model);
  if (
    settings.task === 'video' &&
    settings.video_clean_audio &&
    options.hasAudio !== false &&
    audioModel
  ) {
    stages.unshift({
      kind: 'audio_denoise',
      role: 'audio_denoise',
      label: 'Clean up audio',
      audioModel,
      installed: audioModel.installed,
      canDownload: !audioModel.installed && audioModel.automated_download_allowed,
      needsFile: !audioModel.installed && !audioModel.automated_download_allowed,
      sizeBytes: audioModel.installed ? 0 : audioModel.total_size_bytes,
    });
  }
  return stages;
}

/** Selected audio cleanup model, falling back to the catalog default for empty or unknown ids. */
export function resolveAudioModel(
  audioModels: CatalogVideoModel[] | undefined,
  modelId?: string,
): CatalogVideoModel | undefined {
  if (!audioModels?.length) return undefined;
  return (
    (modelId ? audioModels.find((model) => model.model_id === modelId) : undefined) ??
    audioModels[0]
  );
}

const INTERPOLATION_RATES: Record<Exclude<InterpolationTarget, 'off'>, [number, number]> = {
  x2: [2, 0],
  x4: [4, 0],
  fps60: [0, 60],
  fps120: [0, 120],
};

export function resultFps(target: Exclude<InterpolationTarget, 'off'>, fps: number): number | null {
  if (!(fps > 0)) return null;
  const [multiplier, rate] = INTERPOLATION_RATES[target];
  return multiplier ? fps * multiplier : fps >= rate * 0.99 ? fps : rate;
}

/** The plan row's name for an interpolation target. */
export function interpolationLabel(target: Exclude<InterpolationTarget, 'off'>): string {
  const [multiplier, rate] = INTERPOLATION_RATES[target];
  return multiplier ? `Interpolate ${multiplier}×` : `Interpolate to ${rate} fps`;
}

/**
 * The frame rate a target gives a source, as the worker computes it. A fixed rate
 * at or below the source rate leaves the video unchanged.
 */
export function frameRateChange(target: Exclude<InterpolationTarget, 'off'>, fps: number): string {
  if (!(fps > 0)) return '';
  const [multiplier, rate] = INTERPOLATION_RATES[target];
  const format = (value: number) => String(Number(value.toFixed(3)));
  if (!multiplier && fps >= rate * 0.99) return `Already ${format(fps)} fps · kept as it is`;
  return `${format(fps)} → ${format(multiplier ? fps * multiplier : rate)} fps`;
}

export interface PendingDownloads {
  stages: PlanStage[];
  /** Bytes still to fetch for all of them. */
  bytes: number;
  /** Bytes still to fetch per model id: its size less a part kept from an earlier try. */
  left: Record<string, number>;
}

/**
 * Stages LocalSR can fetch itself before the job, and what is left to fetch for them.
 * `kept` holds the bytes an interrupted or cancelled download left behind per model id;
 * the next download continues from there.
 */
export function pendingDownloads(
  stages: PlanStage[],
  kept: Record<string, number> = {},
): PendingDownloads {
  const left: Record<string, number> = {};
  const pending = stages.filter((stage) => {
    const id = (
      stage.model ??
      stage.videoModel ??
      stage.interpolationModel ??
      stage.audioModel ??
      stage.editModel
    )?.model_id;
    if (!stage.canDownload || !id || id in left) return false;
    left[id] = Math.max(0, stage.sizeBytes - Math.max(0, kept[id] ?? 0));
    return true;
  });
  const bytes = Object.values(left).reduce((total, size) => total + size, 0);
  return { stages: pending, bytes, left };
}

/**
 * `kept` once a download of `id` has ended with `downloaded` bytes on disk. A completed
 * download sends none and leaves nothing to subtract.
 */
export function withKeptBytes(
  kept: Record<string, number>,
  id: string,
  downloaded: unknown,
): Record<string, number> {
  const bytes = Number(downloaded);
  const next = { ...kept };
  if (Number.isFinite(bytes) && bytes > 0) next[id] = bytes;
  else delete next[id];
  return next;
}

export function modelsForTask(
  snapshot: AppSnapshot,
  task: TaskKind | '',
  videoTask: 'upscale' | 'denoise' = 'upscale',
): CatalogModel[] {
  if (task === 'denoise' || (task === 'video' && videoTask === 'denoise')) {
    return snapshot.catalog.models.filter(
      (model) =>
        model.native_scale === 1 &&
        !model.purposes.includes('face') &&
        !model.purposes.includes('background'),
    );
  }
  if (task === 'upscale' || task === 'video') {
    return snapshot.catalog.models.filter(
      (model) =>
        model.native_scale > 1 &&
        !model.purposes.includes('face') &&
        !model.purposes.includes('background'),
    );
  }
  return [];
}

/** Models shown in the library's initial "For this job" group. */
export function libraryModelsForTask(
  models: CatalogModel[],
  task: TaskKind | '',
  content: ContentKind,
  fix?: Exclude<FixKind, 'faces'>,
  videoTask: 'upscale' | 'denoise' = 'upscale',
): CatalogModel[] {
  // A fix stage always chooses among 1× restoration models, whatever the task around it.
  if (task === 'denoise' || (task === 'video' && videoTask === 'denoise') || fix)
    return models.filter(
      (model) =>
        model.native_scale === 1 &&
        !model.purposes.includes('face') &&
        !model.purposes.includes('background') &&
        (!fix || fixesOf(model).includes(fix)),
    );
  if (task !== 'upscale' && task !== 'video') return [];
  return models
    .filter((model) => {
      if (
        model.native_scale <= 1 ||
        model.purposes.includes('face') ||
        model.purposes.includes('background')
      )
        return false;
      return content === 'illustration'
        ? model.purposes.some((purpose) => ['illustration', 'anime', 'general'].includes(purpose))
        : model.purposes.some((purpose) => ['photo', 'general'].includes(purpose));
    })
    .sort((left, right) => {
      const matches = (model: CatalogModel) =>
        content === 'illustration'
          ? model.purposes.some((purpose) => ['illustration', 'anime'].includes(purpose))
            ? 2
            : 1
          : model.purposes.includes('photo')
            ? 2
            : 1;
      return (
        matches(right) - matches(left) ||
        right.quality_tier - left.quality_tier ||
        right.speed_tier - left.speed_tier
      );
    });
}

/** Count compatible choices beyond the installed current selection. */
export function modelsAvailableBeyondSelection(models: CatalogModel[], selectedId: string): number {
  return models.filter((model) => !model.installed || model.model_id !== selectedId).length;
}

/**
 * The checkpoint Quick or Best resolves to for a task and content type.
 *
 * A pin for the slot wins when it names a compatible model. Otherwise models
 * must allow a preset, and the Quick/Best ordering mirrors `presets.py`. For the Restore
 * task the fix chip decides the model instead (see `chooseFixModel`); here it
 * falls back to the first eligible 1× model so a plan always exists.
 */
export function choosePresetModel(
  snapshot: AppSnapshot,
  task: TaskKind,
  quality: 'quick' | 'best',
  content: ContentKind = 'photo',
  pins: Record<string, string> = {},
  videoTask: 'upscale' | 'denoise' = 'upscale',
): CatalogModel | undefined {
  const restoring = task === 'denoise' || (task === 'video' && videoTask === 'denoise');
  const candidates = modelsForTask(snapshot, task, videoTask);
  const pinned = restoring
    ? undefined
    : candidates.find((model) => model.model_id === pins[presetSlot(task, content, quality)]);
  if (pinned) return pinned;
  const eligible = candidates.filter(
    (model) =>
      presetMayPick(model) &&
      (restoring ? !model.purposes.includes('colorize') : contentMatch(model, content) > 0),
  );
  if (task === 'video' && videoTask === 'denoise') return undefined;
  if (restoring) return rankForQuality(eligible, quality)[0];
  const ranked = rankForQuality(eligible, quality);
  return ranked.sort(
    (left, right) => contentMatch(right, content) - contentMatch(left, content),
  )[0];
}

/** Download size for a preset, counting each model once. */
export function presetDownload(
  quality: 'quick' | 'best',
  current: AppSnapshot,
  ui: UiSettings,
): string {
  const task = ui.task;
  if (!task || task === 'edit') return '';
  const restoring = isRestoreTask(ui);
  const temporal =
    task === 'video' && !restoring && ui.selected_video_model_id !== 'frame_by_frame';
  const fixes = (
    !temporal && (task === 'upscale' || task === 'video' || restoring) ? ui.fix_stages : []
  ).map((selection) => chooseFixModel(current, selection.fix, quality, ui.preset_pins));
  // At Same size no upscaler runs, so only the chosen fixes are counted.
  const primary =
    restoring || skipsUpscaleModel(ui)
      ? undefined
      : choosePresetModel(current, task, quality, ui.content, ui.preset_pins, ui.video_task);
  const models = new Map(
    [primary, ...fixes]
      .filter((model): model is CatalogModel => model !== undefined)
      .map((model) => [model.model_id, model]),
  );
  const bytes = [...models.values()].reduce(
    (total, model) => total + (model.installed ? 0 : model.size_bytes),
    0,
  );
  return bytes ? `${formatBytes(bytes)} download` : '';
}

export function supportsHdrPreservation(
  candidate: UiSettings,
  catalog: AppSnapshot['catalog'],
): boolean {
  if (candidate.video_task === 'denoise' || candidate.fix_stages.length > 0) return false;
  if (skipsUpscaleModel(candidate)) return false;
  return (
    candidate.selected_video_model_id === 'frame_by_frame' &&
    (candidate.selected_model_id === '__custom__' ||
      catalog.models.some(
        (model) => model.model_id === candidate.selected_model_id && model.architecture === 'HAT',
      ))
  );
}
