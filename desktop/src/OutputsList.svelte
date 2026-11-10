<script lang="ts">
  import { tick } from 'svelte';
  import FileName from './FileName.svelte';
  import { MAX_SLOTS, pickKey, type OutputGroup } from './lib/compareOutputs';
  import { editDimensions } from './lib/editing';
  import { followAllowed } from './lib/rowRun';
  import { formatDuration } from './lib/format';
  import type { ComparePick, MediaItem, ResultSummary } from './lib/types';
  export let groups: OutputGroup[];
  export let picks: ComparePick[];
  export let togglePick: (pick: ComparePick) => void;
  export let clearPicks: () => void;
  export let clearOutputs: () => void;
  export let openCompare: () => void;
  export let compareActive: boolean;
  export let error = '';
  /** Inputs the user closed; kept by the parent so the choice outlives a visit to the Inputs tab. */
  export let folded = new Set<string>();
  function fold(id: string): void {
    folded = new Set(folded);
    if (folded.has(id)) folded.delete(id);
    else folded.add(id);
  }

  /** When the user last used the list; a new result scrolls into view only after 10 s of calm. */
  let usedAt = -Infinity;
  const used = () => (usedAt = Date.now());
  let listEl: HTMLElement | undefined;
  let seen: Map<string, Set<string>> | null = null;
  $: void noteResults(groups);
  /** A result that arrives opens its input's group and, if the list is left alone, shows it. */
  async function noteResults(current: OutputGroup[]): Promise<void> {
    const before = seen;
    if (current.length) {
      seen = new Map(
        current.map((g) => [g.media.id, new Set(g.outputs.map((o) => o.result.job_id))]),
      );
    }
    if (!before) return;
    const fresh = current.filter((group) =>
      group.outputs.some((output) => !before.get(group.media.id)?.has(output.result.job_id)),
    );
    if (!fresh.length) return;
    if (fresh.some((group) => folded.has(group.media.id)))
      folded = new Set([...folded].filter((id) => !fresh.some((group) => group.media.id === id)));
    if (!followAllowed(usedAt, Date.now())) return;
    await tick();
    listEl
      ?.querySelector<HTMLElement>(`[data-group="${CSS.escape(fresh[0].media.id)}"]`)
      ?.scrollIntoView?.({ block: 'nearest' });
  }
  $: slot = (pick: ComparePick) => picks.findIndex((entry) => pickKey(entry) === pickKey(pick)) + 1;
  const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
  /** What a result row says under its model: settings for an upscale, the edit size for an Edit. */
  function detail(result: ResultSummary, source: MediaItem): string {
    const [width, height] =
      result.kind === 'edit' && result.max_dimension
        ? editDimensions(source.width, source.height, result.max_dimension)
        : [0, 0];
    return [
      capitalize(result.ui?.quality ?? ''),
      result.scale ? `${result.scale}×` : '',
      width ? `${width} × ${height}` : '',
      result.elapsed_seconds ? formatDuration(result.elapsed_seconds) : '',
      result.available ? '' : 'File missing',
    ]
      .filter(Boolean)
      .join(' · ');
  }
  /** The instruction of an Edit as the row's tooltip, cut at a length a tooltip can show. */
  const promptTip = (result: ResultSummary | null) =>
    result?.prompt
      ? result.prompt.length > 120
        ? `${result.prompt.slice(0, 120)}…`
        : result.prompt
      : '';
  /** What a click on a row does: it marks the row for Compare, or takes the mark away. */
  const rowTip = (result: ResultSummary | null, picked: boolean) =>
    [picked ? 'Remove from Compare' : 'Pick for Compare', promptTip(result)]
      .filter(Boolean)
      .join('\n');
</script>

{#if error}<div class="empty-card" role="alert">Could not load outputs: {error}</div>
{:else if groups.length === 0}<p class="outputs-empty">Results appear here as they finish.</p>
{:else}
  <!-- The list only notes that the user is working in it; every action is on a button. -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    class="media-list outputs-list"
    role="group"
    aria-label="Outputs"
    bind:this={listEl}
    on:wheel|passive={used}
    on:touchstart|passive={used}
    on:pointerdown={used}
    on:keydown={used}
  >
    {#each groups as group (group.media.id)}
      <div class="group-head" data-group={group.media.id}>
        <button
          class="group-toggle"
          aria-expanded={!folded.has(group.media.id)}
          title={folded.has(group.media.id) ? 'Show outputs' : 'Hide outputs'}
          on:click={() => fold(group.media.id)}
          ><i class="chevron" class:closed={folded.has(group.media.id)} aria-hidden="true"
          ></i><strong><FileName name={group.media.name} /></strong><span
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
              title={rowTip(entry.result, n > 0)}
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
                    ? detail(entry.result, group.media)
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
  <div class="media-actions compare-bar">
    {#if picks.length >= 2 && !compareActive}<button class="button" on:click={openCompare}
        >Compare these {picks.length}</button
      >{/if}<button class="button" on:click={clearOutputs}>Clear</button>
    {#if picks.length}<span
        >{picks.length} of 4 chosen ·
        <button class="link-button" on:click={clearPicks}>Unpick all</button></span
      >{/if}
  </div>
{/if}

<style>
  .group-head {
    position: sticky;
    top: 0;
    background: var(--panel);
    z-index: 1;
    padding: 8px 0 2px;
  }
  /* The whole stripe is the control: it takes the Inputs rows' hover and the shared focus ring. */
  .group-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    min-width: 0;
    text-align: left;
    cursor: pointer;
    border: 0;
    border-radius: var(--radius);
    background: transparent;
    padding: 6px 10px;
  }
  .group-toggle:hover {
    background: rgba(255, 255, 255, 0.035);
  }
  /* A triangle as tall as the lower-case letters beside it, pointing down when open. */
  .chevron {
    flex: none;
    width: 0;
    height: 0;
    border-style: solid;
    border-color: var(--secondary) transparent transparent;
    border-width: 0.5em 0.3em 0 0.3em;
    font-size: calc(12px * var(--ui-scale));
  }
  .chevron.closed {
    border-color: transparent transparent transparent var(--secondary);
    border-width: 0.3em 0 0.3em 0.5em;
  }
  /* Group headers read like the inspector's section labels. */
  .group-toggle strong {
    flex: 1;
    min-width: 0;
    font-size: calc(12px * var(--ui-scale));
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .group-toggle span {
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .outputs-empty {
    margin: 0;
    padding: 16px 12px;
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
  }
  .media-select {
    cursor: pointer;
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
