import { expect, test } from '@playwright/test';
import { openApp } from './app';
import { hostCalls } from './fake-host';
import { withOutputs } from './fixtures';

test.describe('Use as input', () => {
  test('adds the shown result to Inputs, selects it, and does not add it twice', async ({
    page,
  }) => {
    await openApp(page, { ...withOutputs, worker: 'ready', finished: true, pairs: true });
    const use = page.getByRole('button', { name: 'Use as input' });
    await expect(use).toHaveAttribute(
      'title',
      'Add this result to Inputs so it can be processed again',
    );
    const tabs = page.getByRole('tablist', { name: 'Media' });
    await tabs.getByRole('tab', { name: /^Outputs/ }).click();
    await page.getByRole('button', { name: 'Show SPAN ×4, Alpine lake 1.jpg' }).click();
    await use.click();
    await expect(tabs.getByRole('tab', { name: /^Inputs/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const added = page.getByRole('button', { name: /^alpine-lake-1-span\.png/ });
    await expect(added).toHaveAttribute('aria-current', 'true');
    await expect(page.getByRole('button', { name: /^Alpine lake 1\.jpg/ })).not.toHaveAttribute(
      'aria-current',
      'true',
    );
    // The new input has no result yet, so the button is gone until a result is shown again.
    await expect(use).toHaveCount(0);
    await tabs.getByRole('tab', { name: /^Outputs/ }).click();
    await page.getByRole('button', { name: 'Show SPAN ×4, Alpine lake 1.jpg' }).click();
    await use.click();
    await expect(page.getByRole('button', { name: /^alpine-lake-1-span\.png/ })).toHaveCount(1);
    expect(await hostCalls(page, 'use_result_as_input')).toHaveLength(2);
  });
});
