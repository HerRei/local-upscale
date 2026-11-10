import { formatBytes } from './format';
import {
  FIX_ROW_LABELS,
  chooseFixModel,
  fixQuality,
  fixStageKind,
  fitsFix,
  rememberedFixModel,
} from './fixStages';
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
  TaskKind,
  UiSettings,
} from './types';
export const FIX_LABELS: Record<FixKind, string> = {
  noise: 'Noise',
  jpeg: 'JPEG artifacts',
  blur: 'Blur',
  faces: 'Faces',
};

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
  kind: 'deblock' | 'restore' | 'upscale' | 'face_restore' | 'video' | 'edit' | 'interpolate';
  /**
   * What the row's Change opens: the fix before the upscaler, the main model, or Faces.
   * Frame interpolation is set under Advanced and has no Change.
   */
  role: 'fix' | 'primary' | 'face' | 'interpolate';
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
  editModel?: EditModel;
  custom?: string;
  installed: boolean;
  /** Missing, and the catalog allows LocalSR to fetch it. */
  canDownload: boolean;
  /** Missing, and only a user-supplied copy is accepted. */
  needsFile: boolean;
  sizeBytes: number;
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

  const restoring = settings.task === 'denoise';
  const fixStages = settings.task === 'upscale' || restoring ? settings.fix_stages : [];
  // Restore's model is its last fix; the ones before it run first, as before an upscale.
  const chip = restoring ? fixStages.at(-1)?.fix : undefined;
  const missingStage = (
    kind: PlanStage['kind'],
    role: PlanStage['role'],
    label: string,
    fix: Exclude<FixKind, 'faces'>,
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

  if (options.usingTemporalVideo && options.selectedVideoModel) {
    const video = options.selectedVideoModel;
    stages.push({
      kind: 'video',
      role: 'primary',
      label: 'Video',
      videoModel: video,
      installed: video.installed,
      canDownload: !video.installed && video.automated_download_allowed,
      needsFile: !video.installed && !video.automated_download_allowed,
      sizeBytes: video.installed ? 0 : video.total_size_bytes,
    });
  } else if (settings.selected_model_id === '__custom__') {
    stages.push({
      kind: settings.task === 'denoise' ? 'restore' : 'upscale',
      role: 'primary',
      label: settings.task === 'denoise' ? 'Restore' : `Upscale ×${settings.output_scale}`,
      custom: settings.custom_model_path,
      installed: Boolean(settings.custom_model_path),
      canDownload: false,
      needsFile: !settings.custom_model_path,
      sizeBytes: 0,
    });
  } else {
    const primary = byId(settings.selected_model_id);
    const stage = stageFor(
      restoring ? 'restore' : 'upscale',
      'primary',
      restoring ? 'Restore' : `Upscale ×${settings.output_scale}`,
      primary,
      restoring && primary && chip && fixesOf(primary).includes(chip) ? chip : undefined,
    );
    if (stage) stages.push(stage);
    else if (chip) stages.push(missingStage('restore', 'primary', 'Restore', chip));
  }

  if (options.faceEnabled && options.faceModel && settings.task !== 'denoise') {
    const stage = stageFor('face_restore', 'face', 'Faces', options.faceModel);
    if (stage) stages.push(stage);
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
  return stages;
}

const INTERPOLATION_RATES: Record<Exclude<InterpolationTarget, 'off'>, [number, number]> = {
  x2: [2, 0],
  x4: [4, 0],
  fps60: [0, 60],
  fps120: [0, 120],
};

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

/** Stages LocalSR can fetch itself before the job, and their total size. */
export function pendingDownloads(stages: PlanStage[]): { stages: PlanStage[]; bytes: number } {
  const seen = new Set<string>();
  const pending = stages.filter((stage) => {
    const id = (stage.model ?? stage.videoModel ?? stage.interpolationModel ?? stage.editModel)
      ?.model_id;
    if (!stage.canDownload || !id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  return { stages: pending, bytes: pending.reduce((total, stage) => total + stage.sizeBytes, 0) };
}

export function modelsForTask(snapshot: AppSnapshot, task: TaskKind | ''): CatalogModel[] {
  if (task === 'upscale') {
    return snapshot.catalog.models.filter(
      (model) => model.native_scale > 1 && !model.purposes.includes('face'),
    );
  }
  if (task === 'denoise') {
    return snapshot.catalog.models.filter(
      (model) => model.native_scale === 1 && !model.purposes.includes('face'),
    );
  }
  if (task === 'video') {
    return snapshot.catalog.models.filter(
      (model) => model.native_scale > 1 && !model.purposes.includes('face'),
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
): CatalogModel[] {
  // A fix stage always chooses among 1× restoration models, whatever the task around it.
  if (task === 'denoise' || fix)
    return models.filter(
      (model) =>
        model.native_scale === 1 &&
        !model.purposes.includes('face') &&
        (!fix || fixesOf(model).includes(fix)),
    );
  if (task !== 'upscale' && task !== 'video') return [];
  return models
    .filter((model) => {
      if (model.native_scale <= 1 || model.purposes.includes('face')) return false;
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
): CatalogModel | undefined {
  const candidates = modelsForTask(snapshot, task);
  const pinned = candidates.find(
    (model) => model.model_id === pins[presetSlot(task, content, quality)],
  );
  if (pinned) return pinned;
  const eligible = candidates.filter(
    (model) => presetMayPick(model) && (task === 'denoise' || contentMatch(model, content) > 0),
  );
  if (task === 'denoise') return rankForQuality(eligible, quality)[0];
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
  const fixes = (task === 'upscale' || task === 'denoise' ? ui.fix_stages : []).map((selection) =>
    chooseFixModel(current, selection.fix, quality, ui.preset_pins),
  );
  // Restore's model is its last fix; without one it is the preset's own choice.
  const primary =
    task === 'denoise'
      ? fixes.length
        ? undefined
        : choosePresetModel(current, task, quality)
      : choosePresetModel(current, task, quality, ui.content, ui.preset_pins);
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
  return (
    candidate.selected_video_model_id === 'frame_by_frame' &&
    (candidate.selected_model_id === '__custom__' ||
      catalog.models.some(
        (model) => model.model_id === candidate.selected_model_id && model.architecture === 'HAT',
      ))
  );
}
