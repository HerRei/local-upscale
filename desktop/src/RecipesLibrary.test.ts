// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RecipesLibrary from './RecipesLibrary.svelte';
import { demoSnapshot } from './lib/demo';
import { togglePin } from './lib/recipeList';
import type { AppSnapshot, MediaItem, Recipe } from './lib/types';

afterEach(cleanup);

function makeRecipe(id: string, name: string, overrides: Partial<Recipe> = {}): Recipe {
  return {
    id,
    name,
    task: 'upscale',
    model_id: 'localsr_quick_x4_v1',
    output_scale: 4,
    tile_size: 256,
    halo: 32,
    precision: 'fp16',
    safe_memory: false,
    quality: 'quick',
    output_format: 'png',
    ...overrides,
  };
}

function mountLibrary(
  customize?: (snapshot: AppSnapshot) => void,
  options: {
    selectedMediaKind?: MediaItem['kind'];
    activeRecipeIds?: Set<string>;
    busy?: boolean;
  } = {},
) {
  const snapshot = demoSnapshot();
  customize?.(snapshot);
  const close = vi.fn();
  const applyRecipe = vi.fn();
  const togglePinRecipe = vi.fn((id: string) => {
    snapshot.settings = {
      ...snapshot.settings,
      recipe_pins: togglePin(snapshot.settings.recipe_pins, id),
    };
    void rendered.rerender({ snapshot, settings: snapshot.settings });
  });
  const duplicateRecipe = vi.fn(async (recipe: Recipe) => {
    const copy: Recipe = { ...recipe, id: `${recipe.id}-copy`, name: `${recipe.name} copy` };
    snapshot.recipes = [...snapshot.recipes, copy];
    await rendered.rerender({ snapshot, settings: snapshot.settings });
    return copy.id;
  });
  const renameRecipe = vi.fn(async (recipe: Recipe, name: string) => {
    snapshot.recipes = snapshot.recipes.map((entry) =>
      entry.id === recipe.id ? { ...entry, name } : entry,
    );
    await rendered.rerender({ snapshot, settings: snapshot.settings });
    return true;
  });
  const deleteRecipe = vi.fn(async (id: string) => {
    snapshot.recipes = snapshot.recipes.filter((entry) => entry.id !== id);
    await rendered.rerender({ snapshot, settings: snapshot.settings });
    return true;
  });

  const rendered = render(RecipesLibrary, {
    props: {
      snapshot,
      settings: snapshot.settings,
      activeRecipeIds: options.activeRecipeIds ?? new Set<string>(),
      selectedMediaKind: options.selectedMediaKind ?? 'image',
      busy: options.busy ?? false,
      close,
      applyRecipe,
      togglePinRecipe,
      duplicateRecipe,
      renameRecipe,
      deleteRecipe,
    },
  });

  return {
    snapshot,
    close,
    applyRecipe,
    togglePinRecipe,
    duplicateRecipe,
    renameRecipe,
    deleteRecipe,
    rerender: rendered.rerender,
  };
}

describe('Recipes library', () => {
  it('focuses search on open, selects My recipes, and orders pinned rows first', () => {
    mountLibrary((snapshot) => {
      snapshot.recipes = [
        makeRecipe('r1', 'Alpha'),
        makeRecipe('r2', 'Beta'),
        makeRecipe('r3', 'Gamma'),
      ];
      snapshot.settings.recipe_pins = ['r3'];
    });

    const search = screen.getByRole('searchbox', { name: 'Search recipes' });
    expect(document.activeElement).toBe(search);

    const rail = screen.getByRole('navigation', { name: 'Recipe groups' });
    expect(
      within(rail)
        .getByRole('button', { name: /^My recipes/ })
        .getAttribute('aria-pressed'),
    ).toBe('true');
    expect(within(rail).getByRole('button', { name: /^Recommended/ })).toBeTruthy();

    const list = screen.getByRole('region', { name: 'Recipes' });
    const rows = list.querySelectorAll('button.row');
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain('Gamma');
    expect(rows[0].textContent).toContain('⌃');
    expect(rows[1].textContent).toContain('Alpha');
    expect(rows[2].textContent).toContain('Beta');
  });

  it('filters rows as you type, restores on clear, and shows empty messages', async () => {
    const user = userEvent.setup();
    mountLibrary((snapshot) => {
      snapshot.recipes = [
        makeRecipe('r1', 'Old slides'),
        makeRecipe('r2', 'Phone photos'),
        makeRecipe('r3', 'Slide scans'),
      ];
    });

    const search = screen.getByRole('searchbox', { name: 'Search recipes' });
    const list = screen.getByRole('region', { name: 'Recipes' });

    await user.type(search, 'slide');
    expect(list.querySelectorAll('button.row')).toHaveLength(2);
    expect(within(list).queryByRole('button', { name: /^Phone photos/ })).toBeNull();

    await user.clear(search);
    expect(list.querySelectorAll('button.row')).toHaveLength(3);

    await user.type(search, 'nonexistent');
    expect(list.querySelectorAll('button.row')).toHaveLength(0);
    expect(within(list).getByText('No recipes match “nonexistent”.')).toBeTruthy();
  });

  it('shows the My recipes empty state when no recipes exist', async () => {
    const user = userEvent.setup();
    mountLibrary((snapshot) => {
      snapshot.recipes = [];
    });

    const list = screen.getByRole('region', { name: 'Recipes' });
    expect(
      within(list).getByText(
        'No recipes yet. Set up the panel, then choose Recipes › Save current as recipe…',
      ),
    ).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /^Recommended/ }));
    expect(within(list).getByText('No recommended workflows yet.')).toBeTruthy();
  });

  it('moves down the list with arrow keys and applies on Enter, double-click, or Escape closes', async () => {
    const user = userEvent.setup();
    const { applyRecipe, close, snapshot } = mountLibrary((state) => {
      state.recipes = [makeRecipe('r1', 'Alpha'), makeRecipe('r2', 'Beta')];
    });

    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}');
    expect(applyRecipe).toHaveBeenCalledTimes(1);
    expect(applyRecipe).toHaveBeenCalledWith(snapshot.recipes[1]);
    expect(close).toHaveBeenCalledTimes(1);

    await user.dblClick(screen.getByRole('button', { name: /^Alpha/ }));
    expect(applyRecipe).toHaveBeenCalledTimes(2);
    expect(applyRecipe).toHaveBeenLastCalledWith(snapshot.recipes[0]);

    await user.keyboard('{Escape}');
    expect(close).toHaveBeenCalledTimes(3);
  });

  it('marks mismatched video recipes with Video and disables Apply with its title', async () => {
    const user = userEvent.setup();
    const { applyRecipe } = mountLibrary(
      (snapshot) => {
        snapshot.recipes = [
          makeRecipe('v1', 'Clips for family', { task: 'video', video_task: 'upscale' }),
        ];
      },
      { selectedMediaKind: 'image' },
    );

    const row = screen.getByRole('button', { name: /^Clips for family/ });
    expect(row.textContent).toContain('Video');

    const applyBtn = screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement;
    expect(applyBtn.disabled).toBe(true);
    expect(applyBtn.getAttribute('title')).toBe('Select a video first.');

    await user.dblClick(row);
    expect(applyRecipe).not.toHaveBeenCalled();
  });

  it('shows steps with model names, download totals, Needs your file, and detail metadata', async () => {
    const user = userEvent.setup();
    mountLibrary(
      (snapshot) => {
        snapshot.recipes = [
          makeRecipe('slides', 'Old slides', {
            output_scale: 2,
            quality: 'quick',
            output_format: 'png',
            tile_size: 256,
            halo: 32,
            precision: 'fp16',
            safe_memory: true,
            fixes: ['jpeg'],
            stages: [
              { kind: 'deblock', model_id: 'fbcnn_color', fix: 'jpeg' },
              { kind: 'upscale', model_id: 'localsr_quick_x4_v1' },
            ],
          }),
          makeRecipe('custom', 'My checkpoint', {
            model_id: '__custom__',
            custom_model_path: '',
            quality: 'custom',
          }),
        ];
      },
      { activeRecipeIds: new Set(['slides']) },
    );

    await user.click(screen.getByRole('button', { name: /^Old slides/ }));
    const detail = screen.getByLabelText('Recipe details');
    expect(within(detail).getByText('Photo recipe')).toBeTruthy();
    expect(within(detail).getByText('FBCNN Color')).toBeTruthy();
    expect(within(detail).getByText('LocalSR Photo S')).toBeTruthy();
    expect(within(detail).getByText('290 MB to download')).toBeTruthy();
    expect(within(detail).getByText('PNG')).toBeTruthy();
    expect(within(detail).getByText('Quick')).toBeTruthy();
    expect(within(detail).getByText('Tile 256 · Halo 32 · FP16 · Safe memory')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /^My checkpoint/ }));
    expect(within(detail).getByText('Needs your file')).toBeTruthy();
    expect(within(detail).getByText('Your own choice')).toBeTruthy();
    expect(within(detail).getByText('Nothing to download')).toBeTruthy();
  });

  it('pins and unpins a recipe, adding the mark and moving the row up', async () => {
    const user = userEvent.setup();
    const { togglePinRecipe } = mountLibrary((snapshot) => {
      snapshot.recipes = [makeRecipe('r1', 'Alpha'), makeRecipe('r2', 'Zebra')];
    });

    await user.click(screen.getByRole('button', { name: /^Zebra/ }));
    await user.click(screen.getByRole('button', { name: 'Pin' }));
    expect(togglePinRecipe).toHaveBeenCalledWith('r2');

    const rows = screen.getByRole('region', { name: 'Recipes' }).querySelectorAll('button.row');
    expect(rows[0].textContent).toContain('Zebra');
    expect(rows[0].textContent).toContain('⌃');
    expect(screen.getByRole('button', { name: 'Unpin' })).toBeTruthy();
  });

  it('renames a recipe on Enter and cancels on Escape without saving', async () => {
    const user = userEvent.setup();
    const { renameRecipe, close } = mountLibrary((snapshot) => {
      snapshot.recipes = [makeRecipe('r1', 'Old slides')];
    });

    await user.click(screen.getByRole('button', { name: 'Rename' }));
    const field = screen.getByRole('textbox', { name: 'Recipe name' }) as HTMLInputElement;
    expect(field.maxLength).toBe(60);

    await user.type(field, 'Ignored{Escape}');
    expect(renameRecipe).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    expect(screen.queryByRole('textbox', { name: 'Recipe name' })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Rename' }));
    const field2 = screen.getByRole('textbox', { name: 'Recipe name' });
    await user.clear(field2);
    await user.type(field2, 'Scanned slides{Enter}');
    expect(renameRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'r1' }),
      'Scanned slides',
    );
    expect(screen.getByRole('heading', { name: 'Scanned slides' })).toBeTruthy();
  });

  it('duplicates a recipe, selects the copy, and disables Duplicate at 100 recipes', async () => {
    const user = userEvent.setup();
    const { duplicateRecipe, rerender, snapshot } = mountLibrary((state) => {
      state.recipes = [makeRecipe('r1', 'Old slides')];
    });

    await user.click(screen.getByRole('button', { name: 'Duplicate' }));
    expect(duplicateRecipe).toHaveBeenCalledWith(expect.objectContaining({ id: 'r1' }));
    expect(screen.getByRole('heading', { name: 'Old slides copy' })).toBeTruthy();

    snapshot.recipes = Array.from({ length: 100 }, (_, i) => makeRecipe(`r${i}`, `Recipe ${i}`));
    await rerender({ snapshot, settings: snapshot.settings });
    expect((screen.getByRole('button', { name: 'Duplicate' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('asks before deleting a recipe and supports Cancel, Escape, and Delete', async () => {
    const user = userEvent.setup();
    const { deleteRecipe, close } = mountLibrary((snapshot) => {
      snapshot.recipes = [makeRecipe('r1', 'Phone photos'), makeRecipe('r2', 'Second')];
    });

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    expect(deleteRecipe).not.toHaveBeenCalled();
    expect(screen.getByText('Delete Phone photos?')).toBeTruthy();
    expect(screen.getByText('This can’t be undone.')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(deleteRecipe).not.toHaveBeenCalled();
    expect(screen.queryByText('Delete Phone photos?')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    await user.keyboard('{Escape}');
    expect(deleteRecipe).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    expect(screen.queryByText('Delete Phone photos?')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    await user.click(screen.getByRole('button', { name: 'Delete' }));
    expect(deleteRecipe).toHaveBeenCalledWith('r1');
    expect(screen.queryByRole('button', { name: /^Phone photos/ })).toBeNull();
  });
});
