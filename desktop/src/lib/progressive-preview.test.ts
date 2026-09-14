import { describe, expect, it } from 'vitest';

import {
  boundedPreviewSize,
  canvasTileRect,
  paintTileGrid,
  tilePercentages,
} from './progressive-preview';

describe('progressive tiled preview', () => {
  it.each([1, 4])(
    'keeps %dx image tiles aligned after returning at a narrow edge tile',
    (scale) => {
      const canvas = boundedPreviewSize({ width: 1982 * scale, height: 1361 * scale });
      const tile = {
        output_x: 1792 * scale,
        output_y: 1024 * scale,
        output_width: 190 * scale,
        output_height: 256 * scale,
        image_width: 1982 * scale,
        image_height: 1361 * scale,
        grid_width: 256 * scale,
        grid_height: 256 * scale,
      };
      const cells: number[][] = [];
      paintTileGrid(
        {
          fillRect: (...rect: number[]) => cells.push(rect),
        } as unknown as CanvasRenderingContext2D,
        tile,
        canvas,
      );
      expect(cells).toHaveLength(8 * 6);
      const rect = canvasTileRect(tile, canvas)!;
      const outline = tilePercentages(tile, canvas)!;
      expect(cells[4 * 8 + 7]).toEqual([rect.x, rect.y, rect.width, rect.height]);
      expect((outline.x / 100) * canvas.width).toBeCloseTo(rect.x, 10);
      expect((outline.width / 100) * canvas.width).toBeCloseTo(rect.width, 10);
      expect(cells.at(-1)![0] + cells.at(-1)![2]).toBe(canvas.width);
      expect(cells.at(-1)![1] + cells.at(-1)![3]).toBe(canvas.height);
    },
  );

  it('uses the released UI maximum without changing output aspect ratio', () => {
    expect(boundedPreviewSize({ width: 12096, height: 7856 })).toEqual({
      width: 1600,
      height: 1039,
    });
    expect(boundedPreviewSize({ width: 128, height: 96 })).toEqual({
      width: 128,
      height: 96,
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
          image_height: 7856,
        },
        { width: 1600, height: 1039 },
      ),
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
          image_height: 100,
        },
        { width: 100, height: 100 },
      ),
    ).toBeNull();

    expect(
      tilePercentages({
        output_x: 80,
        output_y: 90,
        output_width: 40,
        output_height: 30,
        image_width: 100,
        image_height: 100,
      }),
    ).toEqual({ x: 80, y: 90, width: 20, height: 10 });
  });

  it('aligns the outline and pending squares with real portrait-video tile pixels', () => {
    const canvas = { width: 900, height: 1600 };
    const first = {
      output_x: 0,
      output_y: 0,
      output_width: 256,
      output_height: 256,
      image_width: 8640,
      image_height: 15360,
    };
    const cells: number[][] = [];
    paintTileGrid(
      {
        fillRect: (...rect: number[]) => {
          cells.push(rect);
        },
      } as unknown as CanvasRenderingContext2D,
      first,
      canvas,
    );
    expect(cells.length).toBe(34 * 60);
    for (const [column, row] of [
      [0, 0],
      [17, 32],
      [33, 59],
    ]) {
      const tile = {
        ...first,
        output_x: column * 256,
        output_y: row * 256,
        output_width: Math.min(256, 8640 - column * 256),
      };
      const rect = canvasTileRect(tile, canvas)!;
      const outline = tilePercentages(tile, canvas)!;
      expect((outline.x / 100) * canvas.width).toBeCloseTo(rect.x, 10);
      expect((outline.width / 100) * canvas.width).toBeCloseTo(rect.width, 10);
      expect((outline.y / 100) * canvas.height).toBeCloseTo(rect.y, 10);
      expect((outline.height / 100) * canvas.height).toBeCloseTo(rect.height, 10);
      expect(cells[row * 34 + column]).toEqual([rect.x, rect.y, rect.width, rect.height]);
    }
    expect(cells.at(-1)![0] + cells.at(-1)![2]).toBe(900);
    expect(cells.at(-1)![1] + cells.at(-1)![3]).toBe(1600);
  });
});
