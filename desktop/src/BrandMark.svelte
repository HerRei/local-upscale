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
      : 'M3.5 9.5v-6h6M14.5 3.5h6v6M20.5 14.5v6h-6M9.5 20.5h-6v-6'}
  />
  <rect
    class="pixel"
    x={variant === 'tile' ? 11.6 : 9.25}
    y={variant === 'tile' ? 11.6 : 9.25}
    width={variant === 'tile' ? 3.6 : 5.5}
    height={variant === 'tile' ? 3.6 : 5.5}
    rx="1"
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
    stroke: #ece9df;
    stroke-width: 2.6;
    stroke-linecap: butt;
  }
  .tile .marks {
    stroke: #242720;
    stroke-width: 1.9;
  }
  .pixel {
    fill: #e0603f;
  }
  .tile .pixel {
    fill: #b33d26;
  }
</style>
