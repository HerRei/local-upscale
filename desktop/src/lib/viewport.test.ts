import { describe, expect, it } from 'vitest';

import {
  clampPan,
  comparisonGeometry,
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

  it('keeps the comparison divider where it is on screen at every zoom', () => {
    const canvas = { width: 1000, height: 700 };
    const fitted = { width: 800, height: 400 };
    const fit = comparisonGeometry(canvas, fitted, 1, { x: 0, y: 0 }, 50);
    expect(fit).toMatchObject({ x: 500, percent: 50, top: 150, bottom: 550, handleY: 350 });
    expect(fit.imagePercent).toBe(50);
    expect(fit.enhancedPercent).toBe(50);

    // 8x zoom on the image's left edge: the divider has not moved, it now cuts
    // the image 500 of 6400 px from its left, and still splits the view.
    const left = comparisonGeometry(canvas, fitted, 8, { x: 2700, y: 0 }, 50);
    expect(left).toMatchObject({ x: 500, top: 0, bottom: 700, enhancedPercent: 50 });
    expect(left.imagePercent).toBeCloseTo((500 / 6400) * 100);
    // Panned to the right edge, the same screen position shows the right edge.
    const right = comparisonGeometry(canvas, fitted, 8, { x: -2700, y: 0 }, 50);
    expect(right.x).toBe(500);
    expect(right.imagePercent).toBeCloseTo(((5400 + 500) / 6400) * 100);
  });

  it('holds the comparison divider on the visible part of the image', () => {
    const canvas = { width: 1000, height: 700 };
    const fitted = { width: 800, height: 400 };
    // The canvas margin left of the fitted image is not part of the comparison.
    const edge = comparisonGeometry(canvas, fitted, 1, { x: 0, y: 0 }, 2);
    expect(edge).toMatchObject({ x: 100, percent: 10, imagePercent: 0, enhancedPercent: 100 });
    // Zoomed in 3x and panned fully down, the handle stays in the canvas middle.
    expect(comparisonGeometry(canvas, fitted, 3, { x: 0, y: 250 }, 50).handleY).toBe(350);
    // Only the image's bottom 250 px show: the handle stays on them, one radius up.
    const bottom = comparisonGeometry(canvas, fitted, 3, { x: 0, y: -700 }, 50);
    expect(bottom).toMatchObject({ top: 0, bottom: 250, handleY: 231 });
  });
});
