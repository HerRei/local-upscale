import { expect, test } from '@playwright/test';
import { openApp } from './app';
import { hostCalls } from './fake-host';
import { video } from './fixtures';

test('Cancel on a running video asks whether to keep or discard, and Keep finished part saves preference when ticked', async ({
  page,
}) => {
  await openApp(page, {
    ...video,
    worker: 'ready',
    runningVideo: true,
    settings: { ...video.settings, stopped_job_result: 'ask' },
  });

  await page.getByRole('button', { name: 'Cancel' }).click();
  const dialog = page.getByRole('dialog', { name: 'Stop Alpine lake.mp4?' });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('0:05 of 0:12 is finished.');

  // Esc keeps the job running without calling cancel_jobs.
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  expect(await hostCalls(page, 'cancel_jobs')).toHaveLength(0);

  // Opening again, ticking Don't ask again, and clicking Keep finished part saves and cancels.
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole('checkbox', { name: "Don't ask again" }).check();
  await dialog.getByRole('button', { name: 'Keep finished part' }).click();

  await expect(dialog).toHaveCount(0);
  await expect
    .poll(async () => (await hostCalls(page, 'set_preference')).at(-1)?.args)
    .toEqual({ name: 'stopped_job_result', value: 'keep' });
  await expect
    .poll(async () => (await hostCalls(page, 'cancel_jobs')).at(-1)?.args)
    .toEqual({ keepPartial: true });
});
