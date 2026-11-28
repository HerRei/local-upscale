import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createStartEstimate, type StartWanted } from './startEstimate';
import type { StartBatchInput } from './types';

const wanted = (scale: number, stamp = 's'): StartWanted => ({
  mediaIds: ['m'],
  input: { output_scale: scale } as StartBatchInput,
  stamp,
});

describe('createStartEstimate', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('asks once for a run of changes, after the delay, with the last one', async () => {
    const fetcher = vi.fn(async () => 40);
    const { seconds, want } = createStartEstimate(fetcher, 400);
    want(wanted(2));
    await vi.advanceTimersByTimeAsync(200);
    want(wanted(3));
    await vi.advanceTimersByTimeAsync(200);
    want(wanted(4));
    expect(fetcher).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(400);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith(['m'], { output_scale: 4 });
    expect(get(seconds)).toBe(40);
  });

  it('does not ask again for the same request', async () => {
    const fetcher = vi.fn(async () => 40);
    const { want } = createStartEstimate(fetcher, 400);
    want(wanted(2));
    await vi.advanceTimersByTimeAsync(500);
    want(wanted(2));
    await vi.advanceTimersByTimeAsync(500);
    expect(fetcher).toHaveBeenCalledTimes(1);
    want(wanted(2, 'a new measured run'));
    await vi.advanceTimersByTimeAsync(500);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('shows nothing for no estimate, an error or a nonsense number', async () => {
    for (const result of [null, 0, Number.NaN, -3]) {
      const { seconds, want } = createStartEstimate(async () => result, 10);
      want(wanted(2));
      await vi.advanceTimersByTimeAsync(20);
      expect(get(seconds)).toBeNull();
    }
    const failing = createStartEstimate(async () => Promise.reject(new Error('refused')), 10);
    failing.want(wanted(2));
    await vi.advanceTimersByTimeAsync(20);
    expect(get(failing.seconds)).toBeNull();
  });

  it('clears the time and cancels the pending call when nothing is wanted', async () => {
    const fetcher = vi.fn(async () => 40);
    const { seconds, want } = createStartEstimate(fetcher, 400);
    want(wanted(2));
    await vi.advanceTimersByTimeAsync(500);
    expect(get(seconds)).toBe(40);
    want(wanted(3));
    want(null);
    expect(get(seconds)).toBeNull();
    await vi.advanceTimersByTimeAsync(500);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('drops the answer of a request that was replaced while it ran', async () => {
    let finishFirst: (value: number) => void = () => {};
    const fetcher = vi
      .fn<() => Promise<number>>()
      .mockImplementationOnce(() => new Promise((resolve) => (finishFirst = resolve)))
      .mockResolvedValueOnce(12);
    const { seconds, want } = createStartEstimate(fetcher, 10);
    want(wanted(2));
    await vi.advanceTimersByTimeAsync(20);
    want(wanted(3));
    await vi.advanceTimersByTimeAsync(20);
    expect(get(seconds)).toBe(12);
    finishFirst(99);
    await vi.advanceTimersByTimeAsync(0);
    expect(get(seconds)).toBe(12);
  });
});
