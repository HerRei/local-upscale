import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { MAX_SLOTS, swapSlots, toggleSlot } from './compareOutputs';
import { clampPan, panBounds } from './viewport';
import type { ComparePick } from './types';

const property = { seed: 0x038, numRuns: 120, endOnFailure: true };

describe('Compare property invariants', () => {
  it('slot assignment stays unique, bounded and preserves the remaining order', () => {
    fc.assert(
      fc.property(
        fc.array(fc.tuple(fc.integer({ min: 0, max: 5 }), fc.integer({ min: 0, max: 8 })), {
          minLength: 0,
          maxLength: 80,
        }),
        (operations) => {
          let selected: ComparePick[] = [];
          for (const [media, output] of operations) {
            const pick: ComparePick =
              output === 0
                ? { kind: 'original', media_id: `m${media}` }
                : { kind: 'output', media_id: `m${media}`, job_id: `j${output}` };
            const before = selected;
            const wasSelected = selected.some(
              (entry) => JSON.stringify(entry) === JSON.stringify(pick),
            );
            selected = toggleSlot(selected, pick);
            expect(selected.length).toBeLessThanOrEqual(MAX_SLOTS);
            expect(new Set(selected.map((entry) => JSON.stringify(entry))).size).toBe(
              selected.length,
            );
            if (wasSelected) {
              expect(selected).toEqual(
                before.filter((entry) => JSON.stringify(entry) !== JSON.stringify(pick)),
              );
            }
          }
        },
      ),
      property,
    );
  });

  it('slot swaps are permutations and invalid indexes are no-ops', () => {
    fc.assert(
      fc.property(
        fc.uniqueArray(fc.integer({ min: 0, max: 20 }), { maxLength: MAX_SLOTS }),
        fc.integer({ min: -3, max: 8 }),
        fc.integer({ min: -3, max: 8 }),
        (ids, first, second) => {
          const picks: ComparePick[] = ids.map((id) => ({ kind: 'original', media_id: `${id}` }));
          const swapped = swapSlots(picks, first, second);
          expect(swapped.map((pick) => pick.media_id).sort()).toEqual(
            picks.map((pick) => pick.media_id).sort(),
          );
          if (first < 0 || second < 0 || first >= picks.length || second >= picks.length)
            expect(swapped).toBe(picks);
        },
      ),
      property,
    );
  });

  it('clamped pans stay inside the image bounds at any zoom', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 3000 }),
        fc.integer({ min: 1, max: 3000 }),
        fc.integer({ min: 1, max: 3000 }),
        fc.integer({ min: 1, max: 3000 }),
        fc.double({ min: 0.1, max: 64, noNaN: true }),
        fc.double({ min: -1e6, max: 1e6, noNaN: true }),
        fc.double({ min: -1e6, max: 1e6, noNaN: true }),
        (canvasWidth, canvasHeight, fitWidth, fitHeight, zoom, x, y) => {
          const bounds = panBounds(
            { width: canvasWidth, height: canvasHeight },
            { width: fitWidth, height: fitHeight },
            zoom,
          );
          const clamped = clampPan({ x, y }, bounds);
          expect(Math.abs(clamped.x)).toBeLessThanOrEqual(bounds.x);
          expect(Math.abs(clamped.y)).toBeLessThanOrEqual(bounds.y);
        },
      ),
      property,
    );
  });
});
