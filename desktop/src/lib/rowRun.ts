import { queueModelState } from './queue-timing';
import type { JobRecord } from './types';

/** How long after the user last used the list it keeps its scroll position. */
export const FOLLOW_IDLE_MS = 10_000;

/** A finished picture counts as part of the current run when its job was queued within this
 *  many milliseconds of a job that still waits or runs (a batch is queued in one go). */
const SAME_RUN_MS = 600_000;

const INFLIGHT = ['queued', 'starting', 'running', 'cancelling'];

export interface RowRun {
  kind: 'idle' | 'queued' | 'running' | 'done';
  /** The running job's own progress, 0 to 100; null while it is not known yet. */
  progress: number | null;
  /** The short status the row shows; empty when the row has none to add. */
  text: string;
  /** What the status said before it was shortened: model, queue length. */
  title: string;
}

const idle: RowRun = { kind: 'idle', progress: null, text: '', title: '' };

export function formatTook(seconds: number | undefined): string {
  if (!seconds || !Number.isFinite(seconds) || seconds <= 0) return '';
  const total = Math.max(1, Math.round(seconds));
  return total >= 60 ? `${Math.floor(total / 60)} min ${total % 60} s` : `${total} s`;
}

/** The state of one picture's row in the Inputs list, from the snapshot's jobs. */
export function rowRun(jobs: JobRecord[], mediaId: string, activeJobId: string): RowRun {
  const own = jobs.filter((job) => job.media_id === mediaId);
  const inflight = own.filter((job) => INFLIGHT.includes(job.status));
  if (inflight.length) {
    const first = own[0];
    const detail =
      first?.media_kind === 'video'
        ? (inflight.find((job) => job.id === activeJobId) ?? inflight[0]).status
        : queueModelState(jobs, mediaId, activeJobId);
    const title = detail.charAt(0).toUpperCase() + detail.slice(1);
    const running =
      inflight.find((job) => job.id === activeJobId) ??
      inflight.find((job) => job.status !== 'queued');
    if (!running) return { kind: 'queued', progress: null, text: 'Queued', title };
    if (running.status === 'cancelling')
      return { kind: 'running', progress: null, text: 'Cancelling', title };
    const known = running.status === 'running' && running.progress > 0;
    const progress = known ? Math.min(100, Math.max(0, running.progress)) : null;
    return {
      kind: 'running',
      progress,
      text: progress === null ? 'Running' : `Running · ${Math.round(progress)} %`,
      title,
    };
  }
  let latest: JobRecord | undefined;
  for (const job of own) if (!latest || job.created_at > latest.created_at) latest = job;
  if (latest?.status !== 'completed') return idle;
  const batchActive = jobs.some(
    (job) =>
      INFLIGHT.includes(job.status) &&
      Math.abs(job.created_at - (latest as JobRecord).created_at) <= SAME_RUN_MS,
  );
  if (!batchActive) return idle;
  const took = formatTook(latest.elapsed_seconds);
  return { kind: 'done', progress: null, text: took ? `Done · ${took}` : 'Done', title: '' };
}

/** The list follows the work only while the user has left it alone. */
export const followAllowed = (lastUserAt: number, now: number): boolean =>
  now - lastUserAt >= FOLLOW_IDLE_MS;
