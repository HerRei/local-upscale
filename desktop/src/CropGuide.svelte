<script lang="ts">
  /** What Stabilise cuts off: a dimmed band of this fraction at each edge of the picture. */
  export let fraction: number;
</script>

<div class="crop-guide" aria-hidden="true" data-crop={fraction}>
  <div class="crop-guide-keep" style={`inset:${fraction * 100}%`}></div>
</div>

<style>
  .crop-guide {
    position: absolute;
    inset: 0;
    overflow: hidden;
    pointer-events: none;
  }
  .crop-guide-keep {
    position: absolute;
    box-shadow: 0 0 0 100vmax rgba(0, 0, 0, 0.5);
    outline: 1px solid var(--surface-border);
  }
</style>
