<script lang="ts">
  /*
   * One side of the comparison: a button that opens the image's saved results
   * as a listbox. Arrow keys move, Enter or Space chooses, Escape closes. Open
   * and Reveal for the highlighted row sit outside the listbox, so the
   * reference has its own actions while the footer follows Showing.
   */
  import { tick } from 'svelte';
  import {
    ORIGINAL,
    configuredStages,
    resultTitle,
    runTimeLabel,
    selectableResults,
    settingsLabel,
    statusLabel,
    whenLabel,
  } from './lib/results';
  import type { ResultSummary, ResultTarget } from './lib/types';

  export let label: 'Showing' | 'against';
  export let filename: string;
  export let target: ResultTarget;
  /** Shown instead of a result while this image's job draws live tiles. */
  export let liveLabel = '';
  export let results: ResultSummary[];
  /** Completed results of this image, also those beyond the listed ones. */
  export let completedCount = 0;
  export let choose: (target: ResultTarget) => void;
  export let openResult: (path: string) => void = () => {};
  export let revealResult: (path: string) => void = () => {};

  let expanded = false;
  let trigger: HTMLButtonElement;
  let popover: HTMLDivElement | undefined;
  let listbox: HTMLDivElement | undefined;
  let active: ResultTarget = ORIGINAL;
  let left = 12;
  let top = 0;
  const prefix = `result-${label.toLowerCase()}-${Math.random().toString(36).slice(2, 8)}`;

  $: completed = results.filter((result) => result.status === 'completed');
  $: unfinished = results.filter((result) => result.status !== 'completed');
  $: choosable = new Set(selectableResults(results).map((result) => result.job_id));
  $: options = [...completed.map((result) => result.job_id), ORIGINAL];
  $: current = results.find((result) => result.job_id === target);
  $: highlighted = results.find((result) => result.job_id === active);
  $: oldestListed = [...completed].reverse().find((result) => result.available);
  $: buttonText = liveLabel || resultTitle(current);

  function optionId(id: string): string {
    return `${prefix}-${id.replace(/[^A-Za-z0-9_-]/g, '_')}`;
  }

  async function show(): Promise<void> {
    const rect = trigger.getBoundingClientRect();
    const width = Math.min(420, window.innerWidth - 24);
    left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
    top = rect.bottom + 4;
    active = options.includes(target) ? target : options[0];
    expanded = true;
    await tick();
    listbox?.focus();
  }

  function close(): void {
    expanded = false;
    trigger?.focus();
  }

  function select(id: ResultTarget): void {
    if (id !== ORIGINAL && !choosable.has(id)) return;
    choose(id);
    close();
  }

  function keydown(event: KeyboardEvent): void {
    // Keys stay in the list: M must not peek and arrows must not move the divider.
    event.stopPropagation();
    const index = options.indexOf(active);
    if (event.key === 'Escape') close();
    else if (event.key === 'Enter' || event.key === ' ') select(active);
    else if (event.key === 'ArrowDown') active = options[Math.min(options.length - 1, index + 1)];
    else if (event.key === 'ArrowUp') active = options[Math.max(0, index - 1)];
    else if (event.key === 'Home') active = options[0];
    else if (event.key === 'End') active = options[options.length - 1];
    else if (event.key === 'Tab') {
      expanded = false;
      return;
    } else return;
    event.preventDefault();
    void tick().then(() =>
      document.getElementById(optionId(active))?.scrollIntoView?.({ block: 'nearest' }),
    );
  }

  function outside(event: PointerEvent): void {
    if (
      expanded &&
      event.target instanceof Node &&
      !popover?.contains(event.target) &&
      !trigger?.contains(event.target)
    )
      expanded = false;
  }
</script>

<svelte:window on:pointerdown={outside} on:resize={() => (expanded = false)} />
<div class="result-chooser">
  <button
    class="result-trigger"
    bind:this={trigger}
    aria-label={`${label}: ${buttonText}`}
    aria-haspopup="listbox"
    aria-expanded={expanded}
    on:click={() => (expanded ? close() : void show())}
    ><span class="result-side">{label}</span><b>{buttonText}</b><span aria-hidden="true">⌄</span
    ></button
  >
  {#if expanded}
    <div
      class="result-popover"
      role="presentation"
      bind:this={popover}
      style={`left:${left}px;top:${top}px;max-height:calc(100vh - ${top + 12}px)`}
      on:keydown|stopPropagation={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          close();
        }
      }}
    >
      <strong>Results for {filename}</strong>
      <div
        class="result-list"
        role="listbox"
        tabindex="0"
        bind:this={listbox}
        aria-label={`${label}: results for ${filename}`}
        aria-activedescendant={optionId(active)}
        on:keydown={keydown}
      >
        {#each completed as result (result.job_id)}
          <button
            type="button"
            role="option"
            tabindex="-1"
            id={optionId(result.job_id)}
            aria-selected={target === result.job_id}
            aria-disabled={!choosable.has(result.job_id)}
            class:focused={active === result.job_id}
            on:pointerdown|preventDefault
            on:click={() => {
              active = result.job_id;
              select(result.job_id);
            }}
          >
            <b>{result.kind === 'edit' ? `Edit · ${result.model_label}` : result.model_label}</b>
            <span
              >{settingsLabel(result)}{result.scale && result.kind !== 'edit'
                ? ` · ${result.scale}×`
                : ''} · {runTimeLabel(result)}</span
            >
            <span>{result.output_name} · {whenLabel(result.completed_at)}</span>
            {#if configuredStages(result).length}<span
                >Configured: {configuredStages(result).join(' · ')}</span
              >{/if}
            {#if result.notes}<span>{result.notes}</span>{/if}
            {#if statusLabel(result)}<span class="result-status">{statusLabel(result)}</span>{/if}
          </button>
        {/each}
        <button
          type="button"
          role="option"
          tabindex="-1"
          id={optionId(ORIGINAL)}
          aria-selected={target === ORIGINAL}
          class:focused={active === ORIGINAL}
          on:pointerdown|preventDefault
          on:click={() => select(ORIGINAL)}
        >
          <b>Original</b><span>{filename}</span>
        </button>
      </div>
      {#if highlighted?.available}
        <div class="result-row-actions">
          <span>{highlighted.output_name}</span><button
            class="link-button"
            title={`Open ${highlighted.output_name}`}
            on:click={() => highlighted && openResult(highlighted.output_path)}>Open</button
          ><button
            class="link-button"
            title={`Show ${highlighted.output_name} in its folder`}
            on:click={() => highlighted && revealResult(highlighted.output_path)}>Reveal</button
          >
        </div>
      {/if}
      {#if unfinished.length}
        <div class="result-unfinished" role="group" aria-label="Runs that did not finish">
          {#each unfinished as result (result.job_id)}
            <p>
              <b>{statusLabel(result)} · {result.model_label}</b>
              <span
                >{result.notes || 'No reason was recorded.'} · {whenLabel(
                  result.completed_at,
                )}</span
              >
            </p>
          {/each}
        </div>
      {/if}
      {#if completedCount > completed.length && oldestListed}
        <p class="result-older">
          Older results are in the output folder ·
          <button
            class="link-button"
            on:click={() => oldestListed && revealResult(oldestListed.output_path)}>Reveal</button
          >
        </p>
      {/if}
    </div>
  {/if}
</div>
