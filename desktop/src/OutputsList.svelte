<script lang="ts">
  import { MAX_SLOTS, pickKey, type OutputGroup } from './lib/compareOutputs';
  import { formatDuration } from './lib/state';
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
          ><strong>{group.media.name}</strong><span
            >{group.outputs.length} {group.outputs.length === 1 ? 'output' : 'outputs'}</span
          ></button
        >
      </div>
      {#if !folded.has(group.media.id)}
        {#each [{ pick: group.original, result: null }, ...group.outputs] as entry (pickKey(entry.pick))}
          {@const n = slot(entry.pick)}
          <label class="output-row" class:picked={n > 0}>
            <input
              type="checkbox"
              checked={n > 0}
              disabled={!n && (picks.length >= MAX_SLOTS || entry.result?.available === false)}
              aria-label={`${entry.result?.model_label ?? 'Original'}, ${group.media.name}`}
              on:change={() => togglePick(entry.pick)}
            />
            <b class="slot">{n || ''}</b>
            <span class="copy"
              ><strong>{entry.result?.model_label || 'Original'}</strong><small
                >{entry.result
                  ? [
                      entry.result.ui?.quality,
                      entry.result.scale ? `${entry.result.scale}×` : '',
                      entry.result.elapsed_seconds
                        ? formatDuration(entry.result.elapsed_seconds)
                        : '',
                      entry.result.available ? '' : 'File missing',
                    ]
                      .filter(Boolean)
                      .join(' · ')
                  : `${group.media.width} × ${group.media.height}`}</small
              ></span
            >
          </label>
        {/each}
      {/if}
    {/each}
  </div>
  <div class="media-actions compare-bar">
    <button class="button" disabled={picks.length < 2 || compareActive} on:click={openCompare}
      >{compareActive ? 'Comparing' : 'Show in Compare'}{picks.length
        ? ` ${picks.length}`
        : ''}</button
    ><button class="button" disabled={!picks.length} on:click={clearPicks}>Clear</button><span
      >{picks.length} of 4 slots</span
    >
  </div>
{/if}

<style>
  .group-head {
    display: flex;
    align-items: center;
    position: sticky;
    top: 0;
    background: var(--panel);
    z-index: 1;
    padding: 6px 0 2px;
  }
  .fold {
    border: 0;
    background: transparent;
    color: var(--secondary);
    width: 24px;
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
    padding: 6px 4px;
  }
  /* Group headers read like the inspector's section labels. */
  .group-select strong {
    font-size: calc(12px * var(--ui-scale));
    font-weight: 600;
  }
  .group-select strong,
  .copy strong {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .group-select span,
  .copy small {
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .output-row {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-left: 24px;
    padding: 5px;
    border-radius: var(--radius);
    cursor: pointer;
  }
  .output-row:hover,
  .output-row.picked {
    background: var(--raised);
  }
  .output-row input {
    margin: 0;
  }
  .slot {
    width: 16px;
    text-align: center;
    font-size: calc(11px * var(--ui-scale));
  }
  .picked .slot {
    color: var(--base);
    background: var(--text);
  }
  .copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .copy strong {
    font-size: calc(13px * var(--ui-scale));
    font-weight: 500;
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
