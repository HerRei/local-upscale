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
    expect(groups.map((group) => group.media.id)).toEqual(['a', 'b']);
    expect(groups[0].original).toEqual({ kind: 'original', media_id: 'a' });
    expect(groups[0].outputs.map(({ result }) => result.job_id)).toEqual(['new', 'old']);
    expect(groups[0].outputs[1]).toEqual({
      pick: { kind: 'output', media_id: 'a', job_id: 'old' },
      result: histories[1].results[2],
    });
  });
});

import { pickKey, toggleSlot, swapSlots, sliderBlock, tabFromKey } from './compareOutputs';

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
  it('supports arrow, Home and End navigation between tabs', () => {
    expect(tabFromKey('ArrowRight', 0, 2)).toBe(1);
    expect(tabFromKey('ArrowLeft', 0, 2)).toBe(1);
    expect(tabFromKey('Home', 1, 2)).toBe(0);
    expect(tabFromKey('End', 0, 2)).toBe(1);
    expect(tabFromKey('Enter', 0, 2)).toBeNull();
  });
});
