import type { AppSnapshot, JobRecord } from './types';

const positive = (value: number | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value > 0;

/** Estimates use real timings for the same saved workflow/device, never the
 * inspector's currently selected model or an unrelated benchmark score. */
export function queueTiming(snapshot: AppSnapshot) {
  const active = snapshot.jobs.find((job) => job.id === snapshot.runtime.active_job_id);
  if (
    !active ||
    ['cancelling', 'cancelled', 'failed', 'interrupted'].includes(active.status) ||
    snapshot.runtime.status_title === 'Cancelling'
  )
    return null;
  const queued = snapshot.jobs
    .filter((job) => job.status === 'queued')
    .slice()
    .reverse();
  if (!queued.length) return null;
  const rates = new Map<string, number>();
  // Snapshot order is newest first. Use the latest completed comparable job.
  for (const job of snapshot.jobs) {
    if (
      job.status === 'completed' &&
      job.work_key &&
      positive(job.work_units) &&
      positive(job.elapsed_seconds) &&
      !rates.has(job.work_key)
    ) {
      rates.set(job.work_key, job.elapsed_seconds / job.work_units);
    }
  }
  const liveRemaining = snapshot.runtime.estimated_remaining_seconds;
  const fraction = snapshot.runtime.progress / 100;
  if (
    active.work_key &&
    positive(active.work_units) &&
    positive(liveRemaining) &&
    fraction > 0 &&
    fraction < 1
  ) {
    rates.set(active.work_key, liveRemaining / (1 - fraction) / active.work_units);
  }
  const estimate = (job: JobRecord): number | null => {
    const rate = job.work_key ? rates.get(job.work_key) : undefined;
    return positive(rate) && positive(job.work_units) ? rate * job.work_units : null;
  };
  const activeEstimate = estimate(active);
  const currentSeconds = positive(liveRemaining)
    ? liveRemaining
    : activeEstimate === null
      ? null
      : Math.max(0, activeEstimate - snapshot.runtime.elapsed_seconds);
  const durations = queued.map(estimate);
  const unknownItems =
    durations.filter((value) => value === null).length + Number(currentSeconds === null);
  const knownSeconds =
    (currentSeconds ?? 0) + durations.reduce<number>((sum, value) => sum + (value ?? 0), 0);
  return {
    currentSeconds,
    nextName: queued[0].media_name,
    nextSeconds: durations[0],
    queueSeconds: unknownItems ? null : knownSeconds,
    knownSeconds,
    unknownItems,
    itemCount: queued.length + 1,
  };
}
