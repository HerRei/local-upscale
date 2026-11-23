<script lang="ts">
  import { formatMediaTime } from './lib/video-sync';
  export let playing = false;
  export let currentTime = 0;
  export let duration = 0;
  export let playbackRate = 1;
  export let label = 'comparison';
  export let toggle: () => void;
  export let seek: (seconds: number) => void;
  export let nextRate: () => void;
  /** One frame back or on; without it the transport has no step buttons. */
  export let step: ((direction: -1 | 1) => void) | undefined = undefined;
  /**
   * A clip of under a second is counted in frames: its length in frames, the frame
   * shown (from 0) and a seek by frame. 0 keeps the clock.
   */
  export let frames = 0;
  export let frame = 0;
  export let seekFrame: ((index: number) => void) | undefined = undefined;
  $: counted = frames > 0 && Boolean(seekFrame);
  $: fractional = duration > 0 && duration < 1;
  $: played = counted
    ? frames > 1
      ? (frame / (frames - 1)) * 100
      : 100
    : duration > 0
      ? Math.min(100, (currentTime / duration) * 100)
      : 0;
</script>

<div class="video-transport" role="group" aria-label="Video playback controls">
  <button
    type="button"
    class="transport-play"
    title="Play or pause · Space"
    aria-label={`${playing ? 'Pause' : 'Play'} ${label}`}
    on:click={toggle}
  >
    {#if playing}
      <svg viewBox="0 0 16 16" aria-hidden="true"
        ><rect x="4" y="3" width="3" height="10" /><rect x="9" y="3" width="3" height="10" /></svg
      >
    {:else}
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3l8 5-8 5z" /></svg>
    {/if}
  </button>
  {#if step}
    <!-- At an end a step button stays focusable: a disabled one would swallow the next key. -->
    <button
      type="button"
      class="transport-step"
      title="Previous frame"
      aria-label="Previous frame"
      aria-disabled={counted && frame <= 0}
      on:click={() => step?.(-1)}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true"
        ><rect x="3" y="4" width="2" height="8" /><path d="M13 4l-6 4 6 4z" /></svg
      >
    </button>
    <button
      type="button"
      class="transport-step"
      title="Next frame"
      aria-label="Next frame"
      aria-disabled={counted && frame >= frames - 1}
      on:click={() => step?.(1)}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true"
        ><path d="M3 4l6 4-6 4z" /><rect x="11" y="4" width="2" height="8" /></svg
      >
    </button>
  {/if}
  {#if counted}
    <span class="transport-time">Frame {frame + 1}</span>
    <input
      class="transport-scrubber"
      aria-label="Video frame"
      aria-valuetext={`Frame ${frame + 1} of ${frames}`}
      type="range"
      min="1"
      max={frames}
      step="1"
      value={frame + 1}
      disabled={frames < 2}
      style={`--played:${played}%`}
      on:input={(event) => seekFrame?.(Number(event.currentTarget.value) - 1)}
    />
    <span class="transport-time">{frames} {frames === 1 ? 'frame' : 'frames'}</span>
  {:else}
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
  {/if}
  <button
    type="button"
    class="transport-rate"
    title="Playback speed"
    aria-label="Playback speed"
    on:click={nextRate}>{playbackRate}×</button
  >
  <slot />
</div>
