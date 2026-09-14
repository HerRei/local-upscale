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
  grid_width?: number;
  grid_height?: number;
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
  maximumDimension = MAXIMUM_PROGRESSIVE_DIMENSION,
): PixelSize {
  const width = positive(output.width);
  const height = positive(output.height);
  const maximum = Math.max(MINIMUM_DIMENSION, maximumDimension);
  const scale = Math.min(1, maximum / Math.max(width, height));
  return {
    width: Math.max(MINIMUM_DIMENSION, Math.round(width * scale)),
    height: Math.max(MINIMUM_DIMENSION, Math.round(height * scale)),
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
  const right = Math.round(((tile.output_x + tile.output_width) / tile.image_width) * canvas.width);
  const bottom = Math.round(
    ((tile.output_y + tile.output_height) / tile.image_height) * canvas.height,
  );

  return {
    x: left,
    y: top,
    width: Math.max(MINIMUM_DIMENSION, right - left),
    height: Math.max(MINIMUM_DIMENSION, bottom - top),
  };
}

/** Use the same canvas pixel boundaries as the JPEG, without whole-percent rounding. */
export function tilePercentages(tile: OutputTile, canvas?: PixelSize): CanvasTileRect | null {
  const size = canvas ?? { width: tile.image_width, height: tile.image_height };
  const rect = canvasTileRect(tile, size);
  if (!rect) return null;
  const left = Math.max(0, Math.min(size.width, rect.x));
  const top = Math.max(0, Math.min(size.height, rect.y));
  const right = Math.max(left, Math.min(size.width, rect.x + rect.width));
  const bottom = Math.max(top, Math.min(size.height, rect.y + rect.height));
  return {
    x: (left / size.width) * 100,
    y: (top / size.height) * 100,
    width: ((right - left) / size.width) * 100,
    height: ((bottom - top) / size.height) * 100,
  };
}

/** Pending squares follow the worker's non-overlapping core tiles, including edge remainders. */
export function paintTileGrid(
  context: CanvasRenderingContext2D,
  firstTile: OutputTile,
  canvas: PixelSize,
  overSource = false,
): void {
  const gridWidth = firstTile.grid_width || firstTile.output_width;
  const gridHeight = firstTile.grid_height || firstTile.output_height;
  if (
    !Number.isFinite(gridWidth) ||
    !Number.isFinite(gridHeight) ||
    gridWidth <= 0 ||
    gridHeight <= 0 ||
    !canvasTileRect(firstTile, canvas)
  )
    return;
  if (
    (firstTile.output_x !== 0 || firstTile.output_y !== 0) &&
    (!firstTile.grid_width || !firstTile.grid_height)
  )
    return;
  // Below two preview pixels, a grid is unreadable; keep the plain pending surface.
  if (
    (gridWidth / firstTile.image_width) * canvas.width < 2 ||
    (gridHeight / firstTile.image_height) * canvas.height < 2
  )
    return;
  let row = 0;
  for (let y = 0; y < firstTile.image_height; y += gridHeight, row += 1) {
    let column = 0;
    for (let x = 0; x < firstTile.image_width; x += gridWidth, column += 1) {
      const rect = canvasTileRect(
        {
          ...firstTile,
          output_x: x,
          output_y: y,
          output_width: Math.min(gridWidth, firstTile.image_width - x),
          output_height: Math.min(gridHeight, firstTile.image_height - y),
        },
        canvas,
      );
      if (!rect) continue;
      context.fillStyle = overSource
        ? (row + column) % 2
          ? '#10172138'
          : '#10172160'
        : (row + column) % 2
          ? '#17202d'
          : '#101721';
      context.fillRect(rect.x, rect.y, rect.width, rect.height);
    }
  }
}
