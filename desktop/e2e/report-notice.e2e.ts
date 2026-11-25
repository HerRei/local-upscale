import { expect, test } from '@playwright/test';
import { openApp } from './app';
import { emit, hostCalls } from './fake-host';

test('skip hides the offered version', async ({ page }) => {
  await openApp(page, { update: '0.5.0' });
  const notice = page.getByRole('status').filter({ hasText: 'LocalSR 0.5.0 is available' });
  await expect(notice).toBeVisible();
  await notice.getByRole('button', { name: 'Skip This Version' }).click();
  await expect(notice).toBeHidden();
  expect(await hostCalls(page, 'skip_update')).toHaveLength(1);
});

test('report previews diagnostics and opens the prefilled issue', async ({ page }) => {
  await openApp(page, {
    diagnosticSummary: JSON.stringify({ platform: 'linux', file: '/home/alice/photo.jpg' }),
  });
  await emit(page, 'native-menu-action', 'help.report-problem');
  const dialog = page.getByRole('dialog', { name: 'Report a problem' });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('What happened?').fill('It stopped');
  await dialog.getByText('Preview diagnostics').click();
  await expect(dialog.locator('textarea[readonly]')).not.toContainText('alice');
  await dialog.getByRole('button', { name: 'Open GitHub Issue' }).click();
  await expect.poll(async () => (await hostCalls(page, 'open_bug_report')).length).toBe(1);
  const calls = await hostCalls(page, 'open_bug_report');
  const url = new URL((calls[0].args as { url: string }).url);
  expect(url.searchParams.get('what-happened')).toBe('It stopped');
  expect(url.searchParams.get('hardware')).not.toContain('alice');
});

test('the diagnostics view opens the report dialog', async ({ page }) => {
  await openApp(page, {});
  await emit(page, 'native-menu-action', 'view.performance');
  await page
    .getByRole('dialog', { name: 'Performance & Diagnostics' })
    .getByRole('button', { name: 'Report a problem…' })
    .click();
  await expect(page.getByRole('dialog', { name: 'Report a problem' })).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Performance & Diagnostics' })).toHaveCount(0);
});
