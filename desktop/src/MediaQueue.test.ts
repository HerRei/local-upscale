// @vitest-environment jsdom
import { cleanup, fireEvent, render } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MediaQueue from './MediaQueue.svelte';
import type { JobRecord, MediaItem } from './lib/types';

const media = (n: number): MediaItem => ({
  id: `p${n}`,
  path: `/p${n}.jpg`,
  name: `p${n}.jpg`,
  kind: 'image',
  width: 1000,
  height: 800,
  frame_count: 1,
  fps: 0,
  duration_seconds: 0,
  preview_data_url: '',
  probe_status: 'ready',
  error: '',
  selected: n === 1,
});
const job = (n: number, extra: Partial<JobRecord> = {}): JobRecord => ({
  id: `j${n}`,
  media_id: `p${n}`,
  media_name: `p${n}.jpg`,
  media_kind: 'image',
  status: 'queued',
  progress: 0,
  output_path: '',
  error: '',
  created_at: 1000 + n,
  model_label: 'SPAN ×4',
  ...extra,
});

function props(jobs: JobRecord[], count = 4) {
  return {
    groups: [],
    picks: [],
    togglePick: () => undefined,
    clearPicks: () => undefined,
    openCompare: () => undefined,
    compareActive: false,
    items: Array.from({ length: count }, (_, i) => media(i + 1)),
    jobs,
    activeJobId: jobs.find((j) => j.status === 'running')?.id ?? '',
    scopeWarning: false,
    scopeHint: '',
    benchmarkRunning: false,
    inflightMediaIds: new Set(
      jobs
        .filter((j) => ['queued', 'running', 'starting'].includes(j.status))
        .map((j) => j.media_id),
    ),
    addFiles: async () => undefined,
    selectQueueMedia: async () => undefined,
    addFolder: async () => undefined,
    removeMedia: async () => undefined,
    clearMedia: () => undefined,
  };
}

const row = (container: HTMLElement, n: number) =>
  container.querySelectorAll<HTMLElement>('.media-row')[n - 1];

let scrolled: HTMLElement[] = [];
beforeEach(() => {
  scrolled = [];
  vi.useFakeTimers();
  vi.setSystemTime(100_000);
  Element.prototype.scrollIntoView = function (this: HTMLElement) {
    scrolled.push(this);
  };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('Inputs list while a batch runs', () => {
  it('draws a progress line for the running row only, with its value and file name', () => {
    const jobs = [
      job(1, { status: 'completed', progress: 100, elapsed_seconds: 31 }),
      job(2, { status: 'running', progress: 38 }),
      job(3),
      job(4, { status: 'failed', error: 'boom' }),
    ];
    const { container } = render(MediaQueue, props(jobs));
    const bars = container.querySelectorAll('[role="progressbar"]');
    expect(bars).toHaveLength(1);
    const bar = bars[0];
    expect(row(container, 2).contains(bar)).toBe(true);
    expect(bar.getAttribute('aria-valuenow')).toBe('38');
    expect(bar.getAttribute('aria-valuemin')).toBe('0');
    expect(bar.getAttribute('aria-valuemax')).toBe('100');
    expect(bar.getAttribute('aria-label')).toBe('Progress of p2.jpg');
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('38%');
    expect(row(container, 2).classList.contains('running')).toBe(true);
    expect(row(container, 3).classList.contains('running')).toBe(false);
    const text = (n: number) => row(container, n).querySelector('.queue-state');
    expect(text(2)?.textContent).toBe('Running · 38 %');
    expect(text(2)?.getAttribute('title')).toBe('Running SPAN ×4');
    expect(text(3)?.textContent).toBe('Queued');
    expect(text(1)?.textContent).toBe('Done · 31 s');
    expect(text(4)?.textContent).toBe('Could not finish');
    expect(text(4)?.getAttribute('title')).toBe('boom');
  });

  it('marks both rows of two lanes, each with its own value', () => {
    const jobs = [
      job(1, { status: 'running', progress: 38 }),
      job(2, { status: 'running', progress: 71 }),
    ];
    const { container } = render(MediaQueue, props(jobs, 2));
    const values = [...container.querySelectorAll('[role="progressbar"]')].map((bar) =>
      bar.getAttribute('aria-valuenow'),
    );
    expect(values).toEqual(['38', '71']);
  });

  it('shows an empty track without a value while the progress is not known', () => {
    const { container } = render(MediaQueue, props([job(1, { status: 'starting' })], 1));
    const bar = container.querySelector('[role="progressbar"]')!;
    expect(bar.hasAttribute('aria-valuenow')).toBe(false);
    expect(bar.querySelector('i')).toBeNull();
  });

  it('scrolls the list to a picture that starts running when the list was left alone', async () => {
    const view = render(MediaQueue, props([job(1, { status: 'running', progress: 5 }), job(2)]));
    await vi.advanceTimersByTimeAsync(0);
    scrolled = [];
    await view.rerender(
      props([job(1, { status: 'completed' }), job(2, { status: 'running', progress: 1 })]),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(scrolled).toEqual([row(view.container, 2)]);
  });

  it('does not scroll within 10 seconds of the user using the list, then follows again', async () => {
    const view = render(MediaQueue, props([job(1, { status: 'running', progress: 5 }), job(2)]));
    await vi.advanceTimersByTimeAsync(0);
    scrolled = [];
    await fireEvent.wheel(view.container.querySelector('.media-list')!);
    vi.setSystemTime(100_000 + 9_000);
    await view.rerender(
      props([job(1, { status: 'completed' }), job(2, { status: 'running', progress: 1 })]),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(scrolled).toEqual([]);
    vi.setSystemTime(100_000 + 11_000);
    await view.rerender(
      props([
        job(1, { status: 'completed' }),
        job(2, { status: 'completed' }),
        job(3, { status: 'running', progress: 1 }),
      ]),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(scrolled).toEqual([row(view.container, 3)]);
  });

  it('never moves the focus or the selection', async () => {
    const view = render(MediaQueue, props([job(1, { status: 'running', progress: 5 }), job(2)]));
    const focused = view.container.querySelector<HTMLElement>('.media-select')!;
    focused.focus();
    await view.rerender(
      props([job(1, { status: 'completed' }), job(2, { status: 'running', progress: 1 })]),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(document.activeElement).toBe(focused);
    expect(row(view.container, 1).classList.contains('selected')).toBe(true);
  });
});

describe('row styles', () => {
  it('stops the shine under reduced motion', async () => {
    const { readFileSync } = await import('node:fs');
    const css = readFileSync('src/styles.css', 'utf8');
    expect(css).toMatch(
      /\.row-progress i::after \{[^}]*animation: progress-shine 1\.6s linear infinite/,
    );
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\) \{[^}]*\.row-progress i::after \{\s*animation: none/,
    );
  });
});
