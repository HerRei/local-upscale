import { describe, expect, it } from 'vitest';

import { clampPan, fitSize, panBounds, pointerCenteredPan, zoomLimits } from './viewport';

describe('preview viewport', () => {
  it('enlarges a small image to use the available canvas', () => {
    expect(fitSize({ width: 1000, height: 700 }, { width: 64, height: 48 })).toEqual({
      width: 858.6666666666666,
      height: 644,
    });
  });

  it('preserves the image aspect ratio while fitting', () => {
    const fitted = fitSize({ width: 900, height: 700 }, { width: 4000, height: 2000 });
    expect(fitted).toEqual({ width: 844, height: 422 });
  });

  it('raises the zoom ceiling enough to reach source pixels for large media', () => {
    const limits = zoomLimits({ width: 12000, height: 8000 }, { width: 750, height: 500 });
    expect(limits.min).toBe(1);
    expect(limits.actual).toBe(16);
    expect(limits.max).toBe(32);

    const tiny = zoomLimits({ width: 640, height: 480 }, { width: 800, height: 600 });
    expect(tiny.min).toBe(0.8);
    expect(tiny.actual).toBe(0.8);
    expect(tiny.max).toBe(8);
  });

  it('keeps pointer-centered zoom bounded by the visible image', () => {
    const bounds = panBounds({ width: 800, height: 600 }, { width: 744, height: 558 }, 2);
    const centered = pointerCenteredPan({ x: 0, y: 0 }, { x: 200, y: 100 }, 1, 2, bounds);
    expect(centered).toEqual({ x: -200, y: -100 });
    expect(clampPan({ x: 9999, y: -9999 }, bounds)).toEqual({
      x: bounds.x,
      y: -bounds.y,
    });
  });
});
