<script module lang="ts">
  let nextId = 0;
  let closeActive: (() => void) | null = null;
</script>

<script lang="ts">
  import { onDestroy, tick } from 'svelte';

  export let placement: 'row' | 'sheet' = 'row';
  export let label: string;
  export let open = false;
  export let width = 230;
  export let disabled = false;
  export let triggerClass = '';
  export let triggerLabel: string | undefined = undefined;
  export let triggerTitle: string | undefined = undefined;
  export let triggerBusy: boolean | undefined = undefined;

  const panelId = `pop-${++nextId}`;
  const FOCUSABLE =
    'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

  let triggerEl: HTMLButtonElement | undefined;
  let surfaceEl: HTMLDivElement | undefined;
  let above = false;
  let maxHeight: number | undefined;
  let sheetBottom: number | undefined;
  let usedPointer = false;
  let pointerOnTrigger = false;

  function clearActive(): void {
    if (closeActive === closeSelf) closeActive = null;
  }

  function closeSelf(): void {
    open = false;
    clearActive();
  }

  export function close(returnFocus = false): void {
    if (!open) return;
    closeSelf();
    if (returnFocus) triggerEl?.focus();
  }

  $: if (disabled && open) closeSelf();
  $: if (!open) clearActive();

  onDestroy(clearActive);

  async function positionSurface(): Promise<void> {
    above = false;
    maxHeight = undefined;
    sheetBottom = undefined;
    await tick();
    if (!open || !surfaceEl || !triggerEl) return;

    if (placement === 'sheet') {
      const pane = surfaceEl.closest('.enhance-pane');
      if (!pane) return;
      const paneRect = pane.getBoundingClientRect();
      const footRect = pane.querySelector('.panel-foot')?.getBoundingClientRect();
      const bodyRect = pane.querySelector('.inspector-scroll')?.getBoundingClientRect();
      sheetBottom = footRect ? Math.max(0, Math.round(paneRect.bottom - footRect.top)) : 0;
      maxHeight = bodyRect
        ? Math.max(120, Math.floor(bodyRect.height))
        : Math.max(120, Math.floor(paneRect.height - sheetBottom));
      return;
    }

    let clip = { top: 0, bottom: window.innerHeight };
    for (let node = surfaceEl.parentElement; node; node = node.parentElement) {
      if (getComputedStyle(node).overflowY !== 'visible') {
        clip = node.getBoundingClientRect();
        break;
      }
    }
    const box = surfaceEl.getBoundingClientRect();
    const triggerBox = triggerEl.getBoundingClientRect();
    const roomAbove = triggerBox.top - clip.top - 8;
    const roomBelow = clip.bottom - triggerBox.bottom - 8;
    if (box.bottom > clip.bottom) {
      if (roomAbove >= box.height) {
        above = true;
      } else if (roomAbove > roomBelow) {
        above = true;
        maxHeight = Math.max(80, Math.floor(roomAbove));
      } else {
        maxHeight = Math.max(80, Math.floor(roomBelow));
      }
    }
  }

  async function handleTriggerClick(event: MouseEvent): Promise<void> {
    if (disabled) return;
    const byKeyboard = !usedPointer && event.detail === 0;
    usedPointer = false;
    pointerOnTrigger = false;
    if (open) {
      close(false);
      return;
    }
    if (closeActive && closeActive !== closeSelf) closeActive();
    closeActive = closeSelf;
    open = true;
    await positionSurface();
    if (!open) return;
    if (byKeyboard) {
      surfaceEl?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    }
  }

  function onWindowClick(event: MouseEvent): void {
    if (!open) return;
    const path = event.composedPath();
    if ((surfaceEl && path.includes(surfaceEl)) || (triggerEl && path.includes(triggerEl))) return;
    close(false);
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    if (!open || event.defaultPrevented || event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    close(true);
  }

  function onWindowFocusIn(event: FocusEvent): void {
    if (!open) return;
    const target = event.target;
    if (!(target instanceof Node)) return;
    if (surfaceEl?.contains(target) || target === triggerEl) return;
    close(false);
  }

  function onWindowPointerUp(): void {
    if (!usedPointer && !pointerOnTrigger) return;
    setTimeout(() => {
      usedPointer = false;
      pointerOnTrigger = false;
    }, 0);
  }

  function onSurfaceFocusOut(event: FocusEvent): void {
    if (!open) return;
    const next = event.relatedTarget;
    if (!(next instanceof Node)) return;
    if (surfaceEl?.contains(next)) return;
    if (next === triggerEl && pointerOnTrigger) return;
    close(false);
  }
</script>

<svelte:window
  on:click={onWindowClick}
  on:keydown={onWindowKeydown}
  on:focusin={onWindowFocusIn}
  on:pointerup={onWindowPointerUp}
/>

{#if placement === 'row'}
  <span class="pop-anchor">
    <button
      bind:this={triggerEl}
      type="button"
      class={triggerClass}
      {disabled}
      aria-expanded={open}
      aria-controls={open ? panelId : undefined}
      aria-label={triggerLabel}
      title={triggerTitle}
      aria-busy={triggerBusy ? 'true' : undefined}
      on:pointerdown={() => {
        usedPointer = true;
        pointerOnTrigger = true;
      }}
      on:keydown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') usedPointer = false;
      }}
      on:click={handleTriggerClick}
    >
      <slot name="trigger" />
    </button>
    {#if open}
      <div
        bind:this={surfaceEl}
        id={panelId}
        class="pop"
        class:above
        role="group"
        aria-label={label}
        style:width={`${width}px`}
        style:max-height={maxHeight ? `${maxHeight}px` : undefined}
        on:focusout={onSurfaceFocusOut}
      >
        <slot {close} />
      </div>
    {/if}
  </span>
{:else}
  <button
    bind:this={triggerEl}
    type="button"
    class={triggerClass}
    {disabled}
    aria-expanded={open}
    aria-controls={open ? panelId : undefined}
    aria-label={triggerLabel}
    title={triggerTitle}
    aria-busy={triggerBusy ? 'true' : undefined}
    on:pointerdown={() => {
      usedPointer = true;
      pointerOnTrigger = true;
    }}
    on:keydown={(event) => {
      if (event.key === 'Enter' || event.key === ' ') usedPointer = false;
    }}
    on:click={handleTriggerClick}
  >
    <slot name="trigger" />
  </button>
  {#if open}
    <div
      bind:this={surfaceEl}
      id={panelId}
      class="sheet"
      role="group"
      aria-label={label}
      style:bottom={sheetBottom !== undefined ? `${sheetBottom}px` : undefined}
      style:max-height={maxHeight ? `${maxHeight}px` : undefined}
      on:focusout={onSurfaceFocusOut}
    >
      <slot {close} />
    </div>
  {/if}
{/if}
