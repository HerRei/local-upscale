import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { emit, hostCalls } from './fake-host';
import { ready } from './fixtures';

// A four-step photo job (JPEG, Colour, Noise, 4x) as the engine reports it, against the fake host.
// Between the steps the viewer keeps the sharpest picture it has; after the job the chips show
// what each step made.

const JOB = 'job-photo-1';
const STEPS = [
  { kind: 'restore', fix: 'jpeg', model_id: 'fbcnn_color' },
  { kind: 'restore', fix: 'colour', model_id: 'ddcolor_modelscope' },
  { kind: 'restore', fix: 'noise', model_id: 'scunet_color_real_gan' },
  { kind: 'upscale', fix: '', model_id: 'span_photo_x4' },
] as const;
const NAMES = ['JPEG', 'Colour', 'Noise', 'Upscale 4×'];

const scenario = (withPictures = true) => ({
  ...ready,
  photos: 1,
  running: true,
  pairs: true,
  features: [...(ready.features ?? []), ...(withPictures ? ['stage_results'] : [])],
  settings: {
    ...ready.settings,
    output_scale: 4,
    fix_stages: STEPS.filter((step) => step.fix).map((step) => ({
      fix: step.fix,
      model_id: step.model_id,
    })),
  },
  jobSteps: STEPS.map((step, index) => ({
    ...step,
    state: index === 0 ? ('running' as const) : ('waiting' as const),
  })),
});

/** A 2048 px wide step picture; the hue tells the steps apart. */
const stepPicture = (hue: number) =>
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="2048" height="1365" viewBox="0 0 16 11">` +
      `<rect width="16" height="11" fill="hsl(${hue},60%,55%)"/>` +
      `<circle cx="8" cy="5" r="3" fill="hsl(${hue + 40},70%,80%)"/></svg>`,
  );

const startStep = (page: Page, index: number) =>
  emit(page, 'worker-message', {
    type: 'stage_started',
    data: {
      job_id: JOB,
      stage_index: index,
      stage_count: STEPS.length,
      step: STEPS[index].kind,
      fix: STEPS[index].fix,
    },
  });

async function finishStep(
  page: Page,
  index: number,
  extra: { picture?: boolean; skipped?: boolean; note?: string; kept?: boolean } = {},
) {
  const { picture = true, skipped = false, note = '', kept = true } = extra;
  await emit(page, 'worker-message', {
    type: 'stage_result',
    data: {
      job_id: JOB,
      stage_index: index,
      stage_count: STEPS.length,
      step: STEPS[index].kind,
      fix: STEPS[index].fix,
      skipped,
      note,
      ...(picture && !skipped ? { preview_url: stepPicture(20 + index * 70) } : {}),
      image_path: kept && !skipped ? `/fixture/steps/${JOB}/${index}.png` : '',
    },
  });
  await emit(page, 'worker-message', {
    type: 'stage_completed',
    data: { job_id: JOB, stage_index: index, stage_count: STEPS.length },
  });
}

/** The 320 px picture the engine sends as a full-image tile at the start of a step. */
async function sendSmallFullImageTile(page: Page, index: number) {
  const jpeg = await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 213;
    const ctx = canvas.getContext('2d')!;
    ctx.fillStyle = 'rgb(200, 30, 30)';
    ctx.fillRect(0, 0, 320, 213);
    return canvas.toDataURL('image/jpeg', 0.9).split(',')[1];
  });
  await emit(page, 'worker-message', {
    type: 'live_preview_frame',
    data: {
      job_id: JOB,
      sequence: 10 + index,
      stage_index: index,
      preview_kind: 'tile',
      output_x: 0,
      output_y: 0,
      output_width: 320,
      output_height: 213,
      image_width: 320,
      image_height: 213,
      jpeg_base64: jpeg,
    },
  });
}

/** Records the narrowest decoded picture the viewer's base had since this call, frame by frame. */
const watchBase = (page: Page) =>
  page.evaluate(() => {
    const w = window as unknown as { __base?: { min: number; srcs: string[] } };
    w.__base = { min: Infinity, srcs: [] };
    const tick = () => {
      const img = document.querySelector('.source-image') as HTMLImageElement | null;
      if (img && img.complete && img.naturalWidth > 0 && w.__base) {
        w.__base.min = Math.min(w.__base.min, img.naturalWidth);
        if (w.__base.srcs.at(-1) !== img.currentSrc) w.__base.srcs.push(img.currentSrc);
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
const baseReport = (page: Page) =>
  page.evaluate(() => (window as unknown as { __base: { min: number; srcs: string[] } }).__base);

const sourceSrc = (page: Page) =>
  page.locator('.source-image').evaluate((img) => (img as HTMLImageElement).currentSrc);
const baseWidth = (page: Page) =>
  page.locator('.source-image').evaluate((img) => (img as HTMLImageElement).naturalWidth);
const statusTitle = (page: Page) => page.locator('.status-bar .status-copy strong');
const stepLabelOf = (page: Page) => page.locator('.canvas-well .step-label');
const planLine = (page: Page) => page.locator('[aria-label^="Plan:"]').first();

test.describe('steps of a job', () => {
  test('a four-step run with step pictures: sharp base, label, plan marks, M, chips', async ({
    page,
  }) => {
    await openApp(page, scenario());
    await showPane(page, 'preview');
    const original = await sourceSrc(page);
    expect(await baseWidth(page)).toBeGreaterThanOrEqual(1000);
    await watchBase(page);

    await startStep(page, 0);
    await expect(stepLabelOf(page)).toHaveText('Step 1 of 4 · JPEG');
    await expect(statusTitle(page)).toContainText('Step 1 of 4 · JPEG');
    await expect(planLine(page)).toHaveAttribute(
      'aria-label',
      'Plan: JPEG running, Colour, Noise, Upscale 4×',
    );

    // A small full-image picture of step 2 never replaces the sharp source.
    await sendSmallFullImageTile(page, 0);
    await page.waitForTimeout(300);
    expect(await sourceSrc(page)).toBe(original);

    await finishStep(page, 0);
    await startStep(page, 1);
    await expect(stepLabelOf(page)).toHaveText('Step 2 of 4 · Colour');
    await expect(statusTitle(page)).toContainText('Step 2 of 4 · Colour');
    await expect(planLine(page)).toHaveAttribute(
      'aria-label',
      'Plan: JPEG done, Colour running, Noise, Upscale 4×',
    );
    // The base is now the picture of step 1, 2048 px wide.
    await expect.poll(() => baseWidth(page)).toBe(2048);
    const afterJpeg = await sourceSrc(page);
    expect(afterJpeg).not.toBe(original);

    // Colour has no tiles; a small picture still changes nothing.
    await sendSmallFullImageTile(page, 1);
    await page.waitForTimeout(300);
    expect(await sourceSrc(page)).toBe(afterJpeg);

    // Hold M: the original, without the label; release: back to the step picture.
    await page.locator('.canvas-well').hover();
    await page.keyboard.down('m');
    await expect.poll(() => sourceSrc(page)).toBe(original);
    await expect(stepLabelOf(page)).toHaveCount(0);
    await page.keyboard.up('m');
    await expect.poll(() => sourceSrc(page)).toBe(afterJpeg);
    await expect(stepLabelOf(page)).toHaveText('Step 2 of 4 · Colour');

    await finishStep(page, 1);
    await startStep(page, 2);
    await expect(stepLabelOf(page)).toHaveText('Step 3 of 4 · Noise');
    await finishStep(page, 2);
    await startStep(page, 3);
    await expect(stepLabelOf(page)).toHaveText('Step 4 of 4 · Upscale 4×');
    await expect(statusTitle(page)).toContainText('Step 4 of 4 · Upscale 4×');
    await expect(planLine(page)).toHaveAttribute(
      'aria-label',
      'Plan: JPEG done, Colour done, Noise done, Upscale 4× running',
    );

    // The base never was smaller than 1000 px at any frame of the run.
    const report = await baseReport(page);
    expect(report.min).toBeGreaterThanOrEqual(1000);

    await emit(page, 'worker-message', {
      type: 'job_completed',
      data: { job_id: JOB, output_path: `/fixture/results/${JOB}.png` },
    });
    await expect(stepLabelOf(page)).toHaveCount(0);

    const chips = page.getByRole('group', { name: 'Result after each step' });
    await expect(chips).toBeVisible();
    await expect(chips.getByRole('button')).toHaveText(['JPEG', 'Colour', 'Noise', '4×']);
    await expect(chips.getByRole('button', { name: '4×' })).toHaveAttribute('aria-pressed', 'true');

    // Noise changes the result side to that step's picture.
    await chips.getByRole('button', { name: 'Noise' }).click();
    await expect(chips.getByRole('button', { name: 'Noise' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(chips.getByRole('button', { name: '4×' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    await expect
      .poll(async () =>
        (await hostCalls(page, 'request_image_comparison')).map(
          (call) => (call.args as { showing?: string }).showing,
        ),
      )
      .toContain(`${JOB}#2`);
    await expect(page.getByText('After Noise').first()).toBeVisible();

    // Against the original: the switch still compares the chosen step with it.
    const views = page.getByRole('radiogroup', { name: 'Comparison view' });
    await expect(views.getByRole('radio')).toHaveText(['Original', 'Split', 'Enhanced']);
    await views.getByRole('radio', { name: 'Split' }).click();
    await expect(page.getByRole('slider', { name: 'Before and after comparison' })).toBeVisible();
    await expect(chips.getByRole('button', { name: 'Noise' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  test('a skipped step keeps the picture, says so for a moment and stays a disabled chip', async ({
    page,
  }) => {
    await openApp(page, scenario());
    await showPane(page, 'preview');
    const original = await sourceSrc(page);
    const note = 'The picture shows no JPEG blocks.';

    await startStep(page, 0);
    await finishStep(page, 0, { skipped: true, note });
    await expect(stepLabelOf(page)).toHaveText('Step 1 of 4 · JPEG skipped');
    expect(await sourceSrc(page)).toBe(original);
    await expect(planLine(page)).toHaveAttribute(
      'aria-label',
      'Plan: JPEG skipped, Colour, Noise, Upscale 4×',
    );

    await startStep(page, 1);
    await finishStep(page, 1);
    await startStep(page, 2);
    await finishStep(page, 2);
    await startStep(page, 3);
    await emit(page, 'worker-message', {
      type: 'job_completed',
      data: { job_id: JOB, output_path: `/fixture/results/${JOB}.png` },
    });

    const chips = page.getByRole('group', { name: 'Result after each step' });
    const jpeg = chips.getByRole('button', { name: 'JPEG' });
    await expect(jpeg).toBeDisabled();
    await expect(jpeg).toHaveAttribute('title', note);
    await expect(chips.getByRole('button', { name: 'Colour' })).toBeEnabled();
  });

  test('an older engine sends no step pictures: the source stays the base, no error', async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    await openApp(page, scenario(false));
    await showPane(page, 'preview');
    const original = await sourceSrc(page);
    await watchBase(page);

    for (let index = 0; index < STEPS.length; index += 1) {
      await startStep(page, index);
      await expect(stepLabelOf(page)).toHaveText(`Step ${index + 1} of 4 · ${NAMES[index]}`);
      if (index < 3) {
        await sendSmallFullImageTile(page, index);
        await page.waitForTimeout(250);
        expect(await sourceSrc(page)).toBe(original);
        await emit(page, 'worker-message', {
          type: 'stage_completed',
          data: { job_id: JOB, stage_index: index, stage_count: STEPS.length },
        });
      }
    }
    expect(await sourceSrc(page)).toBe(original);
    expect((await baseReport(page)).min).toBeGreaterThanOrEqual(1000);
    expect(errors).toEqual([]);
  });
});
