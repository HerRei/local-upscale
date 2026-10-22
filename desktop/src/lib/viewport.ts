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
    height: Math.max(MIN_DIMENSION, imageHeight * scale),
  };
}

/**
 * Zoom is expressed relative to the fitted view. A large source therefore
 * gets enough headroom to reach at least 1:1 source pixels, while ordinary
 * previews keep the familiar 800% ceiling. Two-times source pixels is useful
 * for inspecting reconstruction artifacts without allowing unbounded CSS
 * transforms.
 */
export function zoomLimits(
  image: Size,
  fitted: Size,
): { min: number; max: number; actual: number } {
  const actual = Math.max(
    positive(image.width) / positive(fitted.width),
    positive(image.height) / positive(fitted.height),
  );
  return {
    // Fit is 1. A tiny source is enlarged at Fit, so its true 1:1 view is
    // below 1 and must remain reachable from the dedicated control.
    min: Math.min(1, actual),
    max: Math.min(64, Math.max(8, actual * 2)),
    actual,
  };
}

export function panBounds(canvas: Size, fitted: Size, zoom: number): PanBounds {
  return {
    x: Math.max(0, (positive(fitted.width) * zoom - positive(canvas.width)) / 2),
    y: Math.max(0, (positive(fitted.height) * zoom - positive(canvas.height)) / 2),
  };
}

export function clampPan(point: Point, bounds: PanBounds): Point {
  return {
    x: Math.max(-bounds.x, Math.min(bounds.x, point.x)),
    y: Math.max(-bounds.y, Math.min(bounds.y, point.y)),
  };
}

export interface ComparisonGeometry {
  /** Divider position in canvas pixels from the left edge. */
  x: number;
  /** The same position in percent of the canvas width. */
  percent: number;
  /** Visible part of the image, in canvas pixels. */
  left: number;
  right: number;
  top: number;
  bottom: number;
  /** Handle centre in canvas pixels from the top. */
  handleY: number;
  /** Where the divider cuts the image, in percent of the image width. */
  imagePercent: number;
  /** Share of the visible image width that shows the result. */
  enhancedPercent: number;
}

/**
 * The comparison divider lives in canvas coordinates: `compare` is a
 * percentage of the canvas width, so zooming and panning move both images
 * under a divider that keeps its place, thickness and handle. It is held on
 * the visible part of the image, so a deep zoom always shows both sides.
 */
export function comparisonGeometry(
  canvas: Size,
  fitted: Size,
  zoom: number,
  pan: Point,
  compare: number,
  handleRadius = 19,
): ComparisonGeometry {
  const scale = positive(zoom);
  const canvasWidth = positive(canvas.width);
  const canvasHeight = positive(canvas.height);
  const width = positive(fitted.width) * scale;
  const height = positive(fitted.height) * scale;
  const left = canvasWidth / 2 + pan.x - width / 2;
  const top = canvasHeight / 2 + pan.y - height / 2;
  const visibleLeft = Math.max(0, Math.min(canvasWidth, left));
  const visibleRight = Math.max(visibleLeft, Math.min(canvasWidth, left + width));
  const visibleTop = Math.max(0, Math.min(canvasHeight, top));
  const visibleBottom = Math.max(visibleTop, Math.min(canvasHeight, top + height));
  const wanted = (canvasWidth * (Number.isFinite(compare) ? compare : 50)) / 100;
  const x = Math.max(visibleLeft, Math.min(visibleRight, wanted));
  // The handle follows the vertical middle of the canvas, not of the image.
  const handleY =
    visibleBottom - visibleTop >= 2 * handleRadius
      ? Math.max(
          visibleTop + handleRadius,
          Math.min(visibleBottom - handleRadius, canvasHeight / 2),
        )
      : (visibleTop + visibleBottom) / 2;
  const visibleWidth = visibleRight - visibleLeft;
  return {
    x,
    percent: (x / canvasWidth) * 100,
    left: visibleLeft,
    right: visibleRight,
    top: visibleTop,
    bottom: visibleBottom,
    handleY,
    imagePercent: Math.max(0, Math.min(100, ((x - left) / width) * 100)),
    enhancedPercent: visibleWidth > 0 ? ((visibleRight - x) / visibleWidth) * 100 : 0,
  };
}

/** Keep the image coordinate below the pointer fixed while zooming. */
export function pointerCenteredPan(
  current: Point,
  pointerFromCanvasCenter: Point,
  currentZoom: number,
  nextZoom: number,
  bounds: PanBounds,
): Point {
  const ratio = nextZoom / Math.max(currentZoom, Number.EPSILON);
  return clampPan(
    {
      x: pointerFromCanvasCenter.x - (pointerFromCanvasCenter.x - current.x) * ratio,
      y: pointerFromCanvasCenter.y - (pointerFromCanvasCenter.y - current.y) * ratio,
    },
    bounds,
  );
}
