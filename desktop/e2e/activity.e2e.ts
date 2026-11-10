import { expect, test, type Page } from '@playwright/test';
import { openApp } from './app';
import { ready } from './fixtures';

// The status bar is the one place that reports a job: nothing floats over the stage, and the
// locked note under the Enhance header sits clear of the Task heading.

const SHOTS = process.env.ROWRUN_SHOTS;
async function look(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

for (const size of [
  { width: 1380, height: 860 },
  { width: 880, height: 640 },
]) {
  test(`a running job leaves the stage free and the lock note clear of Task at ${size.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    await openApp(page, { ...ready, photos: 2, running: true });
    await expect(page.locator('.status-bar').getByRole('progressbar')).toHaveCount(1);
    await expect(page.locator('.model-activity')).toHaveCount(0);
    const stage = page.locator('.canvas-well');
    if (await stage.isVisible()) {
      const box = (await stage.boundingBox())!;
      const hit = await page.evaluate(
        ([x, y]) => document.elementFromPoint(x, y)?.className ?? '',
        [box.x + box.width - 40, box.y + 30],
      );
      expect(String(hit)).not.toMatch(/activity/);
    }
    const note = page.locator('.settings-lock');
    const task = page.locator('.eyebrow', { hasText: /^Task$/ });
    if ((await note.isVisible()) && (await task.isVisible())) {
      const a = (await note.boundingBox())!;
      const b = (await task.boundingBox())!;
      expect(a.y + a.height).toBeLessThanOrEqual(b.y);
    }
    await look(page, `running-image-${size.width}`);
  });
}

test('a running edit names its model in the status bar detail and shows no card', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1380, height: 860 });
  await openApp(page, {
    ...ready,
    photos: 1,
    running: true,
    task: 'edit',
    installed: ['flux2_klein_4b_q4_0'],
    features: ['image_edit', 'video_interpolation'],
    settings: { edit_model_id: 'flux2_klein_4b_q4_0' },
  });
  await expect(page.locator('.model-activity')).toHaveCount(0);
  await expect(page.locator('.status-bar .status-detail')).toContainText('FLUX.2 klein 4B');
  await look(page, 'running-edit-1380');
});
