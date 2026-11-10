import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { empty, upscale, withOutputs } from './fixtures';
import { hostCalls } from './fake-host';

const mediaTabs = (page: Page) => page.getByRole('tablist', { name: 'Media' });
const mainTabs = (page: Page) => page.getByRole('tablist', { name: 'Main pane' });
const tick = (page: Page, label: string) => page.getByRole('checkbox', { name: label });

test.describe('Inputs and Outputs tabs', () => {
  test('Inputs lists the queued media and Outputs groups results under their picture', async ({
    page,
  }) => {
    await openApp(page, withOutputs);
    await expect(mediaTabs(page).getByRole('tab', { name: 'Inputs' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(page.getByRole('button', { name: /^Alpine lake 2\.jpg/ })).toBeVisible();

    await mediaTabs(page).getByRole('tab', { name: 'Outputs' }).click();
    const outputs = page.getByRole('group', { name: 'Outputs' });
    await expect(outputs.getByText('3 outputs')).toBeVisible();
    await expect(outputs.getByText('1 output', { exact: true })).toBeVisible();
    await expect(tick(page, 'HAT-S ×4, Alpine lake 1.jpg')).not.toBeChecked();
    // Nothing picked: no Compare bar at all.
    await expect(page.getByText(/of 4 chosen/)).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Compare these/ })).toHaveCount(0);

    await page.getByRole('button', { name: 'Fold Alpine lake 1.jpg' }).click();
    await expect(tick(page, 'HAT-S ×4, Alpine lake 1.jpg')).toHaveCount(0);
    await page.getByRole('button', { name: 'Unfold Alpine lake 1.jpg' }).click();
    await expect(tick(page, 'HAT-S ×4, Alpine lake 1.jpg')).toBeVisible();

    await mediaTabs(page).getByRole('tab', { name: 'Inputs' }).click();
    await expect(page.getByRole('button', { name: /^Alpine lake 1\.jpg/ })).toBeVisible();
  });

  test('both tabs have an empty state', async ({ page }) => {
    await openApp(page, empty);
    await expect(page.getByText('Add media to begin')).toBeVisible();
    await mediaTabs(page)
      .getByRole('tab', { name: /^Outputs/ })
      .click();
    await expect(page.getByText('No outputs yet')).toBeVisible();

    await openApp(page, upscale);
    await mediaTabs(page)
      .getByRole('tab', { name: /^Outputs/ })
      .click();
    await expect(page.getByText('No outputs yet')).toBeVisible();
  });
});

test.describe('Compare', () => {
  const original = 'Original, Alpine lake 1.jpg';
  const span = 'SPAN ×4, Alpine lake 1.jpg';
  const hat = 'HAT-S ×4, Alpine lake 1.jpg';
  const esrgan = 'RealESRGAN ×4, Alpine lake 1.jpg';
  const slots = (page: Page) =>
    page.getByRole('list', { name: 'Compared items' }).getByRole('listitem');

  async function openOutputs(page: Page): Promise<void> {
    await openApp(page, withOutputs);
    await mediaTabs(page)
      .getByRole('tab', { name: /^Outputs/ })
      .click();
  }

  test('without two picks the tab shows its empty state', async ({ page }) => {
    await openApp(page, withOutputs);
    await mainTabs(page).getByRole('tab', { name: 'Compare' }).click();
    await expect(page.getByRole('heading', { name: 'Pick two outputs' })).toBeVisible();
    await expect(page.getByRole('slider')).toHaveCount(0);

    await mediaTabs(page)
      .getByRole('tab', { name: /^Outputs/ })
      .click();
    await tick(page, span).check();
    await expect(slots(page)).toHaveCount(1);
    await expect(page.getByRole('heading', { name: 'Pick two outputs' })).toBeVisible();
  });

  test('two picks use the slider, in slot order, and can be swapped', async ({ page }) => {
    await openOutputs(page);
    await tick(page, original).check();
    await tick(page, hat).check();
    await expect(page.getByText('2 of 4 chosen')).toBeVisible();
    await page.getByRole('button', { name: 'Compare these 2' }).click();

    await expect(mainTabs(page).getByRole('tab', { name: 'Compare' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(
      page.getByRole('slider', { name: /Divider between slot 1 and slot 2/ }),
    ).toBeVisible();
    await expect(slots(page).nth(0)).toContainText('Original');
    await expect(slots(page).nth(1)).toContainText('HAT-S ×4');

    await page.getByRole('button', { name: 'Swap slots 1 and 2' }).click();
    await expect(slots(page).nth(0)).toContainText('HAT-S ×4');
    await expect(slots(page).nth(1)).toContainText('Original');
    expect((await hostCalls(page, 'request_compare')).at(-1)?.args).toEqual({
      picks: [
        { kind: 'output', media_id: 'photo-1', job_id: 'hat' },
        { kind: 'original', media_id: 'photo-1' },
      ],
    });
  });

  test('the slider needs two versions of one image; boxes take different images', async ({
    page,
  }) => {
    await openOutputs(page);
    await tick(page, span).check();
    await tick(page, 'SPAN ×4, Alpine lake 2.jpg').check();
    await expect(page.getByText('The slider needs two versions of the same image')).toHaveCount(0);
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    await expect(page.getByText(/The slider needs two versions of the same image/)).toBeVisible();
    await page.getByRole('radio', { name: 'Boxes' }).click();
    await expect(page.getByRole('slider')).toHaveCount(0);
    await expect(slots(page)).toHaveCount(2);
  });

  test('three or four picks switch to boxes and the fifth is refused', async ({ page }) => {
    await openOutputs(page);
    for (const label of [original, span, hat]) await tick(page, label).check();
    await page.getByRole('button', { name: 'Compare these 3' }).click();
    await expect(page.getByRole('radio', { name: 'Boxes' })).toBeChecked();
    await expect(page.getByRole('slider')).toHaveCount(0);
    await expect(slots(page)).toHaveCount(3);

    await mediaTabs(page)
      .getByRole('tab', { name: /^Outputs/ })
      .click();
    await tick(page, esrgan).check();
    await expect(page.getByText('4 of 4 chosen')).toBeVisible();
    await expect(tick(page, 'SPAN ×4, Alpine lake 2.jpg')).toBeDisabled();
    await expect(slots(page)).toHaveCount(4);
  });

  test('removing a slot drops back to the empty state below two', async ({ page }) => {
    await openOutputs(page);
    for (const label of [original, span, hat]) await tick(page, label).check();
    await page.getByRole('button', { name: 'Compare these 3' }).click();

    await page.getByRole('button', { name: 'Remove SPAN ×4 from the compare' }).click();
    await expect(slots(page)).toHaveCount(2);
    await page.getByRole('button', { name: 'Remove HAT-S ×4 from the compare' }).click();
    await expect(page.getByRole('heading', { name: 'Pick two outputs' })).toBeVisible();

    await page.getByRole('button', { name: 'Remove Original from the compare' }).click();
    await expect(mainTabs(page).getByRole('tab', { name: 'Compare', exact: true })).toBeVisible();
    await mediaTabs(page)
      .getByRole('tab', { name: /^Outputs/ })
      .click();
    await expect(page.getByText(/of 4 chosen/)).toHaveCount(0);
  });
});

test.describe('Help buttons', () => {
  async function expectShown(page: Page, name: string): Promise<void> {
    const tip = page.getByRole('button', { name });
    const text = page.getByRole('tooltip').filter({ visible: true });
    await expect(text).toHaveCount(1);
    const box = (await text.boundingBox())!;
    const view = page.viewportSize()!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(view.width);
    expect(box.y + box.height).toBeLessThanOrEqual(view.height);
    // Nothing covers the text: the element at its centre is the text itself.
    const covered = await text.evaluate((node) => {
      const r = node.getBoundingClientRect();
      (node as HTMLElement).style.pointerEvents = 'auto';
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      (node as HTMLElement).style.pointerEvents = '';
      return !hit || !node.contains(hit);
    });
    expect(covered).toBe(false);
    // And it is not clipped by an ancestor: its full height is inside the clipped area.
    const clipped = await text.evaluate((node) => {
      const r = node.getBoundingClientRect();
      for (let el = node.parentElement; el; el = el.parentElement) {
        if (getComputedStyle(el).overflow === 'visible') continue;
        const a = el.getBoundingClientRect();
        if (r.bottom > a.bottom + 0.5 || r.right > a.right + 0.5 || r.top < a.top - 0.5)
          return true;
      }
      return false;
    });
    expect(clipped).toBe(false);
    await tip.blur();
  }

  test('every ? opens by click and by keyboard with its text fully visible', async ({ page }) => {
    await openApp(page, withOutputs);
    await showPane(page, 'media');
    await page.getByRole('tab', { name: 'Outputs' }).click();
    await page.getByRole('checkbox', { name: 'Original, Alpine lake 1.jpg' }).check();
    await page.getByRole('checkbox', { name: 'HAT-S ×4, Alpine lake 1.jpg' }).check();
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    await expect(page.getByRole('slider')).toBeVisible();
    await showPane(page, 'preview');
    // The left pane (Inputs, Outputs, Compare bar) has no ? button; the Compare tab has one.
    await expect(page.getByRole('button', { name: 'About Compare' })).toHaveCount(1);
    const tip = page.getByRole('button', { name: 'About Compare' });
    await tip.click();
    await expectShown(page, 'About Compare');
    await page.mouse.move(0, 0);
    await tip.focus();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('tooltip').filter({ visible: true })).toHaveCount(0);
    await tip.blur();
    await page.keyboard.press('Tab');
    await tip.focus();
    await expectShown(page, 'About Compare');
  });

  test('the ? next to Try others shows its text below the Model card', async ({ page }) => {
    await openApp(page, withOutputs);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'About Try others' }).click();
    await expectShown(page, 'About Try others');
  });
});

test.describe('Clear', () => {
  test('asks first, Cancel and Escape keep the list, Clear empties it', async ({ page }) => {
    await openApp(page, withOutputs);
    await showPane(page, 'media');
    const clear = page.getByRole('button', { name: 'Clear', exact: true });
    const dialog = page.getByRole('dialog');
    await clear.click();
    await expect(dialog).toContainText('Clear all 2 items from the list?');
    await expect(dialog).toContainText('not deleted');
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toHaveCount(0);
    expect(await hostCalls(page, 'clear_media')).toHaveLength(0);
    await clear.click();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    expect(await hostCalls(page, 'clear_media')).toHaveLength(0);
    await page.getByText('Alpine lake 2.jpg').first().waitFor();
    await clear.click();
    await dialog.getByRole('button', { name: 'Clear', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(async () => (await hostCalls(page, 'clear_media')).length).toBe(1);
    await expect(page.getByText('Add media to begin')).toBeVisible();
  });

  test('the Compare bar Clear only drops the picks and does not ask', async ({ page }) => {
    await openApp(page, withOutputs);
    await showPane(page, 'media');
    await page.getByRole('tab', { name: 'Outputs' }).click();
    await page.getByRole('checkbox', { name: 'Original, Alpine lake 1.jpg' }).check();
    await page.getByRole('button', { name: 'Clear', exact: true }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByText(/of 4 chosen/)).toHaveCount(0);
  });
});
