import type { Point, Size } from './viewport';

export interface Region {
  x: number;
  y: number;
  width: number;
  height: number;
  target_width: number;
  target_height: number;
}

/** Map a source crop between output images while preserving its pixel edges. */
export function mapRegion(region: Region, from: Size, to: Size): Region {
  const xScale = to.width / from.width;
  const yScale = to.height / from.height;
  const x = Math.round(region.x * xScale);
  const y = Math.round(region.y * yScale);
  const right = Math.round((region.x + region.width) * xScale);
  const bottom = Math.round((region.y + region.height) * yScale);
  return {
    ...region,
    x,
    y,
    width: Math.max(1, right - x),
    height: Math.max(1, bottom - y),
  };
}

export function visibleRegion(input: {
  image: Size;
  preview: Size;
  cell: Size;
  fit: Size;
  zoom: number;
  pan: Point;
  pixelRatio?: number;
}): Region | null {
  const { image, preview, cell, fit, zoom, pan } = input;
  const pixelRatio = Math.max(1, input.pixelRatio || 1);
  if (
    !preview.width ||
    !preview.height ||
    (fit.width * zoom * pixelRatio <= preview.width &&
      fit.height * zoom * pixelRatio <= preview.height)
  )
    return null;
  const scaleX = (fit.width * zoom) / image.width;
  const scaleY = (fit.height * zoom) / image.height;
  const left = (cell.width - fit.width * zoom) / 2 + pan.x;
  const top = (cell.height - fit.height * zoom) / 2 + pan.y;
  const x = Math.max(0, Math.floor(-left / scaleX));
  const y = Math.max(0, Math.floor(-top / scaleY));
  const right = Math.min(image.width, Math.ceil((cell.width - left) / scaleX));
  const bottom = Math.min(image.height, Math.ceil((cell.height - top) / scaleY));
  if (right <= x || bottom <= y) return null;
  return {
    x,
    y,
    width: right - x,
    height: bottom - y,
    target_width: Math.max(1, Math.min(2048, Math.ceil((right - x) * scaleX * pixelRatio))),
    target_height: Math.max(1, Math.min(2048, Math.ceil((bottom - y) * scaleY * pixelRatio))),
  };
}
