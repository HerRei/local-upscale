import { expect, test, type Locator, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { emit } from './fake-host';
import { ready, video, withOutputs } from './fixtures';

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
    const inputs = page
      .getByRole('tablist', { name: 'Media' })
      .getByRole('tab', { name: /^Inputs/ });
    const mediaRow = page.locator('.media-pane > .pane-heading');
    expect((await box(mediaRow)).height).toBe((await box(enhanceRow)).height);
    expect(Math.abs((await textBottom(inputs)) - (await textBottom(enhance)))).toBeLessThan(1);
  });

  test('start the Media tabs where its title was, after the window buttons on a Mac', async ({
    page,
  }, info) => {
    await openApp(page, ready);
    const inputs = page
      .getByRole('tablist', { name: 'Media' })
      .getByRole('tab', { name: /^Inputs/ });
    const left = await inputs.evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      return range.getBoundingClientRect().left;
    });
    const mac = await page.evaluate(() => /Mac/.test(navigator.platform));
    const wide = info.project.use.viewport!.width >= 1280;
    expect(Math.round(left)).toBe(mac && wide ? 84 : 16);
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

/** WCAG contrast of an element's text against its own background laid over its parent's. */
const contrast = (locator: Locator) =>
  locator.evaluate((element) => {
    const rgba = (value: string) => (value.match(/[\d.]+/g) ?? []).map(Number);
    const own = rgba(getComputedStyle(element).backgroundColor);
    const under = rgba(getComputedStyle(element.closest('.toolbar')!).backgroundColor);
    const alpha = own[3] ?? 1;
    const back = under.slice(0, 3).map((c, i) => own[i] * alpha + c * (1 - alpha));
    const fore = rgba(getComputedStyle(element).color);
    const lum = (rgb: number[]) =>
      rgb
        .map((c) => c / 255)
        .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
        .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
    const [hi, lo] = [lum(fore), lum(back)].sort((a, b) => b - a);
    return (hi + 0.05) / (lo + 0.05);
  });

test('the narrow window switch is a row of words with a readable count', async ({ page }, info) => {
  test.skip(info.project.use.viewport!.width >= 1280, 'the switch shows only in narrow windows');
  await openApp(page, ready);
  const nav = page.getByRole('navigation', { name: 'Workspace' });
  await expect(nav.getByRole('button', { name: 'Viewer', exact: true })).toBeVisible();
  await expect(nav.getByRole('button', { name: 'Preview' })).toHaveCount(0);
  const boxed = await nav.evaluate((element) => {
    const style = getComputedStyle(element);
    return style.borderTopStyle !== 'none' || style.backgroundColor !== 'rgba(0, 0, 0, 0)';
  });
  expect(boxed).toBe(false);
  // The Media page button only shows below 920 px; its colours apply at every width.
  expect(await contrast(nav.locator('button').first().locator('span'))).toBeGreaterThanOrEqual(4.5);
});

test('the Process choice is one line inside the Media pane', async ({ page }) => {
  await openApp(page, { ...ready, photos: 3 });
  await showPane(page, 'media');
  const line = page.locator('.process-scope');
  await expect(line.getByRole('button', { name: 'Selected' })).toBeVisible();
  await expect(line.getByRole('button', { name: 'All 3' })).toBeVisible();
  expect((await box(line)).height).toBeLessThanOrEqual(30);
  const overflow = await page
    .locator('.media-pane')
    .evaluate((pane) => pane.scrollWidth - pane.clientWidth);
  expect(overflow).toBe(0);
});

test('the welcome note sits above the docked update notice', async ({ page }) => {
  await openApp(page, { ...withOutputs, welcome: true, update: '0.3.9' });
  const notice = page.getByRole('status').filter({ hasText: 'LocalSR 0.3.9 is available' });
  await expect(notice).toBeVisible();
  const welcome = page.locator('.welcome-notice');
  await expect(welcome).toBeVisible();
  const card = await box(welcome);
  expect(card.y + card.height).toBeLessThanOrEqual((await box(notice)).y);
});

test('the update notice docks at the bottom right of the centre pane', async ({ page }) => {
  await openApp(page, { ...withOutputs, update: '0.3.9' });
  const notice = page.getByRole('status').filter({ hasText: 'LocalSR 0.3.9 is available' });
  await expect(notice).toBeVisible();
  const card = await box(notice);
  const bar = await box(page.locator('.status-bar'));
  const centre = await box(page.locator('.main-area'));
  expect(Math.round(bar.y - (card.y + card.height))).toBe(12);
  expect(Math.round(centre.x + centre.width - (card.x + card.width))).toBe(12);
  // Clear of the header row with its tabs and view options.
  const header = await box(page.locator('.preview-header'));
  expect(card.y).toBeGreaterThan(header.y + header.height);
});

test.describe('overflow and alignment', () => {
  const left = async (locator: Locator) => (await box(locator)).x;
  const right = async (locator: Locator) => {
    const b = await box(locator);
    return b.x + b.width;
  };

  test('a long unreadable name keeps Preview unavailable inside the stage, centred', async ({
    page,
  }) => {
    const name = `${'a'.repeat(176)}.jpeg`;
    await openApp(page, {
      ...ready,
      unreadable: { name, error: `Cannot open ${name}: the file ends unexpectedly.` },
    });
    await showPane(page, 'preview');
    const stage = await box(page.locator('.canvas-well'));
    const block = page.locator('.preview-error');
    await expect(block).toBeVisible();
    const b = await box(block);
    expect(b.x).toBeGreaterThanOrEqual(stage.x);
    expect(b.x + b.width).toBeLessThanOrEqual(stage.x + stage.width);
    expect(Math.abs(b.x + b.width / 2 - (stage.x + stage.width / 2))).toBeLessThan(2);
    const art = await box(block.locator('svg').first());
    expect(Math.abs(art.x + art.width / 2 - (stage.x + stage.width / 2))).toBeLessThan(2);
  });

  test('the Try others tip wraps inside its box', async ({ page }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'About Try others' }).click();
    const tip = page.getByRole('tooltip').filter({ visible: true });
    const sizes = await tip.evaluate((node) => [node.scrollWidth, node.clientWidth]);
    expect(sizes[0]).toBeLessThanOrEqual(sizes[1]);
  });

  test('Advanced: three selects share the select column; Find installed FFmpeg is one line', async ({
    page,
  }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: /^Advanced/ }).click();
    const column = page.locator('#device');
    await column.scrollIntoViewIfNeeded();
    expect(Math.abs((await left(page.locator('#halo-size'))) - (await left(column)))).toBeLessThan(
      0.5,
    );
    expect(
      Math.abs((await right(page.locator('#precision'))) - (await right(column))),
    ).toBeLessThan(0.5);

    await openApp(page, video);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: /^Advanced/ }).click();
    const find = page.getByRole('button', { name: 'Find installed FFmpeg' });
    await find.scrollIntoViewIfNeeded();
    const choose = find.locator('xpath=following-sibling::button[1]');
    await expect(choose).toHaveText('Choose…');
    expect(Math.abs((await box(find)).height - (await box(choose)).height)).toBeLessThan(2);
  });

  test('Edit: Steps and Seed look like the other inputs', async ({ page }) => {
    await openApp(page, { ...ready, task: 'edit' });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: /^Advanced/ }).click();
    const steps = page.getByRole('spinbutton', { name: 'Steps' });
    await steps.scrollIntoViewIfNeeded();
    const look = (locator: Locator) =>
      locator.evaluate((node) => {
        const style = getComputedStyle(node);
        return [style.borderRadius, style.backgroundColor, style.fontSize, style.minHeight];
      });
    // The values the other inputs and selects of Advanced use (styles.css).
    const field = ['2px', 'rgb(20, 20, 20)', '12px', '34px'];
    expect(await look(steps)).toEqual(field);
    expect(await look(page.getByRole('spinbutton', { name: 'Seed' }))).toEqual(field);
  });

  test('at 880 px the Enhance page is a centred column as wide as Media', async ({ page }) => {
    await page.setViewportSize({ width: 880, height: 640 });
    await openApp(page, ready);
    await showPane(page, 'enhance');
    const task = await box(page.getByRole('group', { name: 'Task' }));
    expect(task.width).toBeLessThanOrEqual(440);
    expect(Math.abs(task.x + task.width / 2 - 440)).toBeLessThan(2);
  });

  test('at 880 x 640 the library keeps its Download button in view', async ({ page }) => {
    await page.setViewportSize({ width: 880, height: 640 });
    await openApp(page, ready);
    await emit(page, 'native-menu-action', 'view.models');
    const library = page.getByRole('dialog', { name: 'Model library' });
    await library.getByRole('button', { name: /HAT-L ×4 ImageNet/ }).click();
    const download = library.getByRole('button', { name: /^Download/ });
    const b = await box(download);
    expect(b.y + b.height).toBeLessThanOrEqual(640);
  });
});
