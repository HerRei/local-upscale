<script lang="ts">
  import HelpTip from './HelpTip.svelte';

  export let outputDirectory: string;
  export let chooseOutput: () => Promise<void>;
  export let disabled = false;
</script>

<div class="directory-field">
  <button
    type="button"
    class="directory-hit"
    {disabled}
    aria-label={`Save to ${outputDirectory || 'Choose an output folder'}`}
    on:click={chooseOutput}
  ></button>
  <div class="directory-text">
    <span class="label-with-tip"
      ><small>Save to</small><HelpTip
        label="Save to"
        text="The folder where results are saved; it starts as your Desktop. Results get new names and never replace an existing file, and the original photo is not changed."
      /></span
    ><span class="directory-path">{outputDirectory || 'Choose an output folder'}</span>
  </div>
  <b>Choose…</b>
</div>
