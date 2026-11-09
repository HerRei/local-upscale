import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import type { JobRecord } from './types';
import { queueTiming } from './queue-timing';

function job(
  id: string,
  status: JobRecord['status'],
  units: number,
  key = 'nafnet:cpu',
): JobRecord {
  return {
    id,
    media_id: id,
    media_name: `${id}.png`,
    media_kind: 'image',
    status,
    progress: 0,
    output_path: '',
    error: '',
    created_at: 0,
    work_key: key,
    work_units: units,
  };
}

describe('whole queue timing', () => {
  it('shows current, next and whole-queue estimates scaled to each image workload', () => {
    const s = demoSnapshot();
    s.jobs = [job('last', 'queued', 8), job('next', 'queued', 4), job('active', 'running', 4)];
    Object.assign(s.runtime, {
      active_job_id: 'active',
      progress: 50,
      estimated_remaining_seconds: 10,
      elapsed_seconds: 10,
    });
    expect(queueTiming(s)).toMatchObject({
      currentSeconds: 10,
      nextName: 'next.png',
      nextSeconds: 20,
      queueSeconds: 70,
      unknownItems: 0,
      itemCount: 3,
    });
    s.jobs[0].work_key = 'hat:directml:0';
    expect(queueTiming(s)).toMatchObject({
      currentSeconds: 10,
      queueSeconds: null,
      knownSeconds: 30,
      unknownItems: 1,
    });
    s.runtime.status_title = 'Cancelling';
    expect(queueTiming(s)).toBeNull();
  });

  it('leaves times unknown until a comparable job supplies a measurement', () => {
    const s = demoSnapshot();
    s.jobs = [job('next', 'queued', 12), job('active', 'running', 6)];
    s.runtime.active_job_id = 'active';
    expect(queueTiming(s)).toMatchObject({
      currentSeconds: null,
      queueSeconds: null,
      unknownItems: 2,
    });
    s.jobs.push({ ...job('previous', 'completed', 6), elapsed_seconds: 30 });
    expect(queueTiming(s)).toMatchObject({ currentSeconds: 30, nextSeconds: 60, queueSeconds: 90 });
    s.jobs[0].status = 'cancelled';
    expect(queueTiming(s)).toBeNull();
  });

  it('names the model of the next run when the queue runs one image more than once', () => {
    const s = demoSnapshot();
    const trial = (id: string, status: JobRecord['status'], model: string): JobRecord => ({
      ...job(id, status, 4),
      media_id: 'photo',
      media_name: 'photo.png',
      model_label: model,
    });
    // Newest first, as in a snapshot: SwinIR runs, then HAT, then DAT2.
    s.jobs = [
      trial('third', 'queued', 'DAT2'),
      trial('second', 'queued', 'HAT'),
      trial('first', 'running', 'SwinIR'),
    ];
    s.runtime.active_job_id = 'first';
    expect(queueTiming(s)?.nextName).toBe('photo.png · HAT');
    s.jobs = [job('other', 'queued', 4), trial('first', 'running', 'SwinIR')];
    expect(queueTiming(s)?.nextName).toBe('other.png');
  });
});
