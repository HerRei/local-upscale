<script lang="ts">
  import { onMount, tick } from 'svelte';
  import VideoComparison from './VideoComparison.svelte';
  import * as api from './lib/api';
  import { comparisonFromKey, comparisonFromPointer } from './lib/comparison';
  import {
    applyWorkerEnvelope,
    choosePresetModel,
    formatBytes,
    formatDuration,
    modelsForTask,
    resultPreviewForSelectedMedia
  } from './lib/state';
  import { demoSnapshot } from './lib/demo';
  import {
    boundedPreviewSize,
    canvasTileRect,
    tilePercentages,
    type OutputTile
  } from './lib/progressive-preview';
  import {
    clampPan,
    fitSize,
    panBounds,
    pointerCenteredPan,
    zoomLimits
  } from './lib/viewport';
  import type {
    AppSnapshot,
    BenchmarkDeviceResult,
    CatalogModel,
    CatalogVideoModel,
    IntegrationStatus,
    LaunchIntent,
    Recipe,
    StartBatchInput,
    TaskKind,
    UiSettings,
    VideoComparisonSources,
    WorkerEnvelope
  } from './lib/types';

  let snapshot: AppSnapshot = demoSnapshot();
  let booting = true;
  let page: 'media' | 'preview' | 'enhance' = 'preview';
  let advanced = false;
  let recipeEditorOpen = false;
  let recipeName = '';
  let termsAccepted = false;
  let faceTermsAccepted = false;
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
  let inspectorScroll: HTMLDivElement | undefined;
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
  let lastLivePreviewSequence = 0;
  let activeTileVisible = false;
  let activeTileX = 0;
  let activeTileY = 0;
  let activeTileWidth = 0;
  let activeTileHeight = 0;
  let diagnostics = '';
  let integration: IntegrationStatus | null = null;
  let modalKind: 'message' | 'performance' | 'integrations' = 'message';
  let modalTitle = '';
  let modalMessage = '';
  let stateRefresh: ReturnType<typeof setTimeout> | undefined;
  let runtimePulseTimer: ReturnType<typeof setTimeout> | undefined;
  let pendingRuntimePulse: WorkerEnvelope | undefined;
  let autoStartTimer: ReturnType<typeof setTimeout> | undefined;
  let pendingAutoStartIds: string[] = [];
  let stagedAutoStartIds: string[] = [];
  let handlingLaunchIntents = false;
  let lastImageTask: Exclude<TaskKind, 'video'> = 'upscale';
  let videoComparison: VideoComparisonSources | null = null;
  let videoComparisonKey = '';
  let videoComparisonError = '';
  let livePreviewWarning = '';

  $: settings = snapshot.settings;
  $: selectedMedia = snapshot.media.find((media) => media.selected) ?? snapshot.media[0];
  $: compatibleModels = modelsForTask(snapshot, settings.task);
  $: selectedModel = compatibleModels.find((model) => model.model_id === settings.selected_model_id);
  $: selectedVideoModel = snapshot.catalog.video_models.find(
    (model) => model.model_id === settings.selected_video_model_id
  );
  $: faceModel = snapshot.catalog.models.find(
    (model) => model.model_id === selectedModel?.pair_with && model.purposes.includes('face')
  );
  $: preprocessModels = snapshot.catalog.models.filter(
    (model) => model.native_scale === 1 && !model.purposes.includes('face')
  );
  $: preprocessModel = preprocessModels.find(
    (model) => model.model_id === settings.preprocess_model_id
  );
  $: faceEngineAvailable = snapshot.engine?.features.includes('face_aware') === true;
  $: faceEnabled = settings.enable_face_model && faceEngineAvailable;
  $: usingTemporalVideo = settings.task === 'video' && settings.selected_video_model_id !== 'frame_by_frame';
  $: activeDownload = snapshot.runtime.download_model_id;
  $: benchmarkRunning = snapshot.runtime.active_job_id.startsWith('benchmark-');
  $: customModelNeedsOptIn =
    settings.selected_model_id === '__custom__' &&
    Boolean(settings.custom_model_path) &&
    !settings.custom_model_path.toLowerCase().endsWith('.safetensors');
  $: modelReady = usingTemporalVideo
    ? Boolean(selectedVideoModel?.installed)
    : settings.selected_model_id === '__custom__'
      ? Boolean(settings.custom_model_path) &&
        (!customModelNeedsOptIn || settings.allow_unsafe_pickle_model)
      : Boolean(selectedModel?.installed);
  $: faceReady = !faceEnabled || Boolean(faceModel?.installed);
  $: preprocessReady =
    settings.task !== 'upscale' ||
    !settings.preprocess_model_id ||
    Boolean(preprocessModel?.installed);
  $: mediaMatchesTask = selectedMedia
    ? (settings.task === 'video') === (selectedMedia.kind === 'video')
    : false;
  $: inflightMediaIds = new Set(
    snapshot.jobs
      .filter((job) => ['queued', 'starting', 'running', 'cancelling'].includes(job.status))
      .map((job) => job.media_id)
  );
  $: activeJobMediaId = snapshot.jobs.find(
    (job) => job.id === snapshot.runtime.active_job_id
  )?.media_id ?? '';
  $: runnableMedia = settings.task
    ? snapshot.media.filter(
        (media) => (settings.task === 'video') === (media.kind === 'video')
      )
    : [];
  $: queueableMedia = runnableMedia.filter((media) => !inflightMediaIds.has(media.id));
  $: queueSelection = settings.batch_mode
    ? queueableMedia
    : selectedMedia && mediaMatchesTask && !inflightMediaIds.has(selectedMedia.id)
      ? [selectedMedia]
      : [];
  $: canQueue =
    snapshot.runtime.worker === 'ready' &&
    Boolean(settings.task) &&
    queueSelection.length > 0 &&
    modelReady &&
    faceReady &&
    preprocessReady &&
    snapshot.runtime.status_title !== 'Cancelling';
  $: canStart = !snapshot.runtime.active_job_id && canQueue;
  $: canAppendToQueue = Boolean(snapshot.runtime.active_job_id) && canQueue;
  $: queuedCount = snapshot.jobs.filter((job) => job.status === 'queued').length;
  $: sourcePreview = selectedMedia?.preview_data_url ?? '';
  // Keep the full preview bound to the same persisted data URL as the media
  // thumbnail. Removing the image node after a transient WebKit decode error
  // left the packaged app in an endless re-probe/loading loop.
  $: renderableSourcePreview = sourcePreview;
  $: resultPreview = resultPreviewForSelectedMedia(snapshot);
  $: completedVideoOutput = selectedMedia?.kind === 'video'
    ? snapshot.jobs.find(
        (job) =>
          job.media_id === selectedMedia.id &&
          job.status === 'completed' &&
          Boolean(job.output_path)
      )?.output_path ?? ''
    : '';
  $: desiredVideoComparisonKey =
    selectedMedia?.kind === 'video' && completedVideoOutput
      ? `${selectedMedia.id}:${completedVideoOutput}`
      : '';
  $: if (desiredVideoComparisonKey !== videoComparisonKey) {
    void loadVideoComparison(desiredVideoComparisonKey);
  }
  $: outputDimensions = selectedMedia
    ? settings.task === 'denoise'
      ? `${selectedMedia.width} × ${selectedMedia.height} · original size`
      : `${selectedMedia.width * settings.output_scale} × ${selectedMedia.height * settings.output_scale} · ${settings.output_scale}×`
    : '—';
  $: currentDownloadTarget = usingTemporalVideo ? selectedVideoModel : selectedModel;
  $: activeDevice = snapshot.capabilities.devices.find(
    (device) => device.id === settings.device_id
  );
  $: singleScopeMessage = settings.batch_mode
    ? queueableMedia.length === 0 && runnableMedia.length > 0
      ? `All ${runnableMedia.length} compatible item${runnableMedia.length === 1 ? ' is' : 's are'} already running or queued.`
      : runnableMedia.length === snapshot.media.length
        ? `${queueableMedia.length} compatible item${queueableMedia.length === 1 ? '' : 's'} can be added to the queue.`
        : `${queueableMedia.length} ${settings.task === 'video' ? 'video' : 'image'} item${queueableMedia.length === 1 ? '' : 's'} can be queued with this setup; select the other media type to configure it separately.`
    : !selectedMedia
      ? 'Single processes only the selected item.'
      : mediaMatchesTask
        ? `Single processes only “${selectedMedia.name}”.`
        : `“${selectedMedia.name}” does not match ${settings.task || 'the chosen task'}; select a compatible item.`;

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
    let unlistenWorker = () => {};
    let unlistenState = () => {};
    let unlistenMenu = () => {};
    let unlistenLaunchIntent = () => {};
    let disposed = false;
    const resizeObserver =
      typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(() => updateViewport());
    if (canvasWell) resizeObserver?.observe(canvasWell);
    scheduleViewportReset();

    void (async () => {
      try {
        snapshot = await api.bootstrap();
        await tick();
        ensureTaskMatchesSelection();
        chooseInitialModel();
        unlistenWorker = await api.listenForWorker(handleWorkerMessage);
        unlistenState = await api.listenForStateChange(scheduleRefresh);
        unlistenMenu = await api.listenForNativeMenu((action) => void handleNativeMenuAction(action));
        unlistenLaunchIntent = await api.listenForLaunchIntent(() => void consumeLaunchIntents());
        await consumeLaunchIntents();
      } catch (error) {
        showModal('Could not start LocalSR', String(error));
      } finally {
        booting = false;
      }
    })();

    return () => {
      disposed = true;
      if (stateRefresh) clearTimeout(stateRefresh);
      if (runtimePulseTimer) clearTimeout(runtimePulseTimer);
      if (autoStartTimer) clearTimeout(autoStartTimer);
      if (!disposed) return;
      unlistenWorker();
      unlistenState();
      unlistenMenu();
      unlistenLaunchIntent();
      resizeObserver?.disconnect();
      progressiveGeneration += 1;
      if (api.isTauri()) void api.clearVideoComparison();
    };
  });

  function scheduleRefresh(): void {
    if (stateRefresh) clearTimeout(stateRefresh);
    stateRefresh = setTimeout(() => void refresh(), 80);
  }

  async function refresh(): Promise<void> {
    if (!api.isTauri()) return;
    try {
      const latest = await api.refreshSnapshot();
      // Preserve a progressive worker preview only while the same active job
      // is being refreshed. Carrying it across media changes would compare a
      // newly selected source with an unrelated previous result.
      if (
        !latest.runtime.result_preview_data_url &&
        latest.runtime.active_job_id &&
        latest.runtime.active_job_id === snapshot.runtime.active_job_id
      ) {
        latest.runtime.result_preview_data_url = snapshot.runtime.result_preview_data_url;
      }
      snapshot = latest;
      chooseInitialModel();
      await maybeAutoStart();
    } catch (error) {
      console.error('Could not refresh LocalSR state', error);
    }
  }

  function handleWorkerMessage(message: WorkerEnvelope): void {
    // Tile traffic is display-only and can arrive thousands of times. Do not
    // invalidate the full Svelte snapshot for it; only update the live canvas.
    if (message.type === 'tile_update') {
      const jobId = String(message.data.job_id ?? '');
      if (activeJobMediaId && selectedMedia?.id !== activeJobMediaId) return;
      queueProgressiveTile(message);
      return;
    }
    if (message.type === 'live_preview_frame') {
      const jobId = String(message.data.job_id ?? '');
      const sequence = Number(message.data.sequence ?? 0);
      if (
        jobId !== snapshot.runtime.active_job_id ||
        (activeJobMediaId && selectedMedia?.id !== activeJobMediaId) ||
        sequence <= lastLivePreviewSequence
      ) return;
      lastLivePreviewSequence = sequence;
      if (String(message.data.preview_kind ?? '') === 'tile') {
        queueProgressiveTile({
          type: 'tile_update',
          data: { ...message.data, phase: 'completed' }
        });
      } else {
        snapshot = applyWorkerEnvelope(snapshot, message);
      }
      return;
    }
    if (message.type === 'live_preview_warning') {
      livePreviewWarning = String(
        message.data.message ?? 'A sampled enhanced preview could not be displayed.'
      );
      return;
    }

    if (
      message.type === 'progress' ||
      message.type === 'video_frame_started' ||
      message.type === 'video_frame_completed' ||
      message.type === 'benchmark_progress'
    ) {
      queueRuntimePulse(message);
      return;
    }

    discardRuntimePulse();

    snapshot = applyWorkerEnvelope(snapshot, message);
    if (message.type === 'job_started') {
      livePreviewWarning = '';
      resetView();
      resetProgressivePreview(String(message.data.job_id ?? ''));
    } else if (message.type === 'job_completed' || message.type === 'video_job_completed') {
      livePreviewWarning = '';
      resetProgressivePreview();
      resetView();
    } else if (message.type === 'job_cancelled' || message.type === 'job_failed') {
      livePreviewWarning = '';
      resetProgressivePreview();
    }
    if (message.type === 'media_info') {
      if (String(message.data.media_path ?? '') === selectedMedia?.path) {
        previewRetryMediaId = '';
      }
      scheduleRefresh();
    } else if (message.type === 'media_probe_failed') {
      scheduleRefresh();
    }
    if (message.type === 'job_completed' || message.type === 'video_job_completed') {
      scheduleRefresh();
    }
  }

  function queueRuntimePulse(message: WorkerEnvelope): void {
    pendingRuntimePulse = message;
    if (runtimePulseTimer) return;
    runtimePulseTimer = setTimeout(() => {
      runtimePulseTimer = undefined;
      const latest = pendingRuntimePulse;
      pendingRuntimePulse = undefined;
      if (!latest) return;
      const jobId = String(latest.data.job_id ?? '');
      if (jobId && jobId !== snapshot.runtime.active_job_id) return;
      snapshot = applyWorkerEnvelope(snapshot, latest);
    }, 100);
  }

  function discardRuntimePulse(): void {
    if (runtimePulseTimer) clearTimeout(runtimePulseTimer);
    runtimePulseTimer = undefined;
    pendingRuntimePulse = undefined;
  }

  function chooseInitialModel(): void {
    const task = snapshot.settings.task;
    if (!task || snapshot.settings.selected_model_id) return;
    const first = modelsForTask(snapshot, task)[0];
    if (first) updateSettings({ selected_model_id: first.model_id });
  }

  function updateSettings(patch: Partial<UiSettings>): void {
    snapshot = { ...snapshot, settings: { ...snapshot.settings, ...patch } };
    if (api.isTauri()) {
      void api.saveSettings(snapshot.settings).catch((error) =>
        showModal('Could not save settings', String(error))
      );
    }
  }

  function setTask(task: TaskKind): void {
    if (selectedMedia && (task === 'video') !== (selectedMedia.kind === 'video')) return;
    if (task !== 'video') lastImageTask = task;
    const models = modelsForTask(snapshot, task);
    const chosen = models.find((model) => model.model_id === settings.selected_model_id) ?? models[0];
    updateSettings({
      task,
      selected_model_id: chosen?.model_id ?? '',
      preprocess_model_id: task === 'upscale' ? settings.preprocess_model_id : '',
      output_scale:
        task === 'denoise'
          ? 1
          : Math.min(chosen?.native_scale ?? 4, Math.max(2, settings.output_scale)),
      enable_face_model: false
    });
    termsAccepted = false;
    faceTermsAccepted = false;
  }

  function ensureTaskMatchesSelection(): void {
    if (!selectedMedia) return;
    if (selectedMedia.kind === 'video' && settings.task !== 'video') {
      if (settings.task === 'upscale' || settings.task === 'denoise') lastImageTask = settings.task;
      setTask('video');
    } else if (selectedMedia.kind === 'image' && (settings.task === 'video' || !settings.task)) {
      setTask(lastImageTask);
    }
  }

  async function selectQueueMedia(id: string): Promise<void> {
    if (settings.task === 'upscale' || settings.task === 'denoise') lastImageTask = settings.task;
    await api.selectMedia(id);
    await refresh();
    ensureTaskMatchesSelection();
    page = 'preview';
  }

  async function addFiles(replace = false): Promise<void> {
    if (!api.isTauri()) {
      showModal('Desktop runtime required', 'Run “npm run tauri dev” to choose local media.');
      return;
    }
    const paths = await api.chooseMediaFiles();
    if (!paths.length) return;
    await api.addMedia(paths, replace);
    page = 'preview';
    await refresh();
    ensureTaskMatchesSelection();
  }

  function setBatchMode(batchMode: boolean): void {
    if (snapshot.runtime.active_job_id || settings.batch_mode === batchMode) return;
    updateSettings({ batch_mode: batchMode });
  }

  async function addFolder(): Promise<void> {
    const paths = await api.chooseMediaFolder();
    if (!paths.length) return;
    await api.addMedia(paths, false);
    updateSettings({ batch_mode: true });
    page = 'preview';
    await refresh();
    ensureTaskMatchesSelection();
  }

  async function chooseOutput(): Promise<void> {
    const path = await api.chooseOutputDirectory(settings.output_directory);
    if (path) updateSettings({ output_directory: path });
  }

  async function chooseCustomModel(): Promise<void> {
    const path = await api.chooseCustomModel();
    if (path) {
      updateSettings({
        selected_model_id: '__custom__',
        custom_model_path: path,
        allow_unsafe_pickle_model: false,
        enable_face_model: false
      });
      termsAccepted = false;
      faceTermsAccepted = false;
    }
  }

  function selectVideoEngine(modelId: string): void {
    updateSettings({ selected_video_model_id: modelId, enable_face_model: false });
    termsAccepted = false;
    faceTermsAccepted = false;
  }

  function selectPrimaryModel(modelId: string): void {
    const model = snapshot.catalog.models.find((candidate) => candidate.model_id === modelId);
    updateSettings({
      selected_model_id: modelId,
      output_scale: model ? Math.min(model.native_scale, Math.max(2, settings.output_scale)) : settings.output_scale,
      allow_unsafe_pickle_model: false,
      enable_face_model: false
    });
    termsAccepted = false;
    faceTermsAccepted = false;
  }

  function applyPreset(kind: 'quick' | 'best'): void {
    if (!settings.task) {
      showModal('Choose a task', 'Select Upscale, Denoise, or Video before applying a recipe.');
      return;
    }
    const chosen = choosePresetModel(snapshot, settings.task, kind);
    if (!chosen) return;
    updateSettings({
      selected_model_id: chosen.model_id,
      selected_video_model_id: 'frame_by_frame',
      preprocess_model_id: '',
      output_scale: settings.task === 'denoise' ? 1 : chosen.native_scale,
      halo: chosen.recommended_halo,
      safe_memory: kind === 'best' ? settings.safe_memory : false,
      enable_face_model: false
    });
    termsAccepted = false;
    faceTermsAccepted = false;
  }

  async function runDownload(
    target: CatalogModel | CatalogVideoModel | undefined = currentDownloadTarget,
    accepted = termsAccepted
  ): Promise<void> {
    if (!target) return;
    if (activeDownload === target.model_id) {
      await api.cancelDownload(target.model_id);
      return;
    }
    if (target.terms_acceptance_required && !accepted) {
      showModal('Review the model terms', 'Confirm the model-specific license before downloading.');
      return;
    }
    try {
      if (!target.automated_download_allowed) {
        const sourcePath = await api.chooseCustomModel();
        if (!sourcePath) return;
        await api.importCatalogModel(target.model_id, sourcePath, accepted);
      } else {
        await api.downloadModel(target.model_id, accepted);
      }
      await refresh();
    } catch (error) {
      showModal(target.automated_download_allowed ? 'Download failed' : 'Import failed', String(error));
    }
  }

  async function start(mediaIds?: string[]): Promise<void> {
    if ((!mediaIds && !canQueue) || !selectedMedia || !settings.task) return;
    const ids = mediaIds ?? queueSelection.map((media) => media.id);
    if (!ids.length) return;
    const input: StartBatchInput = {
      media_ids: ids,
      batch_mode: mediaIds ? ids.length > 1 : settings.batch_mode,
      task: settings.task,
      model_id: settings.selected_model_id,
      video_model_id: settings.selected_video_model_id,
      preprocess_model_id: settings.task === 'upscale' ? settings.preprocess_model_id : '',
      custom_model_path: settings.custom_model_path,
      output_directory: settings.output_directory,
      output_format: settings.output_format,
      output_scale: settings.output_scale,
      preserve_metadata: settings.preserve_metadata,
      jpeg_quality: settings.jpeg_quality,
      device: settings.device_id,
      tile_size: settings.tile_size,
      halo: settings.halo,
      precision: settings.precision,
      safe_memory: settings.safe_memory,
      deflicker: settings.deflicker,
      deflicker_window: settings.deflicker_window,
      video_container: settings.video_container,
      video_crf: settings.video_crf,
      enable_face_model: faceEnabled,
      face_fidelity: settings.face_fidelity,
      enable_live_preview: settings.enable_live_preview,
      allow_unsafe_pickle_model: settings.allow_unsafe_pickle_model
    };
    try {
      await api.startJobs(input);
      await refresh();
    } catch (error) {
      showModal('Could not start', String(error));
    }
  }

  async function addRecipe(): Promise<void> {
    const name = recipeName.trim() || `My Recipe ${snapshot.recipes.length + 1}`;
    if (!settings.task) return;
    const recipe: Recipe = {
      id: crypto.randomUUID(),
      name,
      task: settings.task,
      model_id: settings.selected_model_id,
      output_scale: settings.output_scale,
      tile_size: settings.tile_size,
      halo: settings.halo,
      precision: settings.precision,
      safe_memory: settings.safe_memory,
      video_model_id: settings.selected_video_model_id,
      custom_model_path:
        settings.selected_model_id === '__custom__' && settings.custom_model_path.toLowerCase().endsWith('.safetensors')
          ? settings.custom_model_path
          : '',
      output_format: settings.output_format,
      preserve_metadata: settings.preserve_metadata,
      jpeg_quality: settings.jpeg_quality,
      deflicker: settings.deflicker,
      deflicker_window: settings.deflicker_window,
      video_container: settings.video_container,
      video_crf: settings.video_crf,
      enable_face_model: faceEnabled,
      face_fidelity: settings.face_fidelity,
      stages: [
        ...(settings.task === 'upscale' && preprocessModel
          ? [{ kind: preprocessModel.model_id === 'fbcnn_color' ? 'deblock' as const : 'restore' as const, model_id: preprocessModel.model_id }]
          : []),
        {
          kind: settings.task === 'video' ? 'video' as const : settings.task === 'denoise' ? 'restore' as const : 'upscale' as const,
          model_id: settings.task === 'video' && usingTemporalVideo ? settings.selected_video_model_id : settings.selected_model_id
        },
        ...(faceEnabled && faceModel
          ? [{ kind: 'face_restore' as const, model_id: faceModel.model_id, fidelity: settings.face_fidelity / 100 }]
          : [])
      ]
    };
    await api.saveRecipe(recipe);
    recipeName = '';
    recipeEditorOpen = false;
    await refresh();
  }

  async function openRecipeEditor(): Promise<void> {
    recipeEditorOpen = true;
    await tick();
    inspectorScroll?.querySelector<HTMLInputElement>('.recipe-input input')?.focus();
  }

  function applyRecipe(recipe: Recipe): void {
    if (selectedMedia && (recipe.task === 'video') !== (selectedMedia.kind === 'video')) {
      showModal(
        'Recipe does not match this media',
        `Select a ${recipe.task === 'video' ? 'video' : 'photo'} before applying “${recipe.name}”.`
      );
      return;
    }
    if (recipe.task !== 'video') lastImageTask = recipe.task;
    const recipePreprocess = recipe.task === 'upscale'
      ? recipe.stages?.find((stage) => stage.kind === 'deblock' || stage.kind === 'restore')
      : undefined;
    const recipeFace = recipe.stages?.find((stage) => stage.kind === 'face_restore');
    const recipeModel = snapshot.catalog.models.find((model) => model.model_id === recipe.model_id);
    updateSettings({
      task: recipe.task,
      selected_model_id: recipe.model_id,
      selected_video_model_id: recipe.video_model_id || 'frame_by_frame',
      preprocess_model_id: recipePreprocess?.model_id ?? '',
      custom_model_path: recipe.custom_model_path ?? '',
      output_scale: recipe.task === 'denoise'
        ? 1
        : Math.min(recipeModel?.native_scale ?? recipe.output_scale, recipe.output_scale),
      output_format: recipe.output_format ?? settings.output_format,
      preserve_metadata: recipe.preserve_metadata ?? settings.preserve_metadata,
      jpeg_quality: recipe.jpeg_quality ?? settings.jpeg_quality,
      tile_size: recipe.tile_size,
      halo: recipe.halo,
      precision: recipe.precision,
      safe_memory: recipe.safe_memory,
      deflicker: recipe.deflicker ?? settings.deflicker,
      deflicker_window: recipe.deflicker_window ?? settings.deflicker_window,
      video_container: recipe.video_container || settings.video_container,
      video_crf: recipe.video_crf ?? settings.video_crf,
      enable_face_model: recipeFace ? true : recipe.enable_face_model ?? false,
      face_fidelity: recipeFace?.fidelity === undefined
        ? recipe.face_fidelity ?? 70
        : Math.round(recipeFace.fidelity * 100),
      allow_unsafe_pickle_model: false
    });
    termsAccepted = false;
    faceTermsAccepted = false;
  }

  async function consumeLaunchIntents(): Promise<void> {
    if (!api.isTauri() || handlingLaunchIntents) return;
    handlingLaunchIntents = true;
    try {
      while (true) {
        const intents = await api.takeLaunchIntents();
        if (!intents.length) break;
        for (const intent of intents) await applyLaunchIntent(intent);
      }
    } catch (error) {
      showModal('Could not open requested media', String(error));
    } finally {
      handlingLaunchIntents = false;
    }
  }

  async function applyLaunchIntent(intent: LaunchIntent): Promise<void> {
    const previousIds = new Set(snapshot.media.map((media) => media.id));
    if (intent.files.length) {
      await api.addMedia(intent.files, false);
      page = 'preview';
      await refresh();
    }
    const added = snapshot.media.filter((media) => !previousIds.has(media.id));
    if (added.length) {
      await api.selectMedia(added[0].id);
      await refresh();
    }

    // Native/file-manager launches must reconcile persisted task state even
    // when the caller did not provide a preset. Otherwise a newly opened
    // photo could leave the disabled Video card selected (and vice versa).
    ensureTaskMatchesSelection();
    await tick();
    if (intent.preset) applyPreset(intent.preset);
    if (intent.recipe) {
      const requested = snapshot.recipes.find(
        (recipe) => recipe.name.toLocaleLowerCase() === intent.recipe?.toLocaleLowerCase()
      );
      if (!requested) {
        showModal('Recipe not found', `No saved recipe named “${intent.recipe}” exists.`);
        continueWithoutAutoStart();
        return;
      }
      applyRecipe(requested);
    }
    await api.saveSettings(snapshot.settings);

    if (!intent.auto_start) return;
    const candidates = added.length
      ? added
      : settings.batch_mode
        ? runnableMedia
        : selectedMedia
          ? [selectedMedia]
          : [];
    if (!candidates.length) {
      showModal('Nothing to start', 'The launch request did not contain compatible media.');
      return;
    }
    if (new Set(candidates.map((media) => media.kind)).size > 1) {
      showModal(
        'Mixed media needs two runs',
        'Images and videos use different tasks. They were added safely; choose one media type and start it from LocalSR.'
      );
      return;
    }
    if (candidates.length > 1) {
      updateSettings({ batch_mode: true });
      await tick();
      await api.saveSettings(snapshot.settings);
    }
    stagedAutoStartIds = Array.from(
      new Set([...stagedAutoStartIds, ...candidates.map((media) => media.id)])
    );
    if (stagedAutoStartIds.length > 1 && !settings.batch_mode) {
      updateSettings({ batch_mode: true });
    }
    if (autoStartTimer) clearTimeout(autoStartTimer);
    // File managers may forward a multi-selection as several rapid launches.
    // Debouncing collects them into one native queue transaction.
    autoStartTimer = setTimeout(() => {
      autoStartTimer = undefined;
      pendingAutoStartIds = [...stagedAutoStartIds];
      stagedAutoStartIds = [];
      void maybeAutoStart();
    }, 300);
  }

  async function maybeAutoStart(): Promise<void> {
    await tick();
    if (!pendingAutoStartIds.length) return;
    const media = snapshot.media.filter((item) => pendingAutoStartIds.includes(item.id));
    if (media.length !== pendingAutoStartIds.length) return;
    if (media.some((item) => item.probe_status === 'pending')) return;
    if (media.some((item) => item.probe_status === 'failed')) {
      pendingAutoStartIds = [];
      showModal('Could not inspect requested media', 'At least one file could not be read, so automatic processing was cancelled.');
      return;
    }
    if (new Set(media.map((item) => item.kind)).size > 1) {
      pendingAutoStartIds = [];
      showModal(
        'Mixed media needs two runs',
        'Images and videos use different tasks. They remain in the media list; choose one type and start it from LocalSR.'
      );
      return;
    }
    if (media.some((item) => (settings.task === 'video') !== (item.kind === 'video'))) {
      pendingAutoStartIds = [];
      showModal(
        'Task does not match the requested media',
        'The files remain in LocalSR. Choose a compatible task or recipe, then start them manually.'
      );
      return;
    }
    const ready =
      media.every((item) => item.probe_status === 'ready') &&
      snapshot.runtime.worker === 'ready' &&
      !snapshot.runtime.active_job_id &&
      Boolean(settings.task) &&
      modelReady &&
      faceReady &&
      preprocessReady;
    if (!ready) return;
    const mediaIds = [...pendingAutoStartIds];
    pendingAutoStartIds = [];
    await start(mediaIds);
  }

  function continueWithoutAutoStart(): void {
    pendingAutoStartIds = [];
    stagedAutoStartIds = [];
    if (autoStartTimer) clearTimeout(autoStartTimer);
  }

  async function handleNativeMenuAction(action: string): Promise<void> {
    try {
      if (action.startsWith('preset.recipe.')) {
        const id = action.slice('preset.recipe.'.length);
        const recipe = snapshot.recipes.find((candidate) => candidate.id === id);
        if (recipe) applyRecipe(recipe);
        return;
      }
      switch (action) {
        case 'file.add-media':
          await addFiles(false);
          break;
        case 'file.add-folder':
          await addFolder();
          break;
        case 'file.open-output':
          await api.openOutputDirectory();
          break;
        case 'file.clear':
          await api.clearMedia();
          await refresh();
          break;
        case 'file.start':
          if (canStart) await start();
          else showModal('Cannot start yet', singleScopeMessage);
          break;
        case 'file.cancel':
          await api.cancelJobs();
          break;
        case 'file.integrations':
          await showIntegrations();
          break;
        case 'preset.quick':
          applyPreset('quick');
          break;
        case 'preset.best':
          applyPreset('best');
          break;
        case 'view.fit':
          resetView();
          break;
        case 'view.actual':
          setZoom(actualPixelZoom);
          break;
        case 'view.refresh-hardware':
          await api.refreshCapabilities();
          scheduleRefresh();
          break;
        case 'view.performance':
          showPerformance();
          break;
      }
    } catch (error) {
      showModal('Native action failed', String(error));
    }
  }

  function showPerformance(): void {
    showModal(
      'Performance & Diagnostics',
      'Live values come from the isolated inference worker. Temperature is shown only when a supported backend reports it.',
      'performance'
    );
  }

  function benchmarkDeviceBarWidth(
    score: number,
    devices: BenchmarkDeviceResult[]
  ): number {
    const fastest = Math.max(1, ...devices.map((device) => device.score));
    return Math.max(5, Math.min(100, (score / fastest) * 100));
  }

  function benchmarkSceneLabel(sceneId: string): string {
    switch (sceneId) {
      case 's1-classroom':
        return 'Classroom';
      case 's2-gallery':
        return 'Gallery · tiled';
      case 's3-gallery-encode':
        return 'Gallery · encode';
      default:
        return sceneId;
    }
  }

  async function showIntegrations(): Promise<void> {
    integration = await api.integrationStatus();
    showModal('System Integrations', integration.summary, 'integrations');
  }

  async function changeIntegrations(install: boolean): Promise<void> {
    try {
      integration = install ? await api.installIntegrations() : await api.uninstallIntegrations();
      modalMessage = integration.summary;
    } catch (error) {
      modalMessage = String(error);
    }
  }

  async function copyDiagnostics(): Promise<void> {
    try {
      diagnostics = await api.diagnosticSummary();
      await navigator.clipboard.writeText(diagnostics);
      showModal('Diagnostics copied', 'Private file paths and media names were intentionally omitted.');
    } catch (error) {
      showModal('Diagnostics', diagnostics || String(error));
    }
  }

  async function runBenchmark(): Promise<void> {
    try {
      await api.startBenchmark(settings.device_id);
      await refresh();
    } catch (error) {
      showModal('Benchmark could not start', String(error), 'performance');
    }
  }

  async function copyBenchmark(): Promise<void> {
    if (!snapshot.latest_benchmark) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(snapshot.latest_benchmark, null, 2));
      modalMessage = 'Benchmark JSON copied. The result remains local unless you share it.';
    } catch (error) {
      modalMessage = `Could not copy benchmark JSON: ${String(error)}`;
    }
  }

  async function exportBenchmark(): Promise<void> {
    try {
      if (await api.exportBenchmark()) {
        modalMessage = 'Benchmark JSON exported. LocalSR never uploads benchmark data.';
      }
    } catch (error) {
      modalMessage = `Could not export benchmark JSON: ${String(error)}`;
    }
  }

  function showModal(
    title: string,
    message: string,
    kind: 'message' | 'performance' | 'integrations' = 'message'
  ): void {
    modalTitle = title;
    modalMessage = message;
    modalKind = kind;
  }

  function closeModal(): void {
    modalTitle = '';
    modalMessage = '';
    modalKind = 'message';
    diagnostics = '';
    integration = null;
  }

  function closeFromBackdrop(event: MouseEvent): void {
    if (event.target === event.currentTarget) closeModal();
  }

  function scheduleViewportReset(): void {
    void tick().then(() => {
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

  function resetProgressivePreview(jobId = ''): void {
    progressiveGeneration += 1;
    progressiveJobId = jobId;
    progressiveOutputWidth = 0;
    progressiveOutputHeight = 0;
    progressiveVisible = false;
    progressivePendingCount = 0;
    lastProgressivePaintAt = 0;
    lastActiveTileAt = 0;
    lastLivePreviewSequence = 0;
    activeTileVisible = false;
    progressiveWork = Promise.resolve();
    const context = progressiveCanvas?.getContext('2d');
    if (progressiveCanvas && context) {
      context.clearRect(0, 0, progressiveCanvas.width, progressiveCanvas.height);
    }
  }

  function queueProgressiveTile(message: WorkerEnvelope): void {
    const data = message.data;
    const jobId = String(data.job_id ?? '');
    if (!jobId || jobId !== snapshot.runtime.active_job_id) return;
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
      if (key !== videoComparisonKey || mediaId !== selectedMedia?.id) return;
      videoComparison = sources;
    } catch (error) {
      if (key !== videoComparisonKey) return;
      videoComparisonError = `Completed video comparison is unavailable: ${String(error)}`;
    }
  }

  function resetView(): void {
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

<svelte:head><title>{selectedMedia ? `${selectedMedia.name} — LocalSR` : 'LocalSR'}</title></svelte:head>

<div class="app-shell" class:booting style={`--ui-scale:${settings.interface_scale / 100}`}>
  <header class="toolbar">
    <nav class="compact-nav" aria-label="Workspace">
      <button class:active={page === 'media'} on:click={() => (page = 'media')}>Media <span>{snapshot.media.length}</span></button>
      <button class:active={page === 'preview'} on:click={() => (page = 'preview')}>Preview</button>
      <button class:active={page === 'enhance'} on:click={() => (page = 'enhance')}>Enhance</button>
    </nav>
    <button class="brand-mark" type="button" aria-label="Performance & diagnostics" title="Performance & diagnostics" on:click={showPerformance}><i></i><i></i><i></i></button>
    <div class="toolbar-actions">
      <button class="button compact benchmark-shortcut" type="button" on:click={showPerformance}>Run Benchmark</button>
      <button class="button primary compact add-media" disabled={benchmarkRunning} on:click={() => addFiles()}>＋ Add Media</button>
    </div>
  </header>

  <main class="workspace">
    <aside class="media-pane pane" class:compact-hidden={page !== 'media'}>
      <div class="pane-heading">
        <h1>Media</h1>
        <span>{snapshot.media.length ? `${snapshot.media.length} item${snapshot.media.length === 1 ? '' : 's'}` : 'No media'}</span>
      </div>

      <div class="segmented">
        <button disabled={Boolean(snapshot.runtime.active_job_id)} class:active={!settings.batch_mode} on:click={() => setBatchMode(false)}>Single</button>
        <button disabled={Boolean(snapshot.runtime.active_job_id)} class:active={settings.batch_mode} on:click={() => setBatchMode(true)}>Batch</button>
      </div>
      <p class="mode-scope" class:warning={!settings.batch_mode && Boolean(selectedMedia) && !mediaMatchesTask}>{singleScopeMessage}</p>

      {#if snapshot.media.length === 0}
        <div class="empty-card">
          <div class="empty-plus">＋</div>
          <strong>Add media</strong>
          <p>Images, camera RAW, or video clips</p>
          <button class="button" on:click={() => addFiles(false)}>Choose…</button>
        </div>
      {:else}
        <div class="media-list">
          {#each snapshot.media as media (media.id)}
            <div class="media-row" class:selected={media.selected}>
              <button class="media-select" type="button" aria-current={media.selected ? 'true' : undefined} on:click={() => selectQueueMedia(media.id)}>
                <div class="thumb">
                  {#if media.preview_data_url}<img src={media.preview_data_url} alt="" />{:else}<span>{media.kind === 'video' ? 'VID' : 'IMG'}</span>{/if}
                  {#if media.kind === 'video'}<b>▶</b>{/if}
                </div>
                <div class="media-copy">
                  <strong>{media.name}</strong>
                  <span>{media.probe_status === 'failed' ? media.error : media.width ? `${media.width} × ${media.height}${media.kind === 'video' ? ` · ${formatDuration(media.duration_seconds)}` : ` · ${((media.width * media.height) / 1_000_000).toFixed(1)} MP`}` : 'Inspecting…'}</span>
                  {#if inflightMediaIds.has(media.id)}<small class="queue-state">{snapshot.jobs.find((job) => job.media_id === media.id && ['queued', 'starting', 'running', 'cancelling'].includes(job.status))?.status ?? 'queued'}</small>{/if}
                </div>
              </button>
              <button type="button" class="remove" disabled={Boolean(snapshot.runtime.active_job_id)} aria-label={`Remove ${media.name}`} on:click|stopPropagation={async () => { await api.removeMedia(media.id); await refresh(); }}><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
            </div>
          {/each}
        </div>
      {/if}

      <div class="media-actions">
        <button class="button" disabled={benchmarkRunning} on:click={() => addFiles(false)}>{snapshot.media.length ? 'Add More…' : 'Choose…'}</button>
        <button class="button" disabled={benchmarkRunning} on:click={addFolder}>Add Folder…</button>
        {#if snapshot.media.length}<button class="button danger ghost" disabled={Boolean(snapshot.runtime.active_job_id)} on:click={async () => { await api.clearMedia(); await refresh(); }}>Clear</button>{/if}
      </div>
    </aside>

    <section class="preview-pane" class:compact-hidden={page !== 'preview'}>
      <div class="preview-header">
        <div>
          <strong>{selectedMedia?.name ?? 'Preview'}</strong>
          <span>{selectedMedia ? `${selectedMedia.width || '—'} × ${selectedMedia.height || '—'}${selectedMedia.kind === 'video' ? ' · Video Labs' : ''}` : 'No media selected'}</span>
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
          {#if selectedMedia?.kind === 'video'}<span class="labs-chip">VIDEO · LABS / EXPERIMENTAL</span>{/if}
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

    <aside class="enhance-pane pane" class:compact-hidden={page !== 'enhance'}>
      <div class="pane-heading"><h1>Enhance</h1><span>{settings.task ? 'Manual' : 'Choose a task'}</span></div>
      <div bind:this={inspectorScroll} class="inspector-scroll" role="region" aria-label="Enhancement settings">
        <section class="control-section">
          <span class="eyebrow">TASK</span>
          <div class="task-grid">
            <button disabled={selectedMedia?.kind === 'video'} class:active={settings.task === 'upscale'} on:click={() => setTask('upscale')}><b>Upscale</b><span>Photos and artwork</span></button>
            <button disabled={selectedMedia?.kind === 'video'} class:active={settings.task === 'denoise'} on:click={() => setTask('denoise')}><b>Denoise</b><span>Noise and blur</span></button>
            <button disabled={Boolean(selectedMedia) && selectedMedia.kind !== 'video'} class="video-task" class:active={settings.task === 'video'} on:click={() => setTask('video')}><b>Upscale Video</b><span>Labs / Experimental</span></button>
          </div>
        </section>

        {#if settings.task}
          <section class="control-section recipes">
            <span class="eyebrow">RECIPES</span>
            <div class="recipe-grid">
              <button on:click={() => applyPreset('quick')}><b>Quick</b><span>Fast and efficient</span></button>
              <button on:click={() => applyPreset('best')}><b>Best</b><span>Maximum quality</span></button>
            </div>
            <div class="recipe-tools">
              {#each snapshot.recipes as recipe (recipe.id)}
                <div class="saved-recipe"><button disabled={Boolean(selectedMedia) && (recipe.task === 'video') !== (selectedMedia.kind === 'video')} on:click={() => applyRecipe(recipe)}><b>{recipe.name}</b><span>{recipe.task} · {recipe.output_scale}× · {recipe.precision.toUpperCase()}</span></button><button aria-label={`Delete ${recipe.name}`} on:click={async () => { await api.deleteRecipe(recipe.id); await refresh(); }}><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button></div>
              {/each}
              {#if recipeEditorOpen}
                <div class="recipe-input"><input aria-label="Recipe name" placeholder="Name this setup" bind:value={recipeName} on:keydown={(event) => { if (event.key === 'Enter') void addRecipe(); if (event.key === 'Escape') recipeEditorOpen = false; }} /><button on:click={addRecipe}>Save</button></div>
              {:else}
                <button class="save-recipe" type="button" on:click={openRecipeEditor}>＋ Save current setup as recipe</button>
              {/if}
            </div>
          </section>

          <section class="control-section model-section">
            <label class="eyebrow" for="model-select">MODEL</label>
            {#if settings.task === 'video'}
              <label class="field-label" for="video-engine">Video engine</label>
              <select id="video-engine" value={settings.selected_video_model_id} on:change={(event) => selectVideoEngine(event.currentTarget.value)}>
                <option value="frame_by_frame">Frame-by-frame · compatible</option>
                {#each snapshot.catalog.video_models as model}<option value={model.model_id}>{model.name}{model.installed ? ' · Installed' : ''}</option>{/each}
              </select>
            {/if}

            {#if !usingTemporalVideo}
              <label class="field-label" for="model-select">{settings.task === 'video' ? 'Frame model' : 'Checkpoint'}</label>
              <select id="model-select" value={settings.selected_model_id} on:change={(event) => selectPrimaryModel(event.currentTarget.value)}>
                {#each compatibleModels as model}<option value={model.model_id}>{model.name}{model.installed ? ' · Installed' : ` · ${formatBytes(model.size_bytes)}`}</option>{/each}
                <option value="__custom__">Use my own checkpoint…</option>
              </select>
            {/if}

            {#if settings.selected_model_id === '__custom__' && !usingTemporalVideo}
              <button class="button full" on:click={chooseCustomModel}>{settings.custom_model_path ? 'Choose another checkpoint…' : 'Choose checkpoint…'}</button>
              <p class="path-text">{settings.custom_model_path || 'Safetensors is strongly recommended.'}</p>
              {#if settings.custom_model_path && !settings.custom_model_path.toLowerCase().endsWith('.safetensors')}
                <label class="terms"><input type="checkbox" checked={settings.allow_unsafe_pickle_model} on:change={(event) => updateSettings({ allow_unsafe_pickle_model: event.currentTarget.checked })} /> Allow this pickle-based checkpoint. It may execute code inside the inference worker.</label>
              {/if}
            {:else if currentDownloadTarget}
              <p class="model-description">{currentDownloadTarget.description}</p>
              <div class="license-line">
                <span>{currentDownloadTarget.license_name}</span>
                <span>{currentDownloadTarget.commercial_use_allowed === false ? 'Non-commercial only' : currentDownloadTarget.commercial_use_allowed === null ? 'Commercial terms unclear' : currentDownloadTarget.author}</span>
              </div>
              {#if currentDownloadTarget.terms_acceptance_required && !currentDownloadTarget.installed}
                <label class="terms"><input type="checkbox" bind:checked={termsAccepted} /> I reviewed the model license and restrictions.</label>
              {/if}
              {#if !currentDownloadTarget.installed}
                <button class="button full" class:primary={currentDownloadTarget.automated_download_allowed} disabled={Boolean(activeDownload) && activeDownload !== currentDownloadTarget.model_id} on:click={() => runDownload()}>
                  {activeDownload === currentDownloadTarget.model_id ? `Downloading ${Math.round(snapshot.runtime.download_progress)}% · Cancel` : currentDownloadTarget.automated_download_allowed ? `Download ${formatBytes('size_bytes' in currentDownloadTarget ? currentDownloadTarget.size_bytes : currentDownloadTarget.total_size_bytes)}` : 'Choose externally downloaded checkpoint…'}
                </button>
                {#if activeDownload === currentDownloadTarget.model_id}<div class="download-track"><i style={`width:${snapshot.runtime.download_progress}%`}></i></div>{/if}
              {:else}
                <div class="installed-badge">✓ Installed · integrity checked before use</div>
              {/if}
            {/if}
          </section>

          {#if settings.task === 'upscale' && !usingTemporalVideo}
            <section class="control-section model-section" aria-labelledby="preprocess-heading">
              <label id="preprocess-heading" class="eyebrow" for="preprocess-model">RESTORE BEFORE UPSCALE</label>
              <select
                id="preprocess-model"
                value={settings.preprocess_model_id}
                on:change={(event) => updateSettings({ preprocess_model_id: event.currentTarget.value })}
              >
                <option value="">None · upscale directly</option>
                {#each preprocessModels as model}
                  <option value={model.model_id}>{model.name}{model.installed ? ' · Installed' : ` · ${formatBytes(model.size_bytes)}`}</option>
                {/each}
              </select>
              {#if preprocessModel}
                <p class="model-description">{preprocessModel.description} This lossless in-memory stage runs before the selected upscaler.</p>
                <div class="license-line"><span>{preprocessModel.license_name}</span><span>{preprocessModel.author}</span></div>
                {#if !preprocessModel.installed}
                  <button
                    class="button full"
                    class:primary={preprocessModel.automated_download_allowed}
                    disabled={Boolean(activeDownload) && activeDownload !== preprocessModel.model_id}
                    on:click={() => runDownload(preprocessModel, false)}
                  >
                    {activeDownload === preprocessModel.model_id
                      ? `Downloading ${Math.round(snapshot.runtime.download_progress)}% · Cancel`
                      : preprocessModel.automated_download_allowed
                        ? `Download restoration model · ${formatBytes(preprocessModel.size_bytes)}`
                        : 'Choose externally downloaded checkpoint…'}
                  </button>
                  {#if activeDownload === preprocessModel.model_id}<div class="download-track"><i style={`width:${snapshot.runtime.download_progress}%`}></i></div>{/if}
                {:else}
                  <div class="installed-badge">✓ Restoration stage installed · integrity checked before use</div>
                {/if}
              {/if}
            </section>
          {/if}

          {#if settings.task !== 'denoise' && !usingTemporalVideo && faceModel}
            <section class="control-section">
              <label class="check-row">
                <input
                  type="checkbox"
                  checked={faceEnabled}
                  on:change={(event) => {
                    if (event.currentTarget.checked && !faceEngineAvailable) {
                      showModal('Face detector unavailable', 'This preview keeps face-aware processing disabled unless a supported local detector is packaged for this platform. The normal model still works.');
                      return;
                    }
                    updateSettings({ enable_face_model: event.currentTarget.checked });
                  }}
                />
                Face-aware pass with {faceModel.name} <em>{faceEngineAvailable ? 'checkpoint rights review required' : 'detector unavailable'}</em>
              </label>
              {#if !faceEngineAvailable}
                <p class="model-description">Visible for workflow parity, but disabled because the OpenCV face-detector runtime is missing from this package.</p>
              {:else if faceEnabled}
                <p class="model-description">Optional companion pass for faces. The exact checkpoint's redistribution and training-data rights are not verified, so LocalSR only accepts a matching user-supplied copy.</p>
                <div class="range-row"><label for="face-fidelity">Face fidelity <b>{settings.face_fidelity}% restored</b></label><input id="face-fidelity" type="range" min="0" max="100" step="5" value={settings.face_fidelity} on:input={(event) => updateSettings({ face_fidelity: Number(event.currentTarget.value) })} /><small>0% retains the resampled original face; 100% applies the strongest restoration. Non-face regions keep the primary result.</small></div>
                {#if faceModel.terms_acceptance_required && !faceModel.installed}
                  <label class="terms"><input type="checkbox" bind:checked={faceTermsAccepted} /> I understand that the checkpoint rights are unresolved and will provide a copy I am permitted to use.</label>
                {/if}
                {#if !faceModel.installed}
                  <button
                    class="button full"
                    class:primary={faceModel.automated_download_allowed}
                    disabled={Boolean(activeDownload) && activeDownload !== faceModel.model_id}
                    on:click={() => runDownload(faceModel, faceTermsAccepted)}
                  >
                    {activeDownload === faceModel.model_id ? `Downloading ${Math.round(snapshot.runtime.download_progress)}% · Cancel` : faceModel.automated_download_allowed ? `Download companion · ${formatBytes(faceModel.size_bytes)}` : 'Choose externally downloaded face checkpoint…'}
                  </button>
                  {#if activeDownload === faceModel.model_id}<div class="download-track"><i style={`width:${snapshot.runtime.download_progress}%`}></i></div>{/if}
                {:else}
                  <div class="installed-badge">✓ Face companion installed · checked before use</div>
                {/if}
              {/if}
            </section>
          {/if}

          <section class="control-section">
            <button class="disclosure" on:click={() => (advanced = !advanced)}><span>Advanced</span><b>{advanced ? '⌃' : '⌄'}</b><small>Model, output, hardware</small></button>
            {#if advanced}
              <div class="advanced-controls">
                {#if settings.task !== 'denoise'}
                  <div class="field-row"><label for="scale">Output scale</label><select id="scale" value={settings.output_scale} on:change={(event) => updateSettings({ output_scale: Number(event.currentTarget.value) })}>{#each Array.from({ length: Math.max(1, (selectedModel?.native_scale ?? 4) - 1) }, (_, index) => index + 2) as scale}<option value={scale}>{scale}×</option>{/each}</select></div>
                {/if}
                {#if settings.task !== 'video'}
                  <div class="field-row"><label for="format">Format</label><select id="format" value={settings.output_format} on:change={(event) => updateSettings({ output_format: event.currentTarget.value as UiSettings['output_format'] })}><option value="png">PNG</option><option value="jpg">JPEG</option><option value="tif">TIFF</option><option value="webp">WebP</option></select></div>
                  {#if settings.output_format === 'jpg' || settings.output_format === 'webp'}<div class="range-row"><label for="quality">{settings.output_format === 'webp' ? 'WebP' : 'JPEG'} quality <b>{settings.jpeg_quality}</b></label><input id="quality" type="range" min="70" max="100" value={settings.jpeg_quality} on:change={(event) => updateSettings({ jpeg_quality: Number(event.currentTarget.value) })} /></div>{/if}
                  <label class="check-row"><input type="checkbox" checked={settings.preserve_metadata} on:change={(event) => updateSettings({ preserve_metadata: event.currentTarget.checked })} /> Preserve safe metadata and color profile</label>
                {:else}
                  <div class="field-row"><label for="container">Container</label><select id="container" value={settings.video_container} on:change={(event) => updateSettings({ video_container: event.currentTarget.value as UiSettings['video_container'] })}><option value="mp4">MP4</option><option value="mkv">MKV</option></select></div>
                  <div class="range-row"><label for="crf">Video quality · CRF <b>{settings.video_crf}</b></label><input id="crf" type="range" min="12" max="30" value={settings.video_crf} on:change={(event) => updateSettings({ video_crf: Number(event.currentTarget.value) })} /></div>
                  <label class="check-row"><input type="checkbox" checked={settings.deflicker} on:change={(event) => updateSettings({ deflicker: event.currentTarget.checked })} /> Temporal median de-flicker</label>
                {/if}
                <button class="directory-field" on:click={chooseOutput}><span><small>Save to</small>{settings.output_directory || 'Choose an output folder'}</span><b>Choose…</b></button>
                <div class="field-row"><label for="device">Hardware</label><select id="device" value={settings.device_id} on:change={(event) => updateSettings({ device_id: event.currentTarget.value })}>{#each snapshot.capabilities.devices as device}<option value={device.id}>{device.name}</option>{/each}</select></div>
                <div class="field-row"><label for="interface-scale">Interface text</label><select id="interface-scale" value={settings.interface_scale} on:change={(event) => updateSettings({ interface_scale: Number(event.currentTarget.value) as UiSettings['interface_scale'] })}><option value="100">100%</option><option value="110">110%</option><option value="125">125%</option></select></div>
                <div class="field-grid"><label>Tile<select value={settings.tile_size} on:change={(event) => updateSettings({ tile_size: Number(event.currentTarget.value) })}>{#each [64, 128, 192, 256, 384, 512] as size}<option value={size}>{size}</option>{/each}</select></label><label>Halo<select value={settings.halo} on:change={(event) => updateSettings({ halo: Number(event.currentTarget.value) })}>{#each [8, 16, 32, 64] as halo}<option value={halo}>{halo}</option>{/each}</select></label><label>Precision<select value={settings.precision} on:change={(event) => updateSettings({ precision: event.currentTarget.value })}><option value="fp32">FP32</option><option value="fp16">FP16</option></select></label></div>
                <label class="check-row"><input type="checkbox" checked={settings.safe_memory} on:change={(event) => updateSettings({ safe_memory: event.currentTarget.checked })} /> Safe memory mode</label>
                <label class="check-row"><input type="checkbox" checked={settings.enable_live_preview} on:change={(event) => updateSettings({ enable_live_preview: event.currentTarget.checked })} /> Show sampled enhanced previews (max 2 fps)</label>
                <div class="hardware-card"><span>{snapshot.capabilities.system_memory_pressure_level === 'unknown' ? 'Hardware ready' : `${snapshot.capabilities.system_memory_pressure_level} memory pressure`}</span><button on:click={() => api.refreshCapabilities()}>Refresh</button><i style={`width:${Math.max(3, snapshot.capabilities.system_memory_pressure_percent)}%`}></i></div>
              </div>
            {/if}
          </section>

          <section class="control-section summary-card">
            <span class="eyebrow">SUMMARY</span>
            <dl><div><dt>Input</dt><dd>{selectedMedia ? `${selectedMedia.width} × ${selectedMedia.height}` : '—'}</dd></div><div><dt>Output</dt><dd>{outputDimensions}</dd></div><div><dt>Model</dt><dd>{usingTemporalVideo ? selectedVideoModel?.name : selectedModel?.name ?? (settings.selected_model_id === '__custom__' ? 'Custom' : '—')}</dd></div><div><dt>Device</dt><dd>{snapshot.capabilities.devices.find((device) => device.id === settings.device_id)?.name ?? 'Detecting'}</dd></div></dl>
          </section>

        {/if}

        <section class="about-block"><div><b>LocalSR</b><span>{snapshot.app_version}</span></div><button on:click={showPerformance}>Performance</button><button on:click={copyDiagnostics}>Copy diagnostics</button><button on:click={showIntegrations}>System integrations</button><p>Local processing · no media uploads<br />Models retain their own licenses.</p></section>
      </div>
    </aside>
  </main>

  <footer class="status-bar">
    <div class="status-copy"><i class:working={Boolean(snapshot.runtime.active_job_id)}></i><div><strong>{booting ? 'Starting' : snapshot.runtime.status_title}{queuedCount ? ` · ${queuedCount} queued` : ''}</strong><span>{booting ? 'Opening the trusted desktop control plane.' : snapshot.runtime.status_detail}</span></div></div>
    {#if livePreviewWarning}<span class="inline-warning preview-warning" role="status">Preview warning: {livePreviewWarning} Processing continues normally.</span>{/if}
    {#if snapshot.runtime.active_job_id}<div class="progress"><i style={`width:${snapshot.runtime.progress}%`}></i></div>{/if}
    <div class="status-actions">
      {#if snapshot.runtime.last_output_path}<button class="button" on:click={() => api.revealResult(snapshot.runtime.last_output_path)}>Reveal</button><button class="button" on:click={() => api.openResult(snapshot.runtime.last_output_path)}>Open</button>{/if}
      {#if snapshot.runtime.active_job_id}
        <button class="button queue-more" disabled={!canAppendToQueue} on:click={() => start()}>{settings.batch_mode ? `Add ${queueSelection.length} to queue` : 'Add selected to queue'}</button>
        <button class="button danger" on:click={() => api.cancelJobs()}>Cancel queue</button>
      {:else}
        <button class="button primary start" disabled={!canStart} on:click={() => start()}>{settings.batch_mode ? `Start ${queueSelection.length} item${queueSelection.length === 1 ? '' : 's'}` : settings.task === 'video' ? 'Start selected video · Labs' : settings.task === 'denoise' ? 'Denoise selected' : 'Upscale selected'}</button>
      {/if}
    </div>
  </footer>
</div>

{#if modalTitle}
  <div class="modal-backdrop" role="presentation" on:click={closeFromBackdrop}>
    <div class="modal" class:performance-modal={modalKind === 'performance'} role="dialog" aria-modal="true" aria-labelledby="modal-title" tabindex="-1">
      <h2 id="modal-title">{modalTitle}</h2><p>{modalMessage}</p>
      {#if modalKind === 'performance'}
        <section class="benchmark-panel" aria-labelledby="benchmark-title">
          <div class="benchmark-heading">
            <div>
              <span class="eyebrow">BENCHMARK V2</span>
              <h3 id="benchmark-title">LocalSR System Score</h3>
            </div>
            <span class="benchmark-private">100% local</span>
          </div>
          <p class="benchmark-intro">A repeatable real-inference test across the GPU, CPU, tiled processing, and image encoding.</p>

          {#if benchmarkRunning}
            <div class="benchmark-running-card" role="status" aria-live="polite">
              <div><strong>{snapshot.runtime.status_title}</strong><b>{Math.round(snapshot.runtime.progress)}%</b></div>
              <span>{snapshot.runtime.status_detail}</span>
              <div class="download-track benchmark-progress" aria-label={`Benchmark ${Math.round(snapshot.runtime.progress)}%`}><i style={`width:${snapshot.runtime.progress}%`}></i></div>
            </div>
          {/if}

          {#if snapshot.latest_benchmark}
            {#if snapshot.latest_benchmark.workload_version.startsWith('localsr-benchmark-v2')}
              <div class:unstable={!snapshot.latest_benchmark.stable} class="benchmark-hero">
                <div class="benchmark-score-block">
                  <div class="benchmark-score-label">
                    <span>System score</span>
                    <span class:unstable={!snapshot.latest_benchmark.stable} class="benchmark-status-pill">
                      {snapshot.latest_benchmark.stable ? '● Stable result' : '● Unstable result'}
                    </span>
                  </div>
                  {#if snapshot.latest_benchmark.stable}
                    {#if snapshot.latest_benchmark.system_score != null}
                      <div class="benchmark-score-value">
                        <strong>{snapshot.latest_benchmark.system_score.toFixed(2)}</strong>
                        <span>output MP/s</span>
                      </div>
                      <p>Higher is faster · GPU score across all three scenes</p>
                    {:else}
                      <div class="benchmark-score-value text-score"><strong>CPU only</strong></div>
                      <p>No supported accelerator was available for a system score.</p>
                    {/if}
                  {:else}
                    <div class="benchmark-score-value text-score"><strong>Run varied too much</strong></div>
                    <p>Close background apps and rerun for a publishable score.</p>
                  {/if}
                </div>

                <div class="benchmark-comparison">
                  {#if snapshot.latest_benchmark.stable && snapshot.latest_benchmark.reference_label && snapshot.latest_benchmark.reference_ratio != null}
                    <span>Compared with reference</span>
                    <strong>{snapshot.latest_benchmark.reference_ratio.toFixed(2)}×</strong>
                    <b>{snapshot.latest_benchmark.reference_label}</b>
                    <div class="benchmark-reference-track" aria-label={`${snapshot.latest_benchmark.reference_ratio.toFixed(2)} times the ${snapshot.latest_benchmark.reference_label} reference`}>
                      <i style={`width:${Math.max(4, Math.min(100, snapshot.latest_benchmark.reference_ratio * 50))}%`}></i>
                      <span>1×</span>
                    </div>
                  {:else}
                    <span>Result confidence</span>
                    <strong>{(snapshot.latest_benchmark.cv_percent ?? 0).toFixed(1)}%</strong>
                    <b>timing spread · target ≤ 5%</b>
                  {/if}
                </div>
              </div>

              <dl class="benchmark-facts">
                {#if snapshot.latest_benchmark.stable}
                  <div><dt>Consistency</dt><dd>{(snapshot.latest_benchmark.cv_percent ?? 0).toFixed(1)}% spread</dd></div>
                {:else}
                  <div><dt>Consistency</dt><dd class="warning-value">{(snapshot.latest_benchmark.cv_percent ?? 0).toFixed(1)}% spread</dd></div>
                {/if}
                <div><dt>Total time</dt><dd>{formatDuration(snapshot.latest_benchmark.result_elapsed_seconds ?? snapshot.latest_benchmark.total_elapsed_seconds)}</dd></div>
                <div><dt>Workload</dt><dd>v2 · 3 fixed scenes</dd></div>
              </dl>

              {#if snapshot.latest_benchmark.device_results?.length}
                <div class="benchmark-section-title"><strong>Hardware results</strong><span>Relative throughput</span></div>
                <div class="benchmark-devices" aria-label="Per-device benchmark results">
                  {#each snapshot.latest_benchmark.device_results as device (device.device)}
                    <article class="benchmark-device" class:cpu={device.device_type === 'cpu'} aria-label={`${device.device_name} benchmark result`}>
                      <div class="benchmark-device-heading">
                        <div><span>{device.device_type === 'cpu' ? 'CPU' : 'ACCELERATOR'}</span><strong>{device.device_name}</strong></div>
                        <div class="benchmark-device-score"><strong>{device.score.toFixed(2)}</strong><span>output MP/s</span></div>
                      </div>
                      <div class="benchmark-device-track" aria-hidden="true"><i style={`width:${benchmarkDeviceBarWidth(device.score, snapshot.latest_benchmark.device_results ?? [])}%`}></i></div>
                      <div class="benchmark-device-meta">
                        <span class:warning-value={!device.stable}>{device.stable ? '● Stable' : '● Unstable'}</span>
                        <span>{device.cv_percent.toFixed(1)}% spread</span>
                        <span>{device.thermal_state}</span>
                      </div>
                      <details class="benchmark-scenes">
                        <summary>Scene breakdown</summary>
                        {#each device.scenes as scene (scene.scene_id)}
                          <div class="benchmark-scene">
                            <span class="scene-name">{benchmarkSceneLabel(scene.scene_id)}</span>
                            <strong>{scene.megapixels_per_second.toFixed(2)} MP/s</strong>
                            <span class="scene-metric">{scene.median_ms.toFixed(0)} ms</span>
                            <span class="scene-metric subtle">{scene.encode_ms != null ? `+${scene.encode_ms.toFixed(0)} ms encode` : `${scene.iterations} runs`}</span>
                          </div>
                        {/each}
                      </details>
                    </article>
                  {/each}
                </div>
              {/if}
              <p class="benchmark-note">The system score is the geometric mean of output throughput across the GPU scenes. CPU is shown separately. Results over 5% timing spread are marked unstable.</p>
            {:else}
              <div class="benchmark-hero legacy-score">
                <div class="benchmark-score-block">
                  <div class="benchmark-score-label"><span>Legacy score</span><span class="benchmark-status-pill">V1 workload</span></div>
                  <div class="benchmark-score-value"><strong>{snapshot.latest_benchmark.score.toFixed(2)}</strong><span>points</span></div>
                  <p>Keep this result for comparison with other v1 runs only.</p>
                </div>
              </div>
              <dl class="benchmark-facts legacy-facts">
                <div><dt>Median / p95</dt><dd>{snapshot.latest_benchmark.median_inference_ms.toFixed(1)} / {snapshot.latest_benchmark.p95_inference_ms.toFixed(1)} ms</dd></div>
                <div><dt>Throughput</dt><dd>{snapshot.latest_benchmark.end_to_end_fps.toFixed(2)} fps · {snapshot.latest_benchmark.processed_megapixels_per_second.toFixed(3)} MP/s</dd></div>
                <div><dt>Device</dt><dd>{snapshot.latest_benchmark.device} · {snapshot.latest_benchmark.model_name}</dd></div>
                <div><dt>Peak process memory</dt><dd>{snapshot.latest_benchmark.peak_memory_bytes ? formatBytes(snapshot.latest_benchmark.peak_memory_bytes) : 'Not reliably available'}</dd></div>
              </dl>
              <p class="benchmark-note">This is an older v1 result. Run the benchmark again to get the more stable multi-device v2 score.</p>
            {/if}
          {:else if !benchmarkRunning}
            <div class="benchmark-empty">
              <div class="benchmark-empty-gauge" aria-hidden="true"><i></i></div>
              <div><strong>No benchmark result yet</strong><span>Run the fixed workload to measure this system and create a local score.</span></div>
            </div>
          {/if}
          <div class="benchmark-actions">
            <button class="button primary" disabled={Boolean(snapshot.runtime.active_job_id) && !benchmarkRunning} on:click={benchmarkRunning ? () => api.cancelJobs() : runBenchmark}>{benchmarkRunning ? 'Cancel Benchmark' : 'Run Benchmark'}</button>
            {#if snapshot.latest_benchmark}<button class="button" disabled={benchmarkRunning} on:click={copyBenchmark}>Copy JSON</button><button class="button" disabled={benchmarkRunning} on:click={exportBenchmark}>Export JSON…</button>{/if}
          </div>
        </section>

        <div class="performance-section-heading"><strong>Live hardware</strong><span>Current worker state</span></div>
        <dl class="performance-grid">
          <div><dt>Backend</dt><dd>{activeDevice?.type?.toUpperCase() ?? 'Detecting'}</dd></div>
          <div><dt>Device</dt><dd>{activeDevice?.name ?? 'Detecting'}</dd></div>
          <div><dt>Worker</dt><dd>{snapshot.runtime.worker}</dd></div>
          <div><dt>Device memory</dt><dd>{snapshot.runtime.device_free_memory ? `${formatBytes(snapshot.runtime.device_free_memory)} free · ${formatBytes(snapshot.runtime.device_allocated_memory)} used` : activeDevice?.free_memory ? `${formatBytes(activeDevice.free_memory)} free` : 'Not reported'}</dd></div>
          <div><dt>System memory</dt><dd>{formatBytes(snapshot.runtime.live_system_ram_available || snapshot.capabilities.system_ram_available)} available</dd></div>
          <div><dt>Memory pressure</dt><dd>{Math.round(snapshot.runtime.live_memory_pressure_percent || snapshot.capabilities.system_memory_pressure_percent)}% · {snapshot.capabilities.system_memory_pressure_level}</dd></div>
          <div><dt>Throughput</dt><dd>{snapshot.runtime.throughput ? `${snapshot.runtime.throughput.toFixed(2)} ${snapshot.runtime.throughput_unit}` : 'Waiting for a job'}</dd></div>
          <div><dt>Elapsed / ETA</dt><dd>{formatDuration(snapshot.runtime.elapsed_seconds)} / {snapshot.runtime.estimated_remaining_seconds ? formatDuration(snapshot.runtime.estimated_remaining_seconds) : '—'}</dd></div>
          <div><dt>Tile</dt><dd>{snapshot.runtime.active_tile_size || settings.tile_size}px · halo {settings.halo}px</dd></div>
          <div><dt>Thermals</dt><dd>{snapshot.runtime.thermal_status}</dd></div>
        </dl>
      {:else if modalKind === 'integrations' && integration}
        <p class="integration-detail">Command: <code>{integration.command_name}</code><br />Platform: {integration.platform}</p>
      {:else if diagnostics}
        <textarea readonly>{diagnostics}</textarea>
      {/if}
      <div class="modal-actions">
        {#if modalKind === 'performance'}<button class="button" on:click={copyDiagnostics}>Copy diagnostics</button>{/if}
        {#if modalKind === 'integrations' && integration}<button class="button" class:danger={integration.installed} on:click={() => changeIntegrations(!integration?.installed)}>{integration.installed ? 'Remove integrations' : 'Install integrations'}</button>{/if}
        <button class="button primary" on:click={closeModal}>OK</button>
      </div>
    </div>
  </div>
{/if}
