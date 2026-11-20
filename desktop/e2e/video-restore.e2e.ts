import { expect, test } from '@playwright/test';
import { openApp, showPane } from './app';
import { hostCalls } from './fake-host';

test('video restore, prefix fixes, 1x output scale, and video recipes', async ({ page }) => {
  await openApp(page, {
    photos: 0,
    video: true,
    worker: 'ready',
    features: ['image_multi_fix', 'video_interpolation'],
    installed: [
      'span_photo_x4',
      'denoise_realplksr_1x',
      'scunet_color_real_psnr',
      'fbcnn_color',
      'nafnet_gopro_deblur',
    ],
    settings: {
      task: 'video',
      video_task: 'upscale',
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: 'span_photo_x4',
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

  // Switch selected video to Restore and enable JPEG artifacts (prefix fix) + Noise (primary restore).
  await page.getByRole('button', { name: 'Restore', exact: true }).click();
  await page.getByRole('button', { name: 'JPEG artifacts', exact: true }).click();
  await page.getByRole('button', { name: 'Noise', exact: true }).click();
  const stages = page.getByRole('list', { name: 'Stages' });
  await expect(stages).toContainText('Fix JPEG');
  await expect(stages).toContainText('Restore');

  // Output scale stays hidden in video Restore.
  await page.getByRole('button', { name: /Advanced/ }).click();
  await expect(page.getByLabel('Output scale', { exact: true })).toHaveCount(0);

  // Save a video Restore recipe and check what is sent to the host.
  await page.getByRole('button', { name: 'Save as recipe…' }).click();
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
  expect(savedRecipe.fixes).toEqual(['jpeg', 'noise']);
  expect(savedRecipe.stages.map((s) => [s.kind, s.fix])).toEqual([
    ['deblock', 'jpeg'],
    ['restore', 'noise'],
  ]);

  await page.getByRole('button', { name: 'Restore selected video' }).click();
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
  expect(restoreCall.fix_stages.map((s) => s.fix)).toEqual(['jpeg', 'noise']);

  // Switch back to Upscale, pick 1x Output scale, add Blur fix-first stage, and start.
  await page.getByRole('button', { name: 'Upscale', exact: true }).click();
  const scaleSelect = page.getByLabel('Output scale', { exact: true });
  await expect(scaleSelect).toBeVisible();
  await scaleSelect.selectOption('1');
  // At 1× the Quality heading carries the "?" that says the upscaler is not used.
  const oneXHelp = page.getByRole('button', { name: 'About Quality' });
  await expect(oneXHelp).toBeVisible();
  await expect(stages).not.toContainText('SPAN');
  await page.getByRole('button', { name: 'Blur', exact: true }).click();
  await expect(scaleSelect).toHaveValue('1');
  await expect(stages).toContainText('Fix blur');
  await expect(stages).not.toContainText('SPAN');

  await scaleSelect.selectOption('2');
  await expect(oneXHelp).toHaveCount(0);
  await expect(stages).toContainText('SPAN');
  await scaleSelect.selectOption('1');
  await expect(oneXHelp).toBeVisible();

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
  expect(upscaleCall.output_scale).toBe(1);
  expect(upscaleCall.fix_stages.map((s) => s.fix)).toContain('blur');

  // Re-apply saved video Restore recipe and confirm it switches back to Restore and hides scale.
  await page.getByRole('button', { name: /^Saved Video Clean/ }).click();
  await expect(page.getByRole('button', { name: 'Restore', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(page.getByLabel('Output scale', { exact: true })).toHaveCount(0);
});

test('1x video upscale disables Start when nothing else is selected and hides the SR stage', async ({
  page,
}) => {
  await openApp(page, {
    photos: 0,
    video: true,
    worker: 'ready',
    features: ['image_multi_fix', 'video_interpolation'],
    installed: ['nafnet_gopro_deblur'],
    settings: {
      task: 'video',
      video_task: 'upscale',
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: 'span_photo_x4',
      output_scale: 4,
    },
  });
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: /Advanced/ }).click();

  const scaleSelect = page.getByLabel('Output scale', { exact: true });
  // At 1× the Quality heading carries the "?" that says the upscaler is not used.
  const oneXHelp = page.getByRole('button', { name: 'About Quality' });
  const startButton = page.getByRole('button', { name: 'Start selected video' });
  const stages = page.getByRole('list', { name: 'Stages' });

  await expect(oneXHelp).toHaveCount(0);
  await expect(stages).toContainText('SPAN');

  await scaleSelect.selectOption('1');
  await expect(oneXHelp).toBeVisible();
  await expect(stages).not.toContainText('SPAN');
  await expect(startButton).toBeDisabled();
  await expect(page.getByText(/Nothing to run at 1×/)).toBeVisible();

  await page.getByRole('button', { name: 'Blur', exact: true }).click();
  await expect(stages).toContainText('Fix blur');
  await expect(startButton).toBeEnabled();

  await scaleSelect.selectOption('2');
  await expect(oneXHelp).toHaveCount(0);
  await expect(stages).toContainText('SPAN');
});
