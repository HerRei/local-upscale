import { expect, test } from '@playwright/test';
import { openApp } from './app';

test('browse a finished photo while a video continues running', async ({ page }) => {
  await page.setViewportSize({ width: 1380, height: 860 });
  await openApp(page, {
    photos: 1,
    video: true,
    runningVideo: true,
    worker: 'ready',
    pairs: true,
    finished: true,
    task: 'video',
    outputs: { 1: [{ id: 'old', label: 'SPAN ×4' }] },
  });
  await page.getByRole('tab', { name: /^Outputs/ }).click();
  await page.getByRole('button', { name: 'Show SPAN ×4, Alpine lake 1.jpg' }).click();
  await expect(page.locator('.preview-header')).toContainText('Alpine lake 1.jpg');
  await expect(page.locator('.result-image')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open', exact: true })).toBeVisible();
  await expect(page.locator('.status-copy')).toContainText('Enhancing video');
  await page.getByRole('tab', { name: /^Inputs/ }).click();
  await expect(
    page.getByRole('progressbar', { name: 'Progress of Alpine lake.mp4' }),
  ).toHaveAttribute('aria-valuenow', '40');
  await page
    .locator('.media-row')
    .filter({ hasText: 'Alpine lake.mp4' })
    .getByRole('button')
    .first()
    .click();
  await expect(page.locator('.preview-header')).toContainText('Alpine lake.mp4');
  await expect(page.getByRole('button', { name: 'Open', exact: true })).toHaveCount(0);
});
