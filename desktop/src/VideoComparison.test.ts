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
  it('exposes one accessible comparison slider and one shared scrubber', async () => {
    render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4'
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
    expect(screen.getByText('Original')).toBeTruthy();
    expect(screen.getByText('Enhanced')).toBeTruthy();
    expect(screen.getByText('VIDEO · COMPARISON')).toBeTruthy();
  });

  it('keeps audio enabled only on the enhanced stream', () => {
    const { container } = render(VideoComparison, {
      originalSrc: 'asset://localhost/original.mp4',
      enhancedSrc: 'asset://localhost/enhanced.mp4'
    });
    const videos = container.querySelectorAll('video');
    expect(videos).toHaveLength(2);
    expect(videos[0].muted).toBe(true);
    expect(videos[1].muted).toBe(false);
  });

  it('integrates shared playback, deterministic seeking, drift correction, and cleanup', async () => {
    const rendered = render(VideoComparison, {
      originalSrc: 'asset://localhost/generated-original.mp4',
      enhancedSrc: 'asset://localhost/generated-enhanced.mp4'
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

    await fireEvent.change(screen.getByRole('combobox', { name: 'Playback speed' }), {
      target: { value: '1.5' }
    });
    expect(videos[0].playbackRate).toBe(1.5);
    expect(videos[1].playbackRate).toBe(1.5);

    rendered.unmount();
    expect(videos[0].getAttribute('src')).toBeNull();
    expect(videos[1].getAttribute('src')).toBeNull();
  });
});
