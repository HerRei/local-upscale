<script lang="ts">
  import HelpTip from './HelpTip.svelte';
  import {
    engineListRows,
    isGpuBackend,
    plainEngineError,
    type EngineListRow,
  } from './lib/engines';
  import { formatBytes } from './lib/format';
  import { modal } from './lib/modal';
  import type { EngineManagerSnapshot } from './lib/types';

  export let engines: EngineManagerSnapshot;
  export let processing = false;
  export let actionBusy = false;
  export let refreshingCatalog = false;
  export let errorMessage = '';
  export let errorBackend = '';
  export let errorAction: 'download' | 'use' | 'remove' | 'verify' | '' = '';
  export let verifiedBackend = '';
  export let onClose: () => void;
  export let onRefreshCatalog: () => void;
  export let onDownload: (backend: string) => void;
  export let onCancelDownload: () => void;
  export let onUse: (backend: string) => void;
  export let onVerify: (id: string) => void = () => {};
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
  $: failingBackend = errorMessage
    ? errorBackend
    : download.stage === 'error' && download.error
      ? download.backend
      : '';
  $: failingAction = errorMessage ? errorAction || 'download' : 'download';
  $: rowMatchedError = Boolean(
    activeError && failingBackend && rows.some((row) => row.backend === failingBackend),
  );
  $: dialogError = activeError && !rowMatchedError ? activeError : '';
  $: percent = download.total
    ? Math.min(100, Math.round((download.downloaded / download.total) * 100))
    : 0;

  function rowMeta(row: EngineListRow, verified: string): string {
    return [
      row.sizeBytes > 0 ? formatBytes(row.sizeBytes) : '',
      row.installedEntry?.needs_reinstall
        ? 'Needs reinstall'
        : row.bundled
          ? 'Built in'
          : row.installed
            ? row.active
              ? ''
              : 'Installed'
            : 'Not installed',
      verified === row.backend && !row.installedEntry?.needs_reinstall ? 'Verified' : '',
    ]
      .filter(Boolean)
      .join(' · ');
  }

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
    <h2 id="engine-dialog-title">Engine</h2>
    <p class="engine-intro">
      {#if engines.recommendation.adapter_name && isGpuBackend(engines.recommendation.backend)}
        Detected graphics card: {engines.recommendation.adapter_name}.
      {:else}
        Choose the engine LocalSR uses on this computer.
      {/if}
      <HelpTip
        label="Engine"
        text="NVIDIA CUDA is fastest on NVIDIA cards. DirectML works with AMD, Intel and NVIDIA cards on Windows. CPU works everywhere and is slowest."
      />
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
        {@const rowError = !rowDownloading && failingBackend === row.backend ? activeError : ''}
        {@const meta = rowMeta(row, rowError ? '' : verifiedBackend)}
        <li class="engine-item" class:active={row.active}>
          <div class="engine-item-main">
            <div class="engine-item-head">
              <strong>{row.label}</strong>
              {#if row.recommended}
                <span class="engine-badge recommended">Recommended</span>
              {/if}
              {#if row.active}
                <span class="engine-badge active">Active</span>
              {/if}
            </div>
            {#if !rowDownloading && meta}
              <div class="engine-item-meta">
                <span>{meta}</span>
              </div>
            {/if}
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
            {:else if rowError}
              <p class="download-error" role="alert">{rowError}</p>
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
                class={row.recommended ? 'button primary compact' : 'button compact'}
                disabled={busy || processing || (!engines.configured && !row.catalogEntry)}
                on:click={() => onDownload(row.backend)}
                >{rowError ? 'Try again' : 'Download'}</button
              >
            {:else}
              {#if !row.active}
                <button
                  type="button"
                  class="button compact"
                  disabled={busy || processing}
                  on:click={() =>
                    row.installedEntry?.needs_reinstall
                      ? onDownload(row.backend)
                      : onUse(row.backend)}
                  >{rowError && failingAction === 'use' ? 'Try again' : 'Use'}</button
                >
              {/if}
              {#if row.installedEntry && !row.bundled && !row.installedEntry.needs_reinstall}
                {@const verifyId = row.installedEntry.id}
                <button
                  type="button"
                  class="button compact ghost"
                  aria-label={`Verify ${row.label}`}
                  disabled={busy || processing}
                  on:click={() => onVerify(verifyId)}
                  >{rowError && failingAction === 'verify' ? 'Try again' : 'Verify'}</button
                >
              {/if}
              {#if row.removable && row.installedEntry}
                {@const removeId = row.installedEntry.id}
                <button
                  type="button"
                  class="button compact ghost danger"
                  disabled={busy || processing}
                  on:click={() => onRemove(removeId)}
                  >{rowError && failingAction === 'remove' ? 'Try again' : 'Remove'}</button
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

    {#if dialogError}
      <p class="engine-error" role="alert">{dialogError}</p>
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
