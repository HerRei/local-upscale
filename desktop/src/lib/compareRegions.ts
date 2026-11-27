import type { Point, Size } from './viewport';

export interface Region {
  x: number;
  y: number;
  width: number;
  height: number;
  target_width: number;
  target_height: number;
}

export interface RegionResult {
  bounds: Region;
  data_url: string;
}

export interface PreviewRegions {
  original: Region | null;
  showing: Region | null;
  against: Region | null;
}

export function sameRegion(a: Region | null | undefined, b: Region | null | undefined): boolean {
  return (
    !!a &&
    !!b &&
    a.x === b.x &&
    a.y === b.y &&
    a.width === b.width &&
    a.height === b.height &&
    a.target_width === b.target_width &&
    a.target_height === b.target_height
  );
}

export function cropStyle(bounds: Region, image: Size): string {
  return `left:${(100 * bounds.x) / image.width}%;top:${(100 * bounds.y) / image.height}%;width:${(100 * bounds.width) / image.width}%;height:${(100 * bounds.height) / image.height}%`;
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

export function previewRegionsFor(input: {
  cell: Size;
  fit: Size;
  zoom: number;
  pan: Point;
  pixelRatio?: number;
  comparisonView: 'original' | 'split' | 'enhanced';
  peeking: boolean;
  singleFrame: boolean;
  referenceIsResult: boolean;
  original?: { image: Size; preview: Size } | null;
  showing?: { image: Size; preview: Size } | null;
  against?: { image: Size; preview: Size } | null;
}): PreviewRegions {
  const common = {
    cell: input.cell,
    fit: input.fit,
    zoom: input.zoom,
    pan: input.pan,
    pixelRatio: input.pixelRatio,
  };
  const wantOriginal =
    !input.singleFrame &&
    Boolean(input.original) &&
    (input.peeking ||
      !input.showing ||
      (!input.referenceIsResult && input.comparisonView !== 'enhanced'));
  const wantShowing = Boolean(input.showing) && input.comparisonView !== 'original';
  const wantAgainst =
    !input.singleFrame &&
    input.referenceIsResult &&
    Boolean(input.against) &&
    input.comparisonView !== 'enhanced';
  return {
    original:
      wantOriginal && input.original ? visibleRegion({ ...common, ...input.original }) : null,
    showing: wantShowing && input.showing ? visibleRegion({ ...common, ...input.showing }) : null,
    against: wantAgainst && input.against ? visibleRegion({ ...common, ...input.against }) : null,
  };
}
