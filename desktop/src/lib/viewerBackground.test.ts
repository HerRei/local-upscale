import { describe, expect, it } from 'vitest';
import { backgroundLabel, nextBackground } from './viewerBackground';

describe('viewerBackground', () => {
  it('cycles through the four backgrounds in order', () => {
    expect(nextBackground('checker')).toBe('black');
    expect(nextBackground('black')).toBe('grey');
    expect(nextBackground('grey')).toBe('white');
    expect(nextBackground('white')).toBe('checker');
  });

  it('returns the display label for each background', () => {
    expect(backgroundLabel('checker')).toBe('Checkerboard');
    expect(backgroundLabel('black')).toBe('Black');
    expect(backgroundLabel('grey')).toBe('Grey');
    expect(backgroundLabel('white')).toBe('White');
  });
});
