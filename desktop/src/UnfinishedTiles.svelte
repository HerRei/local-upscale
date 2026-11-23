<script lang="ts">
  /*
   * The finished tiles of an unfinished photo, drawn over the picture. At rest only
   * the line where the finished part ends; lit, the rest of the picture is dimmed and
   * every finished tile is outlined.
   *
   * It is laid out in canvas pixels beside the stage, not inside it: the stage is
   * scaled as one composited layer, which would thicken and blur the lines.
   */
  import { tileMarks, type TileRect } from './lib/unfinished';

  export let tiles: TileRect[];
  /** Pixel size of the saved file the tiles are measured in. */
  export let width: number;
  export let height: number;
  /** The stage on screen: its fitted size, zoom and pan. */
  export let fit: { width: number; height: number };
  export let zoom = 1;
  export let pan: { x: number; y: number } = { x: 0, y: 0 };
  /** Percent hidden from the left and from the right, as the picture below is cut. */
  export let clipLeft = 0;
  export let clipRight = 0;
  export let lit = false;

  $: marks = tileMarks(tiles, width, height);
</script>

{#if marks}
  <svg
    class="unfinished-tiles"
    class:lit
    viewBox={`0 0 ${width} ${height}`}
    preserveAspectRatio="none"
    aria-hidden="true"
    style={`width:${fit.width * zoom}px;height:${fit.height * zoom}px;transform:translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px));clip-path:inset(0 ${clipRight}% 0 ${clipLeft}%)`}
  >
    <path class="rest" d={marks.rest} fill-rule="evenodd" />
    <path class="grid" d={marks.grid} />
    <path class="frontier under" d={marks.frontier} />
    <path class="frontier" d={marks.frontier} />
  </svg>
{/if}

<style>
  .unfinished-tiles {
    position: absolute;
    z-index: 3;
    left: 50%;
    top: 50%;
    max-width: none;
    overflow: hidden;
    pointer-events: none;
  }
  path {
    fill: none;
    vector-effect: non-scaling-stroke;
    transition: opacity 140ms ease-out;
  }
  .rest {
    fill: var(--unfinished-veil);
    opacity: 0;
  }
  .grid {
    stroke: var(--unfinished-line);
    stroke-width: 1px;
    opacity: 0;
  }
  /* A light dashed line on a dark one, so it reads on any picture. */
  .frontier {
    stroke: var(--unfinished-line);
    stroke-width: 1px;
    stroke-dasharray: 5 4;
    opacity: 0.75;
  }
  .frontier.under {
    stroke: var(--unfinished-line-shadow);
    stroke-width: 3px;
    stroke-dasharray: none;
    opacity: 0.4;
  }
  .lit .rest {
    opacity: 1;
  }
  .lit .grid {
    opacity: 0.4;
  }
  .lit .frontier {
    stroke-dasharray: none;
    opacity: 1;
  }
  .lit .frontier.under {
    opacity: 0.6;
  }
  @media (prefers-reduced-motion: reduce) {
    path {
      transition: none;
    }
  }
</style>
