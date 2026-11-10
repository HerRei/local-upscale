<script lang="ts">
  /**
   * A "?" next to a setting that explains it on hover, keyboard focus or click; Escape
   * closes it. The text is placed by the nearest positioned container, so under a row of
   * fields it spans the whole row instead of running off the side of the panel.
   */
  import { tick } from 'svelte';

  export let label: string;
  export let text: string;

  let open = false;
  let above = false;
  let tip: HTMLSpanElement;
  const id = `help-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;

  /** Below the anchor unless the scrolling column would cut it off there and it fits above. */
  async function show(): Promise<void> {
    if (open) return;
    open = true;
    above = false;
    await tick();
    const box = tip.getBoundingClientRect();
    let clip = { top: 0, bottom: window.innerHeight };
    for (let node = tip.parentElement; node; node = node.parentElement) {
      if (getComputedStyle(node).overflowY !== 'visible') {
        clip = node.getBoundingClientRect();
        break;
      }
    }
    const room = box.top - clip.top - (tip.offsetParent?.clientHeight ?? 0) - 12;
    above = box.bottom > clip.bottom && room >= box.height;
  }
</script>

<button
  type="button"
  class="help-tip"
  aria-label={`About ${label}`}
  aria-describedby={id}
  on:mouseenter={show}
  on:mouseleave={() => (open = false)}
  on:focus={show}
  on:blur={() => (open = false)}
  on:click={show}
  on:keydown={(event) => {
    // An open tip takes the Escape; a pane that closes on Escape then stays.
    if (event.key === 'Escape' && open) {
      open = false;
      event.preventDefault();
    }
  }}>?</button
><span bind:this={tip} class="help-tip-text" class:above role="tooltip" {id} hidden={!open}
  >{text}</span
>
