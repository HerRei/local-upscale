import { describe, expect, it } from 'vitest';
import { followAllowed, formatTook, rowRun } from './rowRun';
import type { JobRecord } from './types';

const job = (id: string, media: string, extra: Partial<JobRecord> = {}): JobRecord => ({
  id,
  media_id: media,
  media_name: `${media}.jpg`,
  media_kind: 'image',
  status: 'queued',
  progress: 0,
  output_path: '',
  error: '',
  created_at: 1000,
  model_label: 'SPAN ×4',
  ...extra,
});

describe('rowRun', () => {
  it('shows the running picture with its own progress and moves the model to the tooltip', () => {
    const jobs = [job('a', 'p1', { status: 'running', progress: 37.6 }), job('b', 'p2')];
    expect(rowRun(jobs, 'p1', 'a')).toEqual({
      kind: 'running',
      progress: 37.6,
      text: 'Running · 38 %',
      title: 'Running SPAN ×4',
    });
  });

  it('gives each of two lanes its own progress', () => {
    const jobs = [
      job('a', 'p1', { status: 'running', progress: 38 }),
      job('b', 'p2', { status: 'running', progress: 71 }),
      job('c', 'p3'),
    ];
    expect(rowRun(jobs, 'p1', 'a').progress).toBe(38);
    expect(rowRun(jobs, 'p2', 'a')).toMatchObject({ progress: 71, text: 'Running · 71 %' });
    expect(rowRun(jobs, 'p3', 'a')).toMatchObject({ kind: 'queued', text: 'Queued' });
  });

  it('has no progress yet while the job starts or reports 0', () => {
    expect(rowRun([job('a', 'p1', { status: 'starting' })], 'p1', 'a')).toMatchObject({
      kind: 'running',
      progress: null,
      text: 'Running',
    });
    expect(rowRun([job('a', 'p1', { status: 'running' })], 'p1', 'a').progress).toBeNull();
  });

  it('clamps progress to 0..100', () => {
    expect(rowRun([job('a', 'p1', { status: 'running', progress: 140 })], 'p1', 'a').progress).toBe(
      100,
    );
  });

  it('says Queued for a waiting picture, with the queue length in the tooltip', () => {
    const jobs = [job('a', 'p1'), job('b', 'p1')];
    expect(rowRun(jobs, 'p1', '')).toEqual({
      kind: 'queued',
      progress: null,
      text: 'Queued',
      title: '2 queued',
    });
  });

  it('shows Done with the elapsed time for a picture finished in this run', () => {
    const jobs = [
      job('a', 'p1', { status: 'completed', progress: 100, elapsed_seconds: 31.2 }),
      job('b', 'p2', { status: 'running', progress: 10, created_at: 1001 }),
    ];
    expect(rowRun(jobs, 'p1', 'b')).toMatchObject({ kind: 'done', text: 'Done · 31 s' });
  });

  it('shows no Done once nothing runs or for an older finished job', () => {
    const done = job('a', 'p1', { status: 'completed', elapsed_seconds: 5 });
    expect(rowRun([done], 'p1', '').kind).toBe('idle');
    const later = job('b', 'p2', { status: 'queued', created_at: 1000 + 3_600_000 });
    expect(rowRun([done, later], 'p1', '').kind).toBe('idle');
  });

  it('leaves a failed picture to its own reason text', () => {
    const jobs = [job('a', 'p1', { status: 'failed' }), job('b', 'p2', { status: 'running' })];
    expect(rowRun(jobs, 'p1', 'b').kind).toBe('idle');
  });

  it('formats the elapsed time', () => {
    expect(formatTook(0.2)).toBe('1 s');
    expect(formatTook(65)).toBe('1 min 5 s');
    expect(formatTook(undefined)).toBe('');
  });
});

describe('followAllowed', () => {
  it('follows only 10 seconds after the last use of the list', () => {
    expect(followAllowed(0, 9_999)).toBe(false);
    expect(followAllowed(0, 10_000)).toBe(true);
    expect(followAllowed(-Infinity, 1)).toBe(true);
  });
});
