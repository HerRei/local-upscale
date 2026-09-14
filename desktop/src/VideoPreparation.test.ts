// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PreviewPane from './PreviewPane.svelte';
import * as api from './lib/api';
import type { MediaItem, VideoComparisonProgress, VideoComparisonSources } from './lib/types';

vi.mock('./lib/api', () => ({
  isTauri: () => true,
  prepareVideoComparison: vi.fn(),
  cancelVideoComparison: vi.fn(async () => {}),
  listenVideoComparisonProgress: vi.fn(),
}));
let progress: (data: VideoComparisonProgress) => void;
const unlisten = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listenVideoComparisonProgress).mockImplementation(async (callback) => {
    progress = callback;
    return unlisten;
  });
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function video(id: string): MediaItem {
  return {
    id,
    path: `/${id}.avi`,
    name: `${id}.avi`,
    kind: 'video',
    width: 176,
    height: 144,
    frame_count: 30,
    fps: 25,
    duration_seconds: 1.2,
    preview_data_url: 'data:image/jpeg;base64,source',
    probe_status: 'ready',
    error: '',
    selected: true,
  };
}
function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function show(id = 'a') {
  return render(PreviewPane, {
    selectedMedia: video(id),
    resultPreview: '',
    completedVideoOutput: `/${id}-result.mp4`,
    addFiles: async () => {},
  });
}

it('shows real conversion progress and ignores the previous selection when switching back', async () => {
  const first = pending<VideoComparisonSources>();
  const second = pending<VideoComparisonSources>();
  const third = pending<VideoComparisonSources>();
  vi.mocked(api.prepareVideoComparison)
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise)
    .mockReturnValueOnce(third.promise);
  const view = show();
  await waitFor(() => expect(api.prepareVideoComparison).toHaveBeenCalledTimes(1));
  const oldRequest = vi.mocked(api.prepareVideoComparison).mock.calls[0][1];
  progress({
    request_id: oldRequest,
    media_id: 'a',
    stage: 'Converting video for playback',
    frame: 12,
    total: 30,
    elapsed_seconds: 2,
  });
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain('12 / ~30 frames'));
  await view.rerender({ selectedMedia: video('b'), completedVideoOutput: '/b-result.mp4' });
  await waitFor(() => expect(api.prepareVideoComparison).toHaveBeenCalledTimes(2));
  expect(api.cancelVideoComparison).toHaveBeenCalledWith(oldRequest);
  await view.rerender({ selectedMedia: video('a'), completedVideoOutput: '/a-result.mp4' });
  await waitFor(() => expect(api.prepareVideoComparison).toHaveBeenCalledTimes(3));
  progress({
    request_id: oldRequest,
    media_id: 'a',
    stage: 'STALE',
    frame: 30,
    total: 30,
    elapsed_seconds: 6,
  });
  first.resolve({ original_url: 'stale-a', enhanced_url: 'stale-result' });
  second.resolve({ original_url: 'stale-b', enhanced_url: 'stale-result-b' });
  await Promise.resolve();
  expect(view.container.textContent).not.toContain('STALE');
  expect(view.container.querySelector('video')).toBeNull();
  third.resolve({
    original_url: 'current-a',
    enhanced_url: 'current-result',
    playback_note: 'Compatible SDR playback copy',
  });
  await waitFor(() =>
    expect(view.container.querySelector('video')?.getAttribute('src')).toBe('current-a'),
  );
  expect(screen.getByText('Compatible SDR playback copy')).toBeTruthy();
  view.unmount();
  await waitFor(() => expect(unlisten).toHaveBeenCalled());
});

it('cancels preparation without replacing the selection with its late completion', async () => {
  const work = pending<VideoComparisonSources>();
  vi.mocked(api.prepareVideoComparison).mockReturnValue(work.promise);
  const view = show();
  await waitFor(() => expect(api.prepareVideoComparison).toHaveBeenCalledTimes(1));
  await fireEvent.click(screen.getByRole('button', { name: 'Cancel preview' }));
  expect(api.cancelVideoComparison).toHaveBeenCalled();
  work.resolve({ original_url: 'late', enhanced_url: 'late' });
  await waitFor(() =>
    expect(screen.getByRole('alert').textContent).toContain('Playback conversion cancelled'),
  );
  expect(view.container.querySelector('video')).toBeNull();
  vi.mocked(api.prepareVideoComparison).mockResolvedValueOnce({
    original_url: 'retry-source',
    enhanced_url: 'retry-export',
  });
  await fireEvent.click(screen.getByRole('button', { name: 'Retry comparison' }));
  await waitFor(() =>
    expect(view.container.querySelector('video')?.getAttribute('src')).toBe('retry-source'),
  );
  expect(api.prepareVideoComparison).toHaveBeenCalledTimes(2);
});

it('converts an unsupported native codec once without a retry loop', async () => {
  vi.mocked(api.prepareVideoComparison)
    .mockResolvedValueOnce({ original_url: 'native.mov', enhanced_url: 'export.mp4' })
    .mockResolvedValueOnce({
      original_url: 'proxy-original.mp4',
      enhanced_url: 'proxy-export.mp4',
    });
  const view = show();
  await waitFor(() => expect(view.container.querySelectorAll('video')).toHaveLength(2));
  let element = view.container.querySelectorAll('video')[0];
  Object.defineProperty(element, 'error', { value: { code: 4 } });
  await fireEvent.error(element);
  await waitFor(() => expect(api.prepareVideoComparison).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.prepareVideoComparison).mock.calls[1][2]).toBe(true);
  await waitFor(() =>
    expect(view.container.querySelector('video')?.getAttribute('src')).toBe('proxy-original.mp4'),
  );
  element = view.container.querySelectorAll('video')[0];
  Object.defineProperty(element, 'error', { value: { code: 4 } });
  await fireEvent.error(element);
  expect(api.prepareVideoComparison).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('alert').textContent).toContain('could not be loaded in this player');
});
