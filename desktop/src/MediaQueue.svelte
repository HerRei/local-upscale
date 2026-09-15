<script lang="ts">
  import { formatDuration } from './lib/state';
  import type { JobRecord, MediaItem } from './lib/types';

  export let items: MediaItem[];
  export let jobs: JobRecord[];
  export let activeJobId: string;
  export let batchMode: boolean;
  export let compactHidden = false;
  export let scopeWarning: boolean;
  export let singleScopeMessage: string;
  export let benchmarkRunning: boolean;
  export let inflightMediaIds: Set<string>;
  export let setBatchMode: (value: boolean) => void;
  export let addFiles: (replace?: boolean) => Promise<void>;
  export let locked = false;
  export let selectQueueMedia: (id: string) => Promise<void>;
  export let addFolder: () => Promise<void>;
  export let removeMedia: (id: string) => Promise<void>;
  export let clearMedia: () => Promise<void>;
</script>

<aside class="media-pane pane" class:compact-hidden={compactHidden}>
  <div class="pane-heading">
    <h1>Media</h1>
    <span class="pane-heading-meta"
      >{items.length ? `${items.length} item${items.length === 1 ? '' : 's'}` : 'No media'}</span
    >
    {#if items.length}
      <button
        class="button primary compact add-media"
        disabled={locked || benchmarkRunning}
        on:click={() => addFiles(false)}>＋ Add Media</button
      >
    {/if}
  </div>

  {#if items.length === 0}
    <div class="empty-card">
      <div class="empty-plus" aria-hidden="true">＋</div>
      <strong>Add media to begin</strong>
      <p>Images, camera RAW, or video clips. A folder becomes a batch.</p>
      <div class="empty-actions">
        <button class="button primary" disabled={benchmarkRunning} on:click={() => addFiles(false)}
          >Add Files…</button
        >
        <button class="button" disabled={benchmarkRunning} on:click={addFolder}>Add Folder…</button>
      </div>
    </div>
  {:else}
    <div class="segmented">
      <button
        disabled={Boolean(activeJobId)}
        class:active={!batchMode}
        on:click={() => setBatchMode(false)}>Single</button
      >
      <button
        disabled={Boolean(activeJobId)}
        class:active={batchMode}
        on:click={() => setBatchMode(true)}>Batch</button
      >
    </div>
    <p class="mode-scope" class:warning={!batchMode && scopeWarning}>{singleScopeMessage}</p>
    <div class="media-list">
      {#each items as media (media.id)}
        <div class="media-row" class:selected={media.selected}>
          <button
            class="media-select"
            type="button"
            aria-current={media.selected ? 'true' : undefined}
            on:click={() => selectQueueMedia(media.id)}
          >
            <div class="thumb">
              {#if media.preview_data_url}<img src={media.preview_data_url} alt="" />{:else}<span
                  >{media.kind === 'video' ? 'VID' : 'IMG'}</span
                >{/if}
              {#if media.kind === 'video'}<b>▶</b>{/if}
            </div>
            <div class="media-copy">
              <strong>{media.name}</strong>
              <span
                >{media.probe_status === 'pending'
                  ? 'Preparing preview…'
                  : media.probe_status === 'failed'
                    ? media.error
                    : media.width
                      ? `${media.width} × ${media.height}${media.kind === 'video' ? ` · ${formatDuration(media.duration_seconds)}` : ` · ${((media.width * media.height) / 1_000_000).toFixed(1)} MP`}`
                      : 'Preparing preview…'}</span
              >
              {#if media.hdr_format}<small class="queue-state">{media.hdr_format} HDR</small>{/if}
              {#if inflightMediaIds.has(media.id)}<small class="queue-state"
                  >{jobs.find(
                    (job) =>
                      job.media_id === media.id &&
                      ['queued', 'starting', 'running', 'cancelling'].includes(job.status),
                  )?.status ?? 'queued'}</small
                >{/if}
            </div>
          </button>
          <button
            type="button"
            class="remove"
            disabled={Boolean(activeJobId)}
            aria-label={`Remove ${media.name}`}
            on:click|stopPropagation={() => removeMedia(media.id)}
            ><svg
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.5"
              stroke-linecap="round"
              stroke-linejoin="round"><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg
            ></button
          >
        </div>
      {/each}
    </div>
  {/if}

  {#if items.length}
    <div class="media-actions">
      <button
        class="button"
        title="Add every image in a folder as a batch"
        disabled={benchmarkRunning || Boolean(activeJobId)}
        on:click={addFolder}>Add Folder…</button
      >
      <button class="button danger ghost" disabled={Boolean(activeJobId)} on:click={clearMedia}
        >Clear</button
      >
    </div>
  {/if}
</aside>
