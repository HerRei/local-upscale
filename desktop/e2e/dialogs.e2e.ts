import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { dropFiles, emit, hostCalls, refuse } from './fake-host';
import { withOutputs } from './fixtures';

const dialogOf = (page: Page) => page.getByRole('dialog');
const clearButton = (scope: ReturnType<typeof dialogOf>) =>
  scope.getByRole('button', { name: 'Clear', exact: true });

test.describe('a dialog opened over the Clear question', () => {
  test('replaces it: the question and its Clear button are gone', async ({ page }) => {
    await openApp(page, withOutputs);
    await showPane(page, 'media');
    await page.getByRole('button', { name: 'Clear', exact: true }).click();
    const dialog = dialogOf(page);
    await expect(dialog).toContainText('Clear all 2 items');
    await refuse(page, 'add_media', 'The file type is not supported.');
    await dropFiles(page, ['/fixture/bad.xyz']);
    await expect(dialog).toContainText('Could not add dropped files');
    await expect(clearButton(dialog)).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: 'OK' })).toBeVisible();
    expect(await hostCalls(page, 'clear_media')).toHaveLength(0);
  });
});

test.describe('the Clear question', () => {
  test('is not bypassed by a menu shortcut: nothing starts behind it', async ({ page }) => {
    await openApp(page, { ...withOutputs, worker: 'ready' });
    await showPane(page, 'media');
    await page.getByRole('button', { name: 'Clear', exact: true }).click();
    await expect(dialogOf(page)).toContainText('Clear all 2 items');
    await emit(page, 'native-menu-action', 'file.start');
    await page.waitForTimeout(300);
    expect(await hostCalls(page, 'start_jobs')).toHaveLength(0);
  });

  test('tells the user when the host refuses it after the answer', async ({ page }) => {
    await openApp(page, withOutputs);
    await showPane(page, 'media');
    await page.getByRole('button', { name: 'Clear', exact: true }).click();
    await refuse(page, 'clear_media', 'wait for the current queue to finish before changing media');
    await clearButton(dialogOf(page)).click();
    await expect(dialogOf(page)).toContainText('Could not clear the list');
    await expect(dialogOf(page)).toContainText('wait for the current queue');
  });

  test('counts the list as it is when answered', async ({ page }) => {
    await openApp(page, withOutputs);
    await showPane(page, 'media');
    await page.getByRole('button', { name: 'Clear', exact: true }).click();
    await expect(dialogOf(page)).toContainText('Clear all 2 items');
    await dropFiles(page, ['/fixture/a.jpg', '/fixture/b.jpg', '/fixture/c.jpg']);
    await expect(dialogOf(page)).toContainText('Clear all 5 items');
  });
});

test.describe('dialog keyboard behaviour', () => {
  const insideDialog = (page: Page) =>
    page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')));

  test('an error dialog takes the focus, keeps Tab inside, closes on Escape and hands the focus back', async ({
    page,
  }) => {
    await openApp(page, { ...withOutputs, worker: 'ready' });
    await refuse(page, 'start_jobs', 'No GPU.');
    // Two photos: the primary starts them all.
    const start = page.getByRole('button', { name: 'Upscale all 2' });
    await start.focus();
    await page.keyboard.press('Enter');
    await expect(dialogOf(page)).toContainText('Could not start');
    expect(await insideDialog(page)).toBe(true);
    for (let step = 0; step < 6; step++) {
      await page.keyboard.press('Tab');
      expect(await insideDialog(page), `after Tab ${step + 1}`).toBe(true);
    }
    await page.keyboard.press('Escape');
    await expect(dialogOf(page)).toHaveCount(0);
    await expect(start).toBeFocused();
  });

  test('the Clear question opens on Cancel and Escape answers it with Cancel', async ({ page }) => {
    await openApp(page, withOutputs);
    await showPane(page, 'media');
    await page.getByRole('button', { name: 'Clear', exact: true }).click();
    await expect(dialogOf(page).getByRole('button', { name: 'Cancel' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialogOf(page)).toHaveCount(0);
    expect(await hostCalls(page, 'clear_media')).toHaveLength(0);
  });

  test('the model library takes the focus and closes on Escape, however it was opened', async ({
    page,
  }) => {
    await openApp(page, { ...withOutputs, worker: 'ready' });
    const library = page.getByRole('dialog', { name: 'Model library' });
    await emit(page, 'native-menu-action', 'view.models');
    await expect(library).toBeVisible();
    expect(await insideDialog(page)).toBe(true);
    await page.keyboard.press('Escape');
    await expect(library).toHaveCount(0);
    await showPane(page, 'enhance');
    const change = page.getByRole('button', { name: /^Change/ }).first();
    await change.focus();
    await page.keyboard.press('Enter');
    await expect(library).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(library).toHaveCount(0);
    await expect(change).toBeFocused();
  });

  test('a dialog that holds the focus draws no ring around itself', async ({ page }) => {
    await openApp(page, { ...withOutputs, worker: 'ready' });
    await page.keyboard.press('Tab'); // the page is in keyboard mode, as after a Tab or Enter
    await emit(page, 'native-menu-action', 'view.models');
    const sheet = page.getByRole('dialog', { name: 'Model library' });
    await expect(sheet).toBeVisible();
    const outline = await sheet.evaluate((node) => {
      node.focus();
      const style = getComputedStyle(node);
      return `${style.outlineStyle} ${style.outlineWidth}`;
    });
    expect(outline).toMatch(/^none /);
  });
});
