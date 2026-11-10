import { describe, expect, it } from 'vitest';
import { visibleRegion } from './compareRegions';

const base = {
  cell: { width: 800, height: 600 },
  fit: { width: 800, height: 600 },
  pan: { x: 0, y: 0 },
};

describe('visibleRegion', () => {
  it('asks for nothing while a slot has no decoded preview, and never produces NaN', () => {
    for (const image of [
      { width: 4000, height: 3000 },
      { width: 0, height: 0 },
    ]) {
      expect(
        visibleRegion({ ...base, image, preview: { width: 0, height: 0 }, zoom: 4 }),
      ).toBeNull();
    }
    expect(
      visibleRegion({
        ...base,
        image: { width: 0, height: 0 },
        preview: { width: 100, height: 100 },
        zoom: 4,
      }),
    ).toBeNull();
  });

  it('maps the same view of a 1x and a 4x output to the same part of the image', () => {
    const view = { ...base, zoom: 4, pan: { x: 0, y: 0 } };
    const small = visibleRegion({
      ...view,
      image: { width: 1000, height: 750 },
      preview: { width: 500, height: 375 },
    });
    const large = visibleRegion({
      ...view,
      image: { width: 4000, height: 3000 },
      preview: { width: 2048, height: 1536 },
    });
    expect(small && large).toBeTruthy();
    expect(large!.x).toBe(small!.x * 4);
    expect(large!.width).toBe(small!.width * 4);
  });
});
