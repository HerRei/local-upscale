import type { AppSnapshot, JobRecord } from './types';

const positive = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value > 0;

/** Estimates use real timings for the same saved workflow/device, never the
 * inspector's currently selected model or an unrelated benchmark score. The engine owns
 * the running job's remaining time; this only scales a typical speed to the queued jobs. */
export function queueTiming(
  snapshot: AppSnapshot,
  live?: { remaining: number | null; elapsed: number },
) {
  const active = snapshot.jobs.find((job) => job.id === snapshot.runtime.active_job_id);
  if (
    !active ||
    ['cancelling', 'cancelled', 'failed', 'interrupted'].includes(active.status) ||
    snapshot.runtime.status_title === 'Cancelling'
  )
    return null;
  const queued = snapshot.jobs.filter((job) => job.status === 'queued').reverse();
  if (!queued.length) return null;
  const rates = new Map<string, number>();
  // The host works out each workload's typical speed from its recent runs (never the latest
  // alone, never a different model, device or settings).
  for (const job of snapshot.jobs) {
    if (job.work_key && positive(job.seconds_per_unit) && !rates.has(job.work_key))
      rates.set(job.work_key, job.seconds_per_unit);
  }
  // The window passes the counted-down values, which move between the engine's reports.
  const liveRemaining = live ? live.remaining : snapshot.runtime.estimated_remaining_seconds;
  const elapsed = Math.max(0, live ? live.elapsed : snapshot.runtime.elapsed_seconds);
  if (active.work_key && positive(active.work_units) && positive(liveRemaining)) {
    // The engine's estimate covers the whole job, so what has run plus what remains is its
    // length, whatever the stages weigh. A quarter of the job is the least that tells more
    // about the speed now than earlier runs do.
    if (!rates.has(active.work_key) || snapshot.runtime.progress >= 25)
      rates.set(active.work_key, (elapsed + liveRemaining) / active.work_units);
  }
  const estimate = (job: JobRecord): number | null => {
    const rate = job.work_key ? rates.get(job.work_key) : undefined;
    return positive(rate) && positive(job.work_units) ? rate * job.work_units : null;
  };
  const activeEstimate = estimate(active);
  // An estimate that the running job has already used up is no estimate.
  const left = activeEstimate === null ? 0 : activeEstimate - elapsed;
  const currentSeconds = positive(liveRemaining) ? liveRemaining : left > 0 ? left : null;
  const durations = queued.map(estimate);
  const unknownItems =
    durations.filter((value) => value === null).length + Number(currentSeconds === null);
  const knownSeconds =
    (currentSeconds ?? 0) + durations.reduce<number>((sum, value) => sum + (value ?? 0), 0);
  // Several runs of one image share a file name; then the model tells them apart.
  const next = queued[0];
  const sameImage = [active, ...queued].filter((job) => job.media_id === next.media_id).length;
  return {
    currentSeconds,
    nextName:
      sameImage > 1 && next.model_label
        ? `${next.media_name} · ${next.model_label}`
        : next.media_name,
    nextSeconds: durations[0],
    queueSeconds: unknownItems ? null : knownSeconds,
    knownSeconds,
    unknownItems,
    itemCount: queued.length + 1,
  };
}

/** "Running SwinIR · 2 queued": the active job first, then this image's queue. */
export function queueModelState(jobs: JobRecord[], mediaId: string, activeJobId: string): string {
  const inflight = jobs.filter(
    (job) =>
      job.media_id === mediaId &&
      ['queued', 'starting', 'running', 'cancelling'].includes(job.status),
  );
  const active =
    inflight.find((job) => job.id === activeJobId) ??
    inflight.find((job) => job.status !== 'queued');
  const queued = inflight.filter((job) => job.status === 'queued').length;
  if (active)
    return `${active.status === 'cancelling' ? 'Cancelling' : 'Running'} ${active.model_label || 'model'}${queued ? ` · ${queued} queued` : ''}`;
  return queued > 1 ? `${queued} queued` : 'queued';
}
