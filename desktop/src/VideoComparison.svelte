<script lang="ts">
  import { createEventDispatcher, onDestroy } from 'svelte';
  const dispatch = createEventDispatcher<{ compatibilityneeded: void }>();
  import { comparisonFromKey, comparisonFromPointer } from './lib/comparison';
  import { commonDuration, formatMediaTime, synchronizationDecision } from './lib/video-sync';

  export let originalSrc: string;
  export let enhancedSrc: string;
  export let compare = 50;

  let viewport: HTMLDivElement;
  let original: HTMLVideoElement;
  let enhanced: HTMLVideoElement;
  let dragging = false;
  let playing = false;
  let currentTime = 0;
  let duration = 0;
  let playbackRate = 1;
  let playbackError = '';
  let originalLoaded = false;
  let enhancedLoaded = false;

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

  function updateDuration(): void {
    duration = commonDuration(original?.duration ?? 0, enhanced?.duration ?? 0);
  }

  function updateComparison(clientX: number): void {
    const bounds = viewport.getBoundingClientRect();
    compare = comparisonFromPointer(clientX, bounds.left, bounds.width);
  }

  function pointerDown(event: PointerEvent): void {
    if (event.button !== 0) return;
    dragging = true;
    updateComparison(event.clientX);
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }

  function pointerMove(event: PointerEvent): void {
    if (!dragging) return;
    updateComparison(event.clientX);
    event.preventDefault();
  }

  function pointerUp(event: PointerEvent): void {
    dragging = false;
    const target = event.currentTarget as HTMLElement;
    if (target.hasPointerCapture?.(event.pointerId))
      target.releasePointerCapture?.(event.pointerId);
  }

  function keyDown(event: KeyboardEvent): void {
    const next = comparisonFromKey(compare, event.key);
    if (next === null) return;
    compare = next;
    event.preventDefault();
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

  function rateChanged(event: Event): void {
    playbackRate = Number((event.currentTarget as HTMLSelectElement).value);
    original.playbackRate = playbackRate;
    enhanced.playbackRate = playbackRate;
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

  onDestroy(() => {
    for (const video of [original, enhanced]) {
      if (!video) continue;
      video.pause();
      video.removeAttribute('src');
      video.load();
    }
  });
</script>

<div
  class="video-comparison"
  role="region"
  aria-label="Video before and after comparison"
  on:pointerdown|stopPropagation
  on:pointermove|stopPropagation
  on:pointerup|stopPropagation
  on:wheel|stopPropagation
>
  <div bind:this={viewport} class="video-viewport">
    <video
      bind:this={original}
      src={originalSrc}
      muted
      playsinline
      preload="auto"
      on:loadedmetadata={updateDuration}
      on:loadeddata={() => (originalLoaded = true)}
      on:error={(event) => mediaError('Original', event)}
    ></video>
    <div class="enhanced-clip" style={`clip-path: inset(0 ${100 - compare}% 0 0)`}>
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
    <span class="video-label original-label">Original</span>
    <span class="video-label enhanced-label">Enhanced</span>
    <div
      class="video-divider"
      class:dragging
      role="slider"
      tabindex="0"
      aria-label="Video before and after comparison"
      aria-valuemin="0"
      aria-valuemax="100"
      aria-valuenow={Math.round(compare)}
      aria-valuetext={`${Math.round(compare)}% enhanced`}
      style={`left:${compare}%`}
      on:pointerdown={pointerDown}
      on:pointermove={pointerMove}
      on:pointerup={pointerUp}
      on:pointercancel={pointerUp}
      on:keydown={keyDown}
    >
      <span aria-hidden="true">↔</span>
    </div>
  </div>
  <div class="video-controls" role="group" aria-label="Video playback controls">
    <button
      class="button"
      type="button"
      on:click={togglePlayback}
      aria-label={playing ? 'Pause comparison' : 'Play comparison'}
      >{playing ? 'Pause' : 'Play'}</button
    >
    <input
      aria-label="Video position"
      type="range"
      min="0"
      max={duration || 0}
      step="0.01"
      value={currentTime}
      on:input={(event) => seek(Number(event.currentTarget.value))}
    />
    <span
      >{formatMediaTime(currentTime, duration > 0 && duration < 1)} / {formatMediaTime(
        duration,
        duration > 0 && duration < 1,
      )}</span
    >
    <label
      >Speed <select
        aria-label="Playback speed"
        value={String(playbackRate)}
        on:change={rateChanged}
        ><option value="0.5">0.5×</option><option value="1">1×</option><option value="1.5"
          >1.5×</option
        ><option value="2">2×</option></select
      ></label
    >
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
  <span class="video-labs">VIDEO · COMPARISON</span>
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
    border-radius: 8px;
    background: #171e30ed;
    color: #c3d1fa;
    pointer-events: none;
  }
  .video-loading i {
    width: 16px;
    height: 16px;
    border: 2px solid #a6c0ff40;
    border-top-color: #b9ccff;
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
