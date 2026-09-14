<script lang="ts">
  import { onMount, tick } from 'svelte';
  import PreviewPane from './PreviewPane.svelte';
  import { queueTiming } from './lib/queue-timing';
  import MediaQueue from './MediaQueue.svelte';
  import AdvancedSettings from './AdvancedSettings.svelte';
  import VideoMemory from './VideoMemory.svelte';
  import LicenseDownload from './LicenseDownload.svelte';
  import UpdatePanel from './UpdatePanel.svelte';
  import BenchmarkStudio from './BenchmarkStudio.svelte';
  import * as api from './lib/api';
  import {
    applyWorkerEnvelope,
    choosePresetModel,
    formatBytes,
    formatDuration,
    modelsForTask,
    resultPreviewForSelectedMedia,
  } from './lib/state';
  import { demoSnapshot } from './lib/demo';
  import type {
    AppSnapshot,
    BenchmarkDeviceResult,
    BenchmarkRender,
    CatalogModel,
    CatalogVideoModel,
    IntegrationStatus,
    LaunchIntent,
    Recipe,
    StartBatchInput,
    TaskKind,
    UiSettings,
    WorkerEnvelope,
  } from './lib/types';

  let snapshot: AppSnapshot = demoSnapshot();
  let booting = true;
  let benchmarkRenders: BenchmarkRender[] = [];
  let benchmarkDevice = '';
  let benchmarkSetup: 'idle' | 'downloading' | 'starting' = 'idle';
  let benchmarkError = '';
  let benchmarkTile: WorkerEnvelope | undefined;
  $: benchmarkScores = snapshot.latest_benchmark?.device_history?.length
    ? snapshot.latest_benchmark.device_history
    : (snapshot.latest_benchmark?.device_results ?? []);

  let page: 'media' | 'preview' | 'enhance' = 'preview';
  let recipeEditorOpen = false;
  let recipeName = '';
  let termsAccepted = false;
  let faceTermsAccepted = false;
  let previewPane: PreviewPane | undefined;
  let inspectorScroll: HTMLDivElement | undefined;
  let lastLivePreviewSequence = 0;
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
  let livePreviewWarning = '';
  let startingJob = false;
  let requestingCancel = false;
  $: settingsLocked = startingJob || Boolean(snapshot.runtime.active_job_id);
  $: cancelling =
    requestingCancel ||
    snapshot.runtime.status_title === 'Cancelling' ||
    snapshot.jobs.some(
      (job) => job.id === snapshot.runtime.active_job_id && job.status === 'cancelling',
    );

  $: settings = snapshot.settings;
  $: selectedMedia = snapshot.media.find((media) => media.selected) ?? snapshot.media[0];
  $: compatibleModels = modelsForTask(snapshot, settings.task);
  $: selectedModel = compatibleModels.find(
    (model) => model.model_id === settings.selected_model_id,
  );
  $: selectedVideoModel = snapshot.catalog.video_models.find(
    (model) => model.model_id === settings.selected_video_model_id,
  );
  $: faceModel = snapshot.catalog.models.find(
    (model) => model.model_id === selectedModel?.pair_with && model.purposes.includes('face'),
  );
  $: preprocessModels = snapshot.catalog.models.filter(
    (model) => model.native_scale === 1 && !model.purposes.includes('face'),
  );
  $: preprocessModel = preprocessModels.find(
    (model) => model.model_id === settings.preprocess_model_id,
  );
  $: faceEngineAvailable = snapshot.engine?.features.includes('face_aware') === true;
  $: faceEnabled = settings.enable_face_model && faceEngineAvailable;
  $: usingTemporalVideo =
    settings.task === 'video' && settings.selected_video_model_id !== 'frame_by_frame';
  $: temporalUnavailableReason = !snapshot.engine?.video_engines.includes('seedvr2')
    ? 'This installation supports frame-by-frame video. SeedVR2 requires a compatible CUDA, ROCm or Metal engine.'
    : /^(directml|xpu)(:|$)/.test(settings.device_id)
      ? 'SeedVR2 cannot run on this device. Choose frame-by-frame video or a compatible CUDA, ROCm or Metal device.'
      : '';
  $: seedMemory = snapshot.runtime.video_memory;
  $: selectedMemory =
    snapshot.jobs.find((job) => job.id === seedMemory?.job_id)?.media_id === selectedMedia?.id
      ? seedMemory
      : undefined;
  $: preservingHdr =
    settings.task === 'video' &&
    settings.video_hdr_mode === 'preserve' &&
    Boolean(selectedMedia?.hdr_format);
  $: hdrPreservationAvailable = supportsHdrPreservation(settings);
  $: hdrModelCompatible = !preservingHdr || hdrPreservationAvailable;
  $: licenseReviewModel =
    !usingTemporalVideo &&
    selectedModel &&
    ['realplksr_hfa2k_anime_x4', 'realplksr_nomoswebphoto_x4'].includes(selectedModel.model_id)
      ? selectedModel
      : undefined;
  $: activeDownload = snapshot.runtime.download_model_id;
  $: benchmarkRunning = snapshot.runtime.active_job_id.startsWith('benchmark-');
  $: benchmarkModel = snapshot.catalog.models.find((model) => model.model_id === 'span_photo_x4');
  $: benchmarkStartBlockReason =
    snapshot.runtime.worker !== 'ready'
      ? 'Waiting for the inference engine to become ready.'
      : snapshot.runtime.active_job_id && !benchmarkRunning
        ? 'Finish or cancel the current work before benchmarking.'
        : activeDownload
          ? 'Wait for the current model download to finish before benchmarking.'
          : !benchmarkModel
            ? 'The benchmark model is unavailable in this catalog.'
            : !benchmarkModel.installed &&
                (!benchmarkModel.automated_download_allowed ||
                  benchmarkModel.terms_acceptance_required)
              ? 'Import the SPAN Quick model before benchmarking.'
              : '';
  $: customModelNeedsOptIn =
    settings.selected_model_id === '__custom__' &&
    Boolean(settings.custom_model_path) &&
    !settings.custom_model_path.toLowerCase().endsWith('.safetensors');
  $: modelReady = usingTemporalVideo
    ? !temporalUnavailableReason && Boolean(selectedVideoModel?.installed)
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
      .map((job) => job.media_id),
  );
  $: activeJobMediaId =
    snapshot.jobs.find((job) => job.id === snapshot.runtime.active_job_id)?.media_id ?? '';
  $: runnableMedia = settings.task
    ? snapshot.media.filter((media) => (settings.task === 'video') === (media.kind === 'video'))
    : [];
  $: queueableMedia = runnableMedia.filter((media) => !inflightMediaIds.has(media.id));
  $: queueSelection = settings.batch_mode
    ? queueableMedia
    : selectedMedia && mediaMatchesTask && !inflightMediaIds.has(selectedMedia.id)
      ? [selectedMedia]
      : [];
  $: canQueue =
    !startingJob &&
    !cancelling &&
    snapshot.runtime.worker === 'ready' &&
    Boolean(settings.task) &&
    queueSelection.length > 0 &&
    queueSelection.every((media) => media.probe_status === 'ready') &&
    modelReady &&
    hdrModelCompatible &&
    faceReady &&
    preprocessReady &&
    snapshot.runtime.status_title !== 'Cancelling';
  $: canStart = !snapshot.runtime.active_job_id && canQueue;
  $: canAppendToQueue = Boolean(snapshot.runtime.active_job_id) && canQueue;
  $: queuedCount = snapshot.jobs.filter((job) => job.status === 'queued').length;
  $: queueEta = queueTiming(snapshot);
  $: resultPreview = resultPreviewForSelectedMedia(snapshot);
  let imageComparisonKey = '';
  let imageComparisonError = '';
  $: completedImageJob =
    selectedMedia?.kind === 'image' && selectedMedia.id !== activeJobMediaId
      ? snapshot.jobs.find(
          (job) =>
            job.media_id === selectedMedia?.id && job.status === 'completed' && job.output_path,
        )
      : undefined;
  $: desiredImageComparisonKey =
    completedImageJob && snapshot.runtime.worker === 'ready'
      ? `${completedImageJob.media_id}:${completedImageJob.output_path}`
      : '';
  $: if (desiredImageComparisonKey !== imageComparisonKey)
    void loadImageComparison(desiredImageComparisonKey);
  $: selectedComparisonError =
    imageComparisonError ||
    (snapshot.runtime.comparison_media_id === selectedMedia?.id
      ? (snapshot.runtime.comparison_error ?? '')
      : '');
  $: completedVideoOutput =
    selectedMedia?.kind === 'video' && selectedMedia.id !== activeJobMediaId
      ? (snapshot.jobs.find(
          (job) =>
            job.media_id === selectedMedia.id &&
            job.status === 'completed' &&
            Boolean(job.output_path),
        )?.output_path ?? '')
      : '';
  $: outputDimensions = selectedMedia
    ? settings.task === 'denoise'
      ? `${selectedMedia.width} × ${selectedMedia.height} · original size`
      : usingTemporalVideo &&
          settings.video_target_resolution &&
          selectedMedia.width > 0 &&
          selectedMedia.height > 0
        ? `${Math.floor((selectedMedia.width * settings.video_target_resolution) / Math.min(selectedMedia.width, selectedMedia.height) / 2) * 2} × ${Math.floor((selectedMedia.height * settings.video_target_resolution) / Math.min(selectedMedia.width, selectedMedia.height) / 2) * 2} · SDR`
        : `${selectedMedia.width * settings.output_scale} × ${selectedMedia.height * settings.output_scale} · ${settings.output_scale}×`
    : '—';
  $: currentDownloadTarget = usingTemporalVideo ? selectedVideoModel : selectedModel;
  $: activeDevice = snapshot.capabilities.devices.find(
    (device) => device.id === settings.device_id,
  );
  $: benchmarkHardware = snapshot.capabilities.devices.find(
    (device) => device.id === benchmarkDevice,
  );
  $: unreadyMedia = queueSelection.find((media) => media.probe_status !== 'ready');
  $: singleScopeMessage = unreadyMedia
    ? unreadyMedia.probe_status === 'failed'
      ? `“${unreadyMedia.name}” cannot be processed: ${unreadyMedia.error}`
      : `Preparing preview… Wait for “${unreadyMedia.name}” before starting.`
    : settings.batch_mode
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

  onMount(() => {
    const unlisteners: (() => void)[] = [];
    let disposed = false;
    async function subscribe(register: () => Promise<() => void>): Promise<void> {
      if (disposed) return;
      const unlisten = await register();
      if (disposed) unlisten();
      else unlisteners.push(unlisten);
    }
    void (async () => {
      try {
        snapshot = await api.bootstrap();
        await tick();
        if (disposed) return;
        ensureTaskMatchesSelection();
        chooseInitialModel();
        if (
          settings.task === 'video' &&
          settings.video_hdr_mode === 'preserve' &&
          !supportsHdrPreservation(settings)
        ) {
          updateSettings({ video_hdr_mode: 'tone_map' });
        }
        await subscribe(() => api.listenForWorker(handleWorkerMessage));
        await subscribe(() => api.listenForStateChange(scheduleRefresh));
        await subscribe(() =>
          api.listenForNativeMenu((action) => void handleNativeMenuAction(action)),
        );
        await subscribe(() => api.listenForLaunchIntent(() => void consumeLaunchIntents()));
        if (!disposed) await consumeLaunchIntents();
      } catch (error) {
        if (!disposed) showModal('Could not start LocalSR', String(error));
      } finally {
        booting = false;
      }
    })();

    return () => {
      disposed = true;
      if (stateRefresh) clearTimeout(stateRefresh);
      if (runtimePulseTimer) clearTimeout(runtimePulseTimer);
      if (autoStartTimer) clearTimeout(autoStartTimer);
      for (const unlisten of unlisteners) unlisten();
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
      for (const media of latest.media) {
        const previous = snapshot.media.find((item) => item.id === media.id);
        if (media.probe_status === 'pending' && previous?.probe_status === 'pending') {
          media.probe_stage = previous.probe_stage;
          media.probe_started_at = previous.probe_started_at;
        }
      }
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
    if (
      cancelling &&
      [
        'progress',
        'video_tile_progress',
        'video_stage_progress',
        'video_frame_started',
        'video_frame_completed',
        'benchmark_progress',
        'tile_update',
        'live_preview_frame',
      ].includes(message.type)
    )
      return;
    if (message.type === 'benchmark_started') {
      benchmarkRenders = [];
      benchmarkTile = undefined;
    }
    if (message.type === 'benchmark_tile') {
      if (String(message.data.job_id ?? '') === snapshot.runtime.active_job_id)
        benchmarkTile = message;
      return;
    }
    if (['benchmark_completed', 'benchmark_cancelled', 'benchmark_failed'].includes(message.type))
      benchmarkRenders = [];
    if (message.type === 'benchmark_preview') {
      if (String(message.data.job_id ?? '') !== snapshot.runtime.active_job_id) return;
      const render = message.data.render as BenchmarkRender | undefined;
      if (
        render?.input_data_url?.startsWith('data:image/jpeg;base64,') &&
        render?.output_data_url?.startsWith('data:image/jpeg;base64,')
      ) {
        benchmarkRenders = [
          ...benchmarkRenders.filter(
            (item) => item.device !== render.device || item.scene_id !== render.scene_id,
          ),
          render,
        ];
      }
      return;
    }
    // Tile traffic is display-only and can arrive thousands of times. Do not
    // invalidate the full Svelte snapshot for it; only update the live canvas.
    if (message.type === 'tile_update') {
      if (activeJobMediaId && selectedMedia?.id !== activeJobMediaId) return;
      queueProgressiveTile(message);
      return;
    }
    if (message.type === 'live_preview_frame') {
      const jobId = String(message.data.job_id ?? '');
      if (message.data.preview_kind === 'source_video') {
        if (
          jobId === snapshot.runtime.active_job_id &&
          (!activeJobMediaId || selectedMedia?.id === activeJobMediaId)
        ) {
          previewPane?.queueVideoSource(message, jobId);
        }
        return;
      }
      const sequence = Number(message.data.sequence ?? 0);
      if (
        jobId !== snapshot.runtime.active_job_id ||
        (activeJobMediaId && selectedMedia?.id !== activeJobMediaId) ||
        sequence <= lastLivePreviewSequence
      )
        return;
      lastLivePreviewSequence = sequence;
      if (['tile', 'video'].includes(String(message.data.preview_kind ?? ''))) {
        queueProgressiveTile({
          type: 'tile_update',
          data: { ...message.data, phase: 'completed' },
        });
      } else {
        snapshot = applyWorkerEnvelope(snapshot, message);
      }
      return;
    }
    if (message.type === 'video_memory') {
      snapshot = applyWorkerEnvelope(snapshot, message);
      return;
    }
    if (message.type === 'live_preview_warning') {
      livePreviewWarning = String(
        message.data.message ?? 'A sampled enhanced preview could not be displayed.',
      );
      return;
    }

    if (
      message.type === 'progress' ||
      message.type === 'video_tile_progress' ||
      message.type === 'video_stage_progress' ||
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
      if (
        String(message.data.media_path ?? '') === selectedMedia?.path &&
        message.data.jpeg_base64
      ) {
        previewPane?.allowPreviewRetry();
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

  function supportsHdrPreservation(candidate: UiSettings): boolean {
    return (
      candidate.selected_video_model_id === 'frame_by_frame' &&
      (candidate.selected_model_id === '__custom__' ||
        snapshot.catalog.models.some(
          (model) => model.model_id === candidate.selected_model_id && model.architecture === 'HAT',
        ))
    );
  }

  function updateSettings(patch: Partial<UiSettings>): void {
    if (settingsLocked) return;
    const next = { ...snapshot.settings, ...patch };
    // Apply the model/output contract for selectors, saved recipes and launch presets.
    if (next.task === 'video' && next.video_hdr_mode === 'preserve') {
      if (!supportsHdrPreservation(next)) next.video_hdr_mode = 'tone_map';
      else {
        next.precision = 'fp32';
        next.deflicker = false;
      }
    }
    snapshot = { ...snapshot, settings: next };
    if (api.isTauri()) {
      void api
        .saveSettings(snapshot.settings)
        .catch((error) => showModal('Could not save settings', String(error)));
    }
  }

  function setTask(task: TaskKind): void {
    if (selectedMedia && (task === 'video') !== (selectedMedia.kind === 'video')) return;
    if (task !== 'video') lastImageTask = task;
    const models = modelsForTask(snapshot, task);
    const chosen =
      models.find((model) => model.model_id === settings.selected_model_id) ?? models[0];
    updateSettings({
      task,
      selected_model_id: chosen?.model_id ?? '',
      preprocess_model_id: task === 'upscale' ? settings.preprocess_model_id : '',
      output_scale:
        task === 'denoise'
          ? 1
          : Math.min(chosen?.native_scale ?? 4, Math.max(2, settings.output_scale)),
      enable_face_model: false,
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

  async function loadImageComparison(key: string): Promise<void> {
    imageComparisonKey = key;
    imageComparisonError = '';
    if (!key || !completedImageJob || resultPreview || !api.isTauri()) return;
    const mediaId = completedImageJob.media_id;
    try {
      await api.requestImageComparison(mediaId);
      if (key === imageComparisonKey) await refresh();
    } catch (error) {
      if (key === imageComparisonKey) imageComparisonError = String(error);
    }
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
    const folder = await api.chooseMediaFolder();
    if (!folder?.paths.length) return;
    await api.addMedia(folder.paths, false);
    await api.saveSettings({
      ...settings,
      batch_mode: true,
      output_directory: folder.outputDirectory,
    });
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
        enable_face_model: false,
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
      output_scale: model
        ? Math.min(model.native_scale, Math.max(2, settings.output_scale))
        : settings.output_scale,
      allow_unsafe_pickle_model: false,
      enable_face_model: false,
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
      enable_face_model: false,
    });
    termsAccepted = false;
    faceTermsAccepted = false;
  }

  async function runDownload(
    target: CatalogModel | CatalogVideoModel | undefined = currentDownloadTarget,
    accepted = termsAccepted,
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
      showModal(
        target.automated_download_allowed ? 'Download failed' : 'Import failed',
        String(error),
      );
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
      video_hdr_mode: settings.video_hdr_mode ?? 'tone_map',
      video_target_resolution: settings.video_target_resolution ?? 0,
      video_low_memory: settings.video_low_memory ?? true,
      video_crf: settings.video_crf,
      enable_face_model: faceEnabled,
      face_fidelity: settings.face_fidelity,
      enable_live_preview: settings.enable_live_preview,
      allow_unsafe_pickle_model: settings.allow_unsafe_pickle_model,
    };
    startingJob = true;
    try {
      await api.startJobs(input);
      await refresh();
    } catch (error) {
      showModal('Could not start', String(error));
    } finally {
      startingJob = false;
    }
  }

  async function cancelWork(): Promise<void> {
    if (cancelling) return;
    requestingCancel = true;
    discardRuntimePulse();
    try {
      await api.cancelJobs();
      await refresh();
    } catch (error) {
      showModal('Could not cancel', String(error));
    } finally {
      requestingCancel = false;
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
        settings.selected_model_id === '__custom__' &&
        settings.custom_model_path.toLowerCase().endsWith('.safetensors')
          ? settings.custom_model_path
          : '',
      output_format: settings.output_format,
      preserve_metadata: settings.preserve_metadata,
      jpeg_quality: settings.jpeg_quality,
      deflicker: settings.deflicker,
      deflicker_window: settings.deflicker_window,
      video_container: settings.video_container,
      video_hdr_mode: settings.video_hdr_mode ?? 'tone_map',
      video_target_resolution: settings.video_target_resolution ?? 0,
      video_low_memory: settings.video_low_memory ?? true,
      video_crf: settings.video_crf,
      enable_face_model: faceEnabled,
      face_fidelity: settings.face_fidelity,
      stages: [
        ...(settings.task === 'upscale' && preprocessModel
          ? [
              {
                kind:
                  preprocessModel.model_id === 'fbcnn_color'
                    ? ('deblock' as const)
                    : ('restore' as const),
                model_id: preprocessModel.model_id,
              },
            ]
          : []),
        {
          kind:
            settings.task === 'video'
              ? ('video' as const)
              : settings.task === 'denoise'
                ? ('restore' as const)
                : ('upscale' as const),
          model_id:
            settings.task === 'video' && usingTemporalVideo
              ? settings.selected_video_model_id
              : settings.selected_model_id,
        },
        ...(faceEnabled && faceModel
          ? [
              {
                kind: 'face_restore' as const,
                model_id: faceModel.model_id,
                fidelity: settings.face_fidelity / 100,
              },
            ]
          : []),
      ],
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
        `Select a ${recipe.task === 'video' ? 'video' : 'photo'} before applying “${recipe.name}”.`,
      );
      return;
    }
    if (recipe.task !== 'video') lastImageTask = recipe.task;
    const recipePreprocess =
      recipe.task === 'upscale'
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
      output_scale:
        recipe.task === 'denoise'
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
      video_hdr_mode: recipe.video_hdr_mode ?? 'tone_map',
      video_target_resolution: recipe.video_target_resolution ?? 0,
      video_low_memory: recipe.video_low_memory ?? true,
      video_crf: recipe.video_crf ?? settings.video_crf,
      enable_face_model: recipeFace ? true : (recipe.enable_face_model ?? false),
      face_fidelity:
        recipeFace?.fidelity === undefined
          ? (recipe.face_fidelity ?? 70)
          : Math.round(recipeFace.fidelity * 100),
      allow_unsafe_pickle_model: false,
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
        (recipe) => recipe.name.toLocaleLowerCase() === intent.recipe?.toLocaleLowerCase(),
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
        'Images and videos use different tasks. They were added safely; choose one media type and start it from LocalSR.',
      );
      return;
    }
    if (candidates.length > 1) {
      updateSettings({ batch_mode: true });
      await tick();
      await api.saveSettings(snapshot.settings);
    }
    stagedAutoStartIds = Array.from(
      new Set([...stagedAutoStartIds, ...candidates.map((media) => media.id)]),
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
      showModal(
        'Could not inspect requested media',
        'At least one file could not be read, so automatic processing was cancelled.',
      );
      return;
    }
    if (new Set(media.map((item) => item.kind)).size > 1) {
      pendingAutoStartIds = [];
      showModal(
        'Mixed media needs two runs',
        'Images and videos use different tasks. They remain in the media list; choose one type and start it from LocalSR.',
      );
      return;
    }
    if (media.some((item) => (settings.task === 'video') !== (item.kind === 'video'))) {
      pendingAutoStartIds = [];
      showModal(
        'Task does not match the requested media',
        'The files remain in LocalSR. Choose a compatible task or recipe, then start them manually.',
      );
      return;
    }
    const ready =
      media.every((item) => item.probe_status === 'ready') &&
      snapshot.runtime.worker === 'ready' &&
      !snapshot.runtime.active_job_id &&
      Boolean(settings.task) &&
      modelReady &&
      hdrModelCompatible &&
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
          previewPane?.actualSize();
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
    if (!benchmarkDevice)
      benchmarkDevice =
        snapshot.capabilities.devices.find((device) => device.id !== 'cpu')?.id ?? 'cpu';
    showModal(
      'Performance & Diagnostics',
      'Live values come from the isolated inference worker. Temperature is shown only when a supported backend reports it.',
      'performance',
    );
  }

  function benchmarkDeviceBarWidth(score: number, devices: BenchmarkDeviceResult[]): number {
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
      showModal(
        'Diagnostics copied',
        'Private file paths and media names were intentionally omitted.',
      );
    } catch (error) {
      showModal('Diagnostics', diagnostics || String(error));
    }
  }

  async function runBenchmark(): Promise<void> {
    if (benchmarkSetup !== 'idle' || benchmarkRunning || benchmarkStartBlockReason) return;
    const device = benchmarkDevice || 'cpu';
    benchmarkError = '';
    try {
      if (!benchmarkModel?.installed) {
        benchmarkSetup = 'downloading';
        await api.downloadModel('span_photo_x4', false);
      }
      benchmarkSetup = 'starting';
      await api.startBenchmark(device);
      await refresh();
    } catch (error) {
      benchmarkError = `Benchmark could not start: ${String(error)}`;
    } finally {
      benchmarkSetup = 'idle';
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
    kind: 'message' | 'performance' | 'integrations' = 'message',
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

  function resetView(): void {
    previewPane?.resetView();
  }

  function resetProgressivePreview(jobId = ''): void {
    lastLivePreviewSequence = 0;
    previewPane?.resetProgressivePreview(jobId);
  }

  function queueProgressiveTile(message: WorkerEnvelope): void {
    previewPane?.queueProgressiveTile(message, snapshot.runtime.active_job_id);
  }
</script>

<svelte:head
  ><title>{selectedMedia ? `${selectedMedia.name} — LocalSR` : 'LocalSR'}</title></svelte:head
>

<div
  class="app-shell"
  class:booting
  class:queue-timing-visible={Boolean(queueEta)}
  style={`--ui-scale:${settings.interface_scale / 100}`}
>
  <header class="toolbar">
    <nav class="compact-nav" aria-label="Workspace">
      <button class:active={page === 'media'} on:click={() => (page = 'media')}
        >Media <span>{snapshot.media.length}</span></button
      >
      <button class:active={page === 'preview'} on:click={() => (page = 'preview')}>Preview</button>
      <button class:active={page === 'enhance'} on:click={() => (page = 'enhance')}>Enhance</button>
    </nav>
    <button
      class="brand-mark"
      type="button"
      aria-label="Performance & diagnostics"
      title="Performance & diagnostics"
      on:click={showPerformance}><i></i><i></i><i></i></button
    >
    <div class="toolbar-actions">
      <button class="button compact benchmark-shortcut" type="button" on:click={showPerformance}
        >Run Benchmark</button
      >
      <button
        class="button primary compact add-media"
        disabled={settingsLocked}
        on:click={() => addFiles()}>＋ Add Media</button
      >
    </div>
  </header>

  <main class="workspace">
    <MediaQueue
      items={snapshot.media}
      jobs={snapshot.jobs}
      activeJobId={snapshot.runtime.active_job_id}
      batchMode={settings.batch_mode}
      compactHidden={page !== 'media'}
      scopeWarning={Boolean(selectedMedia) && !mediaMatchesTask}
      {singleScopeMessage}
      {benchmarkRunning}
      {inflightMediaIds}
      {setBatchMode}
      {selectQueueMedia}
      {addFiles}
      {addFolder}
      removeMedia={async (id) => {
        await api.removeMedia(id);
        await refresh();
      }}
      clearMedia={async () => {
        await api.clearMedia();
        await refresh();
      }}
    />

    <PreviewPane
      bind:this={previewPane}
      modelLabel={usingTemporalVideo
        ? (selectedVideoModel?.name ?? 'Video model')
        : (selectedModel?.name ?? 'Model')}
      activityLabel={snapshot.runtime.status_title}
      processing={Boolean(activeJobMediaId && selectedMedia?.id === activeJobMediaId)}
      activeTileSize={snapshot.runtime.active_tile_size}
      {selectedMedia}
      {resultPreview}
      comparisonError={selectedComparisonError}
      {completedVideoOutput}
      compactHidden={page !== 'preview'}
      addFiles={() => addFiles()}
    />

    <aside class="enhance-pane pane" class:compact-hidden={page !== 'enhance'}>
      <div class="pane-heading">
        <h1>Enhance</h1>
        <span
          >{settingsLocked
            ? 'Locked during processing'
            : settings.task
              ? 'Manual'
              : 'Choose a task'}</span
        >
      </div>
      <div
        bind:this={inspectorScroll}
        class="inspector-scroll"
        role="region"
        aria-label="Enhancement settings"
      >
        {#if settingsLocked}<div class="settings-lock" role="status">
            <strong
              >{cancelling
                ? 'Stopping your queue…'
                : startingJob
                  ? 'Starting your job…'
                  : 'Settings locked'}</strong
            >
            <p>
              {cancelling
                ? 'Controls unlock when processing has stopped and temporary output is removed.'
                : 'Your queue uses the settings shown below. Finish or cancel it to make changes. You can still view media and diagnostics.'}
            </p>
          </div>{/if}
        <fieldset
          class="processing-settings"
          disabled={settingsLocked}
          aria-label="Processing settings"
        >
          <section class="control-section">
            <span class="eyebrow">TASK</span>
            <div class="task-grid">
              <button
                disabled={selectedMedia?.kind === 'video'}
                class:active={settings.task === 'upscale'}
                on:click={() => setTask('upscale')}
                ><b>Upscale</b><span>Photos and artwork</span></button
              >
              <button
                disabled={selectedMedia?.kind === 'video'}
                class:active={settings.task === 'denoise'}
                on:click={() => setTask('denoise')}
                ><b>Denoise</b><span>Noise and blur</span></button
              >
              <button
                disabled={Boolean(selectedMedia) && selectedMedia.kind !== 'video'}
                class="video-task"
                class:active={settings.task === 'video'}
                on:click={() => setTask('video')}
                ><b>Upscale Video</b><span>Local video · HLG / PQ / SDR</span></button
              >
            </div>
          </section>

          {#if settings.task === 'video' && selectedMedia?.hdr_format}
            <section class="control-section" aria-label="HDR conversion">
              <span class="eyebrow"
                >{preservingHdr ? 'HDR INPUT · HDR OUTPUT · LABS' : 'HDR INPUT · SDR OUTPUT'}</span
              >
              <label class="field-label" for="hdr-output">Colour output</label>
              <select
                id="hdr-output"
                value={settings.video_hdr_mode ?? 'tone_map'}
                on:change={(event) =>
                  updateSettings({
                    video_hdr_mode: event.currentTarget.value as 'tone_map' | 'preserve',
                    ...(event.currentTarget.value === 'preserve'
                      ? { precision: 'fp32', deflicker: false }
                      : {}),
                  })}
              >
                <option value="preserve" disabled={!hdrPreservationAvailable}
                  >Preserve {selectedMedia.hdr_format} · 10-bit HEVC · Labs{!hdrPreservationAvailable
                    ? ' · unavailable for this model'
                    : ''}</option
                >
                <option value="tone_map">Convert to SDR · 8-bit H.264</option>
              </select>
              {#if preservingHdr}
                <p class="model-description">
                  Keeps {selectedMedia.hdr_format} in float precision through HAT and exports 10-bit BT.2020
                  HEVC. Source light and colour constrain the model’s added detail. HAT models were trained
                  on SDR; HDR detail quality is experimental.
                </p>
                <p class="model-description">
                  Thumbnails and live tiles use SDR tone mapping. Final playback depends on system
                  HDR support. Dolby Vision dynamic metadata is not retained; compatible HLG/PQ base
                  video is preserved.
                </p>
                {#if !hdrModelCompatible}<p class="inline-warning">
                    Select Frame-by-frame and a HAT model for HDR preservation.
                  </p>{/if}
              {:else}
                <p class="model-description">
                  This {selectedMedia.hdr_format} video is tone-mapped to SDR before enhancement and exported
                  as 8-bit SDR. The preview uses the same conversion.
                </p>
              {/if}
              {#if settings.selected_model_id === '__custom__' && !usingTemporalVideo}<p
                  class="model-description"
                >
                  HDR preservation remains available for custom checkpoints. The current adapter
                  requires HAT; compatibility is checked when the model loads.
                </p>{/if}
            </section>
          {/if}

          {#if settings.task === 'video' && selectedMedia?.audio_warning}
            <section class="control-section" aria-label="Audio compatibility">
              <span class="eyebrow">AUDIO COMPATIBILITY</span>
              <p class="model-description">{selectedMedia.audio_warning}</p>
            </section>
          {/if}

          {#if settings.task}
            <section class="control-section recipes">
              <span class="eyebrow">RECIPES</span>
              <div class="recipe-grid">
                <button on:click={() => applyPreset('quick')}
                  ><b>Quick</b><span>Fast and efficient</span></button
                >
                <button on:click={() => applyPreset('best')}
                  ><b>Best</b><span>Maximum quality</span></button
                >
              </div>
              <div class="recipe-tools">
                {#each snapshot.recipes as recipe (recipe.id)}
                  <div class="saved-recipe">
                    <button
                      disabled={Boolean(selectedMedia) &&
                        (recipe.task === 'video') !== (selectedMedia.kind === 'video')}
                      on:click={() => applyRecipe(recipe)}
                      ><b>{recipe.name}</b><span
                        >{recipe.task} · {recipe.output_scale}× · {recipe.precision.toUpperCase()}</span
                      ></button
                    ><button
                      aria-label={`Delete ${recipe.name}`}
                      on:click={async () => {
                        await api.deleteRecipe(recipe.id);
                        await refresh();
                      }}
                      ><svg
                        xmlns="http://www.w3.org/2000/svg"
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="2.5"
                        stroke-linecap="round"
                        stroke-linejoin="round"><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg
                      ></button
                    >
                  </div>
                {/each}
                {#if recipeEditorOpen}
                  <div class="recipe-input">
                    <input
                      aria-label="Recipe name"
                      placeholder="Name this setup"
                      bind:value={recipeName}
                      on:keydown={(event) => {
                        if (event.key === 'Enter') void addRecipe();
                        if (event.key === 'Escape') recipeEditorOpen = false;
                      }}
                    /><button on:click={addRecipe}>Save</button>
                  </div>
                {:else}
                  <button class="save-recipe" type="button" on:click={openRecipeEditor}
                    >＋ Save current setup as recipe</button
                  >
                {/if}
              </div>
            </section>

            <section class="control-section model-section">
              <label class="eyebrow" for="model-select">MODEL</label>
              {#if settings.task === 'video'}
                <label class="field-label" for="video-engine">Video engine</label>
                <select
                  id="video-engine"
                  value={settings.selected_video_model_id}
                  on:change={(event) => selectVideoEngine(event.currentTarget.value)}
                >
                  <option value="frame_by_frame">Frame-by-frame · compatible</option>
                  {#each snapshot.catalog.video_models as model}<option
                      value={model.model_id}
                      disabled={Boolean(temporalUnavailableReason)}
                      >{model.name}{temporalUnavailableReason
                        ? ' · Unavailable'
                        : model.installed
                          ? ' · Installed'
                          : ''}</option
                    >{/each}
                </select>
                {#if temporalUnavailableReason}<p class="model-output-note" role="status">
                    {temporalUnavailableReason}
                  </p>{/if}
              {/if}

              {#if !usingTemporalVideo}
                <label class="field-label" for="model-select"
                  >{settings.task === 'video' ? 'Frame model' : 'Checkpoint'}</label
                >
                <select
                  id="model-select"
                  value={settings.selected_model_id}
                  on:change={(event) => selectPrimaryModel(event.currentTarget.value)}
                >
                  {#each compatibleModels as model}<option value={model.model_id}
                      >{model.name}{model.installed
                        ? ' · Installed'
                        : ` · ${formatBytes(model.size_bytes)}`}</option
                    >{/each}
                  <option value="__custom__">Use my own checkpoint…</option>
                </select>
              {/if}
              {#if settings.task === 'video' && selectedMedia?.hdr_format && !hdrPreservationAvailable}
                <p class="model-output-note" role="status">
                  SDR output selected. This model cannot preserve HDR.
                </p>
              {/if}

              {#if settings.selected_model_id === '__custom__' && !usingTemporalVideo}
                <button class="button full" on:click={chooseCustomModel}
                  >{settings.custom_model_path
                    ? 'Choose another checkpoint…'
                    : 'Choose checkpoint…'}</button
                >
                <p class="path-text">
                  {settings.custom_model_path || 'Safetensors is strongly recommended.'}
                </p>
                {#if settings.custom_model_path && !settings.custom_model_path
                    .toLowerCase()
                    .endsWith('.safetensors')}
                  <label class="terms"
                    ><input
                      type="checkbox"
                      checked={settings.allow_unsafe_pickle_model}
                      on:change={(event) =>
                        updateSettings({ allow_unsafe_pickle_model: event.currentTarget.checked })}
                    /> Allow this pickle-based checkpoint. It may execute code inside the inference worker.</label
                  >
                {/if}
              {:else if currentDownloadTarget}
                <p class="model-description">{currentDownloadTarget.description}</p>
                <div class="license-line">
                  <span>{currentDownloadTarget.license_name}</span>
                  <span
                    >{currentDownloadTarget.commercial_use_allowed === false
                      ? 'Non-commercial only'
                      : currentDownloadTarget.commercial_use_allowed === null
                        ? 'Commercial terms unclear'
                        : currentDownloadTarget.author}</span
                  >
                </div>
                {#if licenseReviewModel}
                  {#key licenseReviewModel.model_id}<LicenseDownload
                      model={licenseReviewModel}
                      disabled={settingsLocked ||
                        (Boolean(activeDownload) && activeDownload !== licenseReviewModel.model_id)}
                      downloading={activeDownload === licenseReviewModel.model_id}
                      progress={snapshot.runtime.download_progress}
                      download={() => runDownload(licenseReviewModel)}
                      openLicense={() => api.openModelLicense(licenseReviewModel!.model_id)}
                      openSource={() => api.openModelSource(licenseReviewModel!.model_id)}
                    />{/key}
                {/if}
                {#if !licenseReviewModel && currentDownloadTarget.terms_acceptance_required && !currentDownloadTarget.installed}
                  <label class="terms"
                    ><input type="checkbox" bind:checked={termsAccepted} /> I reviewed the model license
                    and restrictions.</label
                  >
                {/if}
                {#if !currentDownloadTarget.installed && !licenseReviewModel}
                  <button
                    class="button full"
                    class:primary={currentDownloadTarget.automated_download_allowed}
                    disabled={(usingTemporalVideo && Boolean(temporalUnavailableReason)) ||
                      (Boolean(activeDownload) &&
                        activeDownload !== currentDownloadTarget.model_id)}
                    on:click={() => runDownload()}
                  >
                    {activeDownload === currentDownloadTarget.model_id
                      ? `Downloading ${Math.round(snapshot.runtime.download_progress)}% · Cancel`
                      : currentDownloadTarget.automated_download_allowed
                        ? `Download ${formatBytes('size_bytes' in currentDownloadTarget ? currentDownloadTarget.size_bytes : currentDownloadTarget.total_size_bytes)}`
                        : 'Choose externally downloaded checkpoint…'}
                  </button>
                  {#if activeDownload === currentDownloadTarget.model_id}<div
                      class="download-track"
                    >
                      <i style={`width:${snapshot.runtime.download_progress}%`}></i>
                    </div>{/if}
                {:else if currentDownloadTarget.installed}
                  <div class="installed-badge">✓ Installed · integrity checked before use</div>
                {/if}
              {/if}
              {#if usingTemporalVideo}
                <VideoMemory
                  lowMemory={settings.video_low_memory ?? true}
                  device={snapshot.capabilities.devices.find(
                    (device) => device.id === settings.device_id,
                  )}
                  memory={selectedMemory}
                  running={Boolean(
                    selectedMemory && snapshot.runtime.active_job_id === selectedMemory.job_id,
                  )}
                  {outputDimensions}
                  disabled={Boolean(snapshot.runtime.active_job_id)}
                  on:change={(event) => updateSettings({ video_low_memory: event.detail })}
                />
                <div class="field-row">
                  <label for="video-resolution">Output resolution</label><select
                    id="video-resolution"
                    value={settings.video_target_resolution ?? 0}
                    on:change={(event) =>
                      updateSettings({
                        video_target_resolution: Number(event.currentTarget.value),
                      })}
                    ><option value={0}>Match {settings.output_scale}× scale</option
                    >{#each [256, 512, 720, 1080, 1440, 2160] as resolution}<option
                        value={resolution}>{resolution} px · shorter edge</option
                      >{/each}</select
                  >
                </div>
                <p class="model-description">
                  Smaller output uses less memory. For a first test, try 256 or 512 px. This can
                  reduce the size of a large source video.
                </p>
                {#if selectedMedia && settings.video_target_resolution && settings.video_target_resolution < Math.min(selectedMedia.width, selectedMedia.height)}
                  <p class="notice">
                    This setting reduces {selectedMedia.width} × {selectedMedia.height} to {outputDimensions}.
                    Fine detail will be lost. Choose a larger output for a quality comparison.
                  </p>
                {/if}
              {/if}
            </section>

            {#if settings.task === 'upscale' && !usingTemporalVideo}
              <section class="control-section model-section" aria-labelledby="preprocess-heading">
                <label id="preprocess-heading" class="eyebrow" for="preprocess-model"
                  >RESTORE BEFORE UPSCALE</label
                >
                <select
                  id="preprocess-model"
                  value={settings.preprocess_model_id}
                  on:change={(event) =>
                    updateSettings({ preprocess_model_id: event.currentTarget.value })}
                >
                  <option value="">None · upscale directly</option>
                  {#each preprocessModels as model}
                    <option value={model.model_id}
                      >{model.name}{model.installed
                        ? ' · Installed'
                        : ` · ${formatBytes(model.size_bytes)}`}</option
                    >
                  {/each}
                </select>
                {#if preprocessModel}
                  <p class="model-description">
                    {preprocessModel.description} This lossless in-memory stage runs before the selected
                    upscaler.
                  </p>
                  <div class="license-line">
                    <span>{preprocessModel.license_name}</span><span>{preprocessModel.author}</span>
                  </div>
                  {#if !preprocessModel.installed}
                    <button
                      class="button full"
                      class:primary={preprocessModel.automated_download_allowed}
                      disabled={Boolean(activeDownload) &&
                        activeDownload !== preprocessModel.model_id}
                      on:click={() => runDownload(preprocessModel, false)}
                    >
                      {activeDownload === preprocessModel.model_id
                        ? `Downloading ${Math.round(snapshot.runtime.download_progress)}% · Cancel`
                        : preprocessModel.automated_download_allowed
                          ? `Download restoration model · ${formatBytes(preprocessModel.size_bytes)}`
                          : 'Choose externally downloaded checkpoint…'}
                    </button>
                    {#if activeDownload === preprocessModel.model_id}<div class="download-track">
                        <i style={`width:${snapshot.runtime.download_progress}%`}></i>
                      </div>{/if}
                  {:else}
                    <div class="installed-badge">
                      ✓ Restoration stage installed · integrity checked before use
                    </div>
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
                        showModal(
                          'Face detector unavailable',
                          'This preview keeps face-aware processing disabled unless a supported local detector is packaged for this platform. The normal model still works.',
                        );
                        return;
                      }
                      updateSettings({ enable_face_model: event.currentTarget.checked });
                    }}
                  />
                  Face-aware pass with {faceModel.name}
                  {settings.task === 'video' ? '· Labs' : ''}<em
                    >{faceEngineAvailable
                      ? 'checkpoint rights review required'
                      : 'detector unavailable'}</em
                  >
                </label>
                {#if !faceEngineAvailable}
                  <p class="model-description">
                    Visible for workflow parity, but disabled because the OpenCV face-detector
                    runtime is missing from this package.
                  </p>
                {:else if faceEnabled}
                  <p class="model-description">
                    Optional companion pass for faces. The exact checkpoint's redistribution and
                    training-data rights are not verified, so LocalSR only accepts a matching
                    user-supplied copy.
                  </p>
                  <div class="range-row">
                    <label for="face-fidelity"
                      >Face fidelity <b>{settings.face_fidelity}% restored</b></label
                    ><input
                      id="face-fidelity"
                      type="range"
                      min="0"
                      max="100"
                      step="5"
                      value={settings.face_fidelity}
                      on:input={(event) =>
                        updateSettings({ face_fidelity: Number(event.currentTarget.value) })}
                    /><small
                      >0% retains the resampled original face; 100% applies the strongest
                      restoration. Non-face regions keep the primary result.</small
                    >
                  </div>
                  {#if faceModel.terms_acceptance_required && !faceModel.installed}
                    <label class="terms"
                      ><input type="checkbox" bind:checked={faceTermsAccepted} /> I understand that the
                      checkpoint rights are unresolved and will provide a copy I am permitted to use.</label
                    >
                  {/if}
                  {#if !faceModel.installed}
                    <button
                      class="button full"
                      class:primary={faceModel.automated_download_allowed}
                      disabled={Boolean(activeDownload) && activeDownload !== faceModel.model_id}
                      on:click={() => runDownload(faceModel, faceTermsAccepted)}
                    >
                      {activeDownload === faceModel.model_id
                        ? `Downloading ${Math.round(snapshot.runtime.download_progress)}% · Cancel`
                        : faceModel.automated_download_allowed
                          ? `Download companion · ${formatBytes(faceModel.size_bytes)}`
                          : 'Choose externally downloaded face checkpoint…'}
                    </button>
                    {#if activeDownload === faceModel.model_id}<div class="download-track">
                        <i style={`width:${snapshot.runtime.download_progress}%`}></i>
                      </div>{/if}
                  {:else}
                    <div class="installed-badge">
                      ✓ Face companion installed · checked before use
                    </div>
                  {/if}
                {/if}
              </section>
            {/if}

            <AdvancedSettings
              {settings}
              {selectedModel}
              capabilities={snapshot.capabilities}
              {usingTemporalVideo}
              {updateSettings}
              {chooseOutput}
              refreshCapabilities={api.refreshCapabilities}
            />

            <section class="control-section summary-card">
              <span class="eyebrow">SUMMARY</span>
              <dl>
                <div>
                  <dt>Input</dt>
                  <dd>
                    {selectedMedia ? `${selectedMedia.width} × ${selectedMedia.height}` : '—'}
                  </dd>
                </div>
                <div>
                  <dt>Output</dt>
                  <dd>{outputDimensions}</dd>
                </div>
                <div>
                  <dt>Model</dt>
                  <dd>
                    {usingTemporalVideo
                      ? selectedVideoModel?.name
                      : (selectedModel?.name ??
                        (settings.selected_model_id === '__custom__' ? 'Custom' : '—'))}
                  </dd>
                </div>
                <div>
                  <dt>Device</dt>
                  <dd>
                    {snapshot.capabilities.devices.find(
                      (device) => device.id === settings.device_id,
                    )?.name ?? 'Detecting'}
                  </dd>
                </div>
              </dl>
            </section>
          {/if}
        </fieldset>
        <section class="about-block">
          <div class="about-heading"><b>LocalSR</b><span>{snapshot.app_version}</span></div>
          <button class="about-link" on:click={showPerformance}>Performance</button><button
            class="about-link"
            on:click={copyDiagnostics}>Copy diagnostics</button
          ><button class="about-link" on:click={showIntegrations}>System integrations</button
          ><UpdatePanel
            processing={settingsLocked || inflightMediaIds.size > 0 || Boolean(activeDownload)}
          />
          <p>Local processing · no media uploads<br />Models retain their own licenses.</p>
        </section>
      </div>
    </aside>
  </main>

  <footer class="status-bar">
    <div class="status-copy">
      <i class:working={Boolean(snapshot.runtime.active_job_id)}></i>
      <div>
        <strong
          >{booting ? 'Starting' : snapshot.runtime.status_title}{queuedCount
            ? ` · ${queuedCount} queued`
            : ''}</strong
        ><span
          >{booting
            ? 'Opening the trusted desktop control plane.'
            : snapshot.runtime.status_detail}</span
        >
        {#if queueEta}<span
            class="queue-eta"
            title="Approximate timings from measured jobs with the same model and settings. Model loading and export can change the estimate."
          >
            Current item: {queueEta.currentSeconds === null
              ? 'measuring…'
              : `≈ ${formatDuration(queueEta.currentSeconds)}`} · Whole queue: {queueEta.queueSeconds ===
            null
              ? `measuring ${queueEta.unknownItems} item${queueEta.unknownItems === 1 ? '' : 's'}…`
              : `≈ ${formatDuration(queueEta.queueSeconds)}`}
          </span><span class="queue-next"
            >Next: {queueEta.nextName} · {queueEta.nextSeconds === null
              ? 'time not yet measured'
              : `≈ ${formatDuration(queueEta.nextSeconds)}`}</span
          >{/if}
      </div>
    </div>
    {#if livePreviewWarning}<span class="inline-warning preview-warning" role="status"
        >Preview warning: {livePreviewWarning} Processing continues normally.</span
      >{/if}
    {#if snapshot.runtime.active_job_id}<div class="progress">
        <i style={`width:${snapshot.runtime.progress}%`}></i>
      </div>{/if}
    <div class="status-actions">
      {#if snapshot.runtime.last_output_path}<button
          class="button"
          on:click={() => api.revealResult(snapshot.runtime.last_output_path)}>Reveal</button
        ><button class="button" on:click={() => api.openResult(snapshot.runtime.last_output_path)}
          >Open</button
        >{/if}
      {#if snapshot.runtime.active_job_id}
        <button class="button queue-more" disabled={!canAppendToQueue} on:click={() => start()}
          >{settings.batch_mode
            ? `Add ${queueSelection.length} to queue`
            : 'Add selected to queue'}</button
        >
        <button class="button danger" disabled={cancelling} on:click={cancelWork}
          >{cancelling ? 'Cancelling…' : 'Cancel queue'}</button
        >
      {:else}
        <button class="button primary start" disabled={!canStart} on:click={() => start()}
          >{settings.batch_mode
            ? `Start ${queueSelection.length} item${queueSelection.length === 1 ? '' : 's'}`
            : settings.task === 'video'
              ? 'Start selected video'
              : settings.task === 'denoise'
                ? 'Denoise selected'
                : 'Upscale selected'}</button
        >
      {/if}
    </div>
  </footer>
</div>

{#if modalTitle}
  <div class="modal-backdrop" role="presentation" on:click={closeFromBackdrop}>
    <div
      class="modal"
      class:performance-modal={modalKind === 'performance'}
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
      tabindex="-1"
    >
      <h2 id="modal-title">{modalTitle}</h2>
      <p>{modalMessage}</p>
      {#if modalKind === 'performance'}
        <section class="benchmark-panel" aria-labelledby="benchmark-title">
          <div class="benchmark-heading">
            <div>
              <span class="eyebrow">BENCHMARK V2.1</span>
              <h3 id="benchmark-title">CPU & GPU Benchmarks</h3>
            </div>
            <span class="benchmark-private">100% local</span>
          </div>
          <p class="benchmark-intro">
            Choose one device for each run. CPU and GPU scores are saved separately, using the same
            fixed SPAN workload.
          </p>
          <p class="benchmark-intro">
            Compare scores from the same workload revision. Earlier results remain in your history.
          </p>

          <label class="field-label" for="benchmark-device">Benchmark device</label>
          <select
            id="benchmark-device"
            bind:value={benchmarkDevice}
            disabled={benchmarkRunning || benchmarkSetup !== 'idle'}
          >
            {#each snapshot.capabilities.devices as device}
              <option value={device.id}
                >{device.id === 'cpu' ? 'CPU' : 'GPU'} · {device.name} ({device.id.toUpperCase()})</option
              >
            {/each}
          </select>
          {#if !benchmarkModel?.installed && benchmarkSetup === 'idle'}
            <p class="benchmark-intro">
              The first run downloads and verifies SPAN Quick{benchmarkModel
                ? ` (${formatBytes(benchmarkModel.size_bytes)})`
                : ''}. It is reused for CPU and GPU benchmarks.
            </p>
          {/if}
          <div class="benchmark-actions">
            <button
              class="button primary"
              disabled={cancelling ||
                (!benchmarkRunning &&
                  (benchmarkSetup !== 'idle' || Boolean(benchmarkStartBlockReason)))}
              on:click={benchmarkRunning ? cancelWork : runBenchmark}
              >{cancelling && benchmarkRunning
                ? 'Cancelling…'
                : benchmarkRunning
                  ? 'Cancel Benchmark'
                  : benchmarkSetup === 'downloading'
                    ? `Downloading SPAN · ${Math.round(snapshot.runtime.download_progress)}%`
                    : benchmarkSetup === 'starting'
                      ? 'Starting benchmark…'
                      : !benchmarkModel?.installed
                        ? 'Download & run benchmark'
                        : 'Run Benchmark'}</button
            >
            {#if benchmarkSetup === 'downloading' && activeDownload === 'span_photo_x4'}<button
                class="button"
                on:click={() => api.cancelDownload('span_photo_x4')}>Cancel download</button
              >{/if}
            {#if snapshot.latest_benchmark}<button
                class="button"
                disabled={benchmarkRunning}
                on:click={copyBenchmark}>Copy JSON</button
              ><button class="button" disabled={benchmarkRunning} on:click={exportBenchmark}
                >Export JSON…</button
              >{/if}
          </div>
          {#if benchmarkError}<p class="error-message" role="alert">{benchmarkError}</p>{/if}
          {#if benchmarkSetup !== 'idle'}
            <p role="status">
              {benchmarkSetup === 'downloading'
                ? 'Downloading and verifying the benchmark model. The selected device will start automatically when it is ready.'
                : 'Starting the benchmark on the selected device…'}
            </p>
          {:else if !benchmarkRunning && benchmarkStartBlockReason}
            <p role="status">{benchmarkStartBlockReason}</p>
          {/if}

          {#if benchmarkRunning}
            <div class="benchmark-running-card" role="status" aria-live="polite">
              <div>
                <strong>{snapshot.runtime.status_title}</strong><b
                  >{Math.round(snapshot.runtime.progress)}%</b
                >
              </div>
              <span>{snapshot.runtime.status_detail}</span>
              <div
                class="download-track benchmark-progress"
                aria-label={`Benchmark ${Math.round(snapshot.runtime.progress)}%`}
              >
                <i style={`width:${snapshot.runtime.progress}%`}></i>
              </div>
            </div>
          {/if}

          <BenchmarkStudio
            tileMessage={benchmarkTile}
            renders={benchmarkRenders.length
              ? benchmarkRenders
              : benchmarkRunning
                ? []
                : (snapshot.latest_benchmark?.device_results ?? []).flatMap((device) =>
                    device.scenes.flatMap((scene) => (scene.preview ? [scene.preview] : [])),
                  )}
            running={benchmarkRunning}
          />

          {#if snapshot.latest_benchmark && !benchmarkRunning}
            {#if snapshot.latest_benchmark.workload_version.startsWith('localsr-benchmark-v2')}
              <div class:unstable={!snapshot.latest_benchmark.stable} class="benchmark-hero">
                <div class="benchmark-score-block">
                  <div class="benchmark-score-label">
                    <span
                      >Latest {snapshot.latest_benchmark.device_results?.[0]?.device_type === 'cpu'
                        ? 'CPU'
                        : 'GPU'} score</span
                    >
                    <span
                      class:unstable={!snapshot.latest_benchmark.stable}
                      class="benchmark-status-pill"
                    >
                      {snapshot.latest_benchmark.stable
                        ? '● Stable result'
                        : snapshot.latest_benchmark.cv_percent == null
                          ? '● Consistency unmeasured'
                          : '● Unstable result'}
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
                      <div class="benchmark-score-value">
                        <strong>{(snapshot.latest_benchmark.cpu_score ?? 0).toFixed(2)}</strong
                        ><span>output MP/s</span>
                      </div>
                      <p>CPU score across all three scenes</p>
                    {/if}
                  {:else if snapshot.latest_benchmark.cv_percent == null}
                    <div class="benchmark-score-value text-score">
                      <strong>Consistency unmeasured</strong>
                    </div>
                    <p>
                      This device completed too few repetitions within the measurement window. Scene
                      timings are saved below.
                    </p>
                  {:else}
                    <div class="benchmark-score-value text-score">
                      <strong>Run varied too much</strong>
                    </div>
                    <p>Close background apps and rerun for a publishable score.</p>
                  {/if}
                </div>

                <div class="benchmark-comparison">
                  {#if snapshot.latest_benchmark.stable && snapshot.latest_benchmark.reference_label && snapshot.latest_benchmark.reference_ratio != null}
                    <span>Compared with reference</span>
                    <strong>{snapshot.latest_benchmark.reference_ratio.toFixed(2)}×</strong>
                    <b>{snapshot.latest_benchmark.reference_label}</b>
                    <div
                      class="benchmark-reference-track"
                      aria-label={`${snapshot.latest_benchmark.reference_ratio.toFixed(2)} times the ${snapshot.latest_benchmark.reference_label} reference`}
                    >
                      <i
                        style={`width:${Math.max(4, Math.min(100, snapshot.latest_benchmark.reference_ratio * 50))}%`}
                      ></i>
                      <span>1×</span>
                    </div>
                  {:else}
                    <span>Result confidence</span>
                    <strong
                      >{snapshot.latest_benchmark.cv_percent == null
                        ? '—'
                        : `${snapshot.latest_benchmark.cv_percent.toFixed(1)}%`}</strong
                    >
                    <b
                      >{snapshot.latest_benchmark.cv_percent == null
                        ? 'more repetitions needed'
                        : 'timing spread · target ≤ 5%'}</b
                    >
                  {/if}
                </div>
              </div>

              <dl class="benchmark-facts">
                {#if snapshot.latest_benchmark.cv_percent == null}
                  <div>
                    <dt>Consistency</dt>
                    <dd>Too few repetitions</dd>
                  </div>
                {:else if snapshot.latest_benchmark.stable}
                  <div>
                    <dt>Consistency</dt>
                    <dd>{(snapshot.latest_benchmark.cv_percent ?? 0).toFixed(1)}% spread</dd>
                  </div>
                {:else}
                  <div>
                    <dt>Consistency</dt>
                    <dd class="warning-value">
                      {(snapshot.latest_benchmark.cv_percent ?? 0).toFixed(1)}% spread
                    </dd>
                  </div>
                {/if}
                <div>
                  <dt>Total time</dt>
                  <dd>
                    {formatDuration(
                      snapshot.latest_benchmark.result_elapsed_seconds ??
                        snapshot.latest_benchmark.total_elapsed_seconds,
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Workload</dt>
                  <dd>v2 · 3 fixed scenes</dd>
                </div>
              </dl>

              {#if benchmarkScores.length}
                <div class="benchmark-section-title">
                  <strong>Hardware results</strong><span>Relative throughput</span>
                </div>
                <div class="benchmark-devices" aria-label="Per-device benchmark results">
                  {#each benchmarkScores as device (device.device)}
                    <article
                      class="benchmark-device"
                      class:cpu={device.device_type === 'cpu'}
                      aria-label={`${device.device_name} benchmark result`}
                    >
                      <div class="benchmark-device-heading">
                        <div>
                          <span>{device.device_type === 'cpu' ? 'CPU' : 'GPU'}</span><strong
                            >{device.device_name}</strong
                          >
                        </div>
                        <div class="benchmark-device-score">
                          <strong>{device.score.toFixed(2)}</strong><span>output MP/s</span>
                        </div>
                      </div>
                      <div class="benchmark-device-track" aria-hidden="true">
                        <i
                          style={`width:${benchmarkDeviceBarWidth(device.score, benchmarkScores)}%`}
                        ></i>
                      </div>
                      <div class="benchmark-device-meta">
                        <span class:warning-value={!device.stable}
                          >{device.stable
                            ? '● Stable'
                            : device.cv_percent == null
                              ? '● Unmeasured consistency'
                              : '● Unstable'}</span
                        >
                        <span
                          >{device.cv_percent == null
                            ? 'Too few repetitions'
                            : `${device.cv_percent.toFixed(1)}% spread`}</span
                        >
                        <span>{device.thermal_state}</span>
                        {#if device.completed_at_unix}<span
                            >Saved {new Date(
                              device.completed_at_unix * 1000,
                            ).toLocaleString()}</span
                          >{/if}
                      </div>
                      <details class="benchmark-scenes">
                        <summary>Scene breakdown</summary>
                        {#each device.scenes as scene (scene.scene_id)}
                          <div class="benchmark-scene">
                            <span class="scene-name">{benchmarkSceneLabel(scene.scene_id)}</span>
                            <strong>{scene.megapixels_per_second.toFixed(2)} MP/s</strong>
                            <span class="scene-metric">{scene.median_ms.toFixed(0)} ms</span>
                            <span class="scene-metric subtle"
                              >{scene.encode_ms != null
                                ? `+${scene.encode_ms.toFixed(0)} ms encode`
                                : `${scene.iterations} runs`}</span
                            >
                          </div>
                        {/each}
                      </details>
                    </article>
                  {/each}
                </div>
              {/if}
              <p class="benchmark-note">
                Each score is the geometric mean of that device’s output throughput across three
                scenes. Running CPU preserves the last GPU result, and vice versa. Results over 5%
                timing spread are marked unstable.
              </p>
            {:else}
              <div class="benchmark-hero legacy-score">
                <div class="benchmark-score-block">
                  <div class="benchmark-score-label">
                    <span>Legacy score</span><span class="benchmark-status-pill">V1 workload</span>
                  </div>
                  <div class="benchmark-score-value">
                    <strong>{snapshot.latest_benchmark.score.toFixed(2)}</strong><span>points</span>
                  </div>
                  <p>Keep this result for comparison with other v1 runs only.</p>
                </div>
              </div>
              <dl class="benchmark-facts legacy-facts">
                <div>
                  <dt>Median / p95</dt>
                  <dd>
                    {snapshot.latest_benchmark.median_inference_ms.toFixed(1)} / {snapshot.latest_benchmark.p95_inference_ms.toFixed(
                      1,
                    )} ms
                  </dd>
                </div>
                <div>
                  <dt>Throughput</dt>
                  <dd>
                    {snapshot.latest_benchmark.end_to_end_fps.toFixed(2)} fps · {snapshot.latest_benchmark.processed_megapixels_per_second.toFixed(
                      3,
                    )} MP/s
                  </dd>
                </div>
                <div>
                  <dt>Device</dt>
                  <dd>
                    {snapshot.latest_benchmark.device} · {snapshot.latest_benchmark.model_name}
                  </dd>
                </div>
                <div>
                  <dt>Peak process memory</dt>
                  <dd>
                    {snapshot.latest_benchmark.peak_memory_bytes
                      ? formatBytes(snapshot.latest_benchmark.peak_memory_bytes)
                      : 'Not reliably available'}
                  </dd>
                </div>
              </dl>
              <p class="benchmark-note">
                This is an older v1 result. Run the benchmark again to get the separate CPU or GPU
                v2 score.
              </p>
            {/if}
          {:else if !benchmarkRunning}
            <div class="benchmark-empty">
              <div class="benchmark-empty-gauge" aria-hidden="true"><i></i></div>
              <div>
                <strong>No benchmark result yet</strong><span
                  >Run the fixed workload to measure this system and create a local score.</span
                >
              </div>
            </div>
          {/if}
        </section>

        {#if benchmarkRunning}
          <div class="performance-section-heading">
            <strong>Benchmark hardware</strong><span>Selected for this run</span>
          </div>
          <dl class="performance-grid">
            <div>
              <dt>Backend</dt>
              <dd>{benchmarkHardware?.type?.toUpperCase() ?? 'Detecting'}</dd>
            </div>
            <div>
              <dt>Device</dt>
              <dd>{benchmarkHardware?.name ?? 'Detecting'}</dd>
            </div>
            <div>
              <dt>Device capacity</dt>
              <dd>
                {benchmarkHardware?.id === 'cpu'
                  ? 'Uses system RAM'
                  : benchmarkHardware?.total_memory
                    ? formatBytes(benchmarkHardware.total_memory)
                    : 'Not reported'}
              </dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>SPAN · fixed benchmark workload</dd>
            </div>
            <div>
              <dt>Timing and memory</dt>
              <dd>Measurements appear in the completed scene results.</dd>
            </div>
          </dl>
        {:else}
          <div class="performance-section-heading">
            <strong>Live hardware</strong><span>Current worker state</span>
          </div>
          <dl class="performance-grid">
            <div>
              <dt>Backend</dt>
              <dd>{activeDevice?.type?.toUpperCase() ?? 'Detecting'}</dd>
            </div>
            <div>
              <dt>Device</dt>
              <dd>{activeDevice?.name ?? 'Detecting'}</dd>
            </div>
            <div>
              <dt>Worker</dt>
              <dd>{snapshot.runtime.worker}</dd>
            </div>
            <div>
              <dt>Device memory</dt>
              <dd>
                {snapshot.runtime.device_free_memory
                  ? `${formatBytes(snapshot.runtime.device_free_memory)} free · ${formatBytes(snapshot.runtime.device_allocated_memory)} used`
                  : activeDevice?.free_memory
                    ? `${formatBytes(activeDevice.free_memory)} free`
                    : 'Not reported'}
              </dd>
            </div>
            <div>
              <dt>System memory</dt>
              <dd>
                {formatBytes(
                  snapshot.runtime.live_system_ram_available ||
                    snapshot.capabilities.system_ram_available,
                )} available
              </dd>
            </div>
            <div>
              <dt>Memory pressure</dt>
              <dd>
                {Math.round(
                  snapshot.runtime.live_memory_pressure_percent ||
                    snapshot.capabilities.system_memory_pressure_percent,
                )}% · {snapshot.capabilities.system_memory_pressure_level}
              </dd>
            </div>
            <div>
              <dt>Throughput</dt>
              <dd>
                {snapshot.runtime.throughput
                  ? `${snapshot.runtime.throughput.toFixed(2)} ${snapshot.runtime.throughput_unit}`
                  : 'Waiting for a job'}
              </dd>
            </div>
            <div>
              <dt>Elapsed / ETA</dt>
              <dd>
                {formatDuration(snapshot.runtime.elapsed_seconds)} / {snapshot.runtime
                  .estimated_remaining_seconds
                  ? formatDuration(snapshot.runtime.estimated_remaining_seconds)
                  : '—'}
              </dd>
            </div>
            <div>
              <dt>Tile</dt>
              <dd>
                {snapshot.runtime.active_tile_size || settings.tile_size}px · halo {settings.halo}px
              </dd>
            </div>
            <div>
              <dt>Thermals</dt>
              <dd>{snapshot.runtime.thermal_status}</dd>
            </div>
          </dl>
        {/if}
      {:else if modalKind === 'integrations' && integration}
        <p class="integration-detail">
          Command: <code>{integration.command_name}</code><br />Platform: {integration.platform}
        </p>
      {:else if diagnostics}
        <textarea readonly>{diagnostics}</textarea>
      {/if}
      <div class="modal-actions">
        {#if modalKind === 'performance'}<button class="button" on:click={copyDiagnostics}
            >Copy diagnostics</button
          >{/if}
        {#if modalKind === 'integrations' && integration}<button
            class="button"
            class:danger={integration.installed}
            on:click={() => changeIntegrations(!integration?.installed)}
            >{integration.installed ? 'Remove integrations' : 'Install integrations'}</button
          >{/if}
        <button class="button primary" on:click={closeModal}>OK</button>
      </div>
    </div>
  </div>
{/if}
