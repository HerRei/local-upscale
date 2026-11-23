<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { modal } from './lib/modal';
  import type { UiSettings } from './lib/types';
  import ThemePicker from './ThemePicker.svelte';

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

  function chooseAppearance(next: Appearance): void {
    appearance = next;
    dispatch('preview', { appearance, colourBlind });
  }

  function toggleColourBlind(event: Event): void {
    colourBlind = (event.currentTarget as HTMLInputElement).checked;
    dispatch('preview', { appearance, colourBlind });
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
      Choose how LocalSR looks and what it checks or records. You can change all of this later in
      Settings.
    </p>

    <section class="setup-section" aria-labelledby="setup-appearance-heading">
      <h3 id="setup-appearance-heading">Appearance</h3>
      <ThemePicker
        {appearance}
        {colourBlind}
        labelledBy="setup-appearance-heading"
        on:choose={(event) => chooseAppearance(event.detail)}
      />

      <div class="setting-row setup-appearance-toggle">
        <label class="setting-label" for="setup-colour-blind">Colour-blind friendly colours</label>
        <input
          id="setup-colour-blind"
          class="switch"
          type="checkbox"
          role="switch"
          checked={colourBlind}
          on:change={toggleColourBlind}
        />
      </div>
    </section>

    <section class="setup-section" aria-labelledby="setup-menu-bar-heading">
      <h3 id="setup-menu-bar-heading">Menu bar</h3>
      <div class="setting-row">
        <div class="setting-copy">
          <label class="setting-label" for="setup-menu-bar">Show LocalSR in the {trayName}</label>
          <p class="setting-desc" id="setup-menu-bar-desc">
            Shows what is running and lets you pause the queue.
          </p>
        </div>
        <input
          id="setup-menu-bar"
          class="switch"
          type="checkbox"
          role="switch"
          aria-describedby="setup-menu-bar-desc"
          bind:checked={menuBar}
        />
      </div>
    </section>

    <section class="setup-section" aria-labelledby="setup-updates-heading">
      <h3 id="setup-updates-heading">Updates</h3>
      <div class="setting-row">
        <div class="setting-copy">
          <label class="setting-label" for="setup-check-updates"
            >Check for updates automatically</label
          >
          <p class="setting-desc" id="setup-check-updates-desc">
            The update check reads the feed on GitHub Pages.
          </p>
        </div>
        <input
          id="setup-check-updates"
          class="switch"
          type="checkbox"
          role="switch"
          aria-describedby="setup-check-updates-desc"
          bind:checked={checkAtLaunch}
        />
      </div>

      <div class="setting-row">
        <div class="setting-copy">
          <label class="setting-label" for="setup-anonymous-count"
            >Send an anonymous count with each update check</label
          >
          <p class="setting-desc" id="setup-anonymous-count-desc">
            When a check happens, this sends the app version, platform and channel to the
            developer's server, with no ID.
          </p>
        </div>
        <input
          id="setup-anonymous-count"
          class="switch"
          type="checkbox"
          role="switch"
          aria-describedby="setup-anonymous-count-desc"
          bind:checked={anonymousCount}
        />
      </div>
    </section>

    <section class="setup-section" aria-labelledby="setup-crashes-heading">
      <h3 id="setup-crashes-heading">Crash reports</h3>
      <div class="setting-row">
        <div class="setting-copy">
          <label class="setting-label" for="setup-crash-reports">Offer to report crashes</label>
          <p class="setting-desc" id="setup-crash-reports-desc">
            If LocalSR crashes, it asks you next time whether to send a short report. You see
            everything before it is sent, and nothing is sent automatically.
          </p>
        </div>
        <input
          id="setup-crash-reports"
          class="switch"
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
  .setup-appearance-toggle {
    align-items: center;
    margin-top: 10px;
  }
  .setup-error {
    margin: 10px 0 0;
    color: var(--danger-text);
  }
</style>
