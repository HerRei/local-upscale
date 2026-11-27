import { expect, test } from '@playwright/test';
import { contrastOnPage, openApp } from './app';
import { ready } from './fixtures';

const YELLOW = { light: '#ffc726', dark: '#ffc600' };

test('system accent on macOS sets yellow accent, black text, inset edge, and >= 4.5 contrast', async ({
  page,
}) => {
  await openApp(page, {
    ...ready,
    systemAccent: YELLOW,
    settings: { ...ready.settings, appearance: 'light', accent: 'system' },
  });

  const start = page.getByRole('button', { name: 'Upscale selected' });
  await expect(start).toHaveCSS('background-color', 'rgb(255, 199, 38)');
  await expect(start).toHaveCSS('color', 'rgb(0, 0, 0)');
  const boxShadow = await start.evaluate((el) => getComputedStyle(el).boxShadow);
  expect(boxShadow).toContain('inset');
  expect(boxShadow).toContain('rgb(178, 139, 27)');
  expect(await contrastOnPage(start)).toBeGreaterThanOrEqual(4.5);
});

test('colour-blind palette and high contrast keep the stylesheet accent over the system accent', async ({
  page,
}) => {
  await openApp(page, {
    ...ready,
    systemAccent: YELLOW,
    settings: {
      ...ready.settings,
      appearance: 'light',
      accent: 'system',
      colour_blind_palette: true,
    },
  });
  const startCb = page.getByRole('button', { name: 'Upscale selected' });
  await expect(startCb).toHaveCSS('background-color', 'rgb(58, 110, 165)');
  await expect(startCb).toHaveCSS('color', 'rgb(255, 255, 255)');

  await openApp(page, {
    ...ready,
    systemAccent: YELLOW,
    settings: {
      ...ready.settings,
      appearance: 'light',
      accent: 'system',
      contrast: 'high',
    },
  });
  const startHc = page.getByRole('button', { name: 'Upscale selected' });
  await expect(startHc).toHaveCSS('background-color', 'rgb(36, 74, 117)');
  await expect(startHc).toHaveCSS('color', 'rgb(255, 255, 255)');
});

test('accent localsr keeps LocalSR blue with white text', async ({ page }) => {
  await openApp(page, {
    ...ready,
    systemAccent: YELLOW,
  });

  const start = page.getByRole('button', { name: 'Upscale selected' });
  await expect(start).toHaveCSS('background-color', 'rgb(58, 110, 165)');
  await expect(start).toHaveCSS('color', 'rgb(255, 255, 255)');
});
