<script lang="ts">
  import FileName from './FileName.svelte';
  import { MAX_SLOTS, pickKey, type OutputGroup } from './lib/compareOutputs';
  import { formatDuration } from './lib/format';
  import type { ComparePick } from './lib/types';
  export let groups: OutputGroup[];
  export let picks: ComparePick[];
  export let togglePick: (pick: ComparePick) => void;
  export let clearPicks: () => void;
  export let openCompare: () => void;
  export let compareActive: boolean;
  export let selectMedia: (id: string) => void;
  export let error = '';
  let folded = new Set<string>();
  function fold(id: string): void {
    folded = new Set(folded);
    if (folded.has(id)) folded.delete(id);
    else folded.add(id);
  }
  $: slot = (pick: ComparePick) => picks.findIndex((entry) => pickKey(entry) === pickKey(pick)) + 1;
  const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
</script>

{#if error}<div class="empty-card" role="alert">Could not load outputs: {error}</div>
{:else if groups.length === 0}<div class="empty-card">
    <strong>No outputs yet</strong>
    <p>Finished results appear here under the picture they came from, ready to compare.</p>
  </div>
{:else}
  <div class="media-list outputs-list" role="group" aria-label="Outputs">
    {#each groups as group (group.media.id)}
      <div class="group-head">
        <button
          class="fold"
          aria-expanded={!folded.has(group.media.id)}
          aria-label={`${folded.has(group.media.id) ? 'Unfold' : 'Fold'} ${group.media.name}`}
          on:click={() => fold(group.media.id)}>{folded.has(group.media.id) ? '▸' : '▾'}</button
        >
        <button class="group-select" on:click={() => selectMedia(group.media.id)}
          ><strong><FileName name={group.media.name} /></strong><span
            >{group.outputs.length} {group.outputs.length === 1 ? 'output' : 'outputs'}</span
          ></button
        >
      </div>
      {#if !folded.has(group.media.id)}
        {#each [{ pick: group.original, result: null }, ...group.outputs] as entry (pickKey(entry.pick))}
          {@const n = slot(entry.pick)}
          <!-- Rows read like the Inputs list; a click marks one for Compare. -->
          <div class="media-row" class:selected={n > 0}>
            <button
              class="media-select"
              type="button"
              role="checkbox"
              aria-checked={n > 0}
              aria-label={`${entry.result?.model_label ?? 'Original'}, ${group.media.name}`}
              disabled={!n && (picks.length >= MAX_SLOTS || entry.result?.available === false)}
              on:click={() => togglePick(entry.pick)}
            >
              <div class="thumb">
                {#if group.media.preview_data_url}<img
                    src={group.media.preview_data_url}
                    alt=""
                  />{:else}<span>IMG</span>{/if}
              </div>
              <div class="media-copy">
                <strong>{entry.result?.model_label || 'Original'}</strong>
                <span
                  >{entry.result
                    ? [
                        capitalize(entry.result.ui?.quality ?? ''),
                        entry.result.scale ? `${entry.result.scale}×` : '',
                        entry.result.elapsed_seconds
                          ? formatDuration(entry.result.elapsed_seconds)
                          : '',
                        entry.result.available ? '' : 'File missing',
                      ]
                        .filter(Boolean)
                        .join(' · ')
                    : `${group.media.width} × ${group.media.height}`}</span
                >
              </div>
              {#if n}<b class="slot-number">{n}</b>{/if}
            </button>
          </div>
        {/each}
      {/if}
    {/each}
  </div>
  {#if picks.length}<div class="media-actions compare-bar">
      {#if picks.length >= 2 && !compareActive}<button class="button" on:click={openCompare}
          >Compare these {picks.length}</button
        >{/if}<button class="button" on:click={clearPicks}>Clear</button><span
        >{picks.length} of 4 chosen</span
      >
    </div>{/if}
{/if}

<style>
  .group-head {
    display: flex;
    align-items: center;
    position: sticky;
    top: 0;
    background: var(--panel);
    z-index: 1;
    padding: 8px 4px 2px;
  }
  .fold {
    border: 0;
    background: transparent;
    color: var(--secondary);
    width: 20px;
    padding: 0;
    font-size: calc(12px * var(--ui-scale));
  }
  .group-select {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    text-align: left;
    min-width: 0;
    flex: 1;
    border: 0;
    background: transparent;
    padding: 6px 6px 6px 2px;
  }
  /* Group headers read like the inspector's section labels. */
  .group-select strong {
    font-size: calc(12px * var(--ui-scale));
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .group-select span {
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .media-select .slot-number {
    margin-left: auto;
  }
  .media-select:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .compare-bar {
    flex-wrap: wrap;
  }
  .compare-bar span {
    flex-basis: 100%;
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
  }
</style>
