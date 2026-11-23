import type { CapabilityInfo, CatalogVideoModel, EngineInfo } from './types';

const GIB = 1024 ** 3;

type VideoModel = Pick<CatalogVideoModel, 'model_id' | 'engine_kind' | 'min_unified_memory_gb'>;
type Engine = Pick<EngineInfo, 'video_engines'> | null | undefined;
type Capabilities = Pick<CapabilityInfo, 'devices' | 'system_ram_total'>;

/** Why a video model cannot run here. */
export interface VideoUnavailable {
  /** The full sentence, for the line under the engine menu and the library. */
  reason: string;
  /** The few words that mark the model in the engine menu and the library list. */
  tag: string;
  /** The tag already says it: the sentence is shown only while this model is the chosen one. */
  quiet: boolean;
}

/** An engine's name in running text; one this version does not know keeps its catalog id. */
export function videoEngineName(kind: string): string {
  return kind === 'seedvr2' ? 'SeedVR2' : kind === 'flashvsr' ? 'FlashVSR' : kind;
}

const loud = (reason: string): VideoUnavailable => ({ reason, tag: 'Unavailable', quiet: false });
const quiet = (reason: string, tag = 'Unavailable'): VideoUnavailable => ({
  reason,
  tag,
  quiet: true,
});

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
      return loud(
        'This installation supports frame-by-frame video. SeedVR2 requires a compatible CUDA, ROCm or Metal engine.',
      );
    return /^(directml|xpu|openvino)(:|$)/.test(deviceId)
      ? loud(
          'SeedVR2 cannot run on this device. Choose frame-by-frame video or a compatible CUDA, ROCm or Metal device.',
        )
      : undefined;
  }
  if (model.engine_kind === 'flashvsr') {
    const needed = model.min_unified_memory_gb;
    if (!engine) return quiet('FlashVSR is unavailable until the engine has started.');
    const appleGpu = capabilities.devices.some((device) => device.id === 'mps');
    if (!listed && !appleGpu)
      return quiet(
        `FlashVSR needs an Apple silicon Mac with ${needed} GB of memory.`,
        'Apple silicon only',
      );
    // The Apple GPU's own total is the share PyTorch recommends, not what the Mac has: the
    // engine's limit is the Mac's unified memory. An unknown size is left to the engine.
    const installed = Math.round(capabilities.system_ram_total / GIB);
    if (installed > 0 && installed < needed)
      return quiet(
        `FlashVSR needs ${needed} GB of memory. This Mac has ${installed} GB.`,
        `Needs ${needed} GB`,
      );
    if (!listed)
      return loud('FlashVSR needs the MLX runtime, which this installation does not have.');
    return deviceId === 'mps'
      ? undefined
      : quiet(
          'FlashVSR cannot run on this device. Choose frame-by-frame video or the Apple GPU.',
          'Apple GPU only',
        );
  }
  return listed
    ? undefined
    : loud(`This installation cannot run ${videoEngineName(model.engine_kind)}.`);
}

export interface VideoAvailability {
  /** Per model id; a model that can run has no entry. */
  unavailable: Record<string, VideoUnavailable | undefined>;
  /** The sentences to show under the engine menu: each once, in catalog order. */
  notes: string[];
}

export function videoAvailability(
  models: VideoModel[],
  engine: Engine,
  capabilities: Capabilities,
  deviceId: string,
  /** The chosen engine; its reason is always shown, because it is what blocks Start. */
  selectedId = '',
): VideoAvailability {
  const unavailable: Record<string, VideoUnavailable | undefined> = {};
  const notes = new Set<string>();
  for (const model of models) {
    const state = videoModelUnavailable(model, engine, capabilities, deviceId);
    unavailable[model.model_id] = state;
    if (state && (!state.quiet || model.model_id === selectedId)) notes.add(state.reason);
  }
  return { unavailable, notes: [...notes] };
}
