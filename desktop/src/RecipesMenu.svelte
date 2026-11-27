<script lang="ts">
  import { tick } from 'svelte';
  import { isRestoreRecipe } from './lib/recipes';
  import type { MediaItem, Recipe } from './lib/types';

  export let recipes: Recipe[];
  export let activeRecipeIds: Set<string>;
  export let selectedMediaKind: MediaItem['kind'] | undefined;
  export let canSave: boolean;
  export let recipeName: string;
  export let recipeEditorOpen: boolean;
  export let deletingRecipeId: string | null;
  export let openRecipeEditor: () => Promise<void>;
  export let applyRecipe: (recipe: Recipe) => void;
  export let deleteRecipe: (recipeId: string) => void;
  export let addRecipe: () => Promise<void>;
  export let onClose: () => void;

  let menuEl: HTMLDivElement | undefined;
  let nameInput: HTMLInputElement | undefined;
  let saveButton: HTMLButtonElement | undefined;

  async function startEditor(): Promise<void> {
    await openRecipeEditor();
    await tick();
    nameInput?.focus();
  }

  async function askDelete(recipeId: string): Promise<void> {
    deletingRecipeId = recipeId;
    await tick();
    menuEl
      ?.querySelector<HTMLButtonElement>('.saved-recipe.confirming button.recipe-cancel')
      ?.focus();
  }

  function handleKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape' && deletingRecipeId !== null) {
      const cancelledId = deletingRecipeId;
      event.preventDefault();
      event.stopPropagation();
      deletingRecipeId = null;
      void tick().then(() => {
        const row = Array.from(menuEl?.querySelectorAll<HTMLElement>('.saved-recipe') ?? []).find(
          (node) => node.dataset.recipeId === cancelledId,
        );
        row?.querySelector<HTMLButtonElement>('.recipe-delete')?.focus();
      });
    }
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div bind:this={menuEl} class="recipes-menu" on:keydown={handleKeydown}>
  {#if recipes.length === 0}
    <p class="recipes-empty">No recipes yet.</p>
  {:else}
    <div class="recipes-list">
      {#each recipes as recipe (recipe.id)}
        {@const active = activeRecipeIds.has(recipe.id)}
        <div
          class="saved-recipe"
          data-recipe-id={recipe.id}
          class:confirming={deletingRecipeId === recipe.id}
        >
          {#if deletingRecipeId === recipe.id}
            <p class="recipe-question">
              <b>Delete {recipe.name}?</b><span>This can’t be undone.</span>
            </p>
            <button
              class="danger"
              type="button"
              aria-label={`Confirm delete ${recipe.name}`}
              on:click={() => deleteRecipe(recipe.id)}>Delete</button
            >
            <button
              class="recipe-cancel"
              type="button"
              aria-label={`Cancel deleting ${recipe.name}`}
              on:click={() => (deletingRecipeId = null)}>Cancel</button
            >
          {:else}
            <button
              type="button"
              class:active
              aria-pressed={active}
              disabled={Boolean(selectedMediaKind) &&
                (recipe.task === 'video') !== (selectedMediaKind === 'video')}
              on:click={() => {
                applyRecipe(recipe);
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
                <b>{recipe.name}</b>
                <span
                  >{isRestoreRecipe(recipe)
                    ? recipe.task === 'video'
                      ? 'video · same size'
                      : 'same size'
                    : `${recipe.task} · ${recipe.output_scale}×`} · {recipe.precision.toUpperCase()}</span
                >
              </span>
            </button><button
              class="recipe-delete"
              type="button"
              aria-label={`Delete ${recipe.name}`}
              on:click={() => void askDelete(recipe.id)}
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2.5"
                stroke-linecap="round"
                stroke-linejoin="round"><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg
              >
            </button>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
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
