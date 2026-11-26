// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import ComparePane from './ComparePane.svelte';
import type { CompareItem } from './lib/compareOutputs';
import type { CompareVideoSources } from './lib/types';

function makeCombinedDescriptor(
  count: number,
  options: { durations?: number[]; fps?: number[] } = {},
): CompareVideoSources {
  const durations = options.durations ?? Array.from({ length: count }, () => 4);
  const fps = options.fps ?? Array.from({ length: count }, () => 30);
  return {
    video_url: 'https://fixture/combined.webm',
    width: 1280,
    height: count * 724 - 4,
    duration: Math.min(...durations),
    slots: Array.from({ length: count }, (_, index) => ({
      x: 0,
      y: index * 724,
      width: 1280,
      height: 720,
      fps: fps[index] ?? 30,
      duration: durations[index] ?? 4,
      frames: Math.round((durations[index] ?? 4) * (fps[index] ?? 30)),
      audio_url: `https://fixture/audio-${index}.webm`,
    })),
  };
}

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
it('shows each side’s recorded details below the comparison and omits missing fields', () => {
  render(ComparePane, {
    ...props,
    items: [
      {
        ...item('one', 'a'),
        details: [
          'Models: Fix → Upscale',
          'Scale: 4×',
          'Output: 400 × 400',
          'Time: 31s',
          'Device: cpu',
          'Date: 1 Jan 2026',
        ],
      },
      { ...item('two', 'a'), details: ['Models: Second'] },
    ],
  });
  const first = screen.getByRole('group', { name: 'Slot 1 details' });
  const second = screen.getByRole('group', { name: 'Slot 2 details' });
  expect(first.textContent).toContain('Models: Fix → Upscale');
  expect(first.textContent).toContain('Output: 400 × 400');
  expect(first.textContent).toContain('Device: cpu');
  expect(second.textContent).toContain('Models: Second');
  expect(second.textContent).not.toMatch(/unknown|Time:|Device:/i);
});
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

it('sets async decoding on comparison images', () => {
  const { container } = render(ComparePane, {
    ...props,
    items: [item('one', 'a'), item('two', 'a')],
  });
  const imgs = [...container.querySelectorAll('img')];
  expect(imgs.length).toBeGreaterThanOrEqual(2);
  expect(imgs.every((img) => img.getAttribute('decoding') === 'async')).toBe(true);
});

it('opens two videos in the image slider with one shared transport, then boxes for three, and releases sources', async () => {
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  let slotCount = 2;
  const prepareVideo = vi.fn(async () => makeCombinedDescriptor(slotCount));
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
    expect(view.container.querySelectorAll('video')).toHaveLength(1);
    expect(view.container.querySelectorAll('.image-stage canvas')).toHaveLength(2);
    expect(screen.getAllByRole('group', { name: 'Video playback controls' })).toHaveLength(1);
    expect(screen.getByRole('group', { name: 'Slot 1 details' }).textContent).toContain('one');
    expect(screen.getByRole('group', { name: 'Slot 2 details' }).textContent).toContain('two');
    await vi.waitFor(() => expect(prepareVideo).toHaveBeenCalledTimes(1));
    expect(prepareVideo).toHaveBeenCalledWith(1, expect.any(String), false, false);
    const video = view.container.querySelector('video')!;
    slotCount = 3;
    await view.rerender({ items: entries, generation: 2 });
    expect(view.container.querySelectorAll('video')).toHaveLength(1);
    expect(view.container.querySelectorAll('.boxes canvas')).toHaveLength(3);
    expect(screen.queryByRole('slider', { name: 'Divider between slot 1 and slot 2' })).toBeNull();
    view.unmount();
    expect(video.hasAttribute('src')).toBe(false);
    expect(view.container.querySelectorAll('video')).toHaveLength(0);
    expect(pause).toHaveBeenCalled();
    expect(load).toHaveBeenCalled();
  } finally {
    pause.mockRestore();
    load.mockRestore();
  }
});

it('defers playback conversion when the engine is busy and retries when the job ends', async () => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  const prepareVideo = vi.fn(
    async (_generation: number, _request: string, _force = false, busy = false) => {
      if (busy)
        throw new Error(
          'This video needs a playback conversion. It will load when the current video is done.',
        );
      return makeCombinedDescriptor(2);
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
    await vi.waitFor(() => expect(prepareVideo).toHaveBeenCalledTimes(1));
    expect(
      screen.getAllByText(/It will load when the current video is done/).length,
    ).toBeGreaterThan(0);
    expect(view.container.querySelector('video')?.hasAttribute('src')).toBe(false);
    await view.rerender({ engineBusy: false });
    await vi.waitFor(() => expect(prepareVideo).toHaveBeenCalledTimes(2));
    await vi.waitFor(() =>
      expect(view.container.querySelector('video')?.getAttribute('src')).toBe(
        'https://fixture/combined.webm',
      ),
    );
    view.unmount();
  } finally {
    vi.restoreAllMocks();
  }
});

it('releases hidden Compare sources, cancels pending preparation and ignores a late descriptor', async () => {
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  let finish!: (source: CompareVideoSources) => void;
  const prepareVideo = vi.fn(
    () =>
      new Promise<CompareVideoSources>((resolve) => {
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
    finish(makeCombinedDescriptor(2));
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

it('drives the shared transport and cropped canvases from one video clock and cancels on close', async () => {
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
  const drawImage = vi.fn();
  const getContext = vi
    .spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D);
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
      prepareVideo: async () => makeCombinedDescriptor(2, { fps: [15, 60] }),
      cancelVideo: async () => {},
    });
    await vi.waitFor(() => expect(view.container.querySelector('video[src]')).toBeTruthy());
    const video = view.container.querySelector('video')!;
    const audioEl = view.container.querySelector('audio')!;
    Object.defineProperties(video, {
      duration: { value: 4 },
      readyState: { value: 4 },
      videoWidth: { value: 1280 },
      videoHeight: { value: 1444 },
      requestVideoFrameCallback: { value: request },
      cancelVideoFrameCallback: { value: cancel },
    });
    Object.defineProperties(audioEl, {
      duration: { value: 4 },
      readyState: { value: 4 },
    });
    await fireEvent.loadedData(video);
    // Both side canvases crop their distinct rectangles from the single combined video snapshot.
    expect(drawImage).toHaveBeenCalledWith(video, 0, 0, 1280, 1444);
    expect(drawImage).toHaveBeenCalledWith(
      expect.any(HTMLCanvasElement),
      0,
      0,
      1280,
      720,
      0,
      0,
      1280,
      720,
    );
    expect(drawImage).toHaveBeenCalledWith(
      expect.any(HTMLCanvasElement),
      0,
      724,
      1280,
      720,
      0,
      0,
      1280,
      720,
    );

    const canvas = screen.getByRole('region', { name: 'Compare canvas' });
    await fireEvent.keyDown(canvas, { key: ' ' });
    await vi.waitFor(() => expect(play).toHaveBeenCalled());
    expect(request).toHaveBeenCalled();
    drawImage.mockClear();
    video.currentTime = 1;
    frame?.(0, {} as VideoFrameCallbackMetadata);
    expect(drawImage).toHaveBeenCalledTimes(3);
    await fireEvent.click(screen.getByRole('button', { name: 'Playback speed' }));
    expect(video.playbackRate).toBe(1.5);
    expect(video.muted).toBe(true);
    // Slot 1 sounds until another side, or none, is chosen.
    const audio = screen.getByRole('radiogroup', { name: 'Audio' });
    const sounding = () =>
      [...audio.querySelectorAll('[role="radio"]')].map(
        (radio) => radio.getAttribute('aria-checked') === 'true',
      );
    expect(sounding()).toEqual([true, false, false]);
    expect(audioEl.getAttribute('src')).toBe('https://fixture/audio-0.webm');
    expect(audioEl.muted).toBe(false);
    await fireEvent.click(screen.getByRole('radio', { name: 'Audio of slot 2: two' }));
    expect(sounding()).toEqual([false, true, false]);
    expect(audioEl.getAttribute('src')).toBe('https://fixture/audio-1.webm');
    expect(audioEl.muted).toBe(false);
    await fireEvent.click(screen.getByRole('radio', { name: 'No audio' }));
    expect(sounding()).toEqual([false, false, true]);
    expect(audioEl.muted).toBe(true);
    // A step lands in the middle of the next frame of the fastest side.
    await fireEvent.keyDown(canvas, { key: 'ArrowRight' });
    expect(video.currentTime).toBeCloseTo(61.5 / 60);
    await fireEvent.click(screen.getByRole('button', { name: 'Previous frame' }));
    expect(video.currentTime).toBeCloseTo(60.5 / 60);
    await fireEvent.keyDown(canvas, { key: ' ' });
    view.unmount();
    expect(cancel).toHaveBeenCalledWith(1);
    expect(video.hasAttribute('src')).toBe(false);
  } finally {
    getContext.mockRestore();
    pause.mockRestore();
    play.mockRestore();
    load.mockRestore();
  }
});

it('keeps the details of every slot to a title and four lines, and marks an unfinished result', () => {
  const chain = Array.from({ length: 12 }, (_, index) => `Model ${index}`).join(' → ');
  render(ComparePane, {
    ...props,
    layout: 'boxes',
    items: ['one', 'two', 'three', 'four'].map((id, index) => ({
      ...item(id, 'a'),
      details: [
        `Models: ${chain}`,
        'Fixes: Noise → JPEG artifacts → Blur → Colour → Faces',
        'Scale: 4×',
        'Output: 5760 × 7680',
        'Time: 1:40',
        'Requested device: mps · Neural Engine enabled',
        'Date: 11/23/2026',
      ],
      ...(index === 1 ? { unfinished: { label: 'Unfinished · 2 of 54 frames' } } : {}),
    })),
  });
  const strip = document.querySelector('.compare-details') as HTMLElement;
  // One row of columns, also for four boxes in a two-by-two grid.
  expect(strip.style.gridTemplateColumns).toBe('repeat(4, minmax(0, 1fr))');
  for (const [index, name] of ['one', 'two', 'three', 'four'].entries()) {
    const slot = screen.getByRole('group', { name: `Slot ${index + 1} details` });
    expect(slot.querySelector('.details-title')?.textContent).toContain(name);
    const lines = slot.querySelectorAll('.details-line, .unfinished-mark');
    expect(lines.length).toBe(4);
    expect(slot.textContent).toContain(chain);
    expect(slot.textContent).toContain('Date: 11/23/2026');
  }
  const second = screen.getByRole('group', { name: 'Slot 2 details' });
  expect(second.querySelector('.unfinished-mark')?.textContent).toBe('Unfinished · 2 of 54 frames');
});

it('opens the full details of a slot whose lines are cut, from More and with the keyboard', async () => {
  // jsdom lays nothing out: report every line as wider than its column.
  const scroll = vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(500);
  const client = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(100);
  try {
    render(ComparePane, {
      ...props,
      items: [
        { ...item('one', 'a'), details: ['Models: Fix → Upscale', 'Scale: 4×', 'Time: 31s'] },
        { ...item('two', 'a'), details: ['Models: Second'] },
      ],
    });
    const more = await screen.findByRole('button', { name: 'All details of slot 1' });
    expect(more.getAttribute('aria-expanded')).toBe('false');
    expect(document.querySelector('.details-full')).toBeNull();
    await fireEvent.click(more);
    expect(more.getAttribute('aria-expanded')).toBe('true');
    const full = document.querySelector('.details-full') as HTMLElement;
    expect([...full.children].map((line) => line.textContent)).toEqual([
      'one',
      'Models: Fix → Upscale',
      'Scale: 4×',
      'Time: 31s',
    ]);
    // Escape closes the list and leaves Compare open.
    const close = props.close;
    close.mockClear();
    await fireEvent.keyDown(more, { key: 'Escape' });
    expect(document.querySelector('.details-full')).toBeNull();
    expect(close).not.toHaveBeenCalled();
  } finally {
    scroll.mockRestore();
    client.mockRestore();
  }
});

it('marks the finished tiles of an unfinished photo and shows them from its mark', async () => {
  // The saved file is four times the source; the item's own size is still the source's.
  const unfinished = {
    label: 'Unfinished · 2 of 6 tiles',
    tiles: {
      tiles: [
        [0, 0, 200, 200],
        [200, 0, 200, 200],
      ] as [number, number, number, number][],
      width: 400,
      height: 400,
    },
  };
  const view = render(ComparePane, {
    ...props,
    items: [item('one', 'a'), { ...item('two', 'a'), unfinished }],
  });
  // The marks follow the side they belong to: the right of the divider in the slider.
  const marks = view.container.querySelector('.unfinished-tiles') as SVGElement;
  expect(view.container.querySelectorAll('.unfinished-tiles')).toHaveLength(1);
  expect(marks.getAttribute('viewBox')).toBe('0 0 400 400');
  expect(marks.getAttribute('style')).toContain('clip-path: inset(0 0% 0 50%)');
  expect(marks.classList.contains('lit')).toBe(false);
  const mark = screen.getByRole('button', {
    name: 'Unfinished · 2 of 6 tiles. Show the finished tiles',
  });
  await fireEvent.pointerEnter(mark);
  expect(marks.classList.contains('lit')).toBe(true);
  await fireEvent.pointerLeave(mark);
  expect(marks.classList.contains('lit')).toBe(false);
  await fireEvent.click(mark);
  expect(mark.getAttribute('aria-pressed')).toBe('true');
  expect(marks.classList.contains('lit')).toBe(true);
  await fireEvent.click(mark);
  expect(marks.classList.contains('lit')).toBe(false);
  // In boxes every unfinished side has its marks over its own box.
  await view.rerender({ layout: 'boxes' });
  expect(view.container.querySelectorAll('.box-well .unfinished-tiles')).toHaveLength(1);
});

it('compares an unfinished video only over its kept frames, one by one', async () => {
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  const entries = [
    { ...item('original', 'v'), media_kind: 'video', fps: 30 },
    {
      ...item('kept', 'v'),
      media_kind: 'video',
      fps: 30,
      unfinished: { label: 'Unfinished · 2 of 60 frames', frames: 2, seconds: 0.074 },
    },
  ];
  try {
    const view = render(ComparePane, {
      ...props,
      items: entries,
      generation: 1,
      prepareVideo: async () => makeCombinedDescriptor(2, { durations: [2, 0.074], fps: [30, 30] }),
      cancelVideo: async () => {},
    });
    await vi.waitFor(() => expect(view.container.querySelector('video[src]')).toBeTruthy());
    const video = view.container.querySelector('video')!;
    Object.defineProperties(video, {
      duration: { value: 0.074 },
      readyState: { value: 4 },
    });
    await fireEvent.loadedData(video);
    const transport = screen.getByRole('group', { name: 'Video playback controls' });
    expect(transport.textContent).toContain('Frame 1');
    expect(transport.textContent).toContain('2 frames');
    expect(transport.textContent).not.toContain('0:0');
    expect(
      screen.getByText('Playback ends where the unfinished result ends, after 2 frames.'),
    ).toBeTruthy();
    const back = screen.getByRole('button', { name: 'Previous frame' }) as HTMLButtonElement;
    const on = screen.getByRole('button', { name: 'Next frame' }) as HTMLButtonElement;
    expect(back.getAttribute('aria-disabled')).toBe('true');
    await fireEvent.click(on);
    // The single clock sits in the middle of the second kept frame; there is no third.
    expect(video.currentTime).toBeCloseTo(0.05);
    expect(transport.textContent).toContain('Frame 2');
    expect(on.getAttribute('aria-disabled')).toBe('true');
    const scrubber = screen.getByRole('slider', { name: 'Video frame' }) as HTMLInputElement;
    expect([scrubber.min, scrubber.max, scrubber.value]).toEqual(['1', '2', '2']);
    await fireEvent.input(scrubber, { target: { value: '1' } });
    expect(video.currentTime).toBeCloseTo(0.5 / 30);
    view.unmount();
  } finally {
    pause.mockRestore();
    load.mockRestore();
  }
});

/** Loaded combined player in the slider; `lengths` are the source durations per slot. */
async function loadedVideos(entries: CompareItem[], lengths: number[], combinedLength?: number) {
  const view = render(ComparePane, {
    ...props,
    items: entries,
    generation: 1,
    prepareVideo: async () =>
      makeCombinedDescriptor(entries.length, {
        durations: lengths,
        fps: entries.map((e) => e.fps || 30),
      }),
    cancelVideo: async () => {},
  });
  await vi.waitFor(() => expect(view.container.querySelector('video[src]')).toBeTruthy());
  const video = view.container.querySelector('video')!;
  const audio = view.container.querySelector('audio')!;
  Object.defineProperties(video, {
    duration: { value: combinedLength ?? Math.min(...lengths) },
    readyState: { value: 4 },
  });
  Object.defineProperties(audio, {
    duration: { value: Math.max(...lengths) },
    readyState: { value: 4 },
  });
  await fireEvent.loadedData(video);
  return { view, video, audio };
}
const clip = (id: string, fps = 30): CompareItem => ({
  ...item(id, 'v'),
  media_kind: 'video',
  fps,
});
function mockPlayers() {
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  return () => {
    pause.mockRestore();
    load.mockRestore();
  };
}

it('pairs the frames of an unfinished video by the file’s length, not the player’s', async () => {
  const restore = mockPlayers();
  try {
    // Two frames of a 30 fps video (0.0667 s in the file); the player reports 0.10 s.
    const { view, video } = await loadedVideos(
      [
        clip('original'),
        {
          ...clip('kept'),
          unfinished: { label: 'Unfinished · 2 of 60 frames', frames: 2, seconds: 2 / 30 },
        },
      ],
      [2, 0.1],
      0.1,
    );
    const transport = screen.getByRole('group', { name: 'Video playback controls' });
    expect(transport.textContent).toContain('2 frames');
    await fireEvent.click(screen.getByRole('button', { name: 'Next frame' }));
    // The middle of frame 1 of the 30 fps original, not 0.075 s in its frame 2.
    expect(video.currentTime).toBeCloseTo(0.05);
    expect(transport.textContent).toContain('Frame 2');
    view.unmount();
  } finally {
    restore();
  }
});

it('moves one frame per arrow key on the timeline, and dims a step button at an end', async () => {
  const restore = mockPlayers();
  try {
    const { view, video } = await loadedVideos([clip('one'), clip('two')], [4, 4]);
    const timeline = screen.getByRole('slider', { name: 'Video position' });
    const back = screen.getByRole('button', { name: 'Previous frame' });
    const on = screen.getByRole('button', { name: 'Next frame' });
    expect(back.getAttribute('aria-disabled')).toBe('true');
    expect(on.getAttribute('aria-disabled')).toBe('false');
    // The timeline's own step, a hundredth of a second, is less than a frame.
    for (const frame of [1, 2, 3]) {
      expect(await fireEvent.keyDown(timeline, { key: 'ArrowRight' })).toBe(false);
      expect(video.currentTime).toBeCloseTo((frame + 0.5) / 30);
    }
    await fireEvent.keyDown(timeline, { key: 'ArrowLeft' });
    expect(video.currentTime).toBeCloseTo(2.5 / 30);
    expect(back.getAttribute('aria-disabled')).toBe('false');
    // Other keys stay with the timeline.
    expect(await fireEvent.keyDown(timeline, { key: 'Home' })).toBe(true);
    await fireEvent.input(timeline, { target: { value: '4' } });
    expect(on.getAttribute('aria-disabled')).toBe('true');
    await fireEvent.click(back);
    expect(video.currentTime).toBeCloseTo(118.5 / 30);
    view.unmount();
  } finally {
    restore();
  }
});

it('keeps the sound with its result when the slots are swapped before any choice', async () => {
  const restore = mockPlayers();
  try {
    const entries = [clip('one'), clip('two')];
    const { view, audio } = await loadedVideos(entries, [4, 4]);
    const sounding = () =>
      screen
        .getAllByRole('radio')
        .filter((radio) => radio.closest('.transport-audio'))
        .filter((radio) => radio.getAttribute('aria-checked') === 'true')
        .map((radio) => radio.getAttribute('aria-label'));
    expect(sounding()).toEqual(['Audio of slot 1: one']);
    expect(audio.getAttribute('src')).toBe('https://fixture/audio-0.webm');
    expect(audio.muted).toBe(false);
    await view.rerender({ items: [entries[1], entries[0]] });
    expect(sounding()).toEqual(['Audio of slot 2: one']);
    expect(audio.muted).toBe(false);
    // A result that leaves hands the sound to slot 1.
    await view.rerender({ items: [entries[1], clip('three')] });
    expect(sounding()).toEqual(['Audio of slot 1: two']);
    view.unmount();
  } finally {
    restore();
  }
});

it('closes an open list of details on Escape wherever the focus is, then Compare', async () => {
  const scroll = vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(500);
  const client = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(100);
  vi.useFakeTimers();
  try {
    render(ComparePane, {
      ...props,
      items: [
        { ...item('one', 'a'), details: ['Models: Fix → Upscale'] },
        { ...item('two', 'a'), details: ['Models: Second'] },
      ],
    });
    await vi.advanceTimersByTimeAsync(0);
    const close = props.close;
    close.mockClear();
    const full = () => document.querySelector('.details-full');
    // Opened by a click that leaves the focus where it was, as in WebKit.
    await fireEvent.click(screen.getByRole('button', { name: 'All details of slot 1' }));
    expect(full()).toBeTruthy();
    await fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(full()).toBeNull();
    expect(close).not.toHaveBeenCalled();
    // Opened by resting the pointer on the slot.
    await fireEvent.pointerEnter(document.querySelector('.details-column') as HTMLElement);
    await vi.advanceTimersByTimeAsync(400);
    expect(full()).toBeTruthy();
    await fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(full()).toBeNull();
    expect(close).not.toHaveBeenCalled();
    // A click elsewhere closes a list that More keeps open.
    await fireEvent.click(screen.getByRole('button', { name: 'All details of slot 1' }));
    expect(full()).toBeTruthy();
    await fireEvent.pointerDown(screen.getByRole('region', { name: 'Compare canvas' }));
    expect(full()).toBeNull();
    // With nothing open, Escape leaves Compare.
    await fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(close).toHaveBeenCalledOnce();
  } finally {
    vi.useRealTimers();
    scroll.mockRestore();
    client.mockRestore();
  }
});

it('shows the viewer background swatch in Compare zoom and steps on click', async () => {
  const setViewerBackground = vi.fn();
  render(ComparePane, {
    ...props,
    items: [item('one', 'a'), item('two', 'a')],
    viewerBackground: 'black',
    setViewerBackground,
  });
  const zoomGroup = screen.getByRole('group', { name: 'Compare zoom' });
  const button = zoomGroup.querySelector('button[aria-label="Viewer background: Black"]');
  expect(button).toBeTruthy();
  await fireEvent.click(button as HTMLElement);
  expect(setViewerBackground).toHaveBeenCalledWith('grey');
});

it('does not open the list of details while the pointer rests on the Unfinished mark', async () => {
  const scroll = vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(500);
  const client = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(100);
  vi.useFakeTimers();
  try {
    const view = render(ComparePane, {
      ...props,
      items: [
        item('one', 'a'),
        {
          ...item('two', 'a'),
          details: ['Models: Second'],
          unfinished: {
            label: 'Unfinished · 1 of 4 tiles',
            tiles: { tiles: [[0, 0, 50, 50]], width: 100, height: 100 },
          },
        },
      ],
    });
    await vi.advanceTimersByTimeAsync(0);
    const column = view.container.querySelectorAll<HTMLElement>('.details-column')[1];
    const mark = screen.getByRole('button', { name: /Show the finished tiles/ });
    // The pointer crosses the column and rests on the mark: the tiles show, the list does not.
    await fireEvent.pointerEnter(column);
    await vi.advanceTimersByTimeAsync(100);
    await fireEvent.pointerEnter(mark);
    await vi.advanceTimersByTimeAsync(1000);
    expect(view.container.querySelector('.unfinished-tiles')?.classList.contains('lit')).toBe(true);
    expect(document.querySelector('.details-full')).toBeNull();
    // Off the mark but still on the slot, the list opens as for any slot.
    await fireEvent.pointerLeave(mark);
    await vi.advanceTimersByTimeAsync(400);
    expect(document.querySelector('.details-full')).toBeTruthy();
  } finally {
    vi.useRealTimers();
    scroll.mockRestore();
    client.mockRestore();
  }
});

it('preserves playback position and audio choice when switching between slider and boxes', async () => {
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  try {
    const entries = [
      { ...item('orig', 'v'), media_kind: 'video' as const, fps: 30 },
      { ...item('res', 'v'), media_kind: 'video' as const, fps: 30 },
    ];
    let layout: 'slider' | 'boxes' = 'slider';
    const view = render(ComparePane, {
      ...props,
      layout,
      setLayout: (next: 'slider' | 'boxes') => {
        layout = next;
      },
      items: entries,
      generation: 1,
      prepareVideo: async () => makeCombinedDescriptor(2, { durations: [3, 3], fps: [30, 30] }),
      cancelVideo: async () => {},
    });
    await vi.waitFor(() => expect(view.container.querySelector('video[src]')).toBeTruthy());
    const video = view.container.querySelector('video')!;
    const audio = view.container.querySelector('audio')!;
    Object.defineProperties(video, { duration: { value: 3 }, readyState: { value: 4 } });
    Object.defineProperties(audio, { duration: { value: 3 }, readyState: { value: 4 } });
    await fireEvent.loadedData(video);
    await fireEvent.click(screen.getByRole('radio', { name: 'Audio of slot 2: res' }));
    const timeline = screen.getByRole('slider', { name: 'Video position' });
    await fireEvent.input(timeline, { target: { value: '0.5' } });
    const sliderTime = video.currentTime;
    expect(sliderTime).toBeCloseTo(15.5 / 30);

    await view.rerender({ ...props, layout: 'boxes', items: entries, generation: 1 });
    await vi.waitFor(() =>
      expect(view.container.querySelectorAll('.boxes canvas')).toHaveLength(2),
    );
    expect(video.currentTime).toBe(sliderTime);
    expect(audio.getAttribute('src')).toBe('https://fixture/audio-1.webm');
    expect(audio.muted).toBe(false);
    expect(
      screen.getByRole('radio', { name: 'Audio of slot 2: res' }).getAttribute('aria-checked'),
    ).toBe('true');
    view.unmount();
  } finally {
    pause.mockRestore();
    load.mockRestore();
  }
});

it('renders comparison images from file-backed asset URLs', () => {
  const urlA = 'asset://localhost/%2Fcache%2Fpreviews%2Fa.jpg';
  const urlB = 'asset://localhost/%2Fcache%2Fpreviews%2Fb.jpg';
  const { container } = render(ComparePane, {
    ...props,
    items: [
      { ...item('one', 'a'), preview_data_url: urlA },
      { ...item('two', 'a'), preview_data_url: urlB },
    ],
  });
  expect(container.querySelector('.source-image')?.getAttribute('src')).toBe(urlA);
  expect(container.querySelector('.result-image')?.getAttribute('src')).toBe(urlB);
});
