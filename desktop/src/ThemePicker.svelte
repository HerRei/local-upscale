<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { UiSettings } from './lib/types';

  type Appearance = UiSettings['appearance'];

  export let appearance: Appearance = 'dark';
  export let colourBlind = false;
  export let labelledBy = '';

  const dispatch = createEventDispatcher<{ choose: Appearance }>();

  const APPEARANCES: { id: Appearance; label: string }[] = [
    { id: 'dark', label: 'Dark' },
    { id: 'light', label: 'Light' },
    { id: 'system', label: 'Match system' },
  ];

  let tileButtons: HTMLButtonElement[] = [];

  function choose(next: Appearance): void {
    appearance = next;
    dispatch('choose', next);
  }

  function onThemeKeydown(event: KeyboardEvent): void {
    const step =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0;
    if (!step) return;
    event.preventDefault();
    const current = APPEARANCES.findIndex((item) => item.id === appearance);
    const nextIndex =
      ((current < 0 ? 0 : current) + step + APPEARANCES.length) % APPEARANCES.length;
    choose(APPEARANCES[nextIndex].id);
    tileButtons[nextIndex]?.focus();
  }
</script>

<div class="setup-theme-grid" role="radiogroup" aria-labelledby={labelledBy || undefined}>
  {#each APPEARANCES as option, index}
    <button
      type="button"
      class="setup-theme-tile"
      class:active={appearance === option.id}
      role="radio"
      aria-checked={appearance === option.id}
      bind:this={tileButtons[index]}
      on:click={() => choose(option.id)}
      on:keydown={onThemeKeydown}
    >
      {#if option.id === 'system'}
        <div class="setup-mock setup-mock-system" aria-hidden="true">
          <div
            class="setup-mock-half left"
            data-theme="dark"
            data-palette={colourBlind ? 'colour-blind' : 'default'}
          >
            <div class="setup-mock-bar"><span></span><span></span><span></span></div>
            <div class="setup-mock-body">
              <div class="setup-mock-sidebar"><i></i><i class="dim"></i></div>
              <div class="setup-mock-stage"><div class="setup-mock-frame"></div></div>
            </div>
          </div>
          <div
            class="setup-mock-half right"
            data-theme="light"
            data-palette={colourBlind ? 'colour-blind' : 'default'}
          >
            <div class="setup-mock-bar"></div>
            <div class="setup-mock-body">
              <div class="setup-mock-stage"><div class="setup-mock-frame"></div></div>
              <div class="setup-mock-inspector">
                <i></i><i class="short"></i><span class="setup-mock-cta"></span>
              </div>
            </div>
          </div>
        </div>
      {:else}
        <div
          class="setup-mock"
          aria-hidden="true"
          data-theme={option.id}
          data-palette={colourBlind ? 'colour-blind' : 'default'}
        >
          <div class="setup-mock-bar"><span></span><span></span><span></span></div>
          <div class="setup-mock-body">
            <div class="setup-mock-sidebar"><i></i><i class="dim"></i></div>
            <div class="setup-mock-stage">
              <div class="setup-mock-frame"><span class="setup-mock-split"></span></div>
            </div>
            <div class="setup-mock-inspector">
              <i></i><i class="short"></i><span class="setup-mock-cta"></span>
            </div>
          </div>
        </div>
      {/if}
      <span class="setup-theme-label">
        <span class="setup-theme-dot" aria-hidden="true"></span>
        <span>{option.label}</span>
      </span>
    </button>
  {/each}
</div>
