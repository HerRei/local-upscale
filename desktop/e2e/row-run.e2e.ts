import { expect, test, type Page } from '@playwright/test';
import { openApp } from './app';
import { hostCalls, refuse } from './fake-host';
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
  await expect(page.locator('.media-list').getByRole('progressbar')).toHaveCount(1);
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
  await expect(page.locator('.media-list').getByRole('progressbar')).toHaveCount(2);
  await expect(page.locator('.media-list').getByRole('progressbar').nth(1)).toHaveAttribute(
    'aria-valuenow',
    '71',
  );
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

test('one running job is cancelled with Cancel, a queue with Cancel queue', async ({ page }) => {
  await openApp(page, { ...ready, photos: 1, running: true });
  const bar = page.locator('.status-bar');
  await expect(bar.getByRole('button', { name: 'Cancel', exact: true })).toBeVisible();
  await expect(bar.getByRole('button', { name: 'Cancel queue' })).toHaveCount(0);
});

test.describe('removing while a queue runs', () => {
  const sentence = 'Cannot remove while it is running. Cancel the run instead.';
  const queue = { ...batch(1), settings: { ...ready.settings } };

  test('the running picture keeps its remove button inactive, the others can go', async ({
    page,
  }) => {
    await openApp(page, queue);
    const running = page.getByRole('button', { name: 'Remove Alpine lake 2.jpg' });
    await expect(running).toBeDisabled();
    await expect(running).toHaveAttribute('title', sentence);
    await expect(page.getByRole('button', { name: 'Remove Alpine lake 1.jpg' })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Remove Alpine lake 5.jpg' })).toBeEnabled();
    const clear = page.getByRole('button', { name: 'Clear' });
    await expect(clear).toBeDisabled();
    await expect(clear).toHaveAttribute('title', 'Cannot clear the list while a queue runs.');
  });

  test('removing a queued picture asks the host to remove it', async ({ page }) => {
    await openApp(page, queue);
    await page.getByRole('button', { name: 'Remove Alpine lake 5.jpg' }).click();
    const calls = await hostCalls(page, 'remove_media');
    expect(calls.at(-1)?.args).toMatchObject({ id: 'photo-5' });
  });

  test('a refusal from the host shows its sentence', async ({ page }) => {
    await openApp(page, queue);
    await refuse(page, 'remove_media', sentence);
    await page.getByRole('button', { name: 'Remove Alpine lake 5.jpg' }).click();
    await expect(page.getByRole('dialog')).toContainText(sentence);
  });
});

test('from 100 % until the file is written the row and the status bar say Saving', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1380, height: 860 });
  await openApp(page, {
    ...batch(2),
    rows: {
      1: { status: 'completed', progress: 100, elapsed_seconds: 31 },
      2: { status: 'running', progress: 100 },
    },
  });
  const running = rows(page).nth(1);
  await expect(running.getByText('Saving…')).toBeVisible();
  await expect(running).not.toContainText('100 %');
  const bar = page.locator('.status-bar');
  await expect(bar.locator('strong')).toHaveText(/^Saving the result/);
  await expect(bar).not.toContainText(/ETA|≈|100%/);
  await expect(rows(page).nth(0)).toContainText('Done · 31 s');
});
