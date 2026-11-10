import { expect, test } from '@playwright/test';
import { openApp } from './app';
import { emit } from './fake-host';
import { ready } from './fixtures';

const report = (remaining: number, elapsed: number) => ({
  type: 'progress',
  data: {
    job_id: 'job-photo-1',
    percentage: 40,
    completed_tiles: 5,
    total_tiles: 12,
    elapsed_seconds: elapsed,
    estimated_remaining_seconds: remaining,
  },
});

test.describe('the remaining time while a queue runs', () => {
  test('counts down by itself and ignores a report that is close to the countdown', async ({
    page,
  }) => {
    await page.clock.install();
    await openApp(page, { ...ready, photos: 2, running: true });
    const detail = page.locator('.status-detail');
    await emit(page, 'worker-message', report(100, 20));
    await expect(detail).toContainText('5 of 12 tiles · ETA ≈ 1:40');
    await page.clock.runFor(30_000);
    await expect(detail).toContainText('5 of 12 tiles · ETA ≈ 1:10');
    // The engine's number moves a little, as the numbers of a steady run do.
    await emit(page, 'worker-message', report(74, 50));
    await page.clock.runFor(1_000);
    await expect(detail).toContainText('5 of 12 tiles · ETA ≈ 1:10');
    await page.clock.runFor(30_000);
    await expect(detail).toContainText('5 of 12 tiles · ETA ≈ 40s');
  });

  test('shows the same time in the queue summary and counts it down too', async ({ page }) => {
    await page.clock.install();
    await openApp(page, { ...ready, photos: 2, running: true });
    await emit(page, 'worker-message', report(100, 20));
    const queue = page.locator('.queue-eta');
    await expect(queue).toContainText('Current item: ≈ 1:40');
    await page.clock.runFor(60_000);
    await expect(queue).toContainText('Current item: ≈ 40s');
  });
});
