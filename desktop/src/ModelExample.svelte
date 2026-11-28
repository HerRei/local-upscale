<script lang="ts">
  import { exampleUrl, type ModelExampleEntry } from './lib/examples';

  export let example: ModelExampleEntry;
  /** The caption's text, made by the caller from the model and the fix. */
  export let caption: string;

  let side: 'before' | 'after' = 'after';
  let held = false;

  $: shown = held ? 'before' : side;
  function arrows(event: KeyboardEvent) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    side = event.key === 'ArrowLeft' ? 'before' : 'after';
    const group = event.currentTarget as HTMLElement;
    group
      .querySelector<HTMLButtonElement>(
        side === 'before' ? 'button:first-child' : 'button:last-child',
      )
      ?.focus();
  }

  $: file = shown === 'before' ? example.before : example.after;
</script>

<div class="example">
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div
    class="example-frame"
    on:pointerdown={() => (held = true)}
    on:pointerup={() => (held = false)}
    on:pointerleave={() => (held = false)}
    on:pointercancel={() => (held = false)}
  >
    <img src={exampleUrl(file)} alt={example.alt} width="312" height="195" draggable="false" />
  </div>
  <div class="example-bar">
    <div class="seg-inline example-switch" role="radiogroup" aria-label="Example picture">
      <button
        type="button"
        role="radio"
        aria-checked={shown === 'before'}
        class:active={shown === 'before'}
        on:keydown={arrows}
        on:click={() => (side = 'before')}>Before</button
      >
      <button
        type="button"
        role="radio"
        aria-checked={shown === 'after'}
        class:active={shown === 'after'}
        on:keydown={arrows}
        on:click={() => (side = 'after')}>After</button
      >
    </div>
    <span class="example-caption">{caption}</span>
  </div>
</div>
