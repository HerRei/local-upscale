// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import ResultChooser from './ResultChooser.svelte';
import type { ResultSummary } from './lib/types';

function result(id: string, overrides: Partial<ResultSummary> = {}): ResultSummary {
  return {
    job_id: id,
    completed_at: 100,
    status: 'completed',
    kind: 'upscale',
    output_path: `/${id}.png`,
    output_name: `${id}.png`,
    available: true,
    model_id: id,
    model_label: id,
    scale: 4,
    stages: [],
    notes: '',
    elapsed_seconds: 14,
    ui: null,
    ...overrides,
  };
}

afterEach(cleanup);

it('chooses with the keyboard, skips nothing and refuses rows without a file', async () => {
  const choose = vi.fn();
  render(ResultChooser, {
    props: {
      label: 'Showing',
      filename: 'photo.png',
      target: 'new',
      results: [
        result('new', { ui: { quality: 'best', content: 'photo', recipe_name: '' } }),
        result('missing', { available: false }),
        result('old', { notes: 'Blur fix skipped' }),
      ],
      completedCount: 3,
      choose,
    },
  });
  await fireEvent.click(screen.getByRole('button', { name: /^Showing: new · Best · Photo/ }));
  const list = screen.getByRole('listbox');
  expect(list.getAttribute('aria-activedescendant')).toContain('new');
  expect(within(list).getByText('Blur fix skipped')).toBeTruthy();
  expect(within(list).getByText('File missing')).toBeTruthy();
  // Arrow keys move over every row; Enter on a missing file does nothing.
  await fireEvent.keyDown(list, { key: 'ArrowDown' });
  expect(list.getAttribute('aria-activedescendant')).toContain('missing');
  await fireEvent.keyDown(list, { key: 'Enter' });
  expect(choose).not.toHaveBeenCalled();
  await fireEvent.keyDown(list, { key: 'ArrowDown' });
  await fireEvent.keyDown(list, { key: 'Enter' });
  expect(choose).toHaveBeenCalledWith('old');
  expect(screen.queryByRole('listbox')).toBeNull();
});

it('keeps its keys away from the window, so M does not peek while the list is open', async () => {
  const peek = vi.fn();
  window.addEventListener('keydown', peek);
  try {
    render(ResultChooser, {
      props: {
        label: 'against',
        filename: 'photo.png',
        target: 'original',
        results: [result('a')],
        completedCount: 1,
        choose: vi.fn(),
      },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'against: Original' }));
    await fireEvent.keyDown(screen.getByRole('listbox'), { key: 'm' });
    await fireEvent.keyDown(screen.getByRole('listbox'), { key: 'ArrowRight' });
    expect(peek).not.toHaveBeenCalled();
    await fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
  } finally {
    window.removeEventListener('keydown', peek);
  }
});

it('opens and reveals the highlighted row, an Edit result included, outside the listbox', async () => {
  const openResult = vi.fn();
  const revealResult = vi.fn();
  render(ResultChooser, {
    props: {
      label: 'against',
      filename: 'photo.png',
      target: 'original',
      results: [result('up'), result('edit', { kind: 'edit', model_label: 'Qwen', scale: 0 })],
      completedCount: 2,
      choose: vi.fn(),
      openResult,
      revealResult,
    },
  });
  await fireEvent.click(screen.getByRole('button', { name: 'against: Original' }));
  const list = screen.getByRole('listbox');
  expect(within(list).getByText('Edit · Qwen')).toBeTruthy();
  await fireEvent.keyDown(list, { key: 'ArrowUp' });
  expect(list.getAttribute('aria-activedescendant')).toContain('edit');
  const actions = screen.getByRole('button', { name: 'Open' }).parentElement!;
  expect(list.contains(actions)).toBe(false);
  await fireEvent.click(screen.getByRole('button', { name: 'Open' }));
  expect(openResult).toHaveBeenCalledWith('/edit.png');
  await fireEvent.click(screen.getByRole('button', { name: 'Reveal' }));
  expect(revealResult).toHaveBeenCalledWith('/edit.png');
});

it('lists runs that did not finish apart, and points to older results in their folder', async () => {
  const revealResult = vi.fn();
  render(ResultChooser, {
    props: {
      label: 'Showing',
      filename: 'photo.png',
      target: 'new',
      results: [
        result('new'),
        result('broken', { status: 'failed', available: false, notes: 'Out of memory' }),
        result('oldest-listed', { output_path: '/elsewhere/oldest.png' }),
      ],
      completedCount: 60,
      choose: vi.fn(),
      revealResult,
    },
  });
  await fireEvent.click(screen.getByRole('button', { name: /^Showing: new/ }));
  const list = screen.getByRole('listbox');
  expect(within(list).queryByText(/Out of memory/)).toBeNull();
  expect(screen.getByRole('group', { name: 'Runs that did not finish' }).textContent).toContain(
    'Failed · broken',
  );
  expect(screen.getByText(/Older results are in the output folder/)).toBeTruthy();
  await fireEvent.click(screen.getAllByRole('button', { name: 'Reveal' }).at(-1)!);
  expect(revealResult).toHaveBeenCalledWith('/elsewhere/oldest.png');
});

it('says Live preview while the image runs and nothing was chosen', () => {
  render(ResultChooser, {
    props: {
      label: 'Showing',
      filename: 'photo.png',
      target: 'new',
      liveLabel: 'Live preview',
      results: [result('new')],
      completedCount: 1,
      choose: vi.fn(),
    },
  });
  expect(screen.getByRole('button', { name: 'Showing: Live preview' })).toBeTruthy();
});
