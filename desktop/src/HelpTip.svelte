<script lang="ts">
  /**
   * A "?" next to a setting that explains it on hover, keyboard focus or click; Escape
   * closes it. The text is placed by the nearest positioned container, so under a row of
   * fields it spans the whole row instead of running off the side of the panel.
   */
  export let label: string;
  export let text: string;

  let open = false;
  const id = `help-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
</script>

<button
  type="button"
  class="help-tip"
  aria-label={`About ${label}`}
  aria-describedby={id}
  on:mouseenter={() => (open = true)}
  on:mouseleave={() => (open = false)}
  on:focus={() => (open = true)}
  on:blur={() => (open = false)}
  on:click={() => (open = true)}
  on:keydown={(event) => {
    if (event.key === 'Escape') open = false;
  }}>?</button
><span class="help-tip-text" role="tooltip" {id} hidden={!open}>{text}</span>
