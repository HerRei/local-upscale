// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, expect, it } from 'vitest';
import VideoMemory from './VideoMemory.svelte';
import type { VideoMemoryStatus } from './lib/types';
afterEach(cleanup);

it('shows the failed stage and captured GPU evidence independently of changed settings', () => {
  const memory: VideoMemoryStatus = {
    job_id: 'failed-job', device: 'cuda:0', stage: 'decoding', low_memory: false,
    clip_frames: 9, vae_tile_size: 512, blocks_to_swap: 0, offload_tensors: false,
    output_width: 2160, output_height: 3840, gpu_sample_available: true, shared_memory: false,
    device_total_memory: 16 * 1024 ** 3, device_free_memory: 1024 ** 3,
    device_allocated_memory: 12 * 1024 ** 3, device_reserved_memory: 14 * 1024 ** 3,
    device_peak_memory: 13 * 1024 ** 3, system_ram_available: 10 * 1024 ** 3,
    process_ram: 6 * 1024 ** 3, elapsed_seconds: 30, oom: true
  };
  render(VideoMemory, { memory, lowMemory: true, outputDimensions: '720 × 1280' });
  expect(screen.getByText('Out of memory')).toBeTruthy();
  expect(screen.getByText('VAE decoding')).toBeTruthy();
  expect(screen.getByText('12.00 GiB / 13.00 GiB')).toBeTruthy();
  expect(screen.getByText('1.00 GiB / 16.00 GiB')).toBeTruthy();
  expect(screen.getByText(/2160 × 3840 · up to 9 frames\/clip · standard/)).toBeTruthy();
  expect(screen.getByText(/Readings captured at failure/)).toBeTruthy();
});
