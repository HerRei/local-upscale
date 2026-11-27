import { expect, test, type Page } from '@playwright/test';
import { contrastOnPage, openApp, showPane } from './app';
import { ready, video } from './fixtures';

const fix = (page: Page, name: string) =>
  page.getByRole('group', { name: 'Fix' }).getByRole('checkbox', { name, exact: true });
const value = (page: Page, name: string) => fix(page, name).locator('../..').locator('.fix-value');

test('photo rows stay put as fixes and Same size are chosen', async ({ page }) => {
  await openApp(page, ready);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  const body = page.locator('.inspector-scroll');
  const size = page.getByRole('radiogroup', { name: 'Size' });
  const rows = page.locator('.fix-row');
  const top = async () =>
    Promise.all((await rows.all()).map(async (row) => (await row.boundingBox())!.y));
  const initial = await top();
  const sizeTop = (await size.boundingBox())!.y;
  const bodyBox = (await body.boundingBox())!;
  const foot = page.locator('.panel-foot');
  let footTop = (await foot.boundingBox())!.y;
  if (page.viewportSize()!.height === 700)
    expect(sizeTop + (await size.boundingBox())!.height).toBeLessThanOrEqual(
      bodyBox.y + bodyBox.height,
    );
  for (const name of ['Blur', 'JPEG artifacts', 'Noise', 'Colour']) {
    await fix(page, name).check();
    await expect(page.locator('.foot-plan')).toContainText(
      name === 'JPEG artifacts' ? 'JPEG' : name,
    );
    const nextFootTop = (await foot.boundingBox())!.y;
    if (name === 'Blur') expect(nextFootTop).toBeLessThan(footTop);
    else expect(Math.abs(nextFootTop - footTop)).toBeLessThanOrEqual(0.5);
    footTop = nextFootTop;
    const current = await top();
    current.forEach((y, i) => expect(Math.abs(y - initial[i])).toBeLessThanOrEqual(0.5));
    expect(Math.abs((await size.boundingBox())!.y - sizeTop)).toBeLessThanOrEqual(0.5);
  }
  await page.getByRole('radio', { name: 'Same size' }).click();
  expect(Math.abs((await size.boundingBox())!.y - sizeTop)).toBeLessThanOrEqual(0.5);
  await expect(page.getByRole('radio', { name: 'Photo' })).toBeDisabled();
  const cut = await body
    .locator('.panel-key, .seg-inline button')
    .evaluateAll((els) =>
      els.filter((el) => el.scrollWidth > el.clientWidth).map((el) => el.textContent),
    );
  expect(cut).toEqual([]);
});

test('video scrolls above a visible foot and sheets dismiss with Escape', async ({ page }) => {
  await openApp(page, video);
  await showPane(page, 'enhance');
  const body = page.locator('.inspector-scroll');
  const foot = page.locator('.panel-foot');
  await expect(fix(page, 'De-flicker')).toBeVisible();
  const metrics = await body.evaluate((el) => ({
    scroll: el.scrollHeight,
    height: el.clientHeight,
    width: el.scrollWidth,
    client: el.clientWidth,
  }));
  if (page.viewportSize()!.height === 700) expect(metrics.scroll).toBeGreaterThan(metrics.height);
  expect(metrics.width).toBeLessThanOrEqual(metrics.client);
  const cut = await body
    .locator('.panel-key, .seg-inline button')
    .evaluateAll((els) =>
      els.filter((el) => el.scrollWidth > el.clientWidth).map((el) => el.textContent),
    );
  expect(cut).toEqual([]);
  await expect(foot).toBeInViewport();
  await page.locator('.foot-plan').click();
  const sheet = page.getByRole('group', { name: 'Plan' });
  await expect(sheet).toBeVisible();
  expect((await sheet.boundingBox())!.y).toBeLessThan((await foot.boundingBox())!.y);
  await page.keyboard.press('Escape');
  await expect(sheet).toHaveCount(0);
  await expect(page.locator('.foot-plan')).toBeFocused();
});

test('fix popover opens by keyboard and returns focus', async ({ page }) => {
  await openApp(page, ready);
  await showPane(page, 'enhance');
  await fix(page, 'Noise').check();
  const trigger = value(page, 'Noise');
  await trigger.focus();
  await page.keyboard.press('Enter');
  const pop = page.getByRole('group', { name: 'Noise options' });
  await expect(pop).toBeVisible();
  await expect(pop.getByRole('button', { name: 'Change the noise fix model' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(pop).toHaveCount(0);
});

test('loading and failed plans prevent a premature start', async ({ page }) => {
  await openApp(page, { ...ready, plan: { delayMs: 1500 } });
  await showPane(page, 'enhance');
  await expect(page.locator('.foot-plan')).toContainText('Working out the plan');
  await expect(page.locator('.foot-plan')).toContainText('Upscale');
  await page.evaluate(() =>
    (
      window as unknown as { __fakeHost: { setPlan: (plan: { delayMs: number }) => void } }
    ).__fakeHost.setPlan({ delayMs: 1500 }),
  );
  await fix(page, 'Noise').check();
  await expect(page.locator('.foot-plan')).toHaveAttribute('aria-busy', 'true');
  await expect(page.locator('.foot-plan')).toHaveClass(/dimmed/);
  await expect(page.locator('.foot-plan')).toContainText('Noise');
  await openApp(page, { ...ready, plan: 'error' });
  await showPane(page, 'enhance');
  await expect(page.getByText('Could not work out the plan.')).toBeVisible();
  await expect(page.locator('.status-actions button.primary.start')).toBeDisabled();
  await page.evaluate(() =>
    (
      window as unknown as { __fakeHost: { setPlan: (plan: undefined) => void } }
    ).__fakeHost.setPlan(undefined),
  );
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.foot-plan')).toContainText('Upscale');
});

test('LocalSR accent fills Start and checked fixes', async ({ page }) => {
  await openApp(page, ready);
  await showPane(page, 'enhance');
  const start = page.locator('.status-actions button.primary.start');
  await expect(start).toHaveCSS('background-color', 'rgb(58, 110, 165)');
  await expect(start).toHaveCSS('color', 'rgb(255, 255, 255)');
  expect(await contrastOnPage(start)).toBeGreaterThanOrEqual(4.5);
  await fix(page, 'Noise').check();
  await expect(fix(page, 'Noise')).toHaveCSS('background-color', 'rgb(58, 110, 165)');
});

test('a custom checkpoint needs a file in the foot', async ({ page }) => {
  await openApp(page, {
    ...ready,
    settings: { ...ready.settings, selected_model_id: '__custom__', custom_model_path: '' },
  });
  await showPane(page, 'enhance');
  await expect(
    page.locator('.panel-foot').getByRole('button', { name: 'Choose checkpoint…' }),
  ).toBeVisible();
  await expect(page.locator('.status-actions button.primary.start')).toBeDisabled();
});

test('a hand-picked temporal video model has its own controls', async ({ page }) => {
  await openApp(page, {
    ...video,
    worker: 'ready',
    videoEngines: ['spandrel_image', 'flashvsr'],
    installed: ['span_photo_x4', 'flashvsr_v11'],
    settings: {
      task: 'video',
      video_task: 'upscale',
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: 'span_photo_x4',
      output_scale: 4,
    },
  });
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Change the upscaler' }).click();
  const library = page.getByRole('dialog', { name: 'Model library' });
  const flash = library.getByRole('button', { name: /^FlashVSR v1\.1 — Labs/ });
  await flash.click();
  await flash.click();
  await library.getByRole('button', { name: 'Use for this job' }).click();
  await expect(page.getByLabel('Output resolution')).toBeVisible();
  await expect(page.getByLabel('FlashVSR memory')).toBeVisible();
  await expect(page.getByRole('group', { name: 'Fix' })).toHaveCount(0);
  await expect(page.locator('#frame-rate')).toHaveCount(0);
});

test('system accent and accessible palette overrides', async ({ page }) => {
  const yellow = { light: '#ffc726', dark: '#ffc726' };
  for (const [extra, expected] of [
    [{}, 'rgb(255, 199, 38)'],
    [{ colour_blind_palette: true }, 'rgb(58, 110, 165)'],
    [{ contrast: 'high' }, 'rgb(36, 74, 117)'],
  ] as const) {
    await openApp(page, {
      ...ready,
      systemAccent: yellow,
      settings: { ...ready.settings, appearance: 'light', accent: 'system', ...extra },
    });
    await showPane(page, 'enhance');
    const start = page.locator('.status-actions button.primary.start');
    await expect(start).toHaveCSS('background-color', expected);
    expect(await contrastOnPage(start)).toBeGreaterThanOrEqual(4.5);
    if (!Object.keys(extra).length) {
      await expect(start).toHaveCSS('color', 'rgb(0, 0, 0)');
      await expect(start).toHaveCSS('box-shadow', /inset/);
    }
  }
});

test('a low fix popover flips above its row inside the scroll body', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 700 });
  await openApp(page, ready);
  await showPane(page, 'enhance');
  await fix(page, 'Noise').check();
  const trigger = value(page, 'Noise');
  await trigger.click();
  const pop = page.getByRole('group', { name: 'Noise options' });
  const popBox = (await pop.boundingBox())!;
  const triggerBox = (await trigger.boundingBox())!;
  const bodyBox = (await page.locator('.inspector-scroll').boundingBox())!;
  expect(popBox.y + popBox.height).toBeLessThanOrEqual(triggerBox.y + 1);
  expect(popBox.y).toBeGreaterThanOrEqual(bodyBox.y);
  expect(popBox.x + popBox.width).toBeLessThanOrEqual(bodyBox.x + bodyBox.width + 1);
});
