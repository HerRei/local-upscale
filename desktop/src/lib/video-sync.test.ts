import { describe, expect, it } from 'vitest';

import { commonDuration, formatMediaTime, synchronizationDecision } from './video-sync';

describe('video comparison synchronization', () => {
  it('uses the safe shared duration when streams differ slightly', () => {
    expect(commonDuration(10.03, 10)).toBe(10);
    expect(commonDuration(Number.NaN, 4)).toBe(4);
    expect(commonDuration(0, Number.NaN)).toBe(0);
  });

  it('seeks only for meaningful drift', () => {
    expect(synchronizationDecision(3, 2.8, false, 1)).toEqual({
      seekTo: 3,
      playbackRate: 1,
    });
    expect(synchronizationDecision(3, 2.99, false, 1)).toEqual({
      seekTo: null,
      playbackRate: 1,
    });
  });

  it('uses a bounded rate correction without oscillating in the dead band', () => {
    expect(synchronizationDecision(3, 2.95, false, 1)).toEqual({
      seekTo: null,
      playbackRate: 1.03,
    });
    expect(synchronizationDecision(3, 3.05, false, 1)).toEqual({
      seekTo: null,
      playbackRate: 0.97,
    });
    expect(synchronizationDecision(3, 2.95, true, 1)).toEqual({
      seekTo: null,
      playbackRate: 1,
    });
  });

  it('formats stable shared timestamps', () => {
    expect(formatMediaTime(0)).toBe('0:00');
    expect(formatMediaTime(65.9)).toBe('1:05');
    expect(formatMediaTime(Number.NaN)).toBe('0:00');
    expect(formatMediaTime(5 / 59.94, true)).toBe('0.08 s');
  });
});
