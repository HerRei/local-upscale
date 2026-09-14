<script lang="ts">
  import type { CatalogModel } from './lib/types';
  export let model: CatalogModel;
  export let disabled = false;
  export let downloading = false;
  export let progress = 0;
  export let download: () => Promise<void>;
  export let openLicense: () => Promise<void>;
  export let openSource: () => Promise<void>;
</script>

<div class="license-notice" role="note" aria-label="Model license">
  <strong>{model.license_name} · {model.author}</strong>
  <p>
    The author permits sharing and commercial use under this license, with attribution. LocalSR
    downloads the author's checkpoint unchanged.
  </p>
  <div class="license-links">
    <button type="button" on:click={openSource}>Model source ↗</button>
    <button type="button" on:click={openLicense}>License terms ↗</button>
  </div>
</div>
{#if !model.installed}
  <button class="button full primary" {disabled} on:click={() => void download()}>
    {downloading
      ? `Downloading ${Math.round(progress)}% · Cancel`
      : `Download ${(model.size_bytes / 1_000_000).toFixed(1)} MB`}
  </button>
  {#if downloading}
    <progress aria-label="Model download progress" max="100" value={progress}></progress>
  {/if}
{/if}

<style>
  .license-notice {
    margin: 12px 0;
    padding: 12px;
    border: 1px solid #465a81;
    border-radius: 8px;
    background: #222b3b;
    color: #c4d4f2;
    font-size: 12px;
  }
  p {
    line-height: 1.5;
    margin: 8px 0;
  }
  .license-links {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
  }
  .license-links button {
    color: #b6cbff;
    background: transparent;
    border: 0;
    padding: 5px 0;
    cursor: pointer;
  }
  .license-links button:focus-visible {
    outline: 2px solid #b6cbff;
    outline-offset: 2px;
  }
  progress {
    width: 100%;
    height: 5px;
    margin-top: 8px;
    accent-color: #6b91f5;
  }
</style>
