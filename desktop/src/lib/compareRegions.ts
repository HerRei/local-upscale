import type { Point, Size } from './viewport';

export interface Region {
  x: number;
  y: number;
  width: number;
  height: number;
  target_width: number;
  target_height: number;
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
