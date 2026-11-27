<script lang="ts">
  import type { StepChip } from './lib/jobSteps';

  export let chips: StepChip[] = [];
  export let label = '';
  export let ariaLabel = '';
  export let onSelect: (id: string) => void = () => {};
</script>

<div class="step-chips" role="group" aria-label={ariaLabel || label || undefined}>
  {#if label}<span class="step-chips-label" aria-hidden={Boolean(ariaLabel) || undefined}
      >{label}</span
    >{/if}
  <div class="step-chips-row">
    {#each chips as chip (chip.id)}
      <button
        type="button"
        class="step-chip"
        class:pressed={chip.pressed}
        class:skipped={chip.skipped}
        class:missing={chip.missing}
        aria-pressed={Boolean(chip.pressed)}
        disabled={Boolean(chip.disabled)}
        title={chip.title || undefined}
        on:click={() => onSelect(chip.id)}
      >
        {#if chip.running}<span class="activity-dot" aria-hidden="true"
          ></span>{/if}{#if chip.missing}<span aria-hidden="true">↓ </span>{/if}{chip.label}
      </button>
    {/each}
  </div>
</div>
