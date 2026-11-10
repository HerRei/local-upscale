// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import OutputsList from './OutputsList.svelte';
import { outputGroups } from './lib/compareOutputs';
import type { MediaItem, MediaResults, ResultSummary } from './lib/types';

afterEach(cleanup);

const media = {
  id: 'a',
  name: 'Lake.jpg',
  kind: 'image',
  width: 1600,
  height: 1067,
  preview_data_url: '',
} as MediaItem;

function result(id: string, overrides: Partial<ResultSummary> = {}): ResultSummary {
  return {
    job_id: id,
    completed_at: 1,
    status: 'completed',
    kind: 'upscale',
    output_path: `/out/${id}.png`,
    output_name: `${id}.png`,
    available: true,
    model_id: id,
    model_label: id,
    scale: 4,
    prompt: '',
    max_dimension: 0,
    stages: [],
    notes: '',
    elapsed_seconds: 31,
    ui: { quality: 'quick', content: 'photo', recipe_name: '', fixes: [] },
    ...overrides,
  };
}

const edit = (id: string, seconds: number, prompt: string) =>
  result(id, {
    kind: 'edit',
    model_label: 'FLUX.2 klein 4B',
    scale: 0,
    max_dimension: 512,
    elapsed_seconds: seconds,
    prompt,
    ui: null,
  });

function mount(results: ResultSummary[]) {
  const histories = [{ media_id: 'a', results }] as MediaResults[];
  return render(OutputsList, {
    groups: outputGroups([media], histories),
    picks: [],
    togglePick: vi.fn(),
    clearPicks: vi.fn(),
    openCompare: vi.fn(),
    compareActive: false,
    selectMedia: vi.fn(),
  });
}

it('lists an upscale and two edits of one input as three rows with their own labels', () => {
  mount([
    edit('e2', 61, 'Remove the boat'),
    edit('e1', 48, 'x'.repeat(400)),
    result('span', { model_label: 'SPAN ×4' }),
  ]);
  const list = screen.getByRole('group', { name: 'Outputs' });
  expect(within(list).getByText('3 outputs')).toBeTruthy();
  const rows = [...list.querySelectorAll<HTMLElement>('.media-copy')];
  expect(rows.map((row) => [...row.children].map((part) => part.textContent))).toEqual([
    ['Original', '1600 × 1067'],
    ['FLUX.2 klein 4B', '512 × 320 · 1:01'],
    ['FLUX.2 klein 4B', '512 × 320 · 48s'],
    ['SPAN ×4', 'Quick · 4× · 31s'],
  ]);
  const tips = [...list.querySelectorAll<HTMLElement>('.media-select')].map((row) =>
    row.getAttribute('title'),
  );
  expect(tips[1]).toBe('Remove the boat');
  expect(tips[2]).toHaveLength(121);
  expect(tips[2]?.endsWith('…')).toBe(true);
});
