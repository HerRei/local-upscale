<script lang="ts">
  import type { CatalogModel } from './lib/types';
  export let model: CatalogModel;
  export let openLicense: () => Promise<void>;
  export let openSource: () => Promise<void>;
</script>

<!-- Attribution for a permissive license; the Start button does the download. -->
<p class="model-credit" role="note" aria-label="Model license">
  {model.license_name} · {model.author} · used unchanged ·
  <button type="button" aria-label="Model source" on:click={openSource}>Source ↗</button>
  <button type="button" aria-label="License terms" on:click={openLicense}>Terms ↗</button>
</p>

<style>
  .model-credit {
    margin: 4px 0 0;
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
    line-height: 1.5;
  }
  button {
    margin-left: 6px;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--secondary);
    font-size: inherit;
    text-decoration: underline;
    text-decoration-color: var(--border);
    text-underline-offset: 2px;
  }
  button:hover {
    color: var(--text);
    text-decoration-color: currentColor;
  }
</style>
