import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { hostCalls } from './fake-host';
import { ready, upscale } from './fixtures';

const fix = (page: Page, name: string) =>
  page.getByRole('group', { name: 'Fix' }).getByRole('checkbox', { name, exact: true });
const rows = (page: Page) => page.getByRole('list', { name: 'Stages' }).getByRole('listitem');

async function openPlanSheet(page: Page): Promise<void> {
  const trigger = page.locator('.foot-plan');
  if ((await trigger.getAttribute('aria-expanded')) !== 'true') {
    await trigger.click();
  }
  await expect(page.getByRole('group', { name: 'Plan' })).toBeVisible();
}

async function closePlanSheet(page: Page): Promise<void> {
  const trigger = page.locator('.foot-plan');
  if ((await trigger.getAttribute('aria-expanded')) === 'true') {
    await page.keyboard.press('Escape');
    await expect(page.getByRole('group', { name: 'Plan' })).toHaveCount(0);
  }
}

async function savedFixes(page: Page): Promise<string[]> {
  const call = (await hostCalls(page, 'save_settings')).at(-1);
  const settings = (call?.args as { settings: { fix_stages: { fix: string }[] } }).settings;
  return settings.fix_stages.map((stage) => stage.fix);
}

test.describe('plan card', () => {
  test.beforeEach(async ({ page }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
  });

  test('the plan starts with the upscaler alone', async ({ page }) => {
    for (const name of ['Noise', 'JPEG artifacts', 'Blur'])
      await expect(fix(page, name)).not.toBeChecked();
    await openPlanSheet(page);
    await expect(page.getByRole('group', { name: 'Plan' })).toBeVisible();
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText('LocalSR Photo S');
  });

  test('fix rows add and remove a fix stage before the upscaler', async ({ page }) => {
    await fix(page, 'Noise').click();
    await expect(fix(page, 'Noise')).toBeChecked();
    await openPlanSheet(page);
    await expect(rows(page)).toHaveCount(2);
    await expect(rows(page).last()).toContainText('LocalSR Photo S');
    expect(await savedFixes(page)).toEqual(['noise']);

    await closePlanSheet(page);
    await fix(page, 'Noise').click();
    await expect(fix(page, 'Noise')).not.toBeChecked();
    await openPlanSheet(page);
    await expect(rows(page)).toHaveCount(1);
    expect(await savedFixes(page)).toEqual([]);
  });

  test('fixes keep the default order and can be moved earlier or later', async ({ page }) => {
    // Pick them out of order; the plan still runs deblur first and noise last.
    await fix(page, 'Noise').click();
    await fix(page, 'Blur').click();
    await openPlanSheet(page);
    await expect(rows(page)).toHaveCount(3);
    expect(await savedFixes(page)).toEqual(['blur', 'noise']);
    await expect(page.getByRole('button', { name: 'Move the blur fix earlier' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Move the noise fix later' })).toHaveCount(0);

    await page.getByRole('button', { name: 'Move the blur fix later' }).click();
    expect(await savedFixes(page)).toEqual(['noise', 'blur']);
    await expect(page.getByRole('button', { name: 'Move the blur fix later' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Move the noise fix earlier' })).toHaveCount(0);

    // A moved order stays when another fix is added at its own place.
    await closePlanSheet(page);
    await fix(page, 'JPEG artifacts').click();
    await openPlanSheet(page);
    expect(await savedFixes(page)).toEqual(['jpeg', 'noise', 'blur']);
    await page.getByRole('button', { name: 'Move the noise fix later' }).click();
    expect(await savedFixes(page)).toEqual(['jpeg', 'blur', 'noise']);
  });

  test('each model row keeps its name, Change… and arrows on one line', async ({ page }) => {
    for (const name of ['Noise', 'JPEG artifacts', 'Blur']) await fix(page, name).click();
    await openPlanSheet(page);
    await expect(rows(page)).toHaveCount(4);
    const row = rows(page).nth(1);
    const centre = async (selector: string) => {
      const box = (await row.locator(selector).first().boundingBox())!;
      return box.y + box.height / 2;
    };
    const line = await centre('.stage');
    for (const part of ['.name', '.plan-end'])
      expect(Math.abs((await centre(part)) - line)).toBeLessThan(4);
    for (const label of ['Move the JPEG fix earlier', 'Move the JPEG fix later']) {
      const arrow = (await page.getByRole('button', { name: label }).boundingBox())!;
      expect(arrow.width).toBeGreaterThanOrEqual(24);
      expect(arrow.height).toBeGreaterThanOrEqual(24);
    }
    // The blur fix's warning stays within two lines under its name.
    const notes = (await rows(page).first().locator('.plan-note').boundingBox())!;
    expect(notes.height).toBeLessThanOrEqual(2 * 18);
    await expect(
      rows(page)
        .first()
        .getByRole('button', { name: /^Change/ }),
    ).toHaveCount(0);
    const cut = await rows(page)
      .locator('.name')
      .evaluateAll((els) => els.filter((el) => el.scrollWidth > el.clientWidth).length);
    expect(cut).toBe(0);
    // The note spans the name column to the right edge.
    const name = (await rows(page).first().locator('.name').boundingBox())!;
    const rowBox = (await rows(page).first().boundingBox())!;
    expect(Math.abs(notes.x - name.x)).toBeLessThan(1);
    expect(notes.x + notes.width).toBeGreaterThan(rowBox.x + rowBox.width - 4);
  });

  test('Faces is not offered', async ({ page }) => {
    await expect(fix(page, 'Faces')).toHaveCount(0);
  });

  test('Change… opens the model library on the upscaler and closes again', async ({ page }) => {
    await page.getByRole('button', { name: 'Change the upscaler' }).click();
    const library = page.getByRole('dialog', { name: 'Model library' });
    await expect(library).toBeVisible();
    await expect(library.getByRole('region', { name: 'Models' })).toBeVisible();
    await page.getByRole('button', { name: 'Close model library' }).click();
    await expect(library).toHaveCount(0);
  });

  test('a fix has its own Change… for its model', async ({ page }) => {
    await fix(page, 'JPEG artifacts').click();
    await fix(page, 'JPEG artifacts').locator('..').locator('..').getByRole('button').click();
    await page.getByRole('button', { name: 'Change the JPEG fix model' }).click();
    await expect(page.getByRole('dialog', { name: 'Model library' })).toBeVisible();
  });

  test('Fix popover supports keyboard dismissal and one open surface', async ({ page }) => {
    await fix(page, 'Noise').check();
    await fix(page, 'Blur').check();
    const noise = fix(page, 'Noise').locator('..').locator('..').getByRole('button');
    const blur = fix(page, 'Blur').locator('..').locator('..').getByRole('button');
    await noise.focus();
    await page.keyboard.press('Enter');
    const options = page.getByRole('group', { name: 'Noise options' });
    await expect(options).toBeVisible();
    await expect(options.getByRole('button', { name: 'Change the noise fix model' })).toBeFocused();
    await expect(options).toContainText('Model');
    await expect(
      options.getByRole('button', { name: /more models? in the library/ }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(noise).toBeFocused();
    await noise.click();
    await blur.focus();
    await expect(blur).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(options).toHaveCount(0);
    await expect(page.getByRole('group', { name: 'Blur options' })).toBeVisible();
    await fix(page, 'Colour').check();
    await fix(page, 'Colour').locator('..').locator('..').getByRole('button').click();
    await expect(
      page.getByRole('group', { name: 'Colour options' }).getByRole('button', { name: /Change/ }),
    ).toHaveCount(0);
  });

  test('Try others… opens the library for trial runs', async ({ page }) => {
    await openPlanSheet(page);
    await page.getByRole('button', { name: 'Try other upscalers on this image' }).click();
    await expect(page.getByRole('dialog', { name: 'Model library' })).toBeVisible();
  });
});

test('Try others… waits for a ready worker', async ({ page }) => {
  await openApp(page, upscale);
  await showPane(page, 'enhance');
  await openPlanSheet(page);
  await expect(
    page.getByRole('button', { name: 'Try other upscalers on this image' }),
  ).toBeDisabled();
});
