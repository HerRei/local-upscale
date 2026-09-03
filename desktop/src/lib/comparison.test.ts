import { describe, expect, it } from 'vitest';

import { clampComparison, comparisonFromKey, comparisonFromPointer } from './comparison';

describe('shared image and video comparison math', () => {
  it('maps pointers into a bounded percentage', () => {
    expect(comparisonFromPointer(150, 100, 200)).toBe(25);
    expect(comparisonFromPointer(20, 100, 200)).toBe(0);
    expect(comparisonFromPointer(400, 100, 200)).toBe(100);
    expect(comparisonFromPointer(100, 100, 0)).toBe(0);
  });

  it('supports arrows, page steps, and endpoint keys', () => {
    expect(comparisonFromKey(50, 'ArrowLeft')).toBe(49);
    expect(comparisonFromKey(50, 'ArrowUp')).toBe(51);
    expect(comparisonFromKey(50, 'PageDown')).toBe(40);
    expect(comparisonFromKey(50, 'PageUp')).toBe(60);
    expect(comparisonFromKey(50, 'Home')).toBe(0);
    expect(comparisonFromKey(50, 'End')).toBe(100);
    expect(comparisonFromKey(50, 'Enter')).toBeNull();
  });

  it('handles non-finite and endpoint values safely', () => {
    expect(clampComparison(Number.NaN)).toBe(50);
    expect(comparisonFromKey(0, 'ArrowLeft')).toBe(0);
    expect(comparisonFromKey(100, 'ArrowRight')).toBe(100);
  });
});
