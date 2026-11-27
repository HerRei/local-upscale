<script lang="ts">
  import HelpTip from './HelpTip.svelte';
  import RunSettings from './RunSettings.svelte';
  import SaveToField from './SaveToField.svelte';
  import { editDevice, editSizeLimit } from './lib/editing';
  import type { CapabilityInfo, EditModel, UiSettings } from './lib/types';

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

  let advanced = false;

  $: editSizeAllowed = editSizeLimit(capabilities, editDevice(capabilities, settings));
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
            text="The photo is scaled down to this longest edge before editing, and the result has that size. Larger sizes keep more detail but need more memory; sizes this computer cannot hold are disabled."
          /></span
        ><select
          id="edit-size"
          value={Math.min(settings.edit_max_dimension, editSizeAllowed)}
          on:change={(event) =>
            updateSettings({ edit_max_dimension: Number(event.currentTarget.value) })}
          >{#each [512, 768, 1024] as size}<option value={size} disabled={size > editSizeAllowed}
              >{size} px · longest edge{size > editSizeAllowed
                ? ' · needs more memory'
                : ''}</option
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
    </div>
  {/if}
</section>
