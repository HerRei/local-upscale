import { expect, test, type Locator, type Page } from '@playwright/test';
import { openApp } from './app';
import { ready, withOutputs } from './fixtures';

// Geometry the pixel baselines only show indirectly: the pane headers form one line, and the
// centre header keeps its tabs, file name and view options in a single row.

/** Bottom of the rendered text of an element: equal bottoms mean one text line. */
const textBottom = (locator: Locator) =>
  locator.evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    return range.getBoundingClientRect().bottom;
  });

const box = async (locator: Locator) => (await locator.boundingBox())!;

async function centreHeader(page: Page) {
  const header = page.locator('.preview-header');
  const tabs = header.getByRole('tablist', { name: 'Main pane' });
  return { header, tabs, preview: tabs.getByRole('tab', { name: 'Preview' }) };
}

test.describe('pane headers', () => {
  test('share one height and one text line in the wide layout', async ({ page }, info) => {
    test.skip(info.project.use.viewport!.width < 1280, 'only the wide layout shows all three');
    await openApp(page, ready);
    const { header, preview } = await centreHeader(page);
    const enhance = page.getByRole('heading', { name: 'Enhance' });
    const enhanceRow = page.locator('.enhance-pane > .pane-heading');
    expect((await box(header)).height).toBe((await box(enhanceRow)).height);
    expect(Math.abs((await textBottom(preview)) - (await textBottom(enhance)))).toBeLessThan(1);
  });

  test('keep the centre tabs, name and view options on one row', async ({ page }) => {
    await openApp(page, withOutputs);
    const { header, tabs } = await centreHeader(page);
    const inRow = async (parts: Locator[]) => {
      const row = await box(header);
      expect(row.height).toBe(52);
      let left = row.x;
      for (const part of parts) {
        const { x, y, height } = await box(part);
        expect(y).toBeGreaterThanOrEqual(row.y);
        expect(y + height).toBeLessThanOrEqual(row.y + row.height);
        expect(x).toBeGreaterThanOrEqual(left);
        left = x;
      }
    };
    await inRow([tabs, header.locator('.file-name')]);

    await page
      .getByRole('tablist', { name: 'Media' })
      .getByRole('tab', { name: /^Outputs/ })
      .click();
    for (const label of ['Original', 'HAT-S ×4'])
      await page.getByRole('checkbox', { name: `${label}, Alpine lake 1.jpg` }).check();
    await tabs.getByRole('tab', { name: 'Compare' }).click();
    const views = header.getByRole('radiogroup', { name: 'Compare layout' });
    await expect(views).toBeVisible();
    await inRow([tabs, header.locator('.compare-names'), views]);
  });
});
