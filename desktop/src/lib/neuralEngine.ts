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
// Catalog architectures an Intel NPU runs, mirroring NPU_ARCHITECTURES in
// src/localsr/core/onnx_runtime.py (tests/test_onnx_runtime.py checks it).
const NPU_ARCHITECTURES = new Set(['SPAN', 'RealESRGAN', 'FBCNN', 'NAFNet']);

/** Whether a model runs on an Intel NPU; a custom model (no architecture) may. */
function npuRuns(architecture?: string): boolean {
  return architecture === undefined || NPU_ARCHITECTURES.has(architecture);
}

function isNpu(capabilities: CapabilityInfo, deviceId: string): boolean {
  return capabilities.devices.some((device) => device.id === deviceId && device.type === 'npu');
}

/** The accelerator a model can use on this computer, for the model library. */
export function acceleratorFor(
  capabilities: CapabilityInfo,
  architecture?: string,
): 'Neural Engine' | 'NPU' | undefined {
  if (!architecture) return undefined;
  if (neuralEngineAvailable(capabilities) && NEURAL_ENGINE_ARCHITECTURES.has(architecture))
    return 'Neural Engine';
  if (
    capabilities.devices.some((device) => device.type === 'npu') &&
    NPU_ARCHITECTURES.has(architecture)
  )
    return 'NPU';
  return undefined;
}

/** Whether a model runs on the Neural Engine; a custom model (no architecture) may. */
export function neuralEngineRuns(architecture?: string): boolean {
  return architecture === undefined || NEURAL_ENGINE_ARCHITECTURES.has(architecture);
}

export function neuralEngineAvailable(capabilities: CapabilityInfo): boolean {
  return capabilities.devices.some((device) => device.id === 'mps' && device.neural_engine);
}

/** Hardware entries for image and frame-by-frame video jobs. The Neural Engine is listed
 *  only for a model it runs; for the others the Apple GPU is the Apple entry. */
export function hardwareChoices(capabilities: CapabilityInfo, architecture?: string): DeviceInfo[] {
  // An NPU is listed only for a model it runs; the engine runs the others on the CPU.
  const devices = npuRuns(architecture)
    ? capabilities.devices
    : capabilities.devices.filter((device) => device.type !== 'npu');
  const mps = devices.find((device) => device.id === 'mps');
  if (!mps?.neural_engine || !neuralEngineRuns(architecture)) return devices;
  return devices.flatMap((device) =>
    device === mps ? [{ ...mps, id: NEURAL_ENGINE_ID, name: 'Neural Engine' }, mps] : [device],
  );
}

/**
 * The settings the window shows and sends. A saved device the engine does not list (an
 * unplugged GPU) reads as the CPU, which the host also starts with; the saved value stays
 * in the snapshot, so the choice returns with the device.
 */
export function withListedDevice<T extends Pick<UiSettings, 'device_id'>>(
  settings: T,
  capabilities: CapabilityInfo,
): T {
  const { devices } = capabilities;
  if (!devices.length || devices.some((device) => device.id === settings.device_id))
    return settings;
  return {
    ...settings,
    device_id: devices.some((device) => device.id === 'cpu') ? 'cpu' : devices[0].id,
  };
}

export function selectedHardware(
  settings: Pick<UiSettings, 'device_id' | 'neural_engine'>,
  capabilities: CapabilityInfo,
  architecture?: string,
): string {
  if (isNpu(capabilities, settings.device_id) && !npuRuns(architecture)) return 'cpu';
  return settings.device_id === 'mps' &&
    settings.neural_engine !== false &&
    neuralEngineAvailable(capabilities) &&
    neuralEngineRuns(architecture)
    ? NEURAL_ENGINE_ID
    : settings.device_id;
}

export function hardwarePatch(value: string, architecture?: string): Partial<UiSettings> {
  if (value === NEURAL_ENGINE_ID) return { device_id: 'mps', neural_engine: true };
  // For a model the Neural Engine cannot run, the Apple GPU is the only Apple entry:
  // choosing it keeps the preference for the models that can.
  if (value === 'mps') {
    return neuralEngineRuns(architecture)
      ? { device_id: 'mps', neural_engine: false }
      : { device_id: 'mps' };
  }
  return { device_id: value };
}

/** What the status bar names before a job: the unit the selected model will use. */
export function engineLabel(
  settings: Pick<UiSettings, 'device_id' | 'neural_engine'>,
  capabilities: CapabilityInfo,
  architecture: string | undefined,
  fallback: string,
): string {
  if (isNpu(capabilities, settings.device_id) && !npuRuns(architecture))
    return 'CPU · this model does not run on the NPU';
  if (selectedHardware(settings, capabilities) !== NEURAL_ENGINE_ID) return fallback;
  if (!architecture) return 'Neural Engine where supported';
  if (NEURAL_ENGINE_ARCHITECTURES.has(architecture)) return 'Neural Engine';
  if (CORE_ML_GPU_ARCHITECTURES.has(architecture)) return 'Apple GPU (Core ML)';
  return 'Apple GPU (Metal) · this model is unsupported on the Neural Engine';
}
