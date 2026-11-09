import { expect, test, type Page } from '@playwright/test';
import { openApp } from './app';
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
    await expect(mediaTabs(page).getByRole('tab', { name: 'Inputs 2' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(page.getByRole('button', { name: /^Alpine lake 2\.jpg/ })).toBeVisible();

    await mediaTabs(page).getByRole('tab', { name: 'Outputs 4' }).click();
    const outputs = page.getByRole('group', { name: 'Outputs' });
    await expect(outputs.getByText('3 outputs')).toBeVisible();
    await expect(outputs.getByText('1 output', { exact: true })).toBeVisible();
    await expect(tick(page, 'HAT-S ×4, Alpine lake 1.jpg')).not.toBeChecked();
    await expect(page.getByText('0 of 4 slots')).toBeVisible();
    await expect(page.getByRole('button', { name: /Show in Compare/ })).toBeDisabled();

    await page.getByRole('button', { name: 'Fold Alpine lake 1.jpg' }).click();
    await expect(tick(page, 'HAT-S ×4, Alpine lake 1.jpg')).toHaveCount(0);
    await page.getByRole('button', { name: 'Unfold Alpine lake 1.jpg' }).click();
    await expect(tick(page, 'HAT-S ×4, Alpine lake 1.jpg')).toBeVisible();

    await mediaTabs(page).getByRole('tab', { name: 'Inputs 2' }).click();
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
    await expect(mainTabs(page).getByRole('tab', { name: 'Compare 1' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Pick two outputs' })).toBeVisible();
  });

  test('two picks use the slider, in slot order, and can be swapped', async ({ page }) => {
    await openOutputs(page);
    await tick(page, original).check();
    await tick(page, hat).check();
    await expect(page.getByText('2 of 4 slots')).toBeVisible();
    await page.getByRole('button', { name: 'Show in Compare 2' }).click();

    await expect(mainTabs(page).getByRole('tab', { name: 'Compare 2' })).toHaveAttribute(
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
    await page.getByRole('button', { name: 'Show in Compare 2' }).click();
    await expect(page.getByText(/The slider needs two versions of the same image/)).toBeVisible();
    await page.getByRole('radio', { name: 'Boxes' }).click();
    await expect(page.getByRole('slider')).toHaveCount(0);
    await expect(slots(page)).toHaveCount(2);
  });

  test('three or four picks switch to boxes and the fifth is refused', async ({ page }) => {
    await openOutputs(page);
    for (const label of [original, span, hat]) await tick(page, label).check();
    await page.getByRole('button', { name: 'Show in Compare 3' }).click();
    await expect(page.getByRole('radio', { name: 'Boxes' })).toBeChecked();
    await expect(page.getByRole('slider')).toHaveCount(0);
    await expect(slots(page)).toHaveCount(3);

    await mediaTabs(page)
      .getByRole('tab', { name: /^Outputs/ })
      .click();
    await tick(page, esrgan).check();
    await expect(page.getByText('4 of 4 slots')).toBeVisible();
    await expect(tick(page, 'SPAN ×4, Alpine lake 2.jpg')).toBeDisabled();
    await expect(slots(page)).toHaveCount(4);
  });

  test('removing a slot drops back to the empty state below two', async ({ page }) => {
    await openOutputs(page);
    for (const label of [original, span, hat]) await tick(page, label).check();
    await page.getByRole('button', { name: 'Show in Compare 3' }).click();

    await page.getByRole('button', { name: 'Remove SPAN ×4 from the compare' }).click();
    await expect(slots(page)).toHaveCount(2);
    await expect(mainTabs(page).getByRole('tab', { name: 'Compare 2' })).toBeVisible();
    await page.getByRole('button', { name: 'Remove HAT-S ×4 from the compare' }).click();
    await expect(page.getByRole('heading', { name: 'Pick two outputs' })).toBeVisible();

    await page.getByRole('button', { name: 'Remove Original from the compare' }).click();
    await expect(mainTabs(page).getByRole('tab', { name: 'Compare', exact: true })).toBeVisible();
    await mediaTabs(page)
      .getByRole('tab', { name: /^Outputs/ })
      .click();
    await expect(page.getByText('0 of 4 slots')).toBeVisible();
  });
});
