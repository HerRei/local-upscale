<script lang="ts">
  /** LocalSR's mark: crop marks around one terracotta pixel. */
  export let variant: 'bare' | 'tile' = 'bare';
  export let size = 20;
</script>

<svg
  class="brand-logo {variant}"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  aria-hidden="true"
  focusable="false"
>
  {#if variant === 'tile'}<rect class="paper" width="24" height="24" rx="5.5" />{/if}
  <path
    class="marks"
    d={variant === 'tile'
      ? 'M5.5 9V5.5H9M15 5.5h3.5V9M18.5 15v3.5H15M9 18.5H5.5V15'
      : 'M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5'}
  />
  <rect
    class="pixel"
    x={variant === 'tile' ? 11.6 : 11.4}
    y={variant === 'tile' ? 11.6 : 11.4}
    width={variant === 'tile' ? 3.6 : 4.4}
    height={variant === 'tile' ? 3.6 : 4.4}
    rx="0.7"
  />
</svg>

<style>
  .brand-logo {
    display: block;
    flex: none;
  }
  .paper {
    fill: #f1eee5;
  }
  .marks {
    fill: none;
    stroke: #e9e6db;
    stroke-width: 2.2;
    stroke-linecap: square;
  }
  .tile .marks {
    stroke: #242720;
    stroke-width: 1.9;
  }
  .pixel {
    fill: #c4492f;
  }
</style>
