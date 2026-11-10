import { expect, test, type Page } from '@playwright/test';
import { openApp } from './app';
import { ready } from './fixtures';

// A running picture is marked in the Inputs list: a progress line inside its row (the row keeps
// its height), the text "Running · 38 %", and a lighter background.

const batch = (selected: number) => ({
  ...ready,
  photos: 7,
  running: true,
  selected,
  rows: {
    1: { status: 'completed', progress: 100, elapsed_seconds: 31 },
    2: { status: 'running', progress: 38 },
  },
});

const rows = (page: Page) => page.locator('.media-list .media-row');

// Set by the maintainer to look at the pane; the tests do not depend on it.
const SHOTS = process.env.ROWRUN_SHOTS;
async function look(page: Page, name: string) {
  if (SHOTS) await page.locator('.media-pane').screenshot({ path: `${SHOTS}/${name}.png` });
}

test('a running row keeps its height and carries a line as wide as the row, filled to its progress', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1380, height: 860 });
  await openApp(page, batch(1));
  const running = rows(page).nth(1);
  const idle = rows(page).nth(3);
  const rowBox = (await running.boundingBox())!;
  expect(rowBox.height).toBe((await idle.boundingBox())!.height);
  const bar = running.getByRole('progressbar', { name: 'Progress of Alpine lake 2.jpg' });
  const barBox = (await bar.boundingBox())!;
  expect(barBox.width).toBe(rowBox.width);
  expect(barBox.height).toBe(2);
  expect(barBox.y + barBox.height).toBeCloseTo(rowBox.y + rowBox.height, 0);
  const fill = (await bar.locator('i').boundingBox())!;
  expect(fill.width / barBox.width).toBeCloseTo(0.38, 1);
  await expect(running.locator('.queue-state')).toHaveText('Running · 38 %');
  await expect(rows(page).nth(0).locator('.queue-state')).toHaveText('Done · 31 s');
  await expect(idle.locator('.queue-state')).toHaveText('Queued');
  await expect(page.getByRole('progressbar')).toHaveCount(1);
  await look(page, 'selected-running-first-other');
});

test('the running row differs from a selected row', async ({ page }) => {
  await page.setViewportSize({ width: 1380, height: 860 });
  await openApp(page, batch(2));
  await expect(rows(page).nth(1)).toHaveClass(/selected/);
  await look(page, 'running-selected');
});

test('two rows can run at once, each with its own line', async ({ page }) => {
  await page.setViewportSize({ width: 1380, height: 860 });
  await openApp(page, {
    ...ready,
    photos: 7,
    running: true,
    rows: { 1: { status: 'running', progress: 38 }, 2: { status: 'running', progress: 71 } },
  });
  await expect(page.getByRole('progressbar')).toHaveCount(2);
  await expect(page.getByRole('progressbar').nth(1)).toHaveAttribute('aria-valuenow', '71');
  await look(page, 'two-lanes');
});

test.describe('adding to a running queue', () => {
  const queue = {
    ...ready,
    settings: { ...ready.settings, batch_mode: true },
    running: true,
    rows: {
      1: { status: 'completed', progress: 100 },
      2: { status: 'completed', progress: 100 },
      3: { status: 'completed', progress: 100 },
      4: { status: 'running', progress: 38 },
    },
  };
  const add = (page: Page) => page.getByRole('button', { name: /^Add (selected|\d+) to queue$/ });

  test('offers nothing while every input has a job in this run', async ({ page }) => {
    await openApp(page, { ...queue, photos: 7 });
    await expect(page.getByRole('button', { name: 'Cancel queue' })).toBeVisible();
    await expect(add(page)).toHaveCount(0);
  });

  test('offers the inputs added after the queue started, only those', async ({ page }) => {
    await openApp(page, { ...queue, photos: 9, jobPhotos: 7 });
    await expect(add(page)).toHaveText('Add 2 to queue');
  });
});
