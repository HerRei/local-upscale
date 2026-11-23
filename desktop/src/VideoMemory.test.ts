// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, expect, it } from 'vitest';
import VideoMemory from './VideoMemory.svelte';
import type { VideoMemoryStatus } from './lib/types';
afterEach(cleanup);

it('shows the failed stage and captured GPU evidence independently of changed settings', () => {
  const memory: VideoMemoryStatus = {
    job_id: 'failed-job',
    device: 'cuda:0',
    stage: 'decoding',
    low_memory: false,
    clip_frames: 9,
    vae_tile_size: 512,
    blocks_to_swap: 0,
    offload_tensors: false,
    output_width: 2160,
    output_height: 3840,
    gpu_sample_available: true,
    shared_memory: false,
    device_total_memory: 16 * 1024 ** 3,
    device_free_memory: 1024 ** 3,
    device_allocated_memory: 12 * 1024 ** 3,
    device_reserved_memory: 14 * 1024 ** 3,
    device_peak_memory: 13 * 1024 ** 3,
    system_ram_available: 10 * 1024 ** 3,
    process_ram: 6 * 1024 ** 3,
    elapsed_seconds: 30,
    oom: true,
  };
  render(VideoMemory, { memory, lowMemory: true, outputDimensions: '720 × 1280' });
  expect(screen.getByText('Out of memory')).toBeTruthy();
  expect(screen.getByText('VAE decoding')).toBeTruthy();
  expect(screen.getByText('12.00 GiB / 13.00 GiB')).toBeTruthy();
  expect(screen.getByText('1.00 GiB / 16.00 GiB')).toBeTruthy();
  expect(screen.getByText(/2160 × 3840 · up to 9 frames\/clip · standard/)).toBeTruthy();
  expect(screen.getByText(/Readings captured at failure/)).toBeTruthy();
});

it.each(['mps', 'cpu', 'xpu:0', 'directml:0'])(
  'does not offer ineffective GPU offload on %s',
  (id) => {
    render(VideoMemory, { device: { id } as never });
    expect(screen.queryByRole('checkbox', { name: 'Reduce GPU memory' })).toBeNull();
  },
);
it.each(['cuda:0', 'cuda:1'])('offers the real CPU offload control on CUDA/ROCm %s', (id) => {
  render(VideoMemory, { device: { id } as never });
  expect(screen.getByRole('checkbox', { name: 'Reduce GPU memory' })).toBeTruthy();
});

const flash = { engine_kind: 'flashvsr', temporal_window: 41, temporal_overlap: 8 };
const reading = (changes: Partial<VideoMemoryStatus>): VideoMemoryStatus => ({
  job_id: 'job',
  device: 'mps',
  stage: 'decoding',
  low_memory: true,
  clip_frames: 5,
  vae_tile_size: 128,
  blocks_to_swap: 0,
  offload_tensors: false,
  output_width: 1920,
  output_height: 1080,
  gpu_sample_available: true,
  shared_memory: true,
  device_total_memory: 0,
  device_free_memory: 0,
  device_allocated_memory: 9 * 1024 ** 3,
  device_reserved_memory: 0,
  device_peak_memory: 11 * 1024 ** 3,
  system_ram_available: 4 * 1024 ** 3,
  process_ram: 12 * 1024 ** 3,
  elapsed_seconds: 30,
  oom: false,
  ...changes,
});

it('explains FlashVSR by its tiles and windows, with no SeedVR2 numbers and nothing to set', () => {
  render(VideoMemory, {
    model: flash,
    device: { id: 'mps', total_memory: 10 * 1024 ** 3 } as never,
    outputDimensions: '3840 × 2160',
  });
  const text = screen.getByLabelText('FlashVSR memory').textContent ?? '';
  expect(text).toMatch(/Apple GPU · automatic memory management/);
  expect(text).toMatch(/tiles sized to this Mac's memory, so there is no memory setting/);
  expect(text).toMatch(/windows of 41 frames; each starts with 8\s+frames of run-up/);
  expect(text).toMatch(/Requested output: 3840 × 2160\. Jobs on large\s+frames can take hours\./);
  expect(text).not.toMatch(/SeedVR2|VAE|per clip|offload|GiB|Capacity/);
  expect(screen.queryByRole('checkbox')).toBeNull();
});

it('reads a FlashVSR job in windows, without VAE, clip or PyTorch wording', () => {
  render(VideoMemory, { model: flash, memory: reading({ clip_frames: 41 }), running: true });
  const text = screen.getByLabelText('FlashVSR memory').textContent ?? '';
  expect(screen.getByText('Decoding')).toBeTruthy();
  expect(text).toMatch(/1920 × 1080 · windows of 41 frames · automatic memory management/);
  expect(screen.getByText('9.00 GiB / 11.00 GiB')).toBeTruthy();
  expect(text).toMatch(/Measured every second\./);
  expect(text).not.toMatch(/VAE|frames\/clip|PyTorch/);
});

it('leaves the window count out when the engine does not report one', () => {
  render(VideoMemory, { model: flash, memory: reading({ clip_frames: 0, stage: 'enhancing' }) });
  expect(screen.getByText('Diffusion')).toBeTruthy();
  expect(screen.getByLabelText('FlashVSR memory').textContent).toMatch(
    /1920 × 1080 · automatic memory management/,
  );
});

it("keeps a SeedVR2 job's readings in SeedVR2 terms while FlashVSR is selected", () => {
  render(VideoMemory, { model: flash, memoryEngine: 'seedvr2', memory: reading({}) });
  expect(screen.getByText('VAE decoding')).toBeTruthy();
  expect(screen.getByText(/1920 × 1080 · up to 5 frames\/clip · automatic memory management/));
  expect(screen.getByText(/Reserved memory includes allocated tensors and PyTorch's cache/));
});

it('keeps the SeedVR2 panel as it was', () => {
  render(VideoMemory, {
    model: { engine_kind: 'seedvr2', temporal_window: 21, temporal_overlap: 0 },
    device: { id: 'mps', total_memory: 10 * 1024 ** 3 } as never,
    outputDimensions: '720 × 1280',
  });
  const text = screen.getByLabelText('SeedVR2 memory').textContent ?? '';
  expect(text).toMatch(/Up to 5 frames per clip · 128 px VAE tiles\./);
  expect(text).toMatch(/Requested output: 720 × 1280\. Memory saving keeps\s+this size\./);
  expect(text).toMatch(/10\.00 GiB\s+shared memory\./);
});
