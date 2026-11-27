import { DownloadDetail, type DownloadPhase } from './downloadProgress';
import type { ModelDownloadStatus } from './types';

export interface DownloadLine {
  title: string;
  percent: number;
  detail: string;
  names: string;
  count: number;
}

type DownloadStatusInput = ModelDownloadStatus & {
  phase?: 'downloading' | 'verifying';
  received_bytes?: number;
};

interface RunEntry {
  modelId: string;
  status: DownloadStatusInput;
  completed: boolean;
  present: boolean;
  totalBytes: number;
  receivedBytes: number;
  detail: DownloadDetail;
}

function formatSpeed(bytesPerSecond: number): string {
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return '';
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s', 'TB/s'];
  const index = Math.min(units.length - 1, Math.max(0, Math.floor(Math.log10(bytesPerSecond) / 3)));
  const value = bytesPerSecond / 1000 ** index;
  const decimals =
    index >= 3 || (index === 2 && value < 10 && !Number.isInteger(Number(value.toFixed(1))))
      ? 1
      : 0;
  return `${value.toFixed(decimals)} ${units[index]}`;
}

function itemDoneBytes(status: DownloadStatusInput, total: number): number {
  if (status.status === 'queued') return 0;
  return Math.min(total, status.downloaded_bytes ?? (total * status.progress) / 100);
}

/** Tracks one active download run so completed models hold their share of the total progress. */
export class DownloadRun {
  private readonly entries = new Map<string, RunEntry>();
  private readonly knownSizes = new Map<string, number>();
  private droppedReceived = 0;
  private runDetail = new DownloadDetail();

  observe(downloads: DownloadStatusInput[], now: number): void {
    if (!downloads.length) {
      this.entries.clear();
      this.knownSizes.clear();
      this.droppedReceived = 0;
      this.runDetail = new DownloadDetail();
      return;
    }

    for (const entry of this.entries.values()) {
      entry.present = false;
    }

    for (const item of downloads) {
      let entry = this.entries.get(item.model_id);
      if (!entry) {
        entry = {
          modelId: item.model_id,
          status: item,
          completed: false,
          present: true,
          totalBytes: 0,
          receivedBytes: 0,
          detail: new DownloadDetail(),
        };
        this.entries.set(item.model_id, entry);
      }
      entry.present = true;
      entry.completed = false;
      entry.status = item;

      if (item.total_bytes && item.total_bytes > 0) {
        entry.totalBytes = item.total_bytes;
        this.knownSizes.set(item.model_id, item.total_bytes);
      } else if (!entry.totalBytes) {
        entry.totalBytes = this.knownSizes.get(item.model_id) ?? 0;
      }

      const phase: DownloadPhase =
        item.status === 'queued'
          ? 'waiting'
          : item.phase === 'verifying'
            ? 'verifying'
            : 'downloading';
      const done = itemDoneBytes(item, entry.totalBytes);
      const received =
        item.status === 'queued' ? 0 : (item.received_bytes ?? item.downloaded_bytes ?? done);
      entry.receivedBytes = received;
      entry.detail.observe({ done, total: entry.totalBytes, received, phase }, now);
    }

    const active = [...this.entries.values()].filter((entry) => entry.present && !entry.completed);
    const hasReceiving = active.some(
      (entry) => entry.status.status === 'downloading' && entry.status.phase !== 'verifying',
    );
    const hasVerifying = active.some(
      (entry) => entry.status.status === 'downloading' && entry.status.phase === 'verifying',
    );
    const runPhase: DownloadPhase = hasReceiving
      ? 'downloading'
      : hasVerifying
        ? 'verifying'
        : 'waiting';

    let runDone = 0;
    let runTotal = 0;
    let runReceived = this.droppedReceived;
    for (const entry of this.entries.values()) {
      if (!entry.present && !entry.completed) continue;
      runReceived += entry.receivedBytes;
      runTotal += entry.totalBytes;
      runDone += entry.completed ? entry.totalBytes : itemDoneBytes(entry.status, entry.totalBytes);
    }

    this.runDetail.observe(
      { done: runDone, total: runTotal, received: runReceived, phase: runPhase },
      now,
    );
  }

  completed(modelId: string): void {
    const entry = this.entries.get(modelId);
    if (!entry) return;
    entry.completed = true;
    entry.present = false;
  }

  dropped(modelId: string): void {
    const entry = this.entries.get(modelId);
    if (!entry) return;
    // Keep received bytes across a cancel/failure so remaining downloads keep their speed sample.
    this.droppedReceived += entry.receivedBytes;
    this.entries.delete(modelId);
  }

  line(
    now: number,
    name: (id: string) => string,
    size: (id: string) => number,
  ): DownloadLine | null {
    const runEntries = [...this.entries.values()].filter(
      (entry) => entry.present || entry.completed,
    );
    if (!runEntries.length || !runEntries.some((entry) => entry.present && !entry.completed)) {
      return null;
    }

    let done = 0;
    let total = 0;
    let unknownSize = false;
    let percentSum = 0;

    for (const entry of runEntries) {
      const itemSize = entry.status.total_bytes || size(entry.modelId) || 0;
      if (itemSize > 0) {
        this.knownSizes.set(entry.modelId, itemSize);
        entry.totalBytes = itemSize;
      }
      if (entry.completed) {
        if (!itemSize) unknownSize = true;
        else {
          total += itemSize;
          done += itemSize;
        }
        percentSum += 100;
      } else if (entry.status.status === 'queued') {
        if (!itemSize) unknownSize = true;
        else total += itemSize;
        percentSum += entry.status.progress;
        entry.detail.setRange(0, itemSize);
      } else if (!itemSize) {
        unknownSize = true;
        percentSum += entry.status.progress;
      } else {
        const itemDone = itemDoneBytes(entry.status, itemSize);
        total += itemSize;
        done += itemDone;
        percentSum += (itemDone / itemSize) * 100;
        entry.detail.setRange(itemDone, itemSize);
      }
    }

    this.runDetail.setRange(unknownSize ? 0 : done, unknownSize ? 0 : total);
    const percent = unknownSize
      ? percentSum / runEntries.length
      : total > 0
        ? (done / total) * 100
        : 0;
    const clampedPercent = Math.max(0, Math.min(100, percent));

    const count = runEntries.length;
    const title =
      count === 1 ? `Downloading ${name(runEntries[0].modelId)}` : `Downloading ${count} models`;
    const detail = this.runDetail.text(now);
    const currentSpeed = this.runDetail.speed(now);
    const speedLine = currentSpeed !== null && currentSpeed > 0 ? formatSpeed(currentSpeed) : '';

    let names = speedLine;
    if (count > 1) {
      const modelLines = runEntries.map((entry) => {
        const label = name(entry.modelId);
        if (entry.completed) return `${label} · 100%`;
        if (entry.status.status === 'queued') return `${label} · waiting`;
        if (entry.status.phase === 'verifying') return `${label} · verifying`;
        const entryPercent =
          entry.totalBytes > 0
            ? Math.min(
                100,
                (itemDoneBytes(entry.status, entry.totalBytes) / entry.totalBytes) * 100,
              )
            : entry.status.progress;
        return `${label} · ${Math.round(entryPercent)}%`;
      });
      names = [...modelLines, ...(speedLine ? [speedLine] : [])].join('\n');
    }

    return {
      title,
      percent: clampedPercent,
      detail,
      names,
      count,
    };
  }

  detail(modelId: string, now: number): string {
    return this.entries.get(modelId)?.detail.text(now) ?? '';
  }
}
