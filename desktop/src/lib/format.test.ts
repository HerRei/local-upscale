import { describe, expect, it } from 'vitest';
import { formatBytes, formatDuration } from './format';

describe('format', () => {
  it('formats bounded human-readable values', () => {
    expect(formatBytes(1024 ** 3)).toBe('1.0 GB');
    expect(formatDuration(125)).toBe('2:05');
  });
  it('writes a size below one byte in bytes, never with a missing unit', () => {
    expect(formatBytes(0.5)).toBe('1 B');
    expect(formatBytes(1e-9)).not.toMatch(/undefined|NaN/);
  });
});
