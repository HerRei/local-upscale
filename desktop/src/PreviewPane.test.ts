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
