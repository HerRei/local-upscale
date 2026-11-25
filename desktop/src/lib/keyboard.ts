export function tabFromKey(
  key: string,
  index: number,
  count: number,
  orientation: 'horizontal' | 'vertical' = 'horizontal',
): number | null {
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  const next = orientation === 'vertical' ? 'ArrowDown' : 'ArrowRight';
  const prev = orientation === 'vertical' ? 'ArrowUp' : 'ArrowLeft';
  if (key === next) return (index + 1) % count;
  if (key === prev) return (index + count - 1) % count;
  return null;
}
