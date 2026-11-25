import type { ModelDownloadStatus } from './types';

export interface DownloadSummary {
  title: string;
  names: string;
  percent: number;
}

/** One status line and one progress value for every model that is downloading or waiting. */
export function downloadSummary(
  downloads: ModelDownloadStatus[],
  lookup: (modelId: string) => { name: string; size: number } | undefined,
): DownloadSummary | null {
  if (!downloads.length) return null;
  let done = 0;
  let total = 0;
  let unknownSize = false;
  for (const item of downloads) {
    const size = item.total_bytes || lookup(item.model_id)?.size || 0;
    if (!size) {
      unknownSize = true;
      continue;
    }
    total += size;
    done += Math.min(size, item.downloaded_bytes ?? (size * item.progress) / 100);
  }
  const percent = unknownSize
    ? downloads.reduce((sum, item) => sum + item.progress, 0) / downloads.length
    : total > 0
      ? (done / total) * 100
      : 0;
  const names = downloads.map((item) => lookup(item.model_id)?.name ?? item.model_id);
  return {
    title:
      downloads.length === 1 ? `Downloading ${names[0]}` : `Downloading ${names.length} models`,
    names: names.join(', '),
    percent: Math.max(0, Math.min(100, percent)),
  };
}
