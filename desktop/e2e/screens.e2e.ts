import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { firstRun, ready, video, windows, withOutputs } from './fixtures';

// Pixel baselines of the key screens, one set per browser, window size and platform
// (see snapshotPathTemplate): they catch layout and styling regressions that the
// behaviour tests cannot see. Update them with `npm run e2e:update` after a
// deliberate visual change and review the images in the diff.

async function settle(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map((image) => image.decode().catch(() => {})));
  });
}

async function shoot(page: Page, name: string): Promise<void> {
  await settle(page);
  await expect(page).toHaveScreenshot(`${name}.png`);
}

const outputsTab = (page: Page) => page.getByRole('tab', { name: /^Outputs/ });
const pick = (page: Page, label: string) =>
  page.getByRole('checkbox', { name: `${label}, Alpine lake 1.jpg` }).check();

test.describe('screens @visual', () => {
  test('first run with the welcome note', async ({ page }) => {
    await openApp(page, firstRun);
    await shoot(page, 'first-run');
  });

  test('plan for a Quick upscale', async ({ page }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
    await shoot(page, 'plan-quick');
  });

  test('plan with two fixes in front of the upscaler', async ({ page }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Noise', exact: true }).click();
    await page.getByRole('button', { name: 'Blur', exact: true }).click();
    await expect(page.getByRole('list', { name: 'Stages' }).getByRole('listitem')).toHaveCount(3);
    await shoot(page, 'plan-fixes');
  });

  test('Outputs tab with two ticked results', async ({ page }) => {
    await openApp(page, withOutputs);
    await outputsTab(page).click();
    await pick(page, 'Original');
    await pick(page, 'HAT-S ×4');
    await expect(page.getByText('2 of 4 chosen')).toBeVisible();
    await shoot(page, 'outputs-picked');
  });

  test('Compare with the slider', async ({ page }) => {
    await openApp(page, withOutputs);
    await outputsTab(page).click();
    await pick(page, 'Original');
    await pick(page, 'HAT-S ×4');
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    await expect(page.getByRole('slider')).toBeVisible();
    await shoot(page, 'compare-slider');
  });

  test('Compare with four boxes', async ({ page }) => {
    await openApp(page, withOutputs);
    await outputsTab(page).click();
    for (const label of ['Original', 'SPAN ×4', 'HAT-S ×4', 'RealESRGAN ×4'])
      await pick(page, label);
    await page.getByRole('button', { name: 'Compare these 4' }).click();
    await expect(
      page.getByRole('list', { name: 'Compared items' }).getByRole('listitem'),
    ).toHaveCount(4);
    await shoot(page, 'compare-boxes');
  });

  test('Advanced settings on a Mac', async ({ page }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: /^Advanced/ }).click();
    await page.locator('#device').scrollIntoViewIfNeeded();
    await shoot(page, 'advanced-mac');
  });

  test('Advanced settings with two GPU routes', async ({ page }) => {
    await openApp(page, { ...windows, worker: 'ready' });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: /^Advanced/ }).click();
    await page.locator('#device').scrollIntoViewIfNeeded();
    await shoot(page, 'advanced-windows');
  });

  test('Advanced settings for video with interpolation', async ({ page }) => {
    await openApp(page, video);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: /^Advanced/ }).click();
    await page.locator('#frame-rate').scrollIntoViewIfNeeded();
    await shoot(page, 'advanced-video');
  });
});
