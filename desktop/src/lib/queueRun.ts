import type { JobRecord } from './types';

const INFLIGHT = ['queued', 'starting', 'running', 'cancelling'];

/** When the current queue run began: the oldest creation time among the jobs that wait or
 *  run, kept for as long as any do. A run ends when none is left; `previous` is the value
 *  from before the snapshot changed. */
export function queueRunSince(jobs: JobRecord[], previous: number | null): number | null {
  const open = jobs.filter((job) => INFLIGHT.includes(job.status));
  if (!open.length) return null;
  return previous ?? Math.min(...open.map((job) => job.created_at));
}

/** The pictures that have a job in the current run, whatever became of it (waiting, running,
 *  finished, failed or cancelled). Only the others are new input to add. */
export function mediaInRun(jobs: JobRecord[], since: number | null): Set<string> {
  if (since === null) return new Set();
  return new Set(jobs.filter((job) => job.created_at >= since).map((job) => job.media_id));
}
