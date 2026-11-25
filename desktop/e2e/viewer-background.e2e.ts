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

test('colour plus upscale live preview keeps an opaque colourised base without a checkerboard', async ({
  page,
}) => {
  await openApp(page, {
    ...withOutputs,
    photos: 1,
    running: true,
    settings: { ...withOutputs.settings, appearance: 'light', viewer_background: 'checker' },
  });
  await showPane(page, 'preview');

  const { blueBase, greenTile } = await page.evaluate(() => {
    const makeJpeg = (fill: string) => {
      const canvas = document.createElement('canvas');
      canvas.width = 64;
      canvas.height = 64;
      const ctx = canvas.getContext('2d')!;
      ctx.fillStyle = fill;
      ctx.fillRect(0, 0, 64, 64);
      return canvas.toDataURL('image/jpeg', 0.95).split(',')[1];
    };
    return { blueBase: makeJpeg('rgb(50, 110, 220)'), greenTile: makeJpeg('rgb(40, 190, 80)') };
  });

  await emit(page, 'worker-message', {
    type: 'live_preview_frame',
    data: {
      job_id: 'job-photo-1',
      sequence: 1,
      stage_index: 0,
      preview_kind: 'tile',
      output_x: 0,
      output_y: 0,
      output_width: 1600,
      output_height: 1067,
      image_width: 1600,
      image_height: 1067,
      jpeg_base64: blueBase,
    },
  });
  await emit(page, 'worker-message', {
    type: 'tile_update',
    data: {
      job_id: 'job-photo-1',
      stage_index: 1,
      phase: 'started',
      active_tile_size: 256,
      output_x: 0,
      output_y: 0,
      output_width: 1024,
      output_height: 1024,
      image_width: 6400,
      image_height: 4268,
    },
  });
  await emit(page, 'worker-message', {
    type: 'live_preview_frame',
    data: {
      job_id: 'job-photo-1',
      sequence: 2,
      stage_index: 1,
      preview_kind: 'tile',
      output_x: 0,
      output_y: 0,
      output_width: 1024,
      output_height: 1024,
      image_width: 6400,
      image_height: 4268,
      jpeg_base64: greenTile,
    },
  });

  const canvas = page.locator('canvas.progressive-image');
  await expect(canvas).toHaveClass(/visible/);
  await expect(page.locator('.preview-pane .canvas-well')).toHaveCSS('background-image', 'none');

  await expect
    .poll(async () =>
      canvas.evaluate((el) => {
        const ctx = (el as HTMLCanvasElement).getContext('2d')!;
        const sample = (x: number, y: number) => Array.from(ctx.getImageData(x, y, 1, 1).data);
        return {
          finishedTile: sample(64, 64),
          unfinishedCellA: sample(500, 128),
          unfinishedCellB: sample(756, 128),
        };
      }),
    )
    .toMatchObject({
      finishedTile: expect.any(Array),
      unfinishedCellA: expect.any(Array),
      unfinishedCellB: expect.any(Array),
    });

  const pixels = await canvas.evaluate((el) => {
    const ctx = (el as HTMLCanvasElement).getContext('2d')!;
    const sample = (x: number, y: number) => Array.from(ctx.getImageData(x, y, 1, 1).data);
    return {
      finishedTile: sample(64, 64),
      unfinishedCellA: sample(500, 128),
      unfinishedCellB: sample(756, 128),
    };
  });

  // Finished tile is opaque green (stage 1 upscaled output).
  expect(pixels.finishedTile[3]).toBe(255);
  expect(pixels.finishedTile[1]).toBeGreaterThan(pixels.finishedTile[0] + 80);
  expect(pixels.finishedTile[1]).toBeGreaterThan(pixels.finishedTile[2] + 50);

  // Unfinished cells are opaque blue (stage 0 colourised base) and match each other (no checkerboard).
  for (const cell of [pixels.unfinishedCellA, pixels.unfinishedCellB]) {
    expect(cell[3]).toBe(255);
    expect(cell[2]).toBeGreaterThan(cell[0] + 80);
    expect(cell[2]).toBeGreaterThan(cell[1] + 40);
  }
  expect(Math.abs(pixels.unfinishedCellA[0] - pixels.unfinishedCellB[0])).toBeLessThanOrEqual(4);
  expect(Math.abs(pixels.unfinishedCellA[1] - pixels.unfinishedCellB[1])).toBeLessThanOrEqual(4);
  expect(Math.abs(pixels.unfinishedCellA[2] - pixels.unfinishedCellB[2])).toBeLessThanOrEqual(4);
});
