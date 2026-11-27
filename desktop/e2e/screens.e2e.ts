import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { emit, failDownload, progressDownload } from './fake-host';
import { firstRun, ready, upscale, video, windows, withOutputs } from './fixtures';

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

  test('download waiting for Edit start', async ({ page }) => {
    await page.clock.install();
    await openApp(page, {
      ...upscale,
      task: 'edit',
      worker: 'ready',
      features: ['image_edit'],
      editRuntime: '/fixture/sd-cli',
      settings: { edit_model_id: 'flux2_klein_4b_q4_0' },
      installed: [],
      concurrentDownloads: true,
    });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByLabel('What to change').fill('Add snow');
    await page.getByRole('button', { name: 'Download 5.3 GB, then edit' }).click();
    for (let pct = 5; pct <= 9; pct += 1) {
      await progressDownload(page, 'flux2_klein_4b_q4_0', pct);
      if (pct < 9) await page.clock.runFor(2_000);
    }
    await expect(page.locator('.status-detail')).toContainText('then edit');
    await shoot(page, 'download-waiting');
  });

  test('several downloads running for Blur and Noise', async ({ page }) => {
    await page.clock.install();
    await openApp(page, { ...ready, installed: [], concurrentDownloads: true });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByRole('checkbox', { name: 'Blur', exact: true }).check();
    await page.getByRole('checkbox', { name: 'Noise', exact: true }).check();
    await page.getByRole('button', { name: /^Download .*, then upscale$/ }).click();
    await progressDownload(page, 'nafnet_gopro_deblur', 80);
    await progressDownload(page, 'denoise_realplksr_1x', 12);
    await progressDownload(page, 'localsr_quick_x4_v1', 33);
    await expect(page.locator('.status-copy strong')).toContainText('Downloading 3 models');
    await shoot(page, 'download-several');
  });

  test('download interrupted in the status bar', async ({ page }) => {
    await openApp(page, {
      ...upscale,
      task: 'edit',
      worker: 'ready',
      features: ['image_edit'],
      editRuntime: '/fixture/sd-cli',
      settings: { edit_model_id: 'flux2_klein_4b_q4_0' },
      installed: [],
      concurrentDownloads: true,
    });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByLabel('What to change').fill('Add snow');
    await page.getByRole('button', { name: 'Download 5.3 GB, then edit' }).click();
    await progressDownload(page, 'flux2_klein_4b_q4_0', 24);
    await failDownload(page, 'flux2_klein_4b_q4_0', 'network timeout', { reason: 'offline' });
    await expect(page.locator('.status-copy strong')).toHaveText('Download interrupted');
    await shoot(page, 'download-failed');
  });

  test('library downloads tray with active, waiting and failed items', async ({ page }) => {
    await page.clock.install();
    await openApp(page, { ...ready, installed: [], concurrentDownloads: true });
    await emit(page, 'native-menu-action', 'view.models');
    const library = page.getByRole('dialog', { name: 'Model library' });
    await library.getByRole('button', { name: /^Upscale photos/ }).click();
    for (const label of [
      /HAT-L ×4 ImageNet/,
      /SwinIR ×4 Real/,
      /HAT-S ×4/,
      /Real-ESRGAN ×4/,
      /RealPLKSR ×4 NomosWebPhoto/,
    ]) {
      await library.getByRole('button', { name: label }).click();
      await library.getByRole('button', { name: /^Download / }).click();
    }
    for (let pct = 5; pct <= 10; pct += 1) {
      await progressDownload(page, 'hat_l_x4_imagenet', pct);
      if (pct < 10) await page.clock.runFor(2_000);
    }
    await progressDownload(page, 'swinir_m_real_x4_gan', 30);
    await failDownload(page, 'swinir_m_real_x4_gan', 'network timeout', { reason: 'offline' });
    await progressDownload(page, 'hat_s_x4', 45);
    await expect(library.getByRole('region', { name: 'Model downloads' })).toContainText(
      'Downloads · 3 active · 1 waiting',
    );
    await shoot(page, 'library-downloads');
  });
});
