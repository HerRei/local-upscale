import { expect, test, type Locator, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { withEdit, withOutputs } from './fixtures';

// At the bottom of the stage live the zoom HUD, the result's label chip, the Original's label
// chip, the preview note chip and the docked update notice. None of them may cover another.

const SIZES = [
  [880, 640],
  [1380, 860],
] as const;

async function boxes(page: Page) {
  const found: { name: string; box: { x: number; y: number; width: number; height: number } }[] =
    [];
  const parts: [string, Locator][] = [
    ['zoom HUD', page.locator('.zoom-hud')],
    ['compare label 1', page.locator('.original-chip').first()],
    ['compare label 2', page.locator('.original-chip').last()],
    ['original label', page.locator('.divider-labels span').first()],
    ['result label', page.locator('.divider-labels span').last()],
    ['note chip', page.locator('.comparison-note')],
    ['update notice', page.locator('.update-notice')],
  ];
  for (const [name, locator] of parts) {
    if ((await locator.count()) === 0 || !(await locator.first().isVisible())) continue;
    const box = await locator.first().boundingBox();
    if (box) found.push({ name, box });
  }
  return found;
}

async function expectNoOverlap(page: Page) {
  const found = await boxes(page);
  for (let i = 0; i < found.length; i++)
    for (let j = i + 1; j < found.length; j++) {
      const a = found[i].box;
      const b = found[j].box;
      const apart =
        a.x + a.width <= b.x ||
        b.x + b.width <= a.x ||
        a.y + a.height <= b.y ||
        b.y + b.height <= a.y;
      expect(apart, `${found[i].name} and ${found[j].name} overlap`).toBe(true);
    }
  return found.map((entry) => entry.name);
}

for (const [width, height] of SIZES)
  for (const [name, scenario] of [
    ['a result', { ...withEdit, outputs: { 1: [{ id: 'span', label: 'SPAN ×4' }] } }],
    ['an edit result', withEdit],
    ['a result with the update notice', { ...withEdit, update: '0.3.9' }],
  ] as const)
    test(`stage overlays do not overlap: ${name} at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await openApp(page, scenario);
      await showPane(page, 'preview');
      await expect(page.locator('.divider-labels')).toBeVisible();
      const canvas = page.getByRole('application', { name: 'Media comparison canvas' });
      await canvas.hover();
      await expect(page.locator('.zoom-hud')).toHaveCSS('opacity', '1');
      const names = await expectNoOverlap(page);
      expect(names).toContain('zoom HUD');
      expect(names).toContain('result label');
    });

const SHOTS = process.env.OVERLAP_SHOTS ?? '';

for (const [width, height] of SIZES)
  test(`a differently framed edit keeps its note chip clear of the zoom HUD at ${width} px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height });
    await openApp(page, {
      ...withEdit,
      outputs: { 1: [{ id: 'edit', label: 'Qwen Edit', edit: { width: 512, height: 512 } }] },
    });
    await showPane(page, 'preview');
    await expect(page.locator('.comparison-note')).toBeVisible();
    await page.getByRole('application', { name: 'Media comparison canvas' }).hover();
    await expect(page.locator('.zoom-hud')).toHaveCSS('opacity', '1');
    expect(await expectNoOverlap(page)).toContain('note chip');
  });

for (const [width, height] of SIZES)
  test(`Compare keeps its labels clear of the zoom HUD at ${width} px`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openApp(page, withOutputs);
    const toMedia = page
      .getByRole('navigation', { name: 'Workspace' })
      .getByRole('button', { name: /^Media/ });
    if (await toMedia.isVisible()) await toMedia.click();
    await page.getByRole('tab', { name: /^Outputs/ }).click();
    await page.getByRole('checkbox', { name: 'Original, Alpine lake 1.jpg' }).check();
    await page.getByRole('checkbox', { name: 'HAT-S ×4, Alpine lake 1.jpg' }).check();
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    await page.getByRole('slider', { name: /Divider between slot 1 and slot 2/ }).waitFor();
    await page.locator('.compare-pane').hover();
    await expect(page.locator('.zoom-hud')).toHaveCSS('opacity', '1');
    const names = await expectNoOverlap(page);
    expect(names).toContain('zoom HUD');
    expect(names).toContain('compare label 1');
  });

test('screenshot of an edit result at 1380 with the zoom control visible', async ({ page }) => {
  test.skip(!SHOTS, 'only when looking at it');
  await page.setViewportSize({ width: 1380, height: 860 });
  await openApp(page, withEdit);
  await page.getByRole('application', { name: 'Media comparison canvas' }).hover();
  await expect(page.locator('.zoom-hud')).toHaveCSS('opacity', '1');
  await page.screenshot({ path: `${SHOTS}/edit-1380-zoom-hud.png` });
});
