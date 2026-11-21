import { expect, test } from '@playwright/test';
import { openApp, showPane } from './app';
import { withOutputs } from './fixtures';

const shots = process.env.SHOTS_DIR;

test.describe('showing a result from the Outputs list', () => {
  test('keeps the picture with a loading note while the host prepares the pair, then splits', async ({
    page,
  }, info) => {
    await openApp(page, { ...withOutputs, worker: 'ready', pairs: true, pairDelayMs: 1000 });
    await showPane(page, 'media');
    await page
      .getByRole('tablist', { name: 'Media' })
      .getByRole('tab', { name: /^Outputs/ })
      .click();
    await page.getByRole('button', { name: 'Show the original, Alpine lake 1.jpg' }).click();
    await expect(page.getByRole('radiogroup', { name: 'Comparison view' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Show SPAN ×4, Alpine lake 1.jpg' }).click();
    await expect(page.getByText('Loading SPAN ×4…')).toBeVisible();
    await expect(page.getByRole('slider', { name: 'Before and after comparison' })).toHaveCount(0);
    await expect(page.getByRole('radiogroup', { name: 'Comparison view' })).toHaveCount(0);
    await expect(page.locator('.side-placeholder')).toHaveCount(0);
    if (shots) await page.screenshot({ path: `${shots}/loading-${info.project.name}.png` });
    const views = page.getByRole('radiogroup', { name: 'Comparison view' });
    await expect(views).toBeVisible({ timeout: 5000 });
    await expect(views.getByRole('radio')).toHaveText(['Original', 'Split', 'Enhanced']);
    await expect(page.getByText('Loading SPAN ×4…')).toHaveCount(0);
    if (shots) await page.screenshot({ path: `${shots}/result-${info.project.name}.png` });
  });
});
