import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { emit, failDownload, progressDownload } from './fake-host';
import {
  cutout,
  firstRun,
  ready,
  scratched,
  scratchedModel,
  shaky,
  slow,
  strength,
  tape,
  upscale,
  video,
  windows,
  withOutputs,
} from './fixtures';

// Pixel baselines of the key screens, one set per browser, window size and platform
// (see snapshotPathTemplate): they catch layout and styling regressions that the
// behaviour tests cannot see. Update them with `npm run e2e:update` after a
// deliberate visual change and review the images in the diff.

async function settle(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map((image) => image.decode().catch(() => {})));
  });
}

async function shoot(page: Page, name: string): Promise<void> {
  await settle(page);
  await expect(page).toHaveScreenshot(`${name}.png`);
}

const recipeFixture = {
  task: 'upscale',
  model_id: 'span_photo_x4',
  output_scale: 4,
  tile_size: 512,
  halo: 32,
  precision: 'fp32',
  safe_memory: false,
};

const outputsTab = (page: Page) => page.getByRole('tab', { name: /^Outputs/ });
const pick = (page: Page, label: string) =>
  page.getByRole('checkbox', { name: `${label}, Alpine lake 1.jpg` }).check();

test.describe('screens @visual', () => {
  test('first run with the welcome note', async ({ page }) => {
    await openApp(page, firstRun);
    await shoot(page, 'first-run');
  });

  test('plan for a Quick upscale', async ({ page }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await shoot(page, 'panel-photo');
  });

  test('plan with two fixes in front of the upscaler', async ({ page }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByRole('checkbox', { name: 'Noise', exact: true }).check();
    await page.getByRole('checkbox', { name: 'Blur', exact: true }).check();
    await expect(page.locator('.foot-plan')).toContainText('Blur');
    await shoot(page, 'panel-fixes');
    await page
      .getByRole('checkbox', { name: 'Noise', exact: true })
      .locator('../..')
      .getByRole('button')
      .click();
    await shoot(page, 'panel-popover');
  });

  test('plan with the background removed', async ({ page }) => {
    await openApp(page, cutout);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page
      .getByRole('radiogroup', { name: 'Background' })
      .getByRole('radio', { name: 'Remove' })
      .click();
    await expect(page.locator('.foot-plan')).toContainText('Background');
    await shoot(page, 'panel-background');
  });

  test('plan with double rate', async ({ page }) => {
    await openApp(page, tape);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page
      .getByRole('combobox', { name: 'Scan' })
      .selectOption({ label: 'Double rate · 50 fps' });
    await expect(page.locator('.foot-plan')).toContainText('Deinterlace to 50 fps');
    await shoot(page, 'panel-scan');
  });

  test('plan with Stabilise, the band in the viewer', async ({ page }) => {
    await openApp(page, shaky);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    const stabilise = page.getByRole('checkbox', { name: 'Stabilise', exact: true });
    await stabilise.check();
    await stabilise.locator('../..').getByRole('button').click();
    await expect(page.locator('.crop-guide')).toHaveCount(1);
    await expect(page.locator('.foot-plan')).toHaveText(/^Stabilise/);
    await shoot(page, 'panel-stabilise');
  });

  test('plan with Dust & scratches on a photo with three marks', async ({ page }) => {
    await openApp(page, scratched);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByRole('checkbox', { name: 'Dust & scratches', exact: true }).check();
    await expect(page.locator('.fix-value[data-paints]')).toHaveText('3 marks');
    await expect(page.locator('.foot-plan')).toHaveText(/^Dust & scratches → /);
    await shoot(page, 'panel-dust');
  });

  test('plan with Dust & scratches that has to download its model', async ({ page }) => {
    await openApp(page, scratchedModel);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByRole('checkbox', { name: 'Dust & scratches', exact: true }).check();
    await expect(page.locator('.fix-value[data-paints]')).toHaveText('3 marks');
    await expect(page.locator('.foot-plan')).toHaveText(/^Dust & scratches → /);
    await expect(page.locator('.foot-cost')).toContainText('28 MB to download');
    await shoot(page, 'panel-dust-download');
  });

  test('painting Dust & scratches in the viewer', async ({ page }) => {
    await openApp(page, scratched);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByRole('checkbox', { name: 'Dust & scratches', exact: true }).check();
    await page.locator('.fix-value[data-paints]').click();
    await expect(page.getByRole('toolbar', { name: 'Dust & scratches' })).toBeVisible();
    await expect(page.locator('.mask-paint path')).toHaveCount(3);
    await page.mouse.move(0, 0);
    await expect(page.locator('.brush-ring')).toHaveCount(0);
    await shoot(page, 'viewer-paint');
  });

  test('plan with Noise at Light', async ({ page }) => {
    await openApp(page, strength);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    const noise = page.getByRole('checkbox', { name: 'Noise', exact: true });
    await noise.check();
    await noise.locator('../..').getByRole('button').click();
    await page
      .getByRole('radiogroup', { name: 'Strength' })
      .getByRole('radio', { name: 'Light' })
      .click();
    await expect(page.locator('.foot-plan')).toContainText('Noise (light)');
    await shoot(page, 'panel-strength');
  });

  test('Outputs tab with two ticked results', async ({ page }) => {
    await openApp(page, withOutputs);
    await outputsTab(page).click();
    await pick(page, 'Original');
    await pick(page, 'HAT-S ×4');
    await expect(page.getByText('2 of 4 chosen')).toBeVisible();
    await shoot(page, 'outputs-picked');
  });

  test('Compare with the slider', async ({ page }) => {
    await openApp(page, withOutputs);
    await outputsTab(page).click();
    await pick(page, 'Original');
    await pick(page, 'HAT-S ×4');
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    await expect(page.getByRole('slider')).toBeVisible();
    await shoot(page, 'compare-slider');
  });

  test('Compare with four boxes', async ({ page }) => {
    await openApp(page, withOutputs);
    await outputsTab(page).click();
    for (const label of ['Original', 'SPAN ×4', 'HAT-S ×4', 'RealESRGAN ×4'])
      await pick(page, label);
    await page.getByRole('button', { name: 'Compare these 4' }).click();
    await expect(
      page.getByRole('list', { name: 'Compared items' }).getByRole('listitem'),
    ).toHaveCount(4);
    await shoot(page, 'compare-boxes');
  });

  test('Advanced settings on a Mac', async ({ page }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.locator('.foot-plan').click();
    await page.locator('#device').scrollIntoViewIfNeeded();
    await shoot(page, 'panel-plan-sheet-mac');
  });

  test('Advanced settings with two GPU routes', async ({ page }) => {
    await openApp(page, { ...windows, worker: 'ready' });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.locator('.foot-plan').click();
    await page.locator('#device').scrollIntoViewIfNeeded();
    await shoot(page, 'panel-plan-sheet-windows');
  });

  test('Advanced settings for video with interpolation', async ({ page }) => {
    await openApp(page, video);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await shoot(page, 'panel-video');
    await page.locator('.foot-output').click();
    await shoot(page, 'panel-output-sheet-video');
  });

  test('Frames at 2× slower', async ({ page }) => {
    await openApp(page, slow);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await expect(page.locator('#frame-rate')).toHaveValue('slow:x2');
    await shoot(page, 'panel-slow-motion');
  });

  test('download waiting for Edit start', async ({ page }) => {
    await page.clock.install();
    await openApp(page, {
      ...upscale,
      task: 'edit',
      worker: 'ready',
      features: ['image_edit'],
      editRuntime: '/fixture/sd-cli',
      settings: { edit_model_id: 'flux2_klein_4b_q4_0' },
      installed: [],
      concurrentDownloads: true,
    });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByLabel('What to change').fill('Add snow');
    await page.getByRole('button', { name: 'Download 5.3 GB, then edit' }).click();
    for (let pct = 5; pct <= 9; pct += 1) {
      await progressDownload(page, 'flux2_klein_4b_q4_0', pct);
      if (pct < 9) await page.clock.runFor(2_000);
    }
    await expect(page.locator('.status-detail')).toContainText('then edit');
    await shoot(page, 'download-waiting');
  });

  test('several downloads running for Blur and Noise', async ({ page }) => {
    await page.clock.install();
    await openApp(page, { ...ready, installed: [], concurrentDownloads: true });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByRole('checkbox', { name: 'Blur', exact: true }).check();
    await page.getByRole('checkbox', { name: 'Noise', exact: true }).check();
    await page.getByRole('button', { name: /^Download .*, then upscale$/ }).click();
    await progressDownload(page, 'nafnet_gopro_deblur', 80);
    await progressDownload(page, 'denoise_realplksr_1x', 12);
    await progressDownload(page, 'localsr_quick_x4_v1', 33);
    await expect(page.locator('.status-copy strong')).toContainText('Downloading 3 models');
    await shoot(page, 'download-several');
  });

  test('download interrupted in the status bar', async ({ page }) => {
    await openApp(page, {
      ...upscale,
      task: 'edit',
      worker: 'ready',
      features: ['image_edit'],
      editRuntime: '/fixture/sd-cli',
      settings: { edit_model_id: 'flux2_klein_4b_q4_0' },
      installed: [],
      concurrentDownloads: true,
    });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByLabel('What to change').fill('Add snow');
    await page.getByRole('button', { name: 'Download 5.3 GB, then edit' }).click();
    await progressDownload(page, 'flux2_klein_4b_q4_0', 24);
    await failDownload(page, 'flux2_klein_4b_q4_0', 'network timeout', { reason: 'offline' });
    await expect(page.locator('.status-copy strong')).toHaveText('Download interrupted');
    await shoot(page, 'download-failed');
  });

  test('library downloads tray with active, waiting and failed items', async ({ page }) => {
    await page.clock.install();
    await openApp(page, { ...ready, installed: [], concurrentDownloads: true });
    await emit(page, 'native-menu-action', 'view.models');
    const library = page.getByRole('dialog', { name: 'Model library' });
    await library.getByRole('button', { name: /^Upscale photos/ }).click();
    for (const label of [
      /HAT-L ×4 ImageNet/,
      /SwinIR ×4 Real/,
      /HAT-S ×4/,
      /Real-ESRGAN ×4/,
      /RealPLKSR ×4 NomosWebPhoto/,
    ]) {
      await library.getByRole('button', { name: label }).click();
      await library.getByRole('button', { name: /^Download / }).click();
    }
    for (let pct = 5; pct <= 10; pct += 1) {
      await progressDownload(page, 'hat_l_x4_imagenet', pct);
      if (pct < 10) await page.clock.runFor(2_000);
    }
    await progressDownload(page, 'swinir_m_real_x4_gan', 30);
    await failDownload(page, 'swinir_m_real_x4_gan', 'network timeout', { reason: 'offline' });
    await progressDownload(page, 'hat_s_x4', 45);
    await expect(library.getByRole('region', { name: 'Model downloads' })).toContainText(
      'Downloads · 3 active · 1 waiting',
    );
    await shoot(page, 'library-downloads');
  });

  test('Edit Expert settings on Windows CUDA', async ({ page }) => {
    await openApp(page, {
      devices: 'cuda',
      platform: 'Win32',
      photos: 1,
      task: 'edit',
      worker: 'ready',
      editRuntime: '/fixture/sd-cli',
      features: ['image_edit', 'edit_placement', 'edit_expert'],
      installed: ['flux2_klein_4b_q4_0'],
      settings: {
        edit_model_id: 'flux2_klein_4b_q4_0',
        edit_max_dimension: 1024,
        edit_vram_limit_mb: 11264,
        edit_allow_any_size: true,
      },
    });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page.getByRole('button', { name: /^Advanced/ }).click();
    await page.locator('#edit-expert').click();
    await page.locator('#edit-expert-reset').scrollIntoViewIfNeeded();
    await shoot(page, 'edit-expert-windows');
  });

  test('Recipes library with the list and a recipe', async ({ page }) => {
    await openApp(page, {
      ...ready,
      recipes: [
        { ...recipeFixture, id: 'a', name: 'Phone photos' },
        { ...recipeFixture, id: 'b', name: 'Old slides', output_scale: 2 },
        { ...recipeFixture, id: 'c', name: 'Web banners', output_scale: 2 },
      ],
      installed: [...(ready.installed ?? []), 'span_photo_x4'],
      settings: { ...ready.settings, recipe_pins: ['a'] },
    });
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Recipes', exact: true }).click();
    await page.getByRole('button', { name: 'Browse recipes…' }).click();
    await page.getByRole('dialog', { name: 'Recipes' }).locator('.row').first().click();
    await shoot(page, 'recipes-library');
  });

  test('Recipes library with a workflow card', async ({ page }) => {
    await openApp(page, ready);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Recipes', exact: true }).click();
    await page.getByRole('button', { name: 'Browse recipes…' }).click();
    const sheet = page.getByRole('dialog', { name: 'Recipes' });
    await sheet.locator('.row').filter({ hasText: 'Web image (JPEG)' }).click();
    await shoot(page, 'recipes-workflow-card');
  });
  const stepsScenario = {
    ...ready,
    photos: 1,
    pairs: true,
    settings: {
      ...ready.settings,
      output_scale: 4,
      fix_stages: [
        { fix: 'jpeg', model_id: 'fbcnn_color' },
        { fix: 'colour', model_id: 'ddcolor_modelscope' },
        { fix: 'noise', model_id: 'scunet_color_real_gan' },
      ],
    },
  };
  const stepKinds = [
    { kind: 'restore', fix: 'jpeg', model_id: 'fbcnn_color' },
    { kind: 'restore', fix: 'colour', model_id: 'ddcolor_modelscope' },
    { kind: 'restore', fix: 'noise', model_id: 'scunet_color_real_gan' },
    { kind: 'upscale', model_id: 'span_photo_x4' },
  ];

  test('the viewer during step 2 of 4, with the plan line marked', async ({ page }) => {
    await openApp(page, {
      ...stepsScenario,
      running: true,
      jobSteps: stepKinds.map((step, index) => ({
        ...step,
        state: index === 0 ? 'done' : index === 1 ? 'running' : 'waiting',
      })),
    });
    await expect(page.locator('.status-bar strong')).toContainText('Step 2 of 4 · Colour');
    await showPane(page, 'preview');
    await expect(page.locator('.canvas-well .step-label')).toHaveText('Step 2 of 4 · Colour');
    await shoot(page, 'steps-running');
  });

  test('the step chips above a finished result', async ({ page }) => {
    await openApp(page, {
      ...stepsScenario,
      finished: true,
      outputs: { 1: [{ id: 'job-steps', label: 'SPAN ×4', quality: 'Quick', seconds: 40 }] },
      lastSteps: {
        job_id: 'job-steps',
        media_id: 'photo-1',
        steps: stepKinds.map((step, index) => ({ ...step, state: 'done', kept: index < 3 })),
      },
    });
    await showPane(page, 'preview');
    const chips = page.getByRole('group', { name: 'Result after each step' });
    await expect(chips.getByRole('button')).toHaveText(['JPEG', 'Colour', 'Noise', '4×']);
    await shoot(page, 'steps-chips');
  });
  const patchScenario = {
    ...ready,
    features: [...(ready.features ?? []), 'patch_preview'],
    installed: ['localsr_quick_x4_v1', 'denoise_realplksr_1x', 'scunet_color_real_psnr'],
    settings: {
      ...ready.settings,
      output_scale: 4,
      fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
      fixes: ['noise'],
    },
  };
  const finishedPatch = async (page: Page) => {
    await page.getByRole('button', { name: 'Not now' }).click();
    await showPane(page, 'preview');
    await page.getByRole('button', { name: 'Preview', exact: true }).click();
    await expect(
      page.getByRole('group', { name: 'Preview patch' }).getByRole('button', { name: '4×' }),
    ).toHaveAttribute('aria-pressed', 'true');
    await page.mouse.move(0, 0);
  };

  test('the preview patch with its tab, finished', async ({ page }) => {
    await openApp(page, patchScenario);
    await finishedPatch(page);
    await shoot(page, 'patch-ready');
  });

  test('the preview patch waiting for a click', async ({ page }) => {
    await openApp(page, {
      ...patchScenario,
      settings: { ...patchScenario.settings, preview_patch: 'manual' },
    });
    await page.getByRole('button', { name: 'Not now' }).click();
    await showPane(page, 'preview');
    await page.getByRole('button', { name: 'Preview', exact: true }).click();
    await expect(page.getByRole('button', { name: /^Update preview/ })).toBeVisible();
    await page.mouse.move(0, 0);
    await shoot(page, 'patch-update');
  });

  test('the preview patch comparing two models', async ({ page }) => {
    await openApp(page, patchScenario);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    await page
      .getByRole('checkbox', { name: 'Noise', exact: true })
      .locator('../..')
      .locator('.fix-value')
      .click();
    await page
      .getByRole('group', { name: 'Noise options' })
      .getByRole('button', { name: 'Change the noise fix model' })
      .click();
    const library = page.getByRole('dialog', { name: 'Model library' });
    await library.getByRole('button', { name: /^SCUNet Color/ }).click();
    await library.getByRole('button', { name: 'Try on this photo' }).click();
    await page.keyboard.press('Escape');
    await showPane(page, 'preview');
    await expect(page.getByRole('group', { name: 'Compare models' })).toBeVisible();
    await expect(
      page.getByRole('group', { name: 'Preview patch' }).getByRole('button', { name: '4×' }),
    ).toHaveAttribute('aria-pressed', 'true');
    await page.mouse.move(0, 0);
    await shoot(page, 'patch-two-models');
  });
});
