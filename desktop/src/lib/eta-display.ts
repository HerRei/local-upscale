import { queueTiming } from './queue-timing';
import type { AppSnapshot } from './types';

/**
 * What the window shows of the engine's remaining-time estimates: one countdown, one way
 * of moving to a new estimate and one way of rounding. The engine sends an estimate every
 * few tiles; this keeps it from being redrawn each time.
 */

/** A new estimate moves the countdown only when it differs by more than this share... */
const DIFFERENCE_SHARE = 0.06;
/** ...or this many seconds, whichever is more. */
const DIFFERENCE_SECONDS = 3;
/** An estimate must keep differing this long before the countdown follows it. */
const CONFIRM_SECONDS = 3;
/** The display then closes the gap over this long. */
const GLIDE_SECONDS = 4;
/** A move the other way within this long needs twice the difference. */
const SETTLE_SECONDS = 10;
/** An estimate is still sharpening in its first seconds: it is followed after confirming this
 * long, and a move the other way is not held back. */
const CONFIRM_YOUNG_SECONDS = 1;
const YOUNG_SECONDS = 8;
/** The countdown never shows less than this while the job still runs. */
const LEAST_SECONDS = 1;

/** The time left, counted down by itself between the engine's estimates. */
export class EtaCountdown {
  private base = 0;
  private baseAt = 0;
  private startedAt = 0;
  private gap = 0;
  private gapAt = 0;
  private active = false;
  private pending: { since: number; direction: number } | null = null;
  private moved: { at: number; direction: number } | null = null;

  clear(): void {
    this.active = false;
    this.pending = null;
    this.moved = null;
  }

  /** The engine's newest estimate, arrived at `now` (seconds on any steady clock). */
  update(raw: number | null | undefined, now: number): void {
    if (typeof raw !== 'number' || !Number.isFinite(raw) || raw <= 0) {
      this.clear();
      return;
    }
    const shown = this.seconds(now);
    if (!this.active || shown === null) {
      this.base = raw;
      this.baseAt = now;
      this.startedAt = now;
      this.gap = 0;
      this.active = true;
      return;
    }
    const difference = raw - shown;
    const direction = Math.sign(difference);
    const allowed = Math.max(shown * DIFFERENCE_SHARE, DIFFERENCE_SECONDS);
    const young = now - this.startedAt < YOUNG_SECONDS;
    const needed =
      !young &&
      this.moved &&
      this.moved.direction !== direction &&
      now - this.moved.at < SETTLE_SECONDS
        ? 2 * allowed
        : allowed;
    if (Math.abs(difference) <= needed) {
      this.pending = null;
      return;
    }
    if (!this.pending || this.pending.direction !== direction) {
      this.pending = { since: now, direction };
      return;
    }
    if (now - this.pending.since < (young ? CONFIRM_YOUNG_SECONDS : CONFIRM_SECONDS)) return;
    // Counting continues from the new estimate; the distance to what was shown fades out.
    this.gap = shown - raw;
    this.gapAt = now;
    this.base = raw;
    this.baseAt = now;
    this.pending = null;
    this.moved = { at: now, direction };
  }

  /** Seconds left at `now`, or null while there is no estimate. */
  seconds(now: number): number | null {
    if (!this.active) return null;
    const fade = Math.max(0, 1 - (now - this.gapAt) / GLIDE_SECONDS);
    return Math.max(LEAST_SECONDS, this.base - (now - this.baseAt) + this.gap * fade);
  }
}

/** The step a time is rounded to: what the estimate can honestly claim. */
export function etaStep(seconds: number): number {
  if (seconds < 60) return 5;
  if (seconds < 600) return 10;
  return 60;
}

/** A time that is on its step, as text. */
function stepText(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 600)
    return `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, '0')}`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

/** How far from the shown value a time may drift before the text changes, in steps. */
const HOLD = 0.7;

/** One displayed time: rounded to its step, and slow to change at the edge of a step. */
export class EtaLabel {
  private shown: number | null = null;

  /** The text for `seconds`, or null when there is nothing to show. */
  text(seconds: number | null | undefined): string | null {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) {
      this.shown = null;
      return null;
    }
    if (this.shown !== null && Math.abs(seconds - this.shown) <= HOLD * etaStep(this.shown))
      return stepText(this.shown);
    const step = etaStep(seconds);
    this.shown = Math.max(step, Math.round(seconds / step) * step);
    return stepText(this.shown);
  }
}

/** The text of one time without any memory of the last one. */
export function formatEta(seconds: number): string | null {
  return new EtaLabel().text(seconds);
}

export interface LiveEta {
  remaining: number | null;
  elapsed: number;
}

/** The running job's countdown and the labels of every time on screen. */
export class EtaDisplay {
  private job = '';
  private raw = Number.NaN;
  private elapsed = 0;
  private elapsedAt = 0;
  private readonly countdown = new EtaCountdown();
  private readonly labels = new Map<string, EtaLabel>();

  /** Take what the snapshot says now; `now` is when it arrived. */
  observe(job: string, raw: number, elapsed: number, now: number): void {
    if (job !== this.job) {
      this.job = job;
      this.raw = Number.NaN;
      this.countdown.clear();
    }
    if (!job) return;
    if (raw !== this.raw) {
      this.raw = raw;
      this.countdown.update(raw, now);
    }
    if (elapsed !== this.elapsed) {
      this.elapsed = elapsed;
      this.elapsedAt = now;
    }
  }

  /** The running job's time left and time spent, both counted on from the last report. */
  live(now: number): LiveEta {
    return {
      remaining: this.countdown.seconds(now),
      elapsed: this.elapsed + Math.max(0, now - this.elapsedAt),
    };
  }

  label(slot: string, seconds: number | null | undefined): string | null {
    let label = this.labels.get(slot);
    if (!label) this.labels.set(slot, (label = new EtaLabel()));
    return label.text(seconds);
  }
}

/** A progress line with the engine's raw "· ETA …" at its end replaced by the shown time. */
export function withEta(detail: string, shown: string | null): string {
  return detail.replace(
    / · ETA(?: ≈)? (?:\d+d \d+h|\d+h \d+m|\d+:\d{2}|\d+s)$/,
    shown ? ` · ETA ≈ ${shown}` : '',
  );
}

/** Every time the window shows for the running job and the queue, as text. */
export function etaView(snapshot: AppSnapshot, display: EtaDisplay, now: number) {
  const runtime = snapshot.runtime;
  display.observe(
    runtime.active_job_id,
    runtime.estimated_remaining_seconds,
    runtime.elapsed_seconds,
    now,
  );
  const live = display.live(now);
  const timing = queueTiming(snapshot, live);
  const job = display.label('job', live.remaining);
  return {
    job,
    timing,
    current: live.remaining !== null ? job : display.label('typical', timing?.currentSeconds),
    queue: display.label('queue', timing?.queueSeconds),
    next: display.label('next', timing?.nextSeconds),
  };
}
