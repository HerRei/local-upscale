<script lang="ts">
  import { createEventDispatcher, onDestroy, onMount } from 'svelte';
  const dispatch = createEventDispatcher<{ compatibilityneeded: void }>();
  import { comparisonFromKey, comparisonFromPointer } from './lib/comparison';
  import { commonDuration, formatMediaTime, synchronizationDecision } from './lib/video-sync';
  import {
    clampPan,
    comparisonGeometry,
    fitSize,
    panBounds,
    pointerCenteredPan,
    zoomLimits,
  } from './lib/viewport';

  export let originalSrc: string;
  export let enhancedSrc: string;
  // Probed source size; the original's own frame size replaces it once loaded.
  export let sourceWidth = 0;
  export let sourceHeight = 0;
  export let compare = 50;
  // Chosen in the preview header; holding M there shows the original.
  export let view: 'original' | 'split' | 'enhanced' = 'split';

  const RATES = [0.5, 1, 1.5, 2];

  let viewport: HTMLDivElement;
  let original: HTMLVideoElement;
  let enhanced: HTMLVideoElement;
  let playing = false;
  let currentTime = 0;
  let duration = 0;
  let playbackRate = 1;
  let playbackError = '';
  let originalLoaded = false;
  let enhancedLoaded = false;
  let pointerOver = false;

  // The same stage as a photo: fitted on the canvas, zoomed and panned as one
  // layer, with the divider in viewport coordinates so it keeps its place.
  let canvasWidth = 1;
  let canvasHeight = 1;
  let frameWidth = 0;
  let frameHeight = 0;
  let zoom = 1;
  let panX = 0;
  let panY = 0;
  let panning = false;
  let comparing = false;
  let panStart = { x: 0, y: 0, panX: 0, panY: 0 };

  $: frame = {
    width: frameWidth || sourceWidth || 16,
    height: frameHeight || sourceHeight || 9,
  };
  $: fitted = fitSize({ width: canvasWidth, height: canvasHeight }, frame);
  $: limits = zoomLimits(frame, fitted);
  $: actualPixelZoom = Math.max(limits.min, Math.min(limits.max, limits.actual));
  $: comparison = comparisonGeometry(
    { width: canvasWidth, height: canvasHeight },
    fitted,
    zoom,
    { x: panX, y: panY },
    compare,
  );
  $: clip = view === 'original' ? 100 : view === 'enhanced' ? 0 : comparison.imagePercent;
  $: fractional = duration > 0 && duration < 1;
  $: played = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0;

  function currentPanBounds(scale = zoom) {
    return panBounds({ width: canvasWidth, height: canvasHeight }, fitted, scale);
  }

  function updateViewport(): void {
    if (!viewport) return;
    const bounds = viewport.getBoundingClientRect();
    canvasWidth = Math.max(1, bounds.width);
    canvasHeight = Math.max(1, bounds.height);
  }

  // Keep zoom and pan inside the limits whenever the canvas or frame changes.
  $: {
    const bounded = Math.max(limits.min, Math.min(limits.max, zoom));
    if (bounded !== zoom) zoom = bounded;
    const pan = clampPan(
      { x: panX, y: panY },
      panBounds({ width: canvasWidth, height: canvasHeight }, fitted, bounded),
    );
    if (pan.x !== panX) panX = pan.x;
    if (pan.y !== panY) panY = pan.y;
  }

  export function setZoom(next: number, clientX?: number, clientY?: number): void {
    const bounded = Math.max(limits.min, Math.min(limits.max, next));
    if (bounded === zoom) return;
    if (bounded === 1 || !viewport) {
      zoom = bounded;
      panX = 0;
      panY = 0;
      return;
    }
    const bounds = viewport.getBoundingClientRect();
    const pan = pointerCenteredPan(
      { x: panX, y: panY },
      {
        x: (clientX ?? bounds.left + bounds.width / 2) - bounds.left - bounds.width / 2,
        y: (clientY ?? bounds.top + bounds.height / 2) - bounds.top - bounds.height / 2,
      },
      zoom,
      bounded,
      currentPanBounds(bounded),
    );
    zoom = bounded;
    panX = pan.x;
    panY = pan.y;
  }

  /** Fit keeps the divider where it was put. */
  export function fitView(): void {
    zoom = 1;
    panX = 0;
    panY = 0;
  }

  export function actualSize(): void {
    setZoom(actualPixelZoom);
  }

  function wheel(event: WheelEvent): void {
    event.preventDefault();
    setZoom(zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12), event.clientX, event.clientY);
  }

  function nearComparison(event: PointerEvent): boolean {
    if (view !== 'split') return false;
    const bounds = viewport.getBoundingClientRect();
    const y = event.clientY - bounds.top;
    return (
      y >= comparison.top &&
      y <= comparison.bottom &&
      Math.abs(event.clientX - bounds.left - comparison.x) <= 32
    );
  }

  /** A new divider position, held on the visible part of the frame. */
  function updateComparison(clientX: number): void {
    const bounds = viewport.getBoundingClientRect();
    compare = comparisonGeometry(
      { width: canvasWidth, height: canvasHeight },
      fitted,
      zoom,
      { x: panX, y: panY },
      comparisonFromPointer(clientX, bounds.left, bounds.width),
    ).percent;
  }

  function pointerDown(event: PointerEvent): void {
    if (event.button !== 0) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('.compare-line') || nearComparison(event)) {
      comparing = true;
      updateComparison(event.clientX);
    } else {
      const bounds = currentPanBounds();
      if (bounds.x <= 0 && bounds.y <= 0) return;
      panning = true;
      panStart = { x: event.clientX, y: event.clientY, panX, panY };
    }
    viewport.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }

  function pointerMove(event: PointerEvent): void {
    if (comparing) {
      updateComparison(event.clientX);
      event.preventDefault();
      return;
    }
    if (!panning) return;
    const pan = clampPan(
      {
        x: panStart.panX + (event.clientX - panStart.x),
        y: panStart.panY + (event.clientY - panStart.y),
      },
      currentPanBounds(),
    );
    panX = pan.x;
    panY = pan.y;
  }

  function pointerUp(event: PointerEvent): void {
    panning = false;
    comparing = false;
    if (viewport.hasPointerCapture?.(event.pointerId))
      viewport.releasePointerCapture?.(event.pointerId);
  }

  function compareKeyDown(event: KeyboardEvent): void {
    // Keys step through the visible frame, the range the slider reports.
    const next = comparisonFromKey(100 - comparison.enhancedPercent, event.key);
    if (next === null) return;
    const x = comparison.left + ((comparison.right - comparison.left) * next) / 100;
    compare = (x / canvasWidth) * 100;
    event.preventDefault();
  }

  function retryPlayback(): void {
    playbackError = '';
    playing = false;
    currentTime = 0;
    duration = 0;
    originalLoaded = false;
    enhancedLoaded = false;
    for (const video of [original, enhanced]) {
      video.pause();
      video.load();
    }
  }

  function originalMetadata(): void {
    frameWidth = original.videoWidth || 0;
    frameHeight = original.videoHeight || 0;
    updateDuration();
  }

  function updateDuration(): void {
    duration = commonDuration(original?.duration ?? 0, enhanced?.duration ?? 0);
  }

  async function togglePlayback(): Promise<void> {
    playbackError = '';
    try {
      if (!enhanced.paused) {
        enhanced.pause();
        original.pause();
        playing = false;
        return;
      }
      if (duration > 0 && currentTime >= duration - 0.02) seek(0);
      original.playbackRate = playbackRate;
      enhanced.playbackRate = playbackRate;
      await Promise.all([original.play(), enhanced.play()]);
      playing = true;
    } catch (error) {
      original.pause();
      enhanced.pause();
      playing = false;
      playbackError = `Playback could not start. Retry the comparison or open the saved file: ${String(error)}`;
    }
  }

  function seek(seconds: number): void {
    const target = Math.max(0, Math.min(duration || 0, seconds));
    currentTime = target;
    if (original) original.currentTime = Math.min(target, finiteDuration(original));
    if (enhanced) enhanced.currentTime = Math.min(target, finiteDuration(enhanced));
  }

  function finiteDuration(video: HTMLVideoElement): number {
    return Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
  }

  function updateTime(): void {
    currentTime = Math.min(enhanced.currentTime || 0, duration || Number.POSITIVE_INFINITY);
    const decision = synchronizationDecision(
      enhanced.currentTime,
      original.currentTime,
      enhanced.paused,
      playbackRate,
    );
    if (decision.seekTo !== null)
      original.currentTime = Math.min(decision.seekTo, finiteDuration(original));
    original.playbackRate = decision.playbackRate;
  }

  function nextRate(): void {
    playbackRate = RATES[(RATES.indexOf(playbackRate) + 1) % RATES.length];
    original.playbackRate = playbackRate;
    enhanced.playbackRate = playbackRate;
  }

  // Space plays and pauses while the comparison (or nothing in particular) has focus.
  function windowKeyDown(event: KeyboardEvent): void {
    if (event.key !== ' ' || event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target && target !== document.body && !viewport?.parentElement?.contains(target)) return;
    if (target?.closest('button, input, textarea, select, [contenteditable]')) return;
    event.preventDefault();
    void togglePlayback();
  }

  function mediaError(which: string, event: Event): void {
    const video = event.currentTarget as HTMLVideoElement;
    const code = video.error?.code ?? 0;
    playing = false;
    original.pause();
    enhanced.pause();
    if (code === 3 || code === 4) dispatch('compatibilityneeded');
    playbackError = `${which} video could not be loaded in this player (media error ${code}). Retry the comparison or open the saved file.`;
  }

  onMount(() => {
    updateViewport();
    const observer =
      typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(updateViewport);
    observer?.observe(viewport);
    return () => observer?.disconnect();
  });

  onDestroy(() => {
    for (const video of [original, enhanced]) {
      if (!video) continue;
      video.pause();
      video.removeAttribute('src');
      video.load();
    }
  });
</script>

<svelte:window on:keydown={windowKeyDown} />

<div
  class="video-comparison"
  role="region"
  aria-label="Video before and after comparison"
  on:pointerenter={() => (pointerOver = true)}
  on:pointerleave={() => (pointerOver = false)}
  on:pointerdown|stopPropagation
  on:pointermove|stopPropagation
  on:pointerup|stopPropagation
  on:wheel|stopPropagation
>
  <div
    bind:this={viewport}
    class="video-viewport"
    class:pannable={zoom > 1}
    class:panning
    class:comparing
    role="application"
    aria-label="Video comparison viewport · scroll to zoom, drag to pan"
    on:wheel={wheel}
    on:pointerdown={pointerDown}
    on:pointermove={pointerMove}
    on:pointerup={pointerUp}
    on:pointercancel={pointerUp}
  >
    <div
      class="image-stage video-stage"
      style={`width:${fitted.width}px;height:${fitted.height}px;left:50%;top:50%;transform:translate(calc(-50% + ${panX}px), calc(-50% + ${panY}px)) scale(${zoom})`}
    >
      <video
        bind:this={original}
        src={originalSrc}
        muted
        playsinline
        preload="auto"
        on:loadedmetadata={originalMetadata}
        on:loadeddata={() => (originalLoaded = true)}
        on:error={(event) => mediaError('Original', event)}
      ></video>
      <div class="enhanced-clip" style={`clip-path: inset(0 0 0 ${clip}%)`}>
        <!-- Captions remain embedded in user media; LocalSR does not invent an external track URL. -->
        <!-- svelte-ignore a11y_media_has_caption -->
        <video
          bind:this={enhanced}
          src={enhancedSrc}
          playsinline
          preload="auto"
          on:loadedmetadata={updateDuration}
          on:loadeddata={() => (enhancedLoaded = true)}
          on:timeupdate={updateTime}
          on:ended={() => {
            playing = false;
            original.pause();
          }}
          on:pause={() => (playing = false)}
          on:play={() => (playing = true)}
          on:error={(event) => mediaError('Enhanced', event)}
        ></video>
      </div>
    </div>
    <div
      class="compare-line"
      class:held={view !== 'split'}
      role="slider"
      tabindex={view === 'split' ? 0 : -1}
      aria-hidden={view !== 'split'}
      aria-label="Video before and after comparison"
      aria-valuemin="0"
      aria-valuemax="100"
      aria-valuenow={Math.round(100 - comparison.enhancedPercent)}
      aria-valuetext={`${Math.round(comparison.enhancedPercent)}% enhanced`}
      style={`left:${comparison.x}px;top:${comparison.top}px;height:${comparison.bottom - comparison.top}px;--handle-top:${comparison.handleY - comparison.top}px`}
      on:keydown={compareKeyDown}
    >
      <span aria-hidden="true">↔</span>
    </div>
    {#if view === 'original'}<span class="original-chip" role="status">Original</span>{/if}
  </div>
  <div class="video-hud">
    <div class="video-transport" role="group" aria-label="Video playback controls">
      <button
        type="button"
        class="transport-play"
        title="Play or pause · Space"
        aria-label={playing ? 'Pause comparison' : 'Play comparison'}
        on:click={togglePlayback}
      >
        {#if playing}
          <svg viewBox="0 0 16 16" aria-hidden="true"
            ><rect x="4" y="3" width="3" height="10" /><rect
              x="9"
              y="3"
              width="3"
              height="10"
            /></svg
          >
        {:else}
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3l8 5-8 5z" /></svg>
        {/if}
      </button>
      <span class="transport-time">{formatMediaTime(currentTime, fractional)}</span>
      <input
        class="transport-scrubber"
        aria-label="Video position"
        type="range"
        min="0"
        max={duration || 0}
        step="0.01"
        value={currentTime}
        style={`--played:${played}%`}
        on:input={(event) => seek(Number(event.currentTarget.value))}
      />
      <span class="transport-time">{formatMediaTime(duration, fractional)}</span>
      <button
        type="button"
        class="transport-rate"
        title="Playback speed"
        aria-label="Playback speed"
        on:click={nextRate}>{playbackRate}×</button
      >
    </div>
    <div class="zoom-hud" class:shown={pointerOver} role="group" aria-label="Video zoom">
      <button
        type="button"
        aria-label="Zoom out"
        disabled={zoom <= limits.min}
        on:click={() => setZoom(zoom / 1.25)}>−</button
      >
      <span title={`Dynamic maximum ${Math.round(limits.max * 100)}%`}
        >{Math.round(zoom * 100)}%</span
      >
      <button
        type="button"
        aria-label="Zoom in"
        disabled={zoom >= limits.max}
        on:click={() => setZoom(zoom * 1.25)}>＋</button
      >
      <button type="button" class:active={Math.abs(zoom - 1) < 0.001} on:click={fitView}>Fit</button
      >
      <button
        type="button"
        class:active={Math.abs(zoom - actualPixelZoom) < 0.001}
        title="One source pixel per screen pixel"
        on:click={actualSize}>1:1</button
      >
    </div>
  </div>
  {#if playbackError}
    <div class="video-error" role="alert">
      <p>{playbackError}</p>
      <button class="button" type="button" on:click={retryPlayback}>Retry comparison</button>
    </div>
  {:else if !originalLoaded || !enhancedLoaded}
    <div class="video-loading" role="status">
      <i aria-hidden="true"></i>Loading video comparison…
    </div>
  {/if}
</div>

<style>
  .video-error p {
    margin: 0 0 8px;
  }
  .video-loading {
    position: absolute;
    z-index: 7;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 16px;
    border-radius: var(--radius);
    background: #1e1e1eed;
    color: #d1d1d1;
    pointer-events: none;
  }
  .video-loading i {
    width: 16px;
    height: 16px;
    border: 2px solid #bfbfbf40;
    border-top-color: #cccccc;
    border-radius: 50%;
    animation: loading-spin 1s linear infinite;
  }
  @keyframes loading-spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .video-loading i {
      animation: none;
    }
  }
</style>
