<script lang="ts">
  /*
   * The bar of Dust & scratches over the viewer: brush mode and size, Undo, Clear, the tip and
   * Done. It holds no paint of its own; the pane and the app keep the strokes.
   */
  import { onMount } from 'svelte';
  import HelpTip from './HelpTip.svelte';

  export let mode: 'paint' | 'erase';
  /** Brush width in source pixels. */
  export let size: number;
  export let limits: { min: number; max: number };
  export let canUndo = false;
  export let canClear = false;
  /** The Mac names ⌘ in the Undo title; other systems Ctrl. */
  export let mac = false;
  export let onMode: (mode: 'paint' | 'erase') => void;
  export let onSize: (size: number) => void;
  export let onUndo: () => void;
  export let onClear: () => void;
  export let onDone: () => void;

  /** A model fills the wider marks (MI-GAN): the tip says so. */
  export let model = false;
  /** That model is not downloaded yet; the first start fetches it. */
  export let missing = false;

  const TIP_START = 'Paint over each mark: a speck of dust, a scratch, a spot';
  const TIP_END =
    'Zoom in with the wheel to see small marks; hold Space and drag to move the picture. B paints, E erases, [ and ] change the brush, and holding M hides the paint. Each photo keeps its own marks, and a photo without marks is left as it is.';
  $: tip = model
    ? `${TIP_START}, a stain. When you start, LocalSR fills the painted places before the other fixes and the upscale. Thin marks are filled from the picture right beside them. Wider ones are filled by a small model, MI-GAN, which continues the surroundings into the gap. Use a brush that just covers the mark: a hairline painted with a wide brush is treated as a wide mark. A mark across a face, lettering or other fine detail can still come out wrong. ${TIP_END}${missing ? ' The first start downloads 28 MB.' : ''}`
    : `${TIP_START}. When you start, LocalSR fills the painted places from the picture around them, before the other fixes and the upscale. This works for small marks on even areas such as sky, walls and skin. A large area, or one that crosses fine detail, comes out smeared. ${TIP_END}`;

  let paintButton: HTMLButtonElement | undefined;

  onMount(() => paintButton?.focus());

  function modeKey(event: KeyboardEvent): void {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const next = mode === 'paint' ? 'erase' : 'paint';
    onMode(next);
    const group = (event.currentTarget as HTMLElement).closest('[role="radiogroup"]');
    group?.querySelector<HTMLButtonElement>(`[data-mode="${next}"]`)?.focus();
  }
</script>

<div
  class="paint-bar"
  role="toolbar"
  aria-label="Dust & scratches"
  tabindex="-1"
  on:pointerdown|stopPropagation
  on:wheel|stopPropagation
>
  <div class="seg-inline paint-mode" role="radiogroup" aria-label="Brush">
    <button
      bind:this={paintButton}
      type="button"
      role="radio"
      data-mode="paint"
      aria-checked={mode === 'paint'}
      class:active={mode === 'paint'}
      title="Paint over a mark · B"
      on:keydown={modeKey}
      on:click={() => onMode('paint')}>Paint</button
    >
    <button
      type="button"
      role="radio"
      data-mode="erase"
      aria-checked={mode === 'erase'}
      class:active={mode === 'erase'}
      title="Take paint away · E"
      on:keydown={modeKey}
      on:click={() => onMode('erase')}>Erase</button
    >
  </div>
  <span class="paint-size">
    <input
      type="range"
      aria-label="Brush size"
      title="Brush size · [ and ]"
      min={limits.min}
      max={limits.max}
      step="0.1"
      value={size}
      on:input={(event) => onSize(Number(event.currentTarget.value))}
    />
    <span class="paint-size-text">{Math.round(size)} px</span>
  </span>
  <button
    type="button"
    class="paint-action"
    title={mac ? 'Undo · ⌘Z' : 'Undo · Ctrl+Z'}
    disabled={!canUndo}
    on:click={onUndo}>Undo</button
  >
  <button
    type="button"
    class="paint-action"
    title="Take every mark off this photo"
    disabled={!canClear}
    on:click={onClear}>Clear</button
  >
  <HelpTip label="Dust & scratches" text={tip} />
  <button
    type="button"
    class="button primary compact paint-done"
    title="Finish painting · Esc"
    on:click={onDone}>Done</button
  >
</div>
