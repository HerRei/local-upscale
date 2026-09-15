<script lang="ts">
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

  let dismissed = dismissedVersion();
  let requested = false;

  $: status = $updateState;
  $: error = $updateError;
  $: percent = status.size ? Math.min(100, Math.round((status.downloaded / status.size) * 100)) : 0;
  $: visible =
    !status.managed_by_store &&
    ((status.stage === 'available' && status.version !== dismissed) ||
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

  function start(): void {
    // While processing, download now and let the reactive block install later.
    requested = processing;
    void updateNow(processing);
  }
</script>

{#if visible}
  <div class="update-notice" role="status" aria-live="polite">
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
          >{processing
            ? 'It installs as soon as the current queue finishes.'
            : 'LocalSR restarts to finish the update.'}</span
        >
      {/if}
      {#if error}<span class="error" role="alert">{error}</span>{/if}
    </div>
    <div class="buttons">
      {#if status.stage === 'available'}
        <button class="notice-button" type="button" on:click={later}>Later</button>
        <button class="notice-button primary" type="button" on:click={start}>Update Now</button>
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
    position: fixed;
    z-index: 80;
    top: 58px;
    right: 14px;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 12px;
    width: min(440px, calc(100vw - 28px));
    padding: 12px 12px 12px 14px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 14px;
    background: rgba(32, 35, 41, 0.86);
    backdrop-filter: blur(22px) saturate(150%);
    box-shadow:
      inset 0 1px rgba(255, 255, 255, 0.06),
      0 18px 46px rgba(0, 0, 0, 0.5);
    animation: arrive 0.32s cubic-bezier(0.2, 0.9, 0.3, 1.1);
  }
  .glyph {
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border-radius: 9px;
    background: linear-gradient(#5b95ff, #3f7cf2);
    box-shadow: inset 0 1px rgba(255, 255, 255, 0.25);
  }
  .glyph svg {
    width: 18px;
    height: 18px;
    fill: none;
    stroke: #fff;
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
    font-size: calc(12.5px * var(--ui-scale));
    font-weight: 600;
  }
  .text span {
    color: var(--secondary);
    font-size: calc(11.5px * var(--ui-scale));
  }
  .text .error {
    color: #f0b2aa;
  }
  .progress {
    display: block;
    height: 4px;
    margin-top: 4px;
    overflow: hidden;
    border-radius: 2px;
    background: rgba(255, 255, 255, 0.1);
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
  }
  .percent {
    color: var(--secondary);
    font-size: calc(11.5px * var(--ui-scale));
    font-variant-numeric: tabular-nums;
  }
  .notice-button {
    padding: 6px 11px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 7px;
    background: rgba(255, 255, 255, 0.08);
    color: var(--text);
    font-size: calc(12px * var(--ui-scale));
    font-weight: 500;
    white-space: nowrap;
  }
  .notice-button:hover:not(:disabled) {
    background: rgba(255, 255, 255, 0.13);
  }
  .notice-button.primary {
    border-color: rgba(0, 0, 0, 0.25);
    background: linear-gradient(#5b95ff, #3f7cf2);
    box-shadow: inset 0 1px rgba(255, 255, 255, 0.22);
    color: #fff;
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
