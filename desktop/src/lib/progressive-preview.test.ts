import { describe, expect, it } from 'vitest';

import {
  boundedPreviewSize,
  canvasTileRect,
  tilePercentages
} from './progressive-preview';

describe('progressive tiled preview', () => {
  it('uses the released UI maximum without changing output aspect ratio', () => {
    expect(boundedPreviewSize({ width: 12096, height: 7856 })).toEqual({
      width: 1600,
      height: 1039
    });
    expect(boundedPreviewSize({ width: 128, height: 96 })).toEqual({
      width: 128,
      height: 96
    });
  });

  it('places each completed output tile into the full bounded mosaic', () => {
    expect(
      canvasTileRect(
        {
          output_x: 4096,
          output_y: 2048,
          output_width: 1024,
          output_height: 1024,
          image_width: 12096,
          image_height: 7856
        },
        { width: 1600, height: 1039 }
      )
    ).toEqual({ x: 542, y: 271, width: 135, height: 135 });
  });

  it('rejects incomplete geometry and bounds the active-tile outline', () => {
    expect(
      canvasTileRect(
        {
          output_x: 0,
          output_y: 0,
          output_width: 0,
          output_height: 20,
          image_width: 100,
          image_height: 100
        },
        { width: 100, height: 100 }
      )
    ).toBeNull();

    expect(
      tilePercentages({
        output_x: 80,
        output_y: 90,
        output_width: 40,
        output_height: 30,
        image_width: 100,
        image_height: 100
      })
    ).toEqual({ x: 80, y: 90, width: 20, height: 10 });
  });
});
