<script lang="ts">
  /*
   * The preview patch: a framed square drawn in place over the photo, and the tab above it
   * with the step chips and the state. The frame lives in a stage of its own that follows the
   * viewer's zoom and pan, so the picture and the 1 px line sit exactly on the source pixels;
   * the tab stays in screen space so it keeps its size.
   */
  import { onDestroy } from 'svelte';
  import StepChips from './StepChips.svelte';
  import type { StepChip } from './lib/jobSteps';
  import { cropStyle } from './lib/compareRegions';
  import {
    movePatch,
    patchChips,
    shownStep,
    snapRegion,
    type PatchArrow,
    type PatchPlanEntry,
  } from './lib/previewPatch';
  import type { PatchRegion, PatchState } from './lib/types';

  export let media: { width: number; height: number };
  /** The viewer's own stage transform, so the frame zooms and pans with the photo. */
  export let stageStyle: string;
  export let canvas: { width: number; height: number };
  export let stage: { width: number; height: number };
  export let zoom = 1;
  export let pan = { x: 0, y: 0 };
  /** Where the patch is, in source pixels. */
  export let region: PatchRegion;
  export let patch: PatchState | null = null;
  /** Two models: variant B's state and the names of both sides; null without a comparison. */
  export let patchB: PatchState | null = null;
  export let compare: { aName: string; bName: string } | null = null;
  /** The side the frame shows. */
  export let side: 'a' | 'b' = 'a';
  export let onUseB: () => void = () => {};
  export let onDone: () => void = () => {};
  /** The plan line's names, one per step. */
  export let plan: PatchPlanEntry[] = [];
  /** A chip the user pressed (`original` or a step key); null follows the newest step. */
  export let picked: string | null = null;
  /** `M` is held: the patch shows the original. */
  export let holdingOriginal = false;
  /** Size shown on the download button, such as `30 MB`; empty when not known. */
  export let downloadSize = '';
  export let onMove: (region: PatchRegion) => void = () => {};
  /** `Update preview`: ask again and run even when the host would wait. */
  export let onUpdate: () => void = () => {};
  export let onDownload: () => void = () => {};
  export let onRetry: () => void = () => {};

  const ROW_HEIGHT = 28;
  const MOVE_DELAY = 250;

  let tab: HTMLDivElement | undefined;
  let dragFrom: { pointerId: number; x: number; y: number; origin: PatchRegion } | null = null;
  let moving: PatchRegion | null = null;
  let moveTimer: ReturnType<typeof setTimeout> | undefined;
  let now = Date.now();
  let runningSince = 0;
  let runningKey = '';
  let clock: ReturnType<typeof setInterval> | undefined;

  // Both sides draw the same plan; B differs in one model. The side shown owns the chips, the
  // picture and the state text.
  $: current = compare && side === 'b' ? patchB : patch;
  $: tabHeight = compare ? 2 * ROW_HEIGHT - 1 : ROW_HEIGHT;
  $: image = { width: Math.max(1, media.width), height: Math.max(1, media.height) };
  $: shown = moving ?? region;
  // A picture belongs to the position it was made for: while the patch moves, or the host has
  // not answered for the new position yet, the frame shows the original.
  $: pictureHere = Boolean(
    current?.region &&
    current.region.x === shown.x &&
    current.region.y === shown.y &&
    current.region.width === shown.width,
  );
  $: failed = current?.state === 'failed';
  $: steps = current?.steps ?? [];
  $: chips = failed ? patchChips(null, []) : patchChips(current, plan);
  $: pickedOriginal = picked === 'original';
  $: shownIndex = pickedOriginal ? -1 : shownStep(steps, picked);
  $: showOriginal = holdingOriginal || moving !== null || !pictureHere || shownIndex < 0;
  $: picture = showOriginal ? null : (steps[shownIndex] ?? null);
  $: dashed = current?.state === 'stale' || current?.state === 'running';
  $: runningIndex = steps.findIndex((step) => step.state === 'running');
  $: nextIndex =
    runningIndex >= 0 ? runningIndex : steps.findIndex((step) => step.state === 'waiting');
  $: missingIndex = steps.findIndex((step) => step.state === 'missing');
  $: missingEntry = missingIndex >= 0 ? plan[missingIndex] : undefined;

  $: stepChips = chips.map((chip): StepChip => {
    const missing = chip.state === 'missing';
    return {
      id: chip.id,
      label: missing ? chip.label.replace(/^↓ /, '') : chip.label,
      pressed: chip.index === shownIndex,
      disabled: chip.disabled,
      skipped: chip.state === 'skipped',
      running: chip.state === 'running',
      missing,
      title: chip.title,
    };
  });

  // Whole seconds of the running step, counted here because the host reports a time only at
  // the step's end.
  $: {
    const key = current?.state === 'running' && runningIndex >= 0 ? `${runningIndex}` : '';
    if (key !== runningKey) {
      runningKey = key;
      runningSince = Date.now();
      now = runningSince;
    }
  }
  $: if (runningKey && !clock) {
    clock = setInterval(() => (now = Date.now()), 1000);
  } else if (!runningKey && clock) {
    clearInterval(clock);
    clock = undefined;
  }
  $: elapsed = Math.max(
    runningIndex >= 0 ? Math.floor(steps[runningIndex]?.seconds ?? 0) : 0,
    Math.floor((now - runningSince) / 1000),
  );
  $: runningText =
    current?.state === 'running' && nextIndex >= 0 && plan[nextIndex]
      ? `${plan[nextIndex].name} · ${elapsed} s`
      : '';
  $: staleSeconds = current?.estimate_seconds
    ? Math.max(1, Math.round(current.estimate_seconds))
    : 0;

  // The tab sits above the frame, below it when the frame touches the canvas's top.
  $: scale = (stage.width * zoom) / image.width;
  $: frameLeft = canvas.width / 2 + pan.x + (shown.x / image.width - 0.5) * stage.width * zoom;
  $: frameTop = canvas.height / 2 + pan.y + (shown.y / image.height - 0.5) * stage.height * zoom;
  $: frameSide = shown.width * scale;
  $: tabAbove = frameTop - tabHeight >= 0;
  $: tabTop = Math.min(
    Math.max(0, canvas.height - tabHeight),
    tabAbove ? frameTop - tabHeight : frameTop + frameSide,
  );
  // The tab's own width is not known here; the percentage in the shift is, so a tab near the
  // right edge slides left by exactly its overhang.
  $: tabLeft = Math.max(0, frameLeft);

  function settle(next: PatchRegion): void {
    moving = next;
    clearTimeout(moveTimer);
    moveTimer = setTimeout(() => {
      moveTimer = undefined;
      const done = moving;
      moving = null;
      if (done) onMove(done);
    }, MOVE_DELAY);
  }

  function pointerDown(event: PointerEvent): void {
    // The canvas pans on a pointer press; the tab is not part of the photo.
    event.stopPropagation();
    if (event.button !== 0) return;
    if ((event.target as Element | null)?.closest('button')) return;
    event.preventDefault();
    tab?.focus();
    dragFrom = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      origin: moving ?? region,
    };
    clearTimeout(moveTimer);
    moving = dragFrom.origin;
    try {
      tab?.setPointerCapture?.(event.pointerId);
    } catch {
      // Without capture the move still follows while the pointer stays on the tab.
    }
  }

  function pointerMove(event: PointerEvent): void {
    if (!dragFrom || event.pointerId !== dragFrom.pointerId || scale <= 0) return;
    moving = snapRegion(
      {
        x: dragFrom.origin.x + (event.clientX - dragFrom.x) / scale,
        y: dragFrom.origin.y + (event.clientY - dragFrom.y) / scale,
      },
      image,
    );
  }

  function pointerUp(event: PointerEvent): void {
    if (!dragFrom || event.pointerId !== dragFrom.pointerId) return;
    dragFrom = null;
    try {
      tab?.releasePointerCapture?.(event.pointerId);
    } catch {
      // Already released.
    }
    if (moving) settle(moving);
  }

  function keyDown(event: KeyboardEvent): void {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    if ((event.target as Element | null)?.closest('button')) return;
    event.preventDefault();
    event.stopPropagation();
    settle(movePatch(moving ?? region, event.key as PatchArrow, event.shiftKey, image));
  }

  function flipKey(event: KeyboardEvent): void {
    if (!compare || event.key.toLowerCase() !== 'x' || event.repeat) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('input, textarea, select, [contenteditable], [aria-modal="true"]')) return;
    event.preventDefault();
    side = side === 'a' ? 'b' : 'a';
  }

  function pick(id: string): void {
    picked = id;
  }

  onDestroy(() => {
    clearTimeout(moveTimer);
    clearInterval(clock);
  });
</script>

<svelte:window on:keydown={flipKey} />

<div class="patch-layer">
  <div class="patch-stage" style={stageStyle}>
    <div
      class="patch-frame"
      class:dashed
      style={`${cropStyle({ ...shown, target_width: shown.width, target_height: shown.height }, image)};--zoom:${zoom}`}
      data-testid="patch-frame"
    >
      {#if picture?.url}<img src={picture.url} alt="" decoding="async" draggable="false" />{/if}
    </div>
  </div>
  <!-- The tab is a draggable handle: arrow keys move the patch once it has focus. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
  <div
    bind:this={tab}
    class="patch-tab"
    role="group"
    aria-label="Preview patch"
    tabindex="0"
    style={`left:${tabLeft}px;top:${tabTop}px;transform:translateX(min(0px, calc(${canvas.width - tabLeft}px - 100%)))`}
    on:pointerdown={pointerDown}
    on:pointermove={pointerMove}
    on:pointerup={pointerUp}
    on:pointercancel={pointerUp}
    on:keydown={keyDown}
    on:wheel|stopPropagation
  >
    <div class="patch-row">
      <span class="patch-grip" title="Drag to move · arrow keys move it" aria-hidden="true">⠿</span>
      <StepChips chips={stepChips} ariaLabel="Preview steps" onSelect={pick} />
      <span class="patch-state" role="status">
        {#if failed}
          <span title={current?.error || undefined}>Preview failed.</span>
          <button type="button" class="patch-link" on:click={onRetry}>Try again</button>
        {:else if missingIndex >= 0 && current?.state !== 'running'}
          {#if missingEntry?.needsPanel}
            {missingEntry.name} needs a step in the panel first.
          {:else}
            <button type="button" class="patch-action" on:click={onDownload}
              >{downloadSize
                ? `Download ${downloadSize} and preview`
                : 'Download and preview'}</button
            >
          {/if}
        {:else if current?.state === 'stale'}
          <button type="button" class="patch-action" on:click={onUpdate}
            >Update preview{staleSeconds ? ` · about ${staleSeconds} s` : ''}</button
          >
        {:else if runningText}
          {runningText}
        {/if}
      </span>
    </div>
    {#if compare}
      <div class="patch-row patch-compare">
        <div class="patch-switch" role="group" aria-label="Compare models" title="Press X to flip">
          <button
            type="button"
            class="step-chip"
            class:pressed={side === 'a'}
            aria-pressed={side === 'a'}
            on:click={() => (side = 'a')}
          >
            {#if side === 'a'}<span class="activity-dot" aria-hidden="true"></span>{/if}A {compare.aName}
          </button>
          <button
            type="button"
            class="step-chip"
            class:pressed={side === 'b'}
            aria-pressed={side === 'b'}
            on:click={() => (side = 'b')}
          >
            {#if side === 'b'}<span class="activity-dot" aria-hidden="true"></span>{/if}B {compare.bName}
          </button>
        </div>
        <span class="patch-state">
          <button type="button" class="patch-action" on:click={onUseB}>Use B</button>
          <button type="button" class="patch-action" on:click={onDone}>Done</button>
        </span>
      </div>
    {/if}
  </div>
</div>
