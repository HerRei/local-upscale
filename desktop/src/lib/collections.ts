import type { AppSnapshot } from './types';

export function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const copy = { ...record };
  delete copy[key];
  return copy;
}

export function mediaNamesFor(paths: string[], media: AppSnapshot['media']): string[] {
  return paths.map(
    (path) => media.find((media) => media.path === path)?.name ?? path.split(/[\\/]/).pop() ?? path,
  );
}
