import { expect, test, type Locator, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { withOutputs } from './fixtures';

// Safari, and so WebKit on macOS, skips buttons and checkboxes with a plain Tab
// unless the "Press Tab to highlight each item" preference is on; Option+Tab does it.
const tabKey = (browserName: string) =>
  browserName === 'webkit' && process.platform === 'darwin' ? 'Alt+Tab' : 'Tab';
const tabsNamed = (page: Page, name: string) => page.getByRole('tablist', { name });

/** The focused element shows an outline or a ring; a bare focus would be invisible. */
async function hasFocusRing(locator: Locator): Promise<boolean> {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
    return element.matches(':focus-visible') && (outline || style.boxShadow !== 'none');
  });
}

test.describe('keyboard navigation', () => {
  test.beforeEach(async ({ page }) => {
    await openApp(page, withOutputs);
  });

  for (const [name, first, second] of [['Media', 'Inputs', 'Outputs']]) {
    test(`${name} tabs follow the arrow keys, Home and End`, async ({ page }) => {
      const list = tabsNamed(page, name);
      await list.getByRole('tab', { name: first }).focus();
      await expect(list.getByRole('tab', { name: first })).toHaveAttribute('tabindex', '0');
      await expect(list.getByRole('tab', { name: second })).toHaveAttribute('tabindex', '-1');

      await page.keyboard.press('ArrowRight');
      await expect(list.getByRole('tab', { name: second })).toBeFocused();
      await expect(list.getByRole('tab', { name: second })).toHaveAttribute(
        'aria-selected',
        'true',
      );
      expect(await hasFocusRing(list.getByRole('tab', { name: second }))).toBe(true);

      await page.keyboard.press('ArrowRight'); // wraps
      await expect(list.getByRole('tab', { name: first })).toBeFocused();
      await page.keyboard.press('ArrowLeft'); // wraps back
      await expect(list.getByRole('tab', { name: second })).toBeFocused();
      await page.keyboard.press('Home');
      await expect(list.getByRole('tab', { name: first })).toHaveAttribute('aria-selected', 'true');
      await page.keyboard.press('End');
      await expect(list.getByRole('tab', { name: second })).toHaveAttribute(
        'aria-selected',
        'true',
      );
    });
  }

  test('the arrow keys switch the visible content with the tab', async ({ page }) => {
    await tabsNamed(page, 'Media').getByRole('tab', { name: 'Inputs' }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('group', { name: 'Outputs' })).toBeVisible();
  });

  test('the Main pane tabs exist only in Compare, where an arrow key returns to the preview', async ({
    page,
  }) => {
    await expect(tabsNamed(page, 'Main pane')).toHaveCount(0);
    await tabsNamed(page, 'Media').getByRole('tab', { name: 'Inputs' }).focus();
    await page.keyboard.press('ArrowRight');
    for (const label of ['Original, Alpine lake 1.jpg', 'HAT-S ×4, Alpine lake 1.jpg'])
      await page.getByRole('checkbox', { name: label }).check();
    await expect(tabsNamed(page, 'Main pane')).toHaveCount(0);
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    const list = tabsNamed(page, 'Main pane');
    await list.getByRole('tab', { name: 'Compare' }).focus();
    await page.keyboard.press('ArrowLeft');
    await expect(list).toHaveCount(0);
  });

  test('Tab reaches the checkboxes and buttons of Outputs with a visible focus ring', async ({
    page,
    browserName,
  }) => {
    await tabsNamed(page, 'Media').getByRole('tab', { name: 'Inputs' }).focus();
    await page.keyboard.press('ArrowRight');
    const checkbox = page.getByRole('checkbox', { name: 'Original, Alpine lake 1.jpg' });
    for (
      let step = 0;
      step < 6 && !(await checkbox.evaluate((e) => e === document.activeElement));
      step++
    )
      await page.keyboard.press(tabKey(browserName));
    await expect(checkbox).toBeFocused();
    expect(await hasFocusRing(checkbox)).toBe(true);
    await page.keyboard.press('Space');
    await expect(checkbox).toBeChecked();
    await expect(page.getByText('1 of 4 chosen')).toBeVisible();
  });

  test('every control on the first Tab stops shows where the focus is', async ({
    page,
    browserName,
  }) => {
    await showPane(page, 'enhance');
    const missing: string[] = [];
    await page.keyboard.press(tabKey(browserName));
    for (let stop = 0; stop < 14; stop++) {
      const active = page.locator(':focus');
      if (!(await hasFocusRing(active))) {
        missing.push(
          await active.evaluate(
            (e) =>
              `${e.tagName} ${e.getAttribute('aria-label') ?? e.textContent?.trim().slice(0, 30)}`,
          ),
        );
      }
      await page.keyboard.press(tabKey(browserName));
    }
    expect(missing).toEqual([]);
  });
});
