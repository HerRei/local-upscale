import { describe, expect, it } from 'vitest';
import {
  NEURAL_ENGINE_ID,
  acceleratorFor,
  engineLabel,
  fitLine,
  hardwareChoices,
  hardwarePatch,
  runsOnAccelerator,
  selectedHardware,
  withListedDevice,
} from './neuralEngine';
import type { CapabilityInfo, DeviceInfo } from './types';

function device(id: string, extra: Partial<DeviceInfo> = {}): DeviceInfo {
  return {
    id,
    type: id,
    name: id === 'mps' ? 'Apple GPU (Metal)' : id.toUpperCase(),
    total_memory: 0,
    free_memory: 0,
    supports_fp16: true,
    is_integrated: true,
    recommended_tile_sizes: [256],
    ...extra,
  };
}

function capabilities(neuralEngine: boolean): CapabilityInfo {
  return {
    devices: [device('mps', { neural_engine: neuralEngine }), device('cpu')],
  } as CapabilityInfo;
}

describe('Neural Engine hardware entry', () => {
  it('is listed before the Apple GPU only where Core ML is available', () => {
    expect(hardwareChoices(capabilities(true)).map((entry) => entry.id)).toEqual([
      NEURAL_ENGINE_ID,
      'mps',
      'cpu',
    ]);
    expect(hardwareChoices(capabilities(false)).map((entry) => entry.id)).toEqual(['mps', 'cpu']);
  });

  it('is the selection for the Apple GPU unless the user chose the GPU', () => {
    expect(selectedHardware({ device_id: 'mps' }, capabilities(true))).toBe(NEURAL_ENGINE_ID);
    expect(selectedHardware({ device_id: 'mps', neural_engine: false }, capabilities(true))).toBe(
      'mps',
    );
    expect(selectedHardware({ device_id: 'mps' }, capabilities(false))).toBe('mps');
    expect(selectedHardware({ device_id: 'cpu' }, capabilities(true))).toBe('cpu');
  });

  it('maps both Apple entries onto the mps device', () => {
    expect(hardwarePatch(NEURAL_ENGINE_ID)).toEqual({ device_id: 'mps', neural_engine: true });
    expect(hardwarePatch('mps')).toEqual({ device_id: 'mps', neural_engine: false });
    expect(hardwarePatch('cpu')).toEqual({ device_id: 'cpu' });
  });

  it('is not offered for a model it cannot run, and that keeps the preference', () => {
    const on = { device_id: 'mps', neural_engine: true };
    expect(hardwareChoices(capabilities(true), 'HAT').map((entry) => entry.id)).toEqual([
      'mps',
      'cpu',
    ]);
    expect(selectedHardware(on, capabilities(true), 'HAT')).toBe('mps');
    // Choosing the Apple GPU there must not switch the Neural Engine off for Quick.
    expect(hardwarePatch('mps', 'HAT')).toEqual({ device_id: 'mps' });
    expect(selectedHardware(on, capabilities(true), 'SPAN')).toBe(NEURAL_ENGINE_ID);
    expect(hardwareChoices(capabilities(true), 'SPAN')[0].id).toBe(NEURAL_ENGINE_ID);
  });

  it('names the unit the selected model will run on', () => {
    const on = { device_id: 'mps', neural_engine: true };
    const caps = capabilities(true);
    expect(engineLabel(on, caps, 'SPAN', 'Apple GPU (Metal)')).toBe('Neural Engine');
    expect(engineLabel(on, caps, 'RealPLKSR', 'Apple GPU (Metal)')).toBe('Apple GPU (Core ML)');
    expect(engineLabel(on, caps, 'HAT', 'Apple GPU (Metal)')).toBe(
      'Apple GPU (Metal) · this model is unsupported on the Neural Engine',
    );
    expect(engineLabel({ ...on, neural_engine: false }, caps, 'SPAN', 'Apple GPU (Metal)')).toBe(
      'Apple GPU (Metal)',
    );
  });
});

function npuMachine(): CapabilityInfo {
  return {
    devices: [
      device('openvino:gpu1', { type: 'openvino', name: 'Intel(R) Graphics (OpenVINO)' }),
      device('openvino:npu1', { type: 'npu', name: 'Intel(R) AI Boost (NPU)' }),
      device('cpu'),
    ],
  } as CapabilityInfo;
}

describe('Intel NPU hardware entry', () => {
  it('is offered only for models it runs', () => {
    expect(hardwareChoices(npuMachine(), 'SPAN').map((entry) => entry.id)).toEqual([
      'openvino:gpu1',
      'openvino:npu1',
      'cpu',
    ]);
    expect(hardwareChoices(npuMachine(), 'HAT').map((entry) => entry.id)).toEqual([
      'openvino:gpu1',
      'cpu',
    ]);
  });

  it('shows the CPU for a model it cannot run, which is where that model runs', () => {
    expect(selectedHardware({ device_id: 'openvino:npu1' }, npuMachine(), 'HAT')).toBe('cpu');
    expect(selectedHardware({ device_id: 'openvino:npu1' }, npuMachine(), 'SPAN')).toBe(
      'openvino:npu1',
    );
    expect(
      engineLabel({ device_id: 'openvino:npu1' }, npuMachine(), 'SPAN', 'Intel(R) AI Boost (NPU)'),
    ).toBe('Intel(R) AI Boost (NPU)');
    expect(
      engineLabel({ device_id: 'openvino:npu1' }, npuMachine(), 'HAT', 'Intel(R) AI Boost (NPU)'),
    ).toBe('CPU · this model does not run on the NPU');
  });

  it('names, for the model library, the accelerator a model can use on this computer', () => {
    expect(acceleratorFor(npuMachine(), 'SPAN')).toBe('NPU');
    expect(acceleratorFor(npuMachine(), 'RealESRGAN')).toBe('NPU');
    expect(acceleratorFor(npuMachine(), 'HAT')).toBeUndefined();
    expect(acceleratorFor(capabilities(true), 'FBCNN')).toBe('Neural Engine');
    expect(acceleratorFor(capabilities(false), 'SPAN')).toBeUndefined();
    expect(acceleratorFor(npuMachine(), undefined)).toBeUndefined();
  });
});

describe('withListedDevice', () => {
  const listed = (...ids: string[]) =>
    ({ devices: ids.map((id) => device(id)) }) as unknown as CapabilityInfo;

  it('keeps a device the engine lists, and any device before it has listed any', () => {
    const settings = { device_id: 'cuda:0' };
    expect(withListedDevice(settings, listed('cuda:0', 'cpu'))).toBe(settings);
    expect(withListedDevice(settings, listed())).toBe(settings);
  });

  it('reads an unlisted device as the CPU without changing the saved settings', () => {
    const settings = { device_id: 'cuda:7' };
    expect(withListedDevice(settings, listed('mps', 'cpu')).device_id).toBe('cpu');
    expect(settings.device_id).toBe('cuda:7');
  });

  it('falls back to the first listed device when the CPU is not listed', () => {
    expect(withListedDevice({ device_id: 'cuda:7' }, listed('mps')).device_id).toBe('mps');
  });
});

describe('library fit line', () => {
  const GIB = 1024 ** 3;
  const mac = {
    devices: [
      device('mps', { neural_engine: true, total_memory: 16 * GIB }),
      device('cpu', { name: 'CPU', total_memory: 16 * GIB }),
    ],
  } as CapabilityInfo;
  const pc = {
    devices: [
      device('openvino:1', { type: 'npu', name: 'Intel AI Boost (NPU)', total_memory: 16 * GIB }),
      device('cpu', { name: 'CPU', total_memory: 16 * GIB }),
    ],
  } as CapabilityInfo;
  const span = { architecture: 'SPAN', vram_estimate_mb: 1024 };
  const hat = { architecture: 'HAT', vram_estimate_mb: 1024 };
  const find = (capabilities: CapabilityInfo, id: string) =>
    capabilities.devices.find((entry) => entry.id === id);
  const onMps = { device_id: 'mps', neural_engine: true };

  it('names the Neural Engine when the model and the selected hardware use it', () => {
    expect(fitLine(span, onMps, mac, find(mac, 'mps'))?.text).toBe(
      'Runs well here · Neural Engine',
    );
    expect(runsOnAccelerator(onMps, mac, 'SPAN')).toBe('Neural Engine');
  });

  it('names the device when the user chose the Apple GPU or the CPU', () => {
    const gpu = { device_id: 'mps', neural_engine: false };
    expect(fitLine(span, gpu, mac, find(mac, 'mps'))?.text).toBe(
      'Runs well here · Apple GPU (Metal)',
    );
    const cpu = { device_id: 'cpu', neural_engine: true };
    expect(fitLine(span, cpu, mac, find(mac, 'cpu'))?.text).toBe('Runs well here · CPU');
    expect(runsOnAccelerator(cpu, mac, 'SPAN')).toBeUndefined();
  });

  it('names the device for a model the accelerator cannot run', () => {
    expect(fitLine(hat, onMps, mac, find(mac, 'mps'))?.text).toBe(
      'Runs well here · Apple GPU (Metal)',
    );
    expect(runsOnAccelerator(onMps, mac, 'HAT')).toBeUndefined();
  });

  it('names an NPU only while it is selected, and the CPU for a model it does not run', () => {
    const npu = { device_id: 'openvino:1', neural_engine: true };
    expect(fitLine(span, npu, pc, find(pc, 'openvino:1'))?.text).toBe('Runs well here · NPU');
    expect(fitLine(hat, npu, pc, find(pc, 'openvino:1'))?.text).toBe('Runs well here · CPU');
    const cpu = { device_id: 'cpu', neural_engine: true };
    expect(fitLine(span, cpu, pc, find(pc, 'cpu'))?.text).toBe('Runs well here · CPU');
  });

  it('keeps the memory wording for tight and too heavy models', () => {
    const tight = { architecture: 'SPAN', vram_estimate_mb: 12 * 1024 };
    const huge = { architecture: 'SPAN', vram_estimate_mb: 20 * 1024 };
    expect(fitLine(tight, onMps, mac, find(mac, 'mps'))).toEqual({
      fit: 'heavy',
      text: 'Heavy here · safe-memory mode · Neural Engine',
    });
    expect(fitLine(huge, onMps, mac, find(mac, 'mps'))).toEqual({
      fit: 'too_heavy',
      text: 'Too heavy for this device · Neural Engine',
    });
  });

  it('shows nothing when the memory is unknown', () => {
    expect(fitLine({ ...span, vram_estimate_mb: 0 }, onMps, mac, find(mac, 'mps'))).toBeUndefined();
  });
});
