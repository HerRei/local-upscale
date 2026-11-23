import { expect, test, type Locator, type Page } from '@playwright/test';
import { openApp } from './app';
import { emit, hostCalls } from './fake-host';
import { ready, videoUnfinished, withLongDetails, withUnfinished } from './fixtures';

// A job that was stopped and kept: a photo with its finished tiles marked, a video
// compared only over its kept frames, and the details strip that never grows.

const box = async (locator: Locator) => (await locator.boundingBox())!;

async function settle(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map((image) => image.decode().catch(() => {})));
  });
}

/**
 * Which 15 fps frame each player shows, or 'edge' within 10 ms of a frame's edge, where two
 * files could round to different frames. Engines report a clip's length a little differently.
 */
const framesShown = (videos: Locator) =>
  videos.evaluateAll((nodes) =>
    nodes.map((node) => {
      const position = (node as HTMLVideoElement).currentTime * 15;
      const within = position - Math.floor(position);
      return within < 0.15 || within > 0.85 ? 'edge' : Math.floor(position);
    }),
  );

/** The marks lie exactly over the picture they belong to. */
async function expectOver(marks: Locator, stage: Locator): Promise<void> {
  await expect
    .poll(async () => {
      const [a, b] = [await box(marks), await box(stage)];
      return Math.max(
        Math.abs(a.x - b.x),
        Math.abs(a.y - b.y),
        Math.abs(a.width - b.width),
        Math.abs(a.height - b.height),
      );
    })
    .toBeLessThan(1);
}

/** Below 920 px the panes are pages; the Media button also carries the number of inputs. */
async function showPane(page: Page, pane: 'media' | 'preview'): Promise<void> {
  const button = page
    .getByRole('navigation', { name: 'Workspace' })
    .getByRole('button', { name: pane === 'media' ? /^Media/ : 'Viewer' });
  if (await button.isVisible()) await button.click();
}

const outputsTab = (page: Page) => page.getByRole('tab', { name: /^Outputs/ });
const pick = (page: Page, label: string, input = 'Alpine lake 1.jpg') =>
  page.getByRole('checkbox', { name: `${label}, ${input}` }).check();

test('stopping a photo with finished tiles asks whether to keep them', async ({ page }) => {
  await openApp(page, {
    ...ready,
    running: true,
    keepPartial: true,
    settings: { ...ready.settings, stopped_job_result: 'ask' },
  });
  await emit(page, 'worker-message', {
    type: 'progress',
    data: { job_id: 'job-photo-1', percentage: 30, completed_tiles: 10, total_tiles: 35 },
  });
  await expect(page.locator('.status-detail')).toContainText('10 of 35 tiles');
  await page.getByRole('button', { name: 'Cancel' }).click();
  const dialog = page.getByRole('dialog', { name: 'Stop Alpine lake 1.jpg?' });
  await expect(dialog).toContainText(
    '10 of 35 tiles are finished. If you keep it, the photo is saved at full size with the rest left as in the original.',
  );
  // The question fits its dialog at the smallest window too.
  await page.setViewportSize({ width: 880, height: 640 });
  const [inner, outer] = [await box(dialog.locator('.modal-message')), await box(dialog)];
  expect(inner.x).toBeGreaterThanOrEqual(outer.x);
  expect(inner.x + inner.width).toBeLessThanOrEqual(outer.x + outer.width);
  await dialog.getByRole('button', { name: 'Keep finished part' }).click();
  await expect(dialog).toHaveCount(0);
  await expect
    .poll(async () => (await hostCalls(page, 'cancel_jobs')).at(-1)?.args)
    .toEqual({ keepPartial: true });
});

test('a photo without a finished tile is stopped without a question', async ({ page }) => {
  await openApp(page, {
    ...ready,
    running: true,
    keepPartial: true,
    settings: { ...ready.settings, stopped_job_result: 'ask' },
  });
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect
    .poll(async () => (await hostCalls(page, 'cancel_jobs')).at(-1)?.args)
    .toEqual({ keepPartial: false });
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test.describe('an unfinished photo', () => {
  test('is marked in Outputs and in the preview, with its finished tiles on the mark', async ({
    page,
  }) => {
    await openApp(page, withUnfinished);
    await showPane(page, 'media');
    await outputsTab(page).click();
    await expect(page.locator('.outputs-list .media-row').nth(1)).toContainText(
      'Unfinished · 10 of 35 tiles',
    );
    await showPane(page, 'preview');
    const pane = page.locator('.preview-pane');
    const mark = pane.getByRole('button', {
      name: 'Unfinished · 10 of 35 tiles. Show the finished tiles',
    });
    await expect(mark).toBeVisible();
    const marks = pane.locator('.unfinished-tiles');
    const stage = pane.locator('.image-stage');
    await expectOver(marks, stage);
    // At rest only the line where the finished part ends; the rest is dimmed from the mark.
    await expect(marks.locator('.rest')).toHaveCSS('opacity', '0');
    await mark.hover();
    await expect(marks.locator('.rest')).toHaveCSS('opacity', '1');
    await pane.getByRole('application').hover({ position: { x: 300, y: 300 } });
    await expect(marks.locator('.rest')).toHaveCSS('opacity', '0');
    // The keyboard reaches the mark; Space keeps the tiles shown.
    await mark.focus();
    await page.keyboard.press('Space');
    await expect(mark).toHaveAttribute('aria-pressed', 'true');
    await expect(marks.locator('.rest')).toHaveCSS('opacity', '1');

    // Zoomed and panned, the marks stay on the picture and their lines keep their width.
    const canvas = pane.getByRole('application');
    const centre = await box(canvas);
    await page.mouse.move(centre.x + centre.width * 0.7, centre.y + centre.height * 0.4);
    for (let step = 0; step < 6; step += 1) await page.mouse.wheel(0, -120);
    await expectOver(marks, stage);
    await page.mouse.down();
    await page.mouse.move(centre.x + centre.width * 0.5, centre.y + centre.height * 0.6);
    await page.mouse.up();
    await expectOver(marks, stage);
    await expect(marks.locator('.frontier').last()).toHaveCSS('stroke-width', '1px');

    // The Original has no finished tiles: its view shows neither mark nor tiles.
    await pane.getByRole('radio', { name: 'Original' }).click();
    await expect(mark).toHaveCount(0);
    await expect(marks).toHaveCSS('clip-path', 'inset(0px 0% 0px 100%)');
  });

  test('keeps its marks on its own side in Compare, in the slider and in boxes', async ({
    page,
  }) => {
    await openApp(page, withUnfinished);
    await showPane(page, 'media');
    await outputsTab(page).click();
    await pick(page, 'Original');
    await pick(page, 'HAT-S ×4');
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    await showPane(page, 'preview');
    const compare = page.locator('.compare-pane');
    const marks = compare.locator('.unfinished-tiles');
    await expect(marks).toHaveCount(1);
    await expectOver(marks, compare.locator('.image-stage'));
    // Slot 2 shows right of the divider, and so do its tiles.
    await expect(marks).toHaveCSS('clip-path', 'inset(0px 0% 0px 50%)');
    const mark = compare.getByRole('button', {
      name: 'Unfinished · 10 of 35 tiles. Show the finished tiles',
    });
    await mark.hover();
    await expect(marks.locator('.rest')).toHaveCSS('opacity', '1');
    await compare.getByRole('radio', { name: 'Boxes' }).click();
    const second = compare.locator('.box').nth(1);
    await expect(compare.locator('.box .unfinished-tiles')).toHaveCount(1);
    await expectOver(second.locator('.unfinished-tiles'), second.locator('.image-stage'));
  });
});

test.describe('the details under Compare', () => {
  /** A title and four lines of 11 px text, as in the two-slot strip before. */
  const TALLEST = 97;

  for (const [width, height] of [
    [880, 640],
    [1380, 860],
  ] as const)
    test(`keep their height with four slots and long facts at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await openApp(page, withLongDetails);
      await showPane(page, 'media');
      await outputsTab(page).click();
      for (const label of ['HAT-L ×4 SRx4 ImageNet-pretrain run 1', 'CodeFormer run 2'])
        await pick(page, label);
      await page.getByRole('button', { name: 'Compare these 2' }).click();
      await showPane(page, 'preview');
      const strip = page.locator('.compare-details');
      await expect(strip).toBeVisible();
      const two = (await box(strip)).height;
      expect(two).toBeLessThanOrEqual(TALLEST);

      await showPane(page, 'media');
      for (const label of ['DDColor ModelScope run 3', 'NAFNet GoPro Deblur run 4'])
        await pick(page, label);
      await showPane(page, 'preview');
      await expect(strip.getByRole('group')).toHaveCount(4);
      const pane = await box(page.locator('.compare-pane'));
      const four = await box(strip);
      // Four slots share one row, so the strip is as tall as with two.
      expect(four.height).toBe(two);
      expect(four.width).toBeLessThanOrEqual(pane.width);
      // Every line is one line of text and stays inside its column.
      for (const column of await strip.locator('.details-column').all()) {
        const outer = await box(column);
        for (const line of await column.locator('.details-title, .details-line').all()) {
          const inner = await box(line);
          expect(inner.height).toBeLessThan(20);
          expect(inner.x + inner.width).toBeLessThanOrEqual(outer.x + outer.width + 0.5);
        }
      }

      // What was cut opens above the strip, whole and inside the pane, from More.
      const more = strip.getByRole('button', { name: 'All details of slot 4' });
      await more.focus();
      await page.keyboard.press('Enter');
      const full = strip.locator('.details-full');
      await expect(full).toBeVisible();
      await expect(full).toContainText(
        'Models: SCUNet GAN → FBCNN colour → NAFNet GoPro Deblur → DDColor ModelScope → CodeFormer → HAT-L ×4 SRx4 ImageNet-pretrain',
      );
      await expect(full).toContainText('Fixes: Noise → JPEG artifacts → Blur → Colour → Faces');
      const card = await box(full);
      expect(card.x).toBeGreaterThanOrEqual(pane.x);
      expect(card.x + card.width).toBeLessThanOrEqual(pane.x + pane.width);
      expect(card.y).toBeGreaterThanOrEqual(pane.y);
      expect(card.y + card.height).toBeLessThanOrEqual(four.y);
      expect((await box(strip)).height).toBe(two);
      await page.keyboard.press('Escape');
      await expect(full).toHaveCount(0);
      await expect(page.locator('.compare-pane')).toBeVisible();
      // Hovering a column opens the same list.
      await strip.locator('.details-column').first().hover();
      await expect(full).toBeVisible();
    });

  test('are as tall with a larger interface text as their five lines need', async ({ page }) => {
    await openApp(page, {
      ...withLongDetails,
      settings: { ...ready.settings, interface_scale: 130 },
    });
    await showPane(page, 'media');
    await outputsTab(page).click();
    for (const label of ['HAT-L ×4 SRx4 ImageNet-pretrain run 1', 'CodeFormer run 2'])
      await pick(page, label);
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    await showPane(page, 'preview');
    const strip = page.locator('.compare-details');
    await expect(strip).toBeVisible();
    expect((await box(strip)).height).toBeLessThanOrEqual(TALLEST * 1.3);
  });
});

test.describe('an unfinished video', () => {
  test('is compared with the original only over its kept frames, one by one', async ({ page }) => {
    await openApp(page, videoUnfinished);
    await showPane(page, 'media');
    await outputsTab(page).click();
    await expect(page.locator('.outputs-list')).toContainText('Unfinished · 2 of 60 frames');
    await pick(page, 'Original', 'Alpine lake.mp4');
    await pick(page, 'Stopped result', 'Alpine lake.mp4');
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    await showPane(page, 'preview');
    const compare = page.locator('.compare-pane');
    const videos = compare.locator('video');
    await expect
      .poll(() =>
        videos.evaluateAll((nodes) =>
          nodes.every((node) => (node as HTMLVideoElement).readyState >= 3),
        ),
      )
      .toBe(true);
    const transport = compare.getByRole('group', { name: 'Video playback controls' });
    await expect(transport).toContainText('Frame 1');
    await expect(transport).toContainText('2 frames');
    await expect(transport).not.toContainText('0:0');
    await expect(compare).toContainText(
      'Playback ends where the unfinished result ends, after 2 frames.',
    );
    await expect(compare.locator('.unfinished-mark')).toHaveText('Unfinished · 2 of 60 frames');

    // One step: both sides in the middle of the second frame. There is no third.
    const next = transport.getByRole('button', { name: 'Next frame' });
    await next.click();
    await expect.poll(() => framesShown(videos)).toEqual([1, 1]);
    await expect(transport).toContainText('Frame 2');
    await expect(next).toHaveAttribute('aria-disabled', 'true');
    await page.keyboard.press('ArrowLeft');
    await expect.poll(() => framesShown(videos)).toEqual([0, 0]);
    await expect(transport).toContainText('Frame 1');

    // Playing ends on the last kept frame; the four-second original never runs on alone.
    await transport.getByRole('button', { name: 'Play comparison' }).click();
    await expect(transport.getByRole('button', { name: 'Play comparison' })).toBeVisible();
    await expect.poll(() => framesShown(videos)).toEqual([1, 1]);
    expect(
      await videos.evaluateAll((nodes) => nodes.every((node) => (node as HTMLVideoElement).paused)),
    ).toBe(true);
  });

  test('lets the audio of either side play, or of none', async ({ page }) => {
    await openApp(page, videoUnfinished);
    await showPane(page, 'media');
    await outputsTab(page).click();
    await pick(page, '15 fps result', 'Alpine lake.mp4');
    await pick(page, '60 fps result', 'Alpine lake.mp4');
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    await showPane(page, 'preview');
    const compare = page.locator('.compare-pane');
    const videos = compare.locator('video');
    const muted = () =>
      videos.evaluateAll((nodes) => nodes.map((node) => (node as HTMLVideoElement).muted));
    const audio = compare.getByRole('radiogroup', { name: 'Audio' });
    await expect
      .poll(() =>
        videos.evaluateAll((nodes) =>
          nodes.every((node) => (node as HTMLVideoElement).readyState >= 3),
        ),
      )
      .toBe(true);
    await expect(audio.getByRole('radio')).toHaveCount(3);
    await expect(
      audio.getByRole('radio', { name: 'Audio of slot 1: 15 fps result' }),
    ).toBeChecked();
    expect(await muted()).toEqual([false, true]);
    await audio.getByRole('radio', { name: 'Audio of slot 2: 60 fps result' }).click();
    expect(await muted()).toEqual([true, false]);
    // The choice follows the result when the slots are swapped.
    await compare.getByRole('button', { name: 'Swap slots 1 and 2' }).click();
    await expect(
      audio.getByRole('radio', { name: 'Audio of slot 1: 60 fps result' }),
    ).toBeChecked();
    await expect.poll(muted).toEqual([false, true]);
    await audio.getByRole('radio', { name: 'No audio' }).click();
    expect(await muted()).toEqual([true, true]);

    // With four players at the smallest window the transport still holds every control.
    await page.setViewportSize({ width: 880, height: 640 });
    await showPane(page, 'media');
    await pick(page, 'Original', 'Alpine lake.mp4');
    await pick(page, 'Stopped result', 'Alpine lake.mp4');
    await showPane(page, 'preview');
    await expect(audio.getByRole('radio')).toHaveCount(5);
    const hud = await box(compare.locator('.video-hud'));
    for (const part of [
      compare.locator('.video-transport'),
      compare.locator('.video-hud .zoom-hud'),
      audio,
      compare.getByRole('slider', { name: /Video (position|frame)/ }),
    ]) {
      const inner = await box(part);
      expect(inner.x).toBeGreaterThanOrEqual(hud.x);
      expect(inner.x + inner.width).toBeLessThanOrEqual(hud.x + hud.width);
    }
    expect(
      (await box(compare.getByRole('slider', { name: /Video (position|frame)/ }))).width,
    ).toBeGreaterThanOrEqual(60);
    expect(hud.height).toBeLessThan(60);
  });

  test('plays in the preview as its two frames, marked Unfinished', async ({ page }) => {
    await openApp(page, videoUnfinished);
    await showPane(page, 'media');
    await page
      .getByRole('button', { name: /Alpine lake\.mp4/ })
      .first()
      .click();
    await showPane(page, 'preview');
    const pane = page.locator('.preview-pane');
    await expect(pane.locator('.unfinished-chip')).toHaveText('Unfinished · 2 of 60 frames');
    await expect(pane).not.toContainText('processed locally');
    const transport = pane.getByRole('group', { name: 'Video playback controls' });
    await expect(transport).toContainText('Frame 1');
    await expect(transport).toContainText('2 frames');
    await transport.getByRole('button', { name: 'Next frame' }).click();
    await expect.poll(() => framesShown(pane.locator('video'))).toEqual([1, 1]);
    // The Original view shows its own chip in the same corner.
    await pane.getByRole('radio', { name: 'Original' }).click();
    await expect(pane.locator('.unfinished-chip')).toHaveCount(0);
    await expect(pane.locator('.original-chip')).toBeVisible();
  });
});

test.describe('unfinished results @visual', () => {
  test('an unfinished photo, at rest and with its tiles shown', async ({ page }) => {
    await openApp(page, withUnfinished);
    await showPane(page, 'preview');
    const pane = page.locator('.preview-pane');
    await expect(pane.locator('.unfinished-tiles')).toBeVisible();
    await settle(page);
    await expect(page).toHaveScreenshot('unfinished-photo.png');
    await pane.getByRole('radio', { name: 'Enhanced' }).click();
    await pane.locator('.unfinished-chip').click();
    await settle(page);
    await expect(page).toHaveScreenshot('unfinished-photo-tiles.png');
  });

  test('Compare details with four long-winded slots and the full list of one', async ({ page }) => {
    await openApp(page, withLongDetails);
    await showPane(page, 'media');
    await outputsTab(page).click();
    for (const label of [
      'HAT-L ×4 SRx4 ImageNet-pretrain run 1',
      'CodeFormer run 2',
      'DDColor ModelScope run 3',
      'NAFNet GoPro Deblur run 4',
    ])
      await pick(page, label);
    await page.getByRole('button', { name: 'Compare these 4' }).click();
    await showPane(page, 'preview');
    const strip = page.locator('.compare-details');
    await strip.getByRole('button', { name: 'All details of slot 2' }).click();
    await expect(strip.locator('.details-full')).toBeVisible();
    await settle(page);
    await expect(page).toHaveScreenshot('compare-details-long.png');
  });

  test('an unfinished video against its original in Compare', async ({ page }) => {
    await openApp(page, videoUnfinished);
    await showPane(page, 'media');
    await outputsTab(page).click();
    await pick(page, 'Original', 'Alpine lake.mp4');
    await pick(page, 'Stopped result', 'Alpine lake.mp4');
    await page.getByRole('button', { name: 'Compare these 2' }).click();
    await showPane(page, 'preview');
    const compare = page.locator('.compare-pane');
    await expect
      .poll(() =>
        compare
          .locator('video')
          .evaluateAll((nodes) =>
            nodes.every((node) => (node as HTMLVideoElement).readyState >= 3),
          ),
      )
      .toBe(true);
    await compare.getByRole('button', { name: 'Next frame' }).click();
    await expect(compare.getByRole('group', { name: 'Video playback controls' })).toContainText(
      'Frame 2',
    );
    await settle(page);
    await expect(page).toHaveScreenshot('compare-unfinished-video.png');
  });
});
