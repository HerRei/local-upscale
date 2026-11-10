import { expect, test } from '@playwright/test';
import { openApp } from './app';

test('video Compare shares a clock, seeks, swaps, adds boxes and releases players', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openApp(page, { photos: 1, video: true, videoCompare: true, task: 'video' });
  await page.getByRole('tab', { name: /Outputs/ }).click();
  await page.getByRole('checkbox', { name: '15 fps result, Alpine lake.mp4' }).click();
  await page.getByRole('checkbox', { name: '60 fps result, Alpine lake.mp4' }).click();
  expect(await page.locator('.compare-pane').count()).toBe(0);
  await page.getByRole('button', { name: 'Compare these 2' }).click();
  const compare = page.locator('.compare-pane');
  const videos = compare.locator('video');
  await expect(
    compare.getByRole('slider', { name: 'Divider between slot 1 and slot 2' }),
  ).toBeVisible();
  await expect(videos).toHaveCount(2);
  await expect(compare.getByRole('group', { name: 'Video playback controls' })).toHaveCount(1);
  await expect
    .poll(() =>
      videos.evaluateAll((nodes) =>
        nodes.every((node) => (node as HTMLVideoElement).readyState >= 3),
      ),
    )
    .toBe(true);
  await compare.getByRole('button', { name: 'Play comparison' }).click();
  // Observing media time proves actual decoding and playback, without a fixed test sleep.
  await expect
    .poll(() =>
      videos.evaluateAll((nodes) =>
        Math.min(...nodes.map((node) => (node as HTMLVideoElement).currentTime)),
      ),
    )
    .toBeGreaterThan(1);
  const times = await videos.evaluateAll((nodes) =>
    nodes.map((node) => (node as HTMLVideoElement).currentTime),
  );
  expect(Math.abs(times[0] - times[1])).toBeLessThanOrEqual(1 / 60);
  await compare.getByRole('button', { name: 'Pause comparison' }).click();
  await compare.getByRole('slider', { name: 'Video position' }).fill('2');
  await expect
    .poll(() =>
      videos.evaluateAll((nodes) =>
        nodes.every((node) => Math.abs((node as HTMLVideoElement).currentTime - 2) < 1 / 60),
      ),
    )
    .toBe(true);
  await compare.getByRole('button', { name: 'Swap slots 1 and 2' }).click();
  await expect(videos.first()).toHaveAttribute('aria-label', '1: 60 fps result');
  await page.getByRole('checkbox', { name: 'Original, Alpine lake.mp4' }).click();
  await expect(compare.locator('.boxes video')).toHaveCount(3);
  await page.keyboard.press('Escape');
  await expect(page.locator('.compare-pane')).toHaveCount(0);
  await expect(page.locator('video')).toHaveCount(0);
});
