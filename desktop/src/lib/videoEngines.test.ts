import { describe, expect, it } from 'vitest';
import rawCatalog from '../../src-tauri/resources/model-catalog.json';
import type { CatalogManifest, DeviceInfo } from './types';
import {
  videoAvailability,
  videoEngineName,
  videoModelShortName,
  videoModelSummary,
  videoModelUnavailable,
  videoModelsForJob,
} from './videoEngines';

const GIB = 1024 ** 3;
const flash = {
  model_id: 'flashvsr_v11',
  engine_kind: 'flashvsr',
  min_unified_memory_gb: 16,
  min_vram_gb: 12,
};
const seed = {
  model_id: 'seedvr2_3b',
  engine_kind: 'seedvr2',
  min_unified_memory_gb: 16,
  min_vram_gb: 16,
};
const seedFp8 = {
  model_id: 'seedvr2_3b_fp8',
  engine_kind: 'seedvr2',
  min_unified_memory_gb: 24,
  min_vram_gb: 12,
};

function device(id: string, totalGb = 0): DeviceInfo {
  return {
    id,
    type: id.split(':')[0],
    name: id,
    total_memory: totalGb * GIB,
    free_memory: 0,
    supports_fp16: true,
    is_integrated: false,
    recommended_tile_sizes: [],
  };
}

// The Apple GPU reports PyTorch's recommended share (about two thirds), not the Mac's memory.
const mac = (gb: number) => ({
  devices: [device('mps', (gb * 2) / 3), device('cpu', gb)],
  system_ram_total: gb * GIB,
});
const pc = { devices: [device('cpu', 32), device('cuda:0', 24)], system_ram_total: 32 * GIB };
const macEngine = { video_engines: ['spandrel_image', 'seedvr2', 'flashvsr'] };
const seedOnly = { video_engines: ['spandrel_image', 'seedvr2'] };

describe('FlashVSR availability', () => {
  it('runs on the Apple GPU of a 16 GB Mac whose engine lists it', () => {
    expect(videoModelUnavailable(flash, macEngine, mac(16), 'mps')).toBeUndefined();
    expect(videoModelUnavailable(flash, macEngine, mac(64), 'mps')).toBeUndefined();
  });

  it('does not run on the CPU or unsupported backends', () => {
    const wrongDevice = {
      reason:
        'FlashVSR cannot run on this device. Choose a frame-by-frame model or a compatible CUDA, ROCm or Metal device.',
      tag: 'Unavailable',
    };
    expect(videoModelUnavailable(flash, macEngine, mac(16), 'cpu')).toEqual(wrongDevice);
    expect(videoModelUnavailable(flash, macEngine, pc, 'cpu')).toEqual(wrongDevice);
    expect(videoModelUnavailable(flash, macEngine, pc, 'directml:0')).toEqual(wrongDevice);
    expect(videoModelUnavailable(flash, macEngine, pc, 'xpu:0')).toEqual(wrongDevice);
    expect(videoModelUnavailable(flash, macEngine, pc, 'openvino:GPU')).toEqual(wrongDevice);
  });

  it('runs on CUDA and ROCm GPUs with enough VRAM when the engine lists it', () => {
    expect(videoModelUnavailable(flash, macEngine, pc, 'cuda:0')).toBeUndefined();
    const pc12 = { devices: [device('cpu', 32), device('cuda:0', 12)], system_ram_total: 32 * GIB };
    expect(videoModelUnavailable(flash, macEngine, pc12, 'cuda:0')).toBeUndefined();
    // Driver reports slightly below 12 GiB (e.g. 11.8 GiB) still rounds to 12 GB.
    const pc11_8 = {
      devices: [device('cpu', 32), device('cuda:0', 11.8)],
      system_ram_total: 32 * GIB,
    };
    expect(videoModelUnavailable(flash, macEngine, pc11_8, 'cuda:0')).toBeUndefined();
  });

  it('does not run on a CUDA/ROCm GPU with less than min_vram_gb', () => {
    const pc8 = { devices: [device('cpu', 32), device('cuda:0', 8)], system_ram_total: 32 * GIB };
    expect(videoModelUnavailable(flash, macEngine, pc8, 'cuda:0')).toEqual({
      reason: 'FlashVSR needs 12 GB of GPU memory. This GPU has 8 GB.',
      tag: 'Needs 12 GB',
    });
    // Unknown VRAM leaves the check to the engine.
    const pcUnknown = { devices: [device('cuda:0', 0)], system_ram_total: 32 * GIB };
    expect(videoModelUnavailable(flash, macEngine, pcUnknown, 'cuda:0')).toBeUndefined();
  });

  it('does not run on a Mac with 8 GB, whatever device is chosen', () => {
    const small = {
      reason: 'FlashVSR needs 16 GB of memory. This Mac has 8 GB.',
      tag: 'Needs 16 GB',
    };
    expect(videoModelUnavailable(flash, macEngine, mac(8), 'mps')).toEqual(small);
    expect(videoModelUnavailable(flash, macEngine, mac(8), 'cpu')).toEqual(small);
    // An engine that leaves FlashVSR out on a small Mac gets the same reason.
    expect(videoModelUnavailable(flash, seedOnly, mac(8), 'mps')).toEqual(small);
  });

  it('counts the memory the Mac has, not the smaller share its GPU reports', () => {
    // 16 GB installed, 10.7 GB reported for the Apple GPU: still enough.
    expect(mac(16).devices[0].total_memory).toBeLessThan(16 * GIB);
    expect(videoModelUnavailable(flash, macEngine, mac(16), 'mps')).toBeUndefined();
  });

  it('leaves an unknown memory size to the engine', () => {
    const unknown = { devices: [device('mps')], system_ram_total: 0 };
    expect(videoModelUnavailable(flash, macEngine, unknown, 'mps')).toBeUndefined();
  });

  it('is unavailable when the engine does not list it', () => {
    expect(videoModelUnavailable(flash, seedOnly, pc, 'cuda:0')).toEqual({
      reason:
        'This installation supports frame-by-frame models. FlashVSR requires a compatible CUDA, ROCm or Metal engine.',
      tag: 'Unavailable',
    });
    // On a Mac that should run it, a missing engine is unexpected and says so.
    expect(videoModelUnavailable(flash, seedOnly, mac(16), 'mps')).toEqual({
      reason: 'FlashVSR needs the MLX runtime, which this installation does not have.',
      tag: 'Unavailable',
    });
  });

  it('claims nothing about the computer before the engine has started', () => {
    for (const engine of [null, undefined])
      expect(videoModelUnavailable(flash, engine, mac(16), 'mps')).toEqual({
        reason: 'FlashVSR is unavailable until the engine has started.',
        tag: 'Unavailable',
      });
  });
});

describe('SeedVR2 availability', () => {
  const notListed =
    'This installation supports frame-by-frame models. SeedVR2 requires a compatible CUDA, ROCm or Metal engine.';
  const wrongDevice =
    'SeedVR2 cannot run on this device. Choose a frame-by-frame model or a compatible CUDA, ROCm or Metal device.';

  it.each(['mps', 'cpu', 'cuda', 'cuda:0', 'cuda:1'])('runs on %s when listed', (id) => {
    expect(videoModelUnavailable(seed, seedOnly, pc, id)).toBeUndefined();
  });

  it.each(['directml:0', 'xpu:0', 'xpu', 'openvino:GPU'])('does not run on %s', (id) => {
    expect(videoModelUnavailable(seed, seedOnly, pc, id)).toEqual({
      reason: wrongDevice,
      tag: 'Unavailable',
    });
  });

  it('is unavailable when the engine does not list it, or has not started', () => {
    const others = { video_engines: ['spandrel_image', 'flashvsr'] };
    const state = { reason: notListed, tag: 'Unavailable' };
    expect(videoModelUnavailable(seed, others, pc, 'cuda:0')).toEqual(state);
    expect(videoModelUnavailable(seed, null, pc, 'cuda:0')).toEqual(state);
  });

  it('has no memory rule of its own: an 8 GB Mac may still choose it', () => {
    expect(videoModelUnavailable(seed, macEngine, mac(8), 'mps')).toBeUndefined();
  });
});

describe('per-model availability and library helpers', () => {
  const models = [seed, seedFp8, flash];
  const ids = (state: ReturnType<typeof videoAvailability>) =>
    Object.keys(state).filter((id) => state[id]);

  it('judges each model by its own engine', () => {
    const onMac = videoAvailability(models, macEngine, mac(16), 'mps');
    expect(ids(onMac)).toEqual([]);

    const onPc = videoAvailability(models, seedOnly, pc, 'cuda:0');
    expect(ids(onPc)).toEqual(['flashvsr_v11']);
    expect(onPc.flashvsr_v11?.tag).toBe('Unavailable');

    const onCuda = videoAvailability(models, macEngine, pc, 'cuda:0');
    expect(ids(onCuda)).toEqual([]);
  });

  it('covers every video model of the shipped catalog', () => {
    // A new engine kind in the catalog needs its own rule here before it ships.
    const shipped = rawCatalog.video_models;
    expect(new Set(shipped.map((model) => model.engine_kind))).toEqual(
      new Set(['seedvr2', 'flashvsr']),
    );
    const state = videoAvailability(shipped, macEngine, mac(16), 'mps');
    expect(Object.keys(state)).toContain('flashvsr_v11');
    expect(ids(state)).toEqual([]);

    const byId = Object.fromEntries(shipped.map((model) => [model.model_id, model]));
    expect([byId.seedvr2_3b.quality_tier, byId.seedvr2_3b.speed_tier]).toEqual([4, 0]);
    expect([byId.seedvr2_3b_fp8.quality_tier, byId.seedvr2_3b_fp8.speed_tier]).toEqual([4, 0]);
    expect([byId.flashvsr_v11.quality_tier, byId.flashvsr_v11.speed_tier]).toEqual([3, 1]);
  });

  it('lets an engine this version does not know run when the worker lists it', () => {
    const next = {
      model_id: 'next',
      engine_kind: 'next_engine',
      min_unified_memory_gb: 0,
      min_vram_gb: 0,
    };
    expect(
      videoModelUnavailable(next, { video_engines: ['next_engine'] }, pc, 'cpu'),
    ).toBeUndefined();
    expect(videoModelUnavailable(next, seedOnly, pc, 'cpu')?.reason).toBe(
      'This installation cannot run next_engine.',
    );
    expect(videoEngineName('seedvr2')).toBe('SeedVR2');
    expect(videoEngineName('flashvsr')).toBe('FlashVSR');
  });

  it('lists video models only for video Upscale', () => {
    const catalog = rawCatalog as unknown as CatalogManifest;
    expect(videoModelsForJob(catalog, 'video', 'upscale')).toHaveLength(3);
    expect(videoModelsForJob(catalog, 'video', 'denoise')).toEqual([]);
    expect(videoModelsForJob(catalog, 'upscale')).toEqual([]);
    expect(videoModelsForJob(catalog, 'denoise')).toEqual([]);
    expect(videoModelsForJob(catalog, 'edit')).toEqual([]);
    expect(videoModelsForJob(catalog, '')).toEqual([]);
  });

  it('derives short names and row summaries for catalog video models', () => {
    expect(videoModelShortName({ name: 'FlashVSR v1.1 — Labs' })).toBe('FlashVSR v1.1');
    expect(videoModelShortName({ name: 'SeedVR2-3B FP8 — Labs' })).toBe('SeedVR2-3B FP8');
    expect(videoModelSummary({ summary: 'Given summary', description: 'Ignored. More.' })).toBe(
      'Given summary',
    );
    expect(
      videoModelSummary({
        summary: '',
        description:
          'Experimental one-step diffusion video upscaling (Tiny pipeline). Runs on MLX.',
      }),
    ).toBe('Experimental one-step diffusion video upscaling (Tiny pipeline).');
  });
});
