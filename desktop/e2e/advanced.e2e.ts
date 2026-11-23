import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { answer, hostCalls } from './fake-host';
import { upscale, video, windows } from './fixtures';

async function openAdvanced(page: Page): Promise<void> {
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: /^Advanced/ }).click();
  await expect(page.locator('#device')).toBeVisible();
}

const optionTexts = (page: Page, selector: string) =>
  page.locator(`${selector} option`).evaluateAll((nodes) => nodes.map((node) => node.textContent));

async function lastSaved(page: Page): Promise<Record<string, unknown>> {
  const call = (await hostCalls(page, 'save_settings')).at(-1);
  return (call?.args as { settings: Record<string, unknown> }).settings;
}

test.describe('Advanced settings: hardware', () => {
  test('a Mac lists the Neural Engine, the Apple GPU and the CPU', async ({ page }) => {
    await openApp(page, upscale);
    await openAdvanced(page);
    expect(await optionTexts(page, '#device')).toEqual(['Neural Engine', 'Apple GPU', 'CPU']);
    await expect(page.locator('#device optgroup')).toHaveCount(0);
  });

  test('a saved GPU that is gone shows as the CPU, and Start sends the CPU', async ({ page }) => {
    await openApp(page, {
      ...upscale,
      worker: 'ready',
      settings: { ...upscale.settings, device_id: 'cuda:7' },
    });
    await openAdvanced(page);
    await expect(page.locator('#device')).toHaveValue('cpu');
    await page.getByRole('button', { name: 'Upscale selected' }).click();
    const start = (await hostCalls(page, 'start_jobs')).at(-1);
    expect((start?.args as { input: { device: string } }).input.device).toBe('cpu');
  });

  test('one GPU reached by two routes is grouped under its name', async ({ page }) => {
    await openApp(page, windows);
    await openAdvanced(page);
    const groups = await page.locator('#device').evaluate((select) =>
      [...select.children].map((child) => ({
        tag: child.tagName,
        label: child.getAttribute('label') ?? child.textContent,
        options: [...child.querySelectorAll('option')].map((option) => option.textContent),
      })),
    );
    expect(groups).toEqual([
      { tag: 'OPTION', label: 'Intel Core i7-8550U', options: [] },
      {
        tag: 'OPTGROUP',
        label: 'Intel UHD Graphics 620',
        options: ['Intel UHD Graphics 620 · DirectML', 'Intel UHD Graphics 620 · OpenVINO'],
      },
      { tag: 'OPTION', label: 'NVIDIA GeForce RTX 3060', options: [] },
    ]);
    await expect(page.locator('#device')).toHaveValue('directml:0');
  });

  test('choosing a route saves that device', async ({ page }) => {
    await openApp(page, windows);
    await openAdvanced(page);
    await page.locator('#device').selectOption('openvino:GPU');
    await expect.poll(async () => (await lastSaved(page)).device_id).toBe('openvino:GPU');
    await expect(page.locator('#device')).toHaveValue('openvino:GPU');
  });
});

test.describe('Advanced settings: frame interpolation', () => {
  test('lists the frame rates and shows the order only when interpolating', async ({ page }) => {
    await openApp(page, video);
    await openAdvanced(page);
    expect(await optionTexts(page, '#frame-rate')).toEqual([
      'Original',
      '2× · interpolated',
      '4× · interpolated',
      '60 fps · interpolated',
      '120 fps · interpolated',
    ]);
    await expect(page.locator('#frame-rate')).toHaveValue('x2');
    expect(await optionTexts(page, '#interpolation-order')).toEqual([
      'After upscaling · faster',
      'Before upscaling',
    ]);

    await page.locator('#frame-rate').selectOption('off');
    await expect(page.locator('#interpolation-order')).toHaveCount(0);
    await page.locator('#frame-rate').selectOption('fps60');
    await expect(page.locator('#interpolation-order')).toHaveValue('after_upscale');
    await page.locator('#interpolation-order').selectOption('before_upscale');
    await expect
      .poll(async () => {
        const settings = await lastSaved(page);
        return [settings.video_interpolation, settings.video_interpolation_order];
      })
      .toEqual(['fps60', 'before_upscale']);
  });

  test('an engine without interpolation leaves the frame rate disabled', async ({ page }) => {
    await openApp(page, { ...video, features: [] });
    await openAdvanced(page);
    await expect(page.locator('#frame-rate')).toBeDisabled();
  });
});

test('chooses an external FFmpeg through the host and displays the chosen path', async ({
  page,
}) => {
  await openApp(page, {
    ...video,
    settings: { ...video.settings, external_ffmpeg_path: '/old/ffmpeg' },
  });
  await openAdvanced(page);
  await answer(page, 'choose_external_ffmpeg', '/chosen/ffmpeg');
  // The path is text inside the field, as in Save to; it cannot be typed.
  const choose = page.getByRole('button', { name: 'Choose FFmpeg', exact: true });
  const field = choose.locator('xpath=..');
  await expect(field).toContainText('/old/ffmpeg');
  await expect(field.locator('input')).toHaveCount(0);
  await choose.click();
  await expect(field).toContainText('/chosen/ffmpeg');
  expect((await hostCalls(page, 'choose_external_ffmpeg')).at(-1)?.args).toEqual({
    current: '/old/ffmpeg',
  });
  await expect
    .poll(async () => (await lastSaved(page)).external_ffmpeg_path)
    .toBe('/chosen/ffmpeg');
});

test('chooses the editing runtime through the host and sends that path to Start Edit', async ({
  page,
}) => {
  await openApp(page, {
    ...upscale,
    task: 'edit',
    worker: 'ready',
    features: ['image_edit'],
    settings: { edit_model_id: 'flux2_klein_4b_q4_0' },
    installed: ['flux2_klein_4b_q4_0'],
  });
  await showPane(page, 'enhance');
  await answer(page, 'choose_edit_runtime', '/chosen/sd-cli');
  await page.getByRole('button', { name: 'Choose runtime…' }).click();
  await expect(page.getByRole('button', { name: 'Choose runtime…' })).toHaveCount(0);
  expect((await hostCalls(page, 'choose_edit_runtime')).length).toBe(1);
  await page.getByLabel('What to change').fill('Add snow');
  await page.getByRole('button', { name: 'Edit selected' }).click();
  const start = (await hostCalls(page, 'start_edit')).at(-1);
  expect((start?.args as { input: { runtime_path: string } }).input.runtime_path).toBe(
    '/chosen/sd-cli',
  );
});

test('Run at low priority stays clickable while a job runs and uses set_preference', async ({
  page,
}) => {
  await openApp(page, { ...upscale, running: true });
  await emit(page, 'native-menu-action', 'app.settings');
  const settings = page.getByRole('dialog', { name: 'Settings' });
  await settings.getByRole('tab', { name: 'Performance' }).click();
  const lowPriority = settings.getByRole('switch', { name: 'Run at low priority' });
  await expect(lowPriority).not.toBeChecked();
  await expect(lowPriority).toBeEnabled();
  await lowPriority.click();
  await expect(lowPriority).toBeChecked();
  expect((await hostCalls(page, 'set_preference')).at(-1)?.args).toEqual({
    name: 'low_priority',
    value: true,
  });
  expect((await hostCalls(page, 'save_settings')).length).toBe(0);
});

test.describe('Video audio cleanup', () => {
  test('toggles Clean up audio, shows its help tip, and starts the job with it enabled', async ({
    page,
  }) => {
    await openApp(page, {
      ...video,
      worker: 'ready',
      installed: [...(video.installed ?? []), 'deepfilternet3', 'dpdfnet8_48khz_hr'],
      settings: { ...video.settings, video_interpolation: 'off' },
    });
    await showPane(page, 'enhance');
    const audio = page.getByRole('region', { name: 'Audio' });
    const checkbox = audio.getByRole('checkbox', { name: 'Clean up audio' });
    await expect(checkbox).not.toBeChecked();
    await expect(checkbox).toBeEnabled();

    await audio.getByRole('button', { name: 'About Clean up audio' }).click();
    await expect(
      page.getByText(/Removes background noise such as hum, wind and hiss from speech/i),
    ).toBeVisible();

    await checkbox.check();
    await expect.poll(async () => (await lastSaved(page)).video_clean_audio).toBe(true);
    await expect(page.getByRole('list', { name: 'Stages' })).toContainText('DeepFilterNet3');

    const picker = audio.getByLabel('Audio model');
    await expect(picker).toHaveValue('deepfilternet3');
    await expect(audio).toContainText('Speech noise removal');

    await picker.selectOption('dpdfnet8_48khz_hr');
    await expect
      .poll(async () => (await lastSaved(page)).video_audio_model)
      .toBe('dpdfnet8_48khz_hr');
    await expect(audio).toContainText('Stronger speech noise removal');
    await expect(page.getByRole('list', { name: 'Stages' })).toContainText('DPDFNet');

    await page.getByRole('button', { name: 'Start selected video' }).click();
    await expect
      .poll(
        async () =>
          ((await hostCalls(page, 'start_jobs')).at(-1)?.args as {
            input: { video_clean_audio?: boolean; video_audio_model?: string };
          })?.input,
      )
      .toMatchObject({ video_clean_audio: true, video_audio_model: 'dpdfnet8_48khz_hr' });
  });

  test('downloads the selected audio model on Start when missing', async ({ page }) => {
    await openApp(page, {
      ...video,
      worker: 'ready',
      installed: [...(video.installed ?? []), 'deepfilternet3'],
      settings: { ...video.settings, video_interpolation: 'off' },
    });
    await showPane(page, 'enhance');
    const audio = page.getByRole('region', { name: 'Audio' });
    await audio.getByRole('checkbox', { name: 'Clean up audio' }).check();
    await audio.getByLabel('Audio model').selectOption('dpdfnet8_48khz_hr');

    await page.getByRole('button', { name: /Download .* start video/i }).click();
    await expect
      .poll(
        async () =>
          ((await hostCalls(page, 'download_model')).at(-1)?.args as {
            modelId?: string;
          })?.modelId,
      )
      .toBe('dpdfnet8_48khz_hr');
  });

  test('disables Clean up audio with a note when a video has no audio track', async ({ page }) => {
    await openApp(page, { ...video, videoHasAudio: false });
    await showPane(page, 'enhance');
    const audio = page.getByRole('region', { name: 'Audio' });
    const checkbox = audio.getByRole('checkbox', { name: 'Clean up audio' });
    await expect(checkbox).toBeDisabled();
    await expect(audio).toContainText('This video has no audio track.');
  });
});
