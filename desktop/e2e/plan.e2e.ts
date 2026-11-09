import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { hostCalls } from './fake-host';
import { ready, upscale } from './fixtures';

const chip = (page: Page, name: string) =>
  page.getByRole('group', { name: 'Fix first' }).getByRole('button', { name, exact: true });
const rows = (page: Page) => page.getByRole('list', { name: 'Stages' }).getByRole('listitem');

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
    await expect(page.getByRole('region', { name: 'Model plan' })).toBeVisible();
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText('SPAN');
    for (const name of ['Noise', 'JPEG artifacts', 'Blur'])
      await expect(chip(page, name)).toHaveAttribute('aria-pressed', 'false');
  });

  test('fix chips add and remove a fix stage before the upscaler', async ({ page }) => {
    await chip(page, 'Noise').click();
    await expect(chip(page, 'Noise')).toHaveAttribute('aria-pressed', 'true');
    await expect(rows(page)).toHaveCount(2);
    await expect(rows(page).last()).toContainText('SPAN');
    expect(await savedFixes(page)).toEqual(['noise']);

    await chip(page, 'Noise').click();
    await expect(chip(page, 'Noise')).toHaveAttribute('aria-pressed', 'false');
    await expect(rows(page)).toHaveCount(1);
    expect(await savedFixes(page)).toEqual([]);
  });

  test('fixes keep the default order and can be moved earlier or later', async ({ page }) => {
    // Pick them out of order; the plan still runs deblur first and noise last.
    await chip(page, 'Noise').click();
    await chip(page, 'Blur').click();
    await expect(rows(page)).toHaveCount(3);
    expect(await savedFixes(page)).toEqual(['blur', 'noise']);
    await expect(page.getByRole('button', { name: 'Move the blur fix earlier' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Move the noise fix later' })).toHaveCount(0);

    await page.getByRole('button', { name: 'Move the blur fix later' }).click();
    expect(await savedFixes(page)).toEqual(['noise', 'blur']);
    await expect(page.getByRole('button', { name: 'Move the blur fix later' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Move the noise fix earlier' })).toHaveCount(0);

    // A moved order stays when another fix is added at its own place.
    await chip(page, 'JPEG artifacts').click();
    expect(await savedFixes(page)).toEqual(['jpeg', 'noise', 'blur']);
    await page.getByRole('button', { name: 'Move the noise fix later' }).click();
    expect(await savedFixes(page)).toEqual(['jpeg', 'blur', 'noise']);
  });

  test('Faces is unavailable without a face model', async ({ page }) => {
    await expect(chip(page, 'Faces')).toBeDisabled();
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
    await chip(page, 'JPEG artifacts').click();
    await page.getByRole('button', { name: 'Change the jpeg fix model' }).click();
    await expect(page.getByRole('dialog', { name: 'Model library' })).toBeVisible();
  });

  test('Try others… opens the library for trial runs', async ({ page }) => {
    await page.getByRole('button', { name: 'Try other upscalers on this image' }).click();
    await expect(page.getByRole('dialog', { name: 'Model library' })).toBeVisible();
  });
});

test('Try others… waits for a ready worker', async ({ page }) => {
  await openApp(page, upscale);
  await showPane(page, 'enhance');
  await expect(
    page.getByRole('button', { name: 'Try other upscalers on this image' }),
  ).toBeDisabled();
});
