import { expect, test } from '@playwright/test';
import { openApp, showPane } from './app';
import { emit, hostCalls } from './fake-host';
import { empty, videoUnfinished, withOutputs } from './fixtures';

test('default viewer background is checkerboard', async ({ page }) => {
  await openApp(page, withOutputs);
  await expect(page.locator('html')).toHaveAttribute('data-viewer-bg', 'checker');
  const bgImage = await page
    .locator('.preview-pane .canvas-well')
    .evaluate((el) => getComputedStyle(el).backgroundImage);
  expect(bgImage).not.toBe('none');
});

test('grey viewer background sets solid grey stage without drop shadow', async ({ page }) => {
  await openApp(page, {
    ...withOutputs,
    settings: { ...withOutputs.settings, viewer_background: 'grey' },
  });
  await showPane(page, 'preview');
  const well = page.locator('.preview-pane .canvas-well');
  await expect(well).toHaveCSS('background-color', 'rgb(118, 118, 118)');
  await expect(well).toHaveCSS('background-image', 'none');
  await expect(well.locator('.image-stage')).toHaveCSS('box-shadow', 'none');
});

test('zoom bar swatch cycles through all four viewer backgrounds', async ({ page }) => {
  await openApp(page, withOutputs);
  await showPane(page, 'preview');
  const well = page.locator('.preview-pane .canvas-well');
  await well.hover();

  await page.getByRole('button', { name: 'Viewer background: Checkerboard' }).click();
  await expect
    .poll(async () => (await hostCalls(page, 'set_preference')).at(-1)?.args)
    .toEqual({ name: 'viewer_background', value: 'black' });
  await expect(well).toHaveCSS('background-color', 'rgb(0, 0, 0)');

  await page.getByRole('button', { name: 'Viewer background: Black' }).click();
  await expect(well).toHaveCSS('background-color', 'rgb(118, 118, 118)');

  await page.getByRole('button', { name: 'Viewer background: Grey' }).click();
  await expect(well).toHaveCSS('background-color', 'rgb(255, 255, 255)');

  await page.getByRole('button', { name: 'Viewer background: White' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-viewer-bg', 'checker');
  expect(await well.evaluate((el) => getComputedStyle(el).backgroundImage)).not.toBe('none');
});

test('empty stage keeps the theme background and text when viewer background is white', async ({
  page,
}) => {
  await openApp(page, {
    ...empty,
    settings: { ...empty.settings, viewer_background: 'white' },
  });
  await showPane(page, 'preview');
  const well = page.locator('.preview-pane .canvas-well');
  await expect(well).toHaveCSS('background-color', 'rgb(15, 15, 15)');
  expect(await well.evaluate((el) => getComputedStyle(el).backgroundImage)).not.toBe('none');
});

test('white viewer background applies to Compare boxes while box bars keep the theme', async ({
  page,
}) => {
  await openApp(page, {
    ...withOutputs,
    settings: { ...withOutputs.settings, viewer_background: 'white' },
  });
  await page.getByRole('tab', { name: /^Outputs/ }).click();
  for (const label of ['Original', 'SPAN ×4', 'HAT-S ×4']) {
    await page.getByRole('checkbox', { name: `${label}, Alpine lake 1.jpg` }).check();
  }
  await page.getByRole('button', { name: 'Compare these 3' }).click();
  await showPane(page, 'preview');

  const boxes = page.locator('.compare-pane .box');
  await expect(boxes).toHaveCount(3);
  for (const box of await boxes.all()) {
    await expect(box).toHaveCSS('background-color', 'rgb(255, 255, 255)');
    await expect(box.locator('.box-bar')).not.toHaveCSS('background-color', 'rgb(255, 255, 255)');
  }
});

test('grey viewer background applies to a finished video and its Video zoom swatch', async ({
  page,
}) => {
  await openApp(page, {
    ...videoUnfinished,
    settings: { ...videoUnfinished.settings, viewer_background: 'grey' },
  });
  await showPane(page, 'media');
  await page.locator('.media-select', { hasText: 'Alpine lake.mp4' }).click();
  await showPane(page, 'preview');
  const well = page.locator('.preview-pane .canvas-well');
  await expect(well.locator('video').first()).toBeVisible();
  await expect(well).toHaveCSS('background-color', 'rgb(118, 118, 118)');
  const videoZoom = page.getByRole('group', { name: 'Video zoom' });
  await expect(videoZoom.getByRole('button', { name: 'Viewer background: Grey' })).toBeVisible();
});

test('Settings Appearance Viewer background White updates the stage', async ({ page }) => {
  await openApp(page, withOutputs);
  await emit(page, 'native-menu-action', 'app.settings');
  const dialog = page.getByRole('dialog', { name: 'Settings' });
  await dialog.getByRole('tab', { name: 'Appearance' }).click();
  await dialog.getByLabel('Viewer background', { exact: true }).selectOption('white');
  await expect
    .poll(async () => (await hostCalls(page, 'set_preference')).at(-1)?.args)
    .toEqual({ name: 'viewer_background', value: 'white' });
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await showPane(page, 'preview');
  await expect(page.locator('.preview-pane .canvas-well')).toHaveCSS(
    'background-color',
    'rgb(255, 255, 255)',
  );
});

test('viewer background grey preview @visual', async ({ page }) => {
  await openApp(page, {
    ...withOutputs,
    settings: { ...withOutputs.settings, viewer_background: 'grey' },
  });
  await showPane(page, 'preview');
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('viewer-bg-grey-preview.png');
});

test('viewer background black split preview light @visual', async ({ page }) => {
  await openApp(page, {
    ...withOutputs,
    worker: 'ready',
    pairs: true,
    settings: { ...withOutputs.settings, appearance: 'light', viewer_background: 'black' },
  });
  await page.getByRole('tab', { name: /^Outputs/ }).click();
  await page.getByRole('button', { name: 'Show SPAN ×4, Alpine lake 1.jpg' }).click();
  await showPane(page, 'preview');
  await expect(page.getByRole('slider', { name: 'Before and after comparison' })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('viewer-bg-black-preview-light.png');
});

test('viewer background white compare boxes @visual', async ({ page }) => {
  await openApp(page, {
    ...withOutputs,
    settings: { ...withOutputs.settings, viewer_background: 'white' },
  });
  await page.getByRole('tab', { name: /^Outputs/ }).click();
  for (const label of ['Original', 'SPAN ×4', 'HAT-S ×4', 'RealESRGAN ×4']) {
    await page.getByRole('checkbox', { name: `${label}, Alpine lake 1.jpg` }).check();
  }
  await page.getByRole('button', { name: 'Compare these 4' }).click();
  await showPane(page, 'preview');
  await expect(
    page.getByRole('list', { name: 'Compared items' }).getByRole('listitem'),
  ).toHaveCount(4);
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('viewer-bg-white-compare-boxes.png');
});
