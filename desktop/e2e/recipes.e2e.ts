import { expect, test } from '@playwright/test';
import { openApp, showPane } from './app';
import { hostCalls } from './fake-host';

const recipe = {
  id: 'r1',
  name: 'Phone photos',
  task: 'upscale',
  model_id: 'span_photo_x4',
  output_scale: 4,
  tile_size: 512,
  halo: 32,
  precision: 'fp32',
  safe_memory: true,
};

test('recipes menu shows plan-line subtitle without delete buttons, applies, and offers Browse recipes…', async ({
  page,
}) => {
  await openApp(page, { devices: 'mac', photos: 1, recipes: [recipe] });
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Recipes' }).click();

  const row = page.getByRole('button', { name: /^Phone photos/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText('Upscale 4×');
  await expect(page.getByRole('button', { name: /^Delete / })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Browse recipes…' })).toBeVisible();

  await row.click();
  await expect.poll(async () => (await hostCalls(page, 'save_settings')).length).toBeGreaterThan(0);
  await expect(page.getByRole('group', { name: 'Recipes' })).toHaveCount(0);
});
