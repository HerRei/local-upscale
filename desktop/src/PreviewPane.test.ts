// @vitest-environment jsdom
import { cleanup, render, waitFor } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import { boundedPreviewSize, canvasTileRect } from './lib/progressive-preview';
import PreviewPane from './PreviewPane.svelte';
import type { MediaItem, WorkerEnvelope } from './lib/types';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
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
    addFiles: async () => {},
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
      addFiles: async () => {},
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
      addFiles: async () => {},
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
    addFiles: async () => {},
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
    addFiles: async () => {},
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
    activityLabel: 'Encoding clip',
    resultPreview: '',
    completedVideoOutput: '',
    addFiles: async () => {},
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
