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

export const MAX_SLOTS = 4;
export function pickKey(pick: ComparePick): string {
  return JSON.stringify(
    pick.kind === 'original' ? [pick.kind, pick.media_id] : [pick.kind, pick.media_id, pick.job_id],
  );
}
export function toggleSlot(picks: ComparePick[], pick: ComparePick): ComparePick[] {
  const key = pickKey(pick);
  if (picks.some((entry) => pickKey(entry) === key))
    return picks.filter((entry) => pickKey(entry) !== key);
  return picks.length < MAX_SLOTS ? [...picks, pick] : picks;
}
export function swapSlots(picks: ComparePick[], first: number, second: number): ComparePick[] {
  if (first < 0 || second < 0 || first >= picks.length || second >= picks.length) return picks;
  const next = [...picks];
  [next[first], next[second]] = [next[second], next[first]];
  return next;
}
export function sliderBlock(picks: readonly ComparePick[]): string {
  if (picks.length < 2) return 'Pick two to use the slider';
  if (picks[0].media_id !== picks[1].media_id)
    return 'The slider needs two versions of the same image; boxes can hold different images';
  return '';
}
export function boxColumns(count: number, stageWidth: number, stageHeight: number): number {
  if (count <= 2) return Math.max(1, count);
  return stageWidth / Math.max(1, stageHeight) >= 2.6 ? count : 2;
}
export interface CompareItem {
  key: string;
  pick: ComparePick;
  media_name: string;
  label: string;
  meta: string;
  preview_data_url: string;
  preview_width?: number;
  preview_height?: number;
  region?: { bounds: import('./compareRegions').Region; data_url: string } | null;
  width: number;
  height: number;
  status: 'loading' | 'ready' | 'error' | 'missing';
  error: string;
}
