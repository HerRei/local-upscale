import { expect, test } from '@playwright/test';
import { contrastOnPage, openApp, showPane } from './app';
import { emit } from './fake-host';
import { ready, withOutputs } from './fixtures';

for (const [name, appearance, colourBlind, contrast, allShots] of [
  ['light', 'light', false, undefined, true],
  ['dark-colour-blind', 'dark', true, undefined, true],
  ['light-colour-blind', 'light', true, undefined, true],
  ['high-contrast-dark', 'dark', false, 'high', true],
  ['high-contrast-light', 'light', false, 'high', true],
  ['high-contrast-dark-colour-blind', 'dark', true, 'high', false],
  ['high-contrast-light-colour-blind', 'light', true, 'high', false],
] as const) {
  test(`appearance ${name} @visual`, async ({ page }) => {
    await openApp(page, {
      ...withOutputs,
      settings: {
        ...withOutputs.settings,
        appearance,
        colour_blind_palette: colourBlind,
        ...(contrast ? { contrast } : {}),
      },
    });
    await expect(page.locator('html')).toHaveAttribute('data-theme', appearance);
    if (contrast) await expect(page.locator('html')).toHaveAttribute('data-contrast', contrast);
    await page.evaluate(() => document.fonts.ready);
    if (allShots) {
      await expect(page).toHaveScreenshot(`theme-${name}-main.png`);
      await showPane(page, 'enhance');
      await expect(page).toHaveScreenshot(`theme-${name}-enhance.png`);
      await showPane(page, 'media');
      await page.getByRole('button', { name: 'Clear', exact: true }).click();
      await expect(page.getByRole('dialog')).toBeVisible();
      await expect(page).toHaveScreenshot(`theme-${name}-dialog.png`);
    } else {
      await showPane(page, 'enhance');
      await expect(page).toHaveScreenshot(`theme-${name}-enhance.png`);
    }
  });
}

test('system contrast follows prefers-contrast without a reload when contrast is omitted', async ({
  page,
}) => {
  await openApp(page, {
    ...withOutputs,
    settings: { ...withOutputs.settings, contrast: undefined },
  });
  await expect(page.locator('html')).toHaveAttribute('data-contrast', 'standard');
  await page.emulateMedia({ contrast: 'more' });
  await expect(page.locator('html')).toHaveAttribute('data-contrast', 'high');
  await page.emulateMedia({ contrast: 'no-preference' });
  await expect(page.locator('html')).toHaveAttribute('data-contrast', 'standard');
});

test('stored standard ignores system prefers-contrast', async ({ page }) => {
  await page.emulateMedia({ contrast: 'more' });
  await openApp(page, {
    ...withOutputs,
    settings: { ...withOutputs.settings, contrast: 'standard' },
  });
  await expect(page.locator('html')).toHaveAttribute('data-contrast', 'standard');
});

test('explicit high contrast stays high with nothing emulated', async ({ page }) => {
  await openApp(page, {
    ...withOutputs,
    settings: { ...withOutputs.settings, contrast: 'high' },
  });
  await expect(page.locator('html')).toHaveAttribute('data-contrast', 'high');
});

test('forced-colors activates high contrast and keeps switches and canvas readable @visual', async ({
  page,
}, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('chromium'), 'forced-colors is Chromium-only');
  await page.emulateMedia({ forcedColors: 'active' });
  await openApp(page, {
    ...withOutputs,
    settings: { ...withOutputs.settings, contrast: undefined },
  });
  await expect(page.locator('html')).toHaveAttribute('data-contrast', 'high');
  expect(
    await page.locator('.canvas-well').evaluate((el) => getComputedStyle(el).forcedColorAdjust),
  ).toBe('none');

  await emit(page, 'native-menu-action', 'app.settings');
  const settingsDialog = page.getByRole('dialog', { name: 'Settings' });
  await expect(settingsDialog).toBeVisible();
  const checkedBg = await settingsDialog
    .locator('input.switch:checked')
    .first()
    .evaluate((el) => getComputedStyle(el).backgroundColor);
  const uncheckedBg = await settingsDialog
    .locator('input.switch:not(:checked)')
    .first()
    .evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(checkedBg).not.toBe(uncheckedBg);
  await page.keyboard.press('Escape');
  await expect(settingsDialog).toHaveCount(0);

  await showPane(page, 'enhance');
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('forced-colors-enhance.png');
});

for (const appearance of ['dark', 'light'] as const) {
  test(`high contrast ${appearance} keeps measured contrast >= 7`, async ({ page }) => {
    await openApp(page, {
      ...ready,
      settings: { ...ready.settings, appearance, contrast: 'high' },
    });

    await showPane(page, 'media');
    expect(
      await contrastOnPage(page.locator('.media-row.selected .media-copy > span')),
    ).toBeGreaterThanOrEqual(7);

    await showPane(page, 'preview');
    await page.locator('.preview-pane .canvas-well').hover();
    await expect(page.locator('.preview-pane .zoom-hud')).toHaveCSS('opacity', '1');
    expect(
      await contrastOnPage(page.locator('.preview-pane .zoom-hud span')),
    ).toBeGreaterThanOrEqual(7);

    await showPane(page, 'enhance');
    expect(await contrastOnPage(page.locator('.fix-row .fix-name').first())).toBeGreaterThanOrEqual(
      7,
    );
    expect(
      await contrastOnPage(page.getByRole('button', { name: 'Upscale selected' })),
    ).toBeGreaterThanOrEqual(7);

    await page.locator('.foot-plan').click();
    expect(await contrastOnPage(page.locator('.plan-row .stage').first())).toBeGreaterThanOrEqual(
      7,
    );
    await page.getByRole('button', { name: 'Change the upscaler' }).click();
    const library = page.getByRole('dialog', { name: 'Model library' });
    await expect(library).toBeVisible();
    expect(await contrastOnPage(library.locator('.pill.quick').first())).toBeGreaterThanOrEqual(7);
    await page.keyboard.press('Escape');

    // Measure .plan-row .state using a custom model slot that needs a file.
    await openApp(page, {
      ...ready,
      settings: {
        ...ready.settings,
        appearance,
        contrast: 'high',
        selected_model_id: '__custom__',
        custom_model_path: '',
      },
    });
    await showPane(page, 'enhance');
    await page.locator('.foot-plan').click();
    const state = page.locator('.plan-row .state').first();
    await expect(state).toContainText('Needs your file');
    expect(await contrastOnPage(state)).toBeGreaterThanOrEqual(7);
  });
}

test('settings appearance @visual', async ({ page }) => {
  await openApp(page, withOutputs);
  await emit(page, 'native-menu-action', 'app.settings');
  const dialog = page.getByRole('dialog', { name: 'Settings' });
  await dialog.getByRole('tab', { name: 'Appearance' }).click();
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('settings-appearance.png');
});

test('settings appearance high contrast light @visual', async ({ page }) => {
  await openApp(page, {
    ...withOutputs,
    settings: { ...withOutputs.settings, appearance: 'light', contrast: 'high' },
  });
  await emit(page, 'native-menu-action', 'app.settings');
  const dialog = page.getByRole('dialog', { name: 'Settings' });
  await dialog.getByRole('tab', { name: 'Appearance' }).click();
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('settings-appearance-high-contrast-light.png');
});
