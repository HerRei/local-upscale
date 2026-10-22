<script lang="ts">
  import { onMount, tick } from 'svelte';
  import VideoComparison from './VideoComparison.svelte';
  import EmptyStage from './EmptyStage.svelte';
  import MediaIllustration from './MediaIllustration.svelte';
  import PreviewActivity from './PreviewActivity.svelte';
  import * as api from './lib/api';
  import { comparisonFromKey, comparisonFromPointer } from './lib/comparison';
  import {
    boundedPreviewSize,
    canvasTileRect,
    paintTileGrid,
    tilePercentages,
    type OutputTile,
  } from './lib/progressive-preview';
  import {
    clampPan,
    comparisonGeometry,
    fitSize,
    panBounds,
    pointerCenteredPan,
    zoomLimits,
  } from './lib/viewport';
  import type {
    MediaItem,
    VideoComparisonSources,
    VideoComparisonProgress,
    WorkerEnvelope,
  } from './lib/types';

  export let selectedMedia: MediaItem | undefined;
  export let resultPreview: string;
  export let comparisonError = '';
  export let completedVideoOutput: string;
  export let compactHidden = false;
  export let modelLabel = '';
  export let activityLabel = '';
  // Editing names its phase and reports an overall percentage; tiles do not exist.
  export let activityDetail = '';
  export let activityProgress = 0;
  export let editing = false;
  export let processing = false;
  export let activeTileSize = 0;
  export let addFiles: () => void = () => {};
  export let dropping = false;

  let videoRequestId = '';
  let videoPreparation = false;
  let videoPreparationStarted = 0;
  let videoPreparationElapsed = 0;
  let videoPreparationProgress: VideoComparisonProgress | null = null;
  let playbackFallbackUsed = false;
  let playbackListener: Promise<() => void> | undefined;
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
  // The divider keeps its place on screen and its size at every zoom; both
  // images move under it, so a deep zoom still shows before and after.
  $: comparison = comparisonGeometry(
    { width: canvasWidth, height: canvasHeight },
    { width: stageWidth, height: stageHeight },
    zoom,
    { x: panX, y: panY },
    compare,
  );
  // Holding M (or the Original button) shows the original across the view.
  let showingOriginal = false;
  $: canShowOriginal = Boolean(resultPreview || videoComparison);
  $: if (!canShowOriginal) showingOriginal = false;
  let minZoom = 1;
  let maxZoom = 8;
  let actualPixelZoom = 1;
  let viewedMediaId = '';
  let laidOutSourcePreview = '';
  let previewRetryMediaId = '';
  let progressiveVisible = false;
  let progressiveJobId = '';
  let progressiveFrame = -1;
  let progressiveStage = 0;
  let videoSourceFrame = -1;
  let videoSourcePreview = '';
  let progressiveOutputWidth = 0;
  let progressiveOutputHeight = 0;
  let progressiveGeneration = 0;
  let progressiveWork: Promise<void> = Promise.resolve();
  let progressivePendingCount = 0;
  let lastProgressivePaintAt = 0;
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
  $: displayedSourcePreview = videoSourcePreview || sourcePreview;
  $: waitingForVideoFrame =
    processing &&
    selectedMedia?.kind === 'video' &&
    progressiveFrame > 0 &&
    videoSourceFrame !== progressiveFrame;
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

  $: if (selectedMedia && selectedMedia.probe_status === 'ready' && !sourcePreview) {
    void repairSelectedPreview();
  }

  onMount(() => {
    const preparationClock = setInterval(() => {
      if (videoPreparation)
        videoPreparationElapsed = Math.floor((Date.now() - videoPreparationStarted) / 1000);
    }, 1000);
    const observer =
      typeof ResizeObserver === 'undefined'
        ? undefined
        : new ResizeObserver(() => updateViewport());
    if (canvasWell) observer?.observe(canvasWell);
    scheduleViewportReset();
    return () => {
      disposed = true;
      clearInterval(preparationClock);
      if (videoRequestId) void api.cancelVideoComparison(videoRequestId).catch(() => {});
      void playbackListener?.then((unlisten) => unlisten()).catch(() => {});
      observer?.disconnect();
      progressiveGeneration += 1;
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

  export function resetProgressivePreview(jobId = '', frame = -1): void {
    if (jobId !== progressiveJobId || frame !== videoSourceFrame) {
      videoSourceFrame = -1;
      videoSourcePreview = '';
    }
    progressiveFrame = frame;
    progressiveStage = 0;
    progressiveGeneration += 1;
    progressiveJobId = jobId;
    progressiveOutputWidth = 0;
    progressiveOutputHeight = 0;
    progressiveVisible = false;
    progressivePendingCount = 0;
    lastProgressivePaintAt = 0;
    activeTileVisible = false;
    progressiveWork = Promise.resolve();
    const context = progressiveCanvas?.getContext('2d');
    if (progressiveCanvas && context) {
      context.clearRect(0, 0, progressiveCanvas.width, progressiveCanvas.height);
    }
  }

  export function queueVideoSource(message: WorkerEnvelope, activeJobId: string): void {
    const data = message.data;
    const jobId = String(data.job_id ?? '');
    const frame = Number(data.frame_index ?? -1);
    if (
      !jobId ||
      jobId !== activeJobId ||
      frame < 0 ||
      frame < progressiveFrame ||
      typeof data.jpeg_base64 !== 'string' ||
      !data.jpeg_base64
    )
      return;
    if (jobId !== progressiveJobId || frame > progressiveFrame)
      resetProgressivePreview(jobId, frame);
    videoSourceFrame = frame;
    videoSourcePreview = `data:image/jpeg;base64,${data.jpeg_base64}`;
  }

  export function queueProgressiveTile(message: WorkerEnvelope, activeJobId: string): void {
    const data = message.data;
    const jobId = String(data.job_id ?? '');
    if (!jobId || jobId !== activeJobId) return;
    const phase = String(data.phase ?? '');
    const frame = Number(data.frame_index ?? -1);
    const stage = numeric(data.stage_index);
    if (jobId === progressiveJobId && stage < progressiveStage) return;
    if (stage > progressiveStage) {
      resetProgressivePreview(jobId, frame);
      progressiveStage = stage;
    }
    if (frame >= 0 && frame < progressiveFrame) return;
    if (frame > progressiveFrame || phase === 'reset') {
      resetProgressivePreview(jobId, frame);
      progressiveStage = stage;
    }
    if (!progressiveJobId) progressiveJobId = jobId;
    if (jobId !== progressiveJobId) return;

    const now = typeof performance === 'undefined' ? Date.now() : performance.now();
    if (phase === 'started') {
      // Geometry is cheap; never sample it together with JPEG decoding. Fast
      // restoration models can finish several tiles within one display frame.
      // Keep the newest model position even while older JPEGs are decoding.
      const tile = tileFromData(data);
      const percentage = tilePercentages(
        tile,
        boundedPreviewSize({ width: tile.image_width, height: tile.image_height }),
      );
      if (percentage) {
        activeTileX = percentage.x;
        activeTileY = percentage.y;
        activeTileWidth = percentage.width;
        activeTileHeight = percentage.height;
        activeTileVisible = true;
      }
      if (progressiveVisible) return;
    }
    if (phase === 'completed') {
      // Completion metadata has no pixels. It must not consume the display
      // throttle before the asynchronous JPEG arrives a few milliseconds later.
      if (!data.jpeg_base64) {
        const percentage = tilePercentages(tileFromData(data), progressiveCanvas);
        if (
          percentage &&
          percentage.x === activeTileX &&
          percentage.y === activeTileY &&
          percentage.width === activeTileWidth &&
          percentage.height === activeTileHeight
        ) {
          activeTileVisible = false;
        }
        return;
      }
      // Decoding every JPEG can monopolize WKWebView when a lightweight model
      // finishes many tiny tiles per second. A sampled live mosaic stays useful
      // while preserving input responsiveness; completion always loads the
      // authoritative full output image.
      if (
        (lastProgressivePaintAt > 0 && now - lastProgressivePaintAt < 75) ||
        progressivePendingCount >= 3
      ) {
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
      image_height: numeric(data.image_height),
      grid_width:
        ((numeric(data.active_tile_size) || activeTileSize) * numeric(data.image_width)) /
        Math.max(1, selectedMedia?.width ?? 1),
      grid_height:
        ((numeric(data.active_tile_size) || activeTileSize) * numeric(data.image_height)) /
        Math.max(1, selectedMedia?.height ?? 1),
    };
  }

  async function paintProgressiveTile(
    data: WorkerEnvelope['data'],
    jobId: string,
    generation: number,
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
      return;
    }
    const ready = await ensureProgressiveCanvas(
      tile,
      jobId,
      generation,
      Boolean(data.processing_stage),
    );
    if (!ready || generation !== progressiveGeneration || jobId !== progressiveJobId) return;

    const percentage = tilePercentages(tile, progressiveCanvas);
    // Started geometry was applied synchronously. An older queued event must
    // not move the outline backwards when its canvas initialization finishes.
    if (phase === 'started') return;

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
      context.drawImage(image, destination.x, destination.y, destination.width, destination.height);
    }
    // JPEG encoding is asynchronous: a previous tile can arrive after the
    // next tile started. Keep that newer model tile's outline visible.
    if (
      percentage &&
      percentage.x === activeTileX &&
      percentage.y === activeTileY &&
      percentage.width === activeTileWidth &&
      percentage.height === activeTileHeight
    ) {
      activeTileVisible = false;
    }
  }

  async function ensureProgressiveCanvas(
    tile: OutputTile,
    jobId: string,
    generation: number,
    overlappingRegions = false,
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
    const context = canvas.getContext('2d');
    if (!context) return false;
    if (selectedMedia?.kind === 'video') {
      // The current decoded frame stays in its own image layer. A late source
      // JPEG must never erase completed model pixels in the canvas above it.
      context.clearRect(0, 0, size.width, size.height);
      // VAE regions overlap; a regular HAT grid would misrepresent their bounds.
      if (!overlappingRegions) paintTileGrid(context, tile, size, true);
      progressiveOutputWidth = tile.image_width;
      progressiveOutputHeight = tile.image_height;
      progressiveVisible = true;
      return true;
    }
    context.fillStyle = '#171717';
    context.fillRect(0, 0, size.width, size.height);
    try {
      const source = await loadHtmlImage(sourcePreview);
      if (generation !== progressiveGeneration || jobId !== progressiveJobId) return false;
      if (source) context.drawImage(source, 0, 0, size.width, size.height);
    } catch {
      // The bounded mosaic can still show completed tiles while the source
      // preview is being repaired independently.
    }
    // Image restoration is 1×, while upscalers can be 2×/4×. Paint the
    // pending grid from the worker's actual output bounds for both tasks.
    if (!overlappingRegions) paintTileGrid(context, tile, size, true);
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
        y: panStartY + event.clientY - dragStartY,
      },
      currentPanBounds(),
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
    if (!canvasWell || !resultPreview || showingOriginal) return false;
    const rectangle = canvasWell.getBoundingClientRect();
    const y = event.clientY - rectangle.top;
    return (
      y >= comparison.top &&
      y <= comparison.bottom &&
      Math.abs(event.clientX - rectangle.left - comparison.x) <= 32
    );
  }

  /** A new divider position, held on the visible part of the image. */
  function placedComparison(percent: number): number {
    return comparisonGeometry(
      { width: canvasWidth, height: canvasHeight },
      { width: stageWidth, height: stageHeight },
      zoom,
      { x: panX, y: panY },
      percent,
    ).percent;
  }

  function updateComparisonFromPointer(clientX: number): void {
    if (!canvasWell) return;
    const rectangle = canvasWell.getBoundingClientRect();
    compare = placedComparison(comparisonFromPointer(clientX, rectangle.left, rectangle.width));
  }

  function compareKeyDown(event: KeyboardEvent): void {
    // Keys step through the visible image, the range the slider reports.
    const next = comparisonFromKey(100 - comparison.enhancedPercent, event.key);
    if (next === null) return;
    const x = comparison.left + ((comparison.right - comparison.left) * next) / 100;
    compare = (x / canvasWidth) * 100;
    event.preventDefault();
  }

  function holdsOriginalKey(event: KeyboardEvent): boolean {
    return event.key.toLowerCase() === 'm' && !event.metaKey && !event.ctrlKey && !event.altKey;
  }

  function windowKeyDown(event: KeyboardEvent): void {
    if (!canShowOriginal || !holdsOriginalKey(event)) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('input, textarea, select, [contenteditable], [aria-modal="true"]')) return;
    showingOriginal = true;
    event.preventDefault();
  }

  function windowKeyUp(event: KeyboardEvent): void {
    if (event.key.toLowerCase() === 'm') showingOriginal = false;
  }

  function holdOriginal(event: PointerEvent): void {
    if (event.button !== 0) return;
    showingOriginal = true;
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
  }

  function releaseOriginal(): void {
    showingOriginal = false;
  }

  function originalButtonKey(event: KeyboardEvent, held: boolean): void {
    if (event.key !== ' ' && event.key !== 'Enter') return;
    showingOriginal = held;
    event.preventDefault();
  }

  async function loadVideoComparison(key: string, forceCompatible = false): Promise<void> {
    const oldRequest = videoRequestId;
    videoRequestId = '';
    if (oldRequest && api.isTauri()) void api.cancelVideoComparison(oldRequest).catch(() => {});
    videoComparisonKey = key;
    videoComparison = null;
    videoComparisonError = '';
    videoPreparationProgress = null;
    videoPreparation = false;
    if (!forceCompatible) playbackFallbackUsed = false;
    if (!key || !api.isTauri() || !selectedMedia) return;
    const mediaId = selectedMedia.id;
    const requestId = crypto.randomUUID();
    videoRequestId = requestId;
    videoPreparationStarted = Date.now();
    videoPreparationElapsed = 0;
    videoPreparation = true;
    try {
      playbackListener ??= api.listenVideoComparisonProgress((data) => {
        if (
          !disposed &&
          videoPreparation &&
          data.request_id === videoRequestId &&
          data.media_id === selectedMedia?.id
        ) {
          videoPreparationProgress = data;
        }
      });
      await playbackListener;
      if (disposed || requestId !== videoRequestId) return;
      const sources = await api.prepareVideoComparison(mediaId, requestId, forceCompatible);
      if (disposed || requestId !== videoRequestId || mediaId !== selectedMedia?.id) return;
      videoComparison = sources;
    } catch (error) {
      if (disposed || requestId !== videoRequestId) return;
      videoComparisonError = `Completed video comparison is unavailable: ${String(error)}`;
    } finally {
      if (requestId === videoRequestId) videoPreparation = false;
    }
  }

  function compatiblePlayback(): void {
    if (playbackFallbackUsed || !videoComparisonKey || !api.isTauri()) return;
    playbackFallbackUsed = true;
    void loadVideoComparison(videoComparisonKey, true);
  }

  function cancelPlaybackPreparation(): void {
    const request = videoRequestId;
    videoRequestId = '';
    videoPreparation = false;
    videoComparisonError = 'Playback conversion cancelled. Your saved export is still available.';
    if (request) void api.cancelVideoComparison(request).catch(() => {});
  }

  /** New media or a new result: fit it and centre the divider. */
  export function resetView(): void {
    compare = 50;
    fitView();
  }

  /** Fit keeps the divider where it was put. */
  export function fitView(): void {
    zoom = 1;
    panX = 0;
    panY = 0;
    updateViewport(true);
  }

  function onPreviewLoad(event: Event): void {
    if (videoSourcePreview && selectedMedia?.kind === 'video') return;
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
      { width: sourceWidth, height: sourceHeight },
    );
    stageWidth = fitted.width;
    stageHeight = fitted.height;
    const limits = zoomLimits({ width: sourceWidth, height: sourceHeight }, fitted);
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
      zoom,
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
      y: (clientY ?? rectangle.top + rectangle.height / 2) - rectangle.top - rectangle.height / 2,
    };
    const nextPan = pointerCenteredPan(
      { x: panX, y: panY },
      pointer,
      zoom,
      boundedZoom,
      panBounds(
        { width: canvasWidth, height: canvasHeight },
        { width: stageWidth, height: stageHeight },
        boundedZoom,
      ),
    );
    zoom = boundedZoom;
    panX = nextPan.x;
    panY = nextPan.y;
  }
</script>

<svelte:window on:keydown={windowKeyDown} on:keyup={windowKeyUp} on:blur={releaseOriginal} />

<section class="preview-pane" class:compact-hidden={compactHidden}>
  <div class="preview-header" data-tauri-drag-region>
    <div data-tauri-drag-region>
      <strong data-tauri-drag-region>{selectedMedia?.name ?? 'Preview'}</strong>
      <span
        >{selectedMedia
          ? `${selectedMedia.width || '—'} × ${selectedMedia.height || '—'}${selectedMedia.kind === 'video' ? ' · Video' : ''}`
          : 'No media selected'}</span
      >
    </div>
    {#if resultPreview || videoComparison}
      <div class="preview-badges"><span>Original</span><span>Enhanced</span></div>
    {/if}
  </div>

  <div
    bind:this={canvasWell}
    class="canvas-well"
    class:rendering={progressiveVisible && !resultPreview}
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
      {#if comparisonError}<p class="comparison-error" role="alert">
          Comparison unavailable: {comparisonError}
        </p>{/if}
      {#if processing}
        {#if editing}<div class="edit-sweep" aria-hidden="true"></div>{/if}
        <div class="model-activity" class:with-progress={editing} role="status">
          <i></i><span
            >{modelLabel} · {activityLabel
              ? `${activityLabel}${progressiveFrame >= 0 ? ` · Frame ${progressiveFrame + 1}` : ''}`
              : progressiveFrame >= 0
                ? `Frame ${progressiveFrame + 1} · live tiles`
                : 'Preparing model output…'}{selectedMedia.hdr_format
              ? ' · SDR display preview'
              : ''}</span
          >
          {#if editing}<small
              >{activityDetail || 'Starting…'} · {Math.round(activityProgress)}%</small
            ><b class="activity-track"
              ><em style={`width:${Math.max(2, Math.min(100, activityProgress))}%`}></em></b
            >{/if}
        </div>
      {/if}
      {#if videoComparison}
        {#key videoComparisonKey}
          <VideoComparison
            originalSrc={videoComparison.original_url}
            enhancedSrc={videoComparison.enhanced_url}
            bind:compare
            bind:showOriginal={showingOriginal}
            on:compatibilityneeded={compatiblePlayback}
          />
        {/key}
      {:else if renderableSourcePreview}
        <div
          class="image-stage"
          style={`width:${stageWidth}px;height:${stageHeight}px;left:50%;top:50%;transform:translate(calc(-50% + ${panX}px), calc(-50% + ${panY}px)) scale(${zoom})`}
        >
          <img
            class="source-image"
            class:waiting-frame={waitingForVideoFrame}
            src={displayedSourcePreview}
            alt={videoSourcePreview
              ? `Source frame ${videoSourceFrame + 1} of ${selectedMedia.name}`
              : `Preview of ${selectedMedia.name}`}
            draggable="false"
            on:load={onPreviewLoad}
            on:error={onSourcePreviewError}
          />
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
            <img
              class="result-image"
              style={`clip-path: inset(0 0 0 ${showingOriginal ? 100 : comparison.imagePercent}%)`}
              src={resultPreview}
              alt="Enhanced result"
              draggable="false"
            />
          {/if}
        </div>
        {#if resultPreview}
          <div
            class="compare-line"
            class:held={showingOriginal}
            role="slider"
            tabindex="0"
            aria-label="Before and after comparison"
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow={Math.round(100 - comparison.enhancedPercent)}
            aria-valuetext={`${Math.round(comparison.enhancedPercent)}% enhanced`}
            style={`left:${comparison.x}px;top:${comparison.top}px;height:${comparison.bottom - comparison.top}px;--handle-top:${comparison.handleY - comparison.top}px`}
            on:pointerdown|stopPropagation={comparisonPointerDown}
            on:pointermove|stopPropagation={comparisonPointerMove}
            on:pointerup|stopPropagation={comparisonPointerUp}
            on:pointercancel|stopPropagation={comparisonPointerUp}
            on:keydown={compareKeyDown}
          >
            <span aria-hidden="true">↔</span>
          </div>
          {#if showingOriginal}<span class="original-chip" role="status">Original</span>{/if}
        {/if}
        {#if selectedMedia?.kind === 'video'}<span class="labs-chip">Video · local upscaling</span
          >{/if}
        {#if selectedMedia?.kind !== 'video'}<div
            class="zoom-hud"
            role="group"
            aria-label="Preview zoom"
            on:pointerdown|stopPropagation
            on:wheel|stopPropagation
          >
            <button
              aria-label="Zoom out"
              disabled={zoom <= minZoom}
              on:click={() => setZoom(zoom / 1.25)}>−</button
            >
            <span title={`Dynamic maximum ${Math.round(maxZoom * 100)}%`}
              >{Math.round(zoom * 100)}%</span
            >
            <button
              aria-label="Zoom in"
              disabled={zoom >= maxZoom}
              on:click={() => setZoom(zoom * 1.25)}>＋</button
            >
            <button class:active={Math.abs(zoom - 1) < 0.001} on:click={fitView}>Fit</button>
            <button
              class:active={Math.abs(zoom - actualPixelZoom) < 0.001}
              title="One source pixel per screen pixel"
              on:click={() => setZoom(actualPixelZoom)}>1:1</button
            >
            {#if resultPreview}<i class="hud-divider" aria-hidden="true"></i><button
                class="hold-original"
                class:active={showingOriginal}
                aria-pressed={showingOriginal}
                title="Hold to show the original (M)"
                on:pointerdown={holdOriginal}
                on:pointerup={releaseOriginal}
                on:pointercancel={releaseOriginal}
                on:lostpointercapture={releaseOriginal}
                on:keydown={(event) => originalButtonKey(event, true)}
                on:keyup={(event) => originalButtonKey(event, false)}
                on:blur={releaseOriginal}>Original</button
              >{/if}
          </div>{/if}
      {:else}
        {#if selectedMedia.probe_status === 'pending'}
          {#key selectedMedia.id}<PreviewActivity media={selectedMedia} />{/key}
        {:else}
          <div class="canvas-empty preview-error">
            <MediaIllustration still />
            <h2>Preview unavailable</h2>
            <p role="alert">
              {selectedMedia.error ||
                'No preview could be created. Try again or choose another file.'}
            </p>
            <button
              class="button"
              on:click={() => {
                previewRetryMediaId = '';
                void repairSelectedPreview();
              }}>Try Again</button
            >
          </div>
        {/if}
      {/if}
      {#if videoPreparation}
        <div class="playback-preparation" role="status" on:pointerdown|stopPropagation>
          <span class="playback-spinner" aria-hidden="true"></span>
          <span
            >{videoPreparationProgress?.stage ?? 'Preparing video comparison…'} · {videoPreparationElapsed}s
            {#if videoPreparationProgress}
              · {videoPreparationProgress.frame}{videoPreparationProgress.total > 0
                ? ` / ~${videoPreparationProgress.total} frames`
                : ' frames'}
            {/if}
          </span>
          <button class="button" on:click={cancelPlaybackPreparation}>Cancel preview</button>
        </div>
      {/if}
      {#if videoComparison?.playback_note}
        <p class="playback-note">{videoComparison.playback_note}</p>
      {/if}
      {#if videoComparisonError}<p class="video-comparison-load-error" role="alert">
          {videoComparisonError}
          <button
            class="button"
            on:pointerdown|stopPropagation
            on:click={() => void loadVideoComparison(videoComparisonKey)}>Retry comparison</button
          >
        </p>{/if}
    {:else}
      <EmptyStage {addFiles} {dropping} />
    {/if}
  </div>
</section>

<style>
  .playback-preparation {
    position: absolute;
    left: 16px;
    right: 16px;
    bottom: 16px;
    z-index: 9;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px;
    border-radius: var(--radius);
    background: #1e1e1eed;
    color: #d1d1d1;
  }
  .playback-preparation > span:nth-child(2) {
    flex: 1;
  }
  .playback-spinner {
    width: 16px;
    height: 16px;
    flex-shrink: 0;
    border: 2px solid #bfbfbf40;
    border-top-color: #cccccc;
    border-radius: 50%;
    animation: playback-spin 1s linear infinite;
  }
  .playback-note {
    position: absolute;
    top: 48px;
    left: 12px;
    right: 12px;
    z-index: 8;
    pointer-events: none;
    background: #1e1e1eed;
    padding: 6px 10px;
    font-size: 12px;
  }
  @keyframes playback-spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .playback-spinner {
      animation: none;
    }
  }

  .comparison-error {
    position: absolute;
    z-index: 5;
    left: 16px;
    right: 16px;
    bottom: 16px;
    padding: 12px;
    border-radius: var(--radius);
    background: #351918;
    color: #ffc6bd;
  }
  .model-activity {
    position: absolute;
    top: 12px;
    right: 12px;
    z-index: 8;
    display: flex;
    align-items: center;
    gap: 8px;
    max-width: calc(100% - 24px);
    padding: 8px 10px;
    background: #1e1e1eed;
    color: #d1d1d1;
    border: 1px solid #6b6b6b;
    border-radius: var(--radius);
    font-size: 11px;
    pointer-events: none;
  }
  .model-activity i {
    flex: 0 0 auto;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 2px solid #bfbfbf40;
    border-top-color: #cccccc;
    animation: model-spin 1s linear infinite;
  }
  .model-activity.with-progress {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    row-gap: 6px;
    min-width: 260px;
  }
  .model-activity small {
    grid-column: 2;
    color: #b2b2b2;
    font-size: 11px;
  }
  .activity-track {
    grid-column: 1 / -1;
    position: relative;
    display: block;
    height: 4px;
    overflow: hidden;
    border-radius: 2px;
    background: #353535;
  }
  .activity-track em {
    position: relative;
    display: block;
    height: 100%;
    overflow: hidden;
    border-radius: 2px;
    background: var(--accent-hi);
    transition: width 0.5s ease;
  }
  .activity-track em::after,
  .edit-sweep {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(
      100deg,
      transparent 35%,
      rgba(226, 226, 226, 0.55) 50%,
      transparent 65%
    );
    background-size: 250% 100%;
    animation: sweep 1.6s linear infinite;
  }
  .edit-sweep {
    z-index: 7;
    pointer-events: none;
    background: linear-gradient(
      100deg,
      transparent 40%,
      rgba(204, 204, 204, 0.07) 50%,
      transparent 60%
    );
    background-size: 250% 100%;
    animation-duration: 3.2s;
  }
  .active-tile {
    animation: tile-pulse 1.2s ease-in-out infinite;
  }
  @keyframes sweep {
    from {
      background-position: 125% 0;
    }
    to {
      background-position: -125% 0;
    }
  }
  .source-image.waiting-frame {
    visibility: hidden;
  }
  @keyframes model-spin {
    to {
      transform: rotate(360deg);
    }
  }
  @keyframes tile-pulse {
    50% {
      border-color: #e0e0e0;
      background: #aaaaaa25;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .model-activity i,
    .active-tile,
    .activity-track em::after,
    .edit-sweep {
      animation: none;
    }
  }
</style>
