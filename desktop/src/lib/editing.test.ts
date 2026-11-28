import { describe, expect, it } from 'vitest';
import rawModels from '../../../src/localsr/core/edit_catalog.json';
import { demoSnapshot } from './demo';
import {
  CUSTOM_EDIT_FAMILIES,
  EDIT_FAMILIES,
  EDIT_GIB,
  customEditFamilySpec,
  editDevice,
  editDimensions,
  editExpert,
  editExpertChanges,
  editFamilyLabel,
  editFitFor,
  editHardwareLabel,
  editPeakEstimate,
  editRecommendedSize,
  editSizeFor,
  editSpeed,
  editSpeedAddon,
  editStepsFor,
  editVramOptions,
  recommendAnyEditModel,
} from './editing';
import type { DeviceInfo, EditModel } from './types';

// The catalog file lists the bundles and the speed add-on; the host splits them.
const entries = rawModels as unknown as EditModel[];
const models = entries.filter((model) => !model.kind);
const lightning = entries.find((model) => model.kind === 'speed_addon')!;
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

const autoExpert = {
  textEncoder: 'auto' as const,
  vae: 'auto' as const,
  vramLimitMb: 0,
  allowAnySize: false,
};

describe('Qwen memory recommendations', () => {
  it('recommends 1024 px for FLUX.2 klein Q4_0 on a 12 GiB card', () => {
    const capabilities = demoSnapshot().capabilities;
    const fluxQ4 = models.find((model) => model.model_id === 'flux2_klein_4b_q4_0')!;
    const card12 = { ...gpu('cuda:0', 12), free_memory: Math.floor(10.66 * EDIT_GIB) };
    expect(editRecommendedSize(fluxQ4, capabilities, card12, autoExpert)).toBe(1024);
  });

  it.each([
    ['flux2_klein_4b_q4_0', 8.0, 1024],
    ['flux2_klein_4b_q4_0', 7.0, 768],
    ['flux2_klein_4b_q4_0', 5.5, 512],
    ['flux2_klein_4b_q8_0', 8.0, 768],
    ['flux2_klein_4b_q8_0', 7.0, 512],
    ['mage_flow_edit_turbo_q6_k', 8.0, 768],
    ['qwen_edit_2511_q2_k', 8.0, 512],
  ] as const)(
    'matches the 8 GB worker size fit for %s with %s GiB free',
    (modelId, freeGib, expected) => {
      const capabilities = demoSnapshot().capabilities;
      const selected = models.find((model) => model.model_id === modelId)!;
      const card8 = {
        ...gpu('cuda:0', 8),
        total_memory: 8 * EDIT_GIB,
        free_memory: Math.floor(freeGib * EDIT_GIB),
      };
      expect(editRecommendedSize(selected, capabilities, card8, autoExpert)).toBe(expected);
    },
  );

  it('responds to GPU memory limits and forced GPU placement', () => {
    const capabilities = demoSnapshot().capabilities;
    const card12 = { ...gpu('cuda:0', 12), free_memory: Math.floor(10.66 * EDIT_GIB) };
    const fluxQ4 = models.find((model) => model.model_id === 'flux2_klein_4b_q4_0')!;
    const fluxQ8 = models.find((model) => model.model_id === 'flux2_klein_4b_q8_0')!;
    const qwenQ3 = models.find((model) => model.model_id === 'qwen_edit_2511_q3_k_s')!;

    expect(
      editRecommendedSize(fluxQ4, capabilities, card12, { ...autoExpert, vramLimitMb: 6144 }),
    ).toBe(768);
    expect(editRecommendedSize(qwenQ3, capabilities, card12, autoExpert)).toBe(512);
    expect(
      editRecommendedSize(qwenQ3, capabilities, card12, { ...autoExpert, vramLimitMb: 11264 }),
    ).toBe(768);
    expect(editRecommendedSize(fluxQ8, capabilities, card12, autoExpert)).toBe(1024);
    expect(
      editRecommendedSize(fluxQ8, capabilities, card12, { ...autoExpert, textEncoder: 'gpu' }),
    ).toBe(512);
  });

  it('keeps the Mac tier recommendations and defaults to 512 with no device', () => {
    const capabilities = demoSnapshot().capabilities;
    for (const [gb, expected] of [
      [24, 512],
      [36, 768],
      [48, 1024],
    ] as const) {
      capabilities.system_ram_total = gb * EDIT_GIB;
      expect(editRecommendedSize(undefined, capabilities, gpu('mps', gb), autoExpert)).toBe(
        expected,
      );
    }
    expect(editRecommendedSize(undefined, capabilities, undefined, autoExpert)).toBe(512);
  });

  it('builds whole-GB GPU memory options and clamps saved expert overrides', () => {
    const card12 = gpu('cuda:0', 12);
    const options = editVramOptions(card12);
    expect(options[0]).toEqual({ value: 0, label: 'Automatic' });
    expect(options.slice(1).map((opt) => opt.label)).toEqual([
      'Up to 4 GB',
      'Up to 5 GB',
      'Up to 6 GB',
      'Up to 7 GB',
      'Up to 8 GB',
      'Up to 9 GB',
      'Up to 10 GB',
      'Up to 11 GB',
      'Up to 12 GB',
    ]);
    expect(options.at(-1)?.value).toBe(12256);
    expect(editVramOptions(gpu('cuda:0', 3))).toEqual([{ value: 0, label: 'Automatic' }]);

    const fluxQ4 = models.find((model) => model.model_id === 'flux2_klein_4b_q4_0')!;
    const mage = models.find((model) => model.model_id === 'mage_flow_edit_turbo_q6_k')!;
    const full = ['image_edit', 'edit_placement', 'edit_expert'];

    expect(editExpert({ edit_vram_limit_mb: 16384 }, fluxQ4, card12, full).vramLimitMb).toBe(12256);
    expect(
      editExpert(
        {
          edit_text_encoder: 'cpu',
          edit_vae: 'gpu',
          edit_vram_limit_mb: 8192,
          edit_allow_any_size: true,
        },
        fluxQ4,
        card12,
        ['image_edit'],
      ),
    ).toEqual(autoExpert);
    expect(
      editExpert(
        {
          edit_text_encoder: 'cpu',
          edit_vae: 'gpu',
          edit_vram_limit_mb: 8192,
          edit_allow_any_size: true,
        },
        fluxQ4,
        card12,
        ['image_edit', 'edit_placement'],
      ),
    ).toEqual({
      textEncoder: 'cpu',
      vae: 'gpu',
      vramLimitMb: 0,
      allowAnySize: false,
    });
    expect(
      editExpert(
        {
          edit_text_encoder: 'gpu',
          edit_vae: 'gpu',
          edit_vram_limit_mb: 8192,
          edit_allow_any_size: true,
        },
        fluxQ4,
        gpu('mps', 24),
        full,
      ),
    ).toEqual({
      textEncoder: 'gpu',
      vae: 'gpu',
      vramLimitMb: 0,
      allowAnySize: false,
    });
    expect(
      editExpert({ edit_text_encoder: 'gpu', edit_vae: 'gpu' }, mage, gpu('mps', 24), full),
    ).toEqual(autoExpert);

    expect(editExpertChanges(autoExpert)).toEqual([]);
    expect(
      editExpertChanges({
        textEncoder: 'cpu',
        vae: 'gpu',
        vramLimitMb: 11264,
        allowAnySize: true,
      }),
    ).toEqual([
      'Text encoder on the CPU',
      'VAE on the GPU',
      'GPU memory up to 11 GB',
      'Any edit size',
    ]);
    expect(editSizeFor(1024, 512, { allowAnySize: false })).toBe(512);
    expect(editSizeFor(1024, 512, { allowAnySize: true })).toBe(1024);
  });
});

describe('Edit helpers', () => {
  it('judges the fit by unified memory on a Mac and by card memory elsewhere', () => {
    const capabilities = demoSnapshot().capabilities;
    capabilities.system_ram_total = 16 * EDIT_GIB;
    const q2 = models.find((model) => model.model_id === 'qwen_edit_2511_q2_k')!;
    // Measured on a 16 GB M1 Pro: 7.31 GiB at 40 steps, 9.00 GiB with Lightning.
    expect(editFitFor(q2, capabilities, gpu('mps', 16))).toBe('runs');
    capabilities.system_ram_total = 8 * EDIT_GIB;
    expect(editFitFor(q2, capabilities, gpu('mps', 8))).toBe('too_heavy');
    capabilities.system_ram_total = 16 * EDIT_GIB;
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

describe('Mage-Flow summary', () => {
  it('says CPU only on a Mac and nothing elsewhere', async () => {
    const { detectPlatform } = await import('./platform');
    const summary = EDIT_FAMILIES.find((family) => family.id === 'mage-flow-edit-turbo')!.summary;
    if (detectPlatform() === 'macos') expect(summary).toBe('4-step editor (CPU only) · MIT');
    else expect(summary).toBe('4-step editor · MIT');
  });
});

describe('Quick quality and the Lightning add-on', () => {
  const qwen = models.find((model) => model.model_id === 'qwen_edit_2511_q2_k')!;
  const flux = models.find((model) => model.model_id === 'flux2_klein_4b_q4_0')!;
  const features = ['image_edit', 'edit_lightning'];

  it('lists the add-on in the catalog file but never as an edit model', () => {
    expect(lightning).toMatchObject({
      model_id: 'qwen_edit_2511_lightning_4step',
      speed: 'lightning',
      family: 'qwen-image-edit-2511',
      default_steps: 4,
      cfg_scale: 1,
    });
    expect(demoSnapshot().edit.models.some((model) => model.kind)).toBe(false);
    expect(demoSnapshot().edit.addons?.map((model) => model.model_id)).toEqual([
      'qwen_edit_2511_lightning_4step',
    ]);
  });

  it.each([8, 16, 24, 48])(
    'never recommends the 850 MB add-on, even as the smallest file, on a %s GB Mac',
    (gb) => {
      const capabilities = demoSnapshot().capabilities;
      capabilities.system_ram_total = gb * EDIT_GIB;
      for (const device of [gpu('mps', gb), gpu('cuda:0', gb), undefined]) {
        expect(recommendAnyEditModel(entries, capabilities, device)?.kind).toBeUndefined();
      }
    },
  );

  it('shows the smallest bundle, not the add-on, where nothing fits', () => {
    const capabilities = demoSnapshot().capabilities;
    capabilities.system_ram_total = 8 * EDIT_GIB;
    expect(recommendAnyEditModel(entries, capabilities, gpu('mps', 8))?.model_id).toBe(
      'flux2_klein_4b_q4_0',
    );
    expect(recommendAnyEditModel(entries, capabilities, undefined)?.model_id).toBe(
      'flux2_klein_4b_q4_0',
    );
  });

  it('offers Quick for a catalog Qwen model on an engine that runs it', () => {
    expect(editSpeedAddon(qwen, [lightning], features)).toBe(lightning);
    expect(editSpeedAddon(qwen, [lightning], ['image_edit'])).toBeUndefined();
    expect(editSpeedAddon(flux, [lightning], features)).toBeUndefined();
    expect(editSpeedAddon({ ...qwen, model_id: '__custom__' }, [lightning], features)).toBe(
      undefined,
    );
    expect(editSpeedAddon(qwen, undefined, features)).toBeUndefined();
    expect(editSpeedAddon(undefined, [lightning], features)).toBeUndefined();
  });

  it('defaults to Quick unless the model is installed and the add-on is not', () => {
    const installed = (model: EditModel, value: boolean) => ({ ...model, installed: value });
    expect(editSpeed({}, qwen, undefined)).toBeNull();
    expect(editSpeed({}, installed(qwen, false), installed(lightning, false))).toBe('lightning');
    expect(editSpeed({}, installed(qwen, true), installed(lightning, false))).toBe('full');
    expect(editSpeed({}, installed(qwen, true), installed(lightning, true))).toBe('lightning');
    expect(
      editSpeed({ edit_speed: 'lightning' }, installed(qwen, true), installed(lightning, false)),
    ).toBe('lightning');
    expect(editSpeed({ edit_speed: 'full' }, qwen, lightning)).toBe('full');
  });

  it('runs the add-on steps in Quick and the saved or own steps in Best', () => {
    expect(editStepsFor({ edit_steps: 25 }, qwen, 'lightning', lightning)).toBe(4);
    expect(editStepsFor({ edit_steps: 25 }, qwen, 'full', lightning)).toBe(25);
    expect(editStepsFor({ edit_steps: 0 }, qwen, 'full', lightning)).toBe(40);
    expect(editStepsFor({ edit_steps: 0 }, flux, null, undefined)).toBe(4);
  });
});
