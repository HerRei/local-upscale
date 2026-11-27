import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { hostCalls } from './fake-host';

const fix = (page: Page, name: string) =>
  page.getByRole('group', { name: 'Fix' }).getByRole('checkbox', { name, exact: true });

test('video Same size, Fix rows and video recipes', async ({ page }) => {
  await openApp(page, {
    photos: 0,
    video: true,
    worker: 'ready',
    features: ['image_multi_fix', 'video_interpolation'],
    installed: [
      'localsr_quick_x4_v1',
      'denoise_realplksr_1x',
      'scunet_color_real_psnr',
      'fbcnn_color',
      'nafnet_gopro_deblur',
    ],
    settings: {
      task: 'video',
      video_task: 'upscale',
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: 'localsr_quick_x4_v1',
      output_scale: 4,
    },
    recipes: [
      {
        id: 'saved-video-restore',
        name: 'Saved Video Clean',
        task: 'video',
        video_task: 'denoise',
        quality: 'custom',
        model_id: 'scunet_color_real_psnr',
        video_model_id: 'frame_by_frame',
        output_scale: 1,
        tile_size: 256,
        halo: 16,
        precision: 'fp32',
        safe_memory: true,
        fixes: ['jpeg', 'noise'],
        stages: [
          { kind: 'deblock', model_id: 'fbcnn_color', fix: 'jpeg' },
          { kind: 'restore', model_id: 'scunet_color_real_psnr', fix: 'noise' },
        ],
      },
    ],
  });
  await showPane(page, 'enhance');

  // Switching a video to Same size leaves Fix rows unticked; tick Noise and Compression.
  await page.getByRole('radio', { name: 'Same size' }).click();
  await expect(fix(page, 'Noise')).not.toBeChecked();
  await fix(page, 'Noise').click();
  await fix(page, 'Compression').click();
  await page.locator('.foot-plan').click();
  const stages = page.getByRole('list', { name: 'Stages' });
  await expect(stages.getByRole('listitem')).toHaveCount(2);
  await expect(stages).toContainText('FBCNN');
  await page.keyboard.press('Escape');

  // Advanced is gone for video, and Output scale is not offered.
  await expect(page.getByRole('button', { name: /^Advanced/ })).toHaveCount(0);
  await expect(page.getByLabel('Output scale', { exact: true })).toHaveCount(0);

  // Save a video Same size recipe and check what is sent to the host.
  await page.getByRole('button', { name: 'Recipes' }).click();
  await page.getByRole('button', { name: 'Save current as recipe…' }).click();
  await page.getByRole('textbox', { name: 'Recipe name' }).fill('Video Clean');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(async () => (await hostCalls(page, 'save_recipe')).length).toBe(1);
  const { recipe: savedRecipe } = (await hostCalls(page, 'save_recipe'))[0].args as {
    recipe: {
      name: string;
      task: string;
      video_task: string;
      output_scale: number;
      fixes: string[];
      stages: { kind: string; fix?: string }[];
    };
  };
  expect(savedRecipe.name).toBe('Video Clean');
  expect(savedRecipe.task).toBe('video');
  expect(savedRecipe.video_task).toBe('denoise');
  expect(savedRecipe.output_scale).toBe(1);
  expect([...savedRecipe.fixes].sort()).toEqual(['jpeg', 'noise']);
  expect(savedRecipe.stages.map((s) => s.fix).sort()).toEqual(['jpeg', 'noise']);

  await page.getByRole('button', { name: 'Start selected video' }).click();
  await expect.poll(async () => (await hostCalls(page, 'start_jobs')).length).toBe(1);
  const { input: restoreCall } = (await hostCalls(page, 'start_jobs'))[0].args as {
    input: {
      task: string;
      video_task: string;
      fix_stages: { fix: string; model_id: string }[];
    };
  };
  expect(restoreCall.task).toBe('video');
  expect(restoreCall.video_task).toBe('denoise');
  expect(restoreCall.fix_stages.map((s) => s.fix).sort()).toEqual(['jpeg', 'noise']);

  // Switch back to 4× via Size; add Blur under Fix and start.
  await page.getByRole('radio', { name: '4×' }).click();
  await fix(page, 'Blur').click();
  await page.locator('.foot-plan').click();
  await expect(stages).toContainText('LocalSR Photo S');
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: 'Start selected video' }).click();
  await expect.poll(async () => (await hostCalls(page, 'start_jobs')).length).toBe(2);
  const { input: upscaleCall } = (await hostCalls(page, 'start_jobs'))[1].args as {
    input: {
      task: string;
      video_task: string;
      output_scale: number;
      fix_stages: { fix: string; model_id: string }[];
    };
  };
  expect(upscaleCall.task).toBe('video');
  expect(upscaleCall.video_task).toBe('upscale');
  expect(upscaleCall.output_scale).toBeGreaterThanOrEqual(2);
  expect(upscaleCall.fix_stages.map((s) => s.fix)).toContain('blur');

  // Re-apply saved video Same size recipe and confirm it switches back to Same size.
  await page.getByRole('button', { name: 'Recipes' }).click();
  await page.getByRole('button', { name: /^Saved Video Clean/ }).click();
  await expect(page.getByRole('radio', { name: 'Same size' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
});

test('video Same size with nothing picked disables Start until a fix or video option is on', async ({
  page,
}) => {
  await openApp(page, {
    photos: 0,
    video: true,
    worker: 'ready',
    features: ['image_multi_fix', 'video_interpolation'],
    installed: ['localsr_quick_x4_v1', 'scunet_color_real_psnr', 'nafnet_gopro_deblur'],
    settings: {
      task: 'video',
      video_task: 'upscale',
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: 'localsr_quick_x4_v1',
      output_scale: 4,
    },
  });
  await showPane(page, 'enhance');
  await page.getByRole('radio', { name: 'Same size' }).click();
  const noise = fix(page, 'Noise');
  await expect(noise).not.toBeChecked();
  await expect(page.getByText(/Nothing to run\. Pick what to fix/).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start selected video' })).toBeDisabled();

  await fix(page, 'Blur').click();
  await expect(page.getByText(/Nothing to run\. Pick what to fix/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Start selected video' })).toBeEnabled();
});
