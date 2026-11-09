<script lang="ts">
  /*
   * The split layout: one stage, the reference under a divider and Showing
   * over it, each side labelled. Both sides fill the same source-shaped stage,
   * so a 1×, 2× and 4× result line up in source coordinates. The source image,
   * the live tiles and anything else below the comparison come in through the
   * slot; the Original stays bound to the real source there, under the
   * reference layer, and holding M uncovers it.
   *
   * Another layout (side by side, flip, grid) can replace this component and
   * keep PreviewPane's viewport and the same side descriptions.
   */
  import type { ComparisonSide } from './lib/results';

  export let stageStyle: string;
  /** Two sides and a divider; false for a single result in its own framing. */
  export let split: boolean;
  export let view: 'original' | 'split' | 'enhanced';
  export let peeking: boolean;
  /** Percent of the Showing layer hidden from the left. */
  export let resultClip: number;
  export let showing: ComparisonSide;
  /** A saved result under the divider; undefined when the reference is the Original. */
  export let reference: ComparisonSide | undefined = undefined;
  export let showingAlt = 'Enhanced result';
  export let divider: { x: number; top: number; bottom: number; handleY: number; percent: number };
  export let dividerDown: (event: PointerEvent) => void;
  export let dividerMove: (event: PointerEvent) => void;
  export let dividerUp: (event: PointerEvent) => void;
  export let dividerKey: (event: KeyboardEvent) => void;

  $: referenceLabel = reference?.label ?? 'Original';
  $: showingLabel = showing.label || 'enhanced';
  $: chip = peeking ? 'Original' : split && view === 'original' ? referenceLabel : '';
  $: sideLabels = split && view === 'split' && !peeking && Boolean(showing.label);

  function placeholder(side: ComparisonSide): string {
    return side.state === 'error'
      ? `${side.label}: ${side.error || 'the preview could not be decoded'}. The saved file is unchanged.`
      : `Loading ${side.label}…`;
  }
</script>

<div class="image-stage" style={stageStyle}>
  <slot />
  {#if reference && !peeking}
    {#if reference.src}
      <img
        class="reference-image"
        src={reference.src}
        alt={`Reference: ${reference.label}`}
        draggable="false"
      />
    {:else}
      <div class="side-placeholder" class:failed={reference.state === 'error'} role="status">
        <span>{placeholder(reference)}</span>
      </div>
    {/if}
  {/if}
  {#if showing.src}
    <img
      class="result-image"
      style={`clip-path: inset(0 0 0 ${resultClip}%)`}
      src={showing.src}
      alt={showingAlt}
      draggable="false"
    />
  {:else if showing.state !== 'ready' && !peeking}
    <div
      class="side-placeholder showing-placeholder"
      class:failed={showing.state === 'error'}
      style={`clip-path: inset(0 0 0 ${resultClip}%)`}
      role="status"
    >
      <span>{placeholder(showing)}</span>
    </div>
  {/if}
</div>
{#if split}
  <div
    class="compare-line"
    class:held={view !== 'split' || peeking}
    role="slider"
    tabindex={view === 'split' && !peeking ? 0 : -1}
    aria-hidden={view !== 'split' || peeking}
    aria-label="Before and after comparison"
    aria-valuemin="0"
    aria-valuemax="100"
    aria-valuenow={divider.percent}
    aria-valuetext={`${divider.percent}% ${referenceLabel}, ${100 - divider.percent}% ${showingLabel}`}
    style={`left:${divider.x}px;top:${divider.top}px;height:${divider.bottom - divider.top}px;--handle-top:${divider.handleY - divider.top}px`}
    on:pointerdown|stopPropagation={dividerDown}
    on:pointermove|stopPropagation={dividerMove}
    on:pointerup|stopPropagation={dividerUp}
    on:pointercancel|stopPropagation={dividerUp}
    on:keydown={dividerKey}
  >
    <span aria-hidden="true">↔</span>
  </div>
  {#if sideLabels}
    <div class="divider-labels">
      <span role="status" title={referenceLabel}>{referenceLabel}</span><span
        role="status"
        title={showing.label}>{showing.label}</span
      >
    </div>
  {/if}
{/if}
{#if chip}<span class="original-chip" role="status">{chip}</span>{/if}
