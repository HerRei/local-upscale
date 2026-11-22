<script lang="ts">
  import HelpTip from './HelpTip.svelte';
  import { chooseEditModelFile, saveCustomEditModel } from './lib/api';
  import { CUSTOM_EDIT_FAMILIES, customEditFamilySpec, editFamilyLabel } from './lib/editing';
  import { formatBytes } from './lib/format';
  import { modal } from './lib/modal';
  import type { CustomEditModel } from './lib/types';

  export let initial: CustomEditModel | undefined = undefined;
  export let editRuntime = '';
  export let chooseRuntime: () => Promise<void>;
  export let saved: () => Promise<void>;
  export let close: () => void;

  let family = initial?.family || CUSTOM_EDIT_FAMILIES[0].id;
  let diffusionPath = initial?.diffusion_path || '';
  let textEncoderPath = initial?.text_encoder_path || '';
  let visionPath = initial?.vision_path || '';
  let vaePath = initial?.vae_path || '';
  let sizes: Record<string, number> = {};
  let detectedNote = '';
  let errorMessage = '';
  let checking = false;

  $: spec = customEditFamilySpec(family);
  $: allPicked = Boolean(
    diffusionPath && textEncoderPath && vaePath && (!spec.needsVision || visionPath),
  );

  function fileName(path: string): string {
    return path.split(/[/\\]/).pop() || path;
  }

  async function pickFile(role: 'diffusion' | 'text_encoder' | 'vision' | 'vae'): Promise<void> {
    errorMessage = '';
    try {
      const pick = await chooseEditModelFile(role);
      if (!pick) return;
      if (role === 'diffusion') {
        diffusionPath = pick.path;
        if (pick.detected_family) {
          family = pick.detected_family;
          detectedNote = `Detected ${editFamilyLabel(pick.detected_family)} from GGUF metadata.`;
        } else {
          detectedNote = '';
        }
      } else if (role === 'text_encoder') {
        textEncoderPath = pick.path;
      } else if (role === 'vision') {
        visionPath = pick.path;
      } else {
        vaePath = pick.path;
      }
      if (pick.size_bytes > 0) {
        sizes = { ...sizes, [role]: pick.size_bytes };
      }
    } catch (error) {
      errorMessage = String(error);
    }
  }

  async function checkAndSave(): Promise<void> {
    if (!allPicked || !editRuntime || checking) return;
    checking = true;
    errorMessage = '';
    try {
      const custom: CustomEditModel = {
        family: spec.id,
        diffusion_path: diffusionPath,
        text_encoder_path: textEncoderPath,
        vision_path: spec.needsVision ? visionPath : '',
        vae_path: vaePath,
      };
      await saveCustomEditModel(custom, editRuntime);
      await saved();
    } catch (error) {
      errorMessage = String(error);
    } finally {
      checking = false;
    }
  }
</script>

<div class="modal-backdrop" role="presentation">
  <div
    class="modal custom-edit-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="custom-edit-title"
    tabindex="-1"
    use:modal={() => {
      if (!checking) close();
    }}
  >
    <h2 id="custom-edit-title">Use your own edit model</h2>
    <p>
      You are responsible for the model's licence. LocalSR does not download or share it, and your
      files stay where they are.
    </p>

    <div class="custom-edit-form">
      <div class="field-row">
        <span class="label-with-tip"
          ><label for="custom-edit-family">Model family</label><HelpTip
            label="Model family"
            text="Chooses the sd-cli options, default step count and guidance scale for your files."
          /></span
        >
        <select
          id="custom-edit-family"
          bind:value={family}
          disabled={checking}
          on:change={() => {
            errorMessage = '';
            detectedNote = '';
          }}
        >
          {#each CUSTOM_EDIT_FAMILIES as item (item.id)}
            <option value={item.id}>{item.label}</option>
          {/each}
        </select>
      </div>
      {#if detectedNote}<p class="custom-edit-meta">{detectedNote}</p>{/if}
      <p class="custom-edit-meta">
        Defaults: {spec.defaultSteps} steps, CFG {spec.cfgScale}
      </p>

      <div class="custom-edit-files">
        <div class="custom-edit-file">
          <div class="custom-edit-file-info">
            <span class="custom-edit-role">Diffusion model</span>
            <span class="custom-edit-path" title={diffusionPath || undefined}>
              {diffusionPath ? fileName(diffusionPath) : 'No file chosen'}{#if sizes.diffusion}
                {' · '}{formatBytes(sizes.diffusion)}
              {/if}
            </span>
          </div>
          <button
            class="button"
            type="button"
            disabled={checking}
            on:click={() => void pickFile('diffusion')}
            >{diffusionPath ? 'Change…' : 'Choose…'}</button
          >
        </div>

        <div class="custom-edit-file">
          <div class="custom-edit-file-info">
            <span class="custom-edit-role">Text encoder</span>
            <span class="custom-edit-path" title={textEncoderPath || undefined}>
              {textEncoderPath
                ? fileName(textEncoderPath)
                : 'No file chosen'}{#if sizes.text_encoder}
                {' · '}{formatBytes(sizes.text_encoder)}
              {/if}
            </span>
          </div>
          <button
            class="button"
            type="button"
            disabled={checking}
            on:click={() => void pickFile('text_encoder')}
            >{textEncoderPath ? 'Change…' : 'Choose…'}</button
          >
        </div>

        {#if spec.needsVision}
          <div class="custom-edit-file">
            <div class="custom-edit-file-info">
              <span class="custom-edit-role">Vision projector</span>
              <span class="custom-edit-path" title={visionPath || undefined}>
                {visionPath ? fileName(visionPath) : 'No file chosen'}{#if sizes.vision}
                  {' · '}{formatBytes(sizes.vision)}
                {/if}
              </span>
            </div>
            <button
              class="button"
              type="button"
              disabled={checking}
              on:click={() => void pickFile('vision')}>{visionPath ? 'Change…' : 'Choose…'}</button
            >
          </div>
        {/if}

        <div class="custom-edit-file">
          <div class="custom-edit-file-info">
            <span class="custom-edit-role">VAE</span>
            <span class="custom-edit-path" title={vaePath || undefined}>
              {vaePath ? fileName(vaePath) : 'No file chosen'}{#if sizes.vae}
                {' · '}{formatBytes(sizes.vae)}
              {/if}
            </span>
          </div>
          <button
            class="button"
            type="button"
            disabled={checking}
            on:click={() => void pickFile('vae')}>{vaePath ? 'Change…' : 'Choose…'}</button
          >
        </div>
      </div>
    </div>

    {#if !editRuntime}
      <p class="inline-warning" role="status">
        Select the sd-cli editing runtime before checking the model.
        <button class="link-button" type="button" on:click={() => void chooseRuntime()}
          >Choose runtime…</button
        >
      </p>
    {/if}

    {#if errorMessage}
      <p class="download-error" role="alert">{errorMessage}</p>
    {/if}

    <div class="modal-actions">
      <button class="button" type="button" disabled={checking} on:click={close}>Cancel</button>
      <button
        class="button primary"
        type="button"
        disabled={checking || !allPicked || !editRuntime}
        on:click={() => void checkAndSave()}
        >{checking ? 'Checking model…' : 'Check and save'}</button
      >
    </div>
  </div>
</div>
