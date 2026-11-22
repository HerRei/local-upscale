<script lang="ts">
  export let busy = false;
  export let onAdd: () => void;
  export let onDismiss: () => void;
</script>

<div
  class="gpu-download-notice linux-menu-notice"
  role="status"
  aria-live="polite"
  data-tauri-drag-region
>
  <div class="glyph" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      <rect x="4" y="4" width="6" height="6" rx="1.5" />
      <rect x="14" y="4" width="6" height="6" rx="1.5" />
      <rect x="4" y="14" width="6" height="6" rx="1.5" />
      <rect x="14" y="14" width="6" height="6" rx="1.5" />
    </svg>
  </div>
  <div class="text">
    <span>Add LocalSR to your applications menu?</span>
  </div>
  <div class="buttons">
    <button class="notice-button primary" type="button" disabled={busy} on:click={onAdd}
      >Add to menu</button
    >
    <button class="notice-button" type="button" disabled={busy} on:click={onDismiss}>Not now</button
    >
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
  .text span {
    color: var(--text);
    font-size: calc(12px * var(--ui-scale));
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
