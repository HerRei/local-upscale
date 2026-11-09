import { describe, expect, it } from 'vitest';
import { outputGroups } from './compareOutputs';
import type { MediaItem, MediaResults, ResultSummary } from './types';

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
