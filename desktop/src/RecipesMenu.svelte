<script lang="ts">
  import { tick } from 'svelte';
  import { menuRows, recipePlanLine } from './lib/recipeList';
  import type { AppSnapshot, MediaItem, Recipe } from './lib/types';

  export let recipes: Recipe[];
  export let catalog: AppSnapshot['catalog'];
  export let pins: string[] = [];
  export let recent: string[] = [];
  export let activeRecipeIds: Set<string>;
  export let selectedMediaKind: MediaItem['kind'] | undefined;
  export let canSave: boolean;
  export let recipeName: string;
  export let recipeEditorOpen: boolean;
  export let openRecipeEditor: () => Promise<void>;
  export let openRecipesLibrary: () => void;
  export let applyRecipe: (recipe: Recipe) => void;
  export let addRecipe: () => Promise<void>;
  export let onClose: () => void;

  let nameInput: HTMLInputElement | undefined;
  let saveButton: HTMLButtonElement | undefined;

  $: rows = menuRows(recipes, {
    pins,
    recent,
    mediaKind: selectedMediaKind,
  });

  async function startEditor(): Promise<void> {
    await openRecipeEditor();
    await tick();
    nameInput?.focus();
  }
</script>

<div class="recipes-menu">
  {#if recipes.length === 0}
    <p class="recipes-empty">No recipes yet.</p>
  {:else if rows.length > 0}
    <div class="recipes-list">
      {#each rows as row (row.id)}
        {#if row.kind === 'separator'}
          <hr class="pop-sep" />
        {:else if row.kind === 'recipe'}
          {@const active = activeRecipeIds.has(row.recipe.id)}
          <div class="saved-recipe" data-recipe-id={row.recipe.id}>
            <button
              type="button"
              class:active
              aria-pressed={active}
              disabled={row.disabled}
              on:click={() => {
                applyRecipe(row.recipe);
                onClose();
              }}
            >
              <span class="recipe-check" aria-hidden="true">
                {#if active}
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.5"
                    stroke-linecap="round"
                    stroke-linejoin="round"><path d="m5 12 5 5L20 7" /></svg
                  >
                {/if}
              </span>
              <span class="recipe-copy">
                <b>{row.recipe.name}</b>
                <span>{recipePlanLine(row.recipe, catalog)}</span>
              </span>
              {#if row.pinned}
                <span class="recipe-pin" aria-hidden="true">⌃</span>
              {/if}
            </button>
          </div>
        {/if}
      {/each}
    </div>
  {/if}
  <hr class="pop-sep" />
  <button
    class="link-button recipes-browse"
    type="button"
    on:click={() => {
      onClose();
      openRecipesLibrary();
    }}>Browse recipes…</button
  >
  <hr class="pop-sep" />
  {#if recipeEditorOpen}
    <div class="recipe-input">
      <input
        bind:this={nameInput}
        aria-label="Recipe name"
        placeholder="Name this setup"
        bind:value={recipeName}
        on:keydown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            void addRecipe();
          }
          if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            recipeEditorOpen = false;
            void tick().then(() => saveButton?.focus());
          }
        }}
      /><button type="button" on:click={() => void addRecipe()}>Save</button>
    </div>
  {:else}
    <button
      bind:this={saveButton}
      class="link-button recipes-save"
      type="button"
      disabled={!canSave}
      on:click={() => void startEditor()}>Save current as recipe…</button
    >
  {/if}
</div>
