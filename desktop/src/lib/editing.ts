import type { Fit } from './plan';
import { detectPlatform } from './platform';
import type { CapabilityInfo, DeviceInfo, EditModel, EditPlacement, UiSettings } from './types';

export const EDIT_GIB = 1024 ** 3;
const EDIT_MIB = 1024 ** 2;

/** The model families in the editing catalog, in the order they are shown and preferred. */
export const EDIT_FAMILIES: { id: string; label: string; summary: string }[] = [
  {
    id: 'qwen-image-edit-2511',
    label: 'Qwen Image Edit 2511',
    summary: 'Edits a photo from a written instruction · Apache-2.0',
  },
  {
    id: 'flux2-klein-4b',
    label: 'FLUX.2 klein 4B',
    summary: 'Fast 4-step editor for 16 GB computers · Apache-2.0',
  },
  {
    id: 'mage-flow-edit-turbo',
    label: 'Mage-Flow-Edit-Turbo',
    // Mage-Flow runs on the CPU only on a Mac; elsewhere it uses the GPU like the others.
    summary: `4-step editor${detectPlatform() === 'macos' ? ' (CPU only)' : ''} · MIT`,
  },
];

export interface CustomEditFamilySpec {
  id: string;
  label: string;
  summary: string;
  needsVision: boolean;
  defaultSteps: number;
  cfgScale: number;
}

/** Families the bundled stable-diffusion.cpp revision supports for local custom files. */
export const CUSTOM_EDIT_FAMILIES: CustomEditFamilySpec[] = [
  {
    id: 'qwen-image-edit-2511',
    label: 'Qwen Image Edit 2511',
    summary: 'Edits a photo from a written instruction',
    needsVision: true,
    defaultSteps: 40,
    cfgScale: 2.5,
  },
  {
    id: 'qwen-image-2.1',
    label: 'Qwen Image 2.1',
    summary: 'Edits a photo with Qwen Image 2.1',
    needsVision: true,
    defaultSteps: 40,
    cfgScale: 6.0,
  },
  {
    id: 'flux2-klein',
    label: 'FLUX.2 klein',
    summary: 'Fast 4-step editor',
    needsVision: false,
    defaultSteps: 4,
    cfgScale: 1.0,
  },
  {
    id: 'mage-flow-edit-turbo',
    label: 'Mage-Flow-Edit-Turbo',
    summary: '4-step editor',
    needsVision: true,
    defaultSteps: 4,
    cfgScale: 1.0,
  },
];

export function customEditFamilySpec(family: string): CustomEditFamilySpec {
  return CUSTOM_EDIT_FAMILIES.find((item) => item.id === family) ?? CUSTOM_EDIT_FAMILIES[0];
}

export const EDIT_FIT_LABELS: Record<Fit, string> = {
  runs: 'Fits this computer',
  heavy: 'Fits this computer',
  too_heavy: 'Too heavy for this computer',
  unknown: '',
};

/**
 * A plain name for a quantization. People choose between sizes, not GGUF
 * formats; the technical name stays in the library's details.
 */
export function editVariantName(model: Pick<EditModel, 'quantization'>): string {
  const bits = Number(/^Q(\d)/i.exec(model.quantization)?.[1] ?? 0);
  if (bits >= 8) return 'Full detail';
  if (bits >= 5) return 'High detail';
  if (bits === 4) return 'Balanced';
  return bits ? 'Compact' : model.quantization;
}

export function editFamilyLabel(family: string): string {
  return (
    EDIT_FAMILIES.find((candidate) => candidate.id === family)?.label ??
    CUSTOM_EDIT_FAMILIES.find((candidate) => candidate.id === family)?.label ??
    family
  );
}

export function editFamilySummary(family: string): string {
  return (
    EDIT_FAMILIES.find((candidate) => candidate.id === family)?.summary.split(' · ')[0] ??
    CUSTOM_EDIT_FAMILIES.find((candidate) => candidate.id === family)?.summary ??
    ''
  );
}

/** Editing runs natively on Apple Silicon (Metal) or a CUDA/ROCm card; never on the CPU. */
export function editingDevices(capabilities: CapabilityInfo): DeviceInfo[] {
  return capabilities.devices.filter(
    (device) => device.id === 'mps' || /^cuda(?::\d+)?$/.test(device.id),
  );
}

/** The GPU an edit uses: the chosen hardware when it can edit, otherwise the first that can. */
export function editDevice(
  capabilities: CapabilityInfo,
  settings: Pick<UiSettings, 'device_id'>,
): DeviceInfo | undefined {
  const devices = editingDevices(capabilities);
  return devices.find((device) => device.id === settings.device_id) ?? devices[0];
}

/**
 * The memory that decides which bundle fits. A Mac's GPU shares the system
 * memory, so the whole bundle has to fit in unified memory. A dedicated card
 * holds the diffusion model while the text encoder runs from system RAM;
 * drivers report a little under the nominal size, so a quarter GiB is added.
 */
export function editCapacity(capabilities: CapabilityInfo, device?: DeviceInfo): number {
  if (!device) return 0;
  return device.id === 'mps' ? capabilities.system_ram_total : device.total_memory + EDIT_GIB / 4;
}

export function editRequirement(model: EditModel, device?: DeviceInfo): number {
  return (device?.id === 'mps' ? model.min_unified_memory_gb : model.min_vram_gb) * EDIT_GIB;
}

export function editModelFits(
  model: EditModel,
  capabilities: CapabilityInfo,
  device?: DeviceInfo,
): boolean {
  return Boolean(device) && editCapacity(capabilities, device) >= editRequirement(model, device);
}

/** Catalog starting profile against this computer; the worker re-checks live memory before loading. */
export function editFitFor(
  model: EditModel,
  capabilities: CapabilityInfo,
  device?: DeviceInfo,
): Fit {
  if (!device || !editCapacity(capabilities, device)) return 'unknown';
  return editModelFits(model, capabilities, device) ? 'runs' : 'too_heavy';
}

/** Share of a Mac's unified memory the default editing model plans to use. */
export const MAC_DEFAULT_EDIT_SHARE = 0.35;

/**
 * Resident memory at the heaviest phase of a 512 px edit on unified memory,
 * as the worker estimates it (edit_memory.estimate_edit_memory): the encoders,
 * or the transformer with the VAE and 1.5 GiB of working buffers, plus 10%.
 */
export function editPeakEstimate(model: EditModel): number {
  const size = (role: string): number =>
    model.files
      .filter((file) => file.role === role)
      .reduce((total, file) => total + file.size_bytes, 0);
  const conditioning = size('text_encoder') + size('vision');
  const sampling = size('vae') + 1.5 * EDIT_GIB + size('diffusion');
  return Math.max(conditioning, sampling) * 1.1;
}

/**
 * The bundle a computer starts with. On a Mac: the preferred family's largest
 * bundle that stays within 35% of unified memory, leaving the rest to macOS and
 * open apps (16 GB → FLUX.2 klein Q4_0, 24–32 GB → FLUX.2 klein Q8_0, 36 GB →
 * Qwen Q3_K_S, 48 GB → Q4_K_M, 64 GB → Q6_K, 96 GB → Q8_0). Qwen at 2 bits is
 * left to an explicit choice: FLUX.2 klein Q8_0 is the faster default there.
 * A dedicated GPU, and a Mac where nothing fits the share, take the preferred
 * family's largest bundle that fits; otherwise the smallest bundle of all.
 */
export function recommendAnyEditModel(
  models: EditModel[],
  capabilities: CapabilityInfo,
  device?: DeviceInfo,
): EditModel | undefined {
  if (device?.id === 'mps') {
    const budget = capabilities.system_ram_total * MAC_DEFAULT_EDIT_SHARE;
    for (const family of EDIT_FAMILIES) {
      const comfortable = models
        .filter(
          (model) =>
            model.family === family.id &&
            model.quantization !== 'Q2_K' &&
            editModelFits(model, capabilities, device) &&
            editPeakEstimate(model) <= budget,
        )
        .at(-1);
      if (comfortable) return comfortable;
    }
  }
  for (const family of EDIT_FAMILIES) {
    const fit = models
      .filter((model) => model.family === family.id && editModelFits(model, capabilities, device))
      .at(-1);
    if (fit) return fit;
  }
  const size = (model: EditModel): number =>
    model.total_size_bytes || model.files.reduce((total, file) => total + file.size_bytes, 0);
  return [...models].sort((left, right) => size(left) - size(right))[0];
}

export interface EditExpertState {
  textEncoder: EditPlacement;
  vae: EditPlacement;
  vramLimitMb: number;
  allowAnySize: boolean;
}

export interface EditVramOption {
  value: number;
  label: string;
}

const DEFAULT_EXPERT: EditExpertState = {
  textEncoder: 'auto',
  vae: 'auto',
  vramLimitMb: 0,
  allowAnySize: false,
};

function validPlacement(value: string | undefined): EditPlacement {
  return value === 'gpu' || value === 'cpu' ? value : 'auto';
}

/** GPU memory choices for a discrete card: Automatic plus whole-GB bounds from 4 GB up. */
export function editVramOptions(device?: DeviceInfo): EditVramOption[] {
  const options: EditVramOption[] = [{ value: 0, label: 'Automatic' }];
  if (!device || device.id === 'mps') return options;
  const maxGb = Math.floor((device.total_memory + EDIT_GIB / 4) / EDIT_GIB);
  const totalMb = Math.floor(device.total_memory / EDIT_MIB);
  for (let gb = 4; gb <= maxGb; gb++) {
    options.push({ value: Math.min(gb * 1024, totalMb), label: `Up to ${gb} GB` });
  }
  return options;
}

/** The four expert values that apply to this engine, device and model. */
export function editExpert(
  settings: Pick<
    UiSettings,
    'edit_text_encoder' | 'edit_vae' | 'edit_vram_limit_mb' | 'edit_allow_any_size'
  >,
  model: EditModel | undefined,
  device: DeviceInfo | undefined,
  features: readonly string[],
): EditExpertState {
  if (!device || !features.includes('edit_placement')) return { ...DEFAULT_EXPERT };
  const isMac = device.id === 'mps';
  const mageFlowOnMac = isMac && model?.family === 'mage-flow-edit-turbo';
  const textEncoder = mageFlowOnMac ? 'auto' : validPlacement(settings.edit_text_encoder);
  const vae = mageFlowOnMac ? 'auto' : validPlacement(settings.edit_vae);
  if (!features.includes('edit_expert') || isMac) {
    return { textEncoder, vae, vramLimitMb: 0, allowAnySize: false };
  }
  const savedLimit = settings.edit_vram_limit_mb ?? 0;
  let vramLimitMb = 0;
  if (savedLimit > 0) {
    const choices = editVramOptions(device)
      .map((option) => option.value)
      .filter((value) => value > 0);
    if (choices.length) {
      const notAbove = choices.filter((value) => value <= savedLimit);
      vramLimitMb = notAbove.length ? notAbove[notAbove.length - 1] : choices[0];
    }
  }
  return {
    textEncoder,
    vae,
    vramLimitMb,
    allowAnySize: Boolean(settings.edit_allow_any_size),
  };
}

/** Human-readable phrases for each active expert override, in display order. */
export function editExpertChanges(expert: EditExpertState): string[] {
  const changes: string[] = [];
  if (expert.textEncoder !== 'auto') {
    changes.push(`Text encoder on the ${expert.textEncoder === 'cpu' ? 'CPU' : 'GPU'}`);
  }
  if (expert.vae !== 'auto') {
    changes.push(`VAE on the ${expert.vae === 'cpu' ? 'CPU' : 'GPU'}`);
  }
  if (expert.vramLimitMb > 0) {
    changes.push(`GPU memory up to ${Math.round(expert.vramLimitMb / 1024)} GB`);
  }
  if (expert.allowAnySize) {
    changes.push('Any edit size');
  }
  return changes;
}

/**
 * The longest edit edge recommended for this model, computer and expert settings,
 * matching the engine's size fit on discrete cards and Mac memory tiers on Apple Silicon.
 */
export function editRecommendedSize(
  model: EditModel | undefined,
  capabilities: CapabilityInfo,
  device: DeviceInfo | undefined,
  expert: EditExpertState,
): number {
  if (!device) return 512;
  if (device.id === 'mps') {
    return capabilities.system_ram_total < 36 * EDIT_GIB
      ? 512
      : capabilities.system_ram_total < 48 * EDIT_GIB
        ? 768
        : 1024;
  }
  if (!model) return 512;
  const total = device.total_memory;
  const free = device.free_memory || total;
  const reserve = Math.max(EDIT_GIB, Math.floor(total * 0.08));
  const budget =
    expert.vramLimitMb > 0
      ? expert.vramLimitMb * EDIT_MIB
      : Math.min(Math.floor(total * 0.9), free - reserve);
  const size = (role: string): number =>
    model.files
      .filter((file) => file.role === role)
      .reduce((sum, file) => sum + file.size_bytes, 0);
  const diffusion = size('diffusion');
  const vae = size('vae');
  const encoders = size('text_encoder') + size('vision');
  for (const candidate of [1024, 768] as const) {
    const working = EDIT_GIB * (candidate / 512) ** 2;
    if (diffusion + working > budget) continue;
    const vaeFits = diffusion + vae + working <= budget;
    if (expert.vae === 'gpu' && !vaeFits) continue;
    const vaeOnGpu = expert.vae === 'gpu' || (expert.vae === 'auto' && vaeFits);
    if (
      expert.textEncoder === 'gpu' &&
      diffusion + (vaeOnGpu ? vae : 0) + encoders + working > budget
    ) {
      continue;
    }
    return candidate;
  }
  return 512;
}

/** The edit size shown in the panel and sent to the engine. */
export function editSizeFor(
  savedSize: number,
  recommendedSize: number,
  expert: Pick<EditExpertState, 'allowAnySize'>,
): number {
  return expert.allowAnySize ? savedSize : Math.min(savedSize, recommendedSize);
}

/** Output size of an edit: fit inside the limit, aligned to 32 pixels like the worker does. */
export function editDimensions(width: number, height: number, limit: number): [number, number] {
  const ratio = Math.min(1, limit / Math.max(1, width, height));
  const align = (value: number): number => Math.max(32, Math.floor((value * ratio) / 32) * 32);
  return [align(width), align(height)];
}

/** What this computer offers, for the sentence beside a bundle's requirement. */
export function editHardwareLabel(capabilities: CapabilityInfo, device?: DeviceInfo): string {
  if (!device) return 'No Apple Silicon or CUDA/ROCm GPU was detected';
  const gb = Math.round(editCapacity(capabilities, device) / EDIT_GIB);
  return device.id === 'mps'
    ? `this Mac has ${gb} GB of unified memory`
    : `${device.name} has ${gb} GB`;
}

export function editRuntimeLabel(device?: DeviceInfo): string {
  return device?.id === 'mps' ? 'Metal' : 'CUDA, ROCm or Vulkan';
}
