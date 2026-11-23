import type {
  CapabilityInfo,
  CatalogManifest,
  CatalogVideoModel,
  EngineInfo,
  TaskKind,
} from './types';

const GIB = 1024 ** 3;

type VideoModel = Pick<CatalogVideoModel, 'model_id' | 'engine_kind' | 'min_unified_memory_gb'>;
type Engine = Pick<EngineInfo, 'video_engines'> | null | undefined;
type Capabilities = Pick<CapabilityInfo, 'devices' | 'system_ram_total'>;

/** Why a video model cannot run here. */
export interface VideoUnavailable {
  /** The full sentence, for the details pane and the warning when this model is chosen. */
  reason: string;
  /** The few words that mark the model in the library list. */
  tag: string;
}

/** An engine's name in running text; one this version does not know keeps its catalog id. */
export function videoEngineName(kind: string): string {
  return kind === 'seedvr2' ? 'SeedVR2' : kind === 'flashvsr' ? 'FlashVSR' : kind;
}

/** Catalog name before the tier suffix (` — Labs`). */
export function videoModelShortName(model: Pick<CatalogVideoModel, 'name'>): string {
  return model.name.split(' — ')[0];
}

/** Short row summary: the catalog summary when set, otherwise the first sentence of the description. */
export function videoModelSummary(
  model: Pick<CatalogVideoModel, 'summary' | 'description'>,
): string {
  const summary = model.summary?.trim();
  if (summary) return summary;
  const description = model.description.trim();
  const sentence = description.match(/^.+?[.!?](?=\s|$)/);
  return sentence ? sentence[0] : description;
}

/** Temporal video models offered alongside frame-by-frame models in video Upscale. */
export function videoModelsForJob(
  catalog: Pick<CatalogManifest, 'video_models'>,
  task: TaskKind | '',
  videoTask: 'upscale' | 'denoise' = 'upscale',
): CatalogVideoModel[] {
  return task === 'video' && videoTask !== 'denoise' ? catalog.video_models : [];
}

const unavailable = (reason: string, tag = 'Unavailable'): VideoUnavailable => ({ reason, tag });

/**
 * Why a catalog video model cannot run here, or undefined when it can. The worker has to list
 * the model's engine, and the chosen device has to suit that engine.
 */
export function videoModelUnavailable(
  model: VideoModel,
  engine: Engine,
  capabilities: Capabilities,
  deviceId: string,
): VideoUnavailable | undefined {
  const listed = engine?.video_engines.includes(model.engine_kind) === true;
  if (model.engine_kind === 'seedvr2') {
    if (!listed)
      return unavailable(
        'This installation supports frame-by-frame models. SeedVR2 requires a compatible CUDA, ROCm or Metal engine.',
      );
    return /^(directml|xpu|openvino)(:|$)/.test(deviceId)
      ? unavailable(
          'SeedVR2 cannot run on this device. Choose a frame-by-frame model or a compatible CUDA, ROCm or Metal device.',
        )
      : undefined;
  }
  if (model.engine_kind === 'flashvsr') {
    const needed = model.min_unified_memory_gb;
    if (!engine) return unavailable('FlashVSR is unavailable until the engine has started.');
    const appleGpu = capabilities.devices.some((device) => device.id === 'mps');
    if (!listed && !appleGpu)
      return unavailable(
        `FlashVSR needs an Apple silicon Mac with ${needed} GB of memory.`,
        'Apple silicon only',
      );
    // The Apple GPU's own total is the share PyTorch recommends, not what the Mac has: the
    // engine's limit is the Mac's unified memory. An unknown size is left to the engine.
    const installed = Math.round(capabilities.system_ram_total / GIB);
    if (installed > 0 && installed < needed)
      return unavailable(
        `FlashVSR needs ${needed} GB of memory. This Mac has ${installed} GB.`,
        `Needs ${needed} GB`,
      );
    if (!listed)
      return unavailable('FlashVSR needs the MLX runtime, which this installation does not have.');
    return deviceId === 'mps'
      ? undefined
      : unavailable(
          'FlashVSR cannot run on this device. Choose a frame-by-frame model or the Apple GPU.',
          'Apple GPU only',
        );
  }
  return listed
    ? undefined
    : unavailable(`This installation cannot run ${videoEngineName(model.engine_kind)}.`);
}

export function videoAvailability(
  models: VideoModel[],
  engine: Engine,
  capabilities: Capabilities,
  deviceId: string,
): Record<string, VideoUnavailable | undefined> {
  const result: Record<string, VideoUnavailable | undefined> = {};
  for (const model of models) {
    result[model.model_id] = videoModelUnavailable(model, engine, capabilities, deviceId);
  }
  return result;
}
