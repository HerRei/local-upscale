import { expect, test } from '@playwright/test';
import { hostCalls, installFakeHost } from './fake-host';

const report = {
  id: 'c5d9cc41-a9a8-4679-8e7f-a2ca008b9a04',
  report: {
    message: 'Engine stopped',
    os: 'macos',
    source: 'engine',
    exit: 'signal 11',
    limitation: 'A native host crash may not be captured.',
  },
};

test.beforeEach(async ({ page }) => {
  await installFakeHost(page, { settings: { crash_reports: true }, crashReports: [report] });
  await page.goto('/');
});

// Pixel baselines exist per platform; CI runs without @visual (see ci.yml).
test('crash notice @visual', async ({ page }) => {
  await expect(
    page.getByRole('dialog', { name: 'LocalSR quit unexpectedly last time.' }),
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('crash-notice.png');
});

test('review shows the report before opening an issue', async ({ page }) => {
  const notice = page.getByRole('dialog', { name: 'LocalSR quit unexpectedly last time.' });
  await expect(notice).toBeVisible();
  await notice.getByRole('button', { name: 'Review report…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Report a problem' });
  await expect(dialog.locator('textarea[readonly]')).toHaveValue(
    JSON.stringify(report.report, null, 2),
  );
  await dialog.getByRole('button', { name: 'Open GitHub Issue' }).click();
  await expect.poll(async () => (await hostCalls(page, 'delete_crash_report')).length).toBe(1);
  const calls = await hostCalls(page, 'open_bug_report');
  const url = new URL((calls[0].args as { url: string }).url);
  expect(url.searchParams.get('title')).toBe('Crash: Engine stopped');
  expect(url.searchParams.get('operating-system')).toBe('macOS');
  expect(url.searchParams.get('hardware')).toContain('signal 11');
});

test("Don't Send deletes the report", async ({ page }) => {
  const notice = page.getByRole('dialog', { name: 'LocalSR quit unexpectedly last time.' });
  await notice.getByRole('button', { name: "Don't Send" }).click();
  await expect(notice).toBeHidden();
  expect(await hostCalls(page, 'delete_crash_report')).toMatchObject([{ args: { id: report.id } }]);
  expect(await hostCalls(page, 'open_bug_report')).toHaveLength(0);
});
