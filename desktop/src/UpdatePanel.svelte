<script lang="ts">
  import { onMount } from 'svelte';
  import * as api from './lib/api';
  import { updateDialogOpen, updateError, updateState } from './lib/updates';
  export let processing = false;
  let channel = 'beta';
  let working = false;
  let recoveryConfirmed = false;
  $: status = $updateState;
  $: error = $updateError;
  $: inProgress = working || ['checking', 'downloading', 'installing'].includes(status.stage);
  $: sizeText = status.size ? `${(status.size / 1024 ** 2).toFixed(1)} MB` : '';
  onMount(() => {
    let unsubscribe: (() => void) | undefined;
    let disposed = false;
    void api
      .updateStatus()
      .then((value) => {
        if (!disposed) {
          updateState.set(value);
          channel = value.channel || channel;
        }
      })
      .catch(() => {});
    void api
      .listenForUpdates((value) => updateState.set(value))
      .then((stop) => {
        if (disposed) stop();
        else unsubscribe = stop;
      })
      .catch(() => {});
    return () => {
      disposed = true;
      unsubscribe?.();
    };
  });
  async function run(action: () => Promise<unknown>) {
    if (working) return;
    working = true;
    updateError.set('');
    try {
      await action();
      updateState.set(await api.updateStatus());
    } catch (e) {
      updateError.set(String(e));
      updateState.update((current) => ({
        ...current,
        stage:
          current.stage === 'installing'
            ? 'downloaded'
            : current.stage === 'checking'
              ? 'idle'
              : current.stage,
      }));
    } finally {
      working = false;
    }
  }
</script>

{#if $updateDialogOpen}
  <div class="update-backdrop">
    <div
      class="update-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="update-title"
      tabindex="-1"
    >
      <h2 id="update-title">Software Update</h2>
      {#if status.managed_by_store}
        <p>
          Settings, recipes and downloaded models are kept. LocalSR backs up your profile on the
          first launch of a new version.
        </p>
      {:else}
        <p>
          Settings, recipes and downloaded models are kept. LocalSR backs up your profile before
          installation.
        </p>
        <label for="update-channel">Release channel</label>
        <select
          id="update-channel"
          bind:value={channel}
          on:change={() => run(api.discardUpdate)}
          disabled={inProgress || status.stage === 'downloaded'}
          ><option value="stable">Stable</option><option value="beta">Beta</option></select
        >
      {/if}
      {#if status.target}<p class="detail">Installed build: {status.target}</p>{/if}
      <p class="detail">
        This software uses libraries from the FFmpeg project under the LGPLv2.1. LocalSR does not
        own FFmpeg. Corresponding source, build instructions and third-party notices are published
        with each download.
      </p>
      <p role="status">{status.message}</p>
      {#if status.version}<h3>{status.version} · {sizeText}</h3>
        <p class="notes">{status.notes}</p>{/if}
      {#if status.stage === 'downloading'}<progress max={status.size || 1} value={status.downloaded}
        ></progress>
        <p>{(status.downloaded / 1024 ** 2).toFixed(1)} MB downloaded</p>{/if}
      {#if processing}<p class="notice">
          {status.managed_by_store
            ? 'Finish or cancel your queue before opening Microsoft Store updates.'
            : 'Finish or cancel your queue before installation. You can check for and download an update meanwhile.'}
        </p>{/if}
      {#if status.settings_recovery}
        <div class="notice">
          <strong>Recover unreadable settings</strong>
          <p>
            Keep the original file as a recovery copy and start with defaults. Existing model files
            and media are kept. Your original recipes remain in the recovery file for manual
            recovery.
          </p>
          <label
            ><input type="checkbox" bind:checked={recoveryConfirmed} /> Preserve the original and use
            default settings</label
          ><button
            class="button"
            disabled={processing || working || !recoveryConfirmed}
            on:click={() => run(api.recoverUpdateSettings)}>Recover settings</button
          >
        </div>
      {/if}
      {#if error}<p role="alert" class="error">{error}</p>{/if}
      <div class="actions">
        {#if status.managed_by_store}<button
            class="button primary"
            disabled={processing || working}
            on:click={() => run(api.openStoreUpdates)}>Open Microsoft Store updates</button
          >
        {:else if status.stage === 'downloading'}<button
            class="button"
            on:click={() => api.cancelUpdate()}>Cancel download</button
          >
        {:else if status.stage === 'downloaded'}<button
            class="button"
            disabled={inProgress}
            on:click={() => run(api.discardUpdate)}>Discard download</button
          ><button
            class="button primary"
            disabled={processing || inProgress || status.settings_recovery}
            on:click={() => run(api.installUpdate)}>Install and restart</button
          >
        {:else if status.stage === 'available'}<button
            class="button primary"
            disabled={inProgress}
            on:click={() => run(api.downloadUpdate)}>Download update · {sizeText}</button
          >
        {:else}<button
            class="button primary"
            disabled={!status.configured || inProgress}
            on:click={() => run(() => api.checkUpdate(channel))}
            >{working ? 'Checking…' : 'Check for updates'}</button
          >{/if}
        <button
          class="button"
          disabled={status.stage === 'installing'}
          on:click={() => updateDialogOpen.set(false)}>Close</button
        >
      </div>
    </div>
  </div>
{/if}

<style>
  .update-backdrop {
    position: fixed;
    inset: 0;
    z-index: 110;
    background: #000a;
    display: grid;
    place-items: center;
    padding: 24px;
  }
  .update-dialog {
    width: min(590px, 100%);
    max-height: 85vh;
    overflow: auto;
    background: #202226;
    padding: 26px;
    border: 1px solid #505665;
    border-radius: 14px;
    box-shadow: 0 18px 70px #0008;
  }
  h2 {
    margin-top: 0;
  }
  p {
    line-height: 1.5;
  }
  .detail {
    font-size: 12px;
    color: #aeb7c9;
  }
  .notes {
    white-space: pre-wrap;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    justify-content: flex-end;
    margin-top: 24px;
  }
  .notice {
    background: #283346;
    border: 1px solid #4f6894;
    padding: 12px;
    border-radius: 8px;
  }
  .error {
    color: #f0b2aa;
  }
  progress {
    width: 100%;
  }
  select {
    margin: 10px 0;
  }
  .notice label {
    display: flex;
    gap: 10px;
    line-height: 1.4;
    margin: 12px 0;
  }
</style>
