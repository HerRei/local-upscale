<script lang="ts">
  import { onMount } from 'svelte';
  import MediaIllustration from './MediaIllustration.svelte';
  import type { MediaItem } from './lib/types';

  export let media: MediaItem;
  /** A result still being prepared: the note sits over the picture instead of filling the stage. */
  export let loading = '';
  const mountedAt = Date.now();
  let now = mountedAt;
  const labels: Record<string, string> = {
    opening: 'Opening file',
    decoding_video: 'Reading the first video frame',
    decoding_image: 'Reading image pixels',
    converting_hdr: 'Converting HDR to an SDR preview',
    preparing_preview: 'Creating preview',
  };
  $: elapsed = Math.max(0, Math.floor((now - (media.probe_started_at ?? mountedAt)) / 1000));
  $: stage = loading
    ? 'Creating preview'
    : (labels[media.probe_stage ?? ''] ?? 'Waiting for the preview engine');
  onMount(() => {
    const timer = setInterval(() => {
      now = Date.now();
    }, 1000);
    return () => clearInterval(timer);
  });
</script>

<div class="canvas-empty preview-activity" class:over-picture={loading} aria-busy="true">
  {#if !loading}<MediaIllustration active />{/if}
  <h2>{loading ? `Loading ${loading}…` : 'Preparing preview…'}</h2>
  {#if !loading}<p class="filename" title={media.name}>{media.name}</p>{/if}
  <div class="phase">
    <span class="activity-dot" aria-hidden="true"></span><span role="status">{stage}</span>
  </div>
  {#if !loading}<p class="elapsed" aria-hidden="true">{elapsed}s elapsed</p>{/if}
  {#if elapsed >= 15 && !loading}<p class="slow-file">
      Large files and files stored in iCloud can take longer to open.
    </p>{/if}
</div>

<style>
  .preview-activity {
    max-width: 420px;
    padding: 24px;
  }
  .preview-activity.over-picture {
    padding: 14px 18px;
    border: 1px solid var(--border);
    border-radius: var(--radius-surface);
    background: var(--panel);
  }
  .over-picture h2 {
    margin: 0 0 8px;
  }
  .filename {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 360px;
  }
  .phase {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 8px;
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
  }
  .activity-dot {
    width: 6px;
    height: 6px;
    flex: 0 0 6px;
    border-radius: 50%;
    background: #aaaaaa;
    animation: pulse 1.5s ease-in-out infinite;
  }
  p.elapsed {
    margin: 9px 0 0;
    font-variant-numeric: tabular-nums;
    font-size: calc(11px * var(--ui-scale));
  }
  p.slow-file {
    margin: 16px 0 0;
    max-width: 320px;
  }
  @keyframes pulse {
    50% {
      opacity: 0.3;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .activity-dot {
      animation: none;
    }
  }
</style>
