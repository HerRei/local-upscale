import { describe, expect, it } from 'vitest';
import {
  addPoint,
  brushLimits,
  commit,
  emptyHistory,
  MAX_NUMBERS,
  MAX_STROKES,
  paintMarks,
  redo,
  roomFor,
  stepBrush,
  strokePath,
  undo,
  type RepairStroke,
} from './maskPaint';

const big = { width: 6000, height: 4000 };
const small = { width: 800, height: 600 };
const stroke = (mode: RepairStroke['mode'], points: number[] = [10, 10]): RepairStroke => ({
  mode,
  size: 12,
  points,
});

describe('paint', () => {
  it('brushLimits follows the longer side', () => {
    expect(brushLimits(big)).toEqual({ min: 2, max: 300, first: 24 });
    expect(brushLimits(small)).toEqual({ min: 2, max: 40, first: 4 });
    expect(brushLimits({ width: 400, height: 30_000 })).toEqual({ min: 2, max: 400, first: 64 });
  });

  it('stepBrush divides and multiplies by 1.25 and stays inside the limits', () => {
    expect(stepBrush(24, ']', big)).toBe(30);
    expect(stepBrush(30, '[', big)).toBe(24);
    expect(stepBrush(2, '[', big)).toBe(2);
    expect(stepBrush(290, ']', big)).toBe(300);
    let size = 4;
    for (let press = 0; press < 40; press += 1) size = stepBrush(size, ']', small);
    expect(size).toBe(40);
    for (let press = 0; press < 40; press += 1) size = stepBrush(size, '[', small);
    expect(size).toBe(2);
  });

  it('addPoint drops a point closer than an eighth of the brush, rounds and holds it to the picture', () => {
    const start = addPoint({ mode: 'paint', size: 16, points: [] }, 10.04, 20.06, small);
    expect(start.points).toEqual([10, 20.1]);
    expect(addPoint(start, 11.5, 20.1, small)).toBe(start);
    expect(addPoint(start, 12, 20.1, small).points).toEqual([10, 20.1, 12, 20.1]);
    expect(addPoint(start, -40, 900, small).points).toEqual([10, 20.1, 0, 600]);
    expect(addPoint({ mode: 'paint', size: 16, points: [] }, 812.3, -3, small).points).toEqual([
      800, 0,
    ]);
  });

  it('strokePath of one point is a line of no length', () => {
    expect(strokePath(stroke('paint', [20, 20]))).toBe('M20 20 L20 20');
    expect(strokePath(stroke('paint', [1, 2, 3.5, 4, 5, 6]))).toBe('M1 2 L3.5 4 5 6');
    expect(strokePath(stroke('paint', []))).toBe('');
  });

  it('paintMarks counts the paint strokes only', () => {
    expect(paintMarks([])).toBe(0);
    expect(paintMarks([stroke('paint'), stroke('erase'), stroke('paint')])).toBe(2);
  });

  it('roomFor keeps to the host limits', () => {
    expect(roomFor([], stroke('paint'))).toBe(true);
    const full = Array.from({ length: MAX_STROKES }, () => stroke('paint'));
    expect(roomFor(full, stroke('paint'))).toBe(false);
    const long = stroke(
      'paint',
      Array.from({ length: MAX_NUMBERS - 2 }, () => 1),
    );
    expect(roomFor([long], stroke('paint'))).toBe(true);
    expect(roomFor([long], stroke('paint', [1, 1, 2, 2]))).toBe(false);
  });

  it('commit, undo and redo: a new stroke empties redo and Clear is one step', () => {
    const a = stroke('paint', [1, 1]);
    const b = stroke('erase', [2, 2]);
    let history = emptyHistory();
    expect(undo(history)).toBe(history);
    expect(redo(history)).toBe(history);
    history = commit(history, [a]);
    history = commit(history, [a, b]);
    expect(history.strokes).toEqual([a, b]);
    history = undo(history);
    expect(history.strokes).toEqual([a]);
    expect(history.undone).toEqual([[a, b]]);
    history = redo(history);
    expect(history.strokes).toEqual([a, b]);
    history = undo(history);
    history = commit(history, [a, a]);
    expect(history.undone).toEqual([]);
    expect(redo(history)).toBe(history);
    history = commit(history, []);
    expect(history.strokes).toEqual([]);
    history = undo(history);
    expect(history.strokes).toEqual([a, a]);
    history = undo(history);
    history = undo(history);
    history = undo(history);
    expect(history.strokes).toEqual([]);
    expect(history.past).toEqual([]);
  });
});
