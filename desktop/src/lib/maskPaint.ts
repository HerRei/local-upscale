import type { Size } from './viewport';

/** One stroke of Dust & scratches, as the host keeps it (`repair_mask.rs`). */
export interface RepairStroke {
  mode: 'paint' | 'erase';
  /** Brush width in source pixels, 1 to 1024. */
  size: number;
  /** x0, y0, x1, y1, … in source pixels with one decimal; two numbers are a dot. */
  points: number[];
}

/** The host's limits for one photo (`MAX_STROKES`, `MAX_NUMBERS` in `repair_mask.rs`). */
export const MAX_STROKES = 2000;
export const MAX_NUMBERS = 100_000;

const SMALLEST_BRUSH = 2;
const BRUSH_STEP = 1.25;

function oneDecimal(value: number): number {
  return Math.round(value * 10) / 10;
}

function held(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** The brush sizes a photo allows, in source pixels, and the size painting starts with. */
export function brushLimits(image: Size): { min: number; max: number; first: number } {
  const longer = Math.max(image.width, image.height, 1);
  return {
    min: SMALLEST_BRUSH,
    max: Math.round(held(longer / 20, 40, 400)),
    first: Math.round(held(longer / 250, 4, 64)),
  };
}

/** `[` divides the size by 1.25 and `]` multiplies it, inside the photo's limits. */
export function stepBrush(size: number, key: '[' | ']', image: Size): number {
  const { min, max } = brushLimits(image);
  return oneDecimal(held(key === '[' ? size / BRUSH_STEP : size * BRUSH_STEP, min, max));
}

/**
 * The stroke with the point added, held to the picture and rounded to one decimal; a point
 * closer than an eighth of the brush to the last one is dropped.
 */
export function addPoint(stroke: RepairStroke, x: number, y: number, image: Size): RepairStroke {
  const px = oneDecimal(held(x, 0, image.width));
  const py = oneDecimal(held(y, 0, image.height));
  const count = stroke.points.length;
  if (count >= 2) {
    const dx = px - stroke.points[count - 2];
    const dy = py - stroke.points[count - 1];
    if (Math.hypot(dx, dy) < stroke.size / 8) return stroke;
  }
  return { ...stroke, points: [...stroke.points, px, py] };
}

/** The `d` of the stroke's path; one point is a line of no length, which its round cap draws as a dot. */
export function strokePath(stroke: RepairStroke): string {
  const pairs: string[] = [];
  for (let index = 0; index + 1 < stroke.points.length; index += 2) {
    pairs.push(`${stroke.points[index]} ${stroke.points[index + 1]}`);
  }
  if (pairs.length === 0) return '';
  if (pairs.length === 1) pairs.push(pairs[0]);
  return `M${pairs[0]} L${pairs.slice(1).join(' ')}`;
}

/** The marks the photo has: its paint strokes, as the host counts them. */
export function paintMarks(strokes: readonly RepairStroke[]): number {
  return strokes.filter((stroke) => stroke.mode === 'paint').length;
}

/** Another stroke still fits the host's limits. */
export function roomFor(strokes: readonly RepairStroke[], stroke: RepairStroke): boolean {
  const numbers = strokes.reduce((total, entry) => total + entry.points.length, 0);
  return strokes.length < MAX_STROKES && numbers + stroke.points.length <= MAX_NUMBERS;
}

/** One photo's strokes, the lists undo returns to (`past`) and the ones redo brings back. */
export interface PaintHistory {
  strokes: RepairStroke[];
  undone: RepairStroke[][];
  past: RepairStroke[][];
}

export function emptyHistory(strokes: RepairStroke[] = []): PaintHistory {
  return { strokes, undone: [], past: [] };
}

/** A new list (a stroke, or Clear's empty one) is one step; it empties redo. */
export function commit(history: PaintHistory, strokes: RepairStroke[]): PaintHistory {
  return { strokes, undone: [], past: [...history.past, history.strokes] };
}

export function undo(history: PaintHistory): PaintHistory {
  const previous = history.past.at(-1);
  if (!previous) return history;
  return {
    strokes: previous,
    undone: [...history.undone, history.strokes],
    past: history.past.slice(0, -1),
  };
}

export function redo(history: PaintHistory): PaintHistory {
  const next = history.undone.at(-1);
  if (!next) return history;
  return {
    strokes: next,
    undone: history.undone.slice(0, -1),
    past: [...history.past, history.strokes],
  };
}
