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
    // One earlier run may have loaded the model or waited for the Mac to wake: not a speed.
    expect(queueTiming(s)).toMatchObject({ currentSeconds: null, nextSeconds: null });
    s.jobs[0].seconds_per_unit = 5;
    expect(queueTiming(s)).toMatchObject({ currentSeconds: 30, nextSeconds: 60, queueSeconds: 90 });
    s.jobs[0].status = 'cancelled';
    expect(queueTiming(s)).toBeNull();
  });

  it('takes the speed from the typical run, never from the latest one alone', () => {
    const s = demoSnapshot();
    s.jobs = [
      job('next', 'queued', 10),
      job('active', 'running', 10),
      // The newest run waited three minutes for the Mac to wake.
      { ...job('slow', 'completed', 10), elapsed_seconds: 200 },
      { ...job('fast', 'completed', 10), elapsed_seconds: 20 },
    ];
    s.jobs.forEach((j) => (j.seconds_per_unit = 2));
    s.runtime.active_job_id = 'active';
    expect(queueTiming(s)).toMatchObject({ currentSeconds: 20, nextSeconds: 20, queueSeconds: 40 });
  });

  it('measures a staged job by its whole elapsed time, not by its stage-weighted percentage', () => {
    const s = demoSnapshot();
    s.jobs = [job('next', 'queued', 10), job('active', 'running', 10)];
    // A fix that took 10 s is done and the upscale is half through: the bar reads 75 %, the
    // job has run 60 s and the engine says 40 s remain, so it takes 100 s in all.
    Object.assign(s.runtime, {
      active_job_id: 'active',
      progress: 75,
      elapsed_seconds: 60,
      estimated_remaining_seconds: 40,
    });
    expect(queueTiming(s)).toMatchObject({ currentSeconds: 40, nextSeconds: 100 });
  });

  it('keeps an earlier typical speed until the running job has shown enough of its own', () => {
    const s = demoSnapshot();
    s.jobs = [
      { ...job('next', 'queued', 10), seconds_per_unit: 2 },
      { ...job('active', 'running', 10), seconds_per_unit: 2 },
    ];
    Object.assign(s.runtime, {
      active_job_id: 'active',
      progress: 5,
      elapsed_seconds: 45,
      estimated_remaining_seconds: 300,
    });
    expect(queueTiming(s)).toMatchObject({ currentSeconds: 300, nextSeconds: 20 });
    s.runtime.progress = 40;
    expect(queueTiming(s)?.nextSeconds).toBeCloseTo(345);
  });

  it('says nothing, not "≈ —", once the earlier estimate for the running job is used up', () => {
    const s = demoSnapshot();
    s.jobs = [
      { ...job('next', 'queued', 10), seconds_per_unit: 2 },
      { ...job('active', 'running', 10), seconds_per_unit: 2 },
    ];
    Object.assign(s.runtime, { active_job_id: 'active', progress: 99, elapsed_seconds: 25 });
    expect(queueTiming(s)).toMatchObject({ currentSeconds: null, nextSeconds: 20 });
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
