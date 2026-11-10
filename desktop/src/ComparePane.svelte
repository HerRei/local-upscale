<script lang="ts">
  /**
   * The Compare mode of the main pane: the picked items as a slider (slot 1 against 2) or
   * as boxes (up to four), with one zoom and pan for every box. Escape or Done leaves.
   */
  import { onDestroy, onMount, tick } from 'svelte';
  import { visibleRegion, type Region } from './lib/compareRegions';
  import * as api from './lib/api';
  import type { VideoComparisonSources } from './lib/types';
  import { CompareVideoSync } from './lib/compare-video-sync';
  import VideoTransport from './VideoTransport.svelte';
  import FileName from './FileName.svelte';
  import HelpTip from './HelpTip.svelte';
  import { comparisonFromKey, comparisonFromPointer } from './lib/comparison';
  import { boxColumns, sliderBlock, type CompareItem } from './lib/compareOutputs';
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

  type Source = {
    url: string;
    status: CompareItem['status'];
    error: string;
    note: string;
    loaded: boolean;
  };
  let sources: Record<string, Source> = {};
  const players = new Map<string, HTMLVideoElement>();
  let sync: CompareVideoSync | undefined;
  let playing = false;
  let currentTime = 0;
  let duration = 0;
  let playbackRate = 1;
  let muted = false;
  let preparationKey = '';
  let preparationRevision = 0;
  let requestId = '';
  let frameId: number | undefined;
  let frameVideo: HTMLVideoElement | undefined;
  let animationId: number | undefined;
  let preparationQueue = Promise.resolve();

  function updateTransport(): void {
    playing = sync?.requestedPlayback ?? false;
    currentTime = sync?.time ?? 0;
    duration = sync?.duration ?? 0;
  }
  function stopClock(): void {
    if (frameId !== undefined) frameVideo?.cancelVideoFrameCallback(frameId);
    if (animationId !== undefined) cancelAnimationFrame(animationId);
    frameId = animationId = undefined;
    frameVideo = undefined;
  }
  function clock(): void {
    stopClock();
    if (!sync?.playing || destroyed) return;
    const master = items
      .map((item) => players.get(item.key))
      .find((player) => player && !player.paused);
    const update = () => {
      frameId = animationId = undefined;
      sync?.tick();
      updateTransport();
      clock();
    };
    if (master?.requestVideoFrameCallback) {
      frameVideo = master;
      frameId = master.requestVideoFrameCallback(update);
    } else animationId = requestAnimationFrame(update);
  }
  function rebuild(): void {
    const time = sync?.time ?? 0;
    const resume = sync?.requestedPlayback ?? false;
    sync?.pause();
    stopClock();
    sync = new CompareVideoSync(
      items.flatMap((item, index) => {
        const video = players.get(item.key);
        return video && sources[item.key]?.loaded
          ? [{ video, fps: item.fps || 30, audible: index === 0 }]
          : [];
      }),
    );
    sync.rate(playbackRate);
    sync.mute(muted);
    sync.seek(time);
    updateTransport();
    if (resume && !destroyed) void start();
  }
  async function start(): Promise<void> {
    try {
      await sync?.play();
    } catch (error) {
      // A failed element is removed by its error event; the remaining slots still play.
      console.warn('Compare playback could not start', error);
    }
    updateTransport();
    clock();
  }
  function togglePlayback(): void {
    if (playing) {
      sync?.pause();
      stopClock();
      updateTransport();
    } else void start();
  }
  function seek(seconds: number): void {
    sync?.seek(seconds);
    updateTransport();
    clock();
  }
  function nextRate(): void {
    const rates = [0.5, 1, 1.5, 2];
    playbackRate = rates[(rates.indexOf(playbackRate) + 1) % rates.length];
    sync?.rate(playbackRate);
    clock();
  }
  function release(video: HTMLVideoElement): void {
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
  function videoPlayer(video: HTMLVideoElement, key: string) {
    players.set(key, video);
    const stalled = () => {
      if (!sources[key]?.loaded) return;
      sync?.stall(video);
      stopClock();
      updateTransport();
    };
    const ready = () => {
      if (!sources[key]?.loaded) return;
      void sync
        ?.ready(video)
        .then(() => {
          updateTransport();
          clock();
        })
        .catch(() => {});
    };
    const loaded = () => {
      if (sources[key]) {
        sources = { ...sources, [key]: { ...sources[key], loaded: true } };
        rebuild();
      }
    };
    video.addEventListener('waiting', stalled);
    video.addEventListener('seeking', stalled);
    video.addEventListener('canplay', ready);
    video.addEventListener('seeked', ready);
    video.addEventListener('loadeddata', loaded);
    video.addEventListener('ended', clock);
    return {
      destroy() {
        video.removeEventListener('waiting', stalled);
        video.removeEventListener('seeking', stalled);
        video.removeEventListener('canplay', ready);
        video.removeEventListener('seeked', ready);
        video.removeEventListener('loadeddata', loaded);
        video.removeEventListener('ended', clock);
        players.delete(key);
        release(video);
        rebuild();
      },
    };
  }
  async function prepareSlot(
    item: CompareItem,
    index: number,
    revision: number,
    force = fallback.has(item.key),
  ): Promise<void> {
    if (destroyed || revision !== preparationRevision) return;
    const id = crypto.randomUUID();
    requestId = id;
    try {
      const descriptor: VideoComparisonSources = await prepareVideo(
        generation,
        index,
        id,
        force,
        engineBusy,
      );
      if (destroyed || revision !== preparationRevision) return;
      sources = {
        ...sources,
        [item.key]: {
          url: descriptor.original_url,
          status: 'ready',
          error: '',
          note: descriptor.playback_note || '',
          loaded: false,
        },
      };
    } catch (error) {
      if (destroyed || revision !== preparationRevision) return;
      sources = {
        ...sources,
        [item.key]: { url: '', status: 'error', error: String(error), note: '', loaded: false },
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
    for (const player of players.values()) release(player);
    sources = Object.fromEntries(
      entries.map((item) => [
        item.key,
        {
          url: '',
          status: item.status === 'ready' ? 'loading' : item.status,
          error: item.error,
          note: '',
          loaded: false,
        },
      ]),
    );
    sync = undefined;
    updateTransport();
    // Single-preview conversion has one cancellation gate. Prepare slots serially
    // through that same gate, allowing each ready side to appear independently.
    preparationQueue = preparationQueue
      .catch(() => {})
      .then(async () => {
        for (const [index, item] of entries.entries()) {
          if (item.status === 'ready') await prepareSlot(item, index, revision);
        }
      });
  }
  const fallback = new Set<string>();
  function videoError(item: CompareItem, index: number): void {
    if (destroyed || !sources[item.key]?.url) return;
    const player = players.get(item.key);
    const code = player?.error?.code;
    if (player) release(player);
    const compatible = !fallback.has(item.key) && (code === 3 || code === 4);
    sources = {
      ...sources,
      [item.key]: {
        url: '',
        status: compatible ? 'loading' : 'error',
        error: 'Preview unavailable',
        note: '',
        loaded: false,
      },
    };
    rebuild();
    if (compatible) {
      fallback.add(item.key);
      const revision = preparationRevision;
      preparationQueue = preparationQueue
        .catch(() => {})
        .then(() => prepareSlot(item, index, revision, true));
    }
  }
  function state(item: CompareItem, available: Record<string, Source>): Source {
    return (
      available[item.key] || {
        url: '',
        status: item.status,
        error: item.error,
        note: '',
        loaded: false,
      }
    );
  }
  $: videos = items[0]?.media_kind === 'video';
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
    preparationRevision++;
    if (requestId) void cancelVideo(requestId).catch(() => {});
    sync?.pause();
    stopClock();
    for (const player of players.values()) release(player);
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
  $: main = items[0];
  $: mainSize = { width: main?.width || 1, height: main?.height || 1 };
  $: fitted = fitSize(cell, mainSize, shownLayout === 'boxes' ? 12 : 56);
  // Pan uses the first image's fitted coordinate system; each box clamps its own view.
  $: bounds =
    shownLayout === 'boxes'
      ? boxes.reduce(
          (acc, item) => {
            const fit = fitSize(
              cell,
              videos ? mainSize : { width: item.width || 1, height: item.height || 1 },
              12,
            );
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

  function boxStyle(item: CompareItem, ...deps: unknown[]): string {
    void deps;
    const fit = fitSize(
      cell,
      videos ? mainSize : { width: item.width || 1, height: item.height || 1 },
      12,
    );
    const pan = clampPan(
      { x: (panX * fit.width) / fitted.width, y: (panY * fit.height) / fitted.height },
      panBounds(cell, fit, zoom),
    );
    return `width:${fit.width}px;height:${fit.height}px;left:50%;top:50%;transform:translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) scale(${zoom})`;
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
    const fit = item
      ? fitSize(cell, videos ? mainSize : { width: item.width || 1, height: item.height || 1 }, 12)
      : fitted;
    const kx = fit.width / fitted.width,
      ky = fit.height / fitted.height;
    const pointer = {
      x: ((clientX ?? rect.left + rect.width / 2) - rect.left - rect.width / 2) / kx,
      y: ((clientY ?? rect.top + rect.height / 2) - rect.top - rect.height / 2) / ky,
    };
    const nextBounds =
      shownLayout === 'boxes'
        ? boxes.reduce(
            (acc, entry) => {
              const f = fitSize(
                cell,
                videos ? mainSize : { width: entry.width || 1, height: entry.height || 1 },
                12,
              );
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
      if (
        target?.closest('.compare-pane') &&
        !target.closest('input, textarea, select, [contenteditable]')
      ) {
        if (event.key === ' ' && !target.closest('button')) {
          event.preventDefault();
          togglePlayback();
          return;
        }
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault();
          sync?.step(event.key === 'ArrowLeft' ? -1 : 1);
          updateTransport();
          return;
        }
      }
    }
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('[aria-modal="true"], [role="listbox"]')) return;
    close();
  }
</script>

<svelte:window on:keydown={windowKey} />

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
                  ? state(item, sources).error || 'Loading…'
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
              <!-- svelte-ignore a11y_media_has_caption -->
              <video
                use:videoPlayer={item.key}
                src={state(item, sources).url || undefined}
                aria-label={`${index + 1}: ${item.label}`}
                muted={index > 0 || muted}
                playsinline
                preload="auto"
                style={index ? `clip-path: inset(0 0 0 ${geometry.imagePercent}%)` : ''}
                on:error={() => videoError(item, index)}
              ></video>
            {/each}
          {:else}
            <img
              class="source-image"
              src={items[0].preview_data_url}
              alt={`1: ${items[0].label}`}
              draggable="false"
            />
            {#if currentCrop(items[0], 0, desiredRegions)}
              <img
                class="region-image"
                style={cropStyle(items[0])}
                src={items[0].region?.data_url}
                alt=""
                draggable="false"
              />
            {/if}
            <img
              class="result-image"
              style={`clip-path: inset(0 0 0 ${geometry.imagePercent}%)`}
              src={items[1].preview_data_url}
              alt={`2: ${items[1].label}`}
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
                  draggable="false"
                />
              </div>
            {/if}
          {/if}
        </div>
        {#each items.slice(0, 2) as item, index (item.key)}
          {#if videos ? state(item, sources).status !== 'ready' || !state(item, sources).loaded : item.status !== 'ready'}<div
              class="slot-state"
              class:right-state={index === 1}
              role="status"
            >
              {item.label}: {(videos ? state(item, sources).status : item.status) === 'loading'
                ? 'Loading…'
                : item.status === 'missing'
                  ? 'File missing'
                  : videos
                    ? state(item, sources).error || 'Loading…'
                    : item.error || 'Preview unavailable'}
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
                {#if videos ? state(item, sources).status !== 'ready' || !state(item, sources).loaded : item.status !== 'ready'}<div
                    class="slot-state"
                    role="status"
                  >
                    {(videos ? state(item, sources).status : item.status) === 'loading'
                      ? 'Loading…'
                      : item.status === 'missing'
                        ? 'File missing'
                        : videos
                          ? state(item, sources).error || 'Loading…'
                          : item.error || 'Preview unavailable'}
                  </div>{/if}
                <div
                  class="image-stage"
                  class:video-stage={videos}
                  style={boxStyle(item, fitted, panX, panY, zoom, cell)}
                >
                  {#if videos}
                    <!-- svelte-ignore a11y_media_has_caption -->
                    <video
                      use:videoPlayer={item.key}
                      src={state(item, sources).url || undefined}
                      aria-label={`${index + 1}: ${item.label}`}
                      muted={index > 0 || muted}
                      playsinline
                      preload="auto"
                      on:error={() => videoError(item, index)}
                    ></video>
                  {:else}
                    <img
                      src={item.preview_data_url}
                      alt={`${index + 1}: ${item.label}`}
                      draggable="false"
                    />
                    {#if currentCrop(item, index, desiredRegions)}
                      <img
                        class="region-image"
                        style={cropStyle(item)}
                        src={item.region?.data_url}
                        alt=""
                        draggable="false"
                      />
                    {/if}
                  {/if}
                </div>
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
          {muted}
          mute={() => {
            muted = !muted;
            sync?.mute(muted);
          }}
        />
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
        </div>
      </div>
      {#each items.filter((item) => state(item, sources).note) as item (item.key)}<span
          class="compare-slots-note">{state(item, sources).note}</span
        >{/each}
    {/if}
  </div>
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
  .compare-pane > div:last-child {
    position: relative;
    min-height: 0;
  }
  .compare-pane > .video-comparison {
    position: relative;
    inset: auto;
  }
  .compare-pane > div:last-child:not(.video-comparison) > .compare-well {
    height: 100%;
  }
  .compare-pane {
    grid-template-rows: 52px auto 1fr;
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
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    background: #1b1b1d;
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
    background: var(--stage);
  }
  .box-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    padding: 0 10px;
    background: #1b1b1d;
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
