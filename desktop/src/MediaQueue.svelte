<script lang="ts">
  import { tabFromKey } from './lib/keyboard';
  import { beingWorkedOn, CANNOT_REMOVE, followAllowed, rowRun } from './lib/rowRun';
  import { tick } from 'svelte';
  import FileName from './FileName.svelte';
  import OutputsList from './OutputsList.svelte';
  import { type OutputGroup } from './lib/compareOutputs';
  import type { ComparePick } from './lib/types';
  import { formatDuration } from './lib/format';
  import type { JobRecord, MediaItem } from './lib/types';

  export let groups: OutputGroup[];
  export let picks: ComparePick[];
  export let togglePick: (pick: ComparePick) => void;
  export let clearPicks: () => void;
  export let openCompare: () => void;
  export let compareActive: boolean;
  export let historyError = '';
  let tab: 'inputs' | 'outputs' = 'inputs';
  $: outputCount = groups.reduce((n, group) => n + group.outputs.length, 0);
  function tabKey(event: KeyboardEvent): void {
    const next = tabFromKey(event.key, tab === 'inputs' ? 0 : 1, 2);
    if (next === null) return;
    event.preventDefault();
    tab = next === 0 ? 'inputs' : 'outputs';
    (event.currentTarget as HTMLElement)
      .querySelectorAll<HTMLButtonElement>('[role="tab"]')
      [next]?.focus();
  }
  export let items: MediaItem[];
  export let jobs: JobRecord[];
  export let activeJobId: string;
  /** The jobs of the batch the engine was sent; they cannot be removed from under it. */
  export let engineJobIds: string[] = [];
  export let compactHidden = false;
  export let scopeWarning: boolean;
  export let scopeHint: string;
  export let benchmarkRunning: boolean;
  export let inflightMediaIds: Set<string>;
  export let addFiles: (replace?: boolean) => Promise<void>;
  export let locked = false;
  export let selectQueueMedia: (id: string) => Promise<void>;
  export let addFolder: () => Promise<void>;
  export let removeMedia: (id: string) => Promise<void>;
  export let clearMedia: () => void;

  /** The row that held the keyboard goes with the item; the focus moves to the row that takes its place. */
  async function removeRow(id: string): Promise<void> {
    const index = items.findIndex((item) => item.id === id);
    await removeMedia(id);
    await tick();
    if (document.activeElement && document.activeElement !== document.body) return;
    const buttons = document.querySelectorAll<HTMLButtonElement>('.media-list .remove:enabled');
    buttons[Math.min(index, buttons.length - 1)]?.focus();
  }

  /** When the user last scrolled or used the list; the list follows the work only after 10 s of calm. */
  let listUsedAt = -Infinity;
  const listUsed = () => (listUsedAt = Date.now());
  let listEl: HTMLElement | undefined;
  let runningIds = new Set<string>();
  $: followWork(
    new Set(
      items
        .filter((media) => rowRun(jobs, media.id, activeJobId).kind === 'running')
        .map((media) => media.id),
    ),
  );
  /** A picture that starts running scrolls into view; focus and selection stay where they are. */
  async function followWork(now: Set<string>): Promise<void> {
    const started = [...now].filter((id) => !runningIds.has(id));
    runningIds = now;
    if (!started.length || !followAllowed(listUsedAt, Date.now())) return;
    await tick();
    const rows = [...(listEl?.querySelectorAll<HTMLElement>('.media-row') ?? [])];
    const index = items.findIndex((media) => media.id === started[0]);
    rows[index]?.scrollIntoView?.({ block: 'nearest' });
  }

  /** The item's newest job when it failed, so the row says it has no result and why (tooltip).
   *  A later run that finished clears it. */
  const failedJob = (mediaId: string) => {
    // The host lists the newest job first; created_at decides whatever the order.
    let latest: JobRecord | undefined;
    for (const job of jobs) {
      if (job.media_id === mediaId && (!latest || job.created_at > latest.created_at)) latest = job;
    }
    return latest?.status === 'failed' ? latest : undefined;
  };
</script>

<aside class="media-pane pane" class:compact-hidden={compactHidden}>
  <!-- The tabs are the pane's heading; each shows how many items it holds. -->
  <div class="pane-heading" data-tauri-drag-region>
    <div
      class="view-switch pane-tabs"
      role="tablist"
      tabindex="-1"
      aria-label="Media"
      on:keydown={tabKey}
    >
      <button
        role="tab"
        aria-selected={tab === 'inputs'}
        tabindex={tab === 'inputs' ? 0 : -1}
        class:active={tab === 'inputs'}
        on:click={() => (tab = 'inputs')}
        >Inputs{#if items.length}{' '}<span class="tab-count">{items.length}</span>{/if}</button
      >
      <button
        role="tab"
        aria-selected={tab === 'outputs'}
        tabindex={tab === 'outputs' ? 0 : -1}
        class:active={tab === 'outputs'}
        on:click={() => (tab = 'outputs')}
        >Outputs{#if outputCount}{' '}<span class="tab-count">{outputCount}</span>{/if}</button
      >
    </div>
    {#if items.length && tab === 'inputs'}<button
        class="icon-button add-media"
        title="Add Media…"
        aria-label="Add Media…"
        disabled={locked || benchmarkRunning}
        on:click={() => addFiles(false)}
        ><svg
          xmlns="http://www.w3.org/2000/svg"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg
        ></button
      >{/if}
  </div>

  {#if tab === 'outputs'}
    <OutputsList
      {groups}
      {picks}
      {togglePick}
      {clearPicks}
      {openCompare}
      {compareActive}
      error={historyError}
      selectMedia={(id) => void selectQueueMedia(id)}
    />
  {:else if items.length === 0}
    <div class="empty-card">
      <div class="empty-plus" aria-hidden="true">
        <svg
          viewBox="0 0 24 24"
          width="18"
          height="18"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg
        >
      </div>
      <strong>Add media to begin</strong>
      <p>Images, camera RAW, or video clips. A folder becomes a batch.</p>
      <div class="empty-actions">
        <button class="button" disabled={benchmarkRunning} on:click={() => addFiles(false)}
          >Add Files…</button
        >
        <button class="button" disabled={benchmarkRunning} on:click={addFolder}>Add Folder…</button>
      </div>
    </div>
  {:else}
    {#if items.length === 1 && scopeWarning}
      <p class="mode-scope warning">{scopeHint}</p>
    {/if}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="media-list"
      bind:this={listEl}
      on:wheel|passive={listUsed}
      on:touchstart|passive={listUsed}
      on:pointerdown={listUsed}
      on:keydown={listUsed}
    >
      {#each items as media (media.id)}
        {@const run = rowRun(jobs, media.id, activeJobId)}
        <div
          class="media-row"
          class:selected={media.selected}
          class:running={run.kind === 'running'}
        >
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
              <strong><FileName name={media.name} /></strong>
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
              {#if !inflightMediaIds.has(media.id) && failedJob(media.id)}<small
                  class="queue-state failed"
                  title={failedJob(media.id)?.error}>Could not finish</small
                >{/if}
              {#if run.text}<small class="queue-state" title={run.title || undefined}
                  >{run.text}</small
                >{/if}
            </div>
          </button>
          <button
            type="button"
            class="remove"
            disabled={beingWorkedOn(jobs, media.id, engineJobIds)}
            title={beingWorkedOn(jobs, media.id, engineJobIds) ? CANNOT_REMOVE : undefined}
            aria-label={`Remove ${media.name}`}
            {...beingWorkedOn(jobs, media.id, engineJobIds)
              ? { 'aria-description': CANNOT_REMOVE }
              : {}}
            on:click|stopPropagation={() => removeRow(media.id)}
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
          {#if run.kind === 'running'}
            <!-- Only running rows carry a progress bar, so a long list stays quiet for screen readers. -->
            <div
              class="row-progress"
              role="progressbar"
              aria-label={`Progress of ${media.name}`}
              aria-valuemin="0"
              aria-valuemax="100"
              aria-valuenow={run.progress === null ? undefined : Math.round(run.progress)}
            >
              {#if run.progress !== null}<i style={`width:${run.progress}%`}></i>{/if}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  {#if items.length && tab === 'inputs'}
    <div class="media-actions">
      <button
        class="button"
        title="Add every image in a folder as a batch"
        disabled={benchmarkRunning || Boolean(activeJobId)}
        on:click={addFolder}>Add Folder…</button
      >
      <button
        class="button"
        disabled={Boolean(activeJobId)}
        title={activeJobId ? 'Cannot clear the list while a queue runs.' : undefined}
        on:click={clearMedia}>Clear</button
      >
    </div>
  {/if}
</aside>

<style>
  /* The tab words carry 8 px of padding, so the row starts 8 px earlier to put their text
     where the pane's title used to be. */
  .pane-heading {
    padding-left: 8px;
  }
  .tab-count {
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
    font-weight: 400;
  }
</style>
