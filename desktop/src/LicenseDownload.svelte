<script lang="ts">
  import type { CatalogModel } from './lib/types';
  export let model: CatalogModel;
  export let disabled = false;
  export let downloading = false;
  export let download: () => Promise<void>;
  export let openLicense: () => Promise<void>;
  export let chooseAlternative: () => void;
  let step = 0;
  let acknowledged = false;
  let submitting = false;
  function close() { step = 0; acknowledged = false; }
  async function confirm() {
    if (!acknowledged || submitting) return;
    if (step === 1) { step = 2; acknowledged = false; return; }
    submitting = true;
    try { await download(); close(); } finally { submitting = false; }
  }
</script>

<div class="license-notice" role="note" aria-label="Model license reminder">
  <strong>License needs clarification · personal research only</strong>
  <p>The publisher lists “CC-BY-0.4”. This is not a recognized license identifier or an explicit non-commercial license. LocalSR cannot confirm permission for commercial use.</p>
  <button type="button" on:click={openLicense}>Read publisher’s terms ↗</button>
  <p>For commercial projects, stock HAT-S uses Apache-2.0; follow its license and attribution terms.</p>
  <button type="button" {disabled} on:click={chooseAlternative}>Use HAT-S instead</button>
</div>
{#if !model.installed}
  <button class="button full" {disabled} on:click={() => { if (downloading) void download(); else { step = 1; acknowledged = false; } }}>{downloading ? 'Cancel download' : `Review terms & download · ${(model.size_bytes / 1_000_000).toFixed(1)} MB`}</button>
{/if}
{#if step}
  <div class="license-backdrop">
    <div class="license-dialog" role="dialog" aria-modal="true" aria-labelledby="license-heading" tabindex="-1">
      <h2 id="license-heading">{step === 1 ? '1 of 2 · Read the model terms' : '2 of 2 · Confirm this download'}</h2>
      <p><strong>{model.name}</strong></p>
      {#if step === 1}
        <p>The upstream license is listed as “CC-BY-0.4” and remains unclear. This download does not establish permission to use the checkpoint commercially.</p>
        <button type="button" on:click={openLicense}>Read publisher’s terms ↗</button>
        <label><input type="checkbox" bind:checked={acknowledged} /> I have read the terms and understand that commercial permission is unverified.</label>
      {:else}
        <p>Use stock HAT-S (Apache-2.0) for a model with documented commercial permissions. This checkpoint remains available for personal research with the license reminder shown whenever selected.</p>
        <label><input type="checkbox" bind:checked={acknowledged} /> I will use this checkpoint only for personal, non-commercial research until its rights are clarified.</label>
      {/if}
      <div class="actions"><button class="button" disabled={submitting} on:click={close}>Cancel</button><button class="button primary" disabled={!acknowledged || submitting} on:click={confirm}>{submitting ? 'Downloading…' : step === 1 ? 'Continue' : 'Confirm & download'}</button></div>
    </div>
  </div>
{/if}

<style>
  .license-notice { margin: 12px 0; padding: 12px; border: 1px solid #8e7950; border-radius: 8px; background: #302c23; color: #eedbae; font-size: 12px; }
  p { line-height: 1.5; }
  .license-notice button { color: #b6cbff; background: transparent; border: 0; padding: 5px 0; cursor: pointer; }
  .license-backdrop { position: fixed; inset: 0; z-index: 120; display: grid; place-items: center; background: #000a; padding: 24px; }
  .license-dialog { width: min(540px, 100%); padding: 24px; background: #202226; border: 1px solid #666; border-radius: 14px; box-shadow: 0 20px 70px #0008; }
  h2 { margin-top: 0; font-size: 21px; }
  label { display: flex; gap: 10px; align-items: flex-start; margin: 24px 0; line-height: 1.5; }
  input { margin-top: 4px; }
  .actions { display: flex; gap: 12px; justify-content: flex-end; }
</style>
