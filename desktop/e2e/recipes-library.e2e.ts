import { expect, test, type Page } from '@playwright/test';
import { openApp, showPane } from './app';
import { emit, hostCalls } from './fake-host';
import { empty, ready } from './fixtures';

const recipe = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id,
  name,
  task: 'upscale',
  model_id: 'span_photo_x4',
  output_scale: 4,
  tile_size: 512,
  halo: 32,
  precision: 'fp32',
  safe_memory: false,
  ...extra,
});

const thirty = Array.from({ length: 30 }, (_, i) => recipe(`r${i + 1}`, `Recipe ${i + 1}`));
const READY = { ...ready, installed: [...(ready.installed ?? []), 'span_photo_x4'] };
const few = [recipe('a', 'Phone photos'), recipe('b', 'Old slides', { output_scale: 2 })];

const dialog = (page: Page) => page.getByRole('dialog', { name: 'Recipes' });

async function openMenu(page: Page): Promise<void> {
  await showPane(page, 'enhance');
  await page.getByRole('button', { name: 'Recipes', exact: true }).click();
}

async function openLibrary(page: Page): Promise<void> {
  await openMenu(page);
  await page.getByRole('button', { name: 'Browse recipes…' }).click();
  await expect(dialog(page)).toBeVisible();
}

const row = (page: Page, name: string) =>
  dialog(page).locator('.row').filter({ hasText: name }).first();

test.describe('Recipes menu', () => {
  test('30 recipes: six rows, the rest in the library', async ({ page }) => {
    await openApp(page, {
      ...ready,
      recipes: thirty,
      settings: { ...ready.settings, recipe_pins: thirty.slice(0, 7).map((r) => r.id) },
    });
    await openMenu(page);
    await expect(page.locator('.recipes-menu .saved-recipe')).toHaveCount(6);
    await expect(page.getByRole('button', { name: 'Browse recipes…' })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Delete / })).toHaveCount(0);
  });

  test('pinned rows lead with the pin mark; Update appears after a change', async ({ page }) => {
    await openApp(page, {
      ...ready,
      recipes: few,
      settings: { ...ready.settings, recipe_pins: ['b'] },
    });
    await openMenu(page);
    const rows = page.locator('.recipes-menu .saved-recipe');
    await expect(rows.first()).toContainText('Old slides');
    await expect(rows.first().locator('.recipe-pin')).toBeVisible();
    await expect(page.getByRole('button', { name: /^Update / })).toHaveCount(0);

    await rows.nth(1).getByRole('button').click();
    await page.getByRole('button', { name: 'Recipes', exact: true }).click();
    await expect(page.getByRole('button', { name: /^Update / })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await page.getByRole('radio', { name: '2×' }).click();
    await page.getByRole('button', { name: 'Recipes', exact: true }).click();
    await page.getByRole('button', { name: 'Update “Phone photos”' }).click();
    await expect.poll(async () => (await hostCalls(page, 'save_recipe')).length).toBeGreaterThan(0);
    const saved = (await hostCalls(page, 'save_recipe')).at(-1)!;
    expect(JSON.stringify(saved.args)).toContain('"id":"a"');
    expect(JSON.stringify(saved.args)).toContain('"output_scale":2');
  });
});

test.describe('Recipes library', () => {
  test('30 recipes: only the list scrolls, search narrows it, the dialog fits', async ({
    page,
  }) => {
    await openApp(page, { ...ready, recipes: thirty });
    await openLibrary(page);
    const sheet = dialog(page);
    const box = (await sheet.boundingBox())!;
    const viewport = page.viewportSize()!;
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
    const scrolls = await sheet
      .locator('.sheet-body, .sheet-head, .rail, .detail, .recipes-rows')
      .evaluateAll((els) =>
        els.map((el) => ({
          cls: el.className,
          scrolls: el.scrollHeight > el.clientHeight + 1,
        })),
      );
    expect(scrolls.filter((entry) => entry.scrolls).map((entry) => entry.cls)).toEqual([
      'recipes-rows',
    ]);
    await expect(sheet.getByRole('button', { name: /^My recipes/ })).toContainText('30');
    await sheet.getByRole('searchbox', { name: 'Search recipes' }).fill('recipe 2'); // every typed word counts: 2, 12 and 20 to 29
    await expect(sheet.locator('.row')).toHaveCount(12);
    await sheet.getByRole('searchbox', { name: 'Search recipes' }).fill('zzz');
    await expect(sheet.getByText('No recipes match “zzz”.')).toBeVisible();
  });

  test('empty My recipes opens on Recommended; the rail switches groups', async ({ page }) => {
    await openApp(page, ready);
    await openLibrary(page);
    const sheet = dialog(page);
    await expect(sheet.getByRole('button', { name: /^Recommended/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await sheet.getByRole('button', { name: /^My recipes/ }).click();
    await expect(
      sheet.getByText('No recipes yet. Set up the panel, then choose Recipes › Save current'),
    ).toBeVisible();
    await sheet.getByRole('button', { name: /^Photo/ }).click();
    await expect(sheet.locator('.row')).toHaveCount(4);
  });

  test('apply, pin, duplicate, rename and delete with confirmation', async ({ page }) => {
    await openApp(page, {
      ...READY,
      recipes: few,
      settings: { ...ready.settings, recipe_pins: [] },
    });
    await openLibrary(page);
    const sheet = dialog(page);
    await expect(sheet.getByRole('button', { name: /^My recipes/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    await row(page, 'Old slides').click();
    await expect(sheet.locator('.detail')).toContainText('Photo recipe');
    await expect(sheet.locator('.detail')).toContainText('Nothing to download');

    await sheet.getByRole('button', { name: 'Pin', exact: true }).click();
    await expect(sheet.getByRole('button', { name: 'Unpin', exact: true })).toBeVisible();
    await expect(sheet.locator('.row').first()).toContainText('Old slides');
    await expect(sheet.locator('.row').first().locator('.recipe-pin')).toBeVisible();

    await sheet.getByRole('button', { name: 'Duplicate', exact: true }).click();
    await expect(sheet.locator('.row').filter({ hasText: 'Old slides copy' })).toHaveCount(1);
    await expect(sheet.locator('.detail')).toContainText('Old slides copy');

    await sheet.getByRole('button', { name: 'Rename', exact: true }).click();
    const name = sheet.getByRole('textbox', { name: 'Recipe name' });
    await name.fill('Renamed');
    await page.keyboard.press('Enter');
    await expect(sheet.locator('.row').filter({ hasText: 'Renamed' })).toHaveCount(1);
    expect(
      (await hostCalls(page, 'save_recipe')).some((call) =>
        JSON.stringify(call.args).includes('"name":"Renamed"'),
      ),
    ).toBe(true);

    await sheet.getByRole('button', { name: 'Rename', exact: true }).click();
    await sheet.getByRole('textbox', { name: 'Recipe name' }).fill('Dropped');
    await page.keyboard.press('Escape');
    await expect(dialog(page)).toBeVisible();
    await expect(sheet.locator('.row').filter({ hasText: 'Dropped' })).toHaveCount(0);

    await sheet.getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(sheet.getByText('This can’t be undone.')).toBeVisible();
    await sheet.getByRole('button', { name: 'Cancel' }).click();
    await expect(sheet.locator('.row').filter({ hasText: 'Renamed' })).toHaveCount(1);
    await sheet.getByRole('button', { name: 'Delete', exact: true }).click();
    await sheet
      .locator('.recipe-question')
      .locator('..')
      .getByRole('button', { name: 'Delete' })
      .click();
    await expect(sheet.locator('.row').filter({ hasText: 'Renamed' })).toHaveCount(0);
    expect((await hostCalls(page, 'delete_recipe')).length).toBe(1);

    await row(page, 'Phone photos').click();
    await sheet.getByRole('button', { name: 'Apply', exact: true }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Recipes', exact: true })).toBeFocused();
  });

  test('keyboard: search, arrows, Enter applies, Escape closes', async ({ page }) => {
    await openApp(page, { ...ready, recipes: few });
    await openLibrary(page);
    const search = dialog(page).getByRole('searchbox', { name: 'Search recipes' });
    await expect(search).toBeFocused();
    await search.fill('slides');
    await page.keyboard.press('ArrowDown');
    await expect(dialog(page).locator('.row').first()).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(dialog(page)).toHaveCount(0);
    await expect(page.getByRole('radio', { name: '2×' })).toBeChecked();

    await page.getByRole('button', { name: 'Recipes', exact: true }).click();
    await page.getByRole('button', { name: 'Browse recipes…' }).click();
    await page.keyboard.press('Escape');
    await expect(dialog(page)).toHaveCount(0);
  });

  test('arrows move through the rows and Tab reaches the actions', async ({ page }) => {
    await openApp(page, { ...ready, recipes: few });
    await openLibrary(page);
    const rows = dialog(page).locator('.row');
    await page.keyboard.press('ArrowDown');
    await expect(rows.first()).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await expect(rows.nth(1)).toBeFocused();
    await page.keyboard.press('ArrowUp');
    await expect(rows.first()).toBeFocused();
    // The remaining rows are tab stops too; the actions follow them in order.
    const apply = dialog(page).getByRole('button', { name: 'Apply', exact: true });
    for (let i = 0; i < 6 && !(await apply.evaluate((el) => el === document.activeElement)); i++)
      await page.keyboard.press('Tab');
    await expect(apply).toBeFocused();
    for (const name of ['Pin', 'Duplicate', 'Rename', 'Delete']) {
      await page.keyboard.press('Tab');
      await expect(dialog(page).getByRole('button', { name, exact: true })).toBeFocused();
    }
  });

  test('a double click applies a recipe and closes the dialog', async ({ page }) => {
    await openApp(page, { ...ready, recipes: few });
    await openLibrary(page);
    await row(page, 'Phone photos').dblclick();
    await expect(dialog(page)).toHaveCount(0);
  });
});

test.describe('Workflows', () => {
  test('Recommended cards: Use workflow fills the panel and starts nothing', async ({ page }) => {
    await openApp(page, { ...ready, recipes: few });
    await openLibrary(page);
    const sheet = dialog(page);
    await sheet.getByRole('button', { name: /^Recommended/ }).click();
    await expect(sheet.locator('.row')).toHaveCount(4);
    await row(page, 'Web image (JPEG)').click();
    const detail = sheet.locator('.detail');
    await expect(detail).toContainText('Recommended workflow');
    await expect(detail).toContainText('For small, blocky pictures saved from websites or chats.');
    await expect(detail).toContainText('288 MB to download');
    await sheet.getByRole('button', { name: 'Use workflow' }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(page.getByRole('checkbox', { name: 'JPEG artifacts' })).toBeChecked();
    await expect(page.getByRole('radio', { name: '2×' })).toBeChecked();
    await expect(page.locator('.foot-plan')).toContainText('JPEG');
    expect((await hostCalls(page, 'start_job')).length).toBe(0);
    expect((await hostCalls(page, 'save_recipe')).length).toBe(0);
  });

  test('Duplicate to My recipes saves a recipe and applies nothing', async ({ page }) => {
    await openApp(page, { ...ready, recipes: few });
    await openLibrary(page);
    const sheet = dialog(page);
    await sheet.getByRole('button', { name: /^Recommended/ }).click();
    await row(page, 'Drawing or anime').click();
    await sheet.getByRole('button', { name: 'Duplicate to My recipes' }).click();
    const saved = await hostCalls(page, 'save_recipe');
    expect(saved).toHaveLength(1);
    expect(JSON.stringify(saved[0].args)).toContain('Drawing or anime');
    await sheet.getByRole('button', { name: /^My recipes/ }).click();
    await expect(row(page, 'Drawing or anime')).toBeVisible();
  });

  test('a pinned workflow is a row in the menu and in the Presets handler', async ({ page }) => {
    await openApp(page, {
      ...ready,
      recipes: few,
      settings: { ...ready.settings, recipe_pins: ['workflow.low-light'] },
    });
    await openMenu(page);
    const rows = page.locator('.recipes-menu .saved-recipe');
    await expect(rows.first()).toContainText('Low-light phone photo');
    await expect(rows.first().locator('.recipe-pin')).toBeVisible();
    await rows.first().getByRole('button').click();
    await expect(page.getByRole('checkbox', { name: 'Noise', exact: true })).toBeChecked();
    await expect(page.getByRole('radio', { name: 'Same size' })).toBeChecked();

    await page.getByRole('radio', { name: '2×' }).click();
    await emit(page, 'native-menu-action', 'preset.recipe.workflow.low-light');
    await expect(page.getByRole('radio', { name: 'Same size' })).toBeChecked();
  });

  test('Shift+Cmd+R, as the native menu action, opens the library', async ({ page }) => {
    await openApp(page, { ...ready, recipes: few });
    await showPane(page, 'enhance');
    await emit(page, 'native-menu-action', 'preset.browse');
    await expect(dialog(page)).toBeVisible();
  });

  test('the empty-screen link opens Recommended; Use workflow sets the panel only', async ({
    page,
  }) => {
    await openApp(page, empty);
    await page.getByRole('button', { name: 'or start from a workflow…' }).click();
    const sheet = dialog(page);
    await expect(sheet.getByRole('button', { name: /^Recommended/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await row(page, 'Old scan').click();
    await sheet.getByRole('button', { name: 'Use workflow' }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'or start from a workflow…' })).toBeVisible();
    await showPane(page, 'enhance');
    await expect(page.getByRole('radio', { name: '2×' })).toBeChecked();
    await expect(
      page.getByRole('group', { name: 'Quality' }).getByRole('button', { name: 'Best' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect((await hostCalls(page, 'start_job')).length).toBe(0);
  });
});
