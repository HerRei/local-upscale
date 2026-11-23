// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import VideoComparison from './VideoComparison.svelte';

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('video comparison player', () => {
  it('loads both frames and retries a failed player without calling the file corrupt', async () => {
    const { container } = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4',
    });
    const videos = container.querySelectorAll('video');
    expect(screen.getByRole('status').textContent).toContain('Loading video comparison');
    await fireEvent.loadedData(videos[0]);
    expect(screen.getByRole('status')).toBeTruthy();
    Object.defineProperty(videos[1], 'error', { configurable: true, value: { code: 4 } });
    await fireEvent.error(videos[1]);
    expect(screen.getByRole('alert').textContent).toContain('could not be loaded in this player');
    expect(screen.getByRole('alert').textContent).not.toContain('codec');
    await fireEvent.click(screen.getByRole('button', { name: 'Retry comparison' }));
    expect(HTMLMediaElement.prototype.load).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).toBeNull();
    await fireEvent.loadedData(videos[0]);
    await fireEvent.loadedData(videos[1]);
    expect(screen.queryByRole('status')).toBeNull();
    await fireEvent.click(screen.getByRole('button', { name: 'Play comparison' }));
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
  });

  it('exposes one accessible comparison slider and one shared scrubber', async () => {
    render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4',
    });

    const divider = screen.getByRole('slider', { name: 'Video before and after comparison' });
    expect(divider.getAttribute('aria-valuenow')).toBe('50');
    await fireEvent.keyDown(divider, { key: 'ArrowRight' });
    expect(divider.getAttribute('aria-valuenow')).toBe('51');
    await fireEvent.keyDown(divider, { key: 'Home' });
    expect(divider.getAttribute('aria-valuenow')).toBe('0');
    await fireEvent.keyDown(divider, { key: 'End' });
    expect(divider.getAttribute('aria-valuenow')).toBe('100');

    expect(screen.getByRole('slider', { name: 'Video position' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Playback speed' }).textContent).toBe('1×');
    // Like a photo comparison, a split view carries no side labels.
    expect(screen.queryByText('Original')).toBeNull();
    expect(screen.queryByText('Enhanced')).toBeNull();
  });

  it('follows the chosen comparison view', async () => {
    const { container, rerender } = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4',
    });
    const clip = () => container.querySelector('.enhanced-clip')?.getAttribute('style');
    const divider = () => container.querySelector('.compare-line') as HTMLElement;
    expect(clip()).toContain('inset(0 0 0 50%)');
    await rerender({ view: 'original' });
    expect(clip()).toContain('inset(0 0 0 100%)');
    expect(screen.getByText('Original')).toBeTruthy();
    expect(divider().getAttribute('tabindex')).toBe('-1');
    await rerender({ view: 'enhanced' });
    expect(clip()).toContain('inset(0 0 0 0%)');
    expect(screen.queryByText('Original')).toBeNull();
    await rerender({ view: 'split' });
    expect(clip()).toContain('inset(0 0 0 50%)');
    expect(divider().getAttribute('tabindex')).toBe('0');
  });

  it('keeps audio enabled only on the enhanced stream', () => {
    const { container } = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4',
    });
    const videos = container.querySelectorAll('video');
    expect(videos).toHaveLength(2);
    expect(videos[0].muted).toBe(true);
    expect(videos[1].muted).toBe(false);
  });

  it('integrates shared playback, deterministic seeking, drift correction, and cleanup', async () => {
    const rendered = render(VideoComparison, {
      originalSrc: 'asset://localhost/generated-original.mp4',
      enhancedSrc: 'asset://localhost/generated-enhanced.mp4',
    });
    const videos = rendered.container.querySelectorAll('video');
    Object.defineProperty(videos[0], 'duration', { configurable: true, value: 4.02 });
    Object.defineProperty(videos[1], 'duration', { configurable: true, value: 4.0 });
    Object.defineProperty(videos[0], 'paused', { configurable: true, writable: true, value: true });
    Object.defineProperty(videos[1], 'paused', { configurable: true, writable: true, value: true });
    await fireEvent.loadedMetadata(videos[0]);
    await fireEvent.loadedMetadata(videos[1]);

    const scrubber = screen.getByRole('slider', { name: 'Video position' }) as HTMLInputElement;
    expect(scrubber.max).toBe('4');
    await fireEvent.input(scrubber, { target: { value: '2.5' } });
    expect(videos[0].currentTime).toBe(2.5);
    expect(videos[1].currentTime).toBe(2.5);

    await fireEvent.click(screen.getByRole('button', { name: 'Play comparison' }));
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
    Object.defineProperty(videos[1], 'paused', { configurable: true, value: false });
    videos[1].currentTime = 3;
    videos[0].currentTime = 2.7;
    await fireEvent.timeUpdate(videos[1]);
    expect(videos[0].currentTime).toBe(3);

    const speed = screen.getByRole('button', { name: 'Playback speed' });
    await fireEvent.click(speed);
    expect(speed.textContent).toBe('1.5×');
    expect(videos[0].playbackRate).toBe(1.5);
    expect(videos[1].playbackRate).toBe(1.5);

    rendered.unmount();
    expect(videos[0].getAttribute('src')).toBeNull();
    expect(videos[1].getAttribute('src')).toBeNull();
  });

  it('counts a clip of under a second in frames and steps both sides one frame at a time', async () => {
    // A stopped job kept two frames (0.074 s) of a two-second source.
    const rendered = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mov',
      enhancedSrc: 'asset://localhost/kept_partial.mp4',
      fps: 30,
      keptFrames: 2,
    });
    const videos = rendered.container.querySelectorAll('video');
    Object.defineProperty(videos[0], 'duration', { configurable: true, value: 2 });
    Object.defineProperty(videos[1], 'duration', { configurable: true, value: 0.074 });
    await fireEvent.loadedMetadata(videos[0]);
    await fireEvent.loadedMetadata(videos[1]);

    const transport = screen.getByRole('group', { name: 'Video playback controls' });
    expect(transport.textContent).toContain('Frame 1');
    expect(transport.textContent).toContain('2 frames');
    expect(transport.textContent).not.toContain(' s');
    const scrubber = screen.getByRole('slider', { name: 'Video frame' }) as HTMLInputElement;
    expect([scrubber.min, scrubber.max, scrubber.value]).toEqual(['1', '2', '1']);

    const back = screen.getByRole('button', { name: 'Previous frame' }) as HTMLButtonElement;
    const on = screen.getByRole('button', { name: 'Next frame' }) as HTMLButtonElement;
    expect(back.getAttribute('aria-disabled')).toBe('true');
    await fireEvent.click(on);
    // The middle of the second kept frame on both sides, by the file's own frame rate.
    expect(videos[0].currentTime).toBeCloseTo(0.0555);
    expect(videos[1].currentTime).toBeCloseTo(0.0555);
    expect(transport.textContent).toContain('Frame 2');
    expect(on.getAttribute('aria-disabled')).toBe('true');
    await fireEvent.input(scrubber, { target: { value: '1' } });
    expect(videos[0].currentTime).toBeCloseTo(0.0185);
    expect(videos[1].currentTime).toBeCloseTo(0.0185);
  });

  it('steps by the kept file’s own rate when the player reports a longer clip', async () => {
    // Two frames of a 30 fps video (0.0667 s in the file); the player says 0.10 s.
    const rendered = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mov',
      enhancedSrc: 'asset://localhost/kept_partial.mp4',
      fps: 30,
      keptFrames: 2,
      keptSeconds: 2 / 30,
    });
    const videos = rendered.container.querySelectorAll('video');
    Object.defineProperty(videos[0], 'duration', { configurable: true, value: 2 });
    Object.defineProperty(videos[1], 'duration', { configurable: true, value: 0.1 });
    await fireEvent.loadedMetadata(videos[0]);
    await fireEvent.loadedMetadata(videos[1]);
    expect(screen.getByRole('group', { name: 'Video playback controls' }).textContent).toContain(
      '2 frames',
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Next frame' }));
    // Frame 1 of the 30 fps original; 0.075 s would be its frame 2.
    expect(videos[0].currentTime).toBeCloseTo(0.05);
    expect(videos[1].currentTime).toBeCloseTo(0.05);
  });

  it('steps back from the end of a longer video and dims a step button at an end', async () => {
    const rendered = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4',
      fps: 30,
    });
    const videos = rendered.container.querySelectorAll('video');
    for (const video of videos)
      Object.defineProperty(video, 'duration', { configurable: true, value: 2 });
    await fireEvent.loadedMetadata(videos[0]);
    await fireEvent.loadedMetadata(videos[1]);
    const back = screen.getByRole('button', { name: 'Previous frame' });
    const on = screen.getByRole('button', { name: 'Next frame' });
    expect(back.getAttribute('aria-disabled')).toBe('true');
    expect(on.getAttribute('aria-disabled')).toBe('false');
    // Played to the end: the last frame, 59, shows.
    videos[1].currentTime = 2;
    await fireEvent.timeUpdate(videos[1]);
    expect(on.getAttribute('aria-disabled')).toBe('true');
    expect(back.getAttribute('aria-disabled')).toBe('false');
    await fireEvent.click(back);
    // One press goes to frame 58, not to the middle of the frame already shown.
    expect(videos[0].currentTime).toBeCloseTo(58.5 / 30);
    expect(videos[1].currentTime).toBeCloseTo(58.5 / 30);
    // The arrow keys on the timeline go by frames too.
    const timeline = screen.getByRole('slider', { name: 'Video position' });
    await fireEvent.keyDown(timeline, { key: 'ArrowLeft' });
    expect(videos[1].currentTime).toBeCloseTo(57.5 / 30);
  });

  it('keeps the clock for a longer video and offers frame steps only with a frame rate', async () => {
    const rendered = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4',
      fps: 24,
    });
    const videos = rendered.container.querySelectorAll('video');
    for (const video of videos)
      Object.defineProperty(video, 'duration', { configurable: true, value: 4 });
    await fireEvent.loadedMetadata(videos[0]);
    await fireEvent.loadedMetadata(videos[1]);
    expect(screen.getByRole('slider', { name: 'Video position' })).toBeTruthy();
    await fireEvent.click(screen.getByRole('button', { name: 'Next frame' }));
    expect(videos[0].currentTime).toBeCloseTo(1.5 / 24);
    expect(videos[1].currentTime).toBeCloseTo(1.5 / 24);
    await rendered.rerender({ fps: 0 });
    expect(screen.queryByRole('button', { name: 'Next frame' })).toBeNull();
  });

  it('keeps the original in step when the enhanced video pauses or stalls on its own', async () => {
    const rendered = render(VideoComparison, {
      originalSrc: 'asset://localhost/generated-original.mp4',
      enhancedSrc: 'asset://localhost/generated-enhanced.mp4',
    });
    const [original, enhanced] = rendered.container.querySelectorAll('video');
    const pause = vi.spyOn(original, 'pause');
    const play = vi.spyOn(original, 'play').mockResolvedValue(undefined);
    await fireEvent(enhanced, new Event('pause'));
    expect(pause).toHaveBeenCalledTimes(1);
    Object.defineProperty(original, 'paused', { configurable: true, value: true });
    await fireEvent(enhanced, new Event('playing'));
    expect(play).toHaveBeenCalledTimes(1);
  });

  it('starts playback although the enhanced video buffers before both have started', async () => {
    const rendered = render(VideoComparison, {
      originalSrc: 'asset://localhost/generated-original.mp4',
      enhancedSrc: 'asset://localhost/generated-enhanced.mp4',
    });
    const [original, enhanced] = rendered.container.querySelectorAll('video');
    Object.defineProperty(enhanced, 'paused', { configurable: true, writable: true, value: true });
    // A pending play() is aborted by pause(), as in WebKit.
    let started = false;
    const pending = (video: HTMLVideoElement) =>
      new Promise<void>((resolve, reject) => {
        video.addEventListener('test-start', () => resolve());
        vi.spyOn(video, 'pause').mockImplementation(() => {
          if (!started) reject(new DOMException('interrupted', 'AbortError'));
        });
      });
    vi.spyOn(original, 'play').mockImplementation(() => pending(original));
    vi.spyOn(enhanced, 'play').mockImplementation(() => pending(enhanced));
    await fireEvent.click(screen.getByRole('button', { name: 'Play comparison' }));
    await fireEvent(enhanced, new Event('waiting'));
    started = true;
    original.dispatchEvent(new Event('test-start'));
    enhanced.dispatchEvent(new Event('test-start'));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText(/Playback could not start/)).toBeNull();
  });

  it('zooms like a photo: wheel, zoom HUD, Fit and 1:1, and pans when zoomed', async () => {
    canvasRect(800, 450);
    const { container } = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4',
      sourceWidth: 1600,
      sourceHeight: 900,
    });
    const viewport = container.querySelector('.video-viewport') as HTMLDivElement;
    const stage = () => (container.querySelector('.video-stage') as HTMLElement).style.transform;
    const readZoom = () => screen.getByRole('group', { name: 'Video zoom' }).textContent;
    expect(readZoom()).toContain('100%');
    await fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    expect(readZoom()).toContain('125%');
    expect(stage()).toContain('scale(1.25)');
    await fireEvent.wheel(viewport, { deltaY: -100, clientX: 400, clientY: 225 });
    expect(readZoom()).toContain('140%');
    // Away from the divider a drag pans both streams as one layer.
    await fireEvent.pointerDown(viewport, { button: 0, clientX: 200, clientY: 225, pointerId: 1 });
    await fireEvent.pointerMove(viewport, { clientX: 260, clientY: 235, pointerId: 1 });
    await fireEvent.pointerUp(viewport, { pointerId: 1 });
    expect(stage()).toContain('translate(calc(-50% + 60px), calc(-50% + 10px))');
    await fireEvent.click(screen.getByRole('button', { name: 'Fit' }));
    expect(readZoom()).toContain('100%');
    expect(stage()).toContain('translate(calc(-50% + 0px), calc(-50% + 0px)) scale(1)');
    expect((screen.getByRole('button', { name: 'Zoom out' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    // 1600 source pixels fitted into 744 px of canvas: 1:1 is 228%.
    await fireEvent.click(screen.getByRole('button', { name: '1:1' }));
    expect(readZoom()).toContain('228%');
  });

  it('holds the divider on the frame and drags it from anywhere on its line', async () => {
    canvasRect(800, 450);
    const { container } = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4',
      sourceWidth: 1600,
      sourceHeight: 900,
    });
    const viewport = container.querySelector('.video-viewport') as HTMLDivElement;
    const divider = screen.getByRole('slider', { name: 'Video before and after comparison' });
    // The fitted frame is 700.4 × 394 px, centred: the line spans its height only.
    expect(divider.style.top).toBe('28px');
    expect(divider.style.height).toBe('394px');
    await fireEvent.pointerDown(viewport, { button: 0, clientX: 410, clientY: 100, pointerId: 1 });
    await fireEvent.pointerMove(viewport, { clientX: 225, clientY: 100, pointerId: 1 });
    await fireEvent.pointerUp(viewport, { pointerId: 1 });
    expect(divider.getAttribute('aria-valuenow')).toBe('25');
    // Past the frame's edge the divider stops on it.
    await fireEvent.pointerDown(viewport, { button: 0, clientX: 225, clientY: 100, pointerId: 2 });
    await fireEvent.pointerMove(viewport, { clientX: 5, clientY: 100, pointerId: 2 });
    await fireEvent.pointerUp(viewport, { pointerId: 2 });
    expect(divider.getAttribute('aria-valuenow')).toBe('0');
    expect(parseFloat(divider.style.left)).toBeCloseTo(49.78, 2);
  });

  it('plays and pauses with Space unless a control has focus', async () => {
    const { container } = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4',
    });
    const videos = container.querySelectorAll('video');
    Object.defineProperty(videos[1], 'paused', { configurable: true, value: true });
    await fireEvent.keyDown(document.body, { key: ' ' });
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Pause comparison' })).toBeTruthy();
    const input = document.createElement('input');
    document.body.append(input);
    await fireEvent.keyDown(input, { key: ' ' });
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
    input.remove();
  });
});

function canvasRect(width: number, height: number): void {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    left: 0,
    top: 0,
    width,
    height,
    right: width,
    bottom: height,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
}

describe('video comparison slider', () => {
  it('announces a value text that moves with aria-valuenow', async () => {
    const { container } = render(VideoComparison, {
      originalSrc: 'asset://o.mp4',
      enhancedSrc: 'asset://e.mp4',
      sourceWidth: 1280,
      sourceHeight: 720,
    });
    const viewport = container.querySelector('.video-viewport') as HTMLDivElement;
    const divider = screen.getByRole('slider', { name: 'Video before and after comparison' });
    await fireEvent.keyDown(divider, { key: 'Home' });
    const low = [
      Number(divider.getAttribute('aria-valuenow')),
      Number(/(\d+)%/.exec(divider.getAttribute('aria-valuetext') ?? '')?.[1]),
    ];
    await fireEvent.keyDown(divider, { key: 'End' });
    const high = [
      Number(divider.getAttribute('aria-valuenow')),
      Number(/(\d+)%/.exec(divider.getAttribute('aria-valuetext') ?? '')?.[1]),
    ];
    expect(viewport).toBeTruthy();
    expect(Math.sign(high[0] - low[0])).toBe(Math.sign(high[1] - low[1]));
  });
});
