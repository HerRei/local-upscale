import { expect, test, type Locator, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { withEdit } from './fixtures';

// An Edit result is saved at the engine's Edit size (512 x 320 for the fixture's 3:2
// photo), yet it gets the same before/after view as an upscale: one frame, the divider,
// Original | Split | Enhanced, hold M to peek, and 1:1 meaning the original's pixels.

const SHOTS = process.env.EDIT_COMPARE_SHOTS ?? '';

async function box(locator: Locator) {
  const rectangle = await locator.boundingBox();
  if (!rectangle) throw new Error('not on screen');
  return rectangle;
}

async function openEdit(page: Page, width: number, height: number) {
  await page.setViewportSize({ width, height });
  await openApp(page, withEdit);
  await showPane(page, 'preview');
  await expect(page.locator('.result-image')).toBeVisible();
  await page.evaluate(async () => {
    await Promise.all([...document.images].map((image) => image.decode().catch(() => {})));
  });
}

for (const [width, height] of [
  [1380, 860],
  [880, 800],
] as const) {
  test.describe(`edit result at ${width} px`, () => {
    test('opens in Split with the divider and the three views', async ({ page }, info) => {
      await openEdit(page, width, height);
      await expect(page.getByRole('slider', { name: 'Before and after comparison' })).toBeVisible();
      const views = page.getByRole('radiogroup', { name: 'Comparison view' });
      await expect(views.getByRole('radio')).toHaveText(['Original', 'Split', 'Enhanced']);
      await expect(views.getByRole('radio', { name: 'Split' })).toBeChecked();
      await expect(page.locator('.comparison-note')).toHaveCount(0);
      // The labels name the edit and give the size of its file.
      await expect(page.locator('.divider-labels')).toContainText('Qwen Edit · 512 × 320');
      if (SHOTS)
        await page.screenshot({ path: `${SHOTS}/edit-split-${width}-${info.project.name}.png` });
    });

    test('both sides fill the original frame, so the halves line up at the divider', async ({
      page,
    }, info) => {
      await openEdit(page, width, height);
      const original = await box(page.locator('.source-image'));
      const edited = await box(page.locator('.result-image'));
      for (const key of ['x', 'y', 'width', 'height'] as const)
        expect(edited[key]).toBeCloseTo(original[key], 0);
      // The frame has the original's 3:2 shape, not the edit's 512 x 320.
      expect(original.width / original.height).toBeCloseTo(1600 / 1067, 2);
      if (SHOTS)
        await page.screenshot({ path: `${SHOTS}/edit-frame-${width}-${info.project.name}.png` });
    });

    test('the switch shows one side, and holding M peeks at the original', async ({ page }) => {
      await openEdit(page, width, height);
      const views = page.getByRole('radiogroup', { name: 'Comparison view' });
      await views.getByRole('radio', { name: 'Original' }).click();
      await expect(page.locator('.result-image')).toHaveCSS('clip-path', /100%/);
      await views.getByRole('radio', { name: 'Enhanced' }).click();
      await expect(page.locator('.result-image')).not.toHaveCSS('clip-path', /100%/);
      await views.getByRole('radio', { name: 'Split' }).click();
      await page.keyboard.down('m');
      await expect(page.locator('.original-chip')).toHaveText('Original');
      await page.keyboard.up('m');
      await expect(page.locator('.original-chip')).toHaveCount(0);
    });

    test("1:1 is the original's pixels and Fit returns", async ({ page }) => {
      await openEdit(page, width, height);
      const stage = page.locator('.image-stage');
      const fitted = await box(stage);
      await page.getByRole('button', { name: '1:1', exact: true }).click();
      await expect.poll(async () => (await box(stage)).width).toBeGreaterThan(fitted.width + 1);
      expect((await box(stage)).width).toBeCloseTo(1600, -1);
      await page.getByRole('button', { name: 'Fit', exact: true }).click();
      await expect.poll(async () => (await box(stage)).width).toBeCloseTo(fitted.width, 0);
    });
  });
}
