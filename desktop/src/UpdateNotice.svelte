<script lang="ts">
  import * as api from './lib/api';
  import {
    dismissVersion,
    dismissedVersion,
    updateDialogOpen,
    updateError,
    updateNow,
    updateState,
  } from './lib/updates';

  /** Installing replaces the running engine, so it waits for the queue. */
  export let processing = false;
  export let visible = false;
  let dismissed = dismissedVersion();
  let requested = false;

  $: status = $updateState;
  $: error = $updateError;
  $: percent = status.size ? Math.min(100, Math.round((status.downloaded / status.size) * 100)) : 0;
  $: visible =
    status.configured &&
    !$updateDialogOpen &&
    !status.managed_by_store &&
    ((status.stage === 'available' &&
      status.version !== dismissed &&
      status.version !== status.skipped_version) ||
      status.stage === 'downloading' ||
      status.stage === 'downloaded' ||
      status.stage === 'installing');
  // Update Now continues on its own once processing finishes.
  $: if (requested && status.stage === 'downloaded' && !processing && !status.settings_recovery) {
    requested = false;
    void updateNow(false);
  }

  function later(): void {
    dismissVersion(status.version);
    dismissed = status.version;
  }

  async function skip(): Promise<void> {
    try {
      updateState.set(await api.skipUpdate());
    } catch (error) {
      updateError.set(String(error));
    }
  }

  function start(): void {
    // While processing, download now and let the reactive block install later.
    requested = processing;
    void updateNow(processing);
  }
</script>

{#if visible}
  <div class="update-notice" role="status" aria-live="polite" data-tauri-drag-region>
    <div class="glyph" aria-hidden="true">
      <svg viewBox="0 0 24 24"
        ><path d="M12 4v11m-4.5-4.5L12 15l4.5-4.5" /><path d="M5 19h14" /></svg
      >
    </div>
    <div class="text">
      {#if status.stage === 'available'}
        <b>LocalSR {status.version} is available</b>
        <span
          >{status.size ? `${(status.size / 1024 ** 2).toFixed(0)} MB · ` : ''}Settings, recipes and
          models are kept.</span
        >
      {:else if status.stage === 'downloading'}
        <b>Downloading LocalSR {status.version}…</b>
        <span class="progress" style={`--done: ${percent}%`}><i></i></span>
      {:else if status.stage === 'installing'}
        <b>Installing LocalSR {status.version}…</b>
        <span>LocalSR restarts when it is done.</span>
      {:else}
        <b>LocalSR {status.version} is ready to install</b>
        <span
          >{status.settings_recovery
            ? 'Settings could not be read. Open Details to recover them before updating.'
            : processing
              ? 'It installs as soon as the current queue finishes.'
              : 'LocalSR restarts to finish the update.'}</span
        >
      {/if}
      {#if error}<span class="error" role="alert">{error}</span>{/if}
    </div>
    <div class="buttons">
      {#if status.stage === 'available'}
        <button class="notice-button primary" type="button" on:click={start}>Update</button>
        <button class="notice-button" type="button" on:click={skip}>Skip This Version</button>
        <button
          class="notice-button"
          type="button"
          on:click={() => api.openUpdateDiscover().catch((error) => updateError.set(String(error)))}
          >What's New</button
        >
        <button
          class="notice-button"
          type="button"
          aria-label="Close update notice"
          on:click={later}>×</button
        >
      {:else if status.stage === 'downloaded'}
        <button class="notice-button" type="button" on:click={() => updateDialogOpen.set(true)}
          >Details</button
        >
        <button
          class="notice-button primary"
          type="button"
          disabled={processing || status.settings_recovery}
          on:click={start}>Restart to Update</button
        >
      {:else if status.stage === 'downloading'}
        <span class="percent">{percent}%</span>
      {/if}
    </div>
  </div>
{/if}

<style>
  .update-notice {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  /* In wide macOS windows there is no compact toolbar above it, so the traffic lights sit in top-left. */
  @media (min-width: 1280px) {
    :global(.app-shell.mac) .update-notice {
      padding-left: 84px;
    }
  }
  .glyph {
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border-radius: var(--radius);
    background: var(--accent);
  }
  .glyph svg {
    width: 18px;
    height: 18px;
    fill: none;
    stroke: var(--on-accent);
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .text {
    display: grid;
    gap: 3px;
    min-width: 0;
  }
  .text b {
    font-size: calc(13px * var(--ui-scale));
    font-weight: 600;
  }
  .text span {
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
  }
  .text .error {
    color: var(--danger-text-muted);
  }
  .progress {
    display: block;
    height: 4px;
    margin-top: 4px;
    overflow: hidden;
    border-radius: 2px;
    background: var(--raised);
  }
  .progress i {
    display: block;
    width: var(--done);
    height: 100%;
    border-radius: 2px;
    background: var(--accent);
    transition: width 0.3s;
  }
  .buttons {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .percent {
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
    font-variant-numeric: tabular-nums;
  }
  .notice-button {
    padding: 6px 11px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--raised);
    color: var(--text);
    font-size: calc(12px * var(--ui-scale));
    font-weight: 500;
    white-space: nowrap;
  }
  .notice-button:hover:not(:disabled) {
    background: var(--panel);
  }
  .notice-button.primary {
    border-color: var(--border);
    background: var(--accent);
    color: var(--on-accent);
  }
  .notice-button:disabled {
    cursor: default;
    opacity: 0.5;
  }
  @keyframes arrive {
    from {
      opacity: 0;
      transform: translateY(-8px) scale(0.98);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .update-notice {
      animation: none;
    }
  }
</style>
