<script lang="ts">
  import HelpTip from './HelpTip.svelte';
  import RunSettings from './RunSettings.svelte';
  import SaveToField from './SaveToField.svelte';
  import {
    editDevice,
    editExpert,
    editExpertChanges,
    editRecommendedSize,
    editSizeFor,
    editVramOptions,
  } from './lib/editing';
  import type { CapabilityInfo, EditModel, EditPlacement, UiSettings } from './lib/types';

  export let settings: UiSettings;
  export let editModel: EditModel | undefined = undefined;
  export let capabilities: CapabilityInfo;
  export let updateSettings: (patch: Partial<UiSettings>) => void;
  export let chooseOutput: () => Promise<void>;
  /** The saved defaults from Settings; hardware and low priority here apply to this session. */
  export let defaults: UiSettings | undefined = undefined;
  export let onUseDefaults: () => void = () => undefined;
  /** Resolves true when the host took the request, false when it refused (and said so). */
  export let refreshCapabilities: () => Promise<boolean>;
  export let features: string[] = [];

  let advanced = false;
  let expertOpen = false;

  $: editingDevice = editDevice(capabilities, settings);
  $: expert = editExpert(settings, editModel, editingDevice, features);
  $: expertChanges = editExpertChanges(expert);
  $: editRecommended = editRecommendedSize(editModel, capabilities, editingDevice, expert);
  $: editSizeShown = editSizeFor(settings.edit_max_dimension, editRecommended, expert);
  $: hasPlacement = Boolean(editingDevice) && features.includes('edit_placement');
  $: hasExpert = Boolean(editingDevice) && features.includes('edit_expert');
  $: isMac = editingDevice?.id === 'mps';
  $: mageFlowOnMac = isMac && editModel?.family === 'mage-flow-edit-turbo';
  $: vramOptions = editVramOptions(editingDevice);
</script>

<section class="control-section">
  <button class="disclosure" on:click={() => (advanced = !advanced)}
    ><span>Advanced</span><b aria-hidden="true"
      ><svg
        viewBox="0 0 12 12"
        width="10"
        height="10"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        ><path d={advanced ? 'M3 7.5 6 4.5 9 7.5' : 'M3 4.5 6 7.5 9 4.5'} /></svg
      ></b
    ><small>Size, steps, seed</small></button
  >
  {#if advanced}
    <div class="advanced-controls">
      <div class="field-row">
        <span class="label-with-tip"
          ><label for="edit-size">Edit size</label><HelpTip
            label="Edit size"
            text="The photo is scaled down to this longest edge before editing, and the result has that size. Larger sizes keep more detail but need more memory. Sizes above what LocalSR recommends for this computer and model are disabled, unless Expert, further down, allows them."
          /></span
        ><select
          id="edit-size"
          value={editSizeShown}
          on:change={(event) =>
            updateSettings({ edit_max_dimension: Number(event.currentTarget.value) })}
          >{#each [512, 768, 1024] as size}<option
              value={size}
              disabled={!expert.allowAnySize && size > editRecommended}
              >{size} px{size > editRecommended
                ? expert.allowAnySize
                  ? ' · may not fit'
                  : ' · longest edge · needs more memory'
                : ' · longest edge'}</option
            >{/each}</select
        >
      </div>
      <div class="field-grid">
        <div>
          <label for="edit-steps">Steps</label><HelpTip
            label="Steps"
            text="How many refinement passes the model makes, from 1 to 60. More steps take longer. Choosing another model resets this to that model's own number."
          /><input
            id="edit-steps"
            type="number"
            min="1"
            max="60"
            value={settings.edit_steps || editModel?.default_steps || 40}
            on:change={(event) =>
              updateSettings({
                edit_steps: Math.min(
                  60,
                  Math.max(1, Math.round(Number(event.currentTarget.value)) || 40),
                ),
              })}
          />
        </div>
        <div>
          <label for="edit-seed">Seed</label><HelpTip
            label="Seed"
            text="The number the model's random choices start from. Try another one to get a different version of the same edit."
          /><input
            id="edit-seed"
            type="number"
            min="0"
            max="2147483647"
            value={settings.edit_seed}
            on:change={(event) =>
              updateSettings({
                edit_seed: Math.min(
                  2147483647,
                  Math.max(0, Math.round(Number(event.currentTarget.value)) || 0),
                ),
              })}
          />
        </div>
      </div>
      <p class="model-description">
        {editModel
          ? `${editModel.name} is tuned for ${editModel.default_steps} steps.`
          : 'More steps take longer.'} The same seed with the same prompt and image repeats a result.
      </p>
      <SaveToField outputDirectory={settings.output_directory} {chooseOutput} />
      <RunSettings
        {settings}
        selectedModel={undefined}
        {capabilities}
        usingTemporalVideo={false}
        {updateSettings}
        {defaults}
        {onUseDefaults}
        {refreshCapabilities}
      />
      {#if hasPlacement}
        <button
          id="edit-expert"
          type="button"
          class="disclosure"
          aria-expanded={expertOpen}
          on:click={() => (expertOpen = !expertOpen)}
          ><span>Expert</span><b aria-hidden="true"
            ><svg
              viewBox="0 0 12 12"
              width="10"
              height="10"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
              ><path d={expertOpen ? 'M3 7.5 6 4.5 9 7.5' : 'M3 4.5 6 7.5 9 4.5'} /></svg
            ></b
          ><small>{expertChanges.length ? 'Changed' : 'Automatic'}</small></button
        >
        {#if expertOpen}
          <div class="expert-controls">
            <div class="field-row">
              <span class="label-with-tip"
                ><label for="edit-text-encoder">Text encoder</label><HelpTip
                  label="Text encoder"
                  text={isMac
                    ? 'The text encoder is the part of the model that reads your description. Automatic runs it on the CPU, which needs the least memory. GPU can be faster but needs more memory at once; if the Mac has too little, the edit stops with a not-enough-memory message.'
                    : "The text encoder is the part of the model that reads your description. Automatic runs it on the GPU when it fits there beside the rest of the model, which is much faster, and on the CPU when it does not. GPU always runs it there; if the GPU's memory is too small, the edit stops with a not-enough-memory message. CPU frees GPU memory and makes every edit much slower."}
                /></span
              ><select
                id="edit-text-encoder"
                disabled={mageFlowOnMac}
                title={mageFlowOnMac ? 'Mage-Flow runs on the CPU on a Mac.' : undefined}
                value={expert.textEncoder}
                on:change={(event) =>
                  updateSettings({
                    edit_text_encoder: event.currentTarget.value as EditPlacement,
                  })}
                ><option value="auto">Automatic</option><option value="gpu">GPU</option><option
                  value="cpu">CPU</option
                ></select
              >
            </div>
            <div class="field-row">
              <span class="label-with-tip"
                ><label for="edit-vae">VAE</label><HelpTip
                  label="VAE"
                  text={isMac
                    ? 'The VAE turns your photo into the form the model works on and turns the result back into a picture. Automatic runs it on the CPU. GPU can be faster but needs more memory at once; if the Mac has too little, the edit stops with a not-enough-memory message.'
                    : 'The VAE turns your photo into the form the model works on and turns the result back into a picture. Automatic runs it on the GPU when it fits, which is faster, most of all at large sizes. GPU always runs it there and can stop the edit with a not-enough-memory message. CPU frees GPU memory and is slower.'}
                /></span
              ><select
                id="edit-vae"
                disabled={mageFlowOnMac}
                title={mageFlowOnMac ? 'Mage-Flow runs on the CPU on a Mac.' : undefined}
                value={expert.vae}
                on:change={(event) =>
                  updateSettings({
                    edit_vae: event.currentTarget.value as EditPlacement,
                  })}
                ><option value="auto">Automatic</option><option value="gpu">GPU</option><option
                  value="cpu">CPU</option
                ></select
              >
            </div>
            {#if hasExpert && !isMac}
              <div class="field-row">
                <span class="label-with-tip"
                  ><label for="edit-vram-limit">GPU memory</label><HelpTip
                    label="GPU memory"
                    text="How much of the graphics card's memory an edit may use. Automatic takes what is free and keeps back at least 1 GB for the screen and other apps. A higher limit can allow a larger Edit size or keep more of the model on the GPU; if that much is not really free, the edit gets much slower or stops with a not-enough-memory message. A lower limit leaves room for other apps."
                  /></span
                ><select
                  id="edit-vram-limit"
                  value={expert.vramLimitMb}
                  on:change={(event) =>
                    updateSettings({ edit_vram_limit_mb: Number(event.currentTarget.value) })}
                  >{#each vramOptions as option}<option value={option.value}>{option.label}</option
                    >{/each}</select
                >
              </div>
              <div class="check-line">
                <label class="check-row"
                  ><input
                    id="edit-any-size"
                    type="checkbox"
                    checked={expert.allowAnySize}
                    on:change={(event) => {
                      const checked = event.currentTarget.checked;
                      updateSettings(
                        checked
                          ? { edit_allow_any_size: true, edit_max_dimension: editSizeShown }
                          : { edit_allow_any_size: false },
                      );
                    }}
                  /> Allow any edit size</label
                ><HelpTip
                  label="Allow any edit size"
                  text="Lets you choose any Edit size, also one above what LocalSR recommends for this computer and model. LocalSR then uses your size as it is and no longer picks a smaller one that fits. If the memory is not enough, the edit gets very slow or stops with a not-enough-memory message. Untick it, or choose a smaller size, to be safe again."
                />
              </div>
            {/if}
            <button
              id="edit-expert-reset"
              type="button"
              class="link-button"
              disabled={!expertChanges.length}
              on:click={() =>
                updateSettings({
                  edit_text_encoder: 'auto',
                  edit_vae: 'auto',
                  edit_vram_limit_mb: 0,
                  edit_allow_any_size: false,
                })}>Reset to automatic</button
            >
          </div>
        {/if}
      {/if}
    </div>
  {/if}
</section>
