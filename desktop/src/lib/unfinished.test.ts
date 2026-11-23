import { describe, expect, it } from 'vitest';
import {
  keptTilesText,
  keptVideoText,
  tileMarks,
  unfinishedLabel,
  unfinishedMark,
  type TileRect,
} from './unfinished';
import type { ResultSummary } from './types';

const result = (fields: Partial<ResultSummary>) => ({ partial: true, ...fields }) as ResultSummary;

describe('the kept part of a stopped video', () => {
  it('is counted in frames under a second and read as a clock from there', () => {
    expect(keptVideoText({ seconds: 0.074, frames: 2 })).toBe('2 frames');
    expect(keptVideoText({ seconds: 0.033, frames: 1 })).toBe('1 frame');
    expect(keptVideoText({ seconds: 0.999, frames: 24 })).toBe('24 frames');
    expect(keptVideoText({ seconds: 1, frames: 24 })).toBe('0:01');
    expect(keptVideoText({ seconds: 42.9 })).toBe('0:42');
  });
  it('sets it against the whole video when that is known', () => {
    expect(keptVideoText({ seconds: 0.074, frames: 2, fps: 27 }, 2)).toBe('2 of 54 frames');
    expect(keptVideoText({ seconds: 5 }, 12)).toBe('0:05 of 0:12');
    // The whole video is not longer than the kept part: nothing to set it against.
    expect(keptVideoText({ seconds: 5 }, 5)).toBe('0:05');
    expect(keptVideoText({ seconds: 5.2 }, 5.4)).toBe('0:05');
    expect(keptVideoText({ seconds: 0.074, frames: 2 }, 2)).toBe('2 frames');
  });
  it('estimates the frames from the frame rate and says so little when it has neither', () => {
    expect(keptVideoText({ seconds: 0.5, fps: 24 })).toBe('12 frames');
    expect(keptVideoText({ seconds: 0.01, fps: 24 })).toBe('1 frame');
    expect(keptVideoText({ frames: 12, fps: 24 }, 10)).toBe('12 of 240 frames');
    expect(keptVideoText({ frames: 48, fps: 24 }, 10)).toBe('0:02 of 0:10');
    expect(keptVideoText({ frames: 3 })).toBe('3 frames');
    expect(keptVideoText({ seconds: 0.5 })).toBe('under a second');
    expect(keptVideoText({ seconds: 0 })).toBe('');
    expect(keptVideoText({})).toBe('');
  });
});

describe('the Unfinished mark', () => {
  it('names frames for a short video, a clock for a longer one and tiles for a photo', () => {
    expect(
      unfinishedLabel(
        result({ kind: 'video', kept_seconds: 0.074, kept_frames: 2, output_fps: 27 }),
        2,
      ),
    ).toBe('Unfinished · 2 of 54 frames');
    expect(unfinishedLabel(result({ kind: 'video', kept_seconds: 0.074 }), 2, 27)).toBe(
      'Unfinished · 2 of 54 frames',
    );
    expect(
      unfinishedLabel(result({ kind: 'video', kept_seconds: 5, output_duration_seconds: 12 })),
    ).toBe('Unfinished · 0:05 of 0:12');
    expect(
      unfinishedLabel(
        result({
          kind: 'upscale',
          finished_tiles: [
            [0, 0, 8, 8],
            [8, 0, 8, 8],
          ],
          total_tiles: 6,
        }),
      ),
    ).toBe('Unfinished · 2 of 6 tiles');
    expect(unfinishedLabel(result({ kind: 'upscale', finished_tiles: [], total_tiles: 6 }))).toBe(
      'Unfinished · 0 of 6 tiles',
    );
  });
  it('reads the seconds of an older result from its file name and stays plain without them', () => {
    expect(
      unfinishedLabel(result({ kind: 'video', output_name: 'clip_partial_1m05s.mp4' }), 120),
    ).toBe('Unfinished · 1:05 of 2:00');
    expect(unfinishedLabel(result({ kind: 'video', kept_seconds: 0 }))).toBe('Unfinished');
    expect(unfinishedLabel(result({ kind: 'upscale' }))).toBe('Unfinished');
    expect(unfinishedLabel(result({ partial: false, kept_seconds: 5 }))).toBe('');
    expect(keptTilesText(3, 0)).toBe('3 tiles');
    expect(keptTilesText(1, 0)).toBe('1 tile');
    expect(keptTilesText(0, 0)).toBe('');
  });
  it('carries what a view needs: the tiles of a photo, the frames of a video', () => {
    const tiles: TileRect[] = [[0, 0, 8, 8]];
    expect(
      unfinishedMark(result({ kind: 'upscale', finished_tiles: tiles, total_tiles: 4 })),
    ).toEqual({ label: 'Unfinished · 1 of 4 tiles', tiles });
    expect(unfinishedMark(result({ kind: 'video', kept_seconds: 0.074, kept_frames: 2 }))).toEqual({
      label: 'Unfinished · 2 frames',
      frames: 2,
    });
    expect(unfinishedMark(result({ partial: false }))).toBeUndefined();
    expect(unfinishedMark(undefined)).toBeUndefined();
  });
});

describe('the marks over an unfinished photo', () => {
  it('draws the frontier only where finished tiles meet the part that was not reached', () => {
    // A 3 × 2 grid of 10 px tiles; the first row and the first tile of the second are done.
    const tiles: TileRect[] = [
      [0, 0, 10, 10],
      [10, 0, 10, 10],
      [20, 0, 10, 10],
      [0, 10, 10, 10],
    ];
    const marks = tileMarks(tiles, 30, 20)!;
    expect(marks.grid).toBe('M0 0H10V10H0ZM10 0H20V10H10ZM20 0H30V10H20ZM0 10H10V20H0Z');
    // The picture, then the finished cells merged along each row.
    expect(marks.rest).toBe('M0 0H30V20H0ZM0 0H30V10H0ZM0 10H10V20H0Z');
    // One step: down beside the lone tile, then along under the rest of the first row.
    expect(marks.frontier).toBe('M10 10V20M10 10H30');
  });
  it('has no frontier when nothing or everything is finished', () => {
    expect(tileMarks([], 30, 20)).toEqual({ grid: '', rest: 'M0 0H30V20H0Z', frontier: '' });
    expect(tileMarks([[0, 0, 30, 20]], 30, 20)!.frontier).toBe('');
  });
  it('handles tiles that overlap or reach past the picture', () => {
    const marks = tileMarks(
      [
        [0, 0, 12, 10],
        [8, 0, 12, 10],
        [25, 15, 10, 10],
      ],
      30,
      20,
    )!;
    // The overlap is one finished run; the last tile is clipped to the picture.
    expect(marks.rest).toBe('M0 0H30V20H0ZM0 0H20V10H0ZM25 15H30V20H25Z');
    expect(marks.frontier).toBe('M20 0V10M25 15V20M0 10H20M25 15H30');
  });
  it('gives nothing for a picture without a size', () => {
    expect(tileMarks([[0, 0, 10, 10]], 0, 0)).toBeNull();
  });
});
