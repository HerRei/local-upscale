export function clampComparison(value: number): number {
  return Math.max(0, Math.min(100, Number.isFinite(value) ? value : 50));
}

export function comparisonFromPointer(clientX: number, left: number, width: number): number {
  return clampComparison(((clientX - left) / Math.max(1, width)) * 100);
}

export function comparisonFromKey(current: number, key: string): number | null {
  const steps: Record<string, number> = {
    ArrowLeft: -1,
    ArrowDown: -1,
    ArrowRight: 1,
    ArrowUp: 1,
    PageDown: -10,
    PageUp: 10,
  };
  if (key === 'Home') return 0;
  if (key === 'End') return 100;
  if (!(key in steps)) return null;
  return clampComparison(current + steps[key]);
}
