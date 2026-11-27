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

// A real click: the page updates between the button's handler and the window's,
// which unit tests in jsdom do not do.
test('deleting a recipe asks first, and only Delete deletes', async ({ page }) => {
  await openApp(page, { devices: 'mac', photos: 1, recipes: [recipe] });
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Recipes' }).click();
  await page.getByRole('button', { name: 'Delete Phone photos' }).click();
  await expect(page.getByText('Delete Phone photos?')).toBeVisible();
  await page.getByRole('button', { name: 'Cancel deleting Phone photos' }).click();
  await expect(page.getByText('Delete Phone photos?')).toHaveCount(0);
  expect(await hostCalls(page, 'delete_recipe')).toHaveLength(0);

  await page.getByRole('button', { name: 'Delete Phone photos' }).click();
  await page.getByRole('heading', { name: 'Enhance' }).click();
  await expect(page.getByText('Delete Phone photos?')).toHaveCount(0);

  await page.getByRole('button', { name: 'Recipes' }).click();
  await page.getByRole('button', { name: 'Delete Phone photos' }).click();
  await page.getByRole('button', { name: 'Confirm delete Phone photos' }).click();
  expect(await hostCalls(page, 'delete_recipe')).toHaveLength(1);
  await expect(page.getByRole('button', { name: /Phone photos/ })).toHaveCount(0);
});
