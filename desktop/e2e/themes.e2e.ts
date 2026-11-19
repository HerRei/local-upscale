import { expect, test } from '@playwright/test';
import { openApp, showPane } from './app';
import { withOutputs } from './fixtures';

for (const [name, appearance, colourBlind] of [
  ['light', 'light', false],
  ['dark-colour-blind', 'dark', true],
  ['light-colour-blind', 'light', true],
] as const) {
  test(`appearance ${name}`, async ({ page }) => {
    await openApp(page, {
      ...withOutputs,
      settings: { ...withOutputs.settings, appearance, colour_blind_palette: colourBlind },
    });
    await expect(page.locator('html')).toHaveAttribute('data-theme', appearance);
    await page.evaluate(() => document.fonts.ready);
    await expect(page).toHaveScreenshot(`theme-${name}-main.png`);
    await showPane(page, 'enhance');
    await expect(page).toHaveScreenshot(`theme-${name}-enhance.png`);
    await showPane(page, 'media');
    await page.getByRole('button', { name: 'Clear', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page).toHaveScreenshot(`theme-${name}-dialog.png`);
  });
}
