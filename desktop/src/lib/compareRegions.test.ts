import { describe, expect, it } from 'vitest';
import { cropStyle, previewRegionsFor, sameRegion, visibleRegion } from './compareRegions';

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

describe('previewRegionsFor and alignment helpers', () => {
  it('aligns Original and 4x result crops to identical stage percentages and respects devicePixelRatio', () => {
    const originalImage = { width: 3264, height: 4896 };
    const resultImage = { width: 13056, height: 19584 };
    const preview = { width: 1365, height: 2048 };
    const cell = { width: 800, height: 800 };
    const fit = { width: 496, height: 744 };

    // At 1x zoom with pixelRatio=2, 744 * 2 = 1488 <= 2048 -> null.
    expect(
      previewRegionsFor({
        cell,
        fit,
        zoom: 1,
        pan: { x: 0, y: 0 },
        pixelRatio: 2,
        comparisonView: 'split',
        peeking: false,
        singleFrame: false,
        referenceIsResult: false,
        original: { image: originalImage, preview },
        showing: { image: resultImage, preview },
      }),
    ).toEqual({ original: null, showing: null, against: null });

    // At 2x zoom with pixelRatio=2, 744 * 2 * 2 = 2976 > 2048 -> requests both sides!
    const regions = previewRegionsFor({
      cell,
      fit,
      zoom: 2,
      pan: { x: 0, y: 0 },
      pixelRatio: 2,
      comparisonView: 'split',
      peeking: false,
      singleFrame: false,
      referenceIsResult: false,
      original: { image: originalImage, preview },
      showing: { image: resultImage, preview },
    });
    expect(regions.original).not.toBeNull();
    expect(regions.showing).not.toBeNull();
    expect(regions.against).toBeNull();
    const percents = (style: string) => [...style.matchAll(/([\d.]+)%/g)].map((m) => Number(m[1]));
    const origPct = percents(cropStyle(regions.original!, originalImage));
    const showPct = percents(cropStyle(regions.showing!, resultImage));
    for (let i = 0; i < 4; i++) {
      expect(showPct[i]).toBeCloseTo(origPct[i], 1);
    }
    expect(sameRegion(regions.original, { ...regions.original! })).toBe(true);
    expect(sameRegion(regions.original, { ...regions.original!, x: regions.original!.x + 1 })).toBe(
      false,
    );
  });

  it('requests against and showing when comparing two results, and original only when peeking', () => {
    const original = {
      image: { width: 3264, height: 4896 },
      preview: { width: 1365, height: 2048 },
    };
    const showing = {
      image: { width: 13056, height: 19584 },
      preview: { width: 1365, height: 2048 },
    };
    const against = {
      image: { width: 6528, height: 9792 },
      preview: { width: 1365, height: 2048 },
    };
    const baseInput = {
      cell: { width: 800, height: 800 },
      fit: { width: 496, height: 744 },
      zoom: 4,
      pan: { x: 0, y: 0 },
      pixelRatio: 1,
      comparisonView: 'split' as const,
      singleFrame: false,
      referenceIsResult: true,
      original,
      showing,
      against,
    };

    const split = previewRegionsFor({ ...baseInput, peeking: false });
    expect(split.original).toBeNull();
    expect(split.showing).not.toBeNull();
    expect(split.against).not.toBeNull();

    const peek = previewRegionsFor({ ...baseInput, peeking: true });
    expect(peek.original).not.toBeNull();
    expect(peek.showing).not.toBeNull();
    expect(peek.against).not.toBeNull();
  });
});
