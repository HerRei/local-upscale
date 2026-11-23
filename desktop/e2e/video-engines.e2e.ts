import { expect, test, type Locator, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { hostCalls, type Scenario } from './fake-host';

// A 16 GB Apple silicon Mac whose worker runs both Labs video engines.
const mac: Scenario = {
  photos: 0,
  video: true,
  worker: 'ready',
  features: ['image_multi_fix', 'video_interpolation'],
  videoEngines: ['spandrel_image', 'seedvr2', 'flashvsr'],
  installed: ['span_photo_x4', 'flashvsr_v11'],
  settings: {
    task: 'video',
    video_task: 'upscale',
    selected_video_model_id: 'frame_by_frame',
    selected_model_id: 'span_photo_x4',
    output_scale: 4,
  },
};

async function openUpscalerLibrary(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: 'Change the upscaler' }).click();
  const library = page.getByRole('dialog', { name: 'Model library' });
  await expect(library).toBeVisible();
  return library;
}

test('FlashVSR is chosen from the library on a Mac that can run it, with its own memory panel', async ({
  page,
}) => {
  await openApp(page, mac);
  await showPane(page, 'enhance');
  await expect(page.locator('#video-engine')).toHaveCount(0);
  await expect(page.locator('.model-output-note')).toHaveCount(0);

  const library = await openUpscalerLibrary(page);
  await expect(library.locator('.list-head').nth(0)).toContainText('Frame by frame');
  await expect(library.locator('.list-head').nth(0)).toContainText('sorted by quality');
  await expect(library.locator('.list-head').nth(1)).toContainText('Several frames at once · Labs');
  await expect(library.locator('.list-head').nth(1)).toContainText(
    'use neighbouring frames · much slower',
  );

  await library.getByRole('button', { name: /^FlashVSR v1\.1 — Labs/ }).click();
  await library.getByRole('button', { name: 'Use for this job' }).click();
  await expect(library).toHaveCount(0);

  const memory = page.getByLabel('FlashVSR memory');
  await expect(memory).toContainText('Apple GPU · automatic memory management');
  await expect(memory).toContainText('windows of 41 frames');
  await expect(memory).not.toContainText(/SeedVR2|VAE|per clip|offload/);
  await expect(page.getByLabel('Reduce GPU memory')).toHaveCount(0);
  await expect(page.getByLabel('Output resolution')).toBeVisible();
  await page.getByRole('button', { name: /^Advanced/ }).click();
  await expect(page.getByText(/^FlashVSR · Labs\./)).toBeVisible();

  await page.getByRole('button', { name: 'Start selected video' }).click();
  await expect.poll(async () => (await hostCalls(page, 'start_jobs')).length).toBe(1);
  const { input } = (await hostCalls(page, 'start_jobs'))[0].args as {
    input: { video_model_id: string };
  };
  expect(input.video_model_id).toBe('flashvsr_v11');
});

test('on the CPU of the same Mac the library says what FlashVSR needs, and SeedVR2 still runs', async ({
  page,
}) => {
  await openApp(page, { ...mac, settings: { ...mac.settings, device_id: 'cpu' } });
  await showPane(page, 'enhance');
  const library = await openUpscalerLibrary(page);
  const flashRow = library.getByRole('button', { name: /^FlashVSR v1\.1 — Labs/ });
  await expect(flashRow.locator('.pill.bad')).toHaveText('Apple GPU only');
  await flashRow.click();
  await expect(library.getByRole('button', { name: 'Use for this job' })).toBeDisabled();

  await library.getByRole('button', { name: /^SeedVR2-3B — Labs/ }).click();
  await expect(library.getByRole('button', { name: 'Use for this job' })).toBeEnabled();

  await library.getByRole('button', { name: 'Close model library' }).click();
  await expect(page.locator('.inline-warning')).toHaveCount(0);
  await expect(page.locator('.model-output-note')).toHaveCount(0);
});

test('a saved FlashVSR choice that cannot run here says why and cannot start', async ({ page }) => {
  await openApp(page, {
    ...mac,
    settings: { ...mac.settings, device_id: 'cpu', selected_video_model_id: 'flashvsr_v11' },
  });
  await showPane(page, 'enhance');
  await expect(page.locator('.inline-warning')).toHaveText(
    'FlashVSR cannot run on this device. Choose a frame-by-frame model or the Apple GPU.',
  );
  await expect(page.getByLabel('FlashVSR memory')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Start selected video' })).toBeDisabled();
});

test('on a Windows PC each video model is judged by its own rule in the library', async ({
  page,
}) => {
  await openApp(page, {
    ...mac,
    devices: 'windows',
    platform: 'Win32',
    videoEngines: ['spandrel_image', 'seedvr2'],
    installed: ['span_photo_x4'],
  });
  await showPane(page, 'enhance');
  await expect(page.locator('.model-output-note')).toHaveCount(0);

  const library = await openUpscalerLibrary(page);
  const flashRow = library.getByRole('button', { name: /^FlashVSR v1\.1 — Labs/ });
  await expect(flashRow.locator('.pill.bad')).toHaveText('Apple silicon only');
  await flashRow.click();
  await expect(library.locator('.detail .fit')).toHaveText(
    'FlashVSR needs an Apple silicon Mac with 16 GB of memory.',
  );

  for (const name of [/^SeedVR2-3B — Labs/, /^SeedVR2-3B FP8 — Labs/]) {
    const row = library.getByRole('button', { name });
    await expect(row.locator('.pill.bad')).toHaveText('Unavailable');
    await row.click();
    await expect(library.locator('.detail .fit')).toHaveText(
      'SeedVR2 cannot run on this device. Choose a frame-by-frame model or a compatible CUDA, ROCm or Metal device.',
    );
  }
});

test('in Photo upscale the library shows no video rows in For this job', async ({ page }) => {
  await openApp(page, {
    ...mac,
    photos: 1,
    video: false,
    task: 'upscale',
    settings: {
      task: 'upscale',
      selected_model_id: 'span_photo_x4',
      output_scale: 4,
    },
  });
  await showPane(page, 'enhance');
  const library = await openUpscalerLibrary(page);
  await expect(library.getByText('Several frames at once · Labs')).toHaveCount(0);
  await expect(library.getByRole('button', { name: /^FlashVSR v1\.1 — Labs/ })).toHaveCount(0);
});
