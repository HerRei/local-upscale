// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte';
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
  expect(tips[0]).toBe('Pick for Compare');
  expect(tips[1]).toBe('Pick for Compare\nRemove the boat');
  expect(tips[2]).toHaveLength('Pick for Compare\n'.length + 121);
  expect(tips[2]?.endsWith('…')).toBe(true);
});

const histories = (...ids: string[]) =>
  [{ media_id: 'a', results: ids.map((id) => result(id)) }] as MediaResults[];
const props = (ids: string[]) => ({
  groups: outputGroups([media], histories(...ids)),
  picks: [],
  togglePick: vi.fn(),
  clearPicks: vi.fn(),
  openCompare: vi.fn(),
  compareActive: false,
});

it('opens every group, names the header as a control and tells what a row click does', () => {
  render(OutputsList, props(['one']));
  const header = screen.getByRole('button', { name: /Lake\.jpg/ });
  expect(header.getAttribute('aria-expanded')).toBe('true');
  expect(header.getAttribute('title')).toBe('Hide outputs');
  expect(header.textContent).toContain('1 output');
  expect(screen.getByRole('checkbox', { name: 'one, Lake.jpg' }).getAttribute('title')).toBe(
    'Pick for Compare',
  );
});

it('collapses by the header, keeps that choice while results change and opens for a new result', async () => {
  const view = render(OutputsList, props(['one']));
  const header = () => screen.getByRole('button', { name: /Lake\.jpg/ });
  await fireEvent.click(header());
  expect(header().getAttribute('aria-expanded')).toBe('false');
  expect(header().getAttribute('title')).toBe('Show outputs');
  expect(screen.queryByRole('checkbox', { name: 'one, Lake.jpg' })).toBeNull();
  // The same results again: still collapsed.
  await view.rerender(props(['one']));
  expect(header().getAttribute('aria-expanded')).toBe('false');
  // A result that has just arrived opens its group.
  await view.rerender(props(['two', 'one']));
  expect(header().getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByRole('checkbox', { name: 'two, Lake.jpg' })).toBeTruthy();
});

it('scrolls a new result into view unless the list was used in the last 10 seconds', async () => {
  const scroll = vi.fn();
  Element.prototype.scrollIntoView = scroll;
  const view = render(OutputsList, props(['one']));
  await view.rerender(props(['two', 'one']));
  await Promise.resolve();
  expect(scroll).toHaveBeenCalledWith({ block: 'nearest' });
  scroll.mockClear();
  await fireEvent.pointerDown(screen.getByRole('group', { name: 'Outputs' }));
  await view.rerender(props(['three', 'two', 'one']));
  await Promise.resolve();
  expect(scroll).not.toHaveBeenCalled();
});

it('says in one muted line that results appear as they finish', () => {
  render(OutputsList, { ...props([]), groups: [] });
  expect(screen.getByText('Results appear here as they finish.')).toBeTruthy();
});
