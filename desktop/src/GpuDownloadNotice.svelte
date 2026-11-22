<script lang="ts">
  export let message: string;
  export let onOpenDownloadPage: () => void;
  export let onDismiss: () => void;
</script>

<div class="gpu-download-notice" role="status" aria-live="polite" data-tauri-drag-region>
  <div class="glyph" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      <path d="M12 4v11m-4.5-4.5L12 15l4.5-4.5" />
      <path d="M5 19h14" />
    </svg>
  </div>
  <div class="text">
    <span>{message}</span>
  </div>
  <div class="buttons">
    <button class="notice-button primary" type="button" on:click={onOpenDownloadPage}
      >Download page</button
    >
    <button
      class="notice-button"
      type="button"
      aria-label="Dismiss GPU download notice"
      on:click={onDismiss}>×</button
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
