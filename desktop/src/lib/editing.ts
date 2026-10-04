import type { Fit } from './state';
import type { CapabilityInfo, DeviceInfo, EditModel, UiSettings } from './types';

export const EDIT_GIB = 1024 ** 3;

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
];

export const EDIT_FIT_LABELS: Record<Fit, string> = {
  runs: 'Fits this computer',
  heavy: 'Fits this computer',
  too_heavy: 'Too heavy for this computer',
  unknown: '',
};

export function editFamilyLabel(family: string): string {
  return EDIT_FAMILIES.find((candidate) => candidate.id === family)?.label ?? family;
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

/** The largest bundle of a family that fits, or its smallest one so a plan always exists. */
export function recommendEditModel(
  models: EditModel[],
  family: string,
  capabilities: CapabilityInfo,
  device?: DeviceInfo,
): EditModel | undefined {
  return (
    models
      .filter((model) => model.family === family && editModelFits(model, capabilities, device))
      .at(-1) ?? models.find((model) => model.family === family)
  );
}

/** The first preferred family with a fitting bundle, its largest; otherwise the smallest bundle of all. */
export function recommendAnyEditModel(
  models: EditModel[],
  capabilities: CapabilityInfo,
  device?: DeviceInfo,
): EditModel | undefined {
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

/** The longest output edge this computer starts with; larger edits are explicit choices elsewhere. */
export function editSizeLimit(capabilities: CapabilityInfo, device?: DeviceInfo): number {
  const capacity = editCapacity(capabilities, device);
  const thresholds = device?.id === 'mps' ? [36, 48] : [12, 16];
  return capacity < thresholds[0] * EDIT_GIB
    ? 512
    : capacity < thresholds[1] * EDIT_GIB
      ? 768
      : 1024;
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
