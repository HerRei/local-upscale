import { describe, expect, it } from 'vitest';
import rawCatalog from '../../src-tauri/resources/model-catalog.json';
import type { DeviceInfo } from './types';
import { videoAvailability, videoEngineName, videoModelUnavailable } from './videoEngines';

const GIB = 1024 ** 3;
const flash = { model_id: 'flashvsr_v11', engine_kind: 'flashvsr', min_unified_memory_gb: 16 };
const seed = { model_id: 'seedvr2_3b', engine_kind: 'seedvr2', min_unified_memory_gb: 16 };
const seedFp8 = { model_id: 'seedvr2_3b_fp8', engine_kind: 'seedvr2', min_unified_memory_gb: 24 };

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

  it('does not run on the CPU', () => {
    expect(videoModelUnavailable(flash, macEngine, mac(16), 'cpu')).toEqual({
      reason: 'FlashVSR cannot run on this device. Choose frame-by-frame video or the Apple GPU.',
      tag: 'Apple GPU only',
      quiet: true,
    });
  });

  it('does not run on CUDA or ROCm even when an engine lists it', () => {
    expect(videoModelUnavailable(flash, macEngine, pc, 'cuda:0')?.reason).toMatch(
      /^FlashVSR cannot run on this device\./,
    );
    expect(videoModelUnavailable(flash, macEngine, pc, 'cuda')?.tag).toBe('Apple GPU only');
  });

  it('does not run on a Mac with 8 GB, whatever device is chosen', () => {
    const small = {
      reason: 'FlashVSR needs 16 GB of memory. This Mac has 8 GB.',
      tag: 'Needs 16 GB',
      quiet: true,
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
      reason: 'FlashVSR needs an Apple silicon Mac with 16 GB of memory.',
      tag: 'Apple silicon only',
      quiet: true,
    });
    // On a Mac that should run it, a missing engine is unexpected and says so.
    expect(videoModelUnavailable(flash, seedOnly, mac(16), 'mps')).toEqual({
      reason: 'FlashVSR needs the MLX runtime, which this installation does not have.',
      tag: 'Unavailable',
      quiet: false,
    });
  });

  it('claims nothing about the computer before the engine has started', () => {
    for (const engine of [null, undefined])
      expect(videoModelUnavailable(flash, engine, mac(16), 'mps')).toEqual({
        reason: 'FlashVSR is unavailable until the engine has started.',
        tag: 'Unavailable',
        quiet: true,
      });
  });
});

describe('SeedVR2 availability', () => {
  const notListed =
    'This installation supports frame-by-frame video. SeedVR2 requires a compatible CUDA, ROCm or Metal engine.';
  const wrongDevice =
    'SeedVR2 cannot run on this device. Choose frame-by-frame video or a compatible CUDA, ROCm or Metal device.';

  it.each(['mps', 'cpu', 'cuda', 'cuda:0', 'cuda:1'])('runs on %s when listed', (id) => {
    expect(videoModelUnavailable(seed, seedOnly, pc, id)).toBeUndefined();
  });

  it.each(['directml:0', 'xpu:0', 'xpu', 'openvino:GPU'])('does not run on %s', (id) => {
    expect(videoModelUnavailable(seed, seedOnly, pc, id)).toEqual({
      reason: wrongDevice,
      tag: 'Unavailable',
      quiet: false,
    });
  });

  it('is unavailable when the engine does not list it, or has not started', () => {
    const others = { video_engines: ['spandrel_image', 'flashvsr'] };
    const state = { reason: notListed, tag: 'Unavailable', quiet: false };
    expect(videoModelUnavailable(seed, others, pc, 'cuda:0')).toEqual(state);
    expect(videoModelUnavailable(seed, null, pc, 'cuda:0')).toEqual(state);
  });

  it('has no memory rule of its own: an 8 GB Mac may still choose it', () => {
    expect(videoModelUnavailable(seed, macEngine, mac(8), 'mps')).toBeUndefined();
  });
});

describe('the engine menu', () => {
  const models = [seed, seedFp8, flash];
  const ids = (state: ReturnType<typeof videoAvailability>) =>
    Object.keys(state.unavailable).filter((id) => state.unavailable[id]);

  it('judges each model by its own engine', () => {
    const onMac = videoAvailability(models, macEngine, mac(16), 'mps');
    expect(ids(onMac)).toEqual([]);
    expect(onMac.notes).toEqual([]);

    const onPc = videoAvailability(models, seedOnly, pc, 'cuda:0');
    expect(ids(onPc)).toEqual(['flashvsr_v11']);
    expect(onPc.unavailable.flashvsr_v11?.tag).toBe('Apple silicon only');
  });

  it('keeps a reason its tag already gives out of the notes, unless that model is chosen', () => {
    expect(videoAvailability(models, seedOnly, pc, 'cuda:0').notes).toEqual([]);
    expect(videoAvailability(models, seedOnly, pc, 'cuda:0', 'seedvr2_3b').notes).toEqual([]);
    expect(videoAvailability(models, seedOnly, pc, 'cuda:0', 'flashvsr_v11').notes).toEqual([
      'FlashVSR needs an Apple silicon Mac with 16 GB of memory.',
    ]);
    expect(videoAvailability(models, macEngine, mac(8), 'mps', 'flashvsr_v11').notes).toEqual([
      'FlashVSR needs 16 GB of memory. This Mac has 8 GB.',
    ]);
  });

  it('says a shared reason once, however many models it covers', () => {
    const cpuOnly = { video_engines: ['spandrel_image'] };
    const state = videoAvailability(models, cpuOnly, pc, 'cpu');
    expect(ids(state)).toEqual(['seedvr2_3b', 'seedvr2_3b_fp8', 'flashvsr_v11']);
    expect(state.notes).toHaveLength(1);
    expect(state.notes[0]).toMatch(/^This installation supports frame-by-frame video\. SeedVR2/);
  });

  it('covers every video model of the shipped catalog', () => {
    // A new engine kind in the catalog needs its own rule here before it ships.
    const shipped = rawCatalog.video_models;
    expect(new Set(shipped.map((model) => model.engine_kind))).toEqual(
      new Set(['seedvr2', 'flashvsr']),
    );
    const state = videoAvailability(shipped, macEngine, mac(16), 'mps');
    expect(Object.keys(state.unavailable)).toContain('flashvsr_v11');
    expect(ids(state)).toEqual([]);
  });

  it('lets an engine this version does not know run when the worker lists it', () => {
    const next = { model_id: 'next', engine_kind: 'next_engine', min_unified_memory_gb: 0 };
    expect(
      videoModelUnavailable(next, { video_engines: ['next_engine'] }, pc, 'cpu'),
    ).toBeUndefined();
    expect(videoModelUnavailable(next, seedOnly, pc, 'cpu')?.reason).toBe(
      'This installation cannot run next_engine.',
    );
    expect(videoEngineName('seedvr2')).toBe('SeedVR2');
    expect(videoEngineName('flashvsr')).toBe('FlashVSR');
  });
});
