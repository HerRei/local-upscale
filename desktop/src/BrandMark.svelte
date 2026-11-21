<script lang="ts">
  /** LocalSR's mark: crop marks around one terracotta pixel. */
  export let size = 20;
</script>

<svg
  class="brand-logo bare"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  aria-hidden="true"
  focusable="false"
>
  <path class="marks" d="M3.5 9.5v-6h6M14.5 3.5h6v6M20.5 14.5v6h-6M9.5 20.5h-6v-6" />
  <rect class="pixel" x="9.25" y="9.25" width="5.5" height="5.5" rx="1" />
</svg>

<style>
  .brand-logo {
    display: block;
    flex: none;
  }
  .marks {
    fill: none;
    stroke: #ece9df;
    stroke-width: 2.6;
    stroke-linecap: butt;
  }
  .pixel {
    fill: #e0603f;
  }
</style>
