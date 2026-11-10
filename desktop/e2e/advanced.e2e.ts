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

test('chooses an external FFmpeg through the host and displays a read-only path', async ({
  page,
}) => {
  await openApp(page, {
    ...video,
    settings: { ...video.settings, external_ffmpeg_path: '/old/ffmpeg' },
  });
  await openAdvanced(page);
  await answer(page, 'choose_external_ffmpeg', '/chosen/ffmpeg');
  const field = page.getByLabel('External FFmpeg', { exact: true });
  await expect(field).toHaveAttribute('readonly', '');
  await page.getByRole('button', { name: 'Choose…', exact: true }).click();
  await expect(field).toHaveValue('/chosen/ffmpeg');
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
