export interface SyncDecision {
  seekTo: number | null;
  playbackRate: number;
}

export function commonDuration(first: number, second: number): number {
  const values = [first, second].filter((value) => Number.isFinite(value) && value > 0);
  return values.length ? Math.min(...values) : 0;
}

export function synchronizationDecision(
  primaryTime: number,
  secondaryTime: number,
  paused: boolean,
  basePlaybackRate: number
): SyncDecision {
  const base = Math.max(0.25, Math.min(4, basePlaybackRate));
  const drift = primaryTime - secondaryTime;
  if (!Number.isFinite(drift) || Math.abs(drift) > 0.09) {
    return { seekTo: Number.isFinite(primaryTime) ? Math.max(0, primaryTime) : 0, playbackRate: base };
  }
  if (paused || Math.abs(drift) < 0.025) return { seekTo: null, playbackRate: base };
  // A small one-way rate correction avoids visible seek oscillation. Once
  // drift falls inside the dead band the exact user-selected rate is restored.
  return {
    seekTo: null,
    playbackRate: Math.max(0.25, Math.min(4, base + (drift > 0 ? 0.03 : -0.03)))
  };
}

export function formatMediaTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const rounded = Math.floor(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}`;
}
