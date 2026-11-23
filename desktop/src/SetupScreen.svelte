<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { modal } from './lib/modal';
  import type { UiSettings } from './lib/types';

  type Appearance = UiSettings['appearance'];

  export let appearance: Appearance = 'dark';
  export let colourBlind = false;
  export let checkAtLaunch = true;
  export let anonymousCount = false;
  export let crashReports = false;
  export let menuBar = true;
  export let saving = false;
  export let error = '';

  const dispatch = createEventDispatcher<{
    preview: { appearance: Appearance; colourBlind: boolean };
    continue: {
      appearance: Appearance;
      colourBlind: boolean;
      checkAtLaunch: boolean;
      anonymousCount: boolean;
      crashReports: boolean;
      menuBar: boolean;
    };
  }>();

  const APPEARANCES: { id: Appearance; label: string }[] = [
    { id: 'dark', label: 'Dark' },
    { id: 'light', label: 'Light' },
    { id: 'system', label: 'Match System' },
  ];

  let tileButtons: HTMLButtonElement[] = [];

  function chooseAppearance(next: Appearance): void {
    appearance = next;
    dispatch('preview', { appearance, colourBlind });
  }

  function toggleColourBlind(event: Event): void {
    colourBlind = (event.currentTarget as HTMLInputElement).checked;
    dispatch('preview', { appearance, colourBlind });
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
    chooseAppearance(APPEARANCES[nextIndex].id);
    tileButtons[nextIndex]?.focus();
  }

  function submit(): void {
    dispatch('continue', {
      appearance,
      colourBlind,
      checkAtLaunch,
      anonymousCount,
      crashReports,
      menuBar,
    });
  }

  const trayName =
    typeof navigator !== 'undefined' && /Mac/.test(navigator.platform) ? 'menu bar' : 'system tray';
</script>

<div class="modal-backdrop" role="presentation">
  <div
    class="modal setup-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="setup-title"
    tabindex="-1"
    use:modal
  >
    <h2 id="setup-title">Welcome to LocalSR</h2>
    <p class="setup-intro">
      Choose how LocalSR looks and what it checks or records. You can change these later in the
      menu.
    </p>

    <section class="setup-section" aria-labelledby="setup-appearance-heading">
      <h3 id="setup-appearance-heading">Appearance</h3>
      <div class="setup-theme-grid" role="radiogroup" aria-labelledby="setup-appearance-heading">
        {#each APPEARANCES as option, index}
          <button
            type="button"
            class="setup-theme-tile"
            class:active={appearance === option.id}
            role="radio"
            aria-checked={appearance === option.id}
            bind:this={tileButtons[index]}
            on:click={() => chooseAppearance(option.id)}
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

      <div class="setup-toggle-row setup-appearance-toggle">
        <label class="setup-toggle-label" for="setup-colour-blind"
          >Colour-blind friendly colours</label
        >
        <input
          id="setup-colour-blind"
          class="setup-switch"
          type="checkbox"
          role="switch"
          checked={colourBlind}
          on:change={toggleColourBlind}
        />
      </div>
    </section>

    <section class="setup-section" aria-labelledby="setup-menu-bar-heading">
      <h3 id="setup-menu-bar-heading">Menu bar</h3>
      <div class="setup-toggle-row">
        <div class="setup-toggle-copy">
          <label class="setup-toggle-label" for="setup-menu-bar"
            >Show LocalSR in the {trayName}</label
          >
          <p class="setup-toggle-desc" id="setup-menu-bar-desc">
            Shows what is running and lets you pause the queue. You can change this later in
            Advanced settings.
          </p>
        </div>
        <input
          id="setup-menu-bar"
          class="setup-switch"
          type="checkbox"
          role="switch"
          aria-describedby="setup-menu-bar-desc"
          bind:checked={menuBar}
        />
      </div>
    </section>

    <section class="setup-section" aria-labelledby="setup-updates-heading">
      <h3 id="setup-updates-heading">Updates</h3>
      <div class="setup-toggle-row">
        <div class="setup-toggle-copy">
          <label class="setup-toggle-label" for="setup-check-updates"
            >Check for updates automatically</label
          >
          <p class="setup-toggle-desc" id="setup-check-updates-desc">
            The update check reads the feed on GitHub Pages.
          </p>
        </div>
        <input
          id="setup-check-updates"
          class="setup-switch"
          type="checkbox"
          role="switch"
          aria-describedby="setup-check-updates-desc"
          bind:checked={checkAtLaunch}
        />
      </div>

      <div class="setup-toggle-row">
        <div class="setup-toggle-copy">
          <label class="setup-toggle-label" for="setup-anonymous-count"
            >Send an anonymous count with each update check</label
          >
          <p class="setup-toggle-desc" id="setup-anonymous-count-desc">
            When a check happens, this sends the app version, platform and channel to the
            developer's server, with no ID.
          </p>
        </div>
        <input
          id="setup-anonymous-count"
          class="setup-switch"
          type="checkbox"
          role="switch"
          aria-describedby="setup-anonymous-count-desc"
          bind:checked={anonymousCount}
        />
      </div>
    </section>

    <section class="setup-section" aria-labelledby="setup-crashes-heading">
      <h3 id="setup-crashes-heading">Crash reports</h3>
      <div class="setup-toggle-row">
        <div class="setup-toggle-copy">
          <label class="setup-toggle-label" for="setup-crash-reports">Offer to report crashes</label
          >
          <p class="setup-toggle-desc" id="setup-crash-reports-desc">
            If LocalSR crashes, it asks you next time whether to send a short report. You see
            everything before it is sent, and nothing is sent automatically.
          </p>
        </div>
        <input
          id="setup-crash-reports"
          class="setup-switch"
          type="checkbox"
          role="switch"
          aria-describedby="setup-crash-reports-desc"
          bind:checked={crashReports}
        />
      </div>
    </section>

    {#if error}
      <p class="setup-error" role="alert">{error}</p>
    {/if}

    <div class="modal-actions">
      <button type="button" class="button primary" disabled={saving} on:click={submit}>
        {saving ? 'Saving…' : 'Continue'}
      </button>
    </div>
  </div>
</div>

<style>
  .setup-modal {
    width: min(600px, calc(100vw - 40px));
    padding: 20px 22px;
  }
  .setup-intro {
    margin: 0 0 14px;
  }
  .setup-section {
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }
  .setup-section + .setup-section {
    margin-top: 12px;
  }
  .setup-section h3 {
    margin: 0 0 10px;
    font-size: calc(12px * var(--ui-scale));
    font-weight: 600;
    color: var(--text);
  }
  .setup-theme-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
  }
  .setup-theme-tile {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px;
    border: 1px solid var(--border);
    border-radius: var(--radius-surface);
    background: var(--recess);
    color: var(--secondary);
    text-align: left;
  }
  .setup-theme-tile:hover {
    background: var(--hover);
  }
  .setup-theme-tile.active {
    border-color: var(--accent-line);
    background: var(--accent-fill);
    color: var(--text);
  }
  .setup-mock {
    width: 100%;
    height: 68px;
    display: grid;
    grid-template-rows: 10px 1fr;
    overflow: hidden;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--base);
  }
  .setup-mock-bar {
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 0 5px;
    background: var(--chrome);
    border-bottom: 1px solid var(--border);
  }
  .setup-mock-bar span {
    width: 4px;
    height: 4px;
    border-radius: 50%;
    background: var(--tertiary);
    opacity: 0.7;
  }
  .setup-mock-body {
    display: grid;
    grid-template-columns: 24% 1fr 28%;
    min-height: 0;
  }
  .setup-mock-sidebar {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 6px 5px;
    background: var(--panel);
    border-right: 1px solid var(--border);
  }
  .setup-mock-sidebar i,
  .setup-mock-inspector i {
    display: block;
    height: 4px;
    border-radius: 1px;
    background: var(--hover);
  }
  .setup-mock-sidebar i.dim {
    width: 72%;
    background: var(--chrome);
  }
  .setup-mock-stage {
    display: grid;
    place-items: center;
    padding: 6px;
    background: var(--stage);
  }
  .setup-mock-frame {
    position: relative;
    width: 100%;
    height: 100%;
    border: 1px solid var(--border);
    border-radius: 1px;
    background: var(--preview-canvas-background);
  }
  .setup-mock-split {
    position: absolute;
    inset: 0 auto 0 50%;
    width: 1px;
    background: var(--border);
  }
  .setup-mock-inspector {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 6px 5px;
    background: var(--panel);
    border-left: 1px solid var(--border);
  }
  .setup-mock-inspector i.short {
    width: 65%;
  }
  .setup-mock-cta {
    margin-top: auto;
    height: 6px;
    border-radius: 1px;
    background: var(--accent);
  }
  .setup-mock-system {
    display: grid;
    grid-template-rows: 1fr;
    grid-template-columns: 1fr 1fr;
  }
  .setup-mock-half {
    display: grid;
    grid-template-rows: 10px 1fr;
    min-width: 0;
    background: var(--base);
  }
  .setup-mock-half.left {
    border-right: 1px solid var(--border);
  }
  .setup-mock-half.left .setup-mock-body {
    grid-template-columns: 44% 1fr;
  }
  .setup-mock-half.left .setup-mock-stage {
    padding-right: 0;
  }
  .setup-mock-half.left .setup-mock-frame {
    border-right: 0;
  }
  .setup-mock-half.right .setup-mock-body {
    grid-template-columns: 1fr 52%;
  }
  .setup-mock-half.right .setup-mock-stage {
    padding-left: 0;
  }
  .setup-mock-half.right .setup-mock-frame {
    border-left: 0;
  }
  .setup-theme-label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: calc(12px * var(--ui-scale));
    font-weight: 500;
  }
  .setup-theme-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    border: 1px solid var(--accent-line);
    flex-shrink: 0;
  }
  .setup-theme-tile.active .setup-theme-dot {
    background: var(--text);
    border-color: var(--text);
  }
  .setup-toggle-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
  }
  .setup-toggle-row + .setup-toggle-row {
    margin-top: 10px;
  }
  .setup-appearance-toggle {
    align-items: center;
    margin-top: 10px;
  }
  .setup-toggle-copy {
    min-width: 0;
  }
  .setup-toggle-label {
    display: block;
    font-size: calc(12.5px * var(--ui-scale));
    font-weight: 500;
    color: var(--text);
    cursor: pointer;
  }
  .setup-toggle-desc {
    margin: 2px 0 0;
    font-size: calc(11.5px * var(--ui-scale));
    line-height: 1.4;
    color: var(--secondary);
  }
  .setup-switch {
    position: relative;
    flex-shrink: 0;
    width: 34px;
    height: 18px;
    margin: 1px 0 0;
    border: 1px solid var(--accent-line);
    border-radius: 999px;
    background: var(--recess);
    appearance: none;
    -webkit-appearance: none;
    cursor: pointer;
  }
  .setup-switch::before {
    content: '';
    position: absolute;
    top: 2px;
    left: 2px;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--text);
    transition: transform 0.14s ease;
  }
  .setup-switch:checked {
    background: var(--text);
    border-color: var(--text);
  }
  .setup-switch:checked::before {
    background: var(--base);
    transform: translateX(16px);
  }
  .setup-error {
    margin: 10px 0 0;
    color: var(--danger-text);
  }
  @media (prefers-reduced-motion: reduce) {
    .setup-switch::before {
      transition: none;
    }
  }
</style>
