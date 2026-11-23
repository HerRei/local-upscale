<script lang="ts">
  import { formatBytes } from './lib/format';
  import type { EngineOffer } from './lib/engines';
  import type { EngineDownloadStatus } from './lib/types';

  export let offer: EngineOffer;
  export let download: EngineDownloadStatus;
  export let processing = false;
  export let error = '';
  export let onDownload: () => void;
  export let onCancel: () => void;
  export let onDismiss: () => void;

  $: activeDownload =
    download.backend === offer.backend &&
    (download.stage === 'downloading' ||
      download.stage === 'installing' ||
      download.stage === 'handshaking');
  $: percent = download.total
    ? Math.min(100, Math.round((download.downloaded / download.total) * 100))
    : 0;
  $: displayError =
    error ||
    (download.stage === 'error' && download.backend === offer.backend ? download.error || '' : '');
</script>

<div
  class="gpu-download-notice engine-recommendation-card"
  role="status"
  aria-live="polite"
  data-tauri-drag-region
>
  <div class="glyph" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      <path d="M12 4v11m-4.5-4.5L12 15l4.5-4.5" />
      <path d="M5 19h14" />
    </svg>
  </div>
  <div class="text">
    {#if activeDownload}
      {#if download.stage === 'downloading'}
        <span>
          Downloading {offer.shortName} engine… {formatBytes(download.downloaded)} of {formatBytes(
            download.total,
          )} ({percent}%)
        </span>
        <progress class="download-progress" max={download.total || 1} value={download.downloaded}
        ></progress>
      {:else if download.stage === 'installing'}
        <span>Unpacking and verifying {offer.shortName} engine…</span>
      {:else}
        <span>Checking {offer.shortName} engine…</span>
      {/if}
    {:else}
      <span>{offer.message}</span>
      {#if displayError}
        <span class="engine-card-error" role="alert">{displayError}</span>
      {/if}
    {/if}
  </div>
  <div class="buttons">
    {#if activeDownload}
      {#if download.stage === 'downloading'}
        <button class="notice-button" type="button" on:click={onCancel}>Cancel</button>
      {/if}
    {:else}
      <button
        class="notice-button primary"
        type="button"
        disabled={processing}
        on:click={onDownload}>Download</button
      >
      <button class="notice-button" type="button" on:click={onDismiss}>Not now</button>
    {/if}
  </div>
</div>

<style>
  .gpu-download-notice {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  @media (min-width: 1280px) {
    :global(.app-shell.mac) .gpu-download-notice {
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
    gap: 4px;
    min-width: 0;
  }
  .text span {
    color: var(--text);
    font-size: calc(12px * var(--ui-scale));
  }
  .text .engine-card-error {
    color: var(--danger-text);
  }
  .buttons {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .notice-button {
    padding: 6px 11px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--raised);
    color: var(--text);
    font-size: calc(12px * var(--ui-scale));
  }
  .notice-button.primary {
    border-color: var(--accent);
    background: var(--accent);
    color: var(--on-accent);
    font-weight: 600;
  }
  .notice-button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  @media (max-width: 1279px) {
    .gpu-download-notice {
      grid-template-columns: auto minmax(0, 1fr);
    }
    .buttons {
      grid-column: 1 / -1;
      justify-content: flex-end;
    }
  }
</style>
