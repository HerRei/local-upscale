import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { EXAMPLES } from './examples';

const DIR = join(process.cwd(), 'src/assets/examples');
const MAX_TOTAL_BYTES = 1_000_000;

describe('example pictures', () => {
  const files = readdirSync(DIR).filter((name) => name.endsWith('.webp'));

  it('are 14 files that add up to at most 1,000,000 bytes', () => {
    expect(files).toHaveLength(14);
    const total = files.reduce((sum, name) => sum + statSync(join(DIR, name)).size, 0);
    expect(total).toBeLessThanOrEqual(MAX_TOTAL_BYTES);
  });

  it('are exactly the files the manifest names', () => {
    const named = new Set(EXAMPLES.flatMap((example) => [example.before, example.after]));
    expect([...named].sort()).toEqual([...files].sort());
  });

  it('are 624 x 390 WebP files', () => {
    for (const name of files) {
      const bytes = readFileSync(join(DIR, name));
      expect(bytes.toString('ascii', 0, 4)).toBe('RIFF');
      expect(bytes.toString('ascii', 8, 12)).toBe('WEBP');
      // Lossy WebP (VP8): 14-bit width and height at offsets 26 and 28.
      expect(bytes.toString('ascii', 12, 16)).toBe('VP8 ');
      expect(bytes.readUInt16LE(26) & 0x3fff).toBe(624);
      expect(bytes.readUInt16LE(28) & 0x3fff).toBe(390);
    }
  });

  it('record a real source and how each picture was made', () => {
    for (const example of EXAMPLES) {
      expect(example.source.url).toMatch(/^https:\/\//);
      expect(example.source.title).not.toMatch(/placeholder/i);
      expect(example.made.settings).not.toMatch(/placeholder/i);
      expect(example.made.app).toBe('0.7.0');
    }
  });
});
