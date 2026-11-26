import { describe, expect, it } from 'vitest';
import {
  clampCropRect,
  croppedBoxView,
  croppedSlotDraw,
  slotFrameSize,
  visibleSourceCrop,
} from './compareVideoCrop';
import { fitSize } from './viewport';

describe('compareVideoCrop', () => {
  it('clamps slot crop rectangles strictly within the combined frame bounds', () => {
    const frame = { width: 1280, height: 1444 };
    expect(clampCropRect({ x: -10, y: 724, width: 2000, height: 900 }, frame)).toEqual({
      x: 0,
      y: 724,
      width: 1280,
      height: 720,
    });
    expect(croppedSlotDraw({ x: 0, y: 724, width: 1280, height: 720 }, frame)).toEqual({
      sx: 0,
      sy: 724,
      sw: 1280,
      sh: 720,
      dx: 0,
      dy: 0,
      dw: 1280,
      dh: 720,
    });
  });

  it('maps zoom and pan per side across four cropped regions without crossing into neighbor tiles', () => {
    // 2x2 combined frame: 4 slots of 640x360 separated by a 4px gutter.
    const frame = { width: 1284, height: 724 };
    const slots = [
      { x: 0, y: 0, width: 640, height: 360 },
      { x: 644, y: 0, width: 640, height: 360 },
      { x: 0, y: 364, width: 640, height: 360 },
      { x: 644, y: 364, width: 640, height: 360 },
    ];
    const cell = { width: 400, height: 300 };
    const mainFit = fitSize(cell, { width: 640, height: 360 }, 12);
    const zoom = 2;
    const pan = { x: 80, y: -40 };

    for (const slot of slots) {
      const { fit, pan: boxPan } = croppedBoxView(
        cell,
        slotFrameSize(slot, cell),
        mainFit,
        zoom,
        pan,
      );
      const visible = visibleSourceCrop(slot, frame, cell, fit, zoom, boxPan);
      expect(visible).not.toBeNull();
      expect(visible!.sx).toBeGreaterThanOrEqual(slot.x);
      expect(visible!.sy).toBeGreaterThanOrEqual(slot.y);
      expect(visible!.sx + visible!.sw).toBeLessThanOrEqual(slot.x + slot.width);
      expect(visible!.sy + visible!.sh).toBeLessThanOrEqual(slot.y + slot.height);
      expect(visible!.dw).toBeCloseTo(cell.width, 4);
      expect(visible!.dh).toBeCloseTo(Math.min(cell.height, fit.height * zoom), 4);
    }
  });

  it('scales pan proportionally for mixed-aspect-ratio cropped boxes', () => {
    const cell = { width: 400, height: 400 };
    const wide = { width: 1280, height: 720 };
    const tall = { width: 720, height: 1280 };
    const mainFit = fitSize(cell, wide, 12);
    const wideView = croppedBoxView(cell, wide, mainFit, 2, { x: 100, y: 50 });
    const tallView = croppedBoxView(cell, tall, mainFit, 2, { x: 100, y: 50 });

    expect(wideView.fit.width).toBeCloseTo(388);
    expect(tallView.fit.height).toBeCloseTo(388);
    // Tall box is narrower, so its horizontal pan scales proportionally and stays clamped.
    expect(tallView.pan.x).toBeLessThan(wideView.pan.x);
    expect(tallView.pan.y).toBeGreaterThan(wideView.pan.y);
  });
});
