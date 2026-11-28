import { expect, test, type Locator, type Page } from '@playwright/test';
import { contrastOnPage, openApp, showPane } from './app';
import { hostCalls } from './fake-host';
import { cutout, ready, scratched, shaky, slow, strength, tape, video } from './fixtures';

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
  await page.setViewportSize({ width: 1024, height: 658 });
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

test('the cost line shows a measured time, and nothing without one', async ({ page }) => {
  await openApp(page, { ...ready, estimateSeconds: 40 });
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  const cost = page.locator('.foot-cost');
  await expect(cost).toContainText('about 40 s');
  const first = await hostCalls(page, 'estimate_start');
  expect(first.length).toBeGreaterThan(0);
  const args = first[0].args as { mediaIds: string[]; input: { task: string } };
  expect(args.mediaIds.length).toBe(1);
  expect(args.input.task).toBe('upscale');

  // Two quick changes are one call, and the answer follows the last one.
  const before = (await hostCalls(page, 'estimate_start')).length;
  await page.getByRole('radio', { name: '3×' }).click();
  await page.getByRole('radio', { name: '2×' }).click();
  await expect(async () => {
    expect((await hostCalls(page, 'estimate_start')).length).toBe(before + 1);
  }).toPass();
  await page.waitForTimeout(700);
  expect((await hostCalls(page, 'estimate_start')).length).toBe(before + 1);
});

test('the cost line has no time when the host has no estimate', async ({ page }) => {
  await openApp(page, ready);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  await expect
    .poll(async () => (await hostCalls(page, 'estimate_start')).length)
    .toBeGreaterThan(0);
  await expect(page.locator('.panel-foot')).not.toContainText('about');
  await expect(page.locator('.foot-cost')).toHaveCount(0);
});

const background = (page: Page) => page.getByRole('radiogroup', { name: 'Background' });

test('Backgr.: Remove fills the plan, the Output line and the cost', async ({ page }) => {
  await openApp(page, cutout);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  // Clicking a row below the fold scrolls the body, so rows are measured inside its content.
  const top = (locator: Locator) =>
    locator.evaluate(
      (el) =>
        el.getBoundingClientRect().y -
        el.closest('.inspector-scroll')!.getBoundingClientRect().y +
        el.closest('.inspector-scroll')!.scrollTop,
    );
  const size = page.getByRole('radiogroup', { name: 'Size' });
  const quality = page.getByRole('group', { name: 'Quality' });
  const sizeTop = await top(size);
  const qualityTop = await top(quality);
  await background(page).getByRole('radio', { name: 'Remove' }).click();
  await expect(page.locator('.foot-plan')).toHaveText(/^Background/);
  await expect(page.locator('.foot-output-value')).toHaveText('PNG · transparent · Desktop');
  await expect(page.locator('.panel-foot')).toContainText('444 MB to download');
  expect(Math.abs((await top(size)) - sizeTop)).toBeLessThanOrEqual(0.5);
  expect(Math.abs((await top(quality)) - qualityTop)).toBeLessThanOrEqual(0.5);
});

test('Backgr.: arrow keys move between Keep and Remove, and both show a focus ring', async ({
  page,
}) => {
  await openApp(page, cutout);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  const keep = background(page).getByRole('radio', { name: 'Keep' });
  const remove = background(page).getByRole('radio', { name: 'Remove' });
  await keep.focus();
  await page.keyboard.press('ArrowRight');
  await expect(remove).toBeFocused();
  await expect(remove).toHaveAttribute('aria-checked', 'true');
  const ring = (locator: Locator) =>
    locator.evaluate((el) => {
      const style = getComputedStyle(el);
      const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
      return el.matches(':focus-visible') && (outline || style.boxShadow !== 'none');
    });
  expect(await ring(remove)).toBe(true);
  await page.keyboard.press('ArrowLeft');
  await expect(keep).toBeFocused();
  await expect(keep).toHaveAttribute('aria-checked', 'true');
  expect(await ring(keep)).toBe(true);
});

test('Backgr.: nothing is cut at 1024 × 700', async ({ page }) => {
  await openApp(page, cutout);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  await background(page).getByRole('radio', { name: 'Remove' }).click();
  const row = background(page).locator('xpath=ancestor::div[contains(@class, "panel-row")][1]');
  const cut = await row
    .locator('.panel-key, .panel-label, .seg-inline button')
    .evaluateAll((els) =>
      els.filter((el) => el.scrollWidth > el.clientWidth).map((el) => el.textContent),
    );
  expect(cut).toEqual([]);
});

const scan = (page: Page) => page.getByRole('combobox', { name: 'Scan' });

test('Scan: an interlaced video gets the row, a progressive one does not', async ({ page }) => {
  await openApp(page, tape);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  await expect(scan(page)).toBeVisible();
  await expect(scan(page).locator('option:checked')).toHaveText('Automatic · interlaced');
});

test('Scan: a video that says it is progressive has no row', async ({ page }) => {
  await openApp(page, { ...tape, videoFieldOrder: 'progressive' });
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  await expect(page.getByRole('radiogroup', { name: 'Content' })).toBeVisible();
  await expect(scan(page)).toHaveCount(0);
});

test('Scan: Double rate changes the head, the plan line and Frames', async ({ page }) => {
  await openApp(page, tape);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  await scan(page).selectOption({ label: 'Double rate · 50 fps' });
  await expect(page.locator('.panel-head-value.field-value')).toContainText('· 50 fps');
  await expect(page.locator('.foot-plan')).toHaveText(/^Deinterlace to 50 fps/);
  await expect(
    page.locator('#frame-rate option').filter({ hasText: '2× · 100 fps' }).first(),
  ).toBeAttached();
  const body = page.locator('.inspector-scroll');
  expect(await body.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  expect(await scan(page).evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
});

const strengthGroup = (page: Page) => page.getByRole('radiogroup', { name: 'Strength' });

async function openNoise(page: Page): Promise<void> {
  await openApp(page, strength);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  await fix(page, 'Noise').check();
  await value(page, 'Noise').click();
}

test('Strength: the three segments fit the popover and arrow keys move between them', async ({
  page,
}) => {
  await openNoise(page);
  const group = strengthGroup(page);
  await expect(group).toBeVisible();
  const cut = await group
    .locator('button')
    .evaluateAll((els) =>
      els.filter((el) => el.scrollWidth > el.clientWidth).map((el) => el.textContent),
    );
  expect(cut).toEqual([]);
  const light = group.getByRole('radio', { name: 'Light' });
  const medium = group.getByRole('radio', { name: 'Medium' });
  await group.getByRole('radio', { name: 'Full' }).focus();
  await page.keyboard.press('ArrowLeft');
  await expect(medium).toBeFocused();
  await page.keyboard.press('Home');
  await expect(light).toBeFocused();
  await expect(light).toHaveAttribute('aria-checked', 'true');
  await page.keyboard.press('End');
  await expect(group.getByRole('radio', { name: 'Full' })).toHaveAttribute('aria-checked', 'true');
});

test('Strength: choosing one moves no row', async ({ page }) => {
  await openNoise(page);
  const rows = page.locator('.fix-row');
  const tops = async () =>
    Promise.all((await rows.all()).map(async (row) => (await row.boundingBox())!.y));
  const before = await tops();
  await strengthGroup(page).getByRole('radio', { name: 'Light' }).click();
  await expect(value(page, 'Noise')).toHaveText(/^Light/);
  await expect(page.locator('.foot-plan')).toContainText('Noise (light)');
  await expect(strengthGroup(page)).toBeVisible();
  expect(await tops()).toEqual(before);
});

test("Slow motion: the option's text is not cut in the select and the head fits", async ({
  page,
}) => {
  await openApp(page, slow);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  const frames = page.locator('#frame-rate');
  await expect(frames).toHaveValue('slow:x2');
  await expect(frames.locator('option:checked')).toHaveText('2× slower · 24 s');
  const head = page.locator('.panel-head-value').last();
  await expect(head).toContainText(' · 24 s');
  const cut = await page
    .locator('.inspector-scroll')
    .locator('.panel-head-value, .panel-key, #frame-rate')
    .evaluateAll((els) =>
      els.filter((el) => el.scrollWidth > el.clientWidth).map((el) => el.textContent),
    );
  expect(cut).toEqual([]);
});

const crop = (page: Page) => page.locator('.crop-guide');

test('Stabilise: ticking moves no other row and changes no number in the head', async ({
  page,
}) => {
  await openApp(page, shaky);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  const group = page.getByRole('group', { name: 'Fix' });
  const names = () => group.locator('.fix-name').allTextContents();
  const headBefore = await page.locator('.panel-head-value.field-value').textContent();
  const before = await names();
  expect(before[0]).toBe('Stabilise');
  const tops = () =>
    group
      .locator('.fix-row')
      .evaluateAll((rows) => rows.map((row) => row.getBoundingClientRect().top));
  const topsBefore = await tops();
  await fix(page, 'Stabilise').check();
  await expect(value(page, 'Stabilise')).toHaveText('Medium');
  expect(await names()).toEqual(before);
  expect(await tops()).toEqual(topsBefore);
  await expect(page.locator('.panel-head-value.field-value')).toHaveText(headBefore ?? '');
  await expect(page.locator('.foot-plan')).toHaveText(/^Stabilise/);
});

test('Stabilise: the band shows while the popover is open and goes when it closes', async ({
  page,
}) => {
  await openApp(page, shaky);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  await fix(page, 'Stabilise').check();
  await expect(crop(page)).toHaveCount(0);
  await value(page, 'Stabilise').click();
  await expect(crop(page)).toHaveAttribute('data-crop', '0.05');
  await page
    .getByRole('radiogroup', { name: 'Smoothing' })
    .getByRole('radio', { name: 'Strong' })
    .click();
  await expect(crop(page)).toHaveAttribute('data-crop', '0.08');
  await page.keyboard.press('Escape');
  await expect(crop(page)).toHaveCount(0);
});

const bar = (page: Page) => page.getByRole('toolbar', { name: 'Dust & scratches' });
const well = (page: Page) => page.locator('.canvas-well');

/** Ticks Dust & scratches and opens painting on the open photo from the row's value. */
async function startPainting(page: Page, keyboard = false): Promise<void> {
  await openApp(page, scratched);
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Not now' }).click();
  await fix(page, 'Dust & scratches').check();
  await expect(value(page, 'Dust & scratches')).toHaveText('3 marks');
  if (keyboard) {
    await value(page, 'Dust & scratches').focus();
    await page.keyboard.press('Enter');
  } else {
    await value(page, 'Dust & scratches').click();
  }
  await expect(bar(page)).toBeVisible();
  await expect(page.locator('.mask-paint path')).toHaveCount(3);
}

/** How many screen pixels one source pixel of the 1600 px wide photo takes now. */
const screenScale = async (page: Page) =>
  (await page.locator('.image-stage').boundingBox())!.width / 1600;

test('Dust & scratches: the bar fits the viewer at 1024 × 700 and wraps instead of being cut', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 700 });
  await startPainting(page);
  const inside = async () => {
    const box = (await well(page).boundingBox())!;
    const parts = await bar(page)
      .locator('button, input, .help-tip, .paint-size-text')
      .evaluateAll((elements) =>
        elements.map((element) => {
          const r = element.getBoundingClientRect();
          return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
        }),
      );
    for (const part of parts) {
      expect(part.left).toBeGreaterThanOrEqual(box.x);
      expect(part.right).toBeLessThanOrEqual(box.x + box.width);
      expect(part.top).toBeGreaterThanOrEqual(box.y);
    }
    const cut = await bar(page)
      .locator('button, .paint-size-text')
      .evaluateAll(
        (elements) =>
          elements.filter((element) => element.scrollWidth > element.clientWidth).length,
      );
    expect(cut).toBe(0);
  };
  const barBox = (await bar(page).boundingBox())!;
  const wellBox = (await well(page).boundingBox())!;
  expect(barBox.y - wellBox.y).toBeCloseTo(8, 0);
  expect(Math.abs(barBox.x + barBox.width / 2 - (wellBox.x + wellBox.width / 2))).toBeLessThan(1);
  await inside();
  const oneLine = barBox.height;
  // A viewer narrower than the bar: it takes two lines and every control stays inside.
  await well(page).evaluate((element) => {
    (element as HTMLElement).style.width = '360px';
  });
  await expect
    .poll(async () => (await bar(page).boundingBox())!.height)
    .toBeGreaterThan(oneLine + 10);
  await inside();
});

/** The box of the pixels a stroke changed in the viewer, in page pixels. */
async function changedBox(page: Page, before: Buffer, after: Buffer, clip: Clip) {
  return page.evaluate(
    async ({ a, b, clip }) => {
      const pixels = async (data: string) => {
        const image = await createImageBitmap(await (await fetch(data)).blob());
        const canvas = new OffscreenCanvas(image.width, image.height);
        const context = canvas.getContext('2d')!;
        context.drawImage(image, 0, 0);
        return context.getImageData(0, 0, image.width, image.height);
      };
      const [one, two] = [await pixels(a), await pixels(b)];
      let [left, top, right, bottom] = [Infinity, Infinity, -Infinity, -Infinity];
      for (let y = 0; y < one.height; y += 1) {
        for (let x = 0; x < one.width; x += 1) {
          const i = (y * one.width + x) * 4;
          const change =
            Math.abs(one.data[i] - two.data[i]) +
            Math.abs(one.data[i + 1] - two.data[i + 1]) +
            Math.abs(one.data[i + 2] - two.data[i + 2]);
          if (change < 60) continue;
          left = Math.min(left, x);
          right = Math.max(right, x + 1);
          top = Math.min(top, y);
          bottom = Math.max(bottom, y + 1);
        }
      }
      return {
        left: clip.x + left,
        top: clip.y + top,
        right: clip.x + right,
        bottom: clip.y + bottom,
      };
    },
    {
      a: `data:image/png;base64,${before.toString('base64')}`,
      b: `data:image/png;base64,${after.toString('base64')}`,
      clip,
    },
  );
}

interface Clip {
  x: number;
  y: number;
  width: number;
  height: number;
}

async function strokeIsTinted(page: Page): Promise<void> {
  const wellBox = (await well(page).boundingBox())!;
  const scale = await screenScale(page);
  const size = Number(await bar(page).getByRole('slider', { name: 'Brush size' }).inputValue());
  const width = size * scale;
  const from = { x: wellBox.x + wellBox.width * 0.4, y: wellBox.y + wellBox.height * 0.62 };
  const to = { x: from.x + 90, y: from.y + 30 };
  const clip = {
    x: Math.round(from.x - width - 20),
    y: Math.round(from.y - width - 20),
    width: Math.round(to.x - from.x + 2 * width + 40),
    height: Math.round(to.y - from.y + 2 * width + 40),
  };
  // The pointer leaves the viewer, so the ring is not in either picture.
  await page.mouse.move(wellBox.x - 40, wellBox.y + 40);
  const before = await page.screenshot({ clip });
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 8 });
  await page.mouse.up();
  await page.mouse.move(wellBox.x - 40, wellBox.y + 40);
  const box = await changedBox(page, before, await page.screenshot({ clip }), clip);
  expect(Math.abs(box.left - (from.x - width / 2))).toBeLessThanOrEqual(2);
  expect(Math.abs(box.right - (to.x + width / 2))).toBeLessThanOrEqual(2);
  expect(Math.abs(box.top - (from.y - width / 2))).toBeLessThanOrEqual(2);
  expect(Math.abs(box.bottom - (to.y + width / 2))).toBeLessThanOrEqual(2);
}

test('Dust & scratches: a stroke is tinted where the pointer went, at 100 % and at 400 %', async ({
  page,
}) => {
  await startPainting(page);
  const zoom = page.getByRole('group', { name: 'Preview zoom' });
  for (let step = 0; step < 12; step += 1) await page.keyboard.press(']');
  await expect(zoom).toContainText('100%');
  await strokeIsTinted(page);
  await expect(page.locator('.mask-paint path')).toHaveCount(4);
  await well(page).hover();
  for (let step = 0; step < 7; step += 1)
    await zoom.getByRole('button', { name: 'Zoom in' }).click();
  await expect(zoom).toContainText(/^[−-]?\s*(4\d\d)%/);
  await strokeIsTinted(page);
  await expect(page.locator('.mask-paint path')).toHaveCount(5);
});

test('Dust & scratches: every control of the bar is reached by Tab and shows a focus ring', async ({
  page,
  browserName,
}) => {
  await startPainting(page, true);
  const names: string[] = [];
  for (let step = 0; step < 7; step += 1) {
    const focused = await page.evaluate(() => {
      const element = document.activeElement as HTMLElement | null;
      if (!element?.closest('.paint-bar')) return null;
      const style = getComputedStyle(element);
      return {
        name: element.getAttribute('aria-label') || element.textContent?.trim() || '',
        ring:
          element.matches(':focus-visible') &&
          ((style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 1) ||
            style.boxShadow !== 'none'),
      };
    });
    if (!focused) break;
    expect(focused.ring, focused.name).toBe(true);
    names.push(focused.name);
    // Safari moves focus to buttons with Option-Tab, as the Mac does by default.
    await page.keyboard.press(
      browserName === 'webkit' && process.platform === 'darwin' ? 'Alt+Tab' : 'Tab',
    );
  }
  // Undo has nothing to take yet, so it is off and out of the order.
  expect(names).toEqual([
    'Paint',
    'Erase',
    'Brush size',
    'Clear',
    'About Dust & scratches',
    'Done',
  ]);
});

test('Dust & scratches: the ring is as wide as the brush at the current zoom', async ({ page }) => {
  await startPainting(page);
  const ring = page.locator('.brush-ring');
  const slider = bar(page).getByRole('slider', { name: 'Brush size' });
  const wellBox = (await well(page).boundingBox())!;
  const check = async () => {
    await page.mouse.move(wellBox.x + wellBox.width / 2, wellBox.y + wellBox.height * 0.6);
    await expect(ring).toBeVisible();
    const expected = Number(await slider.inputValue()) * (await screenScale(page));
    const box = (await ring.boundingBox())!;
    expect(Math.abs(box.width - expected)).toBeLessThanOrEqual(1);
    expect(Math.abs(box.height - expected)).toBeLessThanOrEqual(1);
  };
  for (let step = 0; step < 8; step += 1) await page.keyboard.press(']');
  await check();
  const zoom = page.getByRole('group', { name: 'Preview zoom' });
  for (let step = 0; step < 3; step += 1)
    await zoom.getByRole('button', { name: 'Zoom in' }).click();
  await check();
  await page.keyboard.press('Escape');
  await expect(bar(page)).toHaveCount(0);
  await expect(ring).toHaveCount(0);
});
