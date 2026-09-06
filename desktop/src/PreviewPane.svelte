<script lang="ts">
  import { onMount, tick } from 'svelte';
  import VideoComparison from './VideoComparison.svelte';
  import * as api from './lib/api';
  import { comparisonFromKey, comparisonFromPointer } from './lib/comparison';
  import { boundedPreviewSize, canvasTileRect, tilePercentages, type OutputTile } from './lib/progressive-preview';
  import { clampPan, fitSize, panBounds, pointerCenteredPan, zoomLimits } from './lib/viewport';
  import type { MediaItem, VideoComparisonSources, WorkerEnvelope } from './lib/types';

  export let selectedMedia: MediaItem | undefined;
  export let resultPreview: string;
  export let completedVideoOutput: string;
  export let compactHidden = false;
  export let addFiles: () => Promise<void>;

  let compare = 50;
  let zoom = 1;
  let panX = 0;
  let panY = 0;
  let dragging = false;
  let draggingComparison = false;
  let dragStartX = 0;
  let dragStartY = 0;
  let panStartX = 0;
  let panStartY = 0;
  let canvasWell: HTMLDivElement | undefined;
  let progressiveCanvas: HTMLCanvasElement | undefined;
  let canvasWidth = 1;
  let canvasHeight = 1;
  let previewNaturalWidth = 1;
  let previewNaturalHeight = 1;
  let stageWidth = 1;
  let stageHeight = 1;
  let minZoom = 1;
  let maxZoom = 8;
  let actualPixelZoom = 1;
  let viewedMediaId = '';
  let laidOutSourcePreview = '';
  let previewRetryMediaId = '';
  let progressiveVisible = false;
  let progressiveJobId = '';
  let progressiveOutputWidth = 0;
  let progressiveOutputHeight = 0;
  let progressiveGeneration = 0;
  let progressiveWork: Promise<void> = Promise.resolve();
  let progressivePendingCount = 0;
  let lastProgressivePaintAt = 0;
  let lastActiveTileAt = 0;
  let activeTileVisible = false;
  let activeTileX = 0;
  let activeTileY = 0;
  let activeTileWidth = 0;
  let activeTileHeight = 0;
  let videoComparison: VideoComparisonSources | null = null;
  let videoComparisonKey = '';
  let videoComparisonError = '';
  let disposed = false;

  $: sourcePreview = selectedMedia?.preview_data_url ?? '';
  // Keep the image mounted across transient WebKit decode errors so
  // re-probing cannot turn into an endless loading loop.
  $: renderableSourcePreview = sourcePreview;
  $: desiredVideoComparisonKey =
    selectedMedia?.kind === 'video' && completedVideoOutput
      ? `${selectedMedia.id}:${completedVideoOutput}`
      : '';
  $: if (desiredVideoComparisonKey !== videoComparisonKey) {
    void loadVideoComparison(desiredVideoComparisonKey);
  }

  $: if ((selectedMedia?.id ?? '') !== viewedMediaId) {
    viewedMediaId = selectedMedia?.id ?? '';
    previewNaturalWidth = Math.max(1, selectedMedia?.width ?? 1);
    previewNaturalHeight = Math.max(1, selectedMedia?.height ?? 1);
    laidOutSourcePreview = '';
    previewRetryMediaId = '';
    resetProgressivePreview();
    resetView();
    scheduleViewportReset();
  }

  $: if (sourcePreview && sourcePreview !== laidOutSourcePreview) {
    laidOutSourcePreview = sourcePreview;
    scheduleViewportReset();
  }

  $: if (
    selectedMedia &&
    selectedMedia.probe_status === 'ready' &&
    !sourcePreview
  ) {
    void repairSelectedPreview();
  }

  onMount(() => {
    const observer = typeof ResizeObserver === 'undefined'
      ? undefined : new ResizeObserver(() => updateViewport());
    if (canvasWell) observer?.observe(canvasWell);
    scheduleViewportReset();
    return () => {
      disposed = true;
      observer?.disconnect();
      progressiveGeneration += 1;
      if (api.isTauri()) void api.clearVideoComparison();
    };
  });

  export function actualSize(): void {
    setZoom(actualPixelZoom);
  }

  export function allowPreviewRetry(): void {
    previewRetryMediaId = '';
  }

  function scheduleViewportReset(): void {
    void tick().then(() => {
      if (disposed) return;
      updateViewport(true);
      // The native webview can report a transient 0×0/1×1 rectangle during
      // its first layout pass. Re-check on the next painted frame so an image
      // selected at startup cannot remain as a one-pixel stage.
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => updateViewport(true));
      }
    });
  }

  async function repairSelectedPreview(): Promise<void> {
    const media = selectedMedia;
    if (!media || !api.isTauri() || previewRetryMediaId === media.id) return;
    previewRetryMediaId = media.id;
    try {
      await api.probePath(media.path);
    } catch (error) {
      console.error('Could not refresh the selected media preview', error);
    }
  }

  function onSourcePreviewError(): void {
    previewRetryMediaId = '';
    void repairSelectedPreview();
  }

  export function resetProgressivePreview(jobId = ''): void {
    progressiveGeneration += 1;
    progressiveJobId = jobId;
    progressiveOutputWidth = 0;
    progressiveOutputHeight = 0;
    progressiveVisible = false;
    progressivePendingCount = 0;
    lastProgressivePaintAt = 0;
    lastActiveTileAt = 0;
    activeTileVisible = false;
    progressiveWork = Promise.resolve();
    const context = progressiveCanvas?.getContext('2d');
    if (progressiveCanvas && context) {
      context.clearRect(0, 0, progressiveCanvas.width, progressiveCanvas.height);
    }
  }

  export function queueProgressiveTile(message: WorkerEnvelope, activeJobId: string): void {
    const data = message.data;
    const jobId = String(data.job_id ?? '');
    if (!jobId || jobId !== activeJobId) return;
    const phase = String(data.phase ?? '');
    if (phase === 'reset') resetProgressivePreview(jobId);
    if (!progressiveJobId) progressiveJobId = jobId;
    if (jobId !== progressiveJobId) return;

    const now = typeof performance === 'undefined' ? Date.now() : performance.now();
    if (phase === 'started' && progressiveVisible) {
      // The active outline is informative but does not need to follow a fast
      // model at dozens of updates per second.
      if (now - lastActiveTileAt < 75) return;
      lastActiveTileAt = now;
      const percentage = tilePercentages(tileFromData(data));
      if (percentage) {
        activeTileX = percentage.x;
        activeTileY = percentage.y;
        activeTileWidth = percentage.width;
        activeTileHeight = percentage.height;
        activeTileVisible = true;
      }
      return;
    }
    if (phase === 'completed') {
      // Decoding every JPEG can monopolize WKWebView when a lightweight model
      // finishes many tiny tiles per second. A sampled live mosaic stays useful
      // while preserving input responsiveness; completion always loads the
      // authoritative full output image.
      if (now - lastProgressivePaintAt < 75 || progressivePendingCount >= 3) {
        return;
      }
      lastProgressivePaintAt = now;
    } else if (progressivePendingCount >= 3) {
      return;
    }

    const generation = progressiveGeneration;
    progressivePendingCount += 1;
    progressiveWork = progressiveWork
      .then(() => paintProgressiveTile(data, jobId, generation))
      .catch((error) => console.error('Could not draw progressive tile', error))
      .finally(() => {
        if (generation === progressiveGeneration) {
          progressivePendingCount = Math.max(0, progressivePendingCount - 1);
        }
      });
  }

  function tileFromData(data: WorkerEnvelope['data']): OutputTile {
    return {
      output_x: numeric(data.output_x),
      output_y: numeric(data.output_y),
      output_width: numeric(data.output_width),
      output_height: numeric(data.output_height),
      image_width: numeric(data.image_width),
      image_height: numeric(data.image_height)
    };
  }

  async function paintProgressiveTile(
    data: WorkerEnvelope['data'],
    jobId: string,
    generation: number
  ): Promise<void> {
    if (generation !== progressiveGeneration || jobId !== progressiveJobId) return;
    const phase = String(data.phase ?? '');
    const tile = tileFromData(data);
    if (tile.image_width <= 0 || tile.image_height <= 0) return;

    if (phase === 'reset') {
      progressiveOutputWidth = 0;
      progressiveOutputHeight = 0;
      progressiveVisible = false;
      activeTileVisible = false;
    }
    const ready = await ensureProgressiveCanvas(tile, jobId, generation);
    if (!ready || generation !== progressiveGeneration || jobId !== progressiveJobId) return;

    const percentage = tilePercentages(tile);
    if (phase === 'started' && percentage) {
      activeTileX = percentage.x;
      activeTileY = percentage.y;
      activeTileWidth = percentage.width;
      activeTileHeight = percentage.height;
      activeTileVisible = true;
      return;
    }

    if (phase !== 'completed') return;
    const encoded = typeof data.jpeg_base64 === 'string' ? data.jpeg_base64 : '';
    const canvas = progressiveCanvas;
    const context = canvas?.getContext('2d');
    const destination = canvas
      ? canvasTileRect(tile, { width: canvas.width, height: canvas.height })
      : null;
    if (encoded && canvas && context && destination) {
      const image = await loadHtmlImage(`data:image/jpeg;base64,${encoded}`);
      if (generation !== progressiveGeneration || jobId !== progressiveJobId) return;
      context.drawImage(
        image,
        destination.x,
        destination.y,
        destination.width,
        destination.height
      );
    }
    activeTileVisible = false;
  }

  async function ensureProgressiveCanvas(
    tile: OutputTile,
    jobId: string,
    generation: number
  ): Promise<boolean> {
    const canvas = progressiveCanvas;
    if (!canvas) return false;
    if (
      progressiveOutputWidth === tile.image_width &&
      progressiveOutputHeight === tile.image_height &&
      canvas.width > 1 &&
      canvas.height > 1
    ) {
      return true;
    }

    const size = boundedPreviewSize({ width: tile.image_width, height: tile.image_height });
    canvas.width = size.width;
    canvas.height = size.height;
    const context = canvas.getContext('2d', { alpha: false });
    if (!context) return false;
    context.fillStyle = '#111722';
    context.fillRect(0, 0, size.width, size.height);
    try {
      const source = await loadHtmlImage(sourcePreview);
      if (generation !== progressiveGeneration || jobId !== progressiveJobId) return false;
      context.drawImage(source, 0, 0, size.width, size.height);
    } catch {
      // The bounded mosaic can still show completed tiles while the source
      // preview is being repaired independently.
    }
    context.fillStyle = 'rgba(5, 9, 15, 0.70)';
    context.fillRect(0, 0, size.width, size.height);
    progressiveOutputWidth = tile.image_width;
    progressiveOutputHeight = tile.image_height;
    progressiveVisible = true;
    return true;
  }

  function loadHtmlImage(source: string): Promise<HTMLImageElement> {
    return new Promise((resolve, reject) => {
      if (!source) {
        reject(new Error('preview source is empty'));
        return;
      }
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error('preview image could not be decoded'));
      image.src = source;
    });
  }

  function numeric(value: unknown): number {
    const number = Number(value ?? 0);
    return Number.isFinite(number) ? number : 0;
  }

  function onWheel(event: WheelEvent): void {
    if (!sourcePreview) return;
    event.preventDefault();
    setZoom(zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12), event.clientX, event.clientY);
  }

  function pointerDown(event: PointerEvent): void {
    if (event.button !== 0) return;
    const target = event.target instanceof Element ? event.target : null;
    if (resultPreview && (target?.closest('.compare-line') || pointerNearComparison(event))) {
      draggingComparison = true;
      dragging = false;
      updateComparisonFromPointer(event.clientX);
      (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
      event.preventDefault();
      return;
    }
    if (target?.closest('button, input, select, textarea')) return;
    const bounds = currentPanBounds();
    if (bounds.x <= 0 && bounds.y <= 0) return;
    dragging = true;
    dragStartX = event.clientX;
    dragStartY = event.clientY;
    panStartX = panX;
    panStartY = panY;
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
  }

  function comparisonPointerDown(event: PointerEvent): void {
    if (event.button !== 0 || !resultPreview) return;
    draggingComparison = true;
    dragging = false;
    updateComparisonFromPointer(event.clientX);
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }

  function comparisonPointerMove(event: PointerEvent): void {
    if (!draggingComparison) return;
    updateComparisonFromPointer(event.clientX);
    event.preventDefault();
  }

  function comparisonPointerUp(event: PointerEvent): void {
    draggingComparison = false;
    const target = event.currentTarget as HTMLElement;
    if (target.hasPointerCapture?.(event.pointerId)) {
      target.releasePointerCapture?.(event.pointerId);
    }
  }

  function pointerMove(event: PointerEvent): void {
    if (draggingComparison) {
      updateComparisonFromPointer(event.clientX);
      return;
    }
    if (!dragging) return;
    const bounded = clampPan(
      {
        x: panStartX + event.clientX - dragStartX,
        y: panStartY + event.clientY - dragStartY
      },
      currentPanBounds()
    );
    panX = bounded.x;
    panY = bounded.y;
  }

  function pointerUp(event?: PointerEvent): void {
    dragging = false;
    draggingComparison = false;
    if (event && (event.currentTarget as HTMLElement).hasPointerCapture?.(event.pointerId)) {
      (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
    }
  }

  function pointerNearComparison(event: PointerEvent): boolean {
    if (!canvasWell || !resultPreview) return false;
    const rectangle = canvasWell.getBoundingClientRect();
    const visualWidth = stageWidth * zoom;
    const visualHeight = stageHeight * zoom;
    const left = rectangle.left + rectangle.width / 2 + panX - visualWidth / 2;
    const top = rectangle.top + rectangle.height / 2 + panY - visualHeight / 2;
    const divider = left + visualWidth * (compare / 100);
    return (
      event.clientY >= top &&
      event.clientY <= top + visualHeight &&
      Math.abs(event.clientX - divider) <= 32
    );
  }

  function updateComparisonFromPointer(clientX: number): void {
    if (!canvasWell) return;
    const rectangle = canvasWell.getBoundingClientRect();
    const visualWidth = Math.max(1, stageWidth * zoom);
    const left = rectangle.left + rectangle.width / 2 + panX - visualWidth / 2;
    compare = comparisonFromPointer(clientX, left, visualWidth);
  }

  function compareKeyDown(event: KeyboardEvent): void {
    const next = comparisonFromKey(compare, event.key);
    if (next === null) return;
    compare = next;
    event.preventDefault();
  }

  async function loadVideoComparison(key: string): Promise<void> {
    videoComparisonKey = key;
    videoComparison = null;
    videoComparisonError = '';
    if (!key) {
      if (api.isTauri()) {
        try {
          await api.clearVideoComparison();
        } catch (error) {
          console.warn('Could not clear the video preview scope', error);
        }
      }
      return;
    }
    if (!api.isTauri() || !selectedMedia) return;
    const mediaId = selectedMedia.id;
    try {
      const sources = await api.prepareVideoComparison(mediaId);
      if (disposed || key !== videoComparisonKey || mediaId !== selectedMedia?.id) return;
      videoComparison = sources;
    } catch (error) {
      if (disposed || key !== videoComparisonKey) return;
      videoComparisonError = `Completed video comparison is unavailable: ${String(error)}`;
    }
  }

  export function resetView(): void {
    zoom = 1;
    panX = 0;
    panY = 0;
    compare = 50;
    updateViewport(true);
  }

  function onPreviewLoad(event: Event): void {
    const image = event.currentTarget as HTMLImageElement;
    previewRetryMediaId = '';
    previewNaturalWidth = Math.max(1, image.naturalWidth);
    previewNaturalHeight = Math.max(1, image.naturalHeight);
    updateViewport(true);
    scheduleViewportReset();
  }

  function updateViewport(reset = false): void {
    if (!canvasWell) return;
    const rectangle = canvasWell.getBoundingClientRect();
    canvasWidth = Math.max(1, rectangle.width);
    canvasHeight = Math.max(1, rectangle.height);
    const sourceWidth = Math.max(1, selectedMedia?.width || previewNaturalWidth);
    const sourceHeight = Math.max(1, selectedMedia?.height || previewNaturalHeight);
    const fitted = fitSize(
      { width: canvasWidth, height: canvasHeight },
      { width: sourceWidth, height: sourceHeight }
    );
    stageWidth = fitted.width;
    stageHeight = fitted.height;
    const limits = zoomLimits(
      { width: sourceWidth, height: sourceHeight },
      fitted
    );
    minZoom = limits.min;
    maxZoom = limits.max;
    actualPixelZoom = Math.max(limits.min, Math.min(limits.max, limits.actual));
    if (reset) zoom = 1;
    zoom = Math.max(limits.min, Math.min(limits.max, zoom));
    const bounded = clampPan({ x: panX, y: panY }, currentPanBounds());
    panX = bounded.x;
    panY = bounded.y;
  }

  function currentPanBounds() {
    return panBounds(
      { width: canvasWidth, height: canvasHeight },
      { width: stageWidth, height: stageHeight },
      zoom
    );
  }

  function setZoom(nextZoom: number, clientX?: number, clientY?: number): void {
    const boundedZoom = Math.max(minZoom, Math.min(maxZoom, nextZoom));
    if (boundedZoom === zoom) return;
    if (boundedZoom === 1 || !canvasWell) {
      zoom = boundedZoom;
      panX = 0;
      panY = 0;
      return;
    }
    const rectangle = canvasWell.getBoundingClientRect();
    const pointer = {
      x: (clientX ?? rectangle.left + rectangle.width / 2) - rectangle.left - rectangle.width / 2,
      y: (clientY ?? rectangle.top + rectangle.height / 2) - rectangle.top - rectangle.height / 2
    };
    const nextPan = pointerCenteredPan(
      { x: panX, y: panY },
      pointer,
      zoom,
      boundedZoom,
      panBounds(
        { width: canvasWidth, height: canvasHeight },
        { width: stageWidth, height: stageHeight },
        boundedZoom
      )
    );
    zoom = boundedZoom;
    panX = nextPan.x;
    panY = nextPan.y;
  }
</script>

<section class="preview-pane" class:compact-hidden={compactHidden}>
  <div class="preview-header">
    <div>
      <strong>{selectedMedia?.name ?? 'Preview'}</strong>
      <span>{selectedMedia ? `${selectedMedia.width || '—'} × ${selectedMedia.height || '—'}${selectedMedia.kind === 'video' ? ' · Video' : ''}` : 'No media selected'}</span>
    </div>
    {#if resultPreview || videoComparison}
      <div class="preview-badges"><span>Original</span><span>Enhanced</span></div>
    {/if}
  </div>

  <div
    bind:this={canvasWell}
    class="canvas-well"
    class:panning={dragging}
    class:comparing={draggingComparison}
    role="application"
    aria-label="Media comparison canvas"
    on:wheel={onWheel}
    on:pointerdown={pointerDown}
    on:pointermove={pointerMove}
    on:pointerup={pointerUp}
    on:pointercancel={pointerUp}
  >
    {#if selectedMedia}
      {#if videoComparison}
        {#key videoComparisonKey}
          <VideoComparison
            originalSrc={videoComparison.original_url}
            enhancedSrc={videoComparison.enhanced_url}
            bind:compare
          />
        {/key}
      {:else if renderableSourcePreview}
      <div class="image-stage" style={`width:${stageWidth}px;height:${stageHeight}px;left:50%;top:50%;transform:translate(calc(-50% + ${panX}px), calc(-50% + ${panY}px)) scale(${zoom})`}>
        <img class="source-image" src={renderableSourcePreview} alt={`Preview of ${selectedMedia?.name ?? 'source media'}`} draggable="false" on:load={onPreviewLoad} on:error={onSourcePreviewError} />
        <canvas
          bind:this={progressiveCanvas}
          class="progressive-image"
          class:visible={progressiveVisible && !resultPreview}
          aria-label="Progressive tiled preview"
        ></canvas>
        {#if progressiveVisible && activeTileVisible && !resultPreview}
          <div
            class="active-tile"
            aria-hidden="true"
            style={`left:${activeTileX}%;top:${activeTileY}%;width:${activeTileWidth}%;height:${activeTileHeight}%`}
          ></div>
        {/if}
        {#if resultPreview}
          <img class="result-image" style={`clip-path: inset(0 ${100 - compare}% 0 0)`} src={resultPreview} alt="Enhanced result" draggable="false" />
          <div
            class="compare-line"
            role="slider"
            tabindex="0"
            aria-label="Before and after comparison"
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow={Math.round(compare)}
            aria-valuetext={`${Math.round(compare)}% enhanced`}
            style={`left: ${compare}%`}
            on:pointerdown|stopPropagation={comparisonPointerDown}
            on:pointermove|stopPropagation={comparisonPointerMove}
            on:pointerup|stopPropagation={comparisonPointerUp}
            on:pointercancel|stopPropagation={comparisonPointerUp}
            on:keydown={compareKeyDown}
          ><span aria-hidden="true">↔</span></div>
        {/if}
      </div>
      {#if selectedMedia?.kind === 'video'}<span class="labs-chip">VIDEO · LOCAL UPSCALING</span>{/if}
      {#if selectedMedia?.kind !== 'video'}<div class="zoom-hud" role="group" aria-label="Preview zoom" on:pointerdown|stopPropagation on:wheel|stopPropagation>
        <button aria-label="Zoom out" disabled={zoom <= minZoom} on:click={() => setZoom(zoom / 1.25)}>−</button>
        <span title={`Dynamic maximum ${Math.round(maxZoom * 100)}%`}>{Math.round(zoom * 100)}%</span>
        <button aria-label="Zoom in" disabled={zoom >= maxZoom} on:click={() => setZoom(zoom * 1.25)}>＋</button>
        <button class:active={Math.abs(zoom - 1) < 0.001} on:click={resetView}>Fit</button>
        <button class:active={Math.abs(zoom - actualPixelZoom) < 0.001} title="One source pixel per screen pixel" on:click={() => setZoom(actualPixelZoom)}>1:1</button>
      </div>{/if}
      {:else}
      <div class="canvas-empty preview-loading">
        <div class="canvas-icon" aria-hidden="true">
          <svg viewBox="0 0 64 52" focusable="false">
            <rect x="5" y="7" width="45" height="34" rx="5"></rect>
            <circle cx="38" cy="17" r="4"></circle>
            <path d="M10 35l11-10 8 7 6-5 10 8"></path>
            <circle class="video-disc" cx="49" cy="37" r="10"></circle>
            <path class="play-mark" d="M46 31.5v11l8-5.5z"></path>
          </svg>
        </div>
        <h2>{selectedMedia.probe_status === 'failed' ? 'Preview unavailable' : 'Preparing preview…'}</h2>
        <p>{selectedMedia.probe_status === 'failed' ? selectedMedia.error : `Loading ${selectedMedia.name} locally.`}</p>
        {#if selectedMedia.probe_status === 'failed'}<button class="button" on:click={() => { previewRetryMediaId = ''; void repairSelectedPreview(); }}>Try Again</button>{/if}
      </div>
      {/if}
      {#if videoComparisonError}<p class="video-comparison-load-error" role="alert">{videoComparisonError}</p>{/if}
    {:else}
      <div class="canvas-empty">
        <div class="canvas-icon" aria-hidden="true">
          <svg viewBox="0 0 64 52" focusable="false">
            <rect x="5" y="7" width="45" height="34" rx="5"></rect>
            <circle cx="38" cy="17" r="4"></circle>
            <path d="M10 35l11-10 8 7 6-5 10 8"></path>
            <circle class="video-disc" cx="49" cy="37" r="10"></circle>
            <path class="play-mark" d="M46 31.5v11l8-5.5z"></path>
          </svg>
        </div>
        <h2>Choose an image or video to enhance</h2>
        <p>Upscale photos, artwork and video, or remove noise<br />with private local AI models.</p>
        <button class="button primary" on:click={() => addFiles()}>Add Media</button>
      </div>
    {/if}
  </div>
</section>
