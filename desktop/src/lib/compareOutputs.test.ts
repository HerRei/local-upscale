import { describe, expect, it } from 'vitest';
import { outputGroups } from './compareOutputs';
import type { ComparePick, MediaItem, MediaResults, ResultSummary } from './types';

describe('Outputs list histories', () => {
  it('groups all completed results by image in input order and keeps unavailable results visible', () => {
    const media = [
      { id: 'a', name: 'A', kind: 'image' },
      { id: 'v', name: 'Video', kind: 'video' },
      { id: 'b', name: 'B', kind: 'image' },
      { id: 'empty', kind: 'image' },
    ] as MediaItem[];
    const result = (job_id: string, status: string, available: boolean): ResultSummary =>
      ({ job_id, status, available, model_label: job_id }) as ResultSummary;
    const histories = [
      { media_id: 'b', results: [result('b1', 'completed', true)] },
      { media_id: 'v', results: [result('v1', 'completed', true)] },
      {
        media_id: 'a',
        results: [
          result('new', 'completed', true),
          result('failed', 'failed', false),
          result('old', 'completed', false),
        ],
      },
    ] as MediaResults[];
    const groups = outputGroups(media, histories);
    expect(groups.map((group) => group.media.id)).toEqual(['a', 'v', 'b']);
    expect(groups[0].original).toEqual({ kind: 'original', media_id: 'a' });
    expect(groups[0].outputs.map(({ result }) => result.job_id)).toEqual(['new', 'old']);
    expect(groups[0].outputs[1]).toEqual({
      pick: { kind: 'output', media_id: 'a', job_id: 'old' },
      result: histories[2].results[2],
    });
  });
});

import { pickKey, toggleSlot, swapSlots, sliderBlock } from './compareOutputs';

describe('Compare controls', () => {
  const picks = [
    { kind: 'original', media_id: 'a' },
    { kind: 'output', media_id: 'a', job_id: 'one' },
    { kind: 'output', media_id: 'b', job_id: 'two' },
    { kind: 'original', media_id: 'b' },
    { kind: 'output', media_id: 'b', job_id: 'three' },
  ] as const;
  it('assigns slots in tick order, compacts removals and refuses a fifth', () => {
    let selected: ComparePick[] = picks.slice(0, 4);
    expect(toggleSlot(selected, picks[4])).toEqual(selected);
    selected = toggleSlot(selected, picks[1]);
    expect(selected.map(pickKey)).toEqual([
      pickKey(picks[0]),
      pickKey(picks[2]),
      pickKey(picks[3]),
    ]);
    expect(toggleSlot(selected, picks[4]).at(-1)).toEqual(picks[4]);
  });
  it('swaps occupied slots and requires matching input for the slider', () => {
    expect(swapSlots([...picks.slice(0, 3)], 1, 2)).toEqual([picks[0], picks[2], picks[1]]);
    expect(sliderBlock(picks.slice(0, 2))).toBe('');
    expect(sliderBlock([picks[0], picks[2]])).toContain('same image');
    expect(sliderBlock([picks[0]])).toContain('two');
  });
});

import { visibleRegion } from './compareRegions';

describe('visible compare regions', () => {
  it('requests only when the displayed preview is magnified, in source coordinates', () => {
    const input = {
      image: { width: 4000, height: 2000 },
      preview: { width: 1000, height: 500 },
      cell: { width: 500, height: 500 },
      fit: { width: 500, height: 250 },
      zoom: 4,
      pan: { x: 250, y: 0 },
    };
    expect(visibleRegion({ ...input, zoom: 1 })).toBeNull();
    expect(visibleRegion({ ...input, zoom: 1.5, pixelRatio: 2 })).not.toBeNull();
    expect(visibleRegion(input)).toEqual({
      x: 1000,
      y: 500,
      width: 1000,
      height: 1000,
      target_width: 500,
      target_height: 500,
    });
  });
});

import { pickKindBlock } from './compareOutputs';
it('keeps video and image slots homogeneous, resets after clearing and names video slider restrictions', () => {
  const media = [
    { id: 'v', kind: 'video' },
    { id: 'a', kind: 'image' },
  ];
  const v: ComparePick = { kind: 'original', media_id: 'v' };
  const a: ComparePick = { kind: 'original', media_id: 'a' };
  expect(pickKindBlock([v], a, media)).toBe('Compare videos with videos');
  expect(pickKindBlock([a], v, media)).toBe('Compare images with images');
  expect(toggleSlot([v], a, media)).toEqual([v]);
  expect(toggleSlot([], a, media)).toEqual([a]);
  expect(sliderBlock([v, a], 'video')).toContain('same video');
});
