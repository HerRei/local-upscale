import { expect, test } from '@playwright/test';
import { answer, hostCalls, installFakeHost } from './fake-host';

test('first launch setup screens @visual', async ({ page }) => {
  await installFakeHost(page, { privacyPending: true });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
  await expect(setup).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('setup-privacy-dark.png');
  await setup.getByRole('button', { name: 'About Offer to report crashes' }).focus();
  await expect(page).toHaveScreenshot('setup-privacy-tip-dark.png');
  await setup.getByRole('button', { name: 'Continue' }).click();
  await expect(page).toHaveScreenshot('setup-look-dark.png');
  await setup.getByRole('radio', { name: 'Light' }).click();
  await expect(page).toHaveScreenshot('setup-look-light.png');
});

test('setup saves nothing before Done', async ({ page }) => {
  await installFakeHost(page, { privacyPending: true });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
  await setup.getByRole('button', { name: 'Continue' }).click();
  expect(await hostCalls(page, 'set_privacy_choice')).toHaveLength(0);
  expect(await hostCalls(page, 'check_update_at_launch')).toHaveLength(0);
  await setup.getByRole('button', { name: 'Back' }).click();
  await setup.getByRole('button', { name: 'Continue' }).click();
  await setup.getByRole('button', { name: 'Done' }).click();
  expect(await hostCalls(page, 'set_privacy_choice')).toHaveLength(1);
});

test('first launch setup previews live, saves choices, and persists across reload', async ({
  page,
}) => {
  await installFakeHost(page, { privacyPending: true });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
  await setup
    .getByRole('switch', { name: 'Check for updates when LocalSR starts', exact: true })
    .uncheck();
  await setup
    .getByRole('switch', { name: 'Send an anonymous count with each update check', exact: true })
    .uncheck();
  await setup.getByRole('switch', { name: 'Offer to report crashes', exact: true }).uncheck();
  await setup.getByRole('button', { name: 'Continue' }).click();
  await setup.getByRole('radio', { name: 'Light' }).click();
  await setup.getByRole('switch', { name: 'Colour-blind friendly colours', exact: true }).check();
  await setup.getByRole('switch', { name: 'High contrast', exact: true }).check();
  await expect(page.locator('html')).toHaveAttribute('data-contrast', 'high');
  await setup.getByRole('switch', { name: 'Show LocalSR in the menu bar', exact: true }).uncheck();
  await setup
    .getByRole('switch', { name: 'Play a sound when a file finishes', exact: true })
    .check();
  await setup.getByRole('button', { name: 'Done' }).click();
  await expect(setup).toBeHidden();
  expect((await hostCalls(page, 'set_privacy_choice')).map(({ args }) => ({ args }))).toEqual([
    {
      args: {
        choices: {
          appearance: 'light',
          colourBlindPalette: true,
          contrast: 'high',
          checkAtLaunch: false,
          anonymousCount: false,
          crashReports: false,
          showInMenuBar: false,
          completionSound: true,
        },
      },
    },
  ]);
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('html')).toHaveAttribute('data-palette', 'colour-blind');
  await expect(page.locator('html')).toHaveAttribute('data-contrast', 'high');
  await expect(setup).toHaveCount(0);
});

test('first launch checks for updates only after Done when enabled', async ({ page }) => {
  await installFakeHost(page, { privacyPending: true, update: '0.5.0-beta' });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
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
  expect(await hostCalls(page, 'check_update_at_launch')).toHaveLength(0);
  await setup.getByRole('button', { name: 'Done' }).click();
  await expect.poll(async () => (await hostCalls(page, 'check_update_at_launch')).length).toBe(1);
});

test('Windows uses system tray wording', async ({ page }) => {
  await installFakeHost(page, { privacyPending: true, platform: 'Win32' });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
  await setup.getByRole('button', { name: 'Continue' }).click();
  await expect(
    setup.getByRole('switch', { name: 'Show LocalSR in the system tray', exact: true }),
  ).toBeVisible();
});

test('every tip opens inside the dialog', async ({ page }) => {
  await installFakeHost(page, { privacyPending: true });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
  for (let step = 1; step <= 2; step++) {
    const tips = setup.locator('.help-tip');
    for (let i = 0; i < (await tips.count()); i++) {
      await tips.nth(i).focus();
      const box = await setup.locator('.help-tip-text:visible').boundingBox();
      const dialog = await setup.boundingBox();
      expect(
        box &&
          dialog &&
          box.x >= dialog.x &&
          box.y >= dialog.y &&
          box.x + box.width <= dialog.x + dialog.width &&
          box.y + box.height <= dialog.y + dialog.height,
      ).toBeTruthy();
    }
    if (step === 1) await setup.getByRole('button', { name: 'Continue' }).click();
  }
});

test('fits the smallest window', async ({ page }) => {
  await page.setViewportSize({ width: 880, height: 640 });
  await installFakeHost(page, { privacyPending: true });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: 'Welcome to LocalSR' });
  for (const button of ['Continue', 'Done']) {
    await expect(setup.getByRole('button', { name: button })).toBeVisible();
    expect(await setup.evaluate((el) => el.scrollHeight <= el.clientHeight)).toBe(true);
    if (button === 'Continue') await setup.getByRole('button', { name: button }).click();
  }
});
