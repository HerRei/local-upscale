// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import ComparePane from './ComparePane.svelte';
import type { CompareItem } from './lib/compareOutputs';

afterEach(cleanup);
const item = (id: string, media: string, status: CompareItem['status'] = 'ready'): CompareItem => ({
  key: id,
  pick: { kind: 'output', media_id: media, job_id: id },
  media_name: media,
  label: id,
  meta: '',
  preview_data_url: status === 'ready' ? `data:image/png;base64,${id}` : '',
  width: 100,
  height: 100,
  status,
  error: status === 'error' ? 'Decode failed' : '',
});
const props = {
  layout: 'slider' as const,
  setLayout: vi.fn(),
  swapItems: vi.fn(),
  removeItem: vi.fn(),
  close: vi.fn(),
  asTab: true,
};
it('shows the slider in slot order and provides swap and move controls', async () => {
  render(ComparePane, {
    ...props,
    items: [item('one', 'a'), item('two', 'a'), item('three', 'a')],
  });
  expect(screen.getByRole('slider', { name: /Divider between slot 1 and slot 2/ })).toBeTruthy();
  await fireEvent.click(screen.getByRole('button', { name: 'Swap slots 1 and 2' }));
  await fireEvent.click(screen.getByRole('button', { name: 'Move three to slider slot 2' }));
  expect(props.swapItems).toHaveBeenCalledWith(0, 1);
  expect(props.swapItems).toHaveBeenCalledWith(1, 2);
});
it('uses boxes across inputs and displays each slot state', () => {
  render(ComparePane, {
    ...props,
    items: [item('one', 'a', 'loading'), item('two', 'b', 'missing'), item('three', 'a', 'error')],
  });
  expect(screen.queryByRole('slider')).toBeNull();
  expect(screen.getByText('Loading…')).toBeTruthy();
  expect(screen.getByText('File missing')).toBeTruthy();
  expect(screen.getByText('Decode failed')).toBeTruthy();
});

it('debounces region requests while zooming and clears them on leaving Compare', async () => {
  vi.useFakeTimers();
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width: 300,
    height: 300,
    left: 0,
    top: 0,
    right: 300,
    bottom: 300,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  const requestRegions = vi.fn().mockResolvedValue(undefined);
  const clearRegions = vi.fn().mockResolvedValue(undefined);
  const entries = [item('one', 'a'), item('two', 'a')].map((entry) => ({
    ...entry,
    width: 2000,
    height: 2000,
    preview_width: 300,
    preview_height: 300,
  }));
  try {
    const view = render(ComparePane, {
      ...props,
      items: entries,
      generation: 1,
      requestRegions,
      clearRegions,
    });
    await Promise.resolve();
    const canvas = screen.getByRole('region', { name: 'Compare canvas' });
    await fireEvent.wheel(canvas, { deltaY: -1, clientX: 150, clientY: 150 });
    await fireEvent.wheel(canvas, { deltaY: -1, clientX: 150, clientY: 150 });
    await fireEvent.wheel(canvas, { deltaY: -1, clientX: 150, clientY: 150 });
    expect(requestRegions).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(160);
    expect(requestRegions).toHaveBeenCalledTimes(1);
    const bounds = requestRegions.mock.calls[0][1][0];
    expect(bounds).toMatchObject({ target_width: expect.any(Number) });
    await view.rerender({
      items: [
        { ...entries[0], region: { bounds, data_url: 'data:image/png;base64,crop' } },
        entries[1],
      ],
    });
    expect(canvas.querySelector('.region-image')?.getAttribute('src')).toBe(
      'data:image/png;base64,crop',
    );
    await fireEvent.wheel(canvas, { deltaY: -1, clientX: 150, clientY: 150 });
    expect(canvas.querySelector('.region-image')).toBeNull();
    view.unmount();
    await Promise.resolve();
    await Promise.resolve();
    expect(clearRegions).toHaveBeenCalledTimes(1);
  } finally {
    rect.mockRestore();
    vi.useRealTimers();
  }
});

it('names the compared pictures by their ends, with the full names on hover', () => {
  const first = 'IMG_490C1E18-B4D1-47E5-AC62-FD97F5A26811.jpeg';
  const second = 'Holiday at the lake, evening light, second attempt.png';
  const { container } = render(ComparePane, {
    ...props,
    items: [item('one', first), item('two', first), item('three', second)],
  });
  // The slider holds two versions of one picture; the chips name the picture of each slot.
  for (const name of [first, second]) {
    expect(container.querySelector(`.compare-slot .file-name[title="${name}"]`)).toBeTruthy();
    expect(container.querySelector(`.compare-header .file-name[title="${name}"]`)).toBeTruthy();
  }
});

it('opens two videos in the image slider with one shared transport, then boxes for three, and releases sources', async () => {
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  const prepareVideo = vi.fn(async (_generation: number, slot: number) => ({
    original_url: `https://fixture/${slot}.mp4`,
    enhanced_url: '',
  }));
  const cancelVideo = vi.fn(async () => {});
  const entries = [item('one', 'v'), item('two', 'v'), item('three', 'v')].map((entry) => ({
    ...entry,
    media_kind: 'video',
    fps: 60,
  }));
  try {
    const view = render(ComparePane, {
      ...props,
      items: entries.slice(0, 2),
      generation: 1,
      prepareVideo,
      cancelVideo,
    });
    expect(screen.getByRole('slider', { name: 'Divider between slot 1 and slot 2' })).toBeTruthy();
    expect(view.container.querySelectorAll('video')).toHaveLength(2);
    expect(screen.getAllByRole('group', { name: 'Video playback controls' })).toHaveLength(1);
    await vi.waitFor(() => expect(prepareVideo).toHaveBeenCalledTimes(2));
    expect(prepareVideo).toHaveBeenCalledWith(1, 0, expect.any(String), false, false);
    const old = [...view.container.querySelectorAll('video')];
    await view.rerender({ items: entries, generation: 2 });
    expect(view.container.querySelectorAll('.boxes video')).toHaveLength(3);
    expect(screen.queryByRole('slider', { name: 'Divider between slot 1 and slot 2' })).toBeNull();
    expect(old.every((video) => !video.hasAttribute('src'))).toBe(true);
    const videos = [...view.container.querySelectorAll('video')];
    view.unmount();
    expect(videos.every((video) => !video.hasAttribute('src'))).toBe(true);
    expect(view.container.querySelectorAll('video')).toHaveLength(0);
    expect(pause).toHaveBeenCalled();
    expect(load).toHaveBeenCalled();
  } finally {
    pause.mockRestore();
    load.mockRestore();
  }
});

it('leaves unavailable sides in their boxes while preparing other videos and retries deferred conversion when the job ends', async () => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  const prepareVideo = vi.fn(
    async (_generation: number, slot: number, _request: string, _force = false, busy = false) => {
      if (slot === 0 && busy)
        throw new Error(
          'This video needs a playback conversion. It will load when the current video is done.',
        );
      return { original_url: `https://fixture/${slot}.mp4`, enhanced_url: '' };
    },
  );
  const entries = [item('one', 'v'), item('two', 'v')].map((entry) => ({
    ...entry,
    media_kind: 'video',
  }));
  try {
    const view = render(ComparePane, {
      ...props,
      items: entries,
      generation: 1,
      engineBusy: true,
      prepareVideo,
      cancelVideo: async () => {},
    });
    await vi.waitFor(() => expect(prepareVideo).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/It will load when the current video is done/)).toBeTruthy();
    expect(view.container.querySelectorAll('video[src]')).toHaveLength(1);
    await view.rerender({ engineBusy: false });
    await vi.waitFor(() => expect(prepareVideo).toHaveBeenCalledTimes(4));
    expect(view.container.querySelectorAll('video[src]')).toHaveLength(2);
    view.unmount();
  } finally {
    vi.restoreAllMocks();
  }
});

it('releases hidden Compare sources, cancels pending preparation and ignores a late descriptor', async () => {
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  let finish!: (source: { original_url: string; enhanced_url: string }) => void;
  const prepareVideo = vi.fn(
    () =>
      new Promise<{ original_url: string; enhanced_url: string }>((resolve) => {
        finish = resolve;
      }),
  );
  const cancelVideo = vi.fn(async () => {});
  const entries = [item('one', 'v'), item('two', 'v')].map((entry) => ({
    ...entry,
    media_kind: 'video',
  }));
  try {
    const view = render(ComparePane, {
      ...props,
      items: entries,
      generation: 1,
      prepareVideo,
      cancelVideo,
    });
    await vi.waitFor(() => expect(prepareVideo).toHaveBeenCalledOnce());
    await view.rerender({ compactHidden: true });
    expect(cancelVideo).toHaveBeenCalledOnce();
    finish({ original_url: 'https://fixture/late.mp4', enhanced_url: '' });
    await Promise.resolve();
    await Promise.resolve();
    expect(view.container.querySelectorAll('video[src]')).toHaveLength(0);
    expect(prepareVideo).toHaveBeenCalledOnce();
    view.unmount();
  } finally {
    pause.mockRestore();
    load.mockRestore();
  }
});

it('drives the shared transport from video frame callbacks and cancels the clock on close', async () => {
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    Object.defineProperty(this, 'paused', { configurable: true, value: true });
  });
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(async function (
    this: HTMLMediaElement,
  ) {
    Object.defineProperty(this, 'paused', { configurable: true, value: false });
  });
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  let frame: VideoFrameRequestCallback | undefined;
  const request = vi.fn((callback: VideoFrameRequestCallback) => {
    frame = callback;
    return 1;
  });
  const cancel = vi.fn();
  const entries = [item('one', 'v'), item('two', 'v')].map((entry, index) => ({
    ...entry,
    media_kind: 'video',
    fps: index ? 60 : 15,
  }));
  try {
    const view = render(ComparePane, {
      ...props,
      items: entries,
      generation: 1,
      prepareVideo: async (_g, index) => ({
        original_url: `https://fixture/${index}.mp4`,
        enhanced_url: '',
      }),
      cancelVideo: async () => {},
    });
    await vi.waitFor(() => expect(view.container.querySelectorAll('video[src]')).toHaveLength(2));
    const videos = [...view.container.querySelectorAll('video')];
    for (const video of videos) {
      Object.defineProperties(video, {
        duration: { value: 4 },
        readyState: { value: 4 },
        requestVideoFrameCallback: { value: request },
        cancelVideoFrameCallback: { value: cancel },
      });
      await fireEvent.loadedData(video);
    }
    const canvas = screen.getByRole('region', { name: 'Compare canvas' });
    await fireEvent.keyDown(canvas, { key: ' ' });
    await vi.waitFor(() => expect(play).toHaveBeenCalledTimes(2));
    expect(request).toHaveBeenCalled();
    videos[0].currentTime = 1;
    videos[1].currentTime = 0.9;
    frame?.(0, {} as VideoFrameCallbackMetadata);
    expect(videos[1].currentTime).toBe(1);
    await fireEvent.click(screen.getByRole('button', { name: 'Playback speed' }));
    expect(videos.map((video) => video.playbackRate)).toEqual([1.5, 1.5]);
    await fireEvent.click(screen.getByRole('button', { name: 'Mute comparison' }));
    expect(videos.every((video) => video.muted)).toBe(true);
    await fireEvent.keyDown(canvas, { key: 'ArrowRight' });
    expect(videos.map((video) => video.currentTime)).toEqual([1 + 1 / 60, 1 + 1 / 60]);
    await fireEvent.keyDown(canvas, { key: ' ' });
    view.unmount();
    expect(cancel).toHaveBeenCalledWith(1);
    expect(videos.every((video) => !video.hasAttribute('src'))).toBe(true);
  } finally {
    pause.mockRestore();
    play.mockRestore();
    load.mockRestore();
  }
});
