import { describe, expect, it } from 'vitest';

import {
  clampPan,
  comparisonHandleTop,
  fitSize,
  panBounds,
  pointerCenteredPan,
  zoomLimits,
} from './viewport';

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

  it('keeps the comparison handle in the middle of the visible canvas', () => {
    // Fitted and centred: the canvas middle is the image middle.
    expect(comparisonHandleTop(700, 400, 1, 0)).toBe(50);
    // Zoomed in 3× and panned fully down: the 1200 px image starts at the
    // canvas top, so the canvas middle (350 px) is 350 / 3 image px into its
    // 400 px height, above the image middle.
    expect(comparisonHandleTop(700, 400, 3, 250)).toBeCloseTo((350 / 3 / 400) * 100);
    // Even with a pan beyond the bounds, where only the image's bottom 250 px
    // show, the handle stays on the image, one radius above its lower edge.
    expect(comparisonHandleTop(700, 400, 3, -700)).toBeCloseTo(((231 + 950) / 3 / 400) * 100);
    // Zoomed out: the image is centred and smaller, so its middle is still used.
    expect(comparisonHandleTop(700, 400, 0.5, 0)).toBe(50);
  });
});
