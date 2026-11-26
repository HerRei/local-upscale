import { clampPan, fitSize, panBounds, type Point, type Size } from './viewport';

export interface VideoCropRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CroppedVideoDraw {
  sx: number;
  sy: number;
  sw: number;
  sh: number;
  dx: number;
  dy: number;
  dw: number;
  dh: number;
}

/** Clamp a slot's crop rectangle safely inside the combined video frame. */
export function clampCropRect(rect: VideoCropRect, frame: Size): VideoCropRect {
  const maxWidth = Math.max(1, Math.floor(frame.width));
  const maxHeight = Math.max(1, Math.floor(frame.height));
  const x = Math.max(0, Math.min(maxWidth - 1, Math.round(rect.x)));
  const y = Math.max(0, Math.min(maxHeight - 1, Math.round(rect.y)));
  const width = Math.max(1, Math.min(maxWidth - x, Math.round(rect.width)));
  const height = Math.max(1, Math.min(maxHeight - y, Math.round(rect.height)));
  return { x, y, width, height };
}

/** Native pixel size of a slot from its crop metadata, falling back to media dimensions. */
export function slotFrameSize(rect: VideoCropRect | undefined, fallback: Size): Size {
  if (rect && rect.width > 0 && rect.height > 0) {
    return { width: rect.width, height: rect.height };
  }
  return {
    width: Math.max(1, fallback.width || 1),
    height: Math.max(1, fallback.height || 1),
  };
}

/** Source-to-canvas draw rectangle for copying one slot out of the combined video frame. */
export function croppedSlotDraw(rect: VideoCropRect, frame: Size): CroppedVideoDraw {
  const clamped = clampCropRect(rect, frame);
  return {
    sx: clamped.x,
    sy: clamped.y,
    sw: clamped.width,
    sh: clamped.height,
    dx: 0,
    dy: 0,
    dw: clamped.width,
    dh: clamped.height,
  };
}

/** Fitted box size and clamped pan for one side in Boxes layout, scaled from `mainFit`. */
export function croppedBoxView(
  cell: Size,
  slotSize: Size,
  mainFit: Size,
  zoom: number,
  pan: Point,
  padding = 12,
): { fit: Size; pan: Point } {
  const fit = fitSize(cell, slotSize, padding);
  const commonWidth = Math.max(1, mainFit.width);
  const commonHeight = Math.max(1, mainFit.height);
  const scaledPan = clampPan(
    { x: (pan.x * fit.width) / commonWidth, y: (pan.y * fit.height) / commonHeight },
    panBounds(cell, fit, zoom),
  );
  return { fit, pan: scaledPan };
}

/**
 * Visible sub-rectangle of a cropped slot inside `viewport` at `zoom` and `pan`,
 * mapped back into combined-video source coordinates and viewport destination coordinates.
 */
export function visibleSourceCrop(
  rect: VideoCropRect,
  frame: Size,
  viewport: Size,
  fit: Size,
  zoom: number,
  pan: Point,
): CroppedVideoDraw | null {
  const clamped = clampCropRect(rect, frame);
  const scale = Math.max(Number.EPSILON, zoom);
  const stageWidth = Math.max(1, fit.width) * scale;
  const stageHeight = Math.max(1, fit.height) * scale;
  const stageLeft = viewport.width / 2 + pan.x - stageWidth / 2;
  const stageTop = viewport.height / 2 + pan.y - stageHeight / 2;
  const dx0 = Math.max(0, stageLeft);
  const dy0 = Math.max(0, stageTop);
  const dx1 = Math.min(viewport.width, stageLeft + stageWidth);
  const dy1 = Math.min(viewport.height, stageTop + stageHeight);
  if (dx1 <= dx0 || dy1 <= dy0) return null;

  const u0 = (dx0 - stageLeft) / stageWidth;
  const u1 = (dx1 - stageLeft) / stageWidth;
  const v0 = (dy0 - stageTop) / stageHeight;
  const v1 = (dy1 - stageTop) / stageHeight;
  return {
    sx: clamped.x + u0 * clamped.width,
    sy: clamped.y + v0 * clamped.height,
    sw: (u1 - u0) * clamped.width,
    sh: (v1 - v0) * clamped.height,
    dx: dx0,
    dy: dy0,
    dw: dx1 - dx0,
    dh: dy1 - dy0,
  };
}
