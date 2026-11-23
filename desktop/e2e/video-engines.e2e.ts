import { expect, test } from '@playwright/test';
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

test('FlashVSR is offered on a Mac that can run it, with its own memory panel', async ({
  page,
}) => {
  await openApp(page, mac);
  await showPane(page, 'enhance');
  const engine = page.locator('#video-engine');
  await expect(engine.locator('option[value="flashvsr_v11"]')).toBeEnabled();
  await expect(engine.locator('option[value="flashvsr_v11"]')).toHaveText(
    'FlashVSR v1.1 — Labs · Installed',
  );
  await expect(engine.locator('option[value="seedvr2_3b"]')).toBeEnabled();
  await expect(page.locator('.model-output-note')).toHaveCount(0);

  await engine.selectOption('flashvsr_v11');
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

test('on the CPU of the same Mac the menu says what FlashVSR needs, and SeedVR2 still runs', async ({
  page,
}) => {
  await openApp(page, { ...mac, settings: { ...mac.settings, device_id: 'cpu' } });
  await showPane(page, 'enhance');
  const engine = page.locator('#video-engine');
  await expect(engine.locator('option[value="flashvsr_v11"]')).toBeDisabled();
  await expect(engine.locator('option[value="flashvsr_v11"]')).toHaveText(
    'FlashVSR v1.1 — Labs · Apple GPU only',
  );
  await expect(engine.locator('option[value="seedvr2_3b"]')).toBeEnabled();
  await expect(page.locator('.model-output-note')).toHaveCount(0);
});

test('a saved FlashVSR choice that cannot run here says why and cannot start', async ({ page }) => {
  await openApp(page, {
    ...mac,
    settings: { ...mac.settings, device_id: 'cpu', selected_video_model_id: 'flashvsr_v11' },
  });
  await showPane(page, 'enhance');
  await expect(page.locator('.model-output-note')).toHaveText(
    'FlashVSR cannot run on this device. Choose frame-by-frame video or the Apple GPU.',
  );
  await expect(page.getByLabel('FlashVSR memory')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Start selected video' })).toBeDisabled();
});

test('on a Windows PC each engine is judged by its own rule', async ({ page }) => {
  await openApp(page, {
    ...mac,
    devices: 'windows',
    platform: 'Win32',
    videoEngines: ['spandrel_image', 'seedvr2'],
    installed: ['span_photo_x4'],
  });
  await showPane(page, 'enhance');
  const engine = page.locator('#video-engine');
  for (const id of ['seedvr2_3b', 'seedvr2_3b_fp8', 'flashvsr_v11'])
    await expect(engine.locator(`option[value="${id}"]`)).toBeDisabled();
  await expect(engine.locator('option[value="seedvr2_3b"]')).toHaveText(
    'SeedVR2-3B — Labs · Unavailable',
  );
  await expect(engine.locator('option[value="flashvsr_v11"]')).toHaveText(
    'FlashVSR v1.1 — Labs · Apple silicon only',
  );
  // SeedVR2 keeps its line; FlashVSR's reason is in the menu and adds no second one.
  await expect(page.locator('.model-output-note')).toHaveText(
    'SeedVR2 cannot run on this device. Choose frame-by-frame video or a compatible CUDA, ROCm or Metal device.',
  );
});
