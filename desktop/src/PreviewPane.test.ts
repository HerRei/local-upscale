// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, describe, expect, it, onTestFinished, vi } from 'vitest';
import { boundedPreviewSize, canvasTileRect } from './lib/progressive-preview';
import PreviewPane from './PreviewPane.svelte';
import type { MediaItem, WorkerEnvelope } from './lib/types';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('defers an unprepared input during a video run and retries when the engine is free', async () => {
  const api = await import('./lib/api');
  vi.spyOn(api, 'isTauri').mockReturnValue(true);
  const probe = vi.spyOn(api, 'probePath').mockResolvedValue();
  const media: MediaItem = {
    id: 'waiting',
    path: '/waiting.png',
    name: 'waiting.png',
    kind: 'image',
    width: 0,
    height: 0,
    frame_count: 1,
    fps: 0,
    duration_seconds: 0,
    preview_data_url: '',
    probe_status: 'ready',
    error: '',
    selected: true,
  };
  const pane = render(PreviewPane, {
    selectedMedia: media,
    resultPreview: '',
    completedVideoOutput: '',
    videoEngineBusy: true,
  });
  expect(
    await screen.findByText(
      'This preview needs the engine, which is busy with the video. It will load when the video is done.',
    ),
  ).toBeTruthy();
  expect(probe).not.toHaveBeenCalled();
  await pane.rerender({ videoEngineBusy: false });
  await waitFor(() => expect(probe).toHaveBeenCalledWith('/waiting.png'));
});

it('rejects denoise tiles arriving after the upscale stage established its raster', async () => {
  const drawImage = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    fillRect: vi.fn(),
    clearRect: vi.fn(),
    drawImage,
  } as unknown as CanvasRenderingContext2D);
  vi.stubGlobal(
    'Image',
    class {
      onload?: () => void;
      set src(_value: string) {
        this.onload?.();
      }
    },
  );
  const selectedMedia: MediaItem = {
    id: 'scan',
    path: '/scan.png',
    name: 'scan.png',
    kind: 'image',
    width: 1982,
    height: 1361,
    frame_count: 1,
    fps: 0,
    duration_seconds: 0,
    preview_data_url: 'data:image/jpeg;base64,source',
    probe_status: 'ready',
    error: '',
    selected: true,
  };
  const { component, container } = render(PreviewPane, {
    selectedMedia,
    resultPreview: '',
    completedVideoOutput: '',
  });
  const data = {
    job_id: 'two-stages',
    stage_index: 1,
    phase: 'started',
    active_tile_size: 256,
    image_width: 7928,
    image_height: 5444,
    output_x: 7168,
    output_y: 4096,
    output_width: 760,
    output_height: 1024,
  };
  component.queueProgressiveTile({ type: 'tile_update', data }, 'two-stages');
  await waitFor(() => expect(container.querySelector('.active-tile')).toBeTruthy());
  const outline = (container.querySelector('.active-tile') as HTMLElement).style.cssText;
  const calls = drawImage.mock.calls.length;
  component.queueProgressiveTile(
    {
      type: 'tile_update',
      data: {
        ...data,
        stage_index: 0,
        phase: 'completed',
        image_width: 1982,
        image_height: 1361,
        output_x: 1792,
        output_y: 1024,
        output_width: 190,
        output_height: 256,
        jpeg_base64: 'late-denoise',
      },
    },
    'two-stages',
  );
  await Promise.resolve();
  expect(drawImage).toHaveBeenCalledTimes(calls);
  expect((container.querySelector('.active-tile') as HTMLElement).style.cssText).toBe(outline);
});

it.each([1, 4])(
  'reconstructs the %dx raster when opening a running image for the first time',
  async (scale) => {
    const fillRect = vi.fn();
    const drawImage = vi.fn();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      fillRect,
      drawImage,
      clearRect: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    vi.stubGlobal(
      'Image',
      class {
        onload?: () => void;
        set src(_value: string) {
          this.onload?.();
        }
      },
    );
    const first: MediaItem = {
      id: 'first',
      path: '/first.png',
      name: 'first.png',
      kind: 'image',
      width: 600,
      height: 800,
      frame_count: 1,
      fps: 0,
      duration_seconds: 0,
      preview_data_url: 'data:image/jpeg;base64,first',
      probe_status: 'ready',
      error: '',
      selected: true,
    };
    const app = render(PreviewPane, {
      selectedMedia: first,
      resultPreview: '',
      completedVideoOutput: '',
    });
    await app.rerender({
      selectedMedia: {
        ...first,
        id: 'running',
        width: 1982,
        height: 1361,
        path: '/running.png',
        name: 'running.png',
        preview_data_url: 'data:image/jpeg;base64,running',
      },
      processing: true,
      activeTileSize: 256,
    });
    const data = {
      job_id: 'running-job',
      phase: 'completed',
      jpeg_base64: 'edge-tile',
      output_x: 1792 * scale,
      output_y: 1024 * scale,
      output_width: 190 * scale,
      output_height: 256 * scale,
      image_width: 1982 * scale,
      image_height: 1361 * scale,
    };
    // A legacy worker's first JPEG has no tile-size field. The active job's
    // reported tile size is still available when this image was never viewed.
    app.component.queueProgressiveTile({ type: 'tile_update', data }, 'running-job');
    await waitFor(() => expect(drawImage).toHaveBeenCalledTimes(2));
    expect(fillRect).toHaveBeenCalledTimes(1 + 8 * 6);
    const size = boundedPreviewSize({ width: data.image_width, height: data.image_height });
    const rect = canvasTileRect(data, size)!;
    expect(fillRect.mock.calls[1 + 4 * 8 + 7]).toEqual([rect.x, rect.y, rect.width, rect.height]);
    expect(drawImage.mock.calls[1].slice(1)).toEqual([rect.x, rect.y, rect.width, rect.height]);
    app.component.queueProgressiveTile(
      { type: 'tile_update', data: { ...data, jpeg_base64: '', phase: 'started' } },
      'running-job',
    );
    await waitFor(() => expect(app.container.querySelector('.active-tile')).toBeTruthy());
    expect(
      parseFloat((app.container.querySelector('.active-tile') as HTMLElement).style.width),
    ).toBeCloseTo((rect.width / size.width) * 100);
  },
);

it.each([1, 4])(
  'follows every fast %dx tile and keeps a late JPEG behind the current outline',
  async (scale) => {
    const drawImage = vi.fn();
    const fillRect = vi.fn();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      fillRect,
      clearRect: vi.fn(),
      drawImage,
    } as unknown as CanvasRenderingContext2D);
    vi.stubGlobal(
      'Image',
      class {
        onload?: () => void;
        set src(_value: string) {
          this.onload?.();
        }
      },
    );
    // All three started events arrive in less than the former 75 ms throttle.
    vi.spyOn(performance, 'now').mockReturnValue(1000);
    const selectedMedia: MediaItem = {
      id: 'scan',
      path: '/scan.png',
      name: 'scan.png',
      kind: 'image',
      width: 1982,
      height: 1361,
      frame_count: 1,
      fps: 0,
      duration_seconds: 0,
      preview_data_url: 'data:image/jpeg;base64,source',
      probe_status: 'ready',
      error: '',
      selected: true,
    };
    const { component, container } = render(PreviewPane, {
      selectedMedia,
      resultPreview: '',
      completedVideoOutput: '',
    });
    const data = {
      job_id: 'fast',
      phase: 'started',
      active_tile_size: 256,
      output_x: 0,
      output_y: 0,
      output_width: 256 * scale,
      output_height: 256 * scale,
      image_width: 1982 * scale,
      image_height: 1361 * scale,
    };
    const send = (changes: Record<string, unknown>) =>
      component.queueProgressiveTile(
        { type: 'tile_update', data: { ...data, ...changes } },
        'fast',
      );
    const size = boundedPreviewSize({ width: data.image_width, height: data.image_height });
    send({});
    await waitFor(() => expect(container.querySelector('.progressive-image.visible')).toBeTruthy());
    send({ output_x: 256 * scale });
    send({ output_x: 512 * scale });
    const rect = canvasTileRect({ ...data, output_x: 512 * scale }, size)!;
    await waitFor(() =>
      expect(
        parseFloat((container.querySelector('.active-tile') as HTMLElement).style.left),
      ).toBeCloseTo((rect.x / size.width) * 100),
    );
    // Metadata and pixels from the first tile cannot clear or rewind tile three.
    send({ phase: 'completed' });
    send({ phase: 'completed', jpeg_base64: 'first-tile' });
    await waitFor(() => expect(drawImage).toHaveBeenCalledTimes(2)); // source + real tile
    expect(
      parseFloat((container.querySelector('.active-tile') as HTMLElement).style.left),
    ).toBeCloseTo((rect.x / size.width) * 100);
    send({ phase: 'completed', output_x: 512 * scale });
    await waitFor(() => expect(container.querySelector('.active-tile')).toBeNull());
    expect(fillRect).toHaveBeenCalledTimes(1 + 8 * 6); // source surface + actual grid
  },
);
it('paints the asynchronous JPEG after empty completion metadata, and rejects an older frame', async () => {
  const drawImage = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    fillRect: vi.fn(),
    clearRect: vi.fn(),
    drawImage,
  } as unknown as CanvasRenderingContext2D);
  vi.stubGlobal(
    'Image',
    class {
      onload?: () => void;
      set src(_value: string) {
        this.onload?.();
      }
    },
  );
  const selectedMedia: MediaItem = {
    id: 'source',
    path: '/source.mp4',
    name: 'source.mp4',
    kind: 'video',
    width: 100,
    height: 100,
    frame_count: 2,
    fps: 1,
    duration_seconds: 2,
    preview_data_url: 'data:image/jpeg;base64,source',
    probe_status: 'ready',
    error: '',
    selected: true,
  };
  const { component, container } = render(PreviewPane, {
    selectedMedia,
    resultPreview: '',
    completedVideoOutput: '',
  });
  const data = {
    job_id: 'job',
    phase: 'started',
    frame_index: 0,
    output_x: 0,
    output_y: 0,
    output_width: 200,
    output_height: 200,
    image_width: 400,
    image_height: 400,
  };
  const send = (changes: Record<string, unknown>) =>
    component.queueProgressiveTile(
      { type: 'tile_update', data: { ...data, ...changes } } as WorkerEnvelope,
      'job',
    );
  send({});
  await waitFor(() => expect(container.querySelector('.progressive-image.visible')).toBeTruthy());
  send({ output_x: 200 });
  await waitFor(() =>
    expect((container.querySelector('.active-tile') as HTMLElement)?.style.left).toBe('50%'),
  );
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
  const style = document.createElement('style');
  style.textContent = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8');
  document.head.append(style);
  onTestFinished(() => style.remove());
  const drawImage = vi.fn();
  const context = { fillStyle: '', fillRect: vi.fn(), clearRect: vi.fn(), drawImage };
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    context as unknown as CanvasRenderingContext2D,
  );
  vi.stubGlobal(
    'Image',
    class {
      onload?: () => void;
      set src(_value: string) {
        this.onload?.();
      }
    },
  );
  const selectedMedia: MediaItem = {
    id: 'source',
    path: '/portrait.mov',
    name: 'portrait.mov',
    kind: 'video',
    width: 2160,
    height: 3840,
    frame_count: 3,
    fps: 1,
    duration_seconds: 3,
    preview_data_url: 'data:image/jpeg;base64,thumbnail',
    probe_status: 'ready',
    error: '',
    selected: true,
  };
  const { component, container } = render(PreviewPane, {
    selectedMedia,
    processing: true,
    resultPreview: '',
    completedVideoOutput: '',
  });
  const geometry = {
    job_id: 'job',
    frame_index: 1,
    output_x: 0,
    output_y: 0,
    output_width: 256,
    output_height: 256,
    image_width: 8640,
    image_height: 15360,
  };
  const tile = (changes: Record<string, unknown>) =>
    component.queueProgressiveTile(
      { type: 'tile_update', data: { ...geometry, ...changes } },
      'job',
    );
  const source = (frame: number, jpeg: string, job = 'job') =>
    component.queueVideoSource(
      { type: 'live_preview_frame', data: { job_id: job, frame_index: frame, jpeg_base64: jpeg } },
      'job',
    );
  tile({ phase: 'started' });
  await waitFor(() => expect(container.querySelector('.source-image.waiting-frame')).toBeTruthy());
  // The video grid is translucent; it cannot cover the source with solid black.
  expect(context.fillStyle).toMatch(/^#101721(38|60)$/);
  tile({ phase: 'completed', jpeg_base64: 'model-tile' });
  await waitFor(() => expect(drawImage).toHaveBeenCalledTimes(1));
  const clears = context.clearRect.mock.calls.length;
  source(1, 'frame-two');
  await waitFor(() =>
    expect(container.querySelector('.source-image')?.getAttribute('src')).toContain('frame-two'),
  );
  expect(container.querySelector('.source-image.waiting-frame')).toBeNull();
  expect(context.clearRect).toHaveBeenCalledTimes(clears);
  expect(drawImage).toHaveBeenCalledTimes(1);
  source(0, 'old-frame');
  source(2, 'wrong-job', 'another-job');
  expect(container.querySelector('.source-image')?.getAttribute('src')).toContain('frame-two');
  source(2, 'frame-three');
  await waitFor(() =>
    expect(container.querySelector('.source-image')?.getAttribute('src')).toContain('frame-three'),
  );
  source(1, 'late-frame-two');
  expect(container.querySelector('.source-image')?.getAttribute('src')).toContain('frame-three');
});

it('shows overlapping SeedVR2 regions over the source without inventing a HAT grid', async () => {
  const fillRect = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    clearRect: vi.fn(),
    fillRect,
  } as unknown as CanvasRenderingContext2D);
  const selectedMedia: MediaItem = {
    id: 'source',
    path: '/portrait.mov',
    name: 'portrait.mov',
    kind: 'video',
    width: 2160,
    height: 3840,
    frame_count: 5,
    fps: 1,
    duration_seconds: 5,
    preview_data_url: 'data:image/jpeg;base64,thumbnail',
    probe_status: 'ready',
    error: '',
    selected: true,
  };
  const { component, container } = render(PreviewPane, {
    selectedMedia,
    processing: true,
    resultPreview: '',
    completedVideoOutput: '',
  });
  component.queueVideoSource(
    {
      type: 'live_preview_frame',
      data: { job_id: 'job', frame_index: 0, jpeg_base64: 'current-frame' },
    },
    'job',
  );
  const data = {
    job_id: 'job',
    frame_index: 0,
    processing_stage: 'encoding',
    phase: 'started',
    output_x: 112,
    output_y: 448,
    output_width: 128,
    output_height: 6,
    image_width: 256,
    image_height: 454,
  };
  component.queueProgressiveTile({ type: 'tile_update', data }, 'job');
  await waitFor(() => expect(container.querySelector('.active-tile')).toBeTruthy());
  expect((container.querySelector('.active-tile') as HTMLElement).style.left).toBe('43.75%');
  expect(fillRect).not.toHaveBeenCalled();
  expect(container.querySelector('.source-image')?.getAttribute('src')).toContain('current-frame');
  component.queueProgressiveTile(
    { type: 'tile_update', data: { ...data, phase: 'completed' } },
    'job',
  );
  await waitFor(() => expect(container.querySelector('.active-tile')).toBeNull());
});

describe('image comparison', () => {
  const photo: MediaItem = {
    id: 'photo',
    path: '/photo.jpg',
    name: 'photo.jpg',
    kind: 'image',
    width: 2000,
    height: 1000,
    frame_count: 1,
    fps: 0,
    duration_seconds: 0,
    preview_data_url: 'data:image/jpeg;base64,source',
    probe_status: 'ready',
    error: '',
    selected: true,
  };

  async function renderComparison() {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 800,
      height: 600,
      top: 0,
      left: 0,
      right: 800,
      bottom: 600,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    const rendered = render(PreviewPane, {
      selectedMedia: photo,
      resultPreview: 'data:image/png;base64,result',
      completedVideoOutput: '',
    });
    // 2000 x 1000 fitted into 800 x 600 minus the 56 px margin.
    await waitFor(() =>
      expect(rendered.container.querySelector<HTMLElement>('.image-stage')?.style.width).toBe(
        '744px',
      ),
    );
    const clip = () =>
      Number(
        /inset\(0 0 0 ([\d.]+)%\)/.exec(
          rendered.container.querySelector('.result-image')?.getAttribute('style') ?? '',
        )?.[1],
      );
    return { ...rendered, clip };
  }

  const pair = {
    selectedMedia: photo,
    resultPreview: 'data:image/png;base64,showing',
    completedVideoOutput: '',
    referenceIsResult: true,
    referencePreview: 'data:image/png;base64,reference',
    referenceLabel: 'SwinIR-M · 2× output',
    showingLabel: 'HAT-L · 4× output',
  };

  it('labels both results, keeps M for the real Original and the slider values in step', async () => {
    const { container, rerender } = await renderComparison();
    await rerender(pair);
    expect(screen.getByRole('radio', { name: 'Reference' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Showing' })).toBeTruthy();
    const labels = container.querySelector('.divider-labels')?.textContent ?? '';
    expect(labels).toContain('SwinIR-M · 2× output');
    expect(labels).toContain('HAT-L · 4× output');
    expect(labels).not.toContain('enlarged');
    const slider = screen.getByRole('slider');
    const before = Number(slider.getAttribute('aria-valuenow'));
    await fireEvent.keyDown(slider, { key: 'ArrowRight' });
    const now = Number(slider.getAttribute('aria-valuenow'));
    expect(now).toBeGreaterThan(before);
    expect(slider.getAttribute('aria-valuetext')).toBe(
      `${now}% SwinIR-M · 2× output, ${100 - now}% HAT-L · 4× output`,
    );
    // The source stays the real Original under the reference layer.
    expect((container.querySelector('.source-image') as HTMLImageElement).src).toContain('source');
    expect((container.querySelector('.reference-image') as HTMLImageElement).src).toContain(
      'reference',
    );
    await fireEvent.keyDown(window, { key: 'm' });
    expect(container.querySelector('.reference-image')).toBeNull();
    expect(container.querySelector('.original-chip')?.textContent).toBe('Original');
    await fireEvent.keyUp(window, { key: 'm' });
    expect(container.querySelector('.reference-image')).toBeTruthy();
  });

  it('shows the reference result in the Reference view without the Original chip', async () => {
    const { container, rerender } = await renderComparison();
    await rerender(pair);
    await fireEvent.click(screen.getByRole('radio', { name: 'Reference' }));
    expect(container.querySelector('.reference-image')).toBeTruthy();
    expect(container.querySelector('.original-chip')?.textContent).toBe('SwinIR-M · 2× output');
    await fireEvent.keyDown(window, { key: 'm' });
    expect(container.querySelector('.original-chip')?.textContent).toBe('Original');
    expect(container.querySelector('.reference-image')).toBeNull();
  });

  it('shows a side that is decoding or failed as such, never the Original under it', async () => {
    const { container, rerender } = await renderComparison();
    await rerender({
      ...pair,
      resultPreview: '',
      showingState: 'loading',
      referencePreview: '',
      referenceState: 'error',
      referenceError: 'truncated file',
    });
    expect(screen.getByRole('slider')).toBeTruthy();
    expect(container.querySelector('.result-image')).toBeNull();
    const placeholders = [...container.querySelectorAll('.side-placeholder')].map(
      (element) => element.textContent?.trim() ?? '',
    );
    expect(placeholders).toEqual([
      'SwinIR-M · 2× output: truncated file. The saved file is unchanged.',
      'Loading HAT-L · 4× output…',
    ]);
  });

  it('shows a result with a different framing on its own, in its own shape', async () => {
    const { container, rerender } = await renderComparison();
    await rerender({
      selectedMedia: photo,
      resultPreview: 'data:image/png;base64,edit',
      completedVideoOutput: '',
      singleFrame: { width: 1000, height: 1000 },
      comparisonNote: 'Edit has a different framing from the original, so it is shown on its own.',
    });
    await waitFor(() =>
      expect(container.querySelector<HTMLElement>('.image-stage')?.style.width).toBe(
        container.querySelector<HTMLElement>('.image-stage')?.style.height,
      ),
    );
    expect(screen.queryByRole('slider')).toBeNull();
    expect(screen.queryByRole('radiogroup', { name: 'Comparison view' })).toBeNull();
    expect(screen.getByText(/different framing/)).toBeTruthy();
    const source = container.querySelector('.source-image') as HTMLElement;
    expect(source.classList.contains('covered')).toBe(true);
    await fireEvent.keyDown(window, { key: 'm' });
    expect(source.classList.contains('covered')).toBe(false);
    expect(source.classList.contains('contained')).toBe(true);
    expect(container.querySelector('.original-chip')?.textContent).toBe('Original');
  });

  it('keeps the divider in place on screen while zooming into a corner', async () => {
    const { container, clip } = await renderComparison();
    const divider = screen.getByRole('slider', { name: 'Before and after comparison' });
    expect(divider.style.left).toBe('400px');
    expect(clip()).toBe(50);
    const canvas = container.querySelector('.canvas-well') as HTMLElement;
    for (let step = 0; step < 16; step += 1) {
      await fireEvent.wheel(canvas, { deltaY: -100, clientX: 100, clientY: 300 });
    }
    expect(screen.getByRole('group', { name: 'Preview zoom' }).textContent).toContain('613%');
    // Same place, same size; the image moved under it toward its left part.
    expect(divider.style.left).toBe('400px');
    expect(divider.style.top).toBe('0px');
    expect(divider.style.height).toBe('600px');
    expect(clip()).toBeLessThan(25);
    expect(divider.getAttribute('aria-valuenow')).toBe('50');
    await fireEvent.keyDown(divider, { key: 'ArrowRight' });
    expect(divider.getAttribute('aria-valuenow')).toBe('51');
    expect(divider.style.left).toBe('408px');
  });

  it('switches between original, split and enhanced from the header and peeks with M', async () => {
    const { container, clip, component } = await renderComparison();
    const chip = () => container.querySelector('.original-chip');
    const divider = () => container.querySelector('.compare-line') as HTMLElement;
    const option = (name: string) => screen.getByRole('radio', { name });
    expect(screen.getByRole('radiogroup', { name: 'Comparison view' })).toBeTruthy();
    expect(option('Split').getAttribute('aria-checked')).toBe('true');

    await fireEvent.click(option('Enhanced'));
    expect(option('Enhanced').getAttribute('aria-checked')).toBe('true');
    expect(option('Split').getAttribute('aria-checked')).toBe('false');
    expect(clip()).toBe(0);
    expect(divider().classList.contains('held')).toBe(true);
    expect(divider().getAttribute('tabindex')).toBe('-1');
    expect(chip()).toBeNull();

    // Holding M peeks at the original from any view, then returns to it.
    await fireEvent.keyDown(window, { key: 'm' });
    expect(chip()?.textContent).toBe('Original');
    expect(clip()).toBe(100);
    expect(option('Enhanced').getAttribute('aria-checked')).toBe('true');
    await fireEvent.keyUp(window, { key: 'm' });
    expect(chip()).toBeNull();
    expect(clip()).toBe(0);
    // Command-M minimizes the window; it does not flip the comparison.
    await fireEvent.keyDown(window, { key: 'm', metaKey: true });
    expect(chip()).toBeNull();

    await fireEvent.click(option('Original'));
    expect(chip()?.textContent).toBe('Original');
    expect(clip()).toBe(100);
    await fireEvent.click(option('Split'));
    expect(chip()).toBeNull();
    expect(clip()).toBe(50);
    expect(divider().getAttribute('tabindex')).toBe('0');

    // The View menu sets the same switch; a new result starts in Split again.
    component.setComparisonView('enhanced');
    await waitFor(() => expect(clip()).toBe(0));
    component.resetView();
    await waitFor(() => expect(option('Split').getAttribute('aria-checked')).toBe('true'));
    expect(clip()).toBe(50);
  });

  it('marks an unfinished photo and shows its finished tiles from the mark', async () => {
    const tiles = {
      tiles: [
        [0, 0, 2000, 2000],
        [2000, 0, 2000, 2000],
      ] as [number, number, number, number][],
      width: 8000,
      height: 4000,
    };
    const { container, rerender } = await renderComparison();
    expect(container.querySelector('.unfinished-chip')).toBeNull();
    expect(container.querySelector('.unfinished-tiles')).toBeNull();
    await rerender({
      showingUnfinished: { id: 'kept', label: 'Unfinished · 2 of 8 tiles', tiles },
    });
    const mark = screen.getByRole('button', {
      name: 'Unfinished · 2 of 8 tiles. Show the finished tiles',
    });
    expect(mark.textContent?.trim()).toBe('Unfinished · 2 of 8 tiles');
    // Laid over the stage in pixels of the saved file, cut where the result is cut.
    const marks = container.querySelector('.unfinished-tiles') as SVGElement;
    expect(marks.getAttribute('viewBox')).toBe('0 0 8000 4000');
    expect(marks.getAttribute('style')).toContain('width: 744px');
    expect(marks.getAttribute('style')).toContain('clip-path: inset(0 0% 0 50%)');
    expect(marks.classList.contains('lit')).toBe(false);
    await fireEvent.pointerEnter(mark);
    expect(marks.classList.contains('lit')).toBe(true);
    await fireEvent.pointerLeave(mark);
    expect(marks.classList.contains('lit')).toBe(false);
    await fireEvent.click(mark);
    expect(mark.getAttribute('aria-pressed')).toBe('true');
    expect(marks.classList.contains('lit')).toBe(true);

    // The Original has no mark: the chip gives way to the Original chip, the tiles are cut away.
    await fireEvent.click(screen.getByRole('radio', { name: 'Original' }));
    expect(container.querySelector('.unfinished-chip')).toBeNull();
    expect(marks.getAttribute('style')).toContain('clip-path: inset(0 0% 0 100%)');
    await fireEvent.click(screen.getByRole('radio', { name: 'Enhanced' }));
    expect(container.querySelector('.unfinished-chip')).toBeTruthy();
    await fireEvent.keyDown(window, { key: 'm' });
    expect(container.querySelector('.unfinished-chip')).toBeNull();
    await fireEvent.keyUp(window, { key: 'm' });

    // Zoomed in, the marks grow with the picture instead of being scaled as a layer.
    await fireEvent.wheel(container.querySelector('.canvas-well') as HTMLElement, {
      deltaY: -100,
      clientX: 400,
      clientY: 300,
    });
    expect(parseFloat(marks.style.width)).toBeCloseTo(744 * 1.12);
    expect(marks.getAttribute('style')).not.toContain('scale(');
  });

  const kept = (id: string, finished: number) => ({
    id,
    label: `Unfinished · ${finished} of 8 tiles`,
    tiles: {
      tiles: Array.from(
        { length: finished },
        (_, index) => [index * 1000, 0, 1000, 1000] as [number, number, number, number],
      ),
      width: 4000,
      height: 2000,
    },
  });

  it('marks the unfinished side of a pair of results, on the side it shows', async () => {
    const { container, rerender } = await renderComparison();
    await rerender({ ...pair, referenceUnfinished: kept('reference', 1) });
    const marks = container.querySelector('.unfinished-tiles') as SVGElement;
    // The reference shows left of the divider, so its tiles are cut from the right.
    expect(marks.getAttribute('style')).toContain('clip-path: inset(0 50% 0 0%)');
    expect(container.querySelector('.unfinished-chip')?.textContent).toContain(
      'Unfinished · 1 of 8 tiles',
    );
    await fireEvent.click(screen.getByRole('radio', { name: 'Showing' }));
    expect(container.querySelector('.unfinished-chip')).toBeNull();
  });

  it('gives each of two unfinished results its own mark over its side, and its own tiles', async () => {
    const { container, rerender } = await renderComparison();
    await rerender({
      ...pair,
      referenceUnfinished: kept('reference', 1),
      showingUnfinished: kept('showing', 3),
    });
    const chips = () => [...container.querySelectorAll<HTMLElement>('.unfinished-chip')];
    // Reference left, Showing right, as the labels under them.
    expect(chips().map((chip) => chip.textContent?.trim())).toEqual([
      'Unfinished · 1 of 8 tiles',
      'Unfinished · 3 of 8 tiles',
    ]);
    expect(chips().map((chip) => chip.classList.contains('right'))).toEqual([false, true]);
    const [showingTiles, referenceTiles] = [
      ...container.querySelectorAll<SVGElement>('.unfinished-tiles'),
    ];
    expect(showingTiles.getAttribute('style')).toContain('clip-path: inset(0 0% 0 50%)');
    expect(referenceTiles.getAttribute('style')).toContain('clip-path: inset(0 50% 0 0%)');
    await fireEvent.pointerEnter(chips()[0]);
    expect(referenceTiles.classList.contains('lit')).toBe(true);
    expect(showingTiles.classList.contains('lit')).toBe(false);
    await fireEvent.pointerLeave(chips()[0]);

    // The Reference view still has the reference's mark, which shows its tiles.
    await fireEvent.click(screen.getByRole('radio', { name: 'Reference' }));
    expect(chips().map((chip) => chip.textContent?.trim())).toEqual(['Unfinished · 1 of 8 tiles']);
    await fireEvent.click(chips()[0]);
    expect(chips()[0].getAttribute('aria-pressed')).toBe('true');
    expect(referenceTiles.classList.contains('lit')).toBe(true);
    await fireEvent.click(screen.getByRole('radio', { name: 'Showing' }));
    expect(chips().map((chip) => chip.textContent?.trim())).toEqual(['Unfinished · 3 of 8 tiles']);
    expect(chips()[0].getAttribute('aria-pressed')).toBe('false');
  });

  it('ends a kept view of the tiles when another result takes the side', async () => {
    const { container, rerender } = await renderComparison();
    await rerender({ showingUnfinished: kept('first', 2) });
    const chip = () => container.querySelector('.unfinished-chip') as HTMLElement;
    await fireEvent.click(chip());
    expect(chip().getAttribute('aria-pressed')).toBe('true');
    // The same result again (a refresh of the list) stays as it was.
    await rerender({ showingUnfinished: kept('first', 2) });
    expect(chip().getAttribute('aria-pressed')).toBe('true');
    // Another unfinished result of the same photo starts with only its line.
    await rerender({ showingUnfinished: kept('second', 5) });
    expect(chip().getAttribute('aria-pressed')).toBe('false');
    expect(container.querySelector('.unfinished-tiles')?.classList.contains('lit')).toBe(false);
  });

  it('shows the zoom controls only while the pointer is over the image', async () => {
    const { container } = await renderComparison();
    const zoom = screen.getByRole('group', { name: 'Preview zoom' });
    const canvas = container.querySelector('.canvas-well') as HTMLElement;
    expect(zoom.classList.contains('shown')).toBe(false);
    await fireEvent.pointerEnter(canvas);
    expect(zoom.classList.contains('shown')).toBe(true);
    await fireEvent.pointerLeave(canvas);
    expect(zoom.classList.contains('shown')).toBe(false);
  });
});

it('hides the live tiles once a saved result was chosen while the image runs', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    fillStyle: '',
    fillRect: vi.fn(),
    clearRect: vi.fn(),
    drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  vi.stubGlobal(
    'Image',
    class {
      onload?: () => void;
      set src(_value: string) {
        this.onload?.();
      }
    },
  );
  const selectedMedia: MediaItem = {
    id: 'photo',
    path: '/photo.png',
    name: 'photo.png',
    kind: 'image',
    width: 100,
    height: 100,
    frame_count: 1,
    fps: 0,
    duration_seconds: 0,
    preview_data_url: 'data:image/jpeg;base64,source',
    probe_status: 'ready',
    error: '',
    selected: true,
  };
  const { component, container, rerender } = render(PreviewPane, {
    selectedMedia,
    resultPreview: '',
    completedVideoOutput: '',
    processing: true,
  });
  component.queueProgressiveTile(
    {
      type: 'tile_update',
      data: {
        job_id: 'trial-2',
        phase: 'started',
        output_x: 0,
        output_y: 0,
        output_width: 200,
        output_height: 200,
        image_width: 400,
        image_height: 400,
      },
    } as WorkerEnvelope,
    'trial-2',
  );
  await waitFor(() => expect(container.querySelector('.progressive-image.visible')).toBeTruthy());
  // The user picks the finished first trial: its saved pair replaces the tiles.
  await rerender({
    selectedMedia,
    resultPreview: 'data:image/jpeg;base64,first',
    completedVideoOutput: '',
    processing: true,
    showLiveTiles: false,
  });
  expect(container.querySelector('.progressive-image.visible')).toBeNull();
  expect(container.querySelector('.active-tile')).toBeNull();
  expect(container.querySelector('.result-image')?.getAttribute('src')).toContain('first');
  // A pinned pair whose Showing side is still decoding does not fall back to tiles either.
  await rerender({
    selectedMedia,
    resultPreview: '',
    completedVideoOutput: '',
    processing: true,
    showLiveTiles: false,
  });
  expect(container.querySelector('.progressive-image.visible')).toBeNull();
});

it('shows no chip over a video that is only waiting to play', () => {
  const clip = {
    id: 'clip',
    path: '/clip.mp4',
    name: 'clip.mp4',
    kind: 'video',
    width: 1920,
    height: 1080,
    frame_count: 48,
    fps: 24,
    duration_seconds: 2,
    preview_data_url: 'data:image/jpeg;base64,source',
    probe_status: 'ready',
    error: '',
    selected: true,
  } as MediaItem;
  const { container } = render(PreviewPane, {
    selectedMedia: clip,
    resultPreview: '',
    completedVideoOutput: '',
  });
  expect(container.querySelector('.source-image')).toBeTruthy();
  expect(container.textContent).not.toContain('processed locally');
  expect(container.querySelector('.labs-chip, .unfinished-chip')).toBeNull();
});

it('keeps the preview header free of result choosers', () => {
  const { container } = render(PreviewPane, {
    selectedMedia: undefined,
    resultPreview: '',
    completedVideoOutput: '',
  });
  expect(container.querySelector('.result-selectors')).toBeNull();
  expect(container.querySelector('.preview-header')?.hasAttribute('data-tauri-drag-region')).toBe(
    true,
  );
});

it('shows the viewer background swatch in the preview zoom bar and steps on click', async () => {
  const setViewerBackground = vi.fn();
  const selectedMedia: MediaItem = {
    id: 'bg-photo',
    path: '/bg-photo.png',
    name: 'bg-photo.png',
    kind: 'image',
    width: 400,
    height: 300,
    frame_count: 1,
    fps: 0,
    duration_seconds: 0,
    preview_data_url: 'data:image/jpeg;base64,source',
    probe_status: 'ready',
    error: '',
    selected: true,
  };
  render(PreviewPane, {
    selectedMedia,
    resultPreview: '',
    completedVideoOutput: '',
    viewerBackground: 'black',
    setViewerBackground,
  });
  const zoomGroup = screen.getByRole('group', { name: 'Preview zoom' });
  const button = zoomGroup.querySelector('button[aria-label="Viewer background: Black"]');
  expect(button).toBeTruthy();
  await fireEvent.click(button as HTMLElement);
  expect(setViewerBackground).toHaveBeenCalledWith('grey');
});
