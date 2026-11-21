import { expect, test, type Page } from '@playwright/test';
import { openApp } from './app';
import { emit } from './fake-host';
import { ready } from './fixtures';

// The three "working" indicators keep one DOM node and one running animation for a whole job:
// nothing may restart them while progress, the clock tick and the status text change.

const running = {
  ...ready,
  photos: 2,
  running: true,
  task: 'edit',
  rows: { 1: { status: 'running', progress: 40 } },
};

/** Samples the animation of `selector` for `ms`: the node must stay the same one and its clock
 *  may only move forward, within one duration (it wraps at the end of each cycle). */
async function sample(page: Page, selector: string, ms: number) {
  return page.evaluate(
    async ({ selector, ms }) => {
      const element = document.querySelector(selector) as
        (HTMLElement & { __stamp?: number }) | null;
      if (!element) return { error: `${selector} is not on screen` };
      element.__stamp = Math.random();
      const stamp = element.__stamp;
      const seen: { same: boolean; time: number; duration: number; state: string }[] = [];
      const end = performance.now() + ms;
      while (performance.now() < end) {
        const now = document.querySelector(selector) as (HTMLElement & { __stamp?: number }) | null;
        const animation = now
          ?.getAnimations?.({ subtree: true })
          .find((a) => 'animationName' in a && a.playState !== 'idle');
        const timing = animation?.effect?.getComputedTiming();
        seen.push({
          same: now?.__stamp === stamp,
          time: Number(animation?.currentTime ?? -1),
          duration: Number(timing?.duration ?? 0),
          state: animation?.playState ?? 'none',
        });
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      return { seen };
    },
    { selector, ms },
  );
}

function check(result: Awaited<ReturnType<typeof sample>>, label: string) {
  if ('error' in result) throw new Error(String(result.error));
  const { seen } = result;
  expect(
    seen.every((s) => s.same),
    `${label}: the element was replaced`,
  ).toBe(true);
  expect(
    seen.every((s) => s.state === 'running'),
    `${label}: not running`,
  ).toBe(true);
  let last = -1;
  for (const s of seen) {
    // currentTime counts up to iteration * duration; it never goes back.
    expect(s.time, `${label}: the animation went back`).toBeGreaterThanOrEqual(last);
    last = s.time;
  }
  expect(last, `${label}: no time passed`).toBeGreaterThan(seen[0].time);
}

for (const [name, selector] of [
  ['the sweep over the picture', '.edit-sweep.on'],
  ['the status bar line', '.status-bar .progress i'],
  ["the Inputs row's line", '.media-list .row-progress i'],
] as const)
  test(`${name} keeps running without a restart`, async ({ page }) => {
    // The suite runs with reduced motion; the animations are what is under test here.
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await openApp(page, running);
    // Progress arrives the way the engine sends it, several times a second, while the sample runs.
    let sending = true;
    const feed = (async () => {
      for (let step = 0; sending; step++) {
        await emit(page, 'worker-message', {
          type: 'progress',
          data: {
            job_id: 'job-photo-1',
            percentage: Math.min(95, 40 + step * 2),
            completed_tiles: step,
            total_tiles: 40,
            unit: 'steps',
            detail: `Step ${step} of 40`,
            elapsed_seconds: step / 4,
            estimated_remaining_seconds: 40 - step / 4,
          },
        });
        await page.waitForTimeout(250);
      }
    })();
    const result = await sample(page, selector, 6000);
    sending = false;
    await feed;
    check(result, name);
  });

for (const [name, track] of [
  ['status bar', '.status-bar .progress'],
  ['row', '.media-list .row-progress'],
] as const)
  test(`the ${name} shine is as wide as the whole line, whatever the fill`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await openApp(page, running);
    const widths = await page.evaluate((selector) => {
      const line = document.querySelector(selector) as HTMLElement;
      const fill = line.querySelector('i') as HTMLElement;
      return {
        line: line.getBoundingClientRect().width,
        fill: fill.getBoundingClientRect().width,
        shine: parseFloat(getComputedStyle(fill, '::after').width),
        name: getComputedStyle(fill, '::after').animationName,
      };
    }, track);
    expect(widths.fill).toBeLessThan(widths.line * 0.9);
    expect(widths.shine).toBeCloseTo(widths.line, 0);
    expect(widths.name).toBe('progress-shine');
  });

test('the sweep layer stays mounted while no edit runs, so showing it restarts nothing', async ({
  page,
}) => {
  await openApp(page, ready);
  await expect(page.locator('.edit-sweep')).toHaveCount(1);
  await expect(page.locator('.edit-sweep')).toHaveCSS('opacity', '0');
});
