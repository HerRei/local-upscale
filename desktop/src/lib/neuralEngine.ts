import type { CapabilityInfo, DeviceInfo, UiSettings } from './types';

/**
 * The Hardware entry for Apple's Neural Engine. The worker still runs on the
 * `mps` device: supported models go through Core ML, the rest stay on the GPU.
 */
export const NEURAL_ENGINE_ID = 'neural-engine';

// Catalog architectures that Core ML runs, mirroring COMPUTE_UNITS in
// src/localsr/core/coreml_runtime.py (tests/test_coreml_runtime.py checks it).
const NEURAL_ENGINE_ARCHITECTURES = new Set(['SPAN', 'RealESRGAN', 'FBCNN']);
const CORE_ML_GPU_ARCHITECTURES = new Set(['RealPLKSR']);

export function neuralEngineAvailable(capabilities: CapabilityInfo): boolean {
  return capabilities.devices.some((device) => device.id === 'mps' && device.neural_engine);
}

/** Hardware entries for image and frame-by-frame video jobs. */
export function hardwareChoices(capabilities: CapabilityInfo): DeviceInfo[] {
  const mps = capabilities.devices.find((device) => device.id === 'mps');
  if (!mps?.neural_engine) return capabilities.devices;
  return capabilities.devices.flatMap((device) =>
    device === mps ? [{ ...mps, id: NEURAL_ENGINE_ID, name: 'Neural Engine' }, mps] : [device],
  );
}

export function selectedHardware(
  settings: Pick<UiSettings, 'device_id' | 'neural_engine'>,
  capabilities: CapabilityInfo,
): string {
  return settings.device_id === 'mps' &&
    settings.neural_engine !== false &&
    neuralEngineAvailable(capabilities)
    ? NEURAL_ENGINE_ID
    : settings.device_id;
}

export function hardwarePatch(value: string): Partial<UiSettings> {
  if (value === NEURAL_ENGINE_ID) return { device_id: 'mps', neural_engine: true };
  if (value === 'mps') return { device_id: 'mps', neural_engine: false };
  return { device_id: value };
}

/** What the status bar names before a job: the unit the selected model will use. */
export function engineLabel(
  settings: Pick<UiSettings, 'device_id' | 'neural_engine'>,
  capabilities: CapabilityInfo,
  architecture: string | undefined,
  fallback: string,
): string {
  if (selectedHardware(settings, capabilities) !== NEURAL_ENGINE_ID) return fallback;
  if (!architecture) return 'Neural Engine where supported';
  if (NEURAL_ENGINE_ARCHITECTURES.has(architecture)) return 'Neural Engine';
  if (CORE_ML_GPU_ARCHITECTURES.has(architecture)) return 'Apple GPU (Core ML)';
  return 'Apple GPU (Metal) · this model is unsupported on the Neural Engine';
}
