<script lang="ts">
  /**
   * The Compare mode of the main pane: the picked items as a slider (slot 1 against 2) or
   * as boxes (up to four), with one zoom and pan for every box. Escape or Done leaves.
   */
  import { onDestroy, onMount, tick } from 'svelte';
  import { visibleRegion, type Region } from './lib/compareRegions';
  import * as api from './lib/api';
  import type { CompareVideoSlot } from './lib/types';
  import { SingleVideoCompareSync } from './lib/compare-video-sync';
  import { croppedBoxView, croppedSlotDraw, slotFrameSize } from './lib/compareVideoCrop';
  import VideoTransport from './VideoTransport.svelte';
  import BackgroundSwitch from './BackgroundSwitch.svelte';
  import type { ViewerBackground } from './lib/viewerBackground';
  import FileName from './FileName.svelte';
  import HelpTip from './HelpTip.svelte';
  import UnfinishedTiles from './UnfinishedTiles.svelte';
  import { comparisonFromKey, comparisonFromPointer } from './lib/comparison';
  import { boxColumns, detailLines, sliderBlock, type CompareItem } from './lib/compareOutputs';
  import { formatMediaTime } from './lib/video-sync';
  import {
    clampPan,
    comparisonGeometry,
    fitSize,
    panBounds,
    pointerCenteredPan,
  } from './lib/viewport';

  export let items: CompareItem[];
  export let generation = 0;
  export let engineBusy = false;
  export let prepareVideo: typeof api.prepareCompareVideo = (...args) =>
    api.prepareCompareVideo(...args);
  export let cancelVideo = api.cancelVideoComparison;

  export let requestRegions: (
    generation: number,
    regions: (Region | null)[],
  ) => Promise<void> = async () => {};
  export let clearRegions: () => Promise<void> = async () => {};
  export let layout: 'slider' | 'boxes' = 'slider';
  export let setLayout: (layout: 'slider' | 'boxes') => void;
  export let swapItems: (first: number, second: number) => void;
  export let removeItem: (key: string) => void;
  export let close: () => void;
  /** As a tab of the main area there is no Done: the tabs are the way out. */
  export let asTab = false;
  export let compactHidden = false;
  export let viewerBackground: ViewerBackground = 'checker';
  export let setViewerBackground: (value: ViewerBackground) => void = () => {};

  const BAR = 26;
  let well: HTMLDivElement | undefined;
  let canvasWidth = 1;
  let canvasHeight = 1;
  let zoom = 1;
  let panX = 0;
  let panY = 0;
  let minZoom = 1;
  let maxZoom = 8;
  let compare = 50;
  let dragging = false;
  let draggingLine = false;
  let dragStartX = 0;
  let dragStartY = 0;
  let panStartX = 0;
  let panStartY = 0;
  let hovering = false;
  let regionTimer: ReturnType<typeof setTimeout> | undefined;
  let regionKey = '';
  let regionQueue = Promise.resolve();
  let destroyed = false;

  type VideoState = {
    status: 'idle' | 'loading' | 'ready' | 'error';
    error: string;
    note: string;
    url: string;
    width: number;
    height: number;
    duration: number;
    slots: CompareVideoSlot[];
    loaded: boolean;
  };
  const emptyVideoState = (): VideoState => ({
    status: 'idle',
    error: '',
    note: '',
    url: '',
    width: 0,
    height: 0,
    duration: 0,
    slots: [],
    loaded: false,
  });
  let videoState: VideoState = emptyVideoState();
  let combinedVideo: HTMLVideoElement | undefined;
  let audioElement: HTMLAudioElement | undefined;
  let stagingCanvas: HTMLCanvasElement | undefined;
  const canvases = new Map<number, HTMLCanvasElement>();
  let sync: SingleVideoCompareSync | undefined;
  let playing = false;
  let currentTime = 0;
  let desiredTime = 0;
  let wantedPlayback = false;
  let duration = 0;
  let playbackRate = 1;
  // Frames of the clock's span and the one shown; an unfinished side can end the span early.
  let frames = 0;
  let frame = 0;
  let limited = false;
  /**
   * The side whose sound plays: a slot's key, or 'off'. Slot 1 at first; from then on the
   * choice belongs to that result and stays with it when the slots are swapped.
   */
  let audioChoice = '';
  let preparationKey = '';
  let preparationRevision = 0;
  let requestId = '';
  let frameId: number | undefined;
  let frameVideo: HTMLVideoElement | undefined;
  let animationId: number | undefined;
  let preparationQueue = Promise.resolve();
  let fallbackUsed = false;

  function updateTransport(): void {
    if (sync && sync.duration > 0) {
      wantedPlayback = sync.requestedPlayback;
      desiredTime = sync.time;
    }
    playing = sync?.requestedPlayback ?? wantedPlayback;
    currentTime = sync?.time ?? desiredTime;
    duration = sync?.duration ?? 0;
    frames = sync?.frames ?? 0;
    frame = sync?.frame ?? 0;
    limited = sync?.limited ?? false;
  }
  function stopClock(): void {
    if (frameId !== undefined) frameVideo?.cancelVideoFrameCallback(frameId);
    if (animationId !== undefined) cancelAnimationFrame(animationId);
    frameId = animationId = undefined;
    frameVideo = undefined;
  }
  function paintCanvas(
    index: number,
    canvas: HTMLCanvasElement,
    source: CanvasImageSource,
    sourceFrame: { width: number; height: number },
  ): void {
    const slot = videoState.slots[index];
    if (!slot || slot.width <= 0 || slot.height <= 0) return;
    const draw = croppedSlotDraw(slot, sourceFrame);
    if (canvas.width !== draw.dw) canvas.width = draw.dw;
    if (canvas.height !== draw.dh) canvas.height = draw.dh;
    canvas
      .getContext('2d')
      ?.drawImage(source, draw.sx, draw.sy, draw.sw, draw.sh, draw.dx, draw.dy, draw.dw, draw.dh);
  }
  function snapshotCombinedFrame(): {
    source: CanvasImageSource;
    frame: { width: number; height: number };
  } | null {
    if (!combinedVideo || combinedVideo.readyState < 2 || !videoState.loaded) return null;
    const frame = {
      width: combinedVideo.videoWidth || videoState.width || 1,
      height: combinedVideo.videoHeight || videoState.height || 1,
    };
    // Copy the live video element once into an offscreen staging canvas so every side
    // crops from the exact same decoded frame even when WebKit updates AVPlayer async.
    if (!stagingCanvas && typeof document !== 'undefined') {
      stagingCanvas = document.createElement('canvas');
    }
    const stagingCtx = stagingCanvas?.getContext('2d');
    if (stagingCanvas && stagingCtx) {
      if (stagingCanvas.width !== frame.width) stagingCanvas.width = frame.width;
      if (stagingCanvas.height !== frame.height) stagingCanvas.height = frame.height;
      stagingCtx.drawImage(combinedVideo, 0, 0, frame.width, frame.height);
      return { source: stagingCanvas, frame };
    }
    return { source: combinedVideo, frame };
  }
  function paintAll(): void {
    const snap = snapshotCombinedFrame();
    if (!snap) return;
    for (const [index, canvas] of canvases) paintCanvas(index, canvas, snap.source, snap.frame);
  }
  function clock(): void {
    stopClock();
    if (!sync?.playing || destroyed) return;
    const master = combinedVideo && !combinedVideo.paused ? combinedVideo : undefined;
    const update = () => {
      stopClock();
      sync?.tick();
      paintAll();
      updateTransport();
      clock();
    };
    if (master?.requestVideoFrameCallback) {
      frameVideo = master;
      frameId = master.requestVideoFrameCallback(update);
    }
    animationId = requestAnimationFrame(update);
  }
  function applyAudioSelection(): void {
    if (!sync) return;
    const slot = audioSlotIndex >= 0 ? videoState.slots[audioSlotIndex] : undefined;
    if (!audioKey || !slot?.audio_url || !audioElement) {
      sync.setAudio(null);
      return;
    }
    sync.setAudio(audioElement, slot.duration || videoState.duration);
  }
  function rebuild(): void {
    const time = sync && sync.duration > 0 ? sync.time : desiredTime;
    const resume = wantedPlayback;
    sync?.pause();
    stopClock();
    if (!combinedVideo || !videoState.loaded) {
      sync = undefined;
      updateTransport();
      return;
    }
    sync = new SingleVideoCompareSync(
      combinedVideo,
      items.map((item, index) => {
        const slot = videoState.slots[index];
        return {
          fps: item.fps || slot?.fps || 0,
          duration: slot?.duration || videoState.duration || 0,
          kept: item.unfinished && {
            seconds: item.unfinished.seconds,
            frames: item.unfinished.frames,
          },
        };
      }),
    );
    sync.rate(playbackRate);
    sync.seek(time);
    applyAudioSelection();
    paintAll();
    updateTransport();
    if (resume && !destroyed) void start();
  }
  async function start(): Promise<void> {
    wantedPlayback = true;
    try {
      await sync?.play();
    } catch (error) {
      console.warn('Compare playback could not start', error);
    }
    paintAll();
    updateTransport();
    clock();
  }
  function togglePlayback(): void {
    if (playing) {
      wantedPlayback = false;
      sync?.pause();
      stopClock();
      paintAll();
      updateTransport();
    } else void start();
  }
  function seek(seconds: number): void {
    sync?.scrub(seconds);
    paintAll();
    updateTransport();
    clock();
  }
  function seekFrame(index: number): void {
    sync?.seekFrame(index);
    paintAll();
    updateTransport();
    clock();
  }
  function step(direction: -1 | 1): void {
    wantedPlayback = false;
    sync?.step(direction);
    stopClock();
    paintAll();
    updateTransport();
  }
  function chooseAudio(choice: string): void {
    audioChoice = choice;
    void tick().then(() => applyAudioSelection());
  }
  function nextRate(): void {
    const rates = [0.5, 1, 1.5, 2];
    playbackRate = rates[(rates.indexOf(playbackRate) + 1) % rates.length];
    sync?.rate(playbackRate);
    clock();
  }
  function release(media: HTMLMediaElement): void {
    media.pause();
    media.removeAttribute('src');
    media.load();
  }
  function videoCanvas(canvas: HTMLCanvasElement, index: number) {
    let slotIndex = index;
    canvases.set(slotIndex, canvas);
    const snap = snapshotCombinedFrame();
    if (snap) paintCanvas(slotIndex, canvas, snap.source, snap.frame);
    return {
      update(nextIndex: number) {
        if (nextIndex !== slotIndex) {
          if (canvases.get(slotIndex) === canvas) canvases.delete(slotIndex);
          slotIndex = nextIndex;
          canvases.set(slotIndex, canvas);
        }
        const nextSnap = snapshotCombinedFrame();
        if (nextSnap) paintCanvas(slotIndex, canvas, nextSnap.source, nextSnap.frame);
      },
      destroy() {
        if (canvases.get(slotIndex) === canvas) canvases.delete(slotIndex);
      },
    };
  }
  function combinedPlayer(video: HTMLVideoElement) {
    const stalled = () => {
      if (!videoState.loaded) return;
      sync?.stall(video);
      updateTransport();
      clock();
    };
    const ready = () => {
      if (!videoState.loaded) return;
      paintAll();
      void sync
        ?.ready(video)
        .then(() => {
          updateTransport();
          clock();
        })
        .catch(() => {});
    };
    const loaded = () => {
      if (videoState.status !== 'ready') return;
      videoState = { ...videoState, loaded: true };
      rebuild();
    };
    const ended = () => {
      sync?.finish();
      paintAll();
      updateTransport();
      stopClock();
    };
    video.addEventListener('waiting', stalled);
    video.addEventListener('canplay', ready);
    video.addEventListener('seeked', ready);
    video.addEventListener('loadeddata', loaded);
    video.addEventListener('ended', ended);
    return {
      destroy() {
        video.removeEventListener('waiting', stalled);
        video.removeEventListener('canplay', ready);
        video.removeEventListener('seeked', ready);
        video.removeEventListener('loadeddata', loaded);
        video.removeEventListener('ended', ended);
        release(video);
      },
    };
  }
  function audioPlayer(audio: HTMLAudioElement) {
    const ready = () => {
      if (!videoState.loaded) return;
      applyAudioSelection();
    };
    audio.addEventListener('loadeddata', ready);
    audio.addEventListener('canplay', ready);
    return {
      destroy() {
        audio.removeEventListener('loadeddata', ready);
        audio.removeEventListener('canplay', ready);
        release(audio);
      },
    };
  }
  async function prepareCompare(revision: number, force = fallbackUsed): Promise<void> {
    if (destroyed || revision !== preparationRevision) return;
    const id = crypto.randomUUID();
    requestId = id;
    try {
      const descriptor = await prepareVideo(generation, id, force, engineBusy);
      if (destroyed || revision !== preparationRevision) return;
      videoState = {
        status: 'ready',
        error: '',
        note: descriptor.playback_note || '',
        url: descriptor.video_url,
        width: descriptor.width,
        height: descriptor.height,
        duration: descriptor.duration,
        slots: descriptor.slots,
        loaded: false,
      };
    } catch (error) {
      if (destroyed || revision !== preparationRevision) return;
      videoState = {
        ...emptyVideoState(),
        status: 'error',
        error: String(error),
      };
    } finally {
      if (requestId === id) requestId = '';
    }
  }
  function prepareVideos(key: string, entries: CompareItem[]): void {
    if (key === preparationKey) return;
    preparationKey = key;
    const revision = ++preparationRevision;
    if (requestId) void cancelVideo(requestId).catch(() => {});
    sync?.pause();
    stopClock();
    desiredTime = 0;
    wantedPlayback = false;
    fallbackUsed = false;
    if (combinedVideo) release(combinedVideo);
    if (audioElement) release(audioElement);
    sync = undefined;
    const readyCount = entries.filter((item) => item.status === 'ready').length;
    videoState =
      entries.length >= 2 && readyCount > 0
        ? { ...emptyVideoState(), status: 'loading' }
        : emptyVideoState();
    updateTransport();
    if (entries.length < 2 || readyCount === 0) return;
    preparationQueue = preparationQueue.catch(() => {}).then(() => prepareCompare(revision));
  }
  function videoError(): void {
    if (destroyed || !videoState.url) return;
    const code = combinedVideo?.error?.code;
    if (combinedVideo) release(combinedVideo);
    if (audioElement) release(audioElement);
    const compatible = !fallbackUsed && (code === 3 || code === 4);
    videoState = {
      ...emptyVideoState(),
      status: compatible ? 'loading' : 'error',
      error: 'Preview unavailable',
    };
    rebuild();
    if (compatible) {
      fallbackUsed = true;
      const revision = preparationRevision;
      preparationQueue = preparationQueue
        .catch(() => {})
        .then(() => prepareCompare(revision, true));
    }
  }
  function slotOverlayMessage(
    item: CompareItem,
    index: number,
    current: VideoState,
  ): string | null {
    if (item.status === 'loading') return 'Loading…';
    if (item.status === 'missing') return 'File missing';
    if (item.status !== 'ready') return item.error || 'Preview unavailable';
    if (current.status === 'error') return current.error || 'Preview unavailable';
    if (current.status !== 'ready' || !current.loaded) return 'Loading…';
    const slot = current.slots[index];
    if (!slot || slot.width <= 0 || slot.height <= 0) return 'Preview unavailable';
    return null;
  }
  $: videos = items[0]?.media_kind === 'video';
  $: if (audioChoice !== 'off' && !items.some((item) => item.key === audioChoice))
    audioChoice = items[0]?.key ?? '';
  $: audioKey = audioChoice === 'off' ? '' : audioChoice;
  $: audioSlotIndex = audioKey ? items.findIndex((item) => item.key === audioKey) : -1;
  $: activeAudioUrl =
    audioSlotIndex >= 0 ? (videoState.slots[audioSlotIndex]?.audio_url ?? '') : '';
  $: if (sync) {
    void audioSlotIndex;
    void activeAudioUrl;
    void tick().then(() => applyAudioSelection());
  }
  $: prepareVideos(
    JSON.stringify([
      videos,
      compactHidden,
      generation,
      engineBusy,
      items.map((item) => [item.key, item.status]),
    ]),
    videos && !compactHidden ? items : [],
  );

  function regionsFor(
    entries: CompareItem[],
    currentLayout: string,
    currentCell: { width: number; height: number },
    mainFit: { width: number; height: number },
    currentZoom: number,
    px: number,
    py: number,
  ): (Region | null)[] {
    if (entries[0]?.media_kind === 'video') return entries.map(() => null);
    if (entries.length < 2) return entries.map(() => null);
    return entries.map((item, index) => {
      if ((currentLayout === 'slider' && index >= 2) || item.status !== 'ready') return null;
      const fit =
        currentLayout === 'boxes'
          ? fitSize(currentCell, { width: item.width, height: item.height }, 12)
          : mainFit;
      const pan =
        currentLayout === 'boxes'
          ? clampPan(
              { x: (px * fit.width) / mainFit.width, y: (py * fit.height) / mainFit.height },
              panBounds(currentCell, fit, currentZoom),
            )
          : { x: px, y: py };
      return visibleRegion({
        image: { width: item.width, height: item.height },
        preview: { width: item.preview_width || 0, height: item.preview_height || 0 },
        cell: currentCell,
        fit,
        zoom: currentZoom,
        pan,
        pixelRatio: window.devicePixelRatio || 1,
      });
    });
  }

  function scheduleRegions(
    key: string,
    currentGeneration: number,
    regions: (Region | null)[],
  ): void {
    if (key === regionKey || !currentGeneration) return;
    regionKey = key;
    clearTimeout(regionTimer);
    regionTimer = setTimeout(() => {
      regionQueue = regionQueue
        .catch(() => {})
        .then(() => (destroyed ? undefined : requestRegions(currentGeneration, regions)))
        .catch(() => {});
    }, 160);
  }

  function cropStyle(item: CompareItem): string {
    const r = item.region!.bounds;
    return `left:${(100 * r.x) / item.width}%;top:${(100 * r.y) / item.height}%;width:${(100 * r.width) / item.width}%;height:${(100 * r.height) / item.height}%`;
  }

  function currentCrop(item: CompareItem, index: number, desired: (Region | null)[]): boolean {
    return !!item.region && JSON.stringify(item.region.bounds) === JSON.stringify(desired[index]);
  }

  onDestroy(() => {
    destroyed = true;
    clearTimeout(detailsTimer);
    preparationRevision++;
    if (requestId) void cancelVideo(requestId).catch(() => {});
    sync?.pause();
    stopClock();
    desiredTime = 0;
    wantedPlayback = false;
    if (combinedVideo) release(combinedVideo);
    if (audioElement) release(audioElement);
    clearTimeout(regionTimer);
    void regionQueue
      .catch(() => {})
      .then(() => clearRegions())
      .catch(() => {});
  });

  $: block = sliderBlock(
    items.map((item) => item.pick),
    videos ? 'video' : 'image',
  );
  $: shownLayout =
    (videos && items.length > 2) || (layout === 'slider' && block) ? 'boxes' : layout;
  $: inputs = [...new Set(items.map((item) => item.media_name))];
  $: boxes = shownLayout === 'boxes' ? items : items.slice(0, 2);
  $: cols = shownLayout === 'boxes' ? boxColumns(boxes.length, canvasWidth, canvasHeight) : 1;
  $: rows = Math.max(1, Math.ceil(boxes.length / cols));
  $: cell =
    shownLayout === 'boxes'
      ? {
          width: (canvasWidth - 2 * (cols - 1)) / cols,
          height: (canvasHeight - 2 * (rows - 1)) / rows - BAR,
        }
      : { width: canvasWidth, height: canvasHeight };
  function slotSize(
    item: CompareItem | undefined,
    index: number,
    videoSlots: CompareVideoSlot[],
    isVideo: boolean,
  ) {
    const fallback = { width: item?.width || 1, height: item?.height || 1 };
    return isVideo ? slotFrameSize(videoSlots[index], fallback) : fallback;
  }
  $: main = items[0];
  $: mainSize = slotSize(main, 0, videoState.slots, videos);
  $: fitted = fitSize(cell, mainSize, shownLayout === 'boxes' ? 12 : 56);
  // Pan uses the first item's fitted coordinate system; each box clamps its own view.
  $: bounds =
    shownLayout === 'boxes'
      ? boxes.reduce(
          (acc, item, index) => {
            const fit = fitSize(cell, slotSize(item, index, videoState.slots, videos), 12);
            const b = panBounds(cell, fit, zoom);
            return {
              x: Math.max(acc.x, (b.x * fitted.width) / fit.width),
              y: Math.max(acc.y, (b.y * fitted.height) / fit.height),
            };
          },
          { x: 0, y: 0 },
        )
      : panBounds(cell, fitted, zoom);
  $: {
    const clamped = clampPan({ x: panX, y: panY }, bounds);
    if (clamped.x !== panX || clamped.y !== panY) {
      panX = clamped.x;
      panY = clamped.y;
    }
  }
  $: geometry = comparisonGeometry(
    { width: canvasWidth, height: canvasHeight },
    fitted,
    zoom,
    { x: panX, y: panY },
    compare,
  );
  $: desiredRegions = regionsFor(items, shownLayout, cell, fitted, zoom, panX, panY);
  $: desiredKey = JSON.stringify([generation, desiredRegions]);
  $: if (!videos) scheduleRegions(desiredKey, generation, desiredRegions);
  $: (shownLayout, items.length, tick().then(measure));

  onMount(() => {
    const observer =
      typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(() => measure());
    if (well) observer?.observe(well);
    void tick().then(() => {
      measure();
      well?.focus();
    });
    return () => observer?.disconnect();
  });

  function measure(): void {
    if (!well) return;
    const rect = well.getBoundingClientRect();
    canvasWidth = Math.max(1, rect.width);
    canvasHeight = Math.max(1, rect.height);
  }

  /** A box's own fitted size and its share of the common pan. */
  function boxView(item: CompareItem, index: number, ...deps: unknown[]) {
    void deps;
    return croppedBoxView(
      cell,
      slotSize(item, index, videoState.slots, videos),
      fitted,
      zoom,
      { x: panX, y: panY },
      12,
    );
  }
  function boxStyle(item: CompareItem, index: number, ...deps: unknown[]): string {
    const { fit, pan } = boxView(item, index, ...deps);
    return `width:${fit.width}px;height:${fit.height}px;left:50%;top:50%;transform:translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) scale(${zoom})`;
  }

  // An unfinished photo's mark shows its finished tiles while hovered or focused with the
  // keyboard; a click keeps them shown.
  let tilesHovered = '';
  let tilesPinned = '';
  $: tilesLit = (key: string) => tilesHovered === key || tilesPinned === key;

  // The details strip keeps its height: a fact that does not fit its line is cut, and the
  // full list opens above the strip on hover, on focus of More, or on a click of it.
  let clipped: Record<string, boolean> = {};
  let detailsHovered = '';
  let detailsPinned = '';
  let detailsTimer: ReturnType<typeof setTimeout> | undefined;
  // Every change of what a column shows is measured again; only the key is read.
  function clipWatch(node: HTMLElement, watched: { key: string; [shown: string]: unknown }) {
    let key = watched.key;
    const measure = () => {
      const cut = [...node.querySelectorAll<HTMLElement>('[data-clip]')].some(
        (element) => element.scrollWidth > element.clientWidth + 1,
      );
      if (Boolean(clipped[key]) !== cut) clipped = { ...clipped, [key]: cut };
    };
    const observer =
      typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(measure);
    observer?.observe(node);
    measure();
    return {
      update(next: { key: string; [shown: string]: unknown }) {
        key = next.key;
        void tick().then(measure);
      },
      destroy() {
        observer?.disconnect();
      },
    };
  }
  function hoverDetails(key: string): void {
    clearTimeout(detailsTimer);
    if (!key) detailsHovered = '';
    else detailsTimer = setTimeout(() => (detailsHovered = key), 350);
  }

  function setZoom(
    next: number,
    clientX?: number,
    clientY?: number,
    target?: Element | null,
  ): void {
    const bounded = Math.max(minZoom, Math.min(maxZoom, next));
    if (bounded === zoom) return;
    const box = shownLayout === 'boxes' ? target?.closest('.box-well') : null;
    const rect = (box || well)?.getBoundingClientRect();
    if (!rect) return;
    const index = box ? Array.from(well!.querySelectorAll('.box-well')).indexOf(box) : -1;
    const item = boxes[index];
    const fit = item ? fitSize(cell, slotSize(item, index, videoState.slots, videos), 12) : fitted;
    const kx = fit.width / fitted.width,
      ky = fit.height / fitted.height;
    const pointer = {
      x: ((clientX ?? rect.left + rect.width / 2) - rect.left - rect.width / 2) / kx,
      y: ((clientY ?? rect.top + rect.height / 2) - rect.top - rect.height / 2) / ky,
    };
    const nextBounds =
      shownLayout === 'boxes'
        ? boxes.reduce(
            (acc, entry, entryIndex) => {
              const f = fitSize(cell, slotSize(entry, entryIndex, videoState.slots, videos), 12);
              const b = panBounds(cell, f, bounded);
              return {
                x: Math.max(acc.x, (b.x * fitted.width) / f.width),
                y: Math.max(acc.y, (b.y * fitted.height) / f.height),
              };
            },
            { x: 0, y: 0 },
          )
        : panBounds(cell, fitted, bounded);
    const nextPan = pointerCenteredPan({ x: panX, y: panY }, pointer, zoom, bounded, nextBounds);
    zoom = bounded;
    panX = nextPan.x;
    panY = nextPan.y;
  }

  function onWheel(event: WheelEvent): void {
    event.preventDefault();
    setZoom(
      zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12),
      event.clientX,
      event.clientY,
      event.target instanceof Element ? event.target : null,
    );
  }

  function nearLine(event: PointerEvent): boolean {
    if (shownLayout !== 'slider' || !well) return false;
    const rect = well.getBoundingClientRect();
    const y = event.clientY - rect.top;
    return (
      y >= geometry.top &&
      y <= geometry.bottom &&
      Math.abs(event.clientX - rect.left - geometry.x) <= 32
    );
  }

  function placeLine(clientX: number): void {
    if (!well) return;
    const rect = well.getBoundingClientRect();
    compare = comparisonGeometry(
      { width: canvasWidth, height: canvasHeight },
      fitted,
      zoom,
      { x: panX, y: panY },
      comparisonFromPointer(clientX, rect.left, rect.width),
    ).percent;
  }

  function pointerDown(event: PointerEvent): void {
    if (event.button !== 0) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('button')) return;
    if (target?.closest('.compare-line') || nearLine(event)) {
      draggingLine = true;
      placeLine(event.clientX);
      (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
      event.preventDefault();
      return;
    }
    if (bounds.x <= 0 && bounds.y <= 0) return;
    dragging = true;
    dragStartX = event.clientX;
    dragStartY = event.clientY;
    panStartX = panX;
    panStartY = panY;
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
  }

  function pointerMove(event: PointerEvent): void {
    if (draggingLine) {
      placeLine(event.clientX);
      return;
    }
    if (!dragging) return;
    const next = clampPan(
      { x: panStartX + event.clientX - dragStartX, y: panStartY + event.clientY - dragStartY },
      bounds,
    );
    panX = next.x;
    panY = next.y;
  }

  function pointerUp(): void {
    dragging = false;
    draggingLine = false;
  }

  function lineKey(event: KeyboardEvent): void {
    const next = comparisonFromKey(100 - geometry.enhancedPercent, event.key);
    if (next === null) return;
    const x = geometry.left + ((geometry.right - geometry.left) * next) / 100;
    compare = (x / canvasWidth) * 100;
    event.preventDefault();
  }

  function windowKey(event: KeyboardEvent): void {
    if (videos && !event.defaultPrevented && !event.metaKey && !event.ctrlKey && !event.altKey) {
      const target = event.target instanceof Element ? event.target : null;
      // Also with the focus nowhere: WebKit leaves it there after a click on a button.
      if (
        target &&
        (target === document.body || target.closest('.compare-pane')) &&
        !target.closest('input, textarea, select, [contenteditable]')
      ) {
        if (event.key === ' ' && !target.closest('button')) {
          event.preventDefault();
          togglePlayback();
          return;
        }
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault();
          step(event.key === 'ArrowLeft' ? -1 : 1);
          return;
        }
      }
    }
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('[aria-modal="true"], [role="listbox"]')) return;
    // An open list of details closes first, wherever the focus is: it opens on hover too,
    // and WebKit does not focus a button that is clicked.
    if (clipped[detailsHovered] || clipped[detailsPinned]) {
      event.preventDefault();
      hoverDetails('');
      detailsPinned = '';
      return;
    }
    close();
  }
  /** A list kept open by a click on More closes with a click anywhere else. */
  function windowPointerDown(event: PointerEvent): void {
    if (!detailsPinned) return;
    const target = event.target instanceof Element ? event.target : null;
    if (!target?.closest('.details-more')) detailsPinned = '';
  }
</script>

<svelte:window on:keydown={windowKey} on:pointerdown={windowPointerDown} />

<section class="preview-pane compare-pane" class:compact-hidden={compactHidden}>
  <div class="preview-header compare-header" data-tauri-drag-region>
    <slot name="tabs" />
    <span class="compare-names"
      ><span class="names"
        >{#if inputs.length > 2}{inputs.length}
          {videos ? 'videos' : 'images'}{:else}{#each inputs as name, index (name)}{#if index}<span
                class="names-and">and</span
              >{/if}<FileName {name} />{/each}{/if}</span
      ><HelpTip
        label="Compare"
        text="The slider holds slot 1 against slot 2; boxes show every slot with one zoom and pan. Zoom enlarges the preview."
      /></span
    >
    <div class="compare-tools">
      <div class="view-switch" role="radiogroup" aria-label="Compare layout">
        <button
          role="radio"
          aria-checked={shownLayout === 'slider'}
          class:active={shownLayout === 'slider'}
          disabled={Boolean(block) || (videos && items.length > 2)}
          title={block || 'Slot 1 left, slot 2 right · drag the divider'}
          on:click={() => setLayout('slider')}>Slider</button
        >
        <button
          role="radio"
          aria-checked={shownLayout === 'boxes'}
          class:active={shownLayout === 'boxes'}
          title="Every slot in its own box · one zoom and pan"
          on:click={() => setLayout('boxes')}>Boxes</button
        >
      </div>
      {#if !asTab}<button
          class="button ghost done"
          on:click={close}
          title="Back to the preview (Escape)">Done</button
        >{/if}
    </div>
  </div>
  <div class="compare-slots" role="list" aria-label="Compared items">
    {#each items as item, index (item.key)}
      <span
        class="compare-slot"
        role="listitem"
        class:resting={shownLayout === 'slider' && index > 1}
      >
        <b class="slot-number">{index + 1}</b>
        <span class="slot-label">{item.label}</span>
        {#if (items.length < 2 || (shownLayout === 'slider' && index > 1)) && item.status !== 'ready'}<span
            class="slot-meta"
            role="status"
            >{item.status === 'loading'
              ? 'Loading…'
              : item.status === 'missing'
                ? 'File missing'
                : videos
                  ? videoState.error || 'Loading…'
                  : item.error || 'Preview unavailable'}</span
          >{/if}
        {#if shownLayout !== 'boxes'}<span class="slot-meta"
            >{item.meta}{#if inputs.length > 1}{item.meta ? ' · ' : ''}<FileName
                name={item.media_name}
              />{/if}</span
          >{/if}
        {#if index > 1 && !videos}<button
            class="slot-remove"
            aria-label={`Move ${item.label} to slider slot 2`}
            title="Move to slider slot 2"
            on:click={() => swapItems(1, index)}>↥</button
          >{/if}
        <button
          type="button"
          class="slot-remove"
          aria-label={`Remove ${item.label} from the compare`}
          on:click={async () => {
            removeItem(item.key);
            await tick();
            (
              document.querySelector<HTMLButtonElement>('.compare-slot .slot-remove') ||
              document.querySelector<HTMLElement>(
                '.compare-bar .button:not(:disabled), .outputs-list input:not(:disabled)',
              )
            )?.focus();
          }}>×</button
        >
      </span>
      {#if index === 0 && items.length > 1}<button
          class="slot-remove"
          aria-label="Swap slots 1 and 2"
          title="Swap slots 1 and 2"
          on:click={() => swapItems(0, 1)}>⇄</button
        >{/if}
    {/each}
    {#if block && layout === 'slider'}<span class="compare-slots-note">{block}</span>{/if}
  </div>

  <div class:video-comparison={videos}>
    <div
      bind:this={well}
      class="canvas-well compare-well"
      class:panning={dragging}
      class:comparing={draggingLine}
      role="region"
      aria-label="Compare canvas"
      tabindex="-1"
      on:wheel={onWheel}
      on:pointerenter={() => (hovering = true)}
      on:pointerleave={() => (hovering = false)}
      on:pointerdown={pointerDown}
      on:pointermove={pointerMove}
      on:pointerup={pointerUp}
      on:pointercancel={pointerUp}
    >
      {#if videos && items.length >= 2}
        <!-- svelte-ignore a11y_media_has_caption -->
        <video
          class="compare-video-source"
          bind:this={combinedVideo}
          use:combinedPlayer
          src={videoState.url || undefined}
          muted
          playsinline
          preload="auto"
          aria-hidden="true"
          on:error={videoError}
        ></video>
        <audio
          class="compare-audio-source"
          bind:this={audioElement}
          use:audioPlayer
          src={activeAudioUrl || undefined}
          muted={!activeAudioUrl}
          preload="auto"
          aria-hidden="true"
        ></audio>
      {/if}
      {#if items.length < 2}
        <div class="canvas-empty">
          <h2>Pick two outputs</h2>
          <p>Tick them under Outputs; the first two take the slider, up to four fill the boxes.</p>
        </div>
      {:else if shownLayout === 'slider'}
        <div
          class="image-stage"
          class:video-stage={videos}
          style={`width:${fitted.width}px;height:${fitted.height}px;left:50%;top:50%;transform:translate(calc(-50% + ${panX}px), calc(-50% + ${panY}px)) scale(${zoom})`}
        >
          {#if videos}
            {#each items.slice(0, 2) as item, index (item.key)}
              <canvas
                use:videoCanvas={index}
                aria-label={`${index + 1}: ${item.label}`}
                style={index ? `clip-path: inset(0 0 0 ${geometry.imagePercent}%)` : ''}
              ></canvas>
            {/each}
          {:else}
            <img
              class="source-image"
              src={items[0].preview_data_url}
              alt={`1: ${items[0].label}`}
              decoding="async"
              draggable="false"
            />
            {#if currentCrop(items[0], 0, desiredRegions)}
              <img
                class="region-image"
                style={cropStyle(items[0])}
                src={items[0].region?.data_url}
                alt=""
                decoding="async"
                draggable="false"
              />
            {/if}
            <img
              class="result-image"
              style={`clip-path: inset(0 0 0 ${geometry.imagePercent}%)`}
              src={items[1].preview_data_url}
              alt={`2: ${items[1].label}`}
              decoding="async"
              draggable="false"
            />
            {#if currentCrop(items[1], 1, desiredRegions)}
              <div
                class="region-overlay"
                style={`clip-path: inset(0 0 0 ${geometry.imagePercent}%)`}
              >
                <img
                  class="region-image"
                  style={cropStyle(items[1])}
                  src={items[1].region?.data_url}
                  alt=""
                  decoding="async"
                  draggable="false"
                />
              </div>
            {/if}
          {/if}
        </div>
        {#if !videos}
          {#each items.slice(0, 2) as item, index (item.key)}
            {#if item.unfinished?.tiles && item.status === 'ready'}<UnfinishedTiles
                {...item.unfinished.tiles}
                fit={fitted}
                {zoom}
                pan={{ x: panX, y: panY }}
                clipLeft={index ? geometry.imagePercent : 0}
                clipRight={index ? 0 : 100 - geometry.imagePercent}
                lit={tilesLit(item.key)}
              />{/if}
          {/each}
        {/if}
        {#each items.slice(0, 2) as item, index (item.key)}
          {@const overlay = videos
            ? slotOverlayMessage(item, index, videoState)
            : item.status !== 'ready'
              ? item.status === 'loading'
                ? 'Loading…'
                : item.status === 'missing'
                  ? 'File missing'
                  : item.error || 'Preview unavailable'
              : null}
          {#if overlay}<div class="slot-state" class:right-state={index === 1} role="status">
              {item.label}: {overlay}
            </div>{/if}
        {/each}
        <div
          class="compare-line"
          role="slider"
          tabindex="0"
          aria-label="Divider between slot 1 and slot 2"
          aria-valuemin="0"
          aria-valuemax="100"
          aria-valuenow={Math.round(100 - geometry.enhancedPercent)}
          aria-valuetext={`${Math.round(100 - geometry.enhancedPercent)}% ${items[0].label}, rest ${items[1].label}`}
          style={`left:${geometry.x}px;top:${geometry.top}px;height:${geometry.bottom - geometry.top}px;--handle-top:${geometry.handleY - geometry.top}px`}
          on:keydown={lineKey}
        >
          <span aria-hidden="true">↔</span>
        </div>
        <span
          class="original-chip side"
          style={`left:${geometry.left + 10}px;${videos ? 'top:auto;bottom' : 'top'}:${videos ? canvasHeight - geometry.bottom + 10 : geometry.top + 10}px`}
          ><b class="slot-number">1</b>{items[0].label}</span
        >
        <span
          class="original-chip side right"
          style={`right:${canvasWidth - geometry.right + 10}px;${videos ? 'top:auto;bottom' : 'top'}:${videos ? canvasHeight - geometry.bottom + 10 : geometry.top + 10}px`}
          ><b class="slot-number">2</b>{items[1].label}</span
        >
      {:else}
        <div class="boxes" style={`grid-template-columns: repeat(${cols}, minmax(0, 1fr));`}>
          {#each boxes as item, index (item.key)}
            <div class="box">
              <div class="box-bar">
                <b class="slot-number">{index + 1}</b>
                <strong>{item.label}</strong>
                <span>{item.meta}{inputs.length > 1 ? ` · ${item.media_name}` : ''}</span>
              </div>
              <div class="box-well">
                {#if videos}
                  {@const overlay = slotOverlayMessage(item, index, videoState)}
                  {#if overlay}<div class="slot-state" role="status">{overlay}</div>{/if}
                {:else if item.status !== 'ready'}
                  <div class="slot-state" role="status">
                    {item.status === 'loading'
                      ? 'Loading…'
                      : item.status === 'missing'
                        ? 'File missing'
                        : item.error || 'Preview unavailable'}
                  </div>
                {/if}
                <div
                  class="image-stage"
                  class:video-stage={videos}
                  style={boxStyle(item, index, fitted, panX, panY, zoom, cell, videoState.slots)}
                >
                  {#if videos}
                    <canvas use:videoCanvas={index} aria-label={`${index + 1}: ${item.label}`}
                    ></canvas>
                  {:else}
                    <img
                      src={item.preview_data_url}
                      alt={`${index + 1}: ${item.label}`}
                      decoding="async"
                      draggable="false"
                    />
                    {#if currentCrop(item, index, desiredRegions)}
                      <img
                        class="region-image"
                        style={cropStyle(item)}
                        src={item.region?.data_url}
                        alt=""
                        decoding="async"
                        draggable="false"
                      />
                    {/if}
                  {/if}
                </div>
                {#if !videos && item.unfinished?.tiles && item.status === 'ready'}<UnfinishedTiles
                    {...item.unfinished.tiles}
                    {...boxView(item, index, fitted, panX, panY, zoom, cell, videoState.slots)}
                    {zoom}
                    lit={tilesLit(item.key)}
                  />{/if}
              </div>
            </div>
          {/each}
        </div>
      {/if}
      {#if items.length >= 2 && !videos}
        <div
          class="zoom-hud"
          class:shown={hovering}
          role="group"
          aria-label="Compare zoom"
          on:pointerdown|stopPropagation
          on:wheel|stopPropagation
        >
          <button
            aria-label="Zoom out"
            disabled={zoom <= minZoom}
            on:click={() => setZoom(zoom / 1.25)}>−</button
          >
          <span>{Math.round(zoom * 100)}%</span>
          <button
            aria-label="Zoom in"
            disabled={zoom >= maxZoom}
            on:click={() => setZoom(zoom * 1.25)}>+</button
          >
          <button class:active={Math.abs(zoom - 1) < 0.001} on:click={() => setZoom(1)}>Fit</button>
          <BackgroundSwitch value={viewerBackground} set={setViewerBackground} />
        </div>
      {/if}
    </div>
    {#if videos}
      <div class="video-hud">
        <VideoTransport
          {playing}
          {currentTime}
          {duration}
          {playbackRate}
          toggle={togglePlayback}
          {seek}
          {nextRate}
          {step}
          {frames}
          {frame}
          {seekFrame}
        >
          <div class="transport-audio" role="radiogroup" aria-label="Audio">
            <span aria-hidden="true">Audio</span>
            {#each boxes as item, index (item.key)}
              <button
                type="button"
                role="radio"
                aria-checked={audioKey === item.key}
                class:active={audioKey === item.key}
                aria-label={`Audio of slot ${index + 1}: ${item.label}`}
                title={`Play the audio of slot ${index + 1}: ${item.label}`}
                on:click={() => chooseAudio(item.key)}>{index + 1}</button
              >
            {/each}
            <button
              type="button"
              role="radio"
              aria-checked={!audioKey}
              class:active={!audioKey}
              aria-label="No audio"
              title="No audio"
              on:click={() => chooseAudio('off')}>Off</button
            >
          </div>
        </VideoTransport>
        <div class="zoom-hud" role="group" aria-label="Compare zoom">
          <button
            aria-label="Zoom out"
            disabled={zoom <= minZoom}
            on:click={() => setZoom(zoom / 1.25)}>−</button
          >
          <span>{Math.round(zoom * 100)}%</span>
          <button
            aria-label="Zoom in"
            disabled={zoom >= maxZoom}
            on:click={() => setZoom(zoom * 1.25)}>+</button
          >
          <button class:active={Math.abs(zoom - 1) < 0.001} on:click={() => setZoom(1)}>Fit</button>
          <BackgroundSwitch value={viewerBackground} set={setViewerBackground} />
        </div>
      </div>
      {#if limited}<span class="compare-slots-note" role="status"
          >Playback ends where the unfinished result ends, after {duration < 1
            ? `${frames} ${frames === 1 ? 'frame' : 'frames'}`
            : formatMediaTime(duration)}.</span
        >{/if}
      {#if videoState.note}<span class="compare-slots-note">{videoState.note}</span>{/if}
    {/if}
  </div>
  {#if items.length >= 2}
    <div
      class="compare-details"
      style={`grid-template-columns: repeat(${boxes.length}, minmax(0, 1fr));`}
    >
      {#each boxes as item, index (item.key)}
        {@const facts = item.details ?? []}
        {@const mark = item.unfinished}
        {@const open = clipped[item.key] && [detailsHovered, detailsPinned].includes(item.key)}
        <!-- Hovering only opens what the More button opens too. -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="details-column"
          class:end={index > 0 && index >= boxes.length / 2}
          use:clipWatch={{ key: item.key, facts, mark, label: item.label }}
          on:pointerenter={() => hoverDetails(item.key)}
          on:pointerleave={() => hoverDetails('')}
        >
          <div role="group" aria-label={`Slot ${index + 1} details`}>
            <div class="details-title">
              <b class="slot-number">{index + 1}</b>
              <strong data-clip>{item.label}</strong>
              {#if clipped[item.key]}<button
                  type="button"
                  class="link-button details-more"
                  aria-expanded={open}
                  aria-label={`All details of slot ${index + 1}`}
                  on:focus={(event) => {
                    if (event.currentTarget.matches(':focus-visible')) detailsHovered = item.key;
                  }}
                  on:blur={() => {
                    detailsHovered = '';
                    detailsPinned = '';
                  }}
                  on:click={() => (detailsPinned = detailsPinned === item.key ? '' : item.key)}
                  >More</button
                >{/if}
            </div>
            {#if mark}
              {#if mark.tiles && !videos}<button
                  type="button"
                  class="unfinished-mark"
                  data-clip
                  aria-pressed={tilesPinned === item.key}
                  aria-label={`${mark.label}. Show the finished tiles`}
                  title="Show the finished tiles · click to keep them shown"
                  on:pointerenter={() => {
                    // The tiles show on the canvas, where the list of details would open.
                    hoverDetails('');
                    tilesHovered = item.key;
                  }}
                  on:pointerleave={() => {
                    tilesHovered = '';
                    hoverDetails(item.key);
                  }}
                  on:focus={(event) => {
                    if (event.currentTarget.matches(':focus-visible')) tilesHovered = item.key;
                  }}
                  on:blur={() => (tilesHovered = '')}
                  on:click={() => (tilesPinned = tilesPinned === item.key ? '' : item.key)}
                  >{mark.label}</button
                >{:else}<span class="unfinished-mark" data-clip>{mark.label}</span>{/if}
            {/if}
            {#each detailLines(facts, mark ? 3 : 4) as line}<span class="details-line" data-clip
                >{#each line as fact}<span>{fact}</span>{/each}</span
              >{/each}
          </div>
          {#if open}
            <div class="details-full">
              <strong>{item.label}</strong>
              {#if mark}<span>{mark.label}</span>{/if}
              {#each facts as fact}<span>{fact}</span>{/each}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .slot-state {
    position: absolute;
    z-index: 3;
    top: 50%;
    left: 10%;
    max-width: 38%;
    color: var(--text);
    background: var(--panel);
    padding: 8px;
  }
  .right-state {
    left: auto;
    right: 10%;
  }
  .box-well .slot-state {
    left: 10%;
    max-width: 80%;
  }
  .compare-pane > div:nth-child(3) {
    position: relative;
    min-height: 0;
  }
  .compare-pane > .video-comparison {
    position: relative;
    inset: auto;
  }
  .compare-pane > div:nth-child(3):not(.video-comparison) > .compare-well {
    height: 100%;
  }
  .compare-pane {
    grid-template-rows: 52px auto minmax(0, 1fr) auto;
  }
  /* A title and at most four lines per slot, whatever the job recorded: a line that does
     not fit is cut, and the full list opens above the strip. */
  .compare-details {
    display: grid;
    border-top: 1px solid var(--hairline-faint);
    background: var(--header-bg);
    font-size: calc(11px * var(--ui-scale));
    line-height: 1.3;
  }
  .details-column {
    position: relative;
    min-width: 0;
    padding: 6px 16px;
  }
  .details-column > [role='group'] {
    display: grid;
    grid-auto-rows: min-content;
    gap: 2px;
    justify-items: start;
    min-width: 0;
  }
  .details-title {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
    min-width: 0;
  }
  .details-title strong,
  .details-line,
  .unfinished-mark {
    max-width: 100%;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .details-title strong {
    flex: 0 1 auto;
  }
  .details-more {
    flex: none;
    margin-left: auto;
    color: var(--secondary);
    font-size: inherit;
  }
  .details-line {
    color: var(--secondary);
  }
  .details-line > span + span {
    margin-left: 12px;
  }
  /* The same tag whether it is only a note (a video) or shows the finished tiles (a photo). */
  .unfinished-mark {
    padding: 0 5px;
    border: 1px solid var(--chip-border);
    border-radius: var(--radius);
    background: transparent;
    color: var(--text);
    font-size: inherit;
    line-height: inherit;
  }
  button.unfinished-mark {
    cursor: pointer;
  }
  button.unfinished-mark:hover,
  button.unfinished-mark[aria-pressed='true'] {
    border-color: var(--accent-line);
    background: var(--hover);
  }
  .details-full {
    position: absolute;
    z-index: 30;
    left: 8px;
    bottom: calc(100% + 4px);
    display: grid;
    gap: 3px;
    width: max-content;
    max-width: min(400px, calc(100vw - 48px));
    padding: 8px 10px;
    border: 1px solid var(--border);
    border-radius: var(--radius-surface);
    background: var(--raised);
    color: var(--text);
    font-size: calc(11.5px * var(--ui-scale));
    line-height: 1.45;
    overflow-wrap: anywhere;
    pointer-events: none;
  }
  .details-column.end .details-full {
    left: auto;
    right: 8px;
  }
  .details-full span {
    color: var(--secondary);
  }
  .compare-header span :global(.help-tip) {
    vertical-align: 0;
  }
  /* The help text is positioned inside this block, so it must not clip: only the
     file names truncate. */
  .compare-names {
    position: relative;
    flex: 1 1 auto;
    display: flex;
    align-items: center;
    min-width: 0;
    font-size: calc(12px * var(--ui-scale));
  }
  .compare-names .names {
    display: flex;
    gap: 4px;
    min-width: 0;
    white-space: nowrap;
  }
  .compare-header :global(.help-tip) {
    flex: none;
  }
  .compare-header :global(.help-tip-text) {
    right: auto;
    width: min(300px, 60vw);
    white-space: normal;
  }
  .compare-tools {
    flex: none;
    display: flex;
    align-items: center;
    gap: 12px;
    margin-left: auto;
  }
  .view-switch button:disabled {
    color: var(--tertiary);
    cursor: default;
  }
  .done {
    padding: 4px 10px;
    min-height: 0;
  }
  .compare-slots {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 8px;
    min-height: 36px;
    padding: 5px 16px;
    border-bottom: 1px solid var(--hairline-faint);
    background: var(--header-bg);
    font-size: calc(12px * var(--ui-scale));
  }
  .compare-slot {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 100%;
    padding: 2px 2px 2px 3px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--recess);
  }
  .compare-slot.resting {
    color: var(--tertiary);
  }
  .compare-slot.resting b {
    background: var(--tertiary);
  }
  .slot-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .slot-meta {
    display: inline-flex;
    min-width: 0;
    color: var(--secondary);
    font-size: calc(11px * var(--ui-scale));
    white-space: nowrap;
  }
  .slot-remove {
    width: 20px;
    height: 20px;
    padding: 0;
    border: 0;
    border-radius: var(--radius);
    background: transparent;
    color: var(--secondary);
    font-size: 14px;
    line-height: 1;
  }
  .slot-remove:hover {
    background: var(--hover);
    color: var(--text);
  }
  .compare-slots-note {
    color: var(--tertiary);
    font-size: calc(11px * var(--ui-scale));
  }
  /* Under the transport, in line with it. */
  .video-comparison > .compare-slots-note {
    padding: 0 16px 6px;
  }
  .original-chip.side {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: calc(50% - 20px);
    overflow: hidden;
    white-space: nowrap;
    pointer-events: none;
  }
  .original-chip.right {
    left: auto;
    right: 14px;
  }
  .compare-well .canvas-empty {
    max-width: 320px;
    text-align: center;
  }
  .compare-well .canvas-empty h2 {
    margin: 0 0 6px;
    font-size: calc(14px * var(--ui-scale));
    font-weight: 600;
  }
  .compare-well .canvas-empty p {
    margin: 0;
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
    line-height: 1.5;
  }
  .boxes {
    position: absolute;
    inset: 0;
    display: grid;
    grid-auto-rows: minmax(0, 1fr);
    gap: 2px;
    background: var(--border);
  }
  .box {
    position: relative;
    min-width: 0;
    min-height: 0;
    display: grid;
    grid-template-rows: 26px 1fr;
    background: var(--viewer-bg, var(--stage));
  }
  .box-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    padding: 0 10px;
    background: var(--header-bg);
    font-size: calc(11px * var(--ui-scale));
  }
  .box-bar strong {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 500;
  }
  .box-bar span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--secondary);
  }
  .box-well {
    position: relative;
    overflow: hidden;
  }
  .region-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }
  .region-image {
    position: absolute;
    max-width: none;
    pointer-events: none;
  }
  .box-well .image-stage {
    box-shadow: none;
  }
</style>
