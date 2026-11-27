import { expect, test, type Locator, type Page } from '@playwright/test';
import { contrastOnPage, openApp, showPane } from './app';
import { emit, failDownload, finishDownload, hostCalls, progressDownload } from './fake-host';
import { ready, upscale, withOutputs } from './fixtures';

const EDIT_SCENARIO = {
  ...upscale,
  task: 'edit',
  worker: 'ready' as const,
  features: ['image_edit'],
  editRuntime: '/fixture/sd-cli',
  settings: { edit_model_id: 'flux2_klein_4b_q4_0' },
  installed: [] as string[],
  concurrentDownloads: true,
};

const fix = (page: Page, name: string) =>
  page.getByRole('group', { name: 'Fix' }).getByRole('checkbox', { name, exact: true });

const box = async (locator: Locator) => (await locator.boundingBox())!;

async function prepareEditDownload(page: Page): Promise<void> {
  await showPane(page, 'enhance');
  await page.getByLabel('What to change').fill('Add snow');
  await page.getByRole('button', { name: 'Download 5.3 GB, then edit' }).click();
}

test.describe('downloads', () => {
  test('Edit download shows once in the status bar, tracks speed/time, handles silence and verifying, and starts on finish', async ({
    page,
  }) => {
    await page.clock.install();
    await openApp(page, EDIT_SCENARIO);
    await prepareEditDownload(page);

    await progressDownload(page, 'flux2_klein_4b_q4_0', 9);

    await expect(page.getByRole('progressbar')).toHaveCount(1);
    await expect(page.getByRole('button', { name: /Cancel/ })).toHaveCount(1);
    const bar = page.locator('.status-bar');
    await expect(bar).toContainText('Downloading FLUX.2 klein 4B · 9%');
    await expect(page.locator('body')).not.toContainText('flux2_');
    await expect(page.locator('body')).not.toContainText(/checksum/i);
    const nineMatches = await page.evaluate(
      () => document.body.innerText.match(/9%/g)?.length ?? 0,
    );
    expect(nineMatches).toBe(1);

    // 5 s of steady progress measures speed and prints time left.
    for (let pct = 10; pct <= 14; pct += 1) {
      await page.clock.runFor(1_000);
      await progressDownload(page, 'flux2_klein_4b_q4_0', pct);
    }
    await expect(page.locator('.status-detail')).toHaveText(
      /of 5\.3 GB · about \d+ min left · then edit$/,
    );

    // 9 s without progress replaces time left with connection-waiting wording.
    await page.clock.runFor(9_000);
    await expect(page.locator('.status-detail')).toContainText('Waiting for the connection…');

    // Verifying phase shows after 1 s with a full strip.
    await progressDownload(page, 'flux2_klein_4b_q4_0', 100, { phase: 'verifying' });
    await page.clock.runFor(2_000);
    await expect(page.locator('.status-detail')).toHaveText('Verifying… · then edit');
    await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    const strip = await box(page.locator('.status-bar .progress'));
    const fill = await box(page.locator('.status-bar .progress i'));
    expect(Math.abs(fill.width - strip.width)).toBeLessThanOrEqual(0.5);

    // Finishing starts the edit job.
    await finishDownload(page, 'flux2_klein_4b_q4_0');
    await expect.poll(async () => (await hostCalls(page, 'start_edit')).length).toBe(1);
  });

  for (const viewport of [
    { width: 1024, height: 700 },
    { width: 880, height: 640 },
  ]) {
    test(`Edit panel rows stay put and foot collapses while downloading at ${viewport.width} x ${viewport.height}`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await openApp(page, EDIT_SCENARIO);
      await showPane(page, 'enhance');
      await page.getByLabel('What to change').fill('Add snow');

      const rows = page.locator('.inspector-scroll .panel-row');
      const count = await rows.count();
      expect(count).toBeGreaterThan(0);
      const before = await Promise.all((await rows.all()).map(async (row) => (await box(row)).y));

      await page.getByRole('button', { name: 'Download 5.3 GB, then edit' }).click();
      await progressDownload(page, 'flux2_klein_4b_q4_0', 9);

      await expect(page.locator('.panel-foot')).toBeHidden();
      const after = await Promise.all((await rows.all()).map(async (row) => (await box(row)).y));
      after.forEach((y, index) => expect(Math.abs(y - before[index])).toBeLessThanOrEqual(0.5));
    });
  }

  test('several downloads show one combined line and keep percentage from dropping when one finishes', async ({
    page,
  }) => {
    await openApp(page, { ...ready, installed: [], concurrentDownloads: true });
    await showPane(page, 'enhance');
    await fix(page, 'Blur').check();
    await fix(page, 'Noise').check();

    await page.getByRole('button', { name: /^Download .*, then upscale$/ }).click();

    await expect(page.locator('.status-copy strong')).toContainText('Downloading 3 models');
    await expect(page.getByRole('progressbar')).toHaveCount(1);

    await progressDownload(page, 'nafnet_gopro_deblur', 80);
    await progressDownload(page, 'denoise_realplksr_1x', 12);
    await progressDownload(page, 'localsr_quick_x4_v1', 33);

    const bar = page.getByRole('progressbar');
    const before = Number(await bar.getAttribute('aria-valuenow'));
    expect(before).toBeGreaterThan(0);

    await finishDownload(page, 'nafnet_gopro_deblur');
    const after = Number(await bar.getAttribute('aria-valuenow'));
    expect(after).toBeGreaterThanOrEqual(before);
    await expect(page.locator('.status-copy strong')).toContainText('Downloading 3 models');
  });

  test('offline failure shows kept bytes in the status bar with no dialog and enabled retry button', async ({
    page,
  }) => {
    await openApp(page, EDIT_SCENARIO);
    await prepareEditDownload(page);

    await progressDownload(page, 'flux2_klein_4b_q4_0', 24);
    await failDownload(page, 'flux2_klein_4b_q4_0', 'network timeout', { reason: 'offline' });

    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.locator('.status-copy strong')).toHaveText('Download interrupted');
    await expect(page.locator('.status-detail')).toContainText('kept at 1.3 of 5.3 GB');
    const retry = page.getByRole('button', { name: 'Download 5.3 GB, then edit' });
    await expect(retry).toBeVisible();
    await expect(retry).toBeEnabled();
  });

  test('library download shows in the status bar after closing library and cancels cleanly', async ({
    page,
  }) => {
    await openApp(page, { ...ready, installed: [], concurrentDownloads: true });
    await emit(page, 'native-menu-action', 'view.models');
    const library = page.getByRole('dialog', { name: 'Model library' });
    await library.getByRole('button', { name: /^Upscale photos/ }).click();
    await library.getByRole('button', { name: /HAT-L ×4 ImageNet/ }).click();
    await library.getByRole('button', { name: /^Download / }).click();
    await progressDownload(page, 'hat_l_x4_imagenet', 25);

    await library.getByRole('button', { name: 'Close model library' }).click();
    await expect(library).toHaveCount(0);

    await expect(page.locator('.status-copy strong')).toContainText(
      'Downloading HAT-L ×4 ImageNet · 25%',
    );
    const cancel = page.getByRole('button', { name: 'Cancel download' });
    await expect(cancel).toBeVisible();
    await cancel.click();

    await expect(page.getByRole('progressbar')).toHaveCount(0);
    await expect(page.locator('.status-copy strong')).toHaveText('Download stopped');
  });

  test('880 x 640 with finished result and non-waiting download keeps buttons apart and inside the bar', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 880, height: 640 });
    await openApp(page, {
      ...withOutputs,
      worker: 'ready',
      finished: true,
      concurrentDownloads: true,
    });

    await emit(page, 'native-menu-action', 'view.models');
    const library = page.getByRole('dialog', { name: 'Model library' });
    await library.getByRole('button', { name: /^Upscale photos/ }).click();
    await library.getByRole('button', { name: /HAT-L ×4 ImageNet/ }).click();
    await library.getByRole('button', { name: /^Download / }).click();
    await progressDownload(page, 'hat_l_x4_imagenet', 35);
    await library.getByRole('button', { name: 'Close model library' }).click();

    const bar = page.locator('.status-bar');
    const buttons = bar.locator('.status-actions button');
    await expect(bar.getByRole('button', { name: 'Cancel download' })).toBeVisible();
    await expect(buttons.getByText('Only this one')).toBeVisible();
    await expect(bar.getByRole('button', { name: 'Reveal' })).toBeVisible();
    await expect(bar.getByRole('button', { name: 'Use as input' })).toBeVisible();

    const barBox = await box(bar);
    const text = await box(bar.locator('.status-copy'));
    const rects = [];
    for (const button of await buttons.all()) rects.push(await box(button));
    for (const [index, rect] of rects.entries()) {
      expect(rect.x).toBeGreaterThanOrEqual(barBox.x);
      expect(rect.x + rect.width).toBeLessThanOrEqual(barBox.x + barBox.width);
      expect(rect.y).toBeGreaterThanOrEqual(barBox.y);
      expect(rect.y + rect.height).toBeLessThanOrEqual(barBox.y + barBox.height + 0.5);
      if (index > 0) {
        const before = rects[index - 1];
        expect(rect.x).toBeGreaterThanOrEqual(before.x + before.width);
      }
      expect(text.x + text.width).toBeLessThanOrEqual(rects[0].x + 0.5);
    }
  });

  for (const mode of ['dark', 'light', 'forced-colors'] as const) {
    test(`status line and Cancel download keep readable contrast in ${mode}`, async ({
      page,
    }, info) => {
      test.skip(
        mode === 'forced-colors' && !info.project.name.startsWith('chromium'),
        'forced-colors is Chromium-only',
      );
      if (mode === 'forced-colors') {
        await page.emulateMedia({ forcedColors: 'active' });
      }
      await openApp(page, {
        ...EDIT_SCENARIO,
        settings: {
          ...EDIT_SCENARIO.settings,
          ...(mode === 'forced-colors' ? { contrast: undefined } : { appearance: mode }),
        },
      });
      await prepareEditDownload(page);
      await progressDownload(page, 'flux2_klein_4b_q4_0', 9);

      expect(await contrastOnPage(page.locator('.status-detail'))).toBeGreaterThanOrEqual(4.5);
      expect(
        await contrastOnPage(page.getByRole('button', { name: 'Cancel download' })),
      ).toBeGreaterThanOrEqual(4.5);
    });
  }
});
