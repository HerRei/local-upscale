import { expect, test } from '@playwright/test';
import { openApp, showPane } from './app';
import { answer, refuse } from './fake-host';
import { withOutputs } from './fixtures';

// The host refuses a command (an unsupported file, a queue that just became busy). The
// window says so in a dialog instead of doing nothing.
test.describe('a refused host call', () => {
  test.beforeEach(async ({ page }) => {
    await openApp(page, { ...withOutputs, photos: 3 });
    await showPane(page, 'media');
  });

  test('Add Media… names the problem', async ({ page }) => {
    await answer(page, 'plugin:dialog|open', ['/fixture/photo.heic']);
    await refuse(page, 'add_media', 'unsupported media file type');
    await page.getByRole('button', { name: 'Add Media…' }).click();
    await expect(page.getByRole('dialog')).toContainText('Could not add files');
    await expect(page.getByRole('dialog')).toContainText('unsupported media file type');
  });

  test('Add Folder… names the problem', async ({ page }) => {
    await answer(page, 'plugin:dialog|open', '/fixture/folder');
    await refuse(page, 'scan_media_folder', 'Permission denied (os error 13)');
    await page.getByRole('button', { name: 'Add Folder…' }).click();
    await expect(page.getByRole('dialog')).toContainText('Could not add the folder');
    await expect(page.getByRole('dialog')).toContainText('Permission denied');
  });

  test('clicking an item names the problem', async ({ page }) => {
    await refuse(page, 'select_media', 'media item not found');
    await page.getByRole('button', { name: /^Alpine lake 3\.jpg/ }).click();
    await expect(page.getByRole('dialog')).toContainText('Could not select the item');
  });

  test('Remove names the problem and keeps the item', async ({ page }) => {
    await refuse(page, 'remove_media', 'wait for the current queue to finish');
    await page.getByRole('button', { name: 'Remove Alpine lake 3.jpg' }).click();
    await expect(page.getByRole('dialog')).toContainText('Could not remove the item');
    await page.getByRole('button', { name: 'OK' }).click();
    await expect(page.getByRole('button', { name: /^Alpine lake 3\.jpg/ })).toBeVisible();
  });
});
