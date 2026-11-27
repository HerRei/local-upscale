import { expect, test, type Locator } from '@playwright/test';
import { openApp, showPane } from './app';
import { hostCalls } from './fake-host';
import { ready } from './fixtures';

async function box(locator: Locator) {
  const rect = await locator.boundingBox();
  if (!rect) throw new Error('missing element');
  return rect;
}

test.describe('main preview zoom detail', () => {
  test('requests and draws aligned full-resolution crops for both sides when zoomed into the split', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1380, height: 860 });
    await openApp(page, {
      ...ready,
      photos: 1,
      pairs: true,
      finished: true,
      features: ['image_multi_fix', 'video_interpolation', 'compare_regions', 'preview_regions'],
      outputs: {
        1: [
          { id: 'span', label: 'SPAN ×4', quality: 'Quick', seconds: 9 },
          { id: 'hat', label: 'HAT-S ×4', quality: 'Best', seconds: 42 },
        ],
      },
    });
    await showPane(page, 'preview');
    await expect(page.locator('.result-image')).toBeVisible();
    await page.evaluate(async () => {
      await Promise.all([...document.images].map((image) => image.decode().catch(() => {})));
    });

    // At Fit zoom, no region crop is requested or drawn.
    expect(await hostCalls(page, 'request_preview_regions')).toHaveLength(0);
    await expect(page.locator('.region-image')).toHaveCount(0);

    // Zoom past the preview's 1600 px width (e.g. 400% -> ~3100 px on stage).
    const plus = page.getByRole('button', { name: 'Zoom in' });
    for (let i = 0; i < 6; i++) {
      await plus.click({ force: true });
    }

    await expect
      .poll(async () => (await hostCalls(page, 'request_preview_regions')).length)
      .toBeGreaterThan(0);

    const lastCall = (await hostCalls(page, 'request_preview_regions')).at(-1)?.args as {
      regions: { original: unknown; showing: unknown; against: unknown };
    };
    expect(lastCall.regions.original).toBeTruthy();
    expect(lastCall.regions.showing).toBeTruthy();
    expect(lastCall.regions.against).toBeNull();

    // Both Original (.source-image) and Showing (.result-image inside .region-overlay) draw crops.
    const crops = page.locator('.image-stage .region-image');
    await expect(crops).toHaveCount(2);
    const originalCrop = crops.nth(0);
    const showingCrop = crops.nth(1);
    await expect(originalCrop).toHaveAttribute('src', /^data:image\//);
    await expect(showingCrop).toHaveAttribute('src', /^data:image\//);

    // Both crops cover the same aligned region within one source pixel of integer rounding,
    // and the showing crop overlay shares the divider clip-path with .result-image.
    const origBox = await box(originalCrop);
    const showBox = await box(showingCrop);
    for (const key of ['x', 'y', 'width', 'height'] as const) {
      expect(Math.abs(showBox[key] - origBox[key])).toBeLessThanOrEqual(3);
    }
    const resultClip = await page
      .locator('.result-image')
      .evaluate((el) => getComputedStyle(el).clipPath);
    const overlayClip = await page
      .locator('.region-overlay')
      .evaluate((el) => getComputedStyle(el).clipPath);
    expect(overlayClip).toBe(resultClip);

    // Zooming back to Fit clears the crops.
    await page.getByRole('button', { name: 'Fit', exact: true }).click({ force: true });
    await expect(page.locator('.region-image')).toHaveCount(0);
  });
});
