import { formatBytes } from './format';

export type DownloadPhase = 'waiting' | 'downloading' | 'verifying';

const WINDOW_SECONDS = 10;
const MIN_SAMPLE_GAP_SECONDS = 0.5;
const MIN_SPEED_SPAN_SECONDS = 3;
const SILENCE_SECONDS = 8;
const VERIFY_DELAY_SECONDS = 1;
const HOLD = 0.7;

export function progressBytes(done: number, total: number): string {
  if (!Number.isFinite(total) || total <= 0) {
    return Number.isFinite(done) && done > 0 ? formatBytes(done) : '';
  }
  if (!Number.isFinite(done) || done <= 0 || done >= total) {
    return formatBytes(total);
  }
  const doneText = formatBytes(done);
  const totalText = formatBytes(total);
  const doneSpace = doneText.indexOf(' ');
  const totalSpace = totalText.indexOf(' ');
  if (
    doneSpace > 0 &&
    totalSpace > 0 &&
    doneText.slice(doneSpace + 1) === totalText.slice(totalSpace + 1)
  ) {
    return `${doneText.slice(0, doneSpace)} of ${totalText}`;
  }
  return `${doneText} of ${totalText}`;
}

function timeStep(seconds: number): number {
  if (seconds < 45) return 0;
  if (seconds < 59.5 * 60) return 60;
  if (seconds < 3 * 3600) return 300;
  return 3600;
}

export function timeLeftText(seconds: number): string {
  if (seconds < 45) return 'less than a minute left';
  if (seconds < 59.5 * 60) {
    const minutes = Math.max(1, Math.round(seconds / 60));
    return `about ${minutes} min left`;
  }
  if (seconds < 3 * 3600) {
    const totalMinutes = Math.round(seconds / 300) * 5;
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return minutes ? `about ${hours} h ${minutes} min left` : `about ${hours} h left`;
  }
  const hours = Math.round(seconds / 3600);
  return `about ${hours} h left`;
}

export class TimeLeftLabel {
  private shown: number | null = null;

  text(seconds: number | null | undefined): string | null {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) {
      this.shown = null;
      return null;
    }
    if (this.shown !== null) {
      const heldStep = timeStep(this.shown);
      if (heldStep > 0 && Math.abs(seconds - this.shown) <= HOLD * heldStep) {
        return timeLeftText(this.shown);
      }
    }
    const step = timeStep(seconds);
    this.shown = step === 0 ? 0 : Math.max(step, Math.round(seconds / step) * step);
    return timeLeftText(this.shown);
  }
}

export class DownloadDetail {
  private samples: { received: number; at: number }[] = [];
  private phase: DownloadPhase | null = null;
  private done = 0;
  private total = 0;
  private verifyingSince: number | null = null;
  private heldTime: string | null = null;
  private readonly label = new TimeLeftLabel();

  observe(
    sample: { done: number; total: number; received: number; phase: DownloadPhase },
    now: number,
  ): void {
    const lastSample = this.samples[this.samples.length - 1];
    const bytesDropped =
      (lastSample && sample.received < lastSample.received) || sample.done < this.done;
    if (bytesDropped) {
      this.samples = [];
      this.label.text(null);
      this.heldTime = null;
    }

    if (sample.phase !== 'downloading') {
      // Preserve the displayed time across brief hashes between files of a bundle.
      if (this.phase === 'downloading' && sample.phase === 'verifying') {
        const currentSpeed = this.speed(now);
        if (currentSpeed !== null && currentSpeed > 0 && sample.total > sample.done) {
          this.heldTime = this.label.text((sample.total - sample.done) / currentSpeed);
        }
      }
      this.samples = [];
    }

    if (sample.phase === 'verifying') {
      if (this.verifyingSince === null) this.verifyingSince = now;
    } else {
      this.verifyingSince = null;
    }

    this.phase = sample.phase;
    this.done = sample.done;
    this.total = sample.total;

    if (sample.phase === 'downloading') {
      const currentLast = this.samples[this.samples.length - 1];
      if (!currentLast) {
        this.samples.push({ received: sample.received, at: now });
      } else if (sample.received > currentLast.received) {
        if (now - currentLast.at < MIN_SAMPLE_GAP_SECONDS) {
          this.samples[this.samples.length - 1] = { received: sample.received, at: now };
        } else {
          this.samples.push({ received: sample.received, at: now });
        }
      }
      this.samples = this.samples.filter((entry) => now - entry.at <= WINDOW_SECONDS);
    }
  }

  /** Adjust displayed totals when catalog sizes arrive later than `observe`. */
  setRange(done: number, total: number): void {
    this.done = done;
    this.total = total;
  }

  speed(now: number): number | null {
    if (this.phase !== 'downloading' || this.samples.length < 2) return null;
    const first = this.samples[0];
    const last = this.samples[this.samples.length - 1];
    if (now - last.at >= SILENCE_SECONDS) return null;
    const span = last.at - first.at;
    if (span < MIN_SPEED_SPAN_SECONDS) return null;
    const delta = last.received - first.received;
    return delta > 0 ? delta / span : null;
  }

  text(now: number): string {
    if (this.phase === null) return '';
    if (this.phase === 'waiting') {
      return this.total > 0 ? formatBytes(this.total) : '';
    }
    const size = progressBytes(this.done, this.total);
    if (this.phase === 'verifying') {
      if (this.verifyingSince !== null && now - this.verifyingSince >= VERIFY_DELAY_SECONDS) {
        if (this.total > 0 && this.done >= this.total) return 'Verifying…';
        return size ? `${size} · Verifying…` : 'Verifying…';
      }
      const time = this.done < this.total ? this.heldTime : null;
      return [size, time].filter(Boolean).join(' · ');
    }
    const last = this.samples[this.samples.length - 1];
    if (last && now - last.at >= SILENCE_SECONDS) {
      this.label.text(null);
      this.heldTime = null;
      return [size, 'Waiting for the connection…'].filter(Boolean).join(' · ');
    }
    const currentSpeed = this.speed(now);
    const remaining =
      currentSpeed !== null && currentSpeed > 0 && this.total > this.done
        ? (this.total - this.done) / currentSpeed
        : null;
    const time = this.label.text(remaining);
    this.heldTime = time;
    return [size, time].filter(Boolean).join(' · ');
  }
}

export interface DownloadFailure {
  modelId: string;
  name: string;
  reason: 'offline' | 'server' | 'damaged' | 'disk_full' | 'folder' | 'other';
  done: number;
  total: number;
  message: string;
}

function sentenceCase(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return 'The download could not be completed.';
  const capitalized = trimmed[0].toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capitalized) ? capitalized : `${capitalized}.`;
}

export function downloadFailureText(
  failure: DownloadFailure,
  withName: boolean,
): { title: string; detail: string } {
  if (failure.reason === 'offline') {
    if (failure.done > 0) {
      const size = progressBytes(failure.done, failure.total);
      return {
        title: 'Download interrupted',
        detail: withName
          ? `No connection to the model server. ${failure.name} is kept at ${size} and continues from there.`
          : `No connection to the model server. Kept at ${size}.`,
      };
    }
    return {
      title: 'Download interrupted',
      detail: 'No connection to the model server. Check the internet connection and start again.',
    };
  }
  if (failure.reason === 'server') {
    return {
      title: 'Download failed',
      detail: withName
        ? `The model server did not deliver ${failure.name}. Try again later.`
        : 'The model server did not deliver this model. Try again later.',
    };
  }
  if (failure.reason === 'damaged') {
    return {
      title: 'Download failed',
      detail: withName
        ? `${failure.name} arrived damaged and was removed. Start again to download it once more.`
        : 'Arrived damaged and was removed. Start again to download it once more.',
    };
  }
  return {
    title: 'Download failed',
    detail: sentenceCase(failure.message),
  };
}
