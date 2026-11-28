// @vitest-environment jsdom

import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import RecipesMenu from './RecipesMenu.svelte';
import { demoSnapshot } from './lib/demo';
import type { Recipe } from './lib/types';

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
    ...overrides,
  };
}

describe('Recipes menu', () => {
  it('shows names with plan lines, no Delete buttons, and opens the library on Browse recipes…', async () => {
    const user = userEvent.setup();
    const { catalog } = demoSnapshot();
    const openRecipesLibrary = vi.fn();
    const onClose = vi.fn();
    const recipes = [
      makeRecipe('r1', 'Phone photos', {
        fixes: ['jpeg', 'noise'],
        stages: [
          { kind: 'deblock', model_id: 'fbcnn_color', fix: 'jpeg' },
          { kind: 'restore', model_id: 'denoise_realplksr_1x', fix: 'noise' },
          { kind: 'upscale', model_id: 'localsr_quick_x4_v1' },
        ],
      }),
      makeRecipe('r2', 'Clean noise', {
        task: 'denoise',
        output_scale: 1,
        model_id: 'denoise_realplksr_1x',
        fixes: ['noise'],
        stages: [{ kind: 'restore', model_id: 'denoise_realplksr_1x', fix: 'noise' }],
      }),
      makeRecipe('r3', 'Quick 2x', { output_scale: 2 }),
    ];

    render(RecipesMenu, {
      props: {
        recipes,
        catalog,
        pins: ['r1'],
        recent: [],
        activeRecipeIds: new Set(['r2']),
        selectedMediaKind: 'image',
        canSave: true,
        recipeName: '',
        recipeEditorOpen: false,
        openRecipeEditor: vi.fn(async () => {}),
        openRecipesLibrary,
        applyRecipe: vi.fn(),
        addRecipe: vi.fn(async () => {}),
        onClose,
      },
    });

    const first = screen.getByRole('button', { name: /^Phone photos/ });
    expect(first.textContent).toContain('JPEG → Noise → Upscale 4×');
    expect(first.querySelector('.recipe-pin svg')).not.toBeNull();
    expect(screen.getByRole('button', { name: /^Clean noise/ }).textContent).toContain('Noise');
    expect(screen.getByRole('button', { name: /^Quick 2x/ }).textContent).toContain('Upscale 2×');
    expect(screen.queryByRole('button', { name: /^Delete / })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Browse recipes…' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(openRecipesLibrary).toHaveBeenCalledTimes(1);
  });

  it('shows Update with the recipe name, and disables Save with the limit wording at 100', async () => {
    const user = userEvent.setup();
    const { catalog } = demoSnapshot();
    const updateRecipe = vi.fn(async () => {});
    const recipes = Array.from({ length: 100 }, (_, index) =>
      makeRecipe(`r${index}`, `R ${index}`),
    );
    render(RecipesMenu, {
      props: {
        recipes,
        catalog,
        activeRecipeIds: new Set<string>(),
        selectedMediaKind: 'image',
        canSave: true,
        recipeName: '',
        recipeEditorOpen: false,
        openRecipeEditor: vi.fn(async () => {}),
        openRecipesLibrary: vi.fn(),
        applyRecipe: vi.fn(),
        addRecipe: vi.fn(async () => {}),
        updatableRecipe: recipes[0],
        updateRecipe,
        onClose: vi.fn(),
      },
    });
    const save = screen.getByRole('button', {
      name: 'Save current as recipe…',
    }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(save.title).toBe('100 recipes is the limit. Delete one in Browse recipes… first.');
    await user.click(screen.getByRole('button', { name: 'Update “R 0”' }));
    expect(updateRecipe).toHaveBeenCalledTimes(1);
  });
});
