import { expect, type Locator, type Page } from '@playwright/test';
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

/** WCAG contrast of an element's text against the backgrounds it is drawn on. */
export const contrastOnPage = (locator: Locator) =>
  locator.evaluate((element) => {
    const rgba = (value: string) => (value.match(/[\d.]+/g) ?? []).map(Number);
    const layers: number[][] = [];
    for (let node: Element | null = element; node; node = node.parentElement) {
      const [r, g, b, a = 1] = rgba(getComputedStyle(node).backgroundColor);
      if (a > 0) layers.push([r, g, b, a]);
      if (a >= 1) break;
    }
    let back = [0, 0, 0];
    for (const [r, g, b, a] of layers.reverse())
      back = [r * a + back[0] * (1 - a), g * a + back[1] * (1 - a), b * a + back[2] * (1 - a)];
    const style = getComputedStyle(element);
    const [fr, fg, fb, fa = 1] = rgba(style.color);
    const alpha = fa * Number(style.opacity);
    const fore = [fr, fg, fb].map((c, i) => c * alpha + back[i] * (1 - alpha));
    const lum = (rgb: number[]) =>
      rgb
        .map((c) => c / 255)
        .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
        .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
    const [hi, lo] = [lum(fore), lum(back)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  });
