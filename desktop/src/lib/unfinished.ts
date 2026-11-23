/**
 * A job that was stopped and kept: how much of it is there, in words, and where a
 * photo's finished tiles lie. Nothing here knows the layout.
 */
import { formatVideoClock } from './format';
import type { ResultSummary } from './types';

/** A finished tile as the host reports it: x, y, width, height in pixels of the saved file. */
export type TileRect = [number, number, number, number];

const counted = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`;

/** Under this many seconds a kept part is counted in frames: "0:00" says nothing. */
const FRAMES_BELOW_SECONDS = 1;

/**
 * The kept part of a video: "2 frames", "2 of 54 frames", "0:05", "0:05 of 0:12". Zero
 * seconds or frames mean the host did not record them. '' when nothing is known.
 */
export function keptVideoText(
  kept: { seconds?: number | null; frames?: number | null; fps?: number },
  totalSeconds = 0,
): string {
  const fps = kept.fps && kept.fps > 0 ? kept.fps : 0;
  const frames = kept.frames && kept.frames > 0 ? Math.round(kept.frames) : 0;
  const seconds =
    kept.seconds && kept.seconds > 0 ? kept.seconds : frames && fps ? frames / fps : 0;
  if (!seconds) return frames ? counted(frames, 'frame') : '';
  if (seconds >= FRAMES_BELOW_SECONDS) {
    const clock = formatVideoClock(Math.floor(seconds));
    const total = totalSeconds > seconds ? formatVideoClock(totalSeconds) : '';
    return total && total !== clock ? `${clock} of ${total}` : clock;
  }
  const shown = frames || (fps ? Math.max(1, Math.round(seconds * fps)) : 0);
  if (!shown) return 'under a second';
  const all = fps && totalSeconds > seconds ? Math.round(totalSeconds * fps) : 0;
  return all > shown ? `${shown} of ${counted(all, 'frame')}` : counted(shown, 'frame');
}

/** The kept part of a photo: "12 of 40 tiles". '' when the host recorded no tiles. */
export function keptTilesText(finished: number, total: number): string {
  if (total > 0 && finished >= 0)
    return `${Math.min(finished, total)} of ${counted(total, 'tile')}`;
  return finished > 0 ? counted(finished, 'tile') : '';
}

/** Seconds a stopped video kept; older results carry them only in the file name. */
function partialKeptSeconds(
  result: Pick<ResultSummary, 'partial' | 'kept_seconds' | 'output_name'>,
): number | null {
  if (!result.partial) return null;
  if (typeof result.kept_seconds === 'number' && Number.isFinite(result.kept_seconds)) {
    return Math.max(0, result.kept_seconds);
  }
  const match = /_partial_(\d+)m(\d{2})s(?:\.[^.]+)?$/i.exec(result.output_name ?? '');
  if (!match) return null;
  const minutes = Number(match[1]);
  const secs = Number(match[2]);
  return secs < 60 ? minutes * 60 + secs : null;
}

/** What is there of a kept result, without the word Unfinished. */
function keptText(result: ResultSummary, totalDuration = 0, sourceFps = 0): string {
  if (!result.partial) return '';
  if (result.kind !== 'video' && (result.finished_tiles || result.total_tiles))
    return keptTilesText(result.finished_tiles?.length ?? 0, result.total_tiles ?? 0);
  return keptVideoText(
    {
      seconds: partialKeptSeconds(result),
      frames: result.kept_frames,
      fps: result.output_fps || sourceFps,
    },
    Math.max(result.output_duration_seconds || 0, totalDuration || 0),
  );
}

/** The mark of a kept result: "Unfinished · 2 of 54 frames". '' for a finished one. */
export function unfinishedLabel(result: ResultSummary, totalDuration = 0, sourceFps = 0): string {
  if (!result.partial) return '';
  const kept = keptText(result, totalDuration, sourceFps);
  return kept ? `Unfinished · ${kept}` : 'Unfinished';
}

/** What a view needs to mark an unfinished result. */
export interface UnfinishedMark {
  label: string;
  /** A photo's finished tiles; absent for a video or when the host recorded none. */
  tiles?: TileRect[];
  /** Frames a video kept, when the host counted them. */
  frames?: number;
}

export function unfinishedMark(
  result: ResultSummary | undefined,
  totalDuration = 0,
  sourceFps = 0,
): UnfinishedMark | undefined {
  if (!result?.partial) return undefined;
  return {
    label: unfinishedLabel(result, totalDuration, sourceFps),
    ...(result.kind !== 'video' && result.finished_tiles ? { tiles: result.finished_tiles } : {}),
    ...(result.kept_frames && result.kept_frames > 0 ? { frames: result.kept_frames } : {}),
  };
}

/** A photo's finished tiles with the pixel size of the saved file they are measured in. */
export interface PaneTiles {
  tiles: TileRect[];
  width: number;
  height: number;
}

/** The mark the preview shows, and the side of the pair it belongs to. */
export interface PaneUnfinished {
  side: 'showing' | 'reference';
  label: string;
}

/** SVG paths, in pixels of the saved file, that mark a photo's finished tiles. */
export interface TileMarks {
  /** The outline of every finished tile. */
  grid: string;
  /** The whole picture with the finished tiles cut out (even-odd). */
  rest: string;
  /** The line where finished tiles meet the part that was not reached. */
  frontier: string;
}

/** Past this many cells the frontier is left out; the tiles and the rest are still drawn. */
const MAX_CELLS = 250_000;

const rectPath = (x0: number, y0: number, x1: number, y1: number) =>
  `M${x0} ${y0}H${x1}V${y1}H${x0}Z`;

/**
 * The tiles need not form a regular grid: their edges cut the picture into cells, a
 * cell is finished when a tile covers it, and the frontier runs between finished and
 * unfinished cells. The picture's own border is not part of it.
 */
export function tileMarks(tiles: TileRect[], width: number, height: number): TileMarks | null {
  if (!(width > 0) || !(height > 0)) return null;
  const frame = rectPath(0, 0, width, height);
  const boxes = tiles
    .map(([x, y, w, h]) => ({
      x0: Math.max(0, Math.min(width, Math.round(x))),
      y0: Math.max(0, Math.min(height, Math.round(y))),
      x1: Math.max(0, Math.min(width, Math.round(x + w))),
      y1: Math.max(0, Math.min(height, Math.round(y + h))),
    }))
    .filter((box) => box.x1 > box.x0 && box.y1 > box.y0);
  const grid = boxes.map((box) => rectPath(box.x0, box.y0, box.x1, box.y1)).join('');
  const edges = (values: number[], end: number) =>
    [...new Set([0, end, ...values])].sort((a, b) => a - b);
  const xs = edges(
    boxes.flatMap((box) => [box.x0, box.x1]),
    width,
  );
  const ys = edges(
    boxes.flatMap((box) => [box.y0, box.y1]),
    height,
  );
  const columns = xs.length - 1;
  const rows = ys.length - 1;
  if (columns * rows > MAX_CELLS) return { grid, rest: frame + grid, frontier: '' };

  const column = new Map(xs.map((value, index) => [value, index]));
  const row = new Map(ys.map((value, index) => [value, index]));
  const done = new Uint8Array(columns * rows);
  for (const box of boxes) {
    for (let j = row.get(box.y0)!; j < row.get(box.y1)!; j += 1)
      done.fill(1, j * columns + column.get(box.x0)!, j * columns + column.get(box.x1)!);
  }
  const finished = (i: number, j: number) => done[j * columns + i] === 1;

  let rest = frame;
  let frontier = '';
  for (let j = 0; j < rows; j += 1) {
    for (let i = 0; i < columns; i += 1) {
      if (!finished(i, j)) continue;
      const start = i;
      while (i + 1 < columns && finished(i + 1, j)) i += 1;
      rest += rectPath(xs[start], ys[j], xs[i + 1], ys[j + 1]);
    }
  }
  // Vertical runs between two columns, then horizontal runs between two rows.
  for (let i = 1; i < columns; i += 1) {
    for (let j = 0; j < rows; j += 1) {
      if (finished(i - 1, j) === finished(i, j)) continue;
      const start = j;
      while (j + 1 < rows && finished(i - 1, j + 1) !== finished(i, j + 1)) j += 1;
      frontier += `M${xs[i]} ${ys[start]}V${ys[j + 1]}`;
    }
  }
  for (let j = 1; j < rows; j += 1) {
    for (let i = 0; i < columns; i += 1) {
      if (finished(i, j - 1) === finished(i, j)) continue;
      const start = i;
      while (i + 1 < columns && finished(i + 1, j - 1) !== finished(i + 1, j)) i += 1;
      frontier += `M${xs[start]} ${ys[j]}H${xs[i + 1]}`;
    }
  }
  return { grid, rest, frontier };
}
