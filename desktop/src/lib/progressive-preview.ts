export interface PixelSize {
  width: number;
  height: number;
}

export interface OutputTile {
  output_x: number;
  output_y: number;
  output_width: number;
  output_height: number;
  image_width: number;
  image_height: number;
}

export interface CanvasTileRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

const MINIMUM_DIMENSION = 1;
export const MAXIMUM_PROGRESSIVE_DIMENSION = 1600;

function positive(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : MINIMUM_DIMENSION;
}

/**
 * Match the released preview provider: keep the full output aspect ratio while
 * bounding the live mosaic so thousands of tiles never allocate the full
 * upscaled image inside the UI process.
 */
export function boundedPreviewSize(
  output: PixelSize,
  maximumDimension = MAXIMUM_PROGRESSIVE_DIMENSION
): PixelSize {
  const width = positive(output.width);
  const height = positive(output.height);
  const maximum = Math.max(MINIMUM_DIMENSION, maximumDimension);
  const scale = Math.min(1, maximum / Math.max(width, height));
  return {
    width: Math.max(MINIMUM_DIMENSION, Math.round(width * scale)),
    height: Math.max(MINIMUM_DIMENSION, Math.round(height * scale))
  };
}

/** Map model-output coordinates onto the bounded live-preview canvas. */
export function canvasTileRect(tile: OutputTile, canvas: PixelSize): CanvasTileRect | null {
  if (
    tile.image_width <= 0 ||
    tile.image_height <= 0 ||
    tile.output_width <= 0 ||
    tile.output_height <= 0
  ) {
    return null;
  }

  const left = Math.round((tile.output_x / tile.image_width) * canvas.width);
  const top = Math.round((tile.output_y / tile.image_height) * canvas.height);
  const right = Math.round(
    ((tile.output_x + tile.output_width) / tile.image_width) * canvas.width
  );
  const bottom = Math.round(
    ((tile.output_y + tile.output_height) / tile.image_height) * canvas.height
  );

  return {
    x: left,
    y: top,
    width: Math.max(MINIMUM_DIMENSION, right - left),
    height: Math.max(MINIMUM_DIMENSION, bottom - top)
  };
}

/** Percentage geometry for the active-tile outline drawn above the canvas. */
export function tilePercentages(tile: OutputTile): CanvasTileRect | null {
  const rect = canvasTileRect(tile, { width: 100, height: 100 });
  if (!rect) return null;
  return {
    x: Math.max(0, Math.min(100, rect.x)),
    y: Math.max(0, Math.min(100, rect.y)),
    width: Math.max(0, Math.min(100 - rect.x, rect.width)),
    height: Math.max(0, Math.min(100 - rect.y, rect.height))
  };
}
