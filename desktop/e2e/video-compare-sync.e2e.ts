import { expect, test, type Locator, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { videoSync } from './fixtures';

const outputsTab = (page: Page) => page.getByRole('tab', { name: /^Outputs/ });
const pick = (page: Page, label: string) =>
  page.getByRole('checkbox', { name: `${label}, Alpine lake.mp4`, exact: true }).check();

/**
 * Decode the 8-bit luma barcode burned into the bottom row (y=48..63) of each side's
 * cropped `<canvas>` by sampling it onto a 128x72 scratch canvas.
 */
async function sampleFrames(canvases: Locator): Promise<number[]> {
  return canvases.evaluateAll((nodes) => {
    const scratch = document.createElement('canvas');
    scratch.width = 128;
    scratch.height = 72;
    const ctx = scratch.getContext('2d')!;
    return nodes.map((node) => {
      const source = node as HTMLCanvasElement;
      if (source.width < 2 || source.height < 2) return -1;
      ctx.drawImage(source, 0, 0, 128, 72);
      const data = ctx.getImageData(0, 55, 128, 1).data;
      let frame = 0;
      for (let bit = 0; bit < 8; bit += 1) {
        const px = (bit * 16 + 8) * 4;
        const luma = 0.299 * data[px] + 0.587 * data[px + 1] + 0.114 * data[px + 2];
        frame = (frame << 1) | (luma > 128 ? 1 : 0);
      }
      return frame;
    });
  });
}

async function openCompare(page: Page, labels: string[], layout: 'slider' | 'boxes' = 'slider') {
  await openApp(page, videoSync);
  await showPane(page, 'media');
  await outputsTab(page).click();
  for (const label of labels) await pick(page, label);
  await page.getByRole('button', { name: `Compare these ${labels.length}` }).click();
  await showPane(page, 'preview');
  const compare = page.locator('.compare-pane');
  if (layout === 'boxes' && labels.length <= 2) {
    await compare.getByRole('radio', { name: 'Boxes' }).click();
  }
  const video = compare.locator('video');
  const canvases =
    layout === 'boxes' ? compare.locator('.boxes canvas') : compare.locator('.image-stage canvas');
  await expect(video).toHaveCount(1);
  await expect(canvases).toHaveCount(labels.length);
  await expect
    .poll(() =>
      video.evaluateAll(
        (nodes) => nodes.length === 1 && (nodes[0] as HTMLVideoElement).readyState >= 3,
      ),
    )
    .toBe(true);
  await expect.poll(() => sampleFrames(canvases).then((f) => f.every((v) => v >= 0))).toBe(true);
  return {
    compare,
    video,
    canvases,
    audio: compare.locator('audio'),
    transport: compare.getByRole('group', { name: 'Video playback controls' }),
  };
}

test.describe('video Compare sync (real PyAV clips)', () => {
  for (const layout of ['slider', 'boxes'] as const) {
    test(`same frame number while playing and after pausing in ${layout}`, async ({ page }) => {
      const { video, canvases, transport } = await openCompare(
        page,
        ['Original', '30 fps result'],
        layout,
      );
      await expect.poll(() => sampleFrames(canvases)).toEqual([0, 0]);
      const checkPlayback = async (targetSeconds: number) => {
        await transport.getByRole('button', { name: 'Play comparison' }).click();
        // Sample both side canvases synchronously in rAF while actively playing past targetSeconds.
        const samples = await video.evaluate((node, stopAt) => {
          return new Promise<{ t: number; f0: number; f1: number; seeking: boolean }[]>(
            (resolve) => {
              const v = node as HTMLVideoElement;
              const pane = v.closest('.compare-pane')!;
              const sideCanvases = [
                ...pane.querySelectorAll('.image-stage canvas'),
              ] as HTMLCanvasElement[];
              const scratch = document.createElement('canvas');
              scratch.width = 128;
              scratch.height = 72;
              const ctx = scratch.getContext('2d')!;
              const read = (c: HTMLCanvasElement) => {
                if (c.width < 2 || c.height < 2) return -1;
                ctx.drawImage(c, 0, 0, 128, 72);
                const data = ctx.getImageData(0, 55, 128, 1).data;
                let f = 0;
                for (let b = 0; b < 8; b++) {
                  const px = (b * 16 + 8) * 4;
                  const luma = 0.299 * data[px] + 0.587 * data[px + 1] + 0.114 * data[px + 2];
                  f = (f << 1) | (luma > 128 ? 1 : 0);
                }
                return f;
              };
              const out: { t: number; f0: number; f1: number; seeking: boolean }[] = [];
              const step = () => {
                out.push({
                  t: Number(v.currentTime.toFixed(3)),
                  f0: read(sideCanvases[0]),
                  f1: read(sideCanvases[1]),
                  seeking: v.seeking,
                });
                if (v.currentTime > stopAt || out.length > 220) {
                  resolve(out);
                } else {
                  requestAnimationFrame(step);
                }
              };
              requestAnimationFrame(step);
            },
          );
        }, targetSeconds);
        const settled = samples.filter((s) => !s.seeking && s.f0 >= 0 && s.f1 >= 0);
        expect(settled.length).toBeGreaterThan(0);
        // One combined video on one clock: every rendered frame matches across sides in both Chromium and WebKit.
        expect(Math.max(...settled.map((s) => Math.abs(s.f0 - s.f1)))).toBe(0);
        expect(settled[settled.length - 1].f0).toBeGreaterThan(15);
      };

      await checkPlayback(0.95);
      await transport.getByRole('button', { name: 'Pause comparison' }).click();
      await expect
        .poll(() =>
          video.evaluate((node) => {
            const v = node as HTMLVideoElement;
            return v.paused && !v.seeking;
          }),
        )
        .toBe(true);
      await expect
        .poll(() => sampleFrames(canvases).then((f) => f[0] > 24 && f[0] === f[1]))
        .toBe(true);
    });

    test(`unfinished 1 s comparison with audio overhang stops at last kept frame in ${layout}`, async ({
      page,
    }) => {
      const { compare, video, canvases, transport } = await openCompare(
        page,
        ['Original', 'Stopped overhang result'],
        layout,
      );
      await expect(compare).toContainText(
        'Playback ends where the unfinished result ends, after 0:01.',
      );

      await transport.getByRole('button', { name: 'Play comparison' }).click();
      await expect(transport.getByRole('button', { name: 'Play comparison' })).toBeVisible({
        timeout: 10_000,
      });
      await page.waitForTimeout(300);
      await expect
        .poll(() =>
          video.evaluate((node) => {
            const v = node as HTMLVideoElement;
            return v.paused && !v.seeking;
          }),
        )
        .toBe(true);
      await expect.poll(() => sampleFrames(canvases)).toEqual([29, 29]);
      await expect(transport.getByRole('button', { name: 'Next frame' })).toHaveAttribute(
        'aria-disabled',
        'true',
      );
    });
  }

  test('unfinished result in slot 1 (master) stops at last kept frame too', async ({ page }) => {
    const { video, canvases, transport } = await openCompare(
      page,
      ['Stopped overhang result', 'Original'],
      'boxes',
    );
    await transport.getByRole('button', { name: 'Play comparison' }).click();
    await expect(transport.getByRole('button', { name: 'Play comparison' })).toBeVisible({
      timeout: 10_000,
    });
    await expect
      .poll(() =>
        video.evaluate((node) => {
          const v = node as HTMLVideoElement;
          return v.paused && !v.seeking;
        }),
      )
      .toBe(true);
    await expect.poll(() => sampleFrames(canvases)).toEqual([29, 29]);
  });

  test('step forward and back moves exactly one frame on both sides and stops at ends', async ({
    page,
  }) => {
    const { canvases, transport } = await openCompare(page, [
      'Original',
      'Stopped overhang result',
    ]);
    const prev = transport.getByRole('button', { name: 'Previous frame' });
    const next = transport.getByRole('button', { name: 'Next frame' });

    await expect.poll(() => sampleFrames(canvases)).toEqual([0, 0]);
    await expect(prev).toHaveAttribute('aria-disabled', 'true');
    await prev.click({ force: true });
    await page.keyboard.press('ArrowLeft');
    await expect.poll(() => sampleFrames(canvases)).toEqual([0, 0]);

    for (const expected of [1, 2, 3]) {
      await next.click();
      await expect.poll(() => sampleFrames(canvases)).toEqual([expected, expected]);
    }
    await page.keyboard.press('ArrowLeft');
    await expect.poll(() => sampleFrames(canvases)).toEqual([2, 2]);
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => sampleFrames(canvases)).toEqual([3, 3]);

    // Jump to the end via the timeline and verify end clamp and stepping back.
    const timeline = transport.getByRole('slider', { name: 'Video position' });
    await timeline.focus();
    await page.keyboard.press('End');
    await expect.poll(() => sampleFrames(canvases)).toEqual([29, 29]);
    await expect(next).toHaveAttribute('aria-disabled', 'true');
    await next.click({ force: true });
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => sampleFrames(canvases)).toEqual([29, 29]);

    await prev.click();
    await expect.poll(() => sampleFrames(canvases)).toEqual([28, 28]);
  });

  for (const [label, fps] of [
    ['24 fps result', 24],
    ['60 fps result', 60],
  ] as const) {
    test(`different frame rates (30 fps vs ${fps} fps) stay paired by time without stuttering`, async ({
      page,
    }) => {
      const { video, canvases, transport } = await openCompare(page, ['Original', label]);
      await video.evaluate((node) => {
        let count = 0;
        node.addEventListener('seeking', () => {
          count += 1;
          (window as unknown as { __seekCount: number }).__seekCount = count;
        });
        (window as unknown as { __seekCount: number }).__seekCount = 0;
      });

      await transport.getByRole('button', { name: 'Play comparison' }).click();
      const { backwards, maxGapSeconds } = await video.evaluate((node, targetFps) => {
        return new Promise<{ backwards: number; maxGapSeconds: number }>((resolve) => {
          const v = node as HTMLVideoElement;
          const pane = v.closest('.compare-pane')!;
          const sideCanvases = [
            ...pane.querySelectorAll('.image-stage canvas'),
          ] as HTMLCanvasElement[];
          const scratch = document.createElement('canvas');
          scratch.width = 128;
          scratch.height = 72;
          const ctx = scratch.getContext('2d')!;
          const read = (c: HTMLCanvasElement) => {
            if (c.width < 2 || c.height < 2) return -1;
            ctx.drawImage(c, 0, 0, 128, 72);
            const data = ctx.getImageData(0, 55, 128, 1).data;
            let f = 0;
            for (let b = 0; b < 8; b++) {
              const px = (b * 16 + 8) * 4;
              const luma = 0.299 * data[px] + 0.587 * data[px + 1] + 0.114 * data[px + 2];
              f = (f << 1) | (luma > 128 ? 1 : 0);
            }
            return f;
          };
          let prev = 0;
          let back = 0;
          let maxGap = 0;
          let count = 0;
          const step = () => {
            const t = v.currentTime;
            if (prev > 0.2 && t + 0.05 < prev) back += 1;
            prev = t;
            const f0 = read(sideCanvases[0]);
            const f1 = read(sideCanvases[1]);
            if (!v.seeking && f0 >= 0 && f1 >= 0) {
              maxGap = Math.max(maxGap, Math.abs(f0 / 30 - f1 / targetFps));
            }
            count += 1;
            if (t > 1.1 || count > 260) {
              resolve({ backwards: back, maxGapSeconds: maxGap });
            } else {
              requestAnimationFrame(step);
            }
          };
          requestAnimationFrame(step);
        });
      }, fps);

      const seekCountDuringPlayback = await page.evaluate(
        () => (window as unknown as { __seekCount: number }).__seekCount,
      );

      await transport.locator('.transport-play').evaluate((el) => {
        if (el.getAttribute('aria-label') === 'Pause comparison') (el as HTMLButtonElement).click();
      });
      await expect
        .poll(() =>
          video.evaluate((node) => {
            const v = node as HTMLVideoElement;
            return v.paused && !v.seeking;
          }),
        )
        .toBe(true);

      expect(backwards).toBe(0);
      expect(seekCountDuringPlayback).toBe(0);
      expect(maxGapSeconds).toBeLessThanOrEqual(1 / 24 + 1 / 30 + 0.002);

      await expect
        .poll(() =>
          sampleFrames(canvases).then((frames) => Math.abs(frames[0] / 30 - frames[1] / fps)),
        )
        .toBeLessThanOrEqual(1 / 24 + 1 / 30 + 0.001);
    });
  }

  test('switching between Slider and Boxes keeps position and audio choice', async ({ page }) => {
    const { compare, canvases, audio, transport } = await openCompare(page, [
      'Original',
      '30 fps result',
    ]);
    const timeline = transport.getByRole('slider', { name: 'Video position' });
    await timeline.fill('0.5');
    await expect.poll(() => sampleFrames(canvases)).toEqual([15, 15]);
    const audioGroup = compare.getByRole('radiogroup', { name: 'Audio' });
    await audioGroup.getByRole('radio', { name: 'Audio of slot 2: 30 fps result' }).click();
    await expect.poll(() => audio.evaluate((n) => (n as HTMLAudioElement).muted)).toBe(false);
    const chosenSrc = await audio.getAttribute('src');
    expect(chosenSrc).toBeTruthy();

    // Switch Slider -> Boxes.
    await compare.getByRole('radio', { name: 'Boxes' }).click();
    await expect(compare.locator('.boxes canvas')).toHaveCount(2);
    await expect.poll(() => sampleFrames(compare.locator('.boxes canvas'))).toEqual([15, 15]);
    await expect(
      audioGroup.getByRole('radio', { name: 'Audio of slot 2: 30 fps result' }),
    ).toBeChecked();
    await expect.poll(() => audio.evaluate((n) => (n as HTMLAudioElement).muted)).toBe(false);
    expect(await audio.getAttribute('src')).toBe(chosenSrc);

    // Switch Boxes -> Slider.
    await compare.getByRole('radio', { name: 'Slider' }).click();
    await expect(compare.locator('.image-stage canvas')).toHaveCount(2);
    await expect.poll(() => sampleFrames(compare.locator('.image-stage canvas'))).toEqual([15, 15]);
    await expect(
      audioGroup.getByRole('radio', { name: 'Audio of slot 2: 30 fps result' }),
    ).toBeChecked();
    await expect.poll(() => audio.evaluate((n) => (n as HTMLAudioElement).muted)).toBe(false);
    expect(await audio.getAttribute('src')).toBe(chosenSrc);
  });

  test('unfinished 24 fps result against 30 fps original stops at last kept frame and steps by time', async ({
    page,
    browserName,
  }) => {
    const { video, canvases, transport } = await openCompare(page, [
      'Original',
      'Stopped 24 fps result',
    ]);
    await expect.poll(() => sampleFrames(canvases)).toEqual([0, 0]);
    // GStreamer (WebKit on Linux) shows the previous frame of a mixed-rate playback
    // copy after a seek; KNOWN_LIMITATIONS.md lists it.
    if (!(browserName === 'webkit' && process.platform === 'linux')) {
      const next = transport.getByRole('button', { name: 'Next frame' });
      await next.click();
      await expect.poll(() => sampleFrames(canvases)).toEqual([1, 1]);
      await next.click();
      await expect.poll(() => sampleFrames(canvases)).toEqual([3, 2]);
    }

    await transport.getByRole('button', { name: 'Play comparison' }).click();
    await expect(transport.getByRole('button', { name: 'Play comparison' })).toBeVisible({
      timeout: 10_000,
    });
    await expect
      .poll(() =>
        video.evaluate((node) => {
          const v = node as HTMLVideoElement;
          return v.paused && !v.seeking;
        }),
      )
      .toBe(true);
    await expect.poll(() => sampleFrames(canvases)).toEqual([29, 23]);
  });
});
