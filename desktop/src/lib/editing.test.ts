import { describe, expect, it } from 'vitest';
import rawModels from '../../../src/localsr/core/edit_catalog.json';
import { demoSnapshot } from './demo';
import {
  EDIT_GIB,
  editDevice,
  editDimensions,
  editFitFor,
  editHardwareLabel,
  editSizeLimit,
  recommendAnyEditModel,
  recommendEditModel,
} from './editing';
import type { DeviceInfo, EditModel } from './types';

const models = rawModels as unknown as EditModel[];
const gpu = (id: string, capacity: number): DeviceInfo => ({
  id,
  type: id === 'mps' ? 'mps' : 'cuda',
  name: 'Fixture GPU',
  total_memory: capacity * EDIT_GIB - 32 * 1024 ** 2,
  free_memory: (capacity - 1) * EDIT_GIB,
  supports_fp16: true,
  is_integrated: id === 'mps',
  recommended_tile_sizes: [256],
});

describe('Qwen memory recommendations', () => {
  it.each([
    [24, 'Q3_K_S'],
    [26, 'Q3_K_S'],
    [32, 'Q5_K_M'],
    [36, 'Q6_K'],
    [48, 'Q8_0'],
    [64, 'Q8_0'],
  ])('recommends %s GiB Mac profile without allocating memory', (capacity, quantization) => {
    const capabilities = demoSnapshot().capabilities;
    capabilities.system_ram_total = Number(capacity) * EDIT_GIB;
    expect(
      recommendEditModel(models, 'qwen-image-edit-2511', capabilities, gpu('mps', Number(capacity)))
        ?.quantization,
    ).toBe(quantization);
  });
  it.each([
    [8, 'Q2_K'],
    [12, 'Q3_K_S'],
    [16, 'Q4_K_M'],
    [20, 'Q6_K'],
    [24, 'Q8_0'],
    [32, 'Q8_0'],
  ])('recommends %s GiB GPU profile despite driver overhead', (capacity, quantization) => {
    const capabilities = demoSnapshot().capabilities;
    expect(
      recommendEditModel(
        models,
        'qwen-image-edit-2511',
        capabilities,
        gpu('cuda:0', Number(capacity)),
      )?.quantization,
    ).toBe(quantization);
  });
  it('caps the edit resolution on smaller devices', () => {
    const capabilities = demoSnapshot().capabilities;
    capabilities.system_ram_total = 24 * EDIT_GIB;
    expect(editSizeLimit(capabilities, gpu('mps', 24))).toBe(512);
    expect(editSizeLimit(capabilities, gpu('cuda:0', 12))).toBe(768);
    expect(editSizeLimit(capabilities, gpu('cuda:0', 16))).toBe(1024);
  });
});

describe('Edit helpers', () => {
  it('judges the fit by unified memory on a Mac and by card memory elsewhere', () => {
    const capabilities = demoSnapshot().capabilities;
    capabilities.system_ram_total = 16 * EDIT_GIB;
    const q2 = models.find((model) => model.model_id === 'qwen_edit_2511_q2_k')!;
    expect(editFitFor(q2, capabilities, gpu('mps', 16))).toBe('too_heavy');
    expect(editFitFor(q2, capabilities, gpu('cuda:0', 8))).toBe('runs');
    expect(editFitFor(q2, capabilities, undefined)).toBe('unknown');
    expect(editHardwareLabel(capabilities, gpu('mps', 16))).toBe(
      'this Mac has 16 GB of unified memory',
    );
    expect(editHardwareLabel(capabilities, undefined)).toContain('No Apple Silicon');
  });

  it('edits on the chosen hardware only when it can edit, never on the CPU', () => {
    const capabilities = demoSnapshot().capabilities;
    capabilities.devices = [gpu('cpu', 16), gpu('mps', 24)];
    expect(editDevice(capabilities, { device_id: 'cpu' })?.id).toBe('mps');
    expect(editDevice(capabilities, { device_id: 'mps' })?.id).toBe('mps');
    capabilities.devices = [gpu('cpu', 16)];
    expect(editDevice(capabilities, { device_id: 'cpu' })).toBeUndefined();
  });

  it('prefers the Qwen editor, falls back to FLUX.2 klein, then to the smallest bundle', () => {
    const capabilities = demoSnapshot().capabilities;
    const pick = (gb: number): string | undefined => {
      capabilities.system_ram_total = gb * EDIT_GIB;
      return recommendAnyEditModel(models, capabilities, gpu('mps', gb))?.model_id;
    };
    expect(pick(36)).toBe('qwen_edit_2511_q6_k');
    expect(pick(24)).toBe('qwen_edit_2511_q3_k_s');
    expect(pick(16)).toBe('flux2_klein_4b_q8_0');
    expect(pick(8)).toBe('flux2_klein_4b_q4_0');
  });

  it('fits the edit inside the limit on a 32-pixel grid, like the worker', () => {
    expect(editDimensions(4000, 3000, 512)).toEqual([512, 384]);
    expect(editDimensions(300, 200, 512)).toEqual([288, 192]);
    expect(editDimensions(0, 0, 512)).toEqual([32, 32]);
  });
});
