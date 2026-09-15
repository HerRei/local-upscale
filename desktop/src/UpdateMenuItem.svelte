<script lang="ts">
  import { updateDialogOpen, updateState } from './lib/updates';

  export let onOpen: () => void = () => {};

  $: status = $updateState;
  $: percent = status.size ? Math.round((status.downloaded / status.size) * 100) : 0;
  $: badge = status.settings_recovery
    ? { text: 'Needs attention', tone: 'warn' }
    : status.stage === 'available'
      ? { text: status.version || 'New', tone: 'accent' }
      : status.stage === 'downloading'
        ? { text: `${percent}%`, tone: 'accent' }
        : status.stage === 'downloaded'
          ? { text: 'Ready', tone: 'accent' }
          : null;

  function open(): void {
    onOpen();
    updateDialogOpen.set(true);
  }
</script>

<button class="app-menu-item update-item" type="button" role="menuitem" on:click={open}>
  <span>{status.stage === 'available' ? 'Update LocalSR…' : 'Check for Updates…'}</span>
  {#if badge}<span class="badge {badge.tone}">{badge.text}</span>{/if}
</button>

<style>
  .update-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .badge {
    flex: none;
    padding: 2px 7px;
    border-radius: 999px;
    font-size: calc(10.5px * var(--ui-scale));
    font-weight: 600;
    line-height: 1.5;
  }
  .badge.accent {
    background: rgba(78, 140, 255, 0.18);
    color: #b9d0ff;
  }
  .badge.warn {
    background: rgba(222, 153, 53, 0.18);
    color: #f1c27d;
  }
</style>
