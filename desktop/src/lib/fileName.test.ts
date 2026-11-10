import { describe, expect, it } from 'vitest';

import { splitFileName } from './fileName';

describe('file names cut in the middle', () => {
  it('keeps the last ten characters, extension included, as the tail', () => {
    expect(splitFileName('IMG_490C1E18-B4D1-47E5-AC62-FD97F5A26811.jpeg')).toEqual([
      'IMG_490C1E18-B4D1-47E5-AC62-FD97F5A',
      '26811.jpeg',
    ]);
    expect(splitFileName('Alpine lake 12.jpg')).toEqual(['Alpine l', 'ake 12.jpg']);
  });

  it('leaves a short name whole', () => {
    expect(splitFileName('x.png')).toEqual(['x.png', '']);
    expect(splitFileName('abcdefghij')).toEqual(['abcdefghij', '']);
  });

  it('keeps a long extension whole in the tail', () => {
    const [head, tail] = splitFileName('notes from the trip.webmanifest');
    expect(tail).toBe('.webmanifest');
    expect(head).toBe('notes from the trip');
  });

  it('never splits a character made of several code points', () => {
    const family = '👨‍👩‍👧‍👦';
    const accent = 'é';
    const name = `${family}${accent}${'x'.repeat(8)}${family}.jpg`;
    const [head, tail] = splitFileName(name);
    expect(head + tail).toBe(name);
    expect(tail.startsWith(family) || tail.startsWith(accent) || tail.startsWith('x')).toBe(true);
    expect([
      ...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(tail),
    ]).toHaveLength(10);
  });

  it('keeps the logical order of right-to-left and CJK names', () => {
    for (const name of [
      'صورة_في_ساعات_المساء_المتأخرة.jpg',
      '野生の花畑の写真_2026年10月の旅行.heic',
    ]) {
      const [head, tail] = splitFileName(name);
      expect(head + tail).toBe(name);
      expect(tail.endsWith(name.slice(name.lastIndexOf('.')))).toBe(true);
    }
  });
});
