import { expect, test, type Locator, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { emit, hostCalls } from './fake-host';
import { ready, video } from './fixtures';

// The preview patch (section 3 of docs/design/previews-and-recipes.md) against the fake host:
// the Preview button, the request the app sends, the tab with its chips, moving it, the states
// of the host, the Settings row, the fix popover link and the two-model comparison.

const QUICK = 'localsr_quick_x4_v1';
const NOISE = 'denoise_realplksr_1x';
const FEATURES = [...(ready.features ?? []), 'patch_preview'];
const NOISE_STAGES = [{ fix: 'noise', model_id: NOISE }];

/** One photo, Noise ticked and 4x, every model installed. */
const patchScenario = {
  ...ready,
  features: FEATURES,
  installed: [QUICK, NOISE, 'scunet_color_real_psnr'],
  settings: {
    ...ready.settings,
    output_scale: 4,
    fix_stages: NOISE_STAGES,
    fixes: ['noise'],
  },
};

const tabKey = (browserName: string) =>
  browserName === 'webkit' && process.platform === 'darwin' ? 'Alt+Tab' : 'Tab';

const previewButton = (page: Page) => page.getByRole('button', { name: 'Preview', exact: true });
const patchTab = (page: Page) => page.getByRole('group', { name: 'Preview patch' });
const chips = (page: Page) => patchTab(page).getByRole('group', { name: 'Preview steps' });
const frame = (page: Page) => page.getByTestId('patch-frame');
const fix = (page: Page, name: string) =>
  page.getByRole('group', { name: 'Fix' }).getByRole('checkbox', { name, exact: true });

type Region = { x: number; y: number; width: number; height: number };
type Request = {
  slot: string;
  region: Region;
  force: boolean;
  replace: { slot: string; fix: string; model_id: string } | null;
  input: { fix_stages: { fix: string; model_id: string }[]; output_scale: number };
};

const requests = async (page: Page, slot = 'a') =>
  (await hostCalls(page, 'request_patch_preview'))
    .map((call) => call.args as Request)
    .filter((args) => args.slot === slot);

async function box(locator: Locator) {
  const rect = await locator.boundingBox();
  if (!rect) throw new Error('missing element');
  return rect;
}

async function turnOn(page: Page) {
  await showPane(page, 'preview');
  await previewButton(page).click();
  await expect(frame(page)).toBeVisible();
}

test.describe('preview patch', () => {
  test('Preview turns the patch on, saves it and it stays on', async ({ page }) => {
    await openApp(page, patchScenario);
    await showPane(page, 'preview');
    const button = previewButton(page);
    await expect(button).toHaveAttribute('aria-pressed', 'false');
    await expect(button).toHaveAttribute(
      'title',
      'Show what the ticked fixes do on a part of this photo',
    );
    await expect(patchTab(page)).toHaveCount(0);
    await button.click();
    await expect(button).toHaveAttribute('aria-pressed', 'true');
    await expect(patchTab(page)).toBeVisible();
    await expect(frame(page)).toBeVisible();
    expect(await hostCalls(page, 'set_preference')).toContainEqual(
      expect.objectContaining({ args: { name: 'preview_patch_shown', value: true } }),
    );
    // The comparison view became Original.
    await expect(
      page.getByRole('radiogroup', { name: 'Comparison view' }).getByRole('radio'),
    ).toHaveCount(0);
    // Other work in the app does not turn it off.
    await showPane(page, 'enhance');
    await fix(page, 'Blur').check();
    await fix(page, 'Blur').uncheck();
    await showPane(page, 'preview');
    await expect(button).toHaveAttribute('aria-pressed', 'true');
    await expect(patchTab(page)).toBeVisible();
    await button.click();
    await expect(button).toHaveAttribute('aria-pressed', 'false');
    await expect(patchTab(page)).toHaveCount(0);
  });

  test('the frame lies over the photo at the region and the tab stays on the canvas', async ({
    page,
  }) => {
    await openApp(page, patchScenario);
    await turnOn(page);
    await expect.poll(async () => (await requests(page)).length).toBe(1);
    const [first] = await requests(page);
    expect(first.region.width).toBe(384);
    expect(first.region.height).toBe(384);
    expect(first.region.x % 16).toBe(0);
    expect(first.region.y % 16).toBe(0);

    const photo = await box(page.locator('.image-stage .source-image').first());
    const square = await box(frame(page));
    const scale = photo.width / 1600;
    expect(Math.abs(square.x - (photo.x + first.region.x * scale))).toBeLessThanOrEqual(1);
    expect(Math.abs(square.y - (photo.y + first.region.y * scale))).toBeLessThanOrEqual(1);
    expect(Math.abs(square.width - 384 * scale)).toBeLessThanOrEqual(1);

    const well = await box(page.locator('.canvas-well'));
    const tab = await box(patchTab(page));
    expect(tab.x).toBeGreaterThanOrEqual(well.x - 1);
    expect(tab.y).toBeGreaterThanOrEqual(well.y - 1);
    expect(tab.x + tab.width).toBeLessThanOrEqual(well.x + well.width + 1);
    expect(tab.y + tab.height).toBeLessThanOrEqual(well.y + well.height + 1);
  });

  test('with the frame at the top edge the tab is below it', async ({ page }) => {
    await openApp(page, patchScenario);
    await turnOn(page);
    await patchTab(page).focus();
    for (let i = 0; i < 12; i++) await page.keyboard.press('Shift+ArrowUp');
    await expect.poll(async () => (await requests(page)).at(-1)?.region.y).toBe(0);
    await expect(frame(page)).toBeVisible();
    const square = await box(frame(page));
    const tab = await box(patchTab(page));
    const well = await box(page.locator('.canvas-well'));
    expect(tab.y).toBeGreaterThanOrEqual(well.y - 1);
    // Above the frame there is no room, so the tab hangs from its lower edge.
    expect(Math.abs(tab.y - (square.y + square.height))).toBeLessThanOrEqual(1);
  });

  test('one request goes out 400 ms after the last settings change', async ({ page }) => {
    await openApp(page, patchScenario);
    await turnOn(page);
    await expect.poll(async () => (await requests(page)).length).toBe(1);
    await showPane(page, 'enhance');
    await fix(page, 'Blur').check();
    await fix(page, 'JPEG artifacts').check();
    // Nothing yet: the changes are collected.
    expect(await requests(page)).toHaveLength(1);
    await expect.poll(async () => (await requests(page)).length).toBe(2);
    await page.waitForTimeout(700);
    expect(await requests(page)).toHaveLength(2);
    const last = (await requests(page)).at(-1)!;
    expect(last.input.fix_stages.map((stage) => stage.fix).sort()).toEqual([
      'blur',
      'jpeg',
      'noise',
    ]);
    expect(last.force).toBe(false);
  });

  test('chips are Original and the steps, and the newest finished step shows', async ({ page }) => {
    await openApp(page, { ...patchScenario, patch: { delayMs: 250 } });
    await turnOn(page);
    const row = chips(page).getByRole('button');
    await expect(row).toHaveText(['Original', 'Noise', '4×']);
    // Running: the state text names the step with whole seconds.
    await expect(patchTab(page).getByRole('status')).toHaveText(/^Noise · \d+ s$/);
    await expect(chips(page).getByRole('button', { name: '4×' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(patchTab(page).getByRole('status')).toHaveText('');
    await expect(frame(page).locator('img')).toHaveCount(1);
    await expect(chips(page).getByRole('button', { name: 'Noise' })).toHaveAttribute(
      'title',
      /^After Noise · /,
    );
    const upscaled = await frame(page).locator('img').getAttribute('src');
    // A chip the user presses stays.
    await chips(page).getByRole('button', { name: 'Noise' }).click();
    await expect(chips(page).getByRole('button', { name: 'Noise' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(frame(page).locator('img')).not.toHaveAttribute('src', upscaled!);
    await chips(page).getByRole('button', { name: 'Original' }).click();
    await expect(frame(page).locator('img')).toHaveCount(0);
  });

  test('dragging the tab moves the patch and asks again 250 ms after release', async ({ page }) => {
    await openApp(page, patchScenario);
    await turnOn(page);
    await expect.poll(async () => (await requests(page)).length).toBe(1);
    const before = (await requests(page))[0].region;
    const grip = page.locator('.patch-grip');
    const from = await box(grip);
    const start = { x: from.x + from.width / 2, y: from.y + from.height / 2 };
    const photo = await box(page.locator('.image-stage .source-image').first());
    const scale = photo.width / 1600;
    const dx = Math.round(-96 * scale);
    const dy = Math.round(64 * scale);
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(start.x + dx / 2, start.y + dy / 2, { steps: 4 });
    await page.mouse.move(start.x + dx, start.y + dy, { steps: 4 });
    // While dragging the frame shows the original.
    await expect(frame(page).locator('img')).toHaveCount(0);
    await page.mouse.up();
    await expect.poll(async () => (await requests(page)).length).toBe(2);
    const after = (await requests(page))[1].region;
    expect(Math.abs(after.x - (before.x - 96))).toBeLessThanOrEqual(16);
    expect(Math.abs(after.y - (before.y + 64))).toBeLessThanOrEqual(16);
    expect(after.x % 16).toBe(0);
    expect(after.y % 16).toBe(0);
    const square = await box(frame(page));
    const now = await box(page.locator('.image-stage .source-image').first());
    expect(Math.abs(square.x - (now.x + after.x * scale))).toBeLessThanOrEqual(1);
  });

  test('keyboard only: Tab reaches the button, the tab and each chip; arrows move the patch', async ({
    page,
    browserName,
  }) => {
    await openApp(page, patchScenario);
    await turnOn(page);
    await expect(chips(page).getByRole('button', { name: '4×' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect.poll(async () => (await requests(page)).length).toBe(1);
    const key = tabKey(browserName);
    await previewButton(page).focus();
    await page.keyboard.press(key);
    await expect(patchTab(page)).toBeFocused();
    for (const name of ['Original', 'Noise', '4×']) {
      await page.keyboard.press(key);
      await expect(chips(page).getByRole('button', { name })).toBeFocused();
    }
    await patchTab(page).focus();
    const start = (await requests(page))[0].region;
    await page.keyboard.press('ArrowRight');
    await expect.poll(async () => (await requests(page)).length).toBe(2);
    const right = (await requests(page))[1].region;
    expect(right.x - start.x).toBe(32);
    expect(right.y).toBe(start.y);
    await page.keyboard.press('Shift+ArrowDown');
    await expect.poll(async () => (await requests(page)).length).toBe(3);
    const down = (await requests(page))[2].region;
    expect(down.y - right.y).toBe(128);
    expect(down.x).toBe(right.x);
    await expect(patchTab(page)).toBeFocused();
  });

  test('a job hides the patch and it returns when the job is done', async ({ page }) => {
    await openApp(page, { ...patchScenario, startRuns: true });
    await turnOn(page);
    await expect.poll(async () => (await requests(page)).length).toBe(1);
    await showPane(page, 'enhance');
    await page.locator('.status-actions button.primary.start').click();
    await showPane(page, 'preview');
    await expect(patchTab(page)).toHaveCount(0);
    await expect(frame(page)).toHaveCount(0);
    await expect(previewButton(page)).toBeDisabled();
    await expect(previewButton(page)).toHaveAttribute('title', 'Available when the job finishes.');
    await emit(page, 'worker-message', {
      type: 'job_completed',
      data: { job_id: 'job-photo-1', output_path: '/fixture/results/job-photo-1.png' },
    });
    await expect(previewButton(page)).toBeEnabled();
    await expect(previewButton(page)).toHaveAttribute('aria-pressed', 'true');
    await expect(patchTab(page)).toBeVisible();
    await expect(frame(page)).toBeVisible();
    await expect.poll(async () => (await requests(page)).length).toBe(2);
  });

  test('the host waits: Update preview asks again and forces the run', async ({ page }) => {
    await openApp(page, {
      ...patchScenario,
      settings: { ...patchScenario.settings, preview_patch: 'manual' },
    });
    await turnOn(page);
    const update = patchTab(page).getByRole('button', { name: /^Update preview/ });
    await expect(update).toHaveText('Update preview · about 12 s');
    // The frame is dashed while its picture is older than the settings.
    await expect(frame(page)).toHaveClass(/dashed/);
    expect((await requests(page)).at(-1)!.force).toBe(false);
    await update.click();
    await expect.poll(async () => (await requests(page)).at(-1)!.force).toBe(true);
    await expect(update).toHaveCount(0);
    await expect(chips(page).getByRole('button', { name: '4×' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(frame(page)).not.toHaveClass(/dashed/);
  });

  test('a missing model shows Download and preview', async ({ page }) => {
    await openApp(page, {
      ...patchScenario,
      installed: [QUICK],
      patch: { missing: 0 },
    });
    await turnOn(page);
    const noise = chips(page).getByRole('button', { name: 'Noise' });
    await expect(noise).toHaveAttribute('title', 'RealPLKSR Denoise is not downloaded.');
    const download = patchTab(page).getByRole('button', { name: /^Download .* and preview$/ });
    await expect(download).toBeVisible();
    await download.click();
    await expect
      .poll(async () => (await hostCalls(page, 'download_model')).map((call) => call.args))
      .toEqual([expect.objectContaining({ modelId: NOISE })]);
  });

  test('a failed preview says so and Try again asks once more', async ({ page }) => {
    await openApp(page, { ...patchScenario, patch: { fail: 'Out of memory' } });
    await turnOn(page);
    const state = patchTab(page).getByRole('status');
    await expect(state).toContainText('Preview failed.');
    await expect(state.getByText('Preview failed.')).toHaveAttribute('title', 'Out of memory');
    const before = (await requests(page)).length;
    await state.getByRole('button', { name: 'Try again' }).click();
    await expect.poll(async () => (await requests(page)).length).toBeGreaterThan(before);
  });

  test('Settings Off hides the button and the patch', async ({ page }) => {
    await openApp(page, patchScenario);
    await turnOn(page);
    await emit(page, 'native-menu-action', 'app.settings');
    const dialog = page.getByRole('dialog', { name: 'Settings' });
    await expect(dialog).toBeVisible();
    const row = dialog.getByLabel('Preview fixes on your photo');
    await expect(row).toHaveValue('auto');
    await expect(row.locator('option')).toHaveText([
      'Automatic',
      'Always update by itself',
      'Update when I click',
      'Off',
    ]);
    await row.selectOption('off');
    expect(await hostCalls(page, 'set_preference')).toContainEqual(
      expect.objectContaining({ args: { name: 'preview_patch', value: 'off' } }),
    );
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await showPane(page, 'preview');
    await expect(previewButton(page)).toHaveCount(0);
    await expect(patchTab(page)).toHaveCount(0);
  });

  test('See it on this photo from a fix popover turns the patch on at that fix', async ({
    page,
  }) => {
    await openApp(page, patchScenario);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    const trigger = fix(page, 'Noise').locator('../..').locator('.fix-value');
    await trigger.click();
    const pop = page.getByRole('group', { name: 'Noise options' });
    await pop.getByRole('button', { name: 'See it on this photo' }).click();
    await showPane(page, 'preview');
    await expect(previewButton(page)).toHaveAttribute('aria-pressed', 'true');
    await expect(patchTab(page)).toBeVisible();
    await expect(chips(page).getByRole('button', { name: 'Noise' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect.poll(async () => (await requests(page)).length).toBe(1);
  });

  test('Try on this photo shows A and B; X flips; Use B applies it; Done drops B', async ({
    page,
  }) => {
    await openApp(page, patchScenario);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: 'Not now' }).click();
    const openLibrary = async () => {
      await showPane(page, 'enhance');
      await fix(page, 'Noise').locator('../..').locator('.fix-value').click();
      await page
        .getByRole('group', { name: 'Noise options' })
        .getByRole('button', { name: 'Change the noise fix model' })
        .click();
      return page.getByRole('dialog', { name: 'Model library' });
    };
    const tryB = async () => {
      const library = await openLibrary();
      await library.getByRole('button', { name: /^SCUNet Color/ }).click();
      await library.getByRole('button', { name: 'Try on this photo' }).click();
      await expect(library).toHaveCount(0);
    };
    await tryB();
    await showPane(page, 'preview');
    await expect(previewButton(page)).toHaveAttribute('aria-pressed', 'true');
    const compare = patchTab(page).getByRole('group', { name: 'Compare models' });
    await expect(compare).toHaveAttribute('title', 'Press X to flip');
    const sides = compare.getByRole('button');
    await expect(sides).toHaveCount(2);
    await expect(sides.nth(0)).toHaveText(/^A\s*RealPLKSR Denoise/);
    await expect(sides.nth(1)).toHaveText(/^B\s*SCUNet Color/);
    await expect(sides.nth(1)).toHaveAttribute('aria-pressed', 'true');
    await expect
      .poll(async () => (await requests(page, 'b')).at(-1)?.replace)
      .toEqual({ slot: 'b', fix: 'noise', model_id: 'scunet_color_real_psnr' });
    expect(await requests(page, 'a')).not.toHaveLength(0);

    await page.keyboard.press('x');
    await expect(sides.nth(0)).toHaveAttribute('aria-pressed', 'true');
    await page.keyboard.press('x');
    await expect(sides.nth(1)).toHaveAttribute('aria-pressed', 'true');
    await sides.nth(0).click();
    await expect(sides.nth(0)).toHaveAttribute('aria-pressed', 'true');

    // Done drops the second row and keeps the patch.
    await patchTab(page).getByRole('button', { name: 'Done' }).click();
    await expect(compare).toHaveCount(0);
    await expect(patchTab(page)).toBeVisible();

    // Use B makes B the job's model for that fix.
    await tryB();
    await showPane(page, 'preview');
    await expect(compare).toBeVisible();
    await patchTab(page).getByRole('button', { name: 'Use B' }).click();
    await expect(compare).toHaveCount(0);
    await expect
      .poll(async () => {
        const saved = (await hostCalls(page, 'save_settings')).at(-1)?.args as
          { settings?: { fix_stages?: { fix: string; model_id: string }[] } } | undefined;
        return saved?.settings?.fix_stages;
      })
      .toEqual([{ fix: 'noise', model_id: 'scunet_color_real_psnr' }]);
  });

  test('a video shows its first frame with the This frame label', async ({ page }) => {
    await openApp(page, {
      ...video,
      worker: 'ready',
      features: FEATURES,
      installed: [QUICK, NOISE],
      settings: {
        ...video.settings,
        task: 'video',
        video_task: 'denoise',
        selected_video_model_id: 'frame_by_frame',
        selected_model_id: NOISE,
        output_scale: 1,
        fix_stages: NOISE_STAGES,
        fixes: ['noise'],
      },
    });
    await showPane(page, 'preview');
    await expect(previewButton(page)).toHaveAttribute(
      'title',
      'Show what the ticked fixes do on a part of this frame',
    );
    await previewButton(page).click();
    const label = patchTab(page).getByText('This frame', { exact: true });
    await expect(label).toBeVisible();
    await expect(label).toHaveAttribute(
      'title',
      'De-flicker, added frames and sound cannot be shown on one frame.',
    );
    await expect.poll(async () => (await requests(page)).length).toBe(1);
    expect((await requests(page))[0]).toMatchObject({ slot: 'a' });
  });
});
