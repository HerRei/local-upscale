import { describe, expect, it } from 'vitest';
import rawModels from '../../../src/localsr/core/edit_catalog.json';
import { demoSnapshot } from './demo';
import {
  CUSTOM_EDIT_FAMILIES,
  EDIT_GIB,
  customEditFamilySpec,
  editDevice,
  editDimensions,
  editFamilyLabel,
  editFitFor,
  editHardwareLabel,
  editPeakEstimate,
  editSizeLimit,
  recommendAnyEditModel,
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

  it.each([
    [8, 'flux2_klein_4b_q4_0'],
    [16, 'flux2_klein_4b_q4_0'],
    [24, 'flux2_klein_4b_q8_0'],
    [32, 'flux2_klein_4b_q8_0'],
    [36, 'qwen_edit_2511_q3_k_s'],
    [48, 'qwen_edit_2511_q4_k_m'],
    [64, 'qwen_edit_2511_q6_k'],
    [96, 'qwen_edit_2511_q8_0'],
  ])('starts a %s GB Mac with %s, leaving most memory to the system', (gb, modelId) => {
    const capabilities = demoSnapshot().capabilities;
    capabilities.system_ram_total = Number(gb) * EDIT_GIB;
    const model = recommendAnyEditModel(models, capabilities, gpu('mps', Number(gb)));
    expect(model?.model_id).toBe(modelId);
    if (Number(gb) >= 16)
      expect(editPeakEstimate(model!)).toBeLessThanOrEqual(Number(gb) * EDIT_GIB * 0.35);
  });

  it('keeps the largest fitting bundle on a dedicated GPU', () => {
    const capabilities = demoSnapshot().capabilities;
    expect(recommendAnyEditModel(models, capabilities, gpu('cuda:0', 24))?.model_id).toBe(
      'qwen_edit_2511_q8_0',
    );
  });

  it('estimates the measured FLUX.2 klein Q4_0 edit like the worker', () => {
    const q4 = models.find((model) => model.model_id === 'flux2_klein_4b_q4_0')!;
    expect(editPeakEstimate(q4) / EDIT_GIB).toBeCloseTo(4.51, 1);
  });

  it('fits the edit inside the limit on a 32-pixel grid, like the worker', () => {
    expect(editDimensions(4000, 3000, 512)).toEqual([512, 384]);
    expect(editDimensions(300, 200, 512)).toEqual([288, 192]);
    expect(editDimensions(0, 0, 512)).toEqual([32, 32]);
  });

  it('lists custom edit families with their file roles and default sampler settings', () => {
    expect(CUSTOM_EDIT_FAMILIES.map((item) => item.id)).toEqual([
      'qwen-image-edit-2511',
      'qwen-image-2.1',
      'flux2-klein',
      'mage-flow-edit-turbo',
    ]);
    expect(customEditFamilySpec('qwen-image-2.1')).toMatchObject({
      needsVision: true,
      defaultSteps: 40,
      cfgScale: 6,
    });
    expect(customEditFamilySpec('flux2-klein')).toMatchObject({
      needsVision: false,
      defaultSteps: 4,
      cfgScale: 1,
    });
    expect(customEditFamilySpec('mage-flow-edit-turbo')).toMatchObject({
      needsVision: true,
      defaultSteps: 4,
      cfgScale: 1,
    });
    expect(editFamilyLabel('qwen-image-2.1')).toBe('Qwen Image 2.1');
    expect(editFamilyLabel('flux2-klein')).toBe('FLUX.2 klein');
    expect(editFamilyLabel('mage-flow-edit-turbo')).toBe('Mage-Flow-Edit-Turbo');
  });

  it('keeps FLUX.2 klein as the Mac default while offering Mage-Flow-Edit-Turbo on 16 GB', () => {
    const capabilities = demoSnapshot().capabilities;
    capabilities.system_ram_total = 16 * EDIT_GIB;
    const mage = models.find((model) => model.model_id === 'mage_flow_edit_turbo_q6_k')!;
    expect(mage).toBeDefined();
    expect(editFitFor(mage, capabilities, gpu('mps', 16))).toBe('runs');
    expect(editFitFor(mage, capabilities, gpu('cuda:0', 8))).toBe('runs');
    expect(recommendAnyEditModel(models, capabilities, gpu('mps', 16))?.model_id).toBe(
      'flux2_klein_4b_q4_0',
    );
  });
});
