import { expect, test, type Locator, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { hostCalls, type Scenario } from './fake-host';

const CUDA_EDIT: Scenario = {
  devices: 'cuda',
  platform: 'Win32',
  photos: 1,
  task: 'edit',
  worker: 'ready',
  editRuntime: '/fixture/sd-cli',
  features: ['image_edit', 'edit_placement', 'edit_expert'],
  installed: ['flux2_klein_4b_q4_0', 'qwen_edit_2511_q3_k_s'],
  settings: { edit_model_id: 'flux2_klein_4b_q4_0', edit_max_dimension: 1024 },
};

const MAC_EDIT: Scenario = {
  devices: 'mac',
  platform: 'MacIntel',
  photos: 1,
  task: 'edit',
  worker: 'ready',
  editRuntime: '/fixture/sd-cli',
  features: ['image_edit', 'edit_placement', 'edit_expert'],
  installed: ['flux2_klein_4b_q4_0'],
  settings: { edit_model_id: 'flux2_klein_4b_q4_0', edit_max_dimension: 512 },
};

const tabKey = (browserName: string) =>
  browserName === 'webkit' && process.platform === 'darwin' ? 'Alt+Tab' : 'Tab';

const box = async (locator: Locator) => (await locator.boundingBox())!;

async function hasFocusRing(locator: Locator): Promise<boolean> {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
    return element.matches(':focus-visible') && (outline || style.boxShadow !== 'none');
  });
}

async function openExpert(page: Page): Promise<void> {
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: /^Advanced/ }).click();
  await page.locator('#edit-expert').click();
  await expect(page.locator('.expert-controls')).toBeVisible();
}

test.describe('Edit Expert settings', () => {
  test('offers 1024 px on a 12 GB CUDA card and keeps Advanced rows steady when Expert opens', async ({
    page,
  }) => {
    await openApp(page, CUDA_EDIT);
    await showPane(page, 'enhance');
    await page.getByRole('button', { name: /^Advanced/ }).click();

    await expect(page.locator('#edit-size')).toHaveValue('1024');
    await expect(page.locator('#edit-size option[value="1024"]')).toBeEnabled();

    await page.locator('#edit-expert').scrollIntoViewIfNeeded();
    const sizeTop = (await box(page.locator('#edit-size'))).y;
    const deviceTop = (await box(page.locator('#device'))).y;
    const memoryTop = (await box(page.locator('.hardware-card'))).y;

    await page.locator('#edit-expert').click();
    await expect(page.locator('.expert-controls')).toBeVisible();

    expect(Math.abs((await box(page.locator('#edit-size'))).y - sizeTop)).toBeLessThan(0.5);
    expect(Math.abs((await box(page.locator('#device'))).y - deviceTop)).toBeLessThan(0.5);
    expect(Math.abs((await box(page.locator('.hardware-card'))).y - memoryTop)).toBeLessThan(0.5);

    const sizeLeft = (await box(page.locator('#edit-size'))).x;
    for (const id of ['#edit-text-encoder', '#edit-vae', '#edit-vram-limit']) {
      expect(Math.abs((await box(page.locator(id))).x - sizeLeft), id).toBeLessThan(0.5);
    }
  });

  test('labels fit cleanly at 880 x 640 with 125% interface scale and all tips wrap inside the column', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 880, height: 640 });
    await openApp(page, {
      ...CUDA_EDIT,
      settings: { ...CUDA_EDIT.settings, interface_scale: 125 },
    });
    await openExpert(page);

    for (const id of ['#edit-text-encoder', '#edit-vae', '#edit-vram-limit']) {
      const select = page.locator(id);
      const row = select.locator('..');
      const label = row.locator('label');
      const [scroll, client] = await label.evaluate((node) => [node.scrollWidth, node.clientWidth]);
      expect(scroll, id).toBeLessThanOrEqual(client);
      const labelBox = await box(label);
      const tipBox = await box(row.locator('.help-tip'));
      const selectBox = await box(select);
      expect(labelBox.x + labelBox.width, id).toBeLessThanOrEqual(selectBox.x + 0.5);
      expect(tipBox.x + tipBox.width, id).toBeLessThanOrEqual(selectBox.x + 0.5);
    }

    const column = await box(page.locator('.inspector-scroll'));
    const tips = page.locator('.expert-controls .help-tip');
    const count = await tips.count();
    expect(count).toBe(4);
    for (let index = 0; index < count; index++) {
      const tip = tips.nth(index);
      await tip.scrollIntoViewIfNeeded();
      const target = await box(tip);
      expect(
        Math.min(target.width, target.height),
        await tip.getAttribute('aria-label'),
      ).toBeGreaterThanOrEqual(24);
      await tip.click();
      const tooltip = page.getByRole('tooltip').filter({ visible: true });
      await expect(tooltip).toHaveCount(1);
      const label = await tip.getAttribute('aria-label');
      const [scroll, client] = await tooltip.evaluate((node) => [
        node.scrollWidth,
        node.clientWidth,
      ]);
      expect(scroll, label).toBeLessThanOrEqual(client);
      const shown = await box(tooltip);
      expect(shown.x, label).toBeGreaterThanOrEqual(column.x);
      expect(shown.x + shown.width, label).toBeLessThanOrEqual(column.x + column.width);
      expect(shown.y, label).toBeGreaterThanOrEqual(0);
      expect(shown.y + shown.height, label).toBeLessThanOrEqual(640);
      await page.keyboard.press('Escape');
    }
  });

  test('saves Expert overrides, reflects them on the Model line, and resets all four', async ({
    page,
  }) => {
    await openApp(page, CUDA_EDIT);
    await openExpert(page);

    await page.locator('#edit-text-encoder').selectOption('cpu');
    await expect(page.locator('#edit-expert small')).toHaveText('Changed');
    await expect(page.locator('.plan-note')).toContainText('Expert settings');
    let saved = (await hostCalls(page, 'save_settings')).at(-1)?.args as {
      settings: Record<string, unknown>;
    };
    expect(saved.settings.edit_text_encoder).toBe('cpu');

    await page.locator('#edit-expert-reset').click();
    await expect(page.locator('#edit-expert small')).toHaveText('Automatic');
    await expect(page.locator('.plan-note')).not.toContainText('Expert settings');
    saved = (await hostCalls(page, 'save_settings')).at(-1)?.args as {
      settings: Record<string, unknown>;
    };
    expect(saved.settings).toMatchObject({
      edit_text_encoder: 'auto',
      edit_vae: 'auto',
      edit_vram_limit_mb: 0,
      edit_allow_any_size: false,
    });
  });

  test('updates recommended sizes with GPU memory and sends Expert fields on Start', async ({
    page,
  }) => {
    await openApp(page, {
      ...CUDA_EDIT,
      settings: { edit_model_id: 'qwen_edit_2511_q3_k_s', edit_max_dimension: 512 },
    });
    await openExpert(page);

    await expect(page.locator('#edit-size option[value="768"]')).toBeDisabled();
    await expect(page.locator('#edit-size option[value="1024"]')).toBeDisabled();

    await page.locator('#edit-vram-limit').selectOption('11264');
    await expect(page.locator('#edit-size option[value="768"]')).toBeEnabled();
    await expect(page.locator('#edit-size option[value="1024"]')).toBeDisabled();

    await page.locator('#edit-any-size').check();
    await expect(page.locator('#edit-size option[value="1024"]')).toBeEnabled();
    await page.locator('#edit-size').selectOption('1024');
    await page.getByLabel('What to change').fill('Brighten the sky');
    await page.getByRole('button', { name: 'Edit selected' }).click();

    const start = (await hostCalls(page, 'start_edit')).at(-1);
    expect((start?.args as { input: Record<string, unknown> }).input).toMatchObject({
      max_dimension: 1024,
      edit_allow_any_size: true,
      edit_vram_limit_mb: 11264,
    });
  });

  test('omits GPU memory and Allow any edit size on a Mac', async ({ page }) => {
    await openApp(page, MAC_EDIT);
    await openExpert(page);

    await expect(page.locator('#edit-text-encoder')).toBeVisible();
    await expect(page.locator('#edit-vae')).toBeVisible();
    await expect(page.locator('#edit-expert-reset')).toBeVisible();
    await expect(page.locator('#edit-vram-limit')).toHaveCount(0);
    await expect(page.locator('#edit-any-size')).toHaveCount(0);
  });

  test('shows a focus ring on every Expert control when tabbed through', async ({
    page,
    browserName,
  }) => {
    await openApp(page, {
      ...CUDA_EDIT,
      settings: { ...CUDA_EDIT.settings, edit_text_encoder: 'cpu' },
    });
    await openExpert(page);

    const targets = [
      '#edit-expert',
      '#edit-text-encoder',
      '#edit-vae',
      '#edit-vram-limit',
      '#edit-any-size',
      '#edit-expert-reset',
    ];
    const missing: string[] = [];
    await page.locator('#edit-expert').focus();
    await page.keyboard.press(tabKey(browserName));
    const seen = new Set<string>();
    for (let step = 0; step < 20 && seen.size < targets.length - 1; step++) {
      const hit = await page.evaluate(
        (ids) =>
          ids.find((selector) => document.querySelector(selector) === document.activeElement),
        targets,
      );
      if (hit) {
        seen.add(hit);
        if (!(await hasFocusRing(page.locator(hit)))) missing.push(hit);
      }
      await page.keyboard.press(tabKey(browserName));
    }
    // Tab from the preceding control (.memory-refresh) onto #edit-expert so :focus-visible triggers.
    await page.locator('.memory-refresh').focus();
    await page.keyboard.press(tabKey(browserName));
    await expect(page.locator('#edit-expert')).toBeFocused();
    if (!(await hasFocusRing(page.locator('#edit-expert')))) missing.push('#edit-expert');
    expect([...seen].sort()).toEqual(targets.slice(1).sort());
    expect(missing).toEqual([]);
  });
});
