<script lang="ts">
  import { tick } from 'svelte';
  import { formatBytes } from './lib/format';
  import { tabFromKey } from './lib/keyboard';
  import { modal } from './lib/modal';
  import {
    MAX_RECIPE_PINS,
    recipeDetails,
    recipeMissingBytes,
    recipePlanLine,
    searchRecipes,
    sortRecipes,
    type WorkflowEntry,
  } from './lib/recipeList';
  import type { AppSnapshot, MediaItem, Recipe, UiSettings } from './lib/types';

  export let snapshot: AppSnapshot;
  export let settings: UiSettings;
  export let activeRecipeIds: Set<string>;
  export let selectedMediaKind: MediaItem['kind'] | undefined = undefined;
  export let workflows: WorkflowEntry[] = [];
  export let initialGroup: 'my' | 'recommended' | 'photo' | 'video' | undefined = undefined;
  export let busy = false;
  export let close: () => void;
  export let applyRecipe: (recipe: Recipe) => void;
  export let togglePinRecipe: (id: string) => void;
  export let duplicateRecipe: (recipe: Recipe) => Promise<string | undefined>;
  export let renameRecipe: (recipe: Recipe, name: string) => Promise<boolean>;
  export let deleteRecipe: (id: string) => Promise<boolean>;

  type GroupId = 'my' | 'recommended' | 'photo' | 'video';

  let group: GroupId =
    initialGroup ?? (workflows.length > 0 && snapshot.recipes.length === 0 ? 'recommended' : 'my');
  let searchQuery = '';
  let selectedId = '';
  let editContextId = '';
  let renaming = false;
  let renameValue = '';
  let confirmingDelete = false;
  let mutating = false;

  let rowsEl: HTMLElement | undefined;
  let renameInput: HTMLInputElement | undefined;
  let renameBtn: HTMLButtonElement | undefined;
  let deleteBtn: HTMLButtonElement | undefined;
  let cancelDeleteBtn: HTMLButtonElement | undefined;

  $: recipes = snapshot.recipes;
  $: pins = settings.recipe_pins ?? [];
  $: recent = settings.recipe_recent ?? [];
  $: pinSet = new Set(pins);
  $: photoRecipes = recipes.filter((recipe) => recipe.task !== 'video');
  $: videoRecipes = recipes.filter((recipe) => recipe.task === 'video');
  $: groups = [
    { id: 'my' as GroupId, label: 'My recipes', count: recipes.length },
    { id: 'recommended' as GroupId, label: 'Recommended', count: workflows.length },
    {
      id: 'photo' as GroupId,
      label: 'Photo',
      count:
        photoRecipes.length + workflows.filter((workflow) => workflow.media === 'image').length,
    },
    {
      id: 'video' as GroupId,
      label: 'Video',
      count:
        videoRecipes.length + workflows.filter((workflow) => workflow.media === 'video').length,
    },
  ];
  $: groupRecipes =
    group === 'my'
      ? recipes
      : group === 'photo'
        ? photoRecipes
        : group === 'video'
          ? videoRecipes
          : [];
  $: orderedRecipes = sortRecipes(groupRecipes, pins, recent);
  $: visibleRecipes = searchRecipes(orderedRecipes, searchQuery, snapshot.catalog);
  $: selectedRecipe =
    visibleRecipes.find((recipe) => recipe.id === selectedId) ?? visibleRecipes[0];
  $: if ((selectedRecipe?.id ?? '') !== editContextId) {
    editContextId = selectedRecipe?.id ?? '';
    renaming = false;
    confirmingDelete = false;
  }
  $: selectedDetails = selectedRecipe
    ? recipeDetails(selectedRecipe, snapshot, settings)
    : undefined;
  $: selectedFits = selectedRecipe ? fitsMedia(selectedRecipe) : false;
  $: selectedPinned = selectedRecipe ? pinSet.has(selectedRecipe.id) : false;
  $: atRecipeLimit = recipes.length >= 100;

  function fitsMedia(recipe: Recipe): boolean {
    if (!selectedMediaKind) return true;
    return (recipe.task === 'video') === (selectedMediaKind === 'video');
  }

  function rowStatus(recipe: Recipe): string {
    if (!fitsMedia(recipe)) return recipe.task === 'video' ? 'Video' : 'Photo';
    if (activeRecipeIds.has(recipe.id)) return 'Active';
    const missing = recipeMissingBytes(recipe, snapshot, settings);
    return missing > 0 ? formatBytes(missing) : '';
  }

  function emptyMessage(currentGroup: GroupId, query: string): string {
    const trimmed = query.trim();
    if (trimmed) return `No recipes match “${trimmed}”.`;
    if (currentGroup === 'recommended') return 'No recommended workflows yet.';
    if (currentGroup === 'photo') return 'No photo recipes yet.';
    if (currentGroup === 'video') return 'No video recipes yet.';
    return 'No recipes yet. Set up the panel, then choose Recipes › Save current as recipe…';
  }
  $: emptyText = emptyMessage(group, searchQuery);

  function pickGroup(next: GroupId): void {
    group = next;
    selectedId = '';
    renaming = false;
    confirmingDelete = false;
  }

  function selectRecipe(id: string): void {
    selectedId = id;
  }

  function applyAndClose(recipe: Recipe): void {
    if (busy || !fitsMedia(recipe)) return;
    applyRecipe(recipe);
    close();
  }

  function focusRow(index: number): void {
    const target = rowsEl?.querySelectorAll<HTMLButtonElement>('button.row')[index];
    if (!target) return;
    target.focus();
    target.scrollIntoView?.({ block: 'nearest' });
  }

  function onSearchKeydown(event: KeyboardEvent): void {
    if (event.key === 'ArrowDown' && visibleRecipes.length > 0) {
      event.preventDefault();
      selectedId = visibleRecipes[0].id;
      focusRow(0);
      return;
    }
    if (event.key === 'Enter' && selectedRecipe && fitsMedia(selectedRecipe)) {
      event.preventDefault();
      applyAndClose(selectedRecipe);
    }
  }

  function onRowKeydown(event: KeyboardEvent, index: number, recipe: Recipe): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (fitsMedia(recipe)) applyAndClose(recipe);
      return;
    }
    const next = tabFromKey(event.key, index, visibleRecipes.length, 'vertical');
    if (next !== null) {
      event.preventDefault();
      selectedId = visibleRecipes[next].id;
      focusRow(next);
    }
  }

  async function startRename(): Promise<void> {
    if (!selectedRecipe || busy || mutating) return;
    renaming = true;
    confirmingDelete = false;
    renameValue = selectedRecipe.name;
    await tick();
    renameInput?.focus();
    renameInput?.select();
  }

  function cancelRename(): void {
    renaming = false;
    void tick().then(() => renameBtn?.focus());
  }

  async function commitRename(): Promise<void> {
    if (!selectedRecipe || !renaming || mutating) return;
    const trimmed = renameValue.trim().slice(0, 60);
    if (!trimmed) return;
    if (trimmed === selectedRecipe.name) {
      cancelRename();
      return;
    }
    mutating = true;
    try {
      const ok = await renameRecipe(selectedRecipe, trimmed);
      if (ok) {
        renaming = false;
        await tick();
        renameBtn?.focus();
      }
    } finally {
      mutating = false;
    }
  }

  async function handleDuplicate(recipe: Recipe): Promise<void> {
    if (busy || mutating || atRecipeLimit) return;
    mutating = true;
    try {
      const createdId = await duplicateRecipe(recipe);
      if (createdId) selectedId = createdId;
    } finally {
      mutating = false;
    }
  }

  async function askDelete(): Promise<void> {
    if (!selectedRecipe || busy || mutating) return;
    confirmingDelete = true;
    renaming = false;
    await tick();
    cancelDeleteBtn?.focus();
  }

  function cancelDelete(): void {
    confirmingDelete = false;
    void tick().then(() => deleteBtn?.focus());
  }

  async function confirmDelete(): Promise<void> {
    if (!selectedRecipe || mutating) return;
    const targetId = selectedRecipe.id;
    const currentIndex = visibleRecipes.findIndex((recipe) => recipe.id === targetId);
    const fallbackId =
      visibleRecipes[currentIndex + 1]?.id ?? visibleRecipes[currentIndex - 1]?.id ?? '';
    mutating = true;
    try {
      const ok = await deleteRecipe(targetId);
      confirmingDelete = false;
      if (ok && selectedId === targetId) selectedId = fallbackId;
    } finally {
      mutating = false;
    }
  }

  function handleEscape(): void {
    if (renaming) {
      cancelRename();
      return;
    }
    if (confirmingDelete) {
      cancelDelete();
      return;
    }
    close();
  }
</script>

<div class="modal-backdrop library-backdrop" role="presentation">
  <div
    class="sheet"
    role="dialog"
    aria-modal="true"
    aria-labelledby="recipes-library-title"
    tabindex="-1"
    use:modal={handleEscape}
  >
    <div class="sheet-head">
      <h2 id="recipes-library-title">Recipes</h2>
      <div class="sheet-right">
        <button class="close" type="button" aria-label="Close recipes library" on:click={close}
          ><svg
            xmlns="http://www.w3.org/2000/svg"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12" /></svg
          ></button
        >
      </div>
    </div>

    <div class="sheet-body">
      <nav class="rail" aria-label="Recipe groups">
        {#each groups as item (item.id)}
          {#if item.id === 'photo'}
            <hr class="rail-sep" />
          {/if}
          <button
            class="rail-item"
            class:active={group === item.id}
            type="button"
            aria-pressed={group === item.id}
            on:click={() => pickGroup(item.id)}
            ><span>{item.label}</span><small>{item.count}</small></button
          >
        {/each}
      </nav>

      <section class="list recipes-list" aria-label="Recipes">
        <div class="recipes-search">
          <input
            type="search"
            aria-label="Search recipes"
            placeholder="Search recipes"
            data-autofocus
            bind:value={searchQuery}
            on:keydown={onSearchKeydown}
          />
        </div>
        <div class="recipes-rows" bind:this={rowsEl}>
          {#if visibleRecipes.length === 0}
            <p class="library-empty">{emptyText}</p>
          {:else}
            {#each visibleRecipes as recipe, index (recipe.id)}
              {@const fits = fitsMedia(recipe)}
              {@const pinned = pinSet.has(recipe.id)}
              {@const status = rowStatus(recipe)}
              <button
                class="row"
                type="button"
                class:selected={selectedRecipe?.id === recipe.id}
                class:dimmed={!fits}
                aria-pressed={selectedRecipe?.id === recipe.id}
                on:click={() => selectRecipe(recipe.id)}
                on:dblclick={() => {
                  if (fits) applyAndClose(recipe);
                }}
                on:keydown={(event) => onRowKeydown(event, index, recipe)}
              >
                <div>
                  <div class="name">
                    <span>{recipe.name}</span>{#if pinned}<span
                        class="recipe-pin"
                        aria-hidden="true">⌃</span
                      >{/if}
                  </div>
                  <div class="role">{recipePlanLine(recipe, snapshot.catalog)}</div>
                </div>
                <div class="right">{status}</div>
              </button>
            {/each}
          {/if}
        </div>
      </section>

      <aside class="detail" aria-label="Recipe details">
        {#if selectedRecipe && selectedDetails}
          <div>
            {#if renaming}
              <input
                bind:this={renameInput}
                class="recipe-rename-input"
                type="text"
                aria-label="Recipe name"
                maxlength={60}
                bind:value={renameValue}
                on:keydown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    void commitRename();
                  } else if (event.key === 'Escape') {
                    event.preventDefault();
                    event.stopPropagation();
                    cancelRename();
                  }
                }}
              />
            {:else}
              <h3 class="recipe-detail-title">
                <span>{selectedRecipe.name}</span>{#if selectedPinned}<span
                    class="recipe-pin"
                    aria-hidden="true">⌃</span
                  >{/if}
              </h3>
            {/if}
            <div class="role">
              {selectedRecipe.task === 'video' ? 'Video recipe' : 'Photo recipe'}
            </div>
          </div>

          {#if selectedDetails.steps.length > 0}
            <div class="recipe-steps" role="list" aria-label="Steps">
              {#each selectedDetails.steps as step, index}
                <div class="recipe-step" role="listitem">
                  <span class="recipe-step-num">{index + 1}</span>
                  <span class="recipe-step-name">{step.name}</span>
                  <span class="recipe-step-model">
                    {step.modelName}
                    {#if step.needsFile}
                      <span class="recipe-step-note warn">Needs your file</span>
                    {:else if step.downloadBytes > 0}
                      <span class="recipe-step-note">{formatBytes(step.downloadBytes)}</span>
                    {/if}
                  </span>
                </div>
              {/each}
            </div>
          {/if}

          <dl class="kv recipe-kv">
            <div>
              <dt>Output</dt>
              <dd>{selectedDetails.output}</dd>
            </div>
            <div>
              <dt>Quality</dt>
              <dd>{selectedDetails.quality}</dd>
            </div>
            <div>
              <dt>This run</dt>
              <dd>{selectedDetails.run}</dd>
            </div>
          </dl>

          <div class="recipe-download">
            {selectedDetails.downloadBytes > 0
              ? `${formatBytes(selectedDetails.downloadBytes)} to download`
              : 'Nothing to download'}
          </div>

          <div class="actions">
            {#if confirmingDelete}
              <p class="recipe-question">
                <b>Delete {selectedRecipe.name}?</b>
                <span>This can’t be undone.</span>
              </p>
              <div class="pair">
                <button
                  class="button danger"
                  type="button"
                  disabled={busy || mutating}
                  on:click={() => void confirmDelete()}>Delete</button
                >
                <button
                  bind:this={cancelDeleteBtn}
                  class="button"
                  type="button"
                  disabled={busy || mutating}
                  on:click={cancelDelete}>Cancel</button
                >
              </div>
            {:else}
              <div class="pair">
                <button
                  class="button primary"
                  type="button"
                  disabled={busy || !selectedFits}
                  title={!selectedFits
                    ? selectedRecipe.task === 'video'
                      ? 'Select a video first.'
                      : 'Select a photo first.'
                    : undefined}
                  on:click={() => applyAndClose(selectedRecipe)}>Apply</button
                >
                <button
                  class="button"
                  type="button"
                  disabled={busy || (!selectedPinned && pins.length >= MAX_RECIPE_PINS)}
                  title={!selectedPinned && pins.length >= MAX_RECIPE_PINS
                    ? `${MAX_RECIPE_PINS} pins is the limit. Unpin one first.`
                    : undefined}
                  on:click={() => togglePinRecipe(selectedRecipe.id)}
                  >{selectedPinned ? 'Unpin' : 'Pin'}</button
                >
              </div>
              <div class="recipe-secondary-actions">
                <button
                  class="link-button"
                  type="button"
                  disabled={busy || mutating || atRecipeLimit}
                  title={atRecipeLimit ? '100 recipes is the limit. Delete one first.' : undefined}
                  on:click={() => void handleDuplicate(selectedRecipe)}>Duplicate</button
                >
                <span aria-hidden="true">·</span>
                <button
                  bind:this={renameBtn}
                  class="link-button"
                  type="button"
                  disabled={busy || mutating}
                  on:click={() => void startRename()}>Rename</button
                >
                <span aria-hidden="true">·</span>
                <button
                  bind:this={deleteBtn}
                  class="link-button danger"
                  type="button"
                  disabled={busy || mutating}
                  on:click={() => void askDelete()}>Delete</button
                >
              </div>
            {/if}
          </div>
        {:else if recipes.length > 0}
          <p class="model-description">Select a recipe to see its steps and settings.</p>
        {/if}
      </aside>
    </div>
  </div>
</div>
