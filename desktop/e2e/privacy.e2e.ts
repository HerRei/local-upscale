import { expect, test } from '@playwright/test';
import { answer, hostCalls, installFakeHost } from './fake-host';

// Pixel baselines exist per platform; CI runs without @visual (see ci.yml).
test('first launch setup screens @visual', async ({ page }) => {
  await installFakeHost(page, { privacyPending: true });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
  await expect(setup).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('setup-dark.png');
  await setup.getByRole('radio', { name: 'Light' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page).toHaveScreenshot('setup-light.png');
});

test('first launch setup previews live, saves choices, and persists across reload', async ({
  page,
}) => {
  await installFakeHost(page, { privacyPending: true });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
  await expect(setup).toBeVisible();
  expect(await hostCalls(page, 'check_update_at_launch')).toHaveLength(0);

  await setup.getByRole('radio', { name: 'Light' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  await setup.getByLabel('Colour-blind friendly colours').check();
  await expect(page.locator('html')).toHaveAttribute('data-palette', 'colour-blind');

  await setup.getByLabel('Check for updates automatically').uncheck();
  // Both start on for a new install; the user can switch them off here.
  await expect(setup.getByLabel('Send an anonymous count with each update check')).toBeChecked();
  await expect(setup.getByLabel('Offer to report crashes')).toBeChecked();
  await setup.getByLabel('Send an anonymous count with each update check').uncheck();
  await setup.getByLabel('Offer to report crashes').uncheck();
  await setup.getByRole('button', { name: 'Continue' }).click();

  await expect(setup).toBeHidden();
  expect(await hostCalls(page, 'set_privacy_choice')).toMatchObject([
    {
      args: {
        appearance: 'light',
        colourBlindPalette: true,
        checkAtLaunch: false,
        anonymousCount: false,
        crashReports: false,
      },
    },
  ]);
  expect(await hostCalls(page, 'check_update_at_launch')).toHaveLength(0);

  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('html')).toHaveAttribute('data-palette', 'colour-blind');
  await expect(page.getByRole('dialog', { name: 'Welcome to LocalSR' })).toHaveCount(0);
});

test('first launch checks for updates only after Continue when enabled', async ({ page }) => {
  await installFakeHost(page, { privacyPending: true, update: '0.5.0-beta' });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
  await expect(setup).toBeVisible();
  expect(await hostCalls(page, 'check_update_at_launch')).toHaveLength(0);
  await answer(page, 'update_status', {
    configured: true,
    managed_by_store: false,
    channel: 'beta',
    target: '',
    stage: 'idle',
    version: '',
    notes: '',
    skipped_version: '',
    size: 0,
    downloaded: 0,
    message: '',
    settings_recovery: false,
  });
  await setup.getByRole('button', { name: 'Continue' }).click();
  await expect(setup).toBeHidden();
  await expect.poll(async () => (await hostCalls(page, 'check_update_at_launch')).length).toBe(1);
});
