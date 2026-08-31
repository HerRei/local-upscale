export interface Size {
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface PanBounds {
  x: number;
  y: number;
}

const MIN_DIMENSION = 1;

function positive(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : MIN_DIMENSION;
}

export function fitSize(canvas: Size, image: Size, padding = 56): Size {
  const availableWidth = Math.max(MIN_DIMENSION, positive(canvas.width) - padding);
  const availableHeight = Math.max(MIN_DIMENSION, positive(canvas.height) - padding);
  const imageWidth = positive(image.width);
  const imageHeight = positive(image.height);
  const scale = Math.min(availableWidth / imageWidth, availableHeight / imageHeight);

  return {
    width: Math.max(MIN_DIMENSION, imageWidth * scale),
    height: Math.max(MIN_DIMENSION, imageHeight * scale)
  };
}

/**
 * Zoom is expressed relative to the fitted view. A large source therefore
 * gets enough headroom to reach at least 1:1 source pixels, while ordinary
 * previews keep the familiar 800% ceiling. Two-times source pixels is useful
 * for inspecting reconstruction artifacts without allowing unbounded CSS
 * transforms.
 */
export function zoomLimits(image: Size, fitted: Size): { min: number; max: number; actual: number } {
  const actual = Math.max(
    positive(image.width) / positive(fitted.width),
    positive(image.height) / positive(fitted.height)
  );
  return {
    // Fit is 1. A tiny source is enlarged at Fit, so its true 1:1 view is
    // below 1 and must remain reachable from the dedicated control.
    min: Math.min(1, actual),
    max: Math.min(64, Math.max(8, actual * 2)),
    actual
  };
}

export function panBounds(canvas: Size, fitted: Size, zoom: number): PanBounds {
  return {
    x: Math.max(0, (positive(fitted.width) * zoom - positive(canvas.width)) / 2),
    y: Math.max(0, (positive(fitted.height) * zoom - positive(canvas.height)) / 2)
  };
}

export function clampPan(point: Point, bounds: PanBounds): Point {
  return {
    x: Math.max(-bounds.x, Math.min(bounds.x, point.x)),
    y: Math.max(-bounds.y, Math.min(bounds.y, point.y))
  };
}

/** Keep the image coordinate below the pointer fixed while zooming. */
export function pointerCenteredPan(
  current: Point,
  pointerFromCanvasCenter: Point,
  currentZoom: number,
  nextZoom: number,
  bounds: PanBounds
): Point {
  const ratio = nextZoom / Math.max(currentZoom, Number.EPSILON);
  return clampPan(
    {
      x: pointerFromCanvasCenter.x - (pointerFromCanvasCenter.x - current.x) * ratio,
      y: pointerFromCanvasCenter.y - (pointerFromCanvasCenter.y - current.y) * ratio
    },
    bounds
  );
}
