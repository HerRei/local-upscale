// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import BenchmarkStudio from './BenchmarkStudio.svelte';
import type { BenchmarkRender, WorkerEnvelope } from './lib/types';

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const capture: BenchmarkRender = {
  scene_id: 's2-gallery', device: 'mps', input_width: 512, input_height: 512,
  output_width: 2048, output_height: 2048,
  input_data_url: 'data:image/jpeg;base64,source', output_data_url: 'data:image/jpeg;base64,output'
};
it('waits for real pixels and presents the completed render without a comparison slider', async () => {
  const view = render(BenchmarkStudio, { running: true });
  expect(screen.getByText('Preparing model tiles…')).toBeTruthy();
  expect(screen.queryByRole('img')).toBeNull();
  await view.rerender({ running: false, renders: [capture] });
  expect(screen.getByAltText('Completed SPAN benchmark render').getAttribute('src')).toBe(capture.output_data_url);
  expect(screen.queryByRole('slider')).toBeNull();
});
it('draws completed model pixels in their reported square and outlines the active tile', async () => {
  const drawImage = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ fillRect: vi.fn(), drawImage, strokeRect: vi.fn() } as unknown as CanvasRenderingContext2D);
  vi.stubGlobal('Image', class { onload?: () => void; set src(_value: string) { this.onload?.(); } });
  const data = { job_id: 'benchmark-1', device: 'mps', scene_id: 's1-classroom',
    phase: 'started', output_x: 0, output_y: 0, output_width: 1024, output_height: 1024,
    image_width: 2048, image_height: 2048, completed_tiles: 0, total_tiles: 4 };
  const tileMessage: WorkerEnvelope = { type: 'benchmark_tile', data };
  const view = render(BenchmarkStudio, { running: true, tileMessage });
  expect(await screen.findByLabelText('Tile currently being rendered')).toBeTruthy();
  expect(drawImage).not.toHaveBeenCalled();
  await view.rerender({ running: true, tileMessage: { ...tileMessage, data: { ...data, phase: 'completed', completed_tiles: 1, jpeg_base64: 'real-model-jpeg' } } });
  await waitFor(() => expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 480, 480));
  expect(screen.getByText('Compute · 1 / 4 tiles')).toBeTruthy();
  expect(screen.queryByLabelText('Tile currently being rendered')).toBeNull();
});
