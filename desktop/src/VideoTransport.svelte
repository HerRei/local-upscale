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
  export let mute: (() => void) | undefined = undefined;
  export let muted = false;
  $: fractional = duration > 0 && duration < 1;
  $: played = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0;
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
  {#if mute}<button
      type="button"
      aria-label={muted ? 'Unmute comparison' : 'Mute comparison'}
      aria-pressed={muted}
      on:click={mute}>{muted ? 'Unmute' : 'Mute'}</button
    >{/if}
</div>
