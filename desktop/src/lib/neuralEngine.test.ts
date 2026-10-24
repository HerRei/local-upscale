import { describe, expect, it } from 'vitest';
import {
  NEURAL_ENGINE_ID,
  engineLabel,
  hardwareChoices,
  hardwarePatch,
  selectedHardware,
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
