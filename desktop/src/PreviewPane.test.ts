// @vitest-environment jsdom
import { cleanup, render, waitFor } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import PreviewPane from './PreviewPane.svelte';
import type { MediaItem, WorkerEnvelope } from './lib/types';

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('paints the asynchronous JPEG after empty completion metadata, and rejects an older frame', async () => {
  const drawImage = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ fillRect: vi.fn(), clearRect: vi.fn(), drawImage } as unknown as CanvasRenderingContext2D);
  vi.stubGlobal('Image', class { onload?: () => void; set src(_value: string) { this.onload?.(); } });
  const selectedMedia: MediaItem = { id: 'source', path: '/source.mp4', name: 'source.mp4', kind: 'video', width: 100, height: 100, frame_count: 2, fps: 1, duration_seconds: 2, preview_data_url: 'data:image/jpeg;base64,source', probe_status: 'ready', error: '', selected: true };
  const { component, container } = render(PreviewPane, { selectedMedia, resultPreview: '', completedVideoOutput: '', addFiles: async () => {} });
  const data = { job_id: 'job', phase: 'started', frame_index: 0, output_x: 0, output_y: 0, output_width: 200, output_height: 200, image_width: 400, image_height: 400 };
  const send = (changes: Record<string, unknown>) => component.queueProgressiveTile({ type: 'tile_update', data: { ...data, ...changes } } as WorkerEnvelope, 'job');
  send({});
  await waitFor(() => expect(container.querySelector('.progressive-image.visible')).toBeTruthy());
  send({ output_x: 200 });
  await waitFor(() => expect((container.querySelector('.active-tile') as HTMLElement)?.style.left).toBe('50%'));
  send({ phase: 'completed', jpeg_base64: '' });
  send({ phase: 'completed', jpeg_base64: 'actual-tile' });
  await waitFor(() => expect(drawImage).toHaveBeenCalledTimes(1));
  expect((container.querySelector('.active-tile') as HTMLElement)?.style.left).toBe('50%');
  send({ frame_index: 1 });
  await waitFor(() => expect(container.querySelector('.progressive-image.visible')).toBeTruthy());
  send({ frame_index: 0, phase: 'completed', jpeg_base64: 'late-old-tile' });
  expect(drawImage).toHaveBeenCalledTimes(1);
});

it('keeps the current source frame below completed tiles and rejects stale source JPEGs', async () => {
  const drawImage = vi.fn();
  const context = { fillStyle: '', fillRect: vi.fn(), clearRect: vi.fn(), drawImage };
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.stubGlobal('Image', class { onload?: () => void; set src(_value: string) { this.onload?.(); } });
  const selectedMedia: MediaItem = { id: 'source', path: '/portrait.mov', name: 'portrait.mov', kind: 'video', width: 2160, height: 3840, frame_count: 3, fps: 1, duration_seconds: 3, preview_data_url: 'data:image/jpeg;base64,thumbnail', probe_status: 'ready', error: '', selected: true };
  const { component, container } = render(PreviewPane, { selectedMedia, processing: true, resultPreview: '', completedVideoOutput: '', addFiles: async () => {} });
  const geometry = { job_id: 'job', frame_index: 1, output_x: 0, output_y: 0, output_width: 256, output_height: 256, image_width: 8640, image_height: 15360 };
  const tile = (changes: Record<string, unknown>) => component.queueProgressiveTile({ type: 'tile_update', data: { ...geometry, ...changes } }, 'job');
  const source = (frame: number, jpeg: string, job = 'job') => component.queueVideoSource({ type: 'live_preview_frame', data: { job_id: job, frame_index: frame, jpeg_base64: jpeg } }, 'job');
  tile({ phase: 'started' });
  await waitFor(() => expect(container.querySelector('.source-image.waiting-frame')).toBeTruthy());
  // The video grid is translucent; it cannot cover the source with solid black.
  expect(context.fillStyle).toMatch(/^#101721(38|60)$/);
  tile({ phase: 'completed', jpeg_base64: 'model-tile' });
  await waitFor(() => expect(drawImage).toHaveBeenCalledTimes(1));
  const clears = context.clearRect.mock.calls.length;
  source(1, 'frame-two');
  await waitFor(() => expect(container.querySelector('.source-image')?.getAttribute('src')).toContain('frame-two'));
  expect(container.querySelector('.source-image.waiting-frame')).toBeNull();
  expect(context.clearRect).toHaveBeenCalledTimes(clears);
  expect(drawImage).toHaveBeenCalledTimes(1);
  source(0, 'old-frame');
  source(2, 'wrong-job', 'another-job');
  expect(container.querySelector('.source-image')?.getAttribute('src')).toContain('frame-two');
  source(2, 'frame-three');
  await waitFor(() => expect(container.querySelector('.source-image')?.getAttribute('src')).toContain('frame-three'));
  source(1, 'late-frame-two');
  expect(container.querySelector('.source-image')?.getAttribute('src')).toContain('frame-three');
});

it('shows overlapping SeedVR2 regions over the source without inventing a HAT grid', async () => {
  const fillRect = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ clearRect: vi.fn(), fillRect } as unknown as CanvasRenderingContext2D);
  const selectedMedia: MediaItem = { id: 'source', path: '/portrait.mov', name: 'portrait.mov', kind: 'video', width: 2160, height: 3840, frame_count: 5, fps: 1, duration_seconds: 5, preview_data_url: 'data:image/jpeg;base64,thumbnail', probe_status: 'ready', error: '', selected: true };
  const { component, container } = render(PreviewPane, { selectedMedia, processing: true, activityLabel: 'Encoding clip', resultPreview: '', completedVideoOutput: '', addFiles: async () => {} });
  component.queueVideoSource({ type: 'live_preview_frame', data: { job_id: 'job', frame_index: 0, jpeg_base64: 'current-frame' } }, 'job');
  const data = { job_id: 'job', frame_index: 0, processing_stage: 'encoding', phase: 'started', output_x: 112, output_y: 448, output_width: 128, output_height: 6, image_width: 256, image_height: 454 };
  component.queueProgressiveTile({ type: 'tile_update', data }, 'job');
  await waitFor(() => expect(container.querySelector('.active-tile')).toBeTruthy());
  expect((container.querySelector('.active-tile') as HTMLElement).style.left).toBe('43.75%');
  expect(fillRect).not.toHaveBeenCalled();
  expect(container.querySelector('.source-image')?.getAttribute('src')).toContain('current-frame');
  component.queueProgressiveTile({ type: 'tile_update', data: { ...data, phase: 'completed' } }, 'job');
  await waitFor(() => expect(container.querySelector('.active-tile')).toBeNull());
});
