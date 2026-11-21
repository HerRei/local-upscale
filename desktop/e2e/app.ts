import { expect, type Page } from '@playwright/test';
import { installFakeHost, type Scenario } from './fake-host';

export type Pane = 'media' | 'preview' | 'enhance';

/** Open the app against the fake host and wait until it has booted. */
export async function openApp(page: Page, scenario: Scenario): Promise<void> {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await installFakeHost(page, scenario);
  await page.goto('/');
  await expect(page.locator('.app-shell')).not.toHaveClass(/booting/);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(errors).toEqual([]);
}

/** Below 1280 px the three panes become pages behind the Workspace navigation. */
export async function showPane(page: Page, pane: Pane): Promise<void> {
  const label = { media: 'Media', preview: 'Viewer', enhance: 'Enhance' }[pane];
  const button = page
    .getByRole('navigation', { name: 'Workspace' })
    .getByRole('button', { name: label, exact: true });
  if (await button.isVisible()) await button.click();
}
