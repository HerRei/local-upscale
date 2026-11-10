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
    const mainTabs = page.getByRole('tablist', { name: 'Main pane' });
    await expect(mainTabs.getByRole('tab', { name: 'Compare 2' })).toBeVisible();
    await mediaTabs.getByRole('tab', { name: 'Inputs' }).click();
    await page.getByRole('button', { name: 'Remove Alpine lake 2.jpg' }).click();
    await expect(page.getByRole('button', { name: /^Alpine lake 2\.jpg/ })).toHaveCount(0);
    await expect(mainTabs.getByRole('tab', { name: 'Compare 1' })).toBeVisible();
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
    const mainTabs = page.getByRole('tablist', { name: 'Main pane' });
    await expect(mainTabs.getByRole('tab', { name: /^Compare$/ })).toBeVisible();
  });
});
