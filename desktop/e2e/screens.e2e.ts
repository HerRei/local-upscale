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
    await page.getByRole('button', { name: 'Not now' }).click();
    await shoot(page, 'panel-photo');
  });

  test('plan with two fixes in front of the upscaler', async ({ page }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByRole('checkbox', { name: 'Noise', exact: true }).check();
    await page.getByRole('checkbox', { name: 'Blur', exact: true }).check();
    await expect(page.locator('.foot-plan')).toContainText('Blur');
    await shoot(page, 'panel-fixes');
    await page
      .getByRole('checkbox', { name: 'Noise', exact: true })
      .locator('../..')
      .getByRole('button')
      .click();
    await shoot(page, 'panel-popover');
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
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.locator('.foot-plan').click();
    await page.locator('#device').scrollIntoViewIfNeeded();
    await shoot(page, 'panel-plan-sheet-mac');
  });

  test('Advanced settings with two GPU routes', async ({ page }) => {
    await openApp(page, { ...windows, worker: 'ready' });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.locator('.foot-plan').click();
    await page.locator('#device').scrollIntoViewIfNeeded();
    await shoot(page, 'panel-plan-sheet-windows');
  });

  test('Advanced settings for video with interpolation', async ({ page }) => {
    await openApp(page, video);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await shoot(page, 'panel-video');
    await page.locator('.foot-output').click();
    await shoot(page, 'panel-output-sheet-video');
  });

  test('Edit Expert settings on Windows CUDA', async ({ page }) => {
    await openApp(page, {
      devices: 'cuda',
      platform: 'Win32',
      photos: 1,
      task: 'edit',
      worker: 'ready',
      editRuntime: '/fixture/sd-cli',
      features: ['image_edit', 'edit_placement', 'edit_expert'],
      installed: ['flux2_klein_4b_q4_0'],
      settings: {
        edit_model_id: 'flux2_klein_4b_q4_0',
        edit_max_dimension: 1024,
        edit_vram_limit_mb: 11264,
        edit_allow_any_size: true,
      },
    });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByRole('button', { name: /^Advanced/ }).click();
    await page.locator('#edit-expert').click();
    await page.locator('#edit-expert-reset').scrollIntoViewIfNeeded();
    await shoot(page, 'edit-expert-windows');
  });
});
