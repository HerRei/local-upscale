import type { ComparePick, MediaItem, MediaResults, ResultSummary } from './types';

export interface OutputGroup {
  media: MediaItem;
  original: ComparePick;
  outputs: { pick: ComparePick; result: ResultSummary }[];
}

/** Join the unbounded bulk histories to the current media list for the Outputs tab. */
export function outputGroups(media: MediaItem[], histories: MediaResults[]): OutputGroup[] {
  const byMedia = new Map(histories.map((history) => [history.media_id, history]));
  return media
    .filter((item) => item.kind === 'image')
    .map((item): OutputGroup => ({
      media: item,
      original: { kind: 'original', media_id: item.id },
      outputs: (byMedia.get(item.id)?.results ?? [])
        .filter((result) => result.status === 'completed')
        .map((result) => ({
          pick: { kind: 'output', media_id: item.id, job_id: result.job_id },
          result,
        })),
    }))
    .filter((group) => group.outputs.length > 0);
}
