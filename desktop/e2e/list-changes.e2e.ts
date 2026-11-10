import { expect, test } from '@playwright/test';
import { openApp, showPane } from './app';
import { hostCalls } from './fake-host';
import { withOutputs } from './fixtures';

test.describe('when images leave the list', () => {
  test('their Compare picks go with them and the host is told', async ({ page }) => {
    await openApp(page, withOutputs);
    await showPane(page, 'media');
    const mediaTabs = page.getByRole('tablist', { name: 'Media' });
    await mediaTabs.getByRole('tab', { name: /^Outputs/ }).click();
    await page.getByRole('checkbox', { name: 'Original, Alpine lake 1.jpg' }).check();
    await page.getByRole('checkbox', { name: 'SPAN ×4, Alpine lake 2.jpg' }).check();
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    const chips = page.getByRole('list', { name: 'Compared items' }).getByRole('listitem');
    await expect(chips).toHaveCount(2);
    await mediaTabs.getByRole('tab', { name: 'Inputs' }).click();
    await page.getByRole('button', { name: 'Remove Alpine lake 2.jpg' }).click();
    await expect(page.getByRole('button', { name: /^Alpine lake 2\.jpg/ })).toHaveCount(0);
    // One pick is left: Compare closes.
    await expect(page.getByRole('tablist', { name: 'Main pane' })).toHaveCount(0);
    const asked = await hostCalls(page, 'request_compare');
    expect(JSON.stringify(asked.at(-1)?.args)).not.toContain('photo-2');
  });

  test('Clear leaves no picks behind', async ({ page }) => {
    await openApp(page, withOutputs);
    await showPane(page, 'media');
    const mediaTabs = page.getByRole('tablist', { name: 'Media' });
    await mediaTabs.getByRole('tab', { name: /^Outputs/ }).click();
    await page.getByRole('checkbox', { name: 'Original, Alpine lake 1.jpg' }).check();
    await page.getByRole('checkbox', { name: 'SPAN ×4, Alpine lake 2.jpg' }).check();
    await mediaTabs.getByRole('tab', { name: 'Inputs' }).click();
    await page.getByRole('button', { name: 'Clear', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Clear', exact: true }).click();
    await expect(page.getByText('Add media to begin')).toBeVisible();
    await expect(page.getByRole('tablist', { name: 'Main pane' })).toHaveCount(0);
  });
});

test('Remove by keyboard leaves the focus in the list', async ({ page }) => {
  await openApp(page, { ...withOutputs, photos: 3 });
  await showPane(page, 'media');
  await page.getByRole('button', { name: 'Remove Alpine lake 2.jpg' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: /^Alpine lake 2\.jpg/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Remove Alpine lake 3.jpg' })).toBeFocused();
});
