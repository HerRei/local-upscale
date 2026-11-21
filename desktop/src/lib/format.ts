export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(
    units.length - 1,
    Math.max(0, Math.floor(Math.log(bytes) / Math.log(1024))),
  );
  return `${(bytes / 1024 ** index).toFixed(index >= 3 ? 1 : 0)} ${units[index]}`;
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '—';
  const total = Math.round(seconds);
  if (total >= 86400) return `${Math.floor(total / 86400)}d ${Math.floor((total % 86400) / 3600)}h`;
  if (total >= 3600) return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`;
  return total >= 60
    ? `${Math.floor(total / 60)}:${(total % 60).toString().padStart(2, '0')}`
    : `${total}s`;
}
