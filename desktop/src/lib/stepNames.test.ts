import { describe, expect, it } from 'vitest';
import fixture from '../../src-tauri/tests/fixtures/step_names.json';
import type { PlanStage } from './plan';
import { planLine } from './planLine';

// The host names the running step from the same list (plan_step_name, worker.rs).
describe('Step names', () => {
  it.each(fixture.cases)('$name', ({ stage, settings, video, name }) => {
    const merged = { ...fixture.settings, ...settings } as Parameters<typeof planLine>[1];
    const media = { kind: video ? ('video' as const) : ('image' as const) };
    expect(planLine([stage as PlanStage], merged, media)).toEqual([name]);
  });
});
