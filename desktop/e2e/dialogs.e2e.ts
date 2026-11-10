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
});
