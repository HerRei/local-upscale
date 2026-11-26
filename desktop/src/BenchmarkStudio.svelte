<script lang="ts">
  import { themeColor } from './lib/themeColor';
  import { onDestroy, tick } from 'svelte';
  import {
    boundedPreviewSize,
    canvasTileRect,
    paintTileGrid,
    tilePercentages,
    type OutputTile,
  } from './lib/progressive-preview';
  import type { BenchmarkRender, WorkerEnvelope } from './lib/types';
  export let renders: BenchmarkRender[] = [];
  export let running = false;
  export let tileMessage: WorkerEnvelope | undefined = undefined;
  let canvas: HTMLCanvasElement;
  let renderKey = '';
  let generation = 0;
  let work = Promise.resolve();
  let visible = false;
  let width = 1;
  let height = 1;
  let completed = 0;
  let total = 0;
  let sceneName = '';
  let device = '';
  let modelName = '';
  let modelScale = 4;
  let lastTilePaintAt = 0;
  let pendingTileCount = 0;
  let active: ReturnType<typeof tilePercentages> = null;
  const names: Record<string, string> = {
    's1-classroom': 'Compute',
    's2-gallery': 'Tiled processing',
    's3-gallery-encode': 'Image export',
  };
  $: current = renders[renders.length - 1];
  $: if (tileMessage) queue(tileMessage);
  onDestroy(() => {
    generation += 1;
  });

  function queue(message: WorkerEnvelope): void {
    const data = message.data;
    const key = `${data.job_id}/${data.device}/${data.scene_id}`;
    if (key !== renderKey || data.phase === 'reset') {
      renderKey = key;
      generation += 1;
      work = Promise.resolve();
      visible = false;
      active = null;
      completed = 0;
      lastTilePaintAt = 0;
      pendingTileCount = 0;
    }
    const tile = tileFromData(data);
    if (tile.image_width <= 0 || tile.image_height <= 0 || data.phase === 'reset') return;
    sceneName = names[String(data.scene_id)] ?? String(data.scene_id);
    device = String(data.device).toUpperCase();
    if (typeof data.model_name === 'string' && data.model_name) modelName = data.model_name;
    const tileScale = Number(data.scale ?? 0);
    if (Number.isFinite(tileScale) && tileScale > 0) modelScale = tileScale;
    total = Number(data.total_tiles ?? 0);
    completed = Math.max(completed, Number(data.completed_tiles ?? 0));
    const position = tilePercentages(
      tile,
      boundedPreviewSize({ width: tile.image_width, height: tile.image_height }, 960),
    );
    // Position updates must not wait behind decoding an earlier tile's JPEG.
    // Its later completion may clear only its own outline, never a newer one.
    if (data.phase === 'started') active = position;
    else if (
      data.phase === 'completed' &&
      position &&
      active &&
      position.x === active.x &&
      position.y === active.y &&
      position.width === active.width &&
      position.height === active.height
    )
      active = null;
    const now = typeof performance === 'undefined' ? Date.now() : performance.now();
    if (data.phase === 'completed') {
      if (!data.jpeg_base64) return;
      const isLast = total > 0 && completed >= total;
      if (
        !isLast &&
        ((lastTilePaintAt > 0 && now - lastTilePaintAt < 75) || pendingTileCount >= 3)
      ) {
        return;
      }
      lastTilePaintAt = now;
    } else if (pendingTileCount >= 3) {
      return;
    }
    const ticket = generation;
    pendingTileCount += 1;
    work = work
      .then(() => paint(data, ticket))
      .catch((error) => {
        console.error('Could not decode benchmark tile preview', error);
      })
      .finally(() => {
        if (ticket === generation) {
          pendingTileCount = Math.max(0, pendingTileCount - 1);
        }
      });
  }

  function tileFromData(data: WorkerEnvelope['data']): OutputTile {
    const tile = Object.fromEntries(
      ['output_x', 'output_y', 'output_width', 'output_height', 'image_width', 'image_height'].map(
        (key) => [key, Number(data[key] ?? 0)],
      ),
    ) as unknown as OutputTile;
    // An edge patch can be narrower than the actual tile size, especially
    // when reopening this panel mid-run.
    const scale = Number(data.scale ?? 0) || modelScale || 4;
    const size = Number(data.active_tile_size ?? 0) * scale;
    if (Number.isFinite(size) && size > 0) tile.grid_width = tile.grid_height = size;
    return tile;
  }

  async function paint(data: WorkerEnvelope['data'], ticket: number): Promise<void> {
    await tick();
    if (!canvas || ticket !== generation) return;
    const tile = tileFromData(data);
    if (tile.image_width <= 0 || tile.image_height <= 0) return;
    if (data.phase === 'reset') return;
    const context = canvas.getContext('2d');
    if (!context) return;
    if (!visible) {
      width = tile.image_width;
      height = tile.image_height;
      const size = boundedPreviewSize({ width, height }, 960);
      canvas.width = size.width;
      canvas.height = size.height;
      context.fillStyle = themeColor('--benchmark-canvas-background');
      context.fillRect(0, 0, canvas.width, canvas.height);
      paintTileGrid(context, tile, canvas);
      visible = true;
    }
    if (data.phase === 'completed') {
      if (data.jpeg_base64) {
        const image = new Image();
        await new Promise<void>((resolve, reject) => {
          image.onload = () => resolve();
          image.onerror = reject;
          image.src = `data:image/jpeg;base64,${data.jpeg_base64}`;
        });
        if (ticket !== generation) return;
        const rect = canvasTileRect(tile, canvas);
        if (rect) {
          context.drawImage(image, rect.x, rect.y, rect.width, rect.height);
          context.strokeStyle = themeColor('--benchmark-tile-border');
          context.lineWidth = 1;
          context.strokeRect(rect.x + 0.5, rect.y + 0.5, rect.width - 1, rect.height - 1);
        }
      }
    }
  }
</script>

<section class="render-studio" aria-label="Benchmark render tiles">
  <header>
    <strong>Live render tiles</strong><span
      >{modelName || current?.model_name || 'Preset model'} · {modelScale}×{device && running
        ? ` · ${device}`
        : ''}</span
    >
  </header>
  <div
    class="render-stage"
    style={`--render-ratio:${visible ? width / height : current ? current.output_width / current.output_height : 1.5}`}
  >
    <canvas
      bind:this={canvas}
      class:hidden={!running || !visible}
      aria-label="Actual completed benchmark tiles"
    ></canvas>
    {#if running && visible && active}
      <div
        class="active-tile"
        style={`left:${active.x}%;top:${active.y}%;width:${active.width}%;height:${active.height}%`}
        aria-label="Tile currently being rendered"
      ></div>
    {/if}
    {#if !running && current}<img
        src={current.output_data_url}
        alt={current.model_name
          ? `Completed ${current.model_name} benchmark render`
          : 'Completed benchmark render'}
      />
    {:else if !running || !visible}<div class="waiting">
        <span class:working={running}></span>{running
          ? 'Preparing model tiles…'
          : 'Run the benchmark to see the model render.'}
      </div>{/if}
  </div>
  <footer>
    <span
      >{running && visible
        ? `${sceneName} · ${completed} / ${total} tiles`
        : current
          ? `${names[current.scene_id] ?? current.scene_id} · ${current.device.toUpperCase()}`
          : 'Fixed benchmark scenes'}</span
    >
    <span>{running && visible && completed < total ? 'Rendering warm-up' : 'Warm-up preview'}</span>
  </footer>
  <p>
    Squares follow real model tile boundaries. Warm-up previews are excluded from the score; timed
    runs measure the same workload without preview overhead.
  </p>
</section>

<style>
  .render-studio {
    margin: 16px 0;
    border: 1px solid var(--benchmark-stage-border);
    border-radius: var(--radius-surface);
    overflow: hidden;
    background: var(--benchmark-stage-bg);
  }
  header,
  footer {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 16px;
    font-size: 12px;
  }
  header span,
  footer,
  p {
    color: var(--benchmark-muted-text);
  }
  .render-stage {
    position: relative;
    margin: 0 auto;
    width: min(calc(100% - 32px), calc(280px * var(--render-ratio)));
    aspect-ratio: var(--render-ratio);
    background: var(--benchmark-placeholder-bg);
    overflow: hidden;
  }
  canvas,
  img {
    width: 100%;
    height: 100%;
    display: block;
    object-fit: contain;
  }
  .hidden {
    display: none;
  }
  .active-tile {
    position: absolute;
    box-sizing: border-box;
    border: 2px solid var(--benchmark-box-border);
    background: var(--benchmark-box-fill);
    box-shadow: inset 0 0 20px var(--benchmark-box-glow);
    animation: pulse 1.2s ease-in-out infinite;
  }
  .waiting {
    position: absolute;
    inset: 0;
    display: flex;
    gap: 12px;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    color: var(--benchmark-muted-text);
  }
  .working {
    width: 16px;
    height: 16px;
    border: 2px solid var(--benchmark-spinner-track);
    border-top-color: var(--benchmark-spinner-head);
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }
  p {
    margin: 0;
    padding: 0 16px 14px;
    font-size: 11px;
    line-height: 1.6;
  }
  @keyframes pulse {
    50% {
      border-color: var(--retry-border);
      background: var(--benchmark-retry-hover-bg);
    }
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .active-tile,
    .working {
      animation: none;
    }
  }
</style>
