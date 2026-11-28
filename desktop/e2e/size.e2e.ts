import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { hostCalls, type Scenario } from './fake-host';
import { ready } from './fixtures';

const fix = (page: Page, name: string) =>
  page.getByRole('group', { name: 'Fix' }).getByRole('checkbox', { name, exact: true });

const scenario: Scenario = {
  ...ready,
  installed: ['span_photo_x4'],
};

test.describe('Size decides', () => {
  test('photo switches between 4×, Same size (with and without fixes) and back', async ({
    page,
  }) => {
    await openApp(page, scenario);
    await showPane(page, 'enhance');

    await fix(page, 'Noise').click();
    await fix(page, 'Blur').click();
    await page.getByRole('radio', { name: 'Same size' }).click();

    const content = page.getByRole('radiogroup', { name: 'Content' });
    await expect(content).toBeVisible();
    for (const label of ['Photo', 'Illustration']) {
      await expect(content.getByRole('radio', { name: label })).toBeDisabled();
    }
    await expect(page.locator('.field-value')).toHaveText('1600 × 1067');
    await page.locator('.foot-plan').click();
    const stages = page.getByRole('list', { name: 'Stages' });
    await expect(stages).not.toContainText('Upscale');
    await page.keyboard.press('Escape');

    // Noise + Blur aren't installed in `scenario`, so the primary button downloads then restores.
    const start = page.locator('.status-actions button.primary.start');
    await expect(start).toHaveText(/Restore selected|then restore$/);

    // Click Start (with installed models in a fresh scenario) to check start_jobs payload.
    await openApp(page, {
      ...ready,
      installed: [
        'span_photo_x4',
        'denoise_realplksr_1x',
        'nafnet_gopro_deblur',
        'nafnet_sidd_width64',
      ],
    });
    await showPane(page, 'enhance');
    await fix(page, 'Noise').click();
    await fix(page, 'Blur').click();
    await page.getByRole('radio', { name: 'Same size' }).click();
    await expect(page.getByRole('button', { name: 'Restore selected' })).toBeVisible();
    await page.getByRole('button', { name: 'Restore selected' }).click();
    await expect.poll(async () => (await hostCalls(page, 'start_jobs')).length).toBe(1);
    const { input } = (await hostCalls(page, 'start_jobs'))[0].args as {
      input: { task: string; output_scale: number; fix_stages: { fix: string }[] };
    };
    expect(input.task).toBe('denoise');
    expect(input.output_scale).toBe(1);
    expect(input.fix_stages.map((s) => s.fix)).toEqual(['blur', 'noise']);

    // Click 4×: Upscale row and enabled Content come back, result is 6400 × 4268, both fixes stay on.
    await page.getByRole('radio', { name: '4×' }).click();
    await expect(content).toBeVisible();
    for (const label of ['Photo', 'Illustration']) {
      await expect(content.getByRole('radio', { name: label })).toBeEnabled();
    }
    await expect(page.locator('.field-value')).toHaveText('6400 × 4268');
    await expect(fix(page, 'Noise')).toBeChecked();
    await expect(fix(page, 'Blur')).toBeChecked();
    await page.locator('.foot-plan').click();
    await expect(stages).toContainText('Upscale 4×');
    await page.keyboard.press('Escape');

    // Same size, untick both: photo line shows and Start is disabled.
    await page.getByRole('radio', { name: 'Same size' }).click();
    await fix(page, 'Noise').click();
    await fix(page, 'Blur').click();
    await expect(
      page.getByText('Nothing to run. Pick what to fix, or choose a larger size.'),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Restore selected' })).toBeDisabled();
  });

  test('Best and Quick switch fix models and download sizes at Same size', async ({ page }) => {
    await openApp(page, scenario);
    await showPane(page, 'enhance');
    await fix(page, 'Noise').click();
    await page.getByRole('radio', { name: 'Same size' }).click();

    const quick = page.getByRole('button', { name: /^Quick/ });
    const best = page.getByRole('button', { name: /^Best/ });
    const stages = page.getByRole('list', { name: 'Stages' });
    await expect(quick).toHaveAttribute('title', /30 MB download/);
    await expect(best).toHaveAttribute('title', /464 MB download/);
    await page.locator('.foot-plan').click();
    await expect(stages).toContainText('RealPLKSR Denoise');
    await page.keyboard.press('Escape');

    await best.click();
    await page.locator('.foot-plan').click();
    await expect(stages).toContainText('NAFNet SIDD Width64');
    await page.keyboard.press('Escape');

    await quick.click();
    await page.locator('.foot-plan').click();
    await expect(stages).toContainText('RealPLKSR Denoise');
  });

  test('Size is one radiogroup and arrow keys move focus and selection', async ({ page }) => {
    await openApp(page, scenario);
    await showPane(page, 'enhance');

    const sizeGroup = page.getByRole('radiogroup', { name: 'Size' });
    const radios = sizeGroup.getByRole('radio');
    await expect(radios).toHaveCount(4);

    const fourX = sizeGroup.getByRole('radio', { name: '4×' });
    await fourX.focus();
    await expect(fourX).toHaveAttribute('aria-checked', 'true');

    await page.keyboard.press('ArrowLeft');
    const threeX = sizeGroup.getByRole('radio', { name: '3×' });
    await expect(threeX).toBeFocused();
    await expect(threeX).toHaveAttribute('aria-checked', 'true');

    await page.keyboard.press('ArrowRight');
    await expect(fourX).toBeFocused();
    await expect(fourX).toHaveAttribute('aria-checked', 'true');

    await page.keyboard.press('ArrowRight');
    const sameSize = sizeGroup.getByRole('radio', { name: 'Same size' });
    await expect(sameSize).toBeFocused();
    await expect(sameSize).toHaveAttribute('aria-checked', 'true');
  });
});
