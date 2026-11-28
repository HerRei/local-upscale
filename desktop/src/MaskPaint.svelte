<script lang="ts">
  /*
   * The paint of Dust & scratches over the photo. The strokes live in an <svg> laid out like the
   * viewer's stage, its viewBox in source pixels, so they zoom and pan with the picture and stay
   * sharp; a tinted rectangle shows through a mask made of them. The brush ring stays in screen
   * space so it keeps its line width at every zoom.
   */
  import { addPoint, roomFor, strokePath, type RepairStroke } from './lib/maskPaint';
  import type { Point, Size } from './lib/viewport';

  /** The photo's size in source pixels, as the host draws the mask. */
  export let media: Size;
  /** The viewer's own stage transform. */
  export let stageStyle: string;
  export let stage: Size;
  export let zoom = 1;
  export let pan: Point = { x: 0, y: 0 };
  /** The canvas the pointer is read against. */
  export let well: HTMLElement | undefined;
  export let strokes: readonly RepairStroke[] = [];
  /** Brush width in source pixels. */
  export let size: number;
  export let mode: 'paint' | 'erase' = 'paint';
  /** `M` is held: the paint hides. */
  export let hidden = false;
  /** Where the ring is, in canvas pixels; null hides it (the pointer is elsewhere, or Space is held). */
  export let ring: Point | null = null;
  export let onStroke: (stroke: RepairStroke) => void;

  // Under this width on screen the ring becomes a crosshair.
  const RING_MIN = 6;
  let current: RepairStroke | null = null;
  const maskId = `mask-paint-${Math.random().toString(36).slice(2)}`;

  $: image = { width: Math.max(1, media.width), height: Math.max(1, media.height) };
  $: screenScale = (stage.width / image.width) * zoom;
  $: ringWidth = size * screenScale;
  $: shown = current ? [...strokes, current] : strokes;

  /** A pointer position in source pixels: the stage's transform undone. */
  function toSource(event: PointerEvent): Point {
    const box = well?.getBoundingClientRect();
    const left = box?.left ?? 0;
    const top = box?.top ?? 0;
    const x = event.clientX - left - (box?.width ?? 0) / 2 - pan.x;
    const y = event.clientY - top - (box?.height ?? 0) / 2 - pan.y;
    return {
      x: (x / zoom + stage.width / 2) * (image.width / Math.max(1, stage.width)),
      y: (y / zoom + stage.height / 2) * (image.height / Math.max(1, stage.height)),
    };
  }

  /** A press starts a stroke; Alt held makes it an erase stroke. */
  export function begin(event: PointerEvent): void {
    const point = toSource(event);
    current = addPoint(
      { mode: event.altKey ? 'erase' : mode, size, points: [] },
      point.x,
      point.y,
      image,
    );
  }

  export function extend(event: PointerEvent): void {
    if (!current) return;
    const point = toSource(event);
    const next = addPoint(current, point.x, point.y, image);
    if (next !== current && roomFor(strokes, next)) current = next;
  }

  /** The stroke is done: it goes to the photo's list when the host can still take it. */
  export function end(): void {
    const stroke = current;
    current = null;
    if (stroke && roomFor(strokes, stroke)) onStroke(stroke);
  }

  export function stroking(): boolean {
    return current !== null;
  }
</script>

<svg
  class="mask-paint"
  class:hidden
  style={stageStyle}
  viewBox={`0 0 ${image.width} ${image.height}`}
  preserveAspectRatio="none"
  aria-hidden="true"
>
  <defs>
    <mask id={maskId}>
      {#each shown as stroke, index (index)}
        <path
          d={strokePath(stroke)}
          fill="none"
          stroke={stroke.mode === 'paint' ? 'white' : 'black'}
          stroke-width={stroke.size}
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      {/each}
    </mask>
  </defs>
  <rect class="mask-tint" width={image.width} height={image.height} mask={`url(#${maskId})`} />
</svg>
{#if ring}
  {#if ringWidth < RING_MIN}
    <div
      class="brush-ring crosshair"
      aria-hidden="true"
      style={`left:${ring.x}px;top:${ring.y}px`}
    ></div>
  {:else}
    <div
      class="brush-ring"
      aria-hidden="true"
      style={`left:${ring.x}px;top:${ring.y}px;width:${ringWidth}px;height:${ringWidth}px`}
    ></div>
  {/if}
{/if}
