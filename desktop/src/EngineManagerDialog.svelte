<script lang="ts">
  import HelpTip from './HelpTip.svelte';
  import { engineListRows, isGpuBackend, plainEngineError } from './lib/engines';
  import { formatBytes } from './lib/format';
  import { modal } from './lib/modal';
  import type { EngineManagerSnapshot } from './lib/types';

  export let engines: EngineManagerSnapshot;
  export let processing = false;
  export let actionBusy = false;
  export let refreshingCatalog = false;
  export let errorMessage = '';
  export let onClose: () => void;
  export let onRefreshCatalog: () => void;
  export let onDownload: (backend: string) => void;
  export let onCancelDownload: () => void;
  export let onUse: (backend: string) => void;
  export let onRemove: (id: string) => void;
  export let onImport: () => void;

  $: rows = engineListRows(engines);
  $: download = engines.download;
  $: downloadInProgress =
    download.stage === 'downloading' ||
    download.stage === 'installing' ||
    download.stage === 'handshaking';
  $: busy = actionBusy || downloadInProgress;
  $: lockClose = actionBusy || download.stage === 'installing' || download.stage === 'handshaking';
  $: activeError =
    errorMessage ||
    (download.stage === 'error' && download.error ? plainEngineError(download.error) : '');
  $: percent = download.total
    ? Math.min(100, Math.round((download.downloaded / download.total) * 100))
    : 0;

  function handleClose(): void {
    if (!lockClose) onClose();
  }

  function handleBackdrop(event: MouseEvent): void {
    if (event.target === event.currentTarget) handleClose();
  }
</script>

<div class="modal-backdrop" role="presentation" on:click={handleBackdrop}>
  <div
    class="modal engine-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="engine-dialog-title"
    tabindex="-1"
    use:modal={handleClose}
  >
    <h2 id="engine-dialog-title">Inference engine</h2>
    <p class="engine-intro">
      {#if engines.recommendation.adapter_name && isGpuBackend(engines.recommendation.backend)}
        Detected graphics card: {engines.recommendation.adapter_name}.
      {:else}
        Choose which inference engine LocalSR uses on this computer.
      {/if}
    </p>

    {#if processing}
      <p class="inline-warning" role="status">
        Finish or cancel active jobs before changing the inference engine.
      </p>
    {/if}

    {#if refreshingCatalog}
      <p class="engine-status-line" role="status">Checking available engines…</p>
    {:else if engines.catalog_error}
      <p class="engine-error" role="alert">{plainEngineError(engines.catalog_error)}</p>
    {/if}

    <ul class="engine-list" aria-label="Available engines">
      {#each rows as row (row.backend)}
        {@const rowDownloading = downloadInProgress && download.backend === row.backend}
        <li class="engine-item" class:active={row.active}>
          <div class="engine-item-main">
            <div class="engine-item-head">
              <strong>{row.label}</strong>
              {#if row.recommended}
                <span class="engine-badge recommended">Recommended</span>
              {/if}
              {#if row.active}
                <span class="engine-badge active">Active</span>
              {:else if row.installed}
                <span class="engine-badge">Installed</span>
              {/if}
              {#if row.bundled}
                <span class="engine-badge">Bundled</span>
              {/if}
            </div>
            <div class="engine-item-meta">
              {#if row.sizeBytes > 0}
                <span>{formatBytes(row.sizeBytes)}</span>
              {/if}
              {#if !row.installed && !rowDownloading}
                <span>Not installed</span>
              {/if}
            </div>
            {#if rowDownloading}
              <progress
                class="download-progress"
                max={download.total || 1}
                value={download.downloaded}
              ></progress>
              <p class="engine-progress-label" role="status">
                {#if download.stage === 'downloading'}
                  Downloading… {formatBytes(download.downloaded)} of {formatBytes(download.total)} ({percent}%)
                {:else if download.stage === 'installing'}
                  Unpacking and verifying engine…
                {:else}
                  Checking engine startup…
                {/if}
              </p>
            {/if}
          </div>
          <div class="engine-item-actions">
            {#if rowDownloading}
              {#if download.stage === 'downloading'}
                <button type="button" class="button compact" on:click={onCancelDownload}
                  >Cancel</button
                >
              {/if}
            {:else if !row.installed}
              <button
                type="button"
                class="button primary compact"
                disabled={busy || processing || (!engines.configured && !row.catalogEntry)}
                on:click={() => onDownload(row.backend)}>Download</button
              >
            {:else}
              {#if !row.active}
                <button
                  type="button"
                  class="button primary compact"
                  disabled={busy || processing}
                  on:click={() => onUse(row.backend)}>Use</button
                >
              {/if}
              {#if row.removable && row.installedEntry}
                {@const removeId = row.installedEntry.id}
                <button
                  type="button"
                  class="button danger compact"
                  disabled={busy || processing}
                  on:click={() => onRemove(removeId)}>Remove</button
                >
              {/if}
            {/if}
          </div>
        </li>
      {/each}
    </ul>

    {#if !engines.catalog.length && !refreshingCatalog && engines.configured}
      <div class="engine-refresh-row">
        <button type="button" class="link-button" disabled={busy} on:click={onRefreshCatalog}
          >Refresh available engines</button
        >
      </div>
    {/if}

    {#if activeError}
      <p class="engine-error" role="alert">{activeError}</p>
    {/if}

    <div class="engine-dialog-footer">
      <div class="engine-import-group">
        <button type="button" class="button" disabled={busy || processing} on:click={onImport}
          >Import engine from file…</button
        >
        <HelpTip
          label="Import engine"
          text="Choose a downloaded .engine-payload.json file. Keep its .part files and .sig signature files in the same folder."
        />
      </div>
      <button type="button" class="button" disabled={lockClose} on:click={handleClose}>Close</button
      >
    </div>
  </div>
</div>
