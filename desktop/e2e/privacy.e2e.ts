import { expect, test } from '@playwright/test';
import { hostCalls, installFakeHost } from './fake-host';

test('first launch saves the privacy choices before checking', async ({ page }) => {
  await installFakeHost(page, { privacyPending: true });
  await page.goto('/');
  const welcome = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
  await expect(welcome).toBeVisible();
  expect(await hostCalls(page, 'check_update_at_launch')).toHaveLength(0);
  await welcome.getByLabel('Check for updates automatically').uncheck();
  await welcome.getByLabel('Send an anonymous count with each update check').check();
  await welcome.getByRole('button', { name: 'Continue' }).click();
  await expect(welcome).toBeHidden();
  expect(await hostCalls(page, 'set_privacy_choice')).toMatchObject([{ args: { checkAtLaunch: false, anonymousCount: true } }]);
  expect(await hostCalls(page, 'check_update_at_launch')).toHaveLength(0);
});
