<script lang="ts">
  import { tick } from 'svelte';
  import { menuRows, recipePlanLine } from './lib/recipeList';
  import type { AppSnapshot, MediaItem, Recipe } from './lib/types';
  import type { Workflow } from './lib/workflows';

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
  /** The built-in workflows: a pinned one is a row, and with nothing of your own they all are. */
  export let workflows: readonly Workflow[] = [];
  export let applyWorkflow: (workflow: Workflow) => void = () => {};
  export let workflowLine: (workflow: Workflow) => string = () => '';
  export let workflowActive: (workflow: Workflow) => boolean = () => false;
  export let addRecipe: () => Promise<void>;
  export let updatableRecipe: Recipe | undefined = undefined;
  export let updateRecipe: () => Promise<void> = async () => {};
  export let onClose: () => void;

  let nameInput: HTMLInputElement | undefined;
  let saveButton: HTMLButtonElement | undefined;

  $: atLimit = recipes.length >= 100;
  $: rows = menuRows(recipes, {
    pins,
    recent,
    mediaKind: selectedMediaKind,
    workflows,
  });

  async function startEditor(): Promise<void> {
    await openRecipeEditor();
    await tick();
    nameInput?.focus();
  }
</script>

<div class="recipes-menu">
  {#if rows.length === 0 && recipes.length === 0}
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
                <span class="recipe-pin" aria-hidden="true"
                  ><svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.25"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    ><path
                      d="M12 17v5M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"
                    /></svg
                  ></span
                >
              {/if}
            </button>
          </div>
        {:else}
          {@const active = workflowActive(row.workflow as Workflow)}
          <div class="saved-recipe" data-recipe-id={row.id}>
            <button
              type="button"
              class:active
              aria-pressed={active}
              disabled={row.disabled}
              on:click={() => {
                applyWorkflow(row.workflow as Workflow);
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
                <b>{row.workflow.name}</b>
                <span>{workflowLine(row.workflow as Workflow)}</span>
              </span>
              {#if row.pinned}
                <span class="recipe-pin" aria-hidden="true"
                  ><svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.25"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    ><path
                      d="M12 17v5M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"
                    /></svg
                  ></span
                >
              {/if}
            </button>
          </div>
        {/if}
      {/each}
    </div>
  {/if}
  {#if rows.length > 0 || recipes.length === 0}<hr class="pop-sep" />{/if}
  <button
    class="link-button recipes-browse"
    type="button"
    on:click={() => {
      onClose();
      openRecipesLibrary();
    }}>Browse recipes…</button
  >
  <hr class="pop-sep" />
  {#if updatableRecipe && !recipeEditorOpen}
    <button
      class="link-button recipes-update"
      type="button"
      disabled={!canSave}
      on:click={() => {
        void updateRecipe();
        onClose();
      }}>Update “{updatableRecipe.name}”</button
    >
  {/if}
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
      disabled={!canSave || atLimit}
      title={atLimit ? '100 recipes is the limit. Delete one in Browse recipes… first.' : undefined}
      on:click={() => void startEditor()}>Save current as recipe…</button
    >
  {/if}
</div>
