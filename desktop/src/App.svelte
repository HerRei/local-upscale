<script lang="ts">
  import { detectPlatform } from './lib/platform';

  import { tabFromKey } from './lib/keyboard';
  import { recipeFixStages, recipeMatches } from './lib/recipes';
  import { benchmarkDeviceBarWidth, benchmarkSceneLabel } from './lib/benchmark';
  import { without, mediaNamesFor as mediaNamesForPaths } from './lib/collections';
  import {
    presetDownload,
    supportsHdrPreservation as catalogSupportsHdrPreservation,
  } from './lib/plan';
  import { onMount, tick } from 'svelte';
  import PreviewPane from './PreviewPane.svelte';
  import ComparePane from './ComparePane.svelte';
  import {
    outputGroups,
    pickKey,
    toggleSlot,
    swapSlots,
    type CompareItem,
  } from './lib/compareOutputs';
  import {
    ORIGINAL,
    alignedFraming,
    chooseTarget,
    describeSide,
    effectiveChoice,
    framingRounding,
    slotFor,
    stepShowing,
  } from './lib/results';
  import { EtaDisplay, etaView, withEta } from './lib/eta-display';
  import MediaQueue from './MediaQueue.svelte';
  import { mediaInRun, queueRunSince } from './lib/queueRun';
  import { isSaving } from './lib/rowRun';
  import AdvancedSettings from './AdvancedSettings.svelte';
  import FileName from './FileName.svelte';
  import HelpTip from './HelpTip.svelte';
  import FFmpegNotice from './FFmpegNotice.svelte';
  import { exportNeedsExternalFFmpeg, probeNeedsExternalFFmpeg, systemCodecs } from './lib/ffmpeg';
  import VideoMemory from './VideoMemory.svelte';
  import ModelCredit from './ModelCredit.svelte';
  import UpdateNotice from './UpdateNotice.svelte';
  import UpdatePanel from './UpdatePanel.svelte';
  import WelcomeNotice from './WelcomeNotice.svelte';
  import { startUpdateWatcher, updateDialogOpen } from './lib/updates';
  import BenchmarkStudio from './BenchmarkStudio.svelte';
  import * as api from './lib/api';
  import ModelLibrary from './ModelLibrary.svelte';
  import {
    EDIT_FIT_LABELS,
    editDevice,
    editDimensions,
    editFamilyLabel,
    editFitFor,
    editHardwareLabel,
    editModelFits,
    editSizeLimit,
    editVariantName,
    recommendAnyEditModel,
  } from './lib/editing';
  import {
    FIT_LABELS,
    FIX_LABELS,
    choosePresetModel,
    displayName,
    fitFor,
    fixesOf,
    frameRateChange,
    libraryModelsForTask,
    modelsAvailableBeyondSelection,
    modelsForTask,
    pendingDownloads,
    presetSlot,
    resolvePlan,
  } from './lib/plan';
  import {
    UNSTABLE_FIX_MODELS,
    chooseFixModel,
    fitsFix,
    fixQuality,
    fixSlot,
    fixStageKind,
    moveFixStage,
    toggleFixStage,
  } from './lib/fixStages';
  import { applyWorkerEnvelope, resultPreviewForSelectedMedia } from './lib/workerEvents';
  import { formatBytes, formatDuration } from './lib/format';
  import { demoSnapshot } from './lib/demo';
  import { modal } from './lib/modal';
  import { engineLabel, hardwarePatch, withListedDevice } from './lib/neuralEngine';
  import { cpuHint, deviceLabel } from './lib/hardwareRoutes';
  import type {
    AppSnapshot,
    ComparePick,
    BenchmarkDeviceResult,
    BenchmarkRender,
    CatalogModel,
    CatalogVideoModel,
    EditModel,
    FixKind,
    FixStageSelection,
    IntegrationStatus,
    LaunchIntent,
    MediaItem,
    MediaResults,
    PairChoice,
    Quality,
    Recipe,
    ResultSummary,
    StartBatchInput,
    TaskKind,
    UiSettings,
    WorkerEnvelope,
  } from './lib/types';

  let snapshot: AppSnapshot = demoSnapshot();
  let booting = true;
  let editPrompt = '';
  let editRuntimeOverride = '';
  let benchmarkRenders: BenchmarkRender[] = [];
  let benchmarkDevice = '';
  let benchmarkSetup: 'idle' | 'downloading' | 'starting' = 'idle';
  let benchmarkError = '';
  let benchmarkTile: WorkerEnvelope | undefined;
  $: benchmarkScores = snapshot.latest_benchmark?.device_history?.length
    ? snapshot.latest_benchmark.device_history
    : (snapshot.latest_benchmark?.device_results ?? []);

  let page: 'media' | 'preview' | 'enhance' = 'preview';
  let mainTab: 'preview' | 'compare' = 'preview';
  let mediaQueue: MediaQueue | undefined;
  let compareLayout: 'slider' | 'boxes' = 'slider';
  let comparePicks: ComparePick[] = [];
  let bulkHistories: MediaResults[] = [];
  let bulkHistoryError = '';
  let bulkKey = '';
  let bulkRequest = 0;
  let compareRequestError = '';
  let comparePending = false;
  let compareRevision = 0;
  $: desiredBulkKey = JSON.stringify([
    snapshot.media.map((m) => m.id),
    snapshot.jobs.map((j) => [j.id, j.status]),
  ]);
  $: if (desiredBulkKey !== bulkKey) void loadBulkHistories(desiredBulkKey);
  $: compareGroups = outputGroups(snapshot.media, bulkHistories);
  $: compareItems = comparePicks.map((pick, index): CompareItem => {
    const group = compareGroups.find((entry) => entry.media.id === pick.media_id);
    const media = snapshot.media.find((entry) => entry.id === pick.media_id);
    const result =
      pick.kind === 'output'
        ? group?.outputs.find((entry) => pickKey(entry.pick) === pickKey(pick))?.result
        : undefined;
    const candidate = snapshot.runtime.compare?.slots[index];
    const slot = candidate && pickKey(candidate.pick) === pickKey(pick) ? candidate : undefined;
    return {
      key: pickKey(pick),
      pick,
      media_name: media?.name ?? 'Image',
      label: pick.kind === 'original' ? 'Original' : result?.model_label || 'Earlier result',
      meta: result
        ? [
            result.ui?.quality,
            result.scale ? `${result.scale}×` : '',
            result.elapsed_seconds ? formatDuration(result.elapsed_seconds) : '',
          ]
            .filter(Boolean)
            .join(' · ')
        : `${media?.width ?? 0} × ${media?.height ?? 0}`,
      width: slot?.width || media?.width || 1,
      height: slot?.height || media?.height || 1,
      preview_data_url: slot?.preview_data_url ?? '',
      preview_width: slot?.preview_width ?? 0,
      preview_height: slot?.preview_height ?? 0,
      region: slot?.region,
      status: slot?.status ?? 'loading',
      error: slot?.error ?? '',
    };
  });
  let mainTabs: HTMLElement | undefined;
  // The update notice docks over the bottom of the stage; what sits there moves up by this.
  let updateNoticeHeight = 0;
  async function mainTabKey(event: KeyboardEvent): Promise<void> {
    const next = tabFromKey(event.key, mainTab === 'preview' ? 0 : 1, 2);
    if (next === null) return;
    event.preventDefault();
    mainTab = next === 0 ? 'preview' : 'compare';
    // The tabs sit in the header of the pane they show, so they are drawn anew.
    await tick();
    mainTabs?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  }
  async function loadBulkHistories(key: string): Promise<void> {
    bulkKey = key;
    const request = ++bulkRequest;
    if (!api.isTauri()) return;
    try {
      const histories = await api.listAllMediaResults();
      if (request === bulkRequest) {
        bulkHistories = histories;
        bulkHistoryError = '';
      }
    } catch (error) {
      if (request === bulkRequest) bulkHistoryError = String(error);
    }
  }
  // A pick whose image left the list (Remove, Clear) has nothing to show and no way to be unticked.
  $: if (comparePicks.some((pick) => !snapshot.media.some((media) => media.id === pick.media_id)))
    setComparePicks(
      comparePicks.filter((pick) => snapshot.media.some((media) => media.id === pick.media_id)),
    );
  // A pick whose result has left the list has nothing to show either.
  $: if (
    bulkHistories.length &&
    comparePicks.some(
      (pick) =>
        pick.kind === 'output' &&
        bulkHistories.some((history) => history.media_id === pick.media_id) &&
        !compareGroups.some((group) =>
          group.outputs.some((output) => pickKey(output.pick) === pickKey(pick)),
        ),
    )
  )
    setComparePicks(
      comparePicks.filter(
        (pick) =>
          pick.kind === 'original' ||
          compareGroups.some((group) =>
            group.outputs.some((output) => pickKey(output.pick) === pickKey(pick)),
          ),
      ),
    );
  // Compare is open only while there is something to compare.
  $: if (mainTab === 'compare' && comparePicks.length < 2) mainTab = 'preview';
  function setComparePicks(picks: ComparePick[]): void {
    if (picks.length >= 3 && comparePicks.length < 3) compareLayout = 'boxes';
    comparePicks = picks;
    compareRevision++;
    void sendComparePicks();
  }
  async function sendComparePicks(): Promise<void> {
    if (comparePending || !api.isTauri()) return;
    comparePending = true;
    let sent: number;
    do {
      sent = compareRevision;
      try {
        await api.requestCompare(comparePicks);
        if (sent === compareRevision) compareRequestError = '';
      } catch (error) {
        if (sent === compareRevision) compareRequestError = String(error);
      }
    } while (sent !== compareRevision);
    comparePending = false;
    await refresh();
  }

  let recipeEditorOpen = false;
  let recipeName = '';
  // Model terms are accepted per model: several fixes can each need their own.
  let termsAccepted: Record<string, boolean> = {};
  // How a fix is named in its row's buttons.
  const FIX_NAMES: Record<Exclude<FixKind, 'faces'>, string> = {
    blur: 'blur',
    jpeg: 'JPEG',
    noise: 'noise',
  };
  let faceTermsAccepted = false;
  let previewPane: PreviewPane | undefined;
  let inspectorScroll: HTMLDivElement | undefined;
  let lastLivePreviewSequence = 0;
  let diagnostics = '';
  let integration: IntegrationStatus | null = null;
  let modalKind: 'message' | 'performance' | 'integrations' = 'message';
  let modalTitle = '';
  let modalMessage = '';
  let modalElement: HTMLElement | undefined;
  /** Set for a yes/no question: the modal then offers Cancel and the destructive action. */
  let modalConfirm: {
    label: string;
    run: () => Promise<void>;
    /** Words the question for the list as it is now. */
    titleFor?: (count: number) => string;
    /** What the question counts; the Inputs by default. */
    countOf?: () => number;
  } | null = null;
  // The Clear question counts the list as it is when answered, not as it was when asked.
  $: if (modalConfirm?.titleFor) {
    const left = modalConfirm.countOf ? modalConfirm.countOf() : snapshot.media.length;
    if (left) modalTitle = modalConfirm.titleFor(left);
    else closeModal();
  }
  // The FFmpeg notice: once per batch of videos LocalSR cannot decode, and
  // before an H.264/HEVC export starts without a selected FFmpeg.
  let ffmpegNotice: { context: 'open' | 'export'; paths: string[] } | null = null;
  let stateRefresh: ReturnType<typeof setTimeout> | undefined;
  let runtimePulseTimer: ReturnType<typeof setTimeout> | undefined;
  let pendingRuntimePulse: WorkerEnvelope | undefined;
  let autoStartTimer: ReturnType<typeof setTimeout> | undefined;
  let pendingAutoStartIds: string[] = [];
  let stagedAutoStartIds: string[] = [];
  let handlingLaunchIntents = false;
  let lastImageTask: Exclude<TaskKind, 'video'> = 'upscale';
  let livePreviewWarning = '';
  let preparing = false;
  let preparingCancelled = false;
  let preparingModelId = '';
  const isMac = typeof navigator !== 'undefined' && /Mac/.test(navigator.platform);
  let libraryOpen = false;
  let libraryContext: {
    slot: 'primary' | 'fix' | 'face' | 'browse' | 'edit' | 'try';
    fix?: Exclude<FixKind, 'faces'>;
  } = { slot: 'browse' };
  let startingJob = false;
  let requestingCancel = false;
  // Between two jobs of a queue the host already runs the next one while the window still
  // shows none: its row is queued or starting until the next refresh brings it up.
  $: settingsLocked =
    startingJob ||
    Boolean(snapshot.runtime.active_job_id) ||
    snapshot.jobs.some((job) => job.status === 'queued' || job.status === 'starting');
  $: cancelling =
    requestingCancel ||
    snapshot.runtime.status_title === 'Cancelling' ||
    snapshot.jobs.some((job) => job.status === 'cancelling');
  // A cancel asked for, the engine not yet idle: what the window says until it is.
  $: stoppingJobs = snapshot.jobs.filter((job) => job.status === 'cancelling').length;
  $: stoppingDetail = `${
    stoppingJobs > 1
      ? `Stopping ${stoppingJobs} photos; waiting for the engine`
      : 'Waiting for the engine to stop'
  } (up to ${snapshot.runtime.cancel_grace_seconds || 8} s).`;
  let stopWasOn = false;
  let stopRunSince: number | null = null;
  let stoppedNote = '';
  $: trackStop(cancelling, runSince);
  /** The count of finished pictures of the run, shown once when the engine is idle again. */
  function trackStop(now: boolean, since: number | null): void {
    if (now && !stopWasOn) {
      stopRunSince = since;
      stoppedNote = '';
    } else if (!now && stopWasOn && !snapshot.runtime.active_job_id) {
      const own = snapshot.jobs.filter(
        (job) => stopRunSince !== null && job.created_at >= stopRunSince,
      );
      stoppedNote = `Stopped. ${own.filter((job) => job.status === 'completed').length} of ${own.length} finished.`;
    }
    stopWasOn = now;
  }
  $: if (snapshot.runtime.active_job_id && !cancelling) stoppedNote = '';

  $: settings = withListedDevice(snapshot.settings, snapshot.capabilities);
  // Editing bundle, device and capacity checks.
  $: editModels = snapshot.edit.models;
  $: editingDevice = editDevice(snapshot.capabilities, settings);
  $: editModel = editModels.find((model) => model.model_id === settings.edit_model_id);
  $: editFit = editModel ? editFitFor(editModel, snapshot.capabilities, editingDevice) : 'unknown';
  $: editSizeAllowed = editSizeLimit(snapshot.capabilities, editingDevice);
  $: editRuntime = editRuntimeOverride || snapshot.edit.runtime_path;
  $: editSupported = snapshot.engine?.features.includes('image_edit') === true;
  $: editBlockReason =
    settings.task !== 'edit'
      ? ''
      : !editingDevice
        ? 'Editing needs Apple Silicon or a CUDA/ROCm GPU; none was detected.'
        : !editSupported
          ? 'This engine cannot edit yet. Update LocalSR.'
          : !editRuntime
            ? 'The editing runtime is missing from this build.'
            : !editModel
              ? 'Choose an editing model.'
              : editFit === 'too_heavy'
                ? `${editModel.name} needs ${editingDevice.id === 'mps' ? `${editModel.min_unified_memory_gb} GB of unified memory` : `a GPU with ${editModel.min_vram_gb} GB`} and ${editHardwareLabel(snapshot.capabilities, editingDevice)}. ${editModels.some((model) => editModelFits(model, snapshot.capabilities, editingDevice)) ? 'Pick a smaller model under Change….' : 'No listed model fits this computer yet.'}`
                : '';
  $: editReady =
    settings.task !== 'edit' ||
    (!editBlockReason &&
      Boolean(editModel?.installed) &&
      editPrompt.trim().length > 0 &&
      editPrompt.length <= 4000 &&
      (!editModel?.terms_acceptance_required || termsAccepted[editModel.model_id]));
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
  // The fixes this job runs, in order. In Restore the last one is the model itself.
  $: imageFixes =
    settings.task === 'upscale' || settings.task === 'denoise' ? settings.fix_stages : [];
  // Engines without image_multi_fix run one fix at most.
  $: fixPlanSupported =
    imageFixes.length <= 1 || snapshot.engine?.features.includes('image_multi_fix') === true;
  $: faceEngineAvailable = snapshot.engine?.features.includes('face_aware') === true;
  $: faceEnabled = settings.enable_face_model && faceEngineAvailable;
  $: usingTemporalVideo =
    settings.task === 'video' && settings.selected_video_model_id !== 'frame_by_frame';
  $: temporalUnavailableReason = !snapshot.engine?.video_engines.includes('seedvr2')
    ? 'This installation supports frame-by-frame video. SeedVR2 requires a compatible CUDA, ROCm or Metal engine.'
    : /^(directml|xpu|openvino)(:|$)/.test(settings.device_id)
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
  $: modelReady =
    settings.task === 'edit'
      ? editReady
      : usingTemporalVideo
        ? !temporalUnavailableReason && Boolean(selectedVideoModel?.installed)
        : settings.selected_model_id === '__custom__'
          ? Boolean(settings.custom_model_path) &&
            (!customModelNeedsOptIn || settings.allow_unsafe_pickle_model)
          : Boolean(selectedModel?.installed);
  $: faceReady = !faceEnabled || Boolean(faceModel?.installed);
  $: interpolationStage = plan.find((stage) => stage.kind === 'interpolate');
  $: interpolationReady = !interpolationStage || interpolationStage.installed;
  $: fixesReady =
    fixPlanSupported &&
    imageFixes.every((selection) => {
      const model = snapshot.catalog.models.find((entry) => entry.model_id === selection.model_id);
      return Boolean(model?.installed && fitsFix(model, selection.fix));
    }) &&
    (settings.task !== 'denoise' ||
      imageFixes.length === 0 ||
      imageFixes.at(-1)?.model_id === settings.selected_model_id);
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
  // While a queue runs only new input can be added: a picture with any job in this run
  // (waiting, running, finished, failed or cancelled) is not offered again.
  let runSince: number | null = null;
  $: runSince = queueRunSince(snapshot.jobs, runSince, cancelling);
  $: runMedia = mediaInRun(snapshot.jobs, runSince);
  $: newMedia = runnableMedia.filter((media) => !runMedia.has(media.id));
  // What the status bar adds to the engine's phase text while a picture is processed: the model,
  // the live frame and a note for an HDR source shown as SDR.
  let liveFrame = -1;
  $: activityModel =
    settings.task === 'edit'
      ? (editModel?.name ?? 'Editing model')
      : usingTemporalVideo
        ? (selectedVideoModel?.name ?? 'Video model')
        : (selectedModel?.name ?? 'Model');
  $: activityDetail =
    cancelling && snapshot.runtime.active_job_id
      ? stoppingDetail
      : activeJobMediaId
        ? [
            activityModel,
            runningStatusDetail,
            liveFrame >= 0 ? `Frame ${liveFrame + 1}` : '',
            selectedMedia?.hdr_format && selectedMedia.id === activeJobMediaId
              ? 'SDR display preview'
              : '',
          ]
            .filter(Boolean)
            .join(' · ')
        : '';
  $: queueSelection = cancelling
    ? []
    : settings.batch_mode && settings.task !== 'edit'
      ? snapshot.runtime.active_job_id
        ? newMedia
        : queueableMedia
      : selectedMedia &&
          mediaMatchesTask &&
          !(snapshot.runtime.active_job_id ? runMedia : inflightMediaIds).has(selectedMedia.id)
        ? [selectedMedia]
        : [];
  // Everything a start needs except which items it takes.
  $: queueReady =
    !startingJob &&
    !cancelling &&
    snapshot.runtime.worker === 'ready' &&
    Boolean(settings.task) &&
    modelReady &&
    hdrModelCompatible &&
    faceReady &&
    fixesReady &&
    interpolationReady &&
    snapshot.runtime.status_title !== 'Cancelling';
  const allReady = (items: MediaItem[]): boolean =>
    items.length > 0 && items.every((media) => media.probe_status === 'ready');
  $: canQueue = queueReady && allReady(queueSelection);
  $: canStart = !snapshot.runtime.active_job_id && canQueue;
  // The one picture the selection names, and the batch the task could take: the two scopes
  // the status bar offers side by side when there is a choice.
  $: selectedOnly =
    selectedMedia && mediaMatchesTask && !inflightMediaIds.has(selectedMedia.id)
      ? [selectedMedia]
      : [];
  $: scopeChoice =
    !cancelling &&
    snapshot.media.length > 1 &&
    settings.task !== 'edit' &&
    queueableMedia.length > 1 &&
    !snapshot.runtime.active_job_id;
  $: canStartOne = !snapshot.runtime.active_job_id && queueReady && allReady(selectedOnly);
  $: canStartAll = !snapshot.runtime.active_job_id && queueReady && allReady(queueableMedia);
  // Edit always takes the selected image, and one item is no batch to count.
  $: batchLabel = settings.batch_mode && settings.task !== 'edit' && snapshot.media.length > 1;
  $: canAppendToQueue =
    Boolean(snapshot.runtime.active_job_id) && canQueue && settings.task !== 'edit';
  $: queuedCount = snapshot.jobs.filter((job) => job.status === 'queued').length;
  // The times shown count down by themselves: the view is worked out again every second
  // while a job runs, not only when the engine reports.
  const etaDisplay = new EtaDisplay();
  let etaSecond = 0;
  let etaTimer: ReturnType<typeof setInterval> | undefined;
  const secondsNow = (_second: number) => performance.now() / 1000;
  $: shownEta = etaView(snapshot, etaDisplay, secondsNow(etaSecond));
  $: queueEta = shownEta.timing;
  // Past the last tile only the file is left to write: nothing remains to estimate.
  $: saving = Boolean(snapshot.runtime.active_job_id) && isSaving(snapshot.runtime.progress);
  $: runningStatusDetail = withEta(snapshot.runtime.status_detail, saving ? null : shownEta.job);
  $: if (snapshot.runtime.active_job_id && !etaTimer) {
    etaTimer = setInterval(() => (etaSecond += 1), 1000);
  } else if (!snapshot.runtime.active_job_id && etaTimer) {
    clearInterval(etaTimer);
    etaTimer = undefined;
  }
  // Saved results of the selected image and the pair the preview compares. The
  // host lists the history and checks every target before it decodes one; a
  // manual choice is pinned per image for this session (a restart resets it).
  let histories: Record<string, MediaResults> = {};
  let historyFailures: Record<string, string> = {};
  let pairChoices: Record<string, PairChoice> = {};
  let rememberedResults: Record<string, ResultSummary> = {};
  let historyKey = '';
  let historyRequest = 0;
  let pairKey = '';
  let pairError = '';
  $: selectedImage = selectedMedia?.kind === 'image' ? selectedMedia : undefined;
  // Any new, finished or cancelled job of this image can change its history.
  $: desiredHistoryKey = selectedImage
    ? `${selectedImage.id}|${snapshot.jobs
        .filter((job) => job.media_id === selectedImage?.id)
        .map((job) => `${job.id}:${job.status}`)
        .join(',')}`
    : '';
  $: if (desiredHistoryKey !== historyKey) void loadResultHistory(desiredHistoryKey);
  // A host with pair slots compares by job id once the history is known. If the
  // history cannot be read, the newest result is compared alone, as before.
  $: pairMode = Boolean(
    selectedImage && snapshot.runtime.comparison_pair && !historyFailures[selectedImage.id],
  );
  $: history = selectedImage ? histories[selectedImage.id] : undefined;
  $: choice = effectiveChoice(history, selectedImage ? pairChoices[selectedImage.id] : undefined);
  // While this image runs and nothing was chosen, its live tiles stay in view.
  $: liveRun = Boolean(selectedImage && activeJobMediaId === selectedImage.id && !choice.pinned);
  $: pairShown = Boolean(pairMode && history && !liveRun);
  $: resultRows = history
    ? [
        ...history.results,
        ...[choice.showing, choice.against]
          .filter((id) => id !== ORIGINAL && !history?.results.some((row) => row.job_id === id))
          .map((id) => rememberedResults[id])
          .filter((row): row is ResultSummary => Boolean(row)),
      ]
    : [];
  $: showingSide = selectedImage
    ? describeSide(
        choice.showing,
        resultRows.find((row) => row.job_id === choice.showing),
        slotFor(snapshot.runtime.comparison_pair, selectedImage.id, 'showing', choice.showing),
        selectedImage,
        // The completion preview of the job that just finished bridges the
        // decode; while a job runs that field holds live frames, never a result.
        !snapshot.runtime.active_job_id &&
          resultRows.find((row) => row.job_id === choice.showing)?.output_path ===
            snapshot.runtime.last_output_path
          ? snapshot.runtime.result_preview_data_url
          : '',
      )
    : undefined;
  $: againstSide =
    selectedImage && choice.against !== ORIGINAL
      ? describeSide(
          choice.against,
          resultRows.find((row) => row.job_id === choice.against),
          slotFor(snapshot.runtime.comparison_pair, selectedImage.id, 'against', choice.against),
          selectedImage,
        )
      : undefined;
  // A result whose framing differs from the source (a crop, another shape) is
  // shown on its own; stretching it over the source would fake an alignment.
  // An Edit is sized in steps of 32, so it may differ by that rounding alone.
  $: showingFramed = Boolean(
    pairShown &&
    showingSide &&
    selectedImage &&
    !alignedFraming(
      showingSide,
      selectedImage,
      framingRounding(resultRows.find((row) => row.job_id === choice.showing)),
    ),
  );
  $: againstFramed = Boolean(
    pairShown &&
    againstSide &&
    selectedImage &&
    !alignedFraming(
      againstSide,
      selectedImage,
      framingRounding(resultRows.find((row) => row.job_id === choice.against)),
    ),
  );
  $: singleFrame =
    pairShown && showingSide && (showingFramed || againstFramed) && showingSide.width
      ? { width: showingSide.width, height: showingSide.height }
      : null;
  $: comparisonNote = showingFramed
    ? `${showingSide?.label} has a different framing from the original, so it is shown on its own.`
    : againstFramed
      ? `${againstSide?.label} has a different framing, so it cannot share a split with ${showingSide?.label}.`
      : '';
  $: referenceIsResult = Boolean(pairShown && againstSide && !singleFrame);
  $: resultPreview =
    !pairMode || !history
      ? resultPreviewForSelectedMedia(snapshot)
      : liveRun || !showingSide || showingSide.state !== 'ready'
        ? ''
        : choice.showing === ORIGINAL && !referenceIsResult
          ? ''
          : showingSide.src;
  // A pair waits for both of its sides: until then the pane keeps the view it had (the held
  // one, when it belongs to this picture) and says what is loading, instead of a half-empty split.
  $: loadingTarget =
    pairShown && !singleFrame
      ? showingSide?.state === 'loading'
        ? choice.showing
        : referenceIsResult && againstSide?.state === 'loading'
          ? choice.against
          : ''
      : '';
  $: pairLoadingLabel = loadingTarget
    ? (resultRows.find((row) => row.job_id === loadingTarget)?.model_label ?? 'result')
    : '';
  $: paneView = {
    mediaId: selectedImage?.id ?? '',
    resultPreview,
    referenceIsResult,
    referencePreview: referenceIsResult && againstSide?.state === 'ready' ? againstSide.src : '',
    referenceLabel: referenceIsResult ? (againstSide?.label ?? 'Original') : 'Original',
    showingLabel: showingSide?.label ?? '',
    singleFrame,
  };
  let heldView = paneView;
  $: if (!loadingTarget) heldView = paneView;
  $: shownView =
    loadingTarget && heldView.mediaId !== paneView.mediaId
      ? {
          ...paneView,
          resultPreview: '',
          referenceIsResult: false,
          referencePreview: '',
          referenceLabel: 'Original',
          showingLabel: '',
          singleFrame: null,
        }
      : loadingTarget
        ? heldView
        : paneView;
  $: desiredPairKey =
    pairShown &&
    selectedImage &&
    snapshot.runtime.worker === 'ready' &&
    (choice.showing !== ORIGINAL || choice.against !== ORIGINAL)
      ? `${selectedImage.id}|${choice.showing}|${choice.against}`
      : '';
  $: if (desiredPairKey !== pairKey) void requestPair(desiredPairKey);
  // Open and Reveal follow Showing. Without a history they follow the selected
  // item's latest result (jobs arrive newest first), not whichever job finished last.
  $: selectedOutputPath = pairShown
    ? (resultRows.find((row) => row.job_id === choice.showing && row.available)?.output_path ?? '')
    : selectedMedia && selectedMedia.id !== activeJobMediaId
      ? (snapshot.jobs.find(
          (job) =>
            job.media_id === selectedMedia?.id && job.status === 'completed' && job.output_path,
        )?.output_path ?? '')
      : '';
  $: selectedOutputName = selectedOutputPath.split(/[\\/]/).pop() ?? '';
  // Try others…: one image in Upscale or Restore with a catalog model; the
  // runs queue behind other work but not beside this image's own.
  $: canTryOthers = Boolean(
    selectedImage &&
    !batchLabel &&
    (settings.task === 'upscale' || settings.task === 'denoise') &&
    settings.selected_model_id !== '__custom__',
  );
  $: canOpenTrials = Boolean(
    canTryOthers &&
    selectedImage &&
    !startingJob &&
    !cancelling &&
    snapshot.runtime.worker === 'ready' &&
    selectedImage.probe_status === 'ready' &&
    !inflightMediaIds.has(selectedImage.id),
  );
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
    !pairMode && completedImageJob && snapshot.runtime.worker === 'ready'
      ? `${completedImageJob.media_id}:${completedImageJob.output_path}`
      : '';
  $: if (desiredImageComparisonKey !== imageComparisonKey)
    void loadImageComparison(desiredImageComparisonKey);
  $: selectedComparisonError =
    imageComparisonError ||
    (pairShown
      ? pairError || (!referenceIsResult && showingSide?.state === 'error' ? showingSide.error : '')
      : snapshot.runtime.comparison_media_id === selectedMedia?.id
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
    ? settings.task === 'edit'
      ? `${editDimensions(selectedMedia.width, selectedMedia.height, Math.min(settings.edit_max_dimension, editSizeAllowed)).join(' × ')} · edited`
      : settings.task === 'denoise'
        ? `${selectedMedia.width} × ${selectedMedia.height} · original size`
        : usingTemporalVideo &&
            settings.video_target_resolution &&
            selectedMedia.width > 0 &&
            selectedMedia.height > 0
          ? `${Math.floor((selectedMedia.width * settings.video_target_resolution) / Math.min(selectedMedia.width, selectedMedia.height) / 2) * 2} × ${Math.floor((selectedMedia.height * settings.video_target_resolution) / Math.min(selectedMedia.width, selectedMedia.height) / 2) * 2} · SDR`
          : `${selectedMedia.width * settings.output_scale} × ${selectedMedia.height * settings.output_scale} · ${settings.output_scale}×`
    : '—';
  $: currentDownloadTarget =
    settings.task === 'edit' ? editModel : usingTemporalVideo ? selectedVideoModel : selectedModel;
  $: quickModel = settings.task
    ? choosePresetModel(snapshot, settings.task, 'quick', settings.content, settings.preset_pins)
    : undefined;
  $: bestModel = settings.task
    ? choosePresetModel(snapshot, settings.task, 'best', settings.content, settings.preset_pins)
    : undefined;
  $: quickDownload = presetDownload('quick', snapshot, settings);
  $: bestDownload = presetDownload('best', snapshot, settings);
  // Settings saved before the model library exist without a quality; infer it
  // from the selection so Quick/Best light up correctly after an update.
  $: activeQuality = ((): Quality => {
    if (settings.quality) return settings.quality;
    if (usingTemporalVideo || settings.selected_model_id === '__custom__') return 'custom';
    if (settings.fix_stages.length || settings.enable_face_model) return 'custom';
    if (bestModel && settings.selected_model_id === bestModel.model_id) return 'best';
    if (quickModel && settings.selected_model_id === quickModel.model_id) return 'quick';
    return 'custom';
  })();
  $: plan = resolvePlan(snapshot, settings, {
    faceModel,
    faceEnabled,
    usingTemporalVideo,
    selectedVideoModel,
    editModel,
  });
  $: downloads = pendingDownloads(plan);
  $: planNeedsFile = plan.some((stage) => stage.needsFile);
  $: planMissing = plan.some((stage) => stage.missing);
  // Video runs no fixes yet; its frames can still get Faces.
  $: fixChips = (
    settings.task === 'denoise'
      ? ['noise', 'blur', 'jpeg']
      : settings.task === 'video'
        ? ['faces']
        : ['noise', 'jpeg', 'blur', 'faces']
  ) as FixKind[];
  $: prepareReady =
    !snapshot.runtime.active_job_id &&
    !activeDownload &&
    !preparing &&
    !startingJob &&
    !cancelling &&
    downloads.stages.length > 0 &&
    !planNeedsFile &&
    !planMissing &&
    fixPlanSupported &&
    downloads.stages.every(
      (stage) => !stage.model?.terms_acceptance_required || termsAccepted[stage.model.model_id],
    ) &&
    snapshot.runtime.worker === 'ready' &&
    Boolean(settings.task) &&
    hdrModelCompatible &&
    (!usingTemporalVideo || !temporalUnavailableReason) &&
    (settings.task !== 'edit' || !editBlockReason);
  $: canPrepare = prepareReady && allReady(queueSelection);
  $: canPrepareOne = prepareReady && allReady(selectedOnly);
  $: canPrepareAll = prepareReady && allReady(queueableMedia);
  $: startVerb =
    settings.task === 'video'
      ? 'start video'
      : settings.task === 'denoise'
        ? 'restore'
        : settings.task === 'edit'
          ? 'edit'
          : 'upscale';
  $: activeDevice = snapshot.capabilities.devices.find(
    (device) => device.id === settings.device_id,
  );
  // A finished job keeps its own message while its result is selected.
  $: idleSummary =
    selectedMedia &&
    (['Ready', 'Model ready', 'Download paused', 'Cancelled'].includes(
      snapshot.runtime.status_title,
    ) ||
      (snapshot.runtime.status_title === 'Complete' && !selectedOutputPath)) &&
    !activeDownload &&
    !preparing &&
    !booting &&
    (canStart || canPrepare)
      ? `${selectedMedia.width} × ${selectedMedia.height} → ${outputDimensions} · ${
          settings.task === 'edit'
            ? editingDevice
              ? deviceLabel(editingDevice)
              : 'no supported GPU'
            : usingTemporalVideo
              ? activeDevice
                ? deviceLabel(activeDevice)
                : 'detecting hardware'
              : engineLabel(
                  settings,
                  snapshot.capabilities,
                  selectedModel?.architecture,
                  activeDevice ? deviceLabel(activeDevice) : 'detecting hardware',
                )
        }`
      : '';
  // The last failure also stands above Advanced, next to the setting that can fix it. It stays
  // until the next job starts, also while the engine restarts after it (a driver reset) and
  // the status line moves on to "Starting engine" and "Ready".
  let lastFailure = '';
  $: if (snapshot.runtime.active_job_id) lastFailure = '';
  else if (snapshot.runtime.status_title === 'Could not finish')
    lastFailure = snapshot.runtime.status_detail;
  let failureNote: HTMLElement | undefined;
  // A GPU or NPU that failed (a rejected check, a driver reset) is fixed by the CPU; editing
  // needs a GPU, so it gets no such button.
  $: acceleratorFailure =
    settings.task !== 'edit' &&
    settings.device_id !== 'cpu' &&
    /\b[GN]PU\b|graphics driver/.test(lastFailure);
  // What the engine said about the route (a check, a slow GPU, a partial CPU fallback): progress
  // ticks replace the detail line, so the note keeps its own line until the job ends.
  $: hardwareNote =
    snapshot.runtime.active_job_id &&
    snapshot.runtime.hardware_note &&
    snapshot.runtime.hardware_note !== snapshot.runtime.status_detail
      ? snapshot.runtime.hardware_note
      : '';
  // A CPU-only run on a computer with a GPU or NPU it could use says so in the idle status
  // line, where people look, instead of under Advanced.
  // Only for a task the CPU runs (editing needs a GPU) and with an engine that is ready.
  $: hardwareHint =
    settings.device_id === 'cpu' &&
    Boolean(settings.task) &&
    settings.task !== 'edit' &&
    snapshot.runtime.worker === 'ready' &&
    !snapshot.runtime.active_job_id &&
    !activeDownload &&
    !preparing &&
    !booting &&
    !lastFailure &&
    snapshot.runtime.status_title !== 'Warning'
      ? cpuHint(snapshot.capabilities, detectPlatform())
      : null;
  $: taskCaption = !settings.task
    ? ''
    : settings.task === 'denoise'
      ? 'Removes noise, blur and JPEG artifacts at the same size.'
      : settings.task === 'edit'
        ? 'Describe a change in words.'
        : selectedMedia?.kind === 'video' || settings.task === 'video'
          ? 'Enlarges video, SDR or HDR.'
          : 'Enlarges photos and artwork with new detail.';
  $: benchmarkHardware = snapshot.capabilities.devices.find(
    (device) => device.id === benchmarkDevice,
  );
  $: unreadyMedia = queueSelection.find((media) => media.probe_status !== 'ready');
  const preparingNote = (media: MediaItem): string =>
    media.probe_status === 'failed'
      ? `“${media.name}” cannot be processed: ${media.error}`
      : `Preparing preview… Wait for “${media.name}” before starting.`;
  $: incompatibleNote =
    settings.task && runnableMedia.length < snapshot.media.length
      ? `${queueableMedia.length} ${settings.task === 'video' ? 'video' : 'image'} item${queueableMedia.length === 1 ? '' : 's'} can be queued with this setup; select the other media type to configure it separately.`
      : '';
  // What the choice alone does not say; empty when there is nothing to explain.
  $: scopeHint = unreadyMedia
    ? preparingNote(unreadyMedia)
    : settings.batch_mode && settings.task === 'edit'
      ? selectedMedia
        ? `Edit processes only “${selectedMedia.name}”; batches are not supported yet.`
        : 'Edit processes only the selected image.'
      : settings.batch_mode
        ? queueableMedia.length === 0 && runnableMedia.length > 0
          ? `All ${runnableMedia.length} compatible item${runnableMedia.length === 1 ? ' is' : 's are'} already running or queued.`
          : incompatibleNote
        : selectedMedia && !mediaMatchesTask
          ? `“${selectedMedia.name}” does not match ${settings.task || 'the chosen task'}; select a compatible item.`
          : '';
  $: mismatchNote =
    selectedMedia && !mediaMatchesTask
      ? `“${selectedMedia.name}” does not match ${settings.task || 'the chosen task'}; select a compatible item.`
      : '';
  // The lone start button of several inputs says what limits it; with a choice of two
  // buttons each carries its own note.
  $: singleHint =
    snapshot.media.length < 2
      ? ''
      : unreadyMedia
        ? preparingNote(unreadyMedia)
        : settings.task === 'edit'
          ? selectedMedia
            ? `Edit processes only “${selectedMedia.name}”; batches are not supported yet.`
            : 'Edit processes only the selected image.'
          : mismatchNote ||
            (queueableMedia.length === 0 && runnableMedia.length > 0
              ? `All ${runnableMedia.length} compatible item${runnableMedia.length === 1 ? ' is' : 's are'} already running or queued.`
              : incompatibleNote);
  $: oneUnready = selectedOnly.find((media) => media.probe_status !== 'ready');
  $: allUnready = queueableMedia.find((media) => media.probe_status !== 'ready');
  $: oneTitle = oneUnready
    ? preparingNote(oneUnready)
    : mismatchNote || (canPrepareOne ? `Downloads ${formatBytes(downloads.bytes)} first.` : '');
  $: allTitle = allUnready ? preparingNote(allUnready) : incompatibleNote;
  // What the primary button says it does to all: "Upscale all 52", "Start all 5 videos".
  $: allPhrase = `${startVerb === 'start video' ? 'start' : startVerb} all ${queueableMedia.length}${
    settings.task === 'video' ? ' videos' : ''
  }`;
  const lockedTitle = 'Finish or cancel the run to change settings.';
  $: lockReason = cancelling
    ? 'Stopping your queue… Controls unlock when processing has stopped and temporary output is removed.'
    : 'Finish or cancel the run to change settings.';
  $: startBlockReason = snapshot.runtime.active_job_id
    ? 'Wait for the current job to finish.'
    : snapshot.runtime.worker !== 'ready'
      ? 'The processing engine is not ready yet.'
      : !settings.task
        ? 'Choose a task before starting.'
        : !queueSelection.length
          ? 'Select a compatible item before starting.'
          : unreadyMedia
            ? scopeHint
            : editBlockReason ||
              (!modelReady || !faceReady || !fixesReady || !interpolationReady
                ? 'Choose or download the required models before starting.'
                : 'The current settings are not ready to start.');

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
        await subscribe(() =>
          startUpdateWatcher(snapshot.settings.check_updates_at_launch ?? true),
        );
        await subscribe(() =>
          api.listenForFileDrops({
            hover: (active) => (filesDragging = active),
            drop: (paths) => void addDroppedMedia(paths),
          }),
        );
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
      if (etaTimer) clearInterval(etaTimer);
      for (const unlisten of unlisteners) unlisten();
    };
  });

  // Changes within 80 ms share one refresh. The timer is not moved by later changes: a fast
  // batch reports one every few milliseconds, and a refresh that waited for quiet would not
  // show a finished photo until the whole batch ended.
  function scheduleRefresh(): void {
    if (stateRefresh) return;
    stateRefresh = setTimeout(() => {
      stateRefresh = undefined;
      void refresh();
    }, 80);
  }

  // Refreshes can overlap; an answer to an older request than the one already shown is
  // dropped, so it cannot undo a change (an update switch, for one) that a newer answer
  // showed. Refreshes are not queued behind each other: an auto-start inside a refresh
  // waits for a refresh of its own.
  let refreshesStarted = 0;
  let refreshShown = 0;

  async function refresh(): Promise<void> {
    if (!api.isTauri()) return;
    const order = ++refreshesStarted;
    try {
      const latest = await api.refreshSnapshot();
      if (order < refreshShown) return;
      refreshShown = order;
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
      // Only the newest pulse is kept: one from a batch photo saving off screen
      // must not push out the shown photo's.
      const jobId = String(message.data.job_id ?? '');
      if (
        jobId !== snapshot.runtime.active_job_id &&
        (snapshot.runtime.active_batch_job_ids ?? []).includes(jobId)
      )
        return;
      queueRuntimePulse(message);
      return;
    }

    discardRuntimePulse();

    // A batch photo that ends off screen (it was saving) must not reset the one shown.
    const messageJob = String(message.data.job_id ?? '');
    const offScreen =
      messageJob !== snapshot.runtime.active_job_id &&
      (snapshot.runtime.active_batch_job_ids ?? []).includes(messageJob);
    snapshot = applyWorkerEnvelope(snapshot, message);
    // A pinned pair keeps its zoom, pan and divider while other runs start and finish.
    const pinned = Boolean(selectedMedia && pairChoices[selectedMedia.id]?.pinned);
    if (message.type === 'job_started') {
      livePreviewWarning = '';
      if (!pinned) resetView();
      resetProgressivePreview(messageJob);
    } else if (
      !offScreen &&
      (message.type === 'job_completed' || message.type === 'video_job_completed')
    ) {
      livePreviewWarning = '';
      resetProgressivePreview();
      if (!pinned) resetView();
    } else if (!offScreen && (message.type === 'job_cancelled' || message.type === 'job_failed')) {
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
      noteFfmpegProbeFailure(message.data);
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

  /** Keep a valid bundle chosen: the saved one, or the largest that fits this computer. */
  function ensureEditModel(): void {
    const device = editDevice(snapshot.capabilities, snapshot.settings);
    const current = snapshot.edit.models.find(
      (model) => model.model_id === snapshot.settings.edit_model_id,
    );
    const anyFits = snapshot.edit.models.some((model) =>
      editModelFits(model, snapshot.capabilities, device),
    );
    // Keep the saved choice unless it cannot start here while another bundle can.
    if (current && (editModelFits(current, snapshot.capabilities, device) || !anyFits)) return;
    const model = recommendAnyEditModel(snapshot.edit.models, snapshot.capabilities, device);
    if (model && model.model_id !== current?.model_id)
      updateSettings({ edit_model_id: model.model_id, edit_steps: 0 });
  }

  function chooseInitialModel(): void {
    const task = snapshot.settings.task;
    if (task === 'edit') {
      ensureEditModel();
      return;
    }
    if (!task || snapshot.settings.selected_model_id) return;
    resolveRecipe({ quality: snapshot.settings.quality || 'quick' }, { keepVideoEngine: true });
  }

  // Quick and Best describe the result; the model behind them is named once, under Model.
  function qualityBlurb(quality: 'quick' | 'best'): string {
    if (settings.task === 'denoise')
      return quality === 'quick' ? 'Fast clean-up' : 'Most careful, slower';
    if (settings.task === 'video')
      return quality === 'quick' ? 'Fast, frame by frame' : 'More detail, slower';
    return quality === 'quick' ? 'Fast, clean result' : 'Finest detail, slower';
  }

  /**
   * Resolve the plan for the active recipe from the catalog. Reads
   * `snapshot.settings` (updated synchronously by `updateSettings`) so callers
   * can chain it after a task or content change within the same tick.
   */
  function resolveRecipe(
    patch: Partial<Pick<UiSettings, 'quality' | 'content' | 'fixes' | 'fix_stages'>>,
    options: { keepVideoEngine?: boolean } = {},
  ): void {
    const current = { ...snapshot.settings, ...patch };
    const task = current.task;
    if (!task || task === 'edit') return;
    const quality: 'quick' | 'best' =
      current.quality === 'quick' || current.quality === 'best' ? current.quality : 'best';
    const byId = (id: string): CatalogModel | undefined =>
      snapshot.catalog.models.find((model) => model.model_id === id);
    const keepPrimary = current.quality === 'custom';
    const restoring = task === 'denoise';
    // Quick and Best choose every fix model again; a job set up by hand keeps each one
    // that still fixes its problem.
    const chosen = new Set<Exclude<FixKind, 'faces'>>();
    const fix_stages = (task === 'video' ? [] : current.fix_stages).map((selection, index, all) => {
      const own = byId(selection.model_id);
      if (keepPrimary && own && fitsFix(own, selection.fix)) return selection;
      // A Restore model picked by hand becomes the new last fix when it can do that job.
      const picked = byId(current.selected_model_id);
      if (
        restoring &&
        keepPrimary &&
        index === all.length - 1 &&
        !selection.model_id &&
        picked &&
        fitsFix(picked, selection.fix)
      )
        return { ...selection, model_id: picked.model_id };
      chosen.add(selection.fix);
      return {
        ...selection,
        model_id:
          chooseFixModel(snapshot, selection.fix, quality, current.preset_pins)?.model_id ?? '',
      };
    });
    const wantsFaces = current.fixes.includes('faces');
    const patchSettings: Partial<UiSettings> = {
      quality: current.quality || quality,
      content: current.content,
      fix_stages,
      // The chips show the fixes that run; Faces waits in Restore for the way back.
      fixes: [
        ...fix_stages.map((selection) => selection.fix),
        ...(wantsFaces ? (['faces'] as FixKind[]) : []),
      ],
    };
    if (restoring) {
      // Restore's model is its last fix; without a fix it is the preset's or your own.
      const final = fix_stages.at(-1);
      const kept = keepPrimary && (!final || final.model_id === current.selected_model_id);
      if (!kept) {
        const model = final ? byId(final.model_id) : choosePresetModel(snapshot, task, quality);
        const id = final ? final.model_id : model?.model_id;
        if (id !== undefined) patchSettings.selected_model_id = id;
        if (model) patchSettings.halo = model.recommended_halo;
        // A pick by hand that cannot fix the problem gives way to the preset's model.
        if (keepPrimary && final && chosen.has(final.fix)) patchSettings.quality = quality;
      }
      patchSettings.output_scale = 1;
      patchSettings.enable_face_model = false;
    } else {
      const primary = keepPrimary
        ? undefined
        : choosePresetModel(snapshot, task, quality, current.content, current.preset_pins);
      if (primary) {
        patchSettings.selected_model_id = primary.model_id;
        // Quick and Best are frame-by-frame recipes; a saved SeedVR2 choice
        // survives only the initial resolution and task changes.
        if (!options.keepVideoEngine) patchSettings.selected_video_model_id = 'frame_by_frame';
        patchSettings.output_scale = primary.native_scale;
        patchSettings.halo = primary.recommended_halo;
        patchSettings.safe_memory = quality === 'best' ? current.safe_memory : false;
      }
      const primaryId = patchSettings.selected_model_id ?? current.selected_model_id;
      const primaryModel = byId(primaryId);
      const companion = snapshot.catalog.models.find(
        (model) => model.purposes.includes('face') && primaryModel?.pair_with === model.model_id,
      );
      patchSettings.enable_face_model = wantsFaces && Boolean(companion) && faceEngineAvailable;
    }
    updateSettings(patchSettings);
    termsAccepted = {};
    faceTermsAccepted = false;
  }

  function setContent(content: UiSettings['content']): void {
    if (settings.content === content) return;
    resolveRecipe({ content, quality: activeQuality });
  }

  function toggleFix(fix: FixKind): void {
    if (fix === 'faces') {
      const current = settings.fixes;
      if (!faceModel && !current.includes('faces')) return;
      const fixes: FixKind[] = current.includes('faces')
        ? current.filter((item) => item !== 'faces')
        : [...current, 'faces'];
      resolveRecipe({ fixes, quality: activeQuality });
      return;
    }
    // Noise, JPEG and blur each run as their own fix before the main model.
    resolveRecipe({ fix_stages: toggleFixStage(settings.fix_stages, fix), quality: activeQuality });
  }

  async function moveFix(fix: Exclude<FixKind, 'faces'>, offset: -1 | 1): Promise<void> {
    const fix_stages = moveFixStage(settings.fix_stages, fix, offset);
    if (fix_stages === settings.fix_stages) return;
    // Moving changes the order and nothing else: every model, the scale and the overlap
    // stay. In Restore the last fix is the model, so a new last fix brings its model
    // and the overlap that model needs.
    const final = fix_stages[fix_stages.length - 1];
    const model = snapshot.catalog.models.find((entry) => entry.model_id === final.model_id);
    updateSettings({
      fix_stages,
      fixes: [
        ...fix_stages.map((selection) => selection.fix),
        ...(settings.fixes.includes('faces') ? (['faces'] as FixKind[]) : []),
      ],
      ...(settings.task === 'denoise' && final.fix !== settings.fix_stages.at(-1)?.fix
        ? { selected_model_id: final.model_id, ...(model ? { halo: model.recommended_halo } : {}) }
        : {}),
    });
    // Keep the keyboard on the fix that moved: the same way if it can go on, else back.
    await tick();
    const ways = offset < 0 ? ['earlier', 'later'] : ['later', 'earlier'];
    for (const way of ways) {
      const button = document.querySelector<HTMLButtonElement>(
        `button[aria-label="Move the ${FIX_NAMES[fix]} fix ${way}"]`,
      );
      if (button) {
        button.focus();
        return;
      }
    }
  }

  function useModelForJob(model: CatalogModel | undefined): void {
    if (!model) return;
    selectPrimaryModel(model.model_id);
    libraryOpen = false;
  }

  function pinPreset(model: CatalogModel, quality: 'quick' | 'best'): void {
    if (!settings.task) return;
    const preset_pins = {
      ...settings.preset_pins,
      [presetSlot(settings.task, settings.content, quality)]: model.model_id,
    };
    updateSettings({ preset_pins });
    resolveRecipe({ quality });
    libraryOpen = false;
  }

  function openLibrary(
    slot: 'primary' | 'fix' | 'face' | 'browse' | 'edit' | 'try',
    fix = settings.fix_stages.at(-1)?.fix,
  ): void {
    // Restore's model is the fix itself, so with a chip on it is chosen like one.
    libraryContext =
      slot === 'fix' ||
      ((slot === 'primary' || slot === 'try') && settings.task === 'denoise' && fix)
        ? { slot, fix }
        : { slot };
    // Try others… keeps the settings the plan card shows now, whatever the
    // library changes before the runs are queued.
    trialBase =
      slot === 'try' &&
      selectedImage &&
      (settings.task === 'upscale' || settings.task === 'denoise')
        ? makeStartInput([selectedImage.id], settings.task, false)
        : undefined;
    trialMedia =
      trialBase && selectedImage ? { id: selectedImage.id, name: selectedImage.name } : undefined;
    libraryOpen = true;
  }

  let trialBase: StartBatchInput | undefined;
  let trialMedia: { id: string; name: string } | undefined;

  function closeLibrary(): void {
    const returnToTry = libraryContext.slot === 'try';
    libraryOpen = false;
    // Focus goes back to where the sheet was opened, as for Change….
    if (returnToTry)
      void tick().then(() =>
        document.querySelector<HTMLButtonElement>('.try-others button')?.focus(),
      );
  }

  /** One ordinary job per model for the image, admitted together by the host. */
  async function runTrials(modelIds: string[]): Promise<void> {
    if (!trialBase || !trialMedia) return;
    const media = trialMedia;
    startingJob = true;
    try {
      await api.startModelTrials(media.id, modelIds, trialBase);
      unpinResults([media.id]);
      closeLibrary();
      await refresh();
    } catch (error) {
      showModal('Could not run these models', String(error));
    } finally {
      startingJob = false;
    }
  }

  /**
   * The user's own model for a problem, remembered for the quality whose fixes this job
   * uses, so a chip click, the content switch or Quick and back keeps it.
   */
  function rememberFixModel(model: CatalogModel, fix: Exclude<FixKind, 'faces'>): void {
    const preset_pins = {
      ...settings.preset_pins,
      [fixSlot(fix, fixQuality(activeQuality))]: model.model_id,
    };
    const fix_stages = settings.fix_stages.map((selection) =>
      selection.fix === fix ? { ...selection, model_id: model.model_id } : selection,
    );
    updateSettings({
      preset_pins,
      fix_stages,
      ...(settings.task === 'denoise' && fix_stages.at(-1)?.fix === fix
        ? {
            selected_model_id: model.model_id,
            output_scale: 1,
            halo: model.recommended_halo,
          }
        : {}),
    });
    libraryOpen = false;
  }

  function forgetFixModel(fix: Exclude<FixKind, 'faces'>): void {
    const slot = fixSlot(fix, fixQuality(activeQuality));
    updateSettings({
      fix_stages: settings.fix_stages.map((selection) =>
        selection.fix === fix ? { ...selection, model_id: '' } : selection,
      ),
      preset_pins: Object.fromEntries(
        Object.entries(settings.preset_pins).filter(([key]) => key !== slot),
      ),
    });
    // In Restore the model is the fix itself, so the default replaces a hand-picked one too.
    resolveRecipe({
      quality: settings.task === 'denoise' ? fixQuality(activeQuality) : activeQuality,
    });
    libraryOpen = false;
  }

  async function removeModel(model: CatalogModel | EditModel): Promise<void> {
    try {
      await api.removeModel(model.model_id);
      await refresh();
    } catch (error) {
      showModal('Could not remove the model', String(error));
    }
  }

  async function downloadModels(
    models: (CatalogModel | CatalogVideoModel | EditModel | undefined)[],
  ): Promise<boolean> {
    preparing = true;
    preparingCancelled = false;
    try {
      for (const model of models) {
        if (preparingCancelled) return false;
        if (!model || model.installed || !model.automated_download_allowed) continue;
        preparingModelId = model.model_id;
        await api.downloadModel(model.model_id, Boolean(termsAccepted[model.model_id]));
      }
      await refresh();
      return !preparingCancelled;
    } catch (error) {
      if (!preparingCancelled) showModal('Download failed', String(error));
      return false;
    } finally {
      preparing = false;
      preparingModelId = '';
    }
  }

  /** Stop the downloads Start began; nothing starts afterwards. */
  async function cancelPreparation(): Promise<void> {
    preparingCancelled = true;
    // The host registers a download a moment after Start asks for it.
    for (let attempt = 0; attempt < 30 && preparing; attempt += 1) {
      const modelId = activeDownload || preparingModelId;
      if (modelId) {
        try {
          await api.cancelDownload(modelId);
          return;
        } catch {
          // Not registered yet; try again shortly.
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }

  async function prepareAndStart(): Promise<void> {
    if (!canPrepare) return;
    const targets = downloads.stages.map(
      (stage) => stage.model ?? stage.videoModel ?? stage.editModel ?? stage.interpolationModel,
    );
    if (!(await downloadModels(targets))) return;
    await tick();
    if (canStart) await start();
  }

  /** The status bar's two start buttons. The scope used is kept in the settings, so File > Start
   *  and automatic starts repeat it. */
  async function startScope(batch: boolean): Promise<void> {
    if (settings.batch_mode !== batch) updateSettings({ batch_mode: batch });
    await tick();
    if (settings.batch_mode !== batch) return;
    if (canPrepare) await prepareAndStart();
    else await start();
  }

  /** The failure's shortcut. The button goes away with the GPU, so the keyboard moves to the
   *  Hardware menu that now says CPU, or to the error itself while Advanced is closed. */
  async function useTheCpu(): Promise<void> {
    updateSettings(hardwarePatch('cpu'));
    await tick();
    (document.getElementById('device') ?? failureNote)?.focus();
  }

  function supportsHdrPreservation(candidate: UiSettings): boolean {
    return catalogSupportsHdrPreservation(candidate, snapshot.catalog);
  }

  function updateSettings(patch: Partial<UiSettings>): void {
    if (settingsLocked) return;
    const next = { ...snapshot.settings, ...patch };
    // Apply the model/output contract for selectors, saved recipes and launch presets.
    if (next.task === 'video' && next.video_hdr_mode === 'preserve') {
      if (!supportsHdrPreservation(next)) next.video_hdr_mode = 'tone_map';
      else if (selectedMedia?.hdr_format) {
        next.precision = 'fp32';
        next.deflicker = false;
        next.video_interpolation = 'off';
      }
    }
    snapshot = { ...snapshot, settings: next };
    if (api.isTauri()) {
      void api
        .saveSettings(snapshot.settings)
        .catch((error) => showModal('Could not save settings', String(error)));
    }
  }

  /**
   * The update switches are saved by the host on its own and may change during a job.
   * The window shows the host's value afterwards, also when saving failed.
   */
  function setUpdateSwitch(
    name: 'check_updates_at_launch' | 'anonymous_update_count',
    value: boolean,
  ): void {
    if (!api.isTauri()) return;
    void api
      .setUpdateSwitch(name, value)
      .catch((error) => showModal('Could not save settings', String(error)))
      .finally(() => scheduleRefresh());
  }

  function setTask(task: TaskKind): void {
    if (selectedMedia && (task === 'video') !== (selectedMedia.kind === 'video')) return;
    if (task !== 'video') lastImageTask = task;
    if (task === 'edit') {
      // Editing leaves the upscale and restore setup untouched for when you switch back.
      updateSettings({ task, enable_face_model: false });
      termsAccepted = {};
      faceTermsAccepted = false;
      ensureEditModel();
      return;
    }
    const models = modelsForTask(snapshot, task);
    // Keep a manual choice that still fits the task; otherwise the active
    // recipe (Quick until one is chosen) resolves the plan for the new task.
    const kept =
      activeQuality === 'custom'
        ? models.find((model) => model.model_id === settings.selected_model_id)
        : undefined;
    updateSettings({
      task,
      selected_model_id: kept?.model_id ?? '',
      // Video runs no fixes; Upscale and Restore share theirs and their order.
      ...(task === 'video' ? { fix_stages: [], fixes: [] } : {}),
      output_scale:
        task === 'denoise'
          ? 1
          : Math.min(kept?.native_scale ?? 4, Math.max(2, settings.output_scale)),
      enable_face_model: false,
    });
    termsAccepted = {};
    faceTermsAccepted = false;
    if (!kept) {
      resolveRecipe(
        {
          quality: activeQuality === 'quick' || activeQuality === 'best' ? activeQuality : 'quick',
          fixes: task === 'video' ? [] : settings.fixes,
        },
        { keepVideoEngine: true },
      );
    }
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
    try {
      await api.selectMedia(id);
    } catch (error) {
      showModal('Could not select the item', String(error));
      return;
    }
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

  async function loadResultHistory(key: string): Promise<void> {
    historyKey = key;
    const request = ++historyRequest;
    const media = selectedImage;
    if (!key || !media || !api.isTauri()) return;
    try {
      const loaded = await api.listMediaResults(media.id);
      // A late answer for an earlier request, or for another image, is dropped.
      if (request !== historyRequest || loaded.media_id !== media.id) return;
      histories = { ...histories, [media.id]: loaded };
      if (historyFailures[media.id]) historyFailures = without(historyFailures, media.id);
    } catch (error) {
      if (request === historyRequest)
        historyFailures = { ...historyFailures, [media.id]: String(error) };
    }
  }

  async function requestPair(key: string): Promise<void> {
    pairKey = key;
    pairError = '';
    if (!key || !api.isTauri()) return;
    const [mediaId, showing, against] = key.split('|');
    try {
      await api.requestImageComparison(mediaId, { showing, against });
      if (key === pairKey) await refresh();
    } catch (error) {
      if (key === pairKey) pairError = String(error);
    }
  }

  // The Outputs row that matches what the centre pane shows.
  $: shownPick = ((): ComparePick | null => {
    if (mainTab !== 'preview' || !selectedImage || !pairShown) return null;
    return choice.showing === ORIGINAL
      ? { kind: 'original', media_id: selectedImage.id }
      : { kind: 'output', media_id: selectedImage.id, job_id: choice.showing };
  })();

  /** A click on an Outputs row: that input and that result in the centre pane's Preview. */
  async function showResult(pick: ComparePick): Promise<void> {
    const results = bulkHistories.find((entry) => entry.media_id === pick.media_id)?.results ?? [];
    const showing = pick.kind === 'original' ? ORIGINAL : pick.job_id;
    // A row shows its own picture against the Original (or the Original alone): the
    // result-against-result view is entered only from the pair menus.
    const against = ORIGINAL;
    const remembered = { ...rememberedResults };
    for (const row of results)
      if ([showing, against].includes(row.job_id)) remembered[row.job_id] = row;
    rememberedResults = remembered;
    pairChoices = { ...pairChoices, [pick.media_id]: { showing, against, pinned: true } };
    mainTab = 'preview';
    await selectQueueMedia(pick.media_id);
  }

  /** A manual choice pins the pair of this image until a new run starts. */
  function choosePair(side: 'showing' | 'against', target: string): void {
    if (!selectedImage) return;
    const remembered = { ...rememberedResults };
    for (const id of [target, choice.showing, choice.against]) {
      const row = resultRows.find((candidate) => candidate.job_id === id);
      if (row) remembered[id] = row;
    }
    rememberedResults = remembered;
    pairChoices = { ...pairChoices, [selectedImage.id]: chooseTarget(choice, side, target) };
  }

  /** Previous Result (older) and Next Result (newer) move Showing only. */
  function stepResult(direction: 'older' | 'newer'): void {
    if (!pairMode || !history) return;
    const next = stepShowing(resultRows, choice, direction);
    if (next) choosePair('showing', next);
  }

  /** A new run shows its own results again: the image's pair is no longer pinned. */
  function unpinResults(mediaIds: string[]): void {
    let next = pairChoices;
    for (const id of mediaIds) if (next[id]) next = without(next, id);
    pairChoices = next;
  }

  let filesDragging = false;

  async function addDroppedMedia(paths: string[]): Promise<void> {
    try {
      await api.addMedia(paths, false);
    } catch (error) {
      showModal('Could not add dropped files', String(error));
      return;
    }
    page = 'preview';
    await refresh();
    ensureTaskMatchesSelection();
  }

  async function addFiles(replace = false): Promise<void> {
    if (!api.isTauri()) {
      showModal('Desktop runtime required', 'Run “npm run tauri dev” to choose local media.');
      return;
    }
    try {
      const paths = await api.chooseMediaFiles();
      if (!paths.length) return;
      await api.addMedia(paths, replace);
    } catch (error) {
      showModal('Could not add files', String(error));
      return;
    }
    page = 'preview';
    await refresh();
    ensureTaskMatchesSelection();
  }

  async function addFolder(): Promise<void> {
    try {
      const folder = await api.chooseMediaFolder();
      if (!folder?.paths.length) return;
      await api.addMedia(folder.paths, false);
      await api.saveSettings({
        ...snapshot.settings,
        batch_mode: true,
        output_directory: folder.outputDirectory,
      });
    } catch (error) {
      showModal('Could not add the folder', String(error));
      return;
    }
    page = 'preview';
    await refresh();
    ensureTaskMatchesSelection();
  }

  async function chooseOutput(): Promise<void> {
    try {
      const path = await api.chooseOutputDirectory(settings.output_directory);
      if (path) updateSettings({ output_directory: path });
    } catch (error) {
      showModal('Could not choose the folder', String(error));
    }
  }

  async function chooseCustomModel(): Promise<void> {
    const path = await api.chooseCustomModel().catch((error) => {
      showModal('Could not choose the model', String(error));
      return null;
    });
    if (path) {
      updateSettings({
        selected_model_id: '__custom__',
        custom_model_path: path,
        allow_unsafe_pickle_model: false,
        enable_face_model: false,
        quality: 'custom',
        // In Restore your own checkpoint is the model, not one of the fixes.
        ...(settings.task === 'denoise'
          ? {
              fix_stages: [],
              fixes: settings.fixes.filter((fix) => fix === 'faces'),
            }
          : {}),
      });
      libraryOpen = false;
      termsAccepted = {};
      faceTermsAccepted = false;
    }
  }

  function selectVideoEngine(modelId: string): void {
    updateSettings({ selected_video_model_id: modelId, enable_face_model: false });
    termsAccepted = {};
    faceTermsAccepted = false;
  }

  function selectPrimaryModel(modelId: string): void {
    const model = snapshot.catalog.models.find((candidate) => candidate.model_id === modelId);
    updateSettings({
      selected_model_id: modelId,
      output_scale: model
        ? Math.min(model.native_scale, Math.max(2, settings.output_scale))
        : settings.output_scale,
      // As the presets do: some models need a wider overlap between tiles.
      halo: model ? model.recommended_halo : settings.halo,
      allow_unsafe_pickle_model: false,
      enable_face_model:
        settings.task !== 'denoise' &&
        settings.fixes.includes('faces') &&
        faceEngineAvailable &&
        snapshot.catalog.models.some(
          (entry) => entry.purposes.includes('face') && entry.model_id === model?.pair_with,
        ),
      quality: 'custom',
    });
    termsAccepted = {};
    faceTermsAccepted = false;
  }

  function applyPreset(kind: 'quick' | 'best'): void {
    if (!settings.task || settings.task === 'edit') {
      showModal('Choose a task', 'Select Upscale, Restore or Video before applying a preset.');
      return;
    }
    resolveRecipe({ quality: kind });
  }

  async function runDownload(
    target: CatalogModel | CatalogVideoModel | EditModel | undefined = currentDownloadTarget,
    accepted?: boolean,
  ): Promise<void> {
    if (!target) return;
    if (activeDownload === target.model_id) {
      await attempt('Could not cancel the download', () => api.cancelDownload(target.model_id));
      return;
    }
    accepted ??= Boolean(termsAccepted[target.model_id]);
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

  function makeStartInput(ids: string[], task: TaskKind, batchMode: boolean): StartBatchInput {
    return {
      media_ids: ids,
      // What the plan card showed, kept with the job for the result history.
      ui: {
        quality: settings.quality,
        content: settings.content,
        recipe_name: snapshot.recipes.find((recipe) => activeRecipeIds.has(recipe.id))?.name ?? '',
        fixes: [...settings.fixes],
      },
      batch_mode: batchMode,
      task,
      model_id: settings.selected_model_id,
      video_model_id: settings.selected_video_model_id,
      fix_stages: imageFixes,
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
      neural_engine: settings.neural_engine ?? true,
      deflicker: settings.deflicker,
      deflicker_window: settings.deflicker_window,
      video_container: settings.video_container,
      video_codec: settings.video_codec ?? 'auto',
      external_ffmpeg_path: settings.external_ffmpeg_path ?? '',
      video_hdr_mode: settings.video_hdr_mode ?? 'tone_map',
      video_target_resolution: settings.video_target_resolution ?? 0,
      video_low_memory: settings.video_low_memory ?? true,
      video_crf: settings.video_crf,
      enable_face_model: faceEnabled,
      face_fidelity: settings.face_fidelity,
      enable_live_preview: settings.enable_live_preview,
      allow_unsafe_pickle_model: settings.allow_unsafe_pickle_model,
      // Frame interpolation is a frame-by-frame video stage.
      video_interpolation:
        settings.task === 'video' && !usingTemporalVideo
          ? (settings.video_interpolation ?? 'off')
          : 'off',
      video_interpolation_order: settings.video_interpolation_order ?? 'after_upscale',
    };
  }

  async function start(mediaIds?: string[]): Promise<void> {
    if ((!mediaIds && !canQueue) || !selectedMedia || !settings.task) return;
    if (settings.task === 'edit') {
      await startEdit();
      return;
    }
    const ids = mediaIds ?? queueSelection.map((media) => media.id);
    if (!ids.length) return;
    if (exportNeedsExternalFFmpeg(settings, snapshot.capabilities)) {
      ffmpegNotice = { context: 'export', paths: [] };
      return;
    }
    const input = makeStartInput(
      ids,
      settings.task,
      mediaIds ? ids.length > 1 : settings.batch_mode,
    );
    startingJob = true;
    try {
      await api.startJobs(input);
      unpinResults(ids);
      await refresh();
    } catch (error) {
      showModal('Could not start', String(error));
    } finally {
      startingJob = false;
    }
  }

  /** The worker rechecks memory before loading the editing bundle. */
  async function startEdit(): Promise<void> {
    if (!selectedMedia || !editModel || !editingDevice) return;
    startingJob = true;
    try {
      await api.startEdit({
        media_id: selectedMedia.id,
        model_id: editModel.model_id,
        prompt: editPrompt.trim(),
        runtime_path: editRuntime,
        device: editingDevice.id,
        max_dimension: Math.min(settings.edit_max_dimension, editSizeAllowed),
        steps: settings.edit_steps || editModel.default_steps,
        seed: settings.edit_seed,
        accepted_terms: Boolean(termsAccepted[editModel.model_id]),
        output_directory: settings.output_directory,
      });
      unpinResults([selectedMedia.id]);
      page = 'preview';
      await refresh();
    } catch (error) {
      showModal('Could not start the edit', String(error));
    } finally {
      startingJob = false;
    }
  }

  async function chooseEditRuntime(): Promise<void> {
    const path = await api.chooseEditRuntime().catch((error) => {
      showModal('Could not choose the editing runtime', String(error));
      return null;
    });
    if (path) editRuntimeOverride = path;
  }

  function useEditModel(model: EditModel): void {
    // A new model brings its own step count; a chosen override is reset with it.
    updateSettings({ edit_model_id: model.model_id, edit_steps: 0 });
    termsAccepted = {};
    libraryOpen = false;
  }

  function isEditModelId(modelId: string): boolean {
    return snapshot.edit.models.some((model) => model.model_id === modelId);
  }

  function openAnyLicense(modelId: string): Promise<void> {
    return attempt('Could not open the link', () =>
      isEditModelId(modelId) ? api.openEditLink(modelId, 'license') : api.openModelLicense(modelId),
    );
  }

  function openAnySource(modelId: string): Promise<void> {
    return attempt('Could not open the link', () =>
      isEditModelId(modelId) ? api.openEditLink(modelId, 'source') : api.openModelSource(modelId),
    );
  }

  // Only when it differs from Cancel queue, or names what a trial is doing.
  $: selectedInflight = selectedImage
    ? snapshot.jobs.filter(
        (job) =>
          job.media_id === selectedImage?.id &&
          ['queued', 'starting', 'running', 'cancelling'].includes(job.status),
      ).length
    : 0;
  $: canCancelSelectedImage =
    !cancelling && selectedInflight > 0 && (selectedInflight > 1 || inflightMediaIds.size > 1);
  // One job is just cancelled; "queue" is for several running or waiting.
  $: inflightJobCount = Math.max(
    snapshot.jobs.filter((job) =>
      ['queued', 'starting', 'running', 'cancelling'].includes(job.status),
    ).length,
    snapshot.runtime.active_batch_job_ids?.length ?? 0,
  );
  let cancellingImage = false;
  let retryingEngine = false;

  async function cancelSelectedImage(): Promise<void> {
    if (!selectedImage) return;
    cancellingImage = true;
    try {
      await api.cancelJobsForMedia(selectedImage.id);
      await refresh();
    } catch (error) {
      showModal('Could not cancel', String(error));
    } finally {
      cancellingImage = false;
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
    // The fixes that run before the model; a fix without a model has nothing to keep.
    const recipeFixPrefix = (
      settings.task === 'denoise' ? imageFixes.slice(0, -1) : imageFixes
    ).filter((selection) => selection.model_id);
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
      video_codec: settings.video_codec ?? 'auto',
      video_hdr_mode: settings.video_hdr_mode ?? 'tone_map',
      video_target_resolution: settings.video_target_resolution ?? 0,
      video_low_memory: settings.video_low_memory ?? true,
      video_crf: settings.video_crf,
      enable_face_model: faceEnabled,
      face_fidelity: settings.face_fidelity,
      video_interpolation: settings.video_interpolation ?? 'off',
      video_interpolation_order: settings.video_interpolation_order ?? 'after_upscale',
      quality: activeQuality,
      content: settings.content,
      fixes: settings.fixes,
      stages: [
        ...recipeFixPrefix.map((selection) => ({
          kind: fixStageKind(
            snapshot.catalog.models.find((model) => model.model_id === selection.model_id),
            selection.fix,
          ),
          model_id: selection.model_id,
          fix: selection.fix,
        })),
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
          // Restore's model is its last fix.
          ...(settings.task === 'denoise' && imageFixes.length
            ? { fix: imageFixes[imageFixes.length - 1].fix }
            : {}),
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
    try {
      await api.saveRecipe(recipe);
    } catch (error) {
      showModal('Could not save the recipe', String(error));
      return;
    }
    recipeName = '';
    recipeEditorOpen = false;
    await refresh();
  }

  async function openRecipeEditor(): Promise<void> {
    recipeEditorOpen = true;
    await tick();
    inspectorScroll?.querySelector<HTMLInputElement>('.recipe-input input')?.focus();
  }

  $: activeRecipeIds = new Set(
    snapshot.recipes
      .filter((recipe) => recipeMatches(recipe, settings, snapshot.catalog))
      .map((recipe) => recipe.id),
  );

  function applyRecipe(recipe: Recipe): void {
    if (selectedMedia && (recipe.task === 'video') !== (selectedMedia.kind === 'video')) {
      showModal(
        'Recipe does not match this media',
        `Select a ${recipe.task === 'video' ? 'video' : 'photo'} before applying “${recipe.name}”.`,
      );
      return;
    }
    if (recipe.task !== 'video') lastImageTask = recipe.task;
    const fix_stages = recipeFixStages(recipe, snapshot.catalog);
    const recipeFace = recipe.stages?.find((stage) => stage.kind === 'face_restore');
    const recipeModel = snapshot.catalog.models.find((model) => model.model_id === recipe.model_id);
    updateSettings({
      task: recipe.task,
      selected_model_id: recipe.model_id,
      selected_video_model_id: recipe.video_model_id || 'frame_by_frame',
      fix_stages,
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
      video_codec: recipe.video_codec || settings.video_codec || 'auto',
      external_ffmpeg_path: settings.external_ffmpeg_path ?? '',
      video_hdr_mode: recipe.video_hdr_mode ?? 'tone_map',
      video_target_resolution: recipe.video_target_resolution ?? 0,
      video_low_memory: recipe.video_low_memory ?? true,
      video_crf: recipe.video_crf ?? settings.video_crf,
      // Recipes saved before 0.3.7 carry no interpolation, so they run without it.
      video_interpolation: recipe.video_interpolation || 'off',
      video_interpolation_order: recipe.video_interpolation_order || 'after_upscale',
      enable_face_model: recipeFace ? true : (recipe.enable_face_model ?? false),
      face_fidelity:
        recipeFace?.fidelity === undefined
          ? (recipe.face_fidelity ?? 70)
          : Math.round(recipeFace.fidelity * 100),
      allow_unsafe_pickle_model: false,
      quality: recipe.quality || 'custom',
      content: recipe.content || settings.content,
      fixes: [
        ...fix_stages.map((selection) => selection.fix),
        ...(recipeFace || recipe.fixes?.includes('faces') ? (['faces'] as FixKind[]) : []),
      ],
    });
    termsAccepted = {};
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
    const requested = intent.files.length
      ? snapshot.media.filter((media) => intent.files.includes(media.path))
      : added;
    if (requested.length) {
      await api.selectMedia(requested[0].id);
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
    const candidates = intent.files.length
      ? requested
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
      pendingAutoStartIds = [...new Set([...pendingAutoStartIds, ...stagedAutoStartIds])];
      stagedAutoStartIds = [];
      void maybeAutoStart();
    }, 300);
  }

  async function maybeAutoStart(): Promise<void> {
    await tick();
    if (!pendingAutoStartIds.length) return;
    if (
      snapshot.capabilities.system_ram_total === 0 &&
      !snapshot.capabilities.devices.some((device) => device.id === snapshot.settings.device_id)
    )
      return;
    const media = snapshot.media.filter((item) => pendingAutoStartIds.includes(item.id));
    if (media.length !== pendingAutoStartIds.length) return;
    if (media.some((item) => item.probe_status === 'pending')) return;
    if (media.some((item) => item.probe_status === 'failed')) {
      pendingAutoStartIds = [];
      if (!ffmpegNotice) {
        showModal(
          'Could not inspect requested media',
          'At least one file could not be read, so automatic processing was cancelled.',
        );
      }
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
      fixesReady &&
      interpolationReady;
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
    // A dialog takes the input until it is answered; only Cancel, which ends work, still acts.
    if (
      (modalTitle || libraryOpen || ffmpegNotice || $updateDialogOpen) &&
      action !== 'file.cancel'
    )
      return;
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
          askClearMedia();
          break;
        case 'file.start':
          if (canStart) await start();
          else if (canPrepare) await prepareAndStart();
          else showModal('Cannot start yet', startBlockReason);
          break;
        case 'file.cancel':
          if (preparing) await cancelPreparation();
          else await api.cancelJobs();
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
          previewPane?.fitView();
          break;
        case 'view.actual':
          previewPane?.actualSize();
          break;
        case 'view.compare-original':
          previewPane?.setComparisonView('original');
          break;
        case 'view.compare-split':
          previewPane?.setComparisonView('split');
          break;
        case 'view.compare-enhanced':
          previewPane?.setComparisonView('enhanced');
          break;
        case 'view.previous-result':
          stepResult('older');
          break;
        case 'view.next-result':
          stepResult('newer');
          break;
        case 'view.refresh-hardware':
          await api.refreshCapabilities();
          scheduleRefresh();
          break;
        case 'view.performance':
          showPerformance();
          break;
        case 'view.models':
          openLibrary('browse');
          break;
        case 'app.updates':
          updateDialogOpen.set(true);
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

  function noteFfmpegProbeFailure(data: Record<string, unknown>): void {
    if (!probeNeedsExternalFFmpeg(data) || (settings.external_ffmpeg_path ?? '').trim()) return;
    const path = String(data.media_path ?? '');
    if (ffmpegNotice?.context === 'open') {
      if (path && !ffmpegNotice.paths.includes(path)) {
        ffmpegNotice = { ...ffmpegNotice, paths: [...ffmpegNotice.paths, path] };
      }
      return;
    }
    if (ffmpegNotice) return;
    ffmpegNotice = { context: 'open', paths: path ? [path] : [] };
  }

  /** Save the FFmpeg the user chose, then re-inspect the videos that failed without it. */
  async function useExternalFFmpeg(path: string): Promise<void> {
    const notice = ffmpegNotice;
    ffmpegNotice = null;
    if (settingsLocked) {
      showModal(
        'Settings are locked',
        'Wait for the running job to finish, then select FFmpeg under Advanced settings → Video.',
      );
      return;
    }
    snapshot = { ...snapshot, settings: { ...snapshot.settings, external_ffmpeg_path: path } };
    try {
      if (api.isTauri()) await api.saveSettings(snapshot.settings);
    } catch (error) {
      showModal('Could not save settings', String(error));
      return;
    }
    if (notice?.context !== 'open') return;
    const retry = snapshot.media.filter(
      (media) =>
        media.probe_status === 'failed' &&
        (notice.paths.includes(media.path) || media.kind === 'video'),
    );
    for (const media of retry) {
      try {
        await api.probePath(media.path);
      } catch (error) {
        console.error('Could not re-inspect the video', media.path, error);
      }
    }
    await refresh();
  }

  function mediaNamesFor(paths: string[]): string[] {
    return mediaNamesForPaths(paths, snapshot.media);
  }

  function showModal(
    title: string,
    message: string,
    kind: 'message' | 'performance' | 'integrations' = 'message',
  ): void {
    modalTitle = title;
    modalMessage = message;
    modalKind = kind;
    // A new dialog replaces a question that was open, answers included.
    modalConfirm = null;
    // The button that had the keyboard may go with the dialog it replaces.
    void tick().then(() => {
      if (modalElement && !modalElement.contains(document.activeElement))
        (modalElement.querySelector<HTMLElement>('[data-autofocus]') ?? modalElement).focus();
    });
  }

  /** Runs a host call the user asked for; a refusal opens the dialog the drop and menu paths use. */
  async function attempt(title: string, run: () => Promise<unknown>): Promise<void> {
    try {
      await run();
    } catch (error) {
      showModal(title, String(error));
    }
  }

  /** The result on screen becomes an input: it is added (or found) on the host and selected here. */
  async function useResultAsInput(): Promise<void> {
    let id: string;
    try {
      id = await api.useResultAsInput(selectedOutputPath);
    } catch (error) {
      showModal('Could not use the result as input', String(error));
      return;
    }
    await refresh();
    ensureTaskMatchesSelection();
    mainTab = 'preview';
    await mediaQueue?.showInput(id);
  }

  async function retryEngine(): Promise<void> {
    retryingEngine = true;
    try {
      await attempt('Could not restart the engine', () => api.retryEngine());
    } finally {
      retryingEngine = false;
    }
  }

  function closeModal(): void {
    modalTitle = '';
    modalMessage = '';
    modalConfirm = null;
    modalKind = 'message';
    diagnostics = '';
    integration = null;
  }

  /** Clear removes the list entries only, so it asks first and says what stays. */
  function askClearMedia(): void {
    const count = snapshot.media.length;
    if (count === 0 || snapshot.runtime.active_job_id) return;
    const titleFor = (n: number) => `Clear all ${n} ${n === 1 ? 'item' : 'items'} from the list?`;
    showModal(
      titleFor(count),
      'This removes them from the Inputs and Outputs lists and clears the current preview. The files on disk, including saved outputs, are not deleted.',
    );
    modalConfirm = {
      label: 'Clear',
      titleFor,
      run: () =>
        attempt('Could not clear the list', async () => {
          await api.clearMedia();
          await refresh();
        }),
    };
  }

  const outputTotal = () => compareGroups.reduce((n, group) => n + group.outputs.length, 0);

  /** Clear in Outputs empties the list of results only; the files stay where they are. */
  function askClearOutputs(): void {
    const count = outputTotal();
    if (count === 0 || snapshot.runtime.active_job_id) return;
    const titleFor = (n: number) =>
      `Clear all ${n} ${n === 1 ? 'output' : 'outputs'} from the list?`;
    showModal(titleFor(count), 'The files stay in their folders; only the list is emptied.');
    modalConfirm = {
      label: 'Clear',
      titleFor,
      countOf: outputTotal,
      run: () =>
        attempt('Could not clear the outputs', async () => {
          await api.clearResults();
          setComparePicks([]);
          unpinResults(snapshot.media.map((media) => media.id));
          await refresh();
          await loadBulkHistories(bulkKey);
        }),
    };
  }

  async function confirmModal(): Promise<void> {
    const confirm = modalConfirm;
    closeModal();
    await confirm?.run();
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
  class:mac={isMac}
  class:queue-timing-visible={Boolean(queueEta)}
  class:status-extra={Boolean(hardwareNote || hardwareHint)}
  style={`--ui-scale:${settings.interface_scale / 100};--notice-room:${updateNoticeHeight ? updateNoticeHeight + 20 : 0}px`}
>
  <!-- Only narrow windows need a bar: it switches between the panes that no longer fit side by side. -->
  <header class="toolbar" data-tauri-drag-region>
    <nav class="compact-nav view-switch pane-tabs" aria-label="Workspace">
      <button class:active={page === 'media'} on:click={() => (page = 'media')}
        >Media <span>{snapshot.media.length}</span></button
      >
      <button class:active={page === 'preview'} on:click={() => (page = 'preview')}>Viewer</button>
      <button class:active={page === 'enhance'} on:click={() => (page = 'enhance')}>Enhance</button>
    </nav>
  </header>

  <UpdatePanel
    processing={settingsLocked || inflightMediaIds.size > 0 || Boolean(activeDownload)}
    anonymousUpdateCount={settings.anonymous_update_count ?? true}
    checkAtLaunch={settings.check_updates_at_launch ?? true}
    on:anonymouscount={(event) => setUpdateSwitch('anonymous_update_count', event.detail)}
    on:checkatlaunch={(event) => setUpdateSwitch('check_updates_at_launch', event.detail)}
  />
  <WelcomeNotice
    anonymousUpdateCount={settings.anonymous_update_count ?? true}
    checkAtLaunch={settings.check_updates_at_launch ?? true}
  />

  <main class="workspace">
    <MediaQueue
      bind:this={mediaQueue}
      groups={compareGroups}
      picks={comparePicks}
      togglePick={(pick) => setComparePicks(toggleSlot(comparePicks, pick))}
      clearPicks={() => setComparePicks([])}
      clearOutputs={askClearOutputs}
      shown={shownPick}
      {showResult}
      openCompare={() => {
        if (comparePicks.length < 2) return;
        mainTab = 'compare';
        page = 'preview';
      }}
      compareActive={mainTab === 'compare'}
      historyError={bulkHistoryError}
      items={snapshot.media}
      jobs={snapshot.jobs}
      activeJobId={snapshot.runtime.active_job_id}
      engineJobIds={snapshot.runtime.active_batch_job_ids ?? []}
      compactHidden={page !== 'media'}
      scopeWarning={Boolean(selectedMedia) && !mediaMatchesTask}
      {scopeHint}
      {benchmarkRunning}
      {inflightMediaIds}
      {selectQueueMedia}
      {addFiles}
      {addFolder}
      locked={settingsLocked}
      removeMedia={(id) =>
        attempt('Could not remove the item', async () => {
          await api.removeMedia(id);
          await refresh();
        })}
      clearMedia={askClearMedia}
    />

    <div class="main-area" class:compact-hidden={page !== 'preview'}>
      {#snippet paneTabs()}
        <div
          bind:this={mainTabs}
          class="view-switch pane-tabs"
          role="tablist"
          tabindex="-1"
          aria-label="Main pane"
          on:keydown={mainTabKey}
        >
          <button
            role="tab"
            aria-selected={mainTab === 'preview'}
            tabindex={mainTab === 'preview' ? 0 : -1}
            class:active={mainTab === 'preview'}
            on:click={() => (mainTab = 'preview')}>Preview</button
          >
          <button
            role="tab"
            aria-selected={mainTab === 'compare'}
            tabindex={mainTab === 'compare' ? 0 : -1}
            class:active={mainTab === 'compare'}
            on:click={() => (mainTab = 'compare')}>Compare</button
          >
        </div>
      {/snippet}
      {#if mainTab === 'compare'}
        {#if compareRequestError}<div class="compare-request-error" role="alert">
            {compareRequestError}
          </div>{/if}
        <ComparePane
          items={compareItems}
          generation={snapshot.runtime.compare?.generation ?? 0}
          requestRegions={(generation, regions) =>
            api.isTauri() ? api.requestCompareRegions(generation, regions) : Promise.resolve()}
          clearRegions={() => (api.isTauri() ? api.clearCompareRegions() : Promise.resolve())}
          layout={compareLayout}
          setLayout={(layout) => (compareLayout = layout)}
          swapItems={(first, second) => setComparePicks(swapSlots(comparePicks, first, second))}
          removeItem={(key) =>
            setComparePicks(comparePicks.filter((pick) => pickKey(pick) !== key))}
          close={() => (mainTab = 'preview')}
          asTab
        >
          <svelte:fragment slot="tabs">{@render paneTabs()}</svelte:fragment>
        </ComparePane>
      {:else}
        <PreviewPane
          bind:this={previewPane}
          bind:liveFrame
          editing={settings.task === 'edit'}
          processing={Boolean(activeJobMediaId && selectedMedia?.id === activeJobMediaId)}
          activeTileSize={snapshot.runtime.active_tile_size}
          {selectedMedia}
          resultPreview={shownView.resultPreview}
          referenceIsResult={shownView.referenceIsResult}
          referencePreview={shownView.referencePreview}
          referenceLabel={shownView.referenceLabel}
          referenceState={againstSide?.state ?? 'ready'}
          referenceError={againstSide?.error ?? ''}
          showingLabel={shownView.showingLabel}
          showingState={showingSide?.state ?? 'ready'}
          showingError={showingSide?.error ?? ''}
          singleFrame={shownView.singleFrame}
          loadingLabel={pairLoadingLabel}
          {comparisonNote}
          showLiveTiles={!choice.pinned}
          comparisonError={selectedComparisonError}
          {completedVideoOutput}
          compactHidden={page !== 'preview'}
          addFiles={() => void addFiles()}
          dropping={filesDragging}
        />
      {/if}
    </div>

    <aside class="enhance-pane pane" class:compact-hidden={page !== 'enhance'}>
      <div class="pane-heading" data-tauri-drag-region>
        <h1 data-tauri-drag-region>Enhance</h1>
        <span data-tauri-drag-region title={settingsLocked ? lockedTitle : undefined}
          >{settingsLocked
            ? 'Locked while running'
            : settings.task === 'edit'
              ? editModel
                ? editFamilyLabel(editModel.family)
                : 'Choose a model'
              : settings.task
                ? `${activeQuality === 'custom' ? 'Custom' : activeQuality === 'best' ? 'Best' : 'Quick'}${settings.task === 'denoise' ? '' : settings.content === 'illustration' ? ' · Illustration' : ' · Photo'}`
                : 'Choose a task'}</span
        >
      </div>
      <!-- Locked: the header says so once, and the dimmed controls show the rest. Screen readers
           hear the explanation when the lock starts, and it describes the settings region. -->
      {#if settingsLocked}<span id="settings-lock-reason" class="visually-hidden" role="status"
          >{lockReason}</span
        >{/if}
      <div
        bind:this={inspectorScroll}
        class="inspector-scroll"
        role="region"
        aria-label="Enhancement settings"
        aria-describedby={settingsLocked ? 'settings-lock-reason' : undefined}
      >
        <fieldset
          class="processing-settings"
          disabled={settingsLocked}
          aria-label="Processing settings"
        >
          <section class="control-section">
            <span class="eyebrow">Task</span>
            <!-- A selected video is upscaled as video, so Upscale stays pressed for it. -->
            <div class="seg-inline task-switch" role="group" aria-label="Task">
              <button
                aria-pressed={settings.task === 'upscale' || settings.task === 'video'}
                class:active={settings.task === 'upscale' || settings.task === 'video'}
                on:click={() => setTask(selectedMedia?.kind === 'video' ? 'video' : 'upscale')}
                >Upscale</button
              >
              <button
                aria-pressed={settings.task === 'denoise'}
                disabled={selectedMedia?.kind === 'video'}
                class:active={settings.task === 'denoise'}
                on:click={() => setTask('denoise')}>Restore</button
              >
              <button
                aria-pressed={settings.task === 'edit'}
                disabled={selectedMedia?.kind === 'video'}
                class:active={settings.task === 'edit'}
                on:click={() => setTask('edit')}>Edit</button
              >
            </div>
            {#if taskCaption}<p class="task-caption">{taskCaption}</p>{/if}
          </section>

          {#if settings.task === 'video' && selectedMedia?.hdr_format}
            <section class="control-section" aria-label="HDR conversion">
              <span class="eyebrow"
                >{preservingHdr ? 'HDR input · HDR output · Labs' : 'HDR input · SDR output'}</span
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
              <span class="eyebrow">Audio compatibility</span>
              <p class="model-description">{selectedMedia.audio_warning}</p>
            </section>
          {/if}

          {#if settings.task}
            {#if settings.task === 'edit'}
              <section class="control-section" aria-label="Edit prompt">
                <span class="eyebrow">What to change</span>
                <textarea
                  class="edit-prompt"
                  aria-label="What to change"
                  rows="4"
                  maxlength="4000"
                  placeholder="Replace the background with a sunset beach. Keep the person and their clothing unchanged."
                  bind:value={editPrompt}></textarea>
              </section>
            {:else}
              <section class="control-section recipes">
                <div class="eyebrow eyebrow-row">
                  <span>Quality</span>
                  {#if !recipeEditorOpen}<span class="eyebrow-actions"
                      ><button class="link-button" type="button" on:click={openRecipeEditor}
                        >Save as recipe…</button
                      ></span
                    >{/if}
                </div>
                <div class="recipe-grid" role="group" aria-label="Quality">
                  <button
                    class="quick"
                    class:active={activeQuality === 'quick'}
                    aria-pressed={activeQuality === 'quick'}
                    on:click={() => applyPreset('quick')}
                    ><b>Quick</b><span>{qualityBlurb('quick')}</span>{#if quickDownload}<small
                        >{quickDownload}</small
                      >{/if}</button
                  >
                  <button
                    class="best"
                    class:active={activeQuality === 'best'}
                    aria-pressed={activeQuality === 'best'}
                    on:click={() => applyPreset('best')}
                    ><b>Best</b><span>{qualityBlurb('best')}</span>{#if bestDownload}<small
                        >{bestDownload}</small
                      >{/if}</button
                  >
                </div>
                <div class="recipe-tools">
                  {#each snapshot.recipes as recipe (recipe.id)}
                    <div class="saved-recipe">
                      <button
                        class:active={activeRecipeIds.has(recipe.id)}
                        aria-pressed={activeRecipeIds.has(recipe.id)}
                        disabled={Boolean(selectedMedia) &&
                          (recipe.task === 'video') !== (selectedMedia.kind === 'video')}
                        on:click={() => applyRecipe(recipe)}
                        ><b>{recipe.name}</b><span
                          >{recipe.task} · {recipe.output_scale}× · {recipe.precision.toUpperCase()}</span
                        ></button
                      ><button
                        aria-label={`Delete ${recipe.name}`}
                        on:click={() =>
                          attempt('Could not delete the recipe', async () => {
                            await api.deleteRecipe(recipe.id);
                            await refresh();
                          })}
                        ><svg
                          xmlns="http://www.w3.org/2000/svg"
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          stroke-width="2.5"
                          stroke-linecap="round"
                          stroke-linejoin="round"
                          ><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg
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
                  {/if}
                </div>
              </section>
            {/if}

            {#if !usingTemporalVideo && settings.task !== 'edit'}
              <section
                class="control-section"
                aria-label={settings.task === 'denoise' ? 'What to fix' : 'Your image'}
              >
                <span class="eyebrow"
                  >{settings.task === 'denoise' ? 'What to fix' : 'Your image'}</span
                >
                {#if settings.task !== 'denoise'}
                  <div class="field-heading">
                    <span class="field-label" id="content-label">Content</span><HelpTip
                      label="Content"
                      text="Photo picks models trained on camera pictures: skin, foliage, fabric, buildings. Illustration picks models for drawings, anime and comics, which keep lines clean and colours flat. Choose what the picture is, not the look you want."
                    />
                  </div>
                  <div class="seg-inline" role="radiogroup" aria-labelledby="content-label">
                    <button
                      role="radio"
                      aria-checked={settings.content !== 'illustration'}
                      class:active={settings.content !== 'illustration'}
                      title="Uses a model made for photographs"
                      on:click={() => setContent('photo')}>Photo</button
                    >
                    <button
                      role="radio"
                      aria-checked={settings.content === 'illustration'}
                      class:active={settings.content === 'illustration'}
                      title="Uses a model made for drawings and anime"
                      on:click={() => setContent('illustration')}>Illustration</button
                    >
                  </div>
                  {#if settings.task === 'upscale'}
                    <div class="field-heading">
                      <span class="field-label" id="fix-label">Fix first</span><HelpTip
                        label="Fix first"
                        text="Each problem you pick gets its own fix, run before the upscaler in the order listed under Model. The ↑ and ↓ buttons change that order; each fix's Change… picks its model, which LocalSR remembers for that problem."
                      />
                    </div>
                  {/if}
                {/if}
                <div
                  class="chips"
                  role="group"
                  aria-label={settings.task === 'denoise' ? 'Problems to fix' : 'Fix first'}
                >
                  {#each fixChips as fix (fix)}
                    <button
                      class="chip"
                      type="button"
                      class:on={settings.fixes.includes(fix)}
                      aria-pressed={settings.fixes.includes(fix)}
                      disabled={fix === 'faces' && !faceModel}
                      title={fix === 'faces' && !faceModel
                        ? 'Faces need a model with a face companion (HAT-S or HAT-L).'
                        : ''}
                      on:click={() => toggleFix(fix)}
                      >{#if settings.fixes.includes(fix)}<svg
                          xmlns="http://www.w3.org/2000/svg"
                          width="12"
                          height="12"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          stroke-width="2.5"
                          stroke-linecap="round"
                          stroke-linejoin="round"
                          aria-hidden="true"><path d="m5 12 5 5L20 7" /></svg
                        >{/if}{FIX_LABELS[fix]}</button
                    >
                  {/each}
                </div>
              </section>
            {/if}

            <section class="control-section model-section" aria-label="Model plan">
              <div class="eyebrow eyebrow-row">
                <span
                  >{activeQuality === 'custom' && settings.task !== 'edit'
                    ? 'Model · chosen by you'
                    : 'Model'}</span
                >
                <span class="eyebrow-actions">
                  {#if activeQuality === 'custom' && bestModel && settings.task !== 'edit'}<button
                      class="link-button"
                      type="button"
                      on:click={() => applyPreset('best')}>Back to Best</button
                    >{/if}
                  {#if !plan.some((stage) => stage.role === 'primary')}<button
                      class="link-button"
                      type="button"
                      on:click={() => openLibrary(settings.task === 'edit' ? 'edit' : 'primary')}
                      >Change…</button
                    >{/if}
                </span>
              </div>
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
                {#if selectedMedia?.hdr_format && !hdrPreservationAvailable}
                  <p class="model-output-note" role="status">
                    SDR output selected. This model cannot preserve HDR.
                  </p>
                {/if}
              {/if}

              <div
                class="plan"
                class:movable={imageFixes.length > 1}
                role="list"
                aria-label="Stages"
              >
                {#each plan as stage (`${stage.role}:${stage.fix ?? stage.kind}`)}
                  {@const target =
                    stage.model ?? stage.videoModel ?? stage.editModel ?? stage.interpolationModel}
                  {@const changeSlot =
                    stage.role === 'fix'
                      ? stage.fix && 'fix'
                      : stage.role === 'primary' && !stage.videoModel
                        ? stage.editModel
                          ? 'edit'
                          : 'primary'
                        : undefined}
                  {@const moreModelCount =
                    changeSlot && settings.task !== 'edit'
                      ? modelsAvailableBeyondSelection(
                          libraryModelsForTask(
                            snapshot.catalog.models,
                            settings.task,
                            settings.content,
                            stage.fix,
                          ),
                          stage.model?.model_id ?? '',
                        )
                      : 0}
                  {@const tryOthers = stage.role === 'primary' && canTryOthers}
                  {@const name = stage.model
                    ? displayName(stage.model)
                    : stage.videoModel
                      ? stage.videoModel.name
                      : stage.interpolationModel
                        ? stage.interpolationModel.name.split(' — ')[0]
                        : stage.editModel
                          ? editFamilyLabel(stage.editModel.family)
                          : stage.missing
                            ? 'No model for this fix'
                            : 'Your checkpoint'}
                  <!-- Licenses live in the model library; only exceptions show here. -->
                  {@const meta = stage.editModel
                    ? `${editVariantName(stage.editModel)}${
                        editFit !== 'unknown' && editFit !== 'runs'
                          ? ` · ${EDIT_FIT_LABELS[editFit]}`
                          : ''
                      }`
                    : stage.model
                      ? [
                          stage.remembered ? 'Your pick' : '',
                          stage.model.support_tier === 'labs' ? 'Labs' : '',
                          stage.model.commercial_use_allowed === false ? 'Non-commercial' : '',
                          ['heavy', 'too_heavy'].includes(fitFor(stage.model, activeDevice))
                            ? FIT_LABELS[fitFor(stage.model, activeDevice)]
                            : '',
                        ]
                          .filter(Boolean)
                          .join(' · ')
                      : stage.videoModel
                        ? 'Labs'
                        : stage.interpolationModel
                          ? [
                              settings.video_interpolation && settings.video_interpolation !== 'off'
                                ? frameRateChange(
                                    settings.video_interpolation,
                                    selectedMedia?.fps ?? 0,
                                  )
                                : '',
                              'Labs',
                            ]
                              .filter(Boolean)
                              .join(' · ')
                          : ''}
                  {@const warning =
                    stage.model && UNSTABLE_FIX_MODELS.has(stage.model.model_id)
                      ? stage.role === 'fix'
                        ? 'Can add artifacts to sharp pictures and line art. Skipped if the result is unstable.'
                        : 'Can add artifacts to sharp pictures and line art.'
                      : ''}
                  {@const movable = stage.fix && imageFixes.length > 1}
                  {@const fixIndex = stage.fix
                    ? imageFixes.findIndex((selection) => selection.fix === stage.fix)
                    : -1}
                  <!-- Line 1: role, name, arrows and Change…; line 2 the library links; line 3 the note. -->
                  <div class="plan-row" role="listitem">
                    <span class="stage">{stage.label}</span>
                    <div class="name" title={name}>{name}</div>
                    <div class="plan-end">
                      <span class="state" class:warn={stage.needsFile}
                        >{activeDownload === target?.model_id
                          ? `${Math.round(snapshot.runtime.download_progress)}%`
                          : stage.needsFile && !stage.installed
                            ? 'Needs your file'
                            : ''}</span
                      >
                      {#if movable}
                        <!-- Only the moves this fix can make, directly left of Change…. -->
                        <span class="plan-moves">
                          {#if fixIndex > 0}<button
                              class="move-button"
                              type="button"
                              title="Move earlier"
                              aria-label={`Move the ${FIX_NAMES[stage.fix!]} fix earlier`}
                              on:click={() => void moveFix(stage.fix!, -1)}>↑</button
                            >{/if}
                          {#if fixIndex >= 0 && fixIndex < imageFixes.length - 1}<button
                              class="move-button"
                              type="button"
                              title="Move later"
                              aria-label={`Move the ${FIX_NAMES[stage.fix!]} fix later`}
                              on:click={() => void moveFix(stage.fix!, 1)}>↓</button
                            >{/if}
                        </span>
                      {/if}
                      {#if changeSlot}<button
                          class="link-button"
                          type="button"
                          aria-label={changeSlot === 'fix' && stage.fix
                            ? `Change the ${FIX_NAMES[stage.fix]} fix model`
                            : changeSlot === 'edit'
                              ? 'Change the edit model'
                              : settings.task === 'denoise'
                                ? 'Change the restore model'
                                : 'Change the upscaler'}
                          on:click={() => openLibrary(changeSlot, stage.fix)}>Change…</button
                        >
                      {/if}
                    </div>
                    {#if moreModelCount || tryOthers}<div class="plan-links">
                        {#if moreModelCount}<button
                            class="link-button"
                            type="button"
                            aria-label={`${moreModelCount} more ${moreModelCount === 1 ? 'model' : 'models'} in the library`}
                            on:click={() => changeSlot && openLibrary(changeSlot, stage.fix)}
                            >{moreModelCount} more {moreModelCount === 1
                              ? 'model'
                              : 'models'}</button
                          >{/if}
                        {#if tryOthers}<span class="try-others"
                            ><button
                              class="link-button"
                              type="button"
                              aria-label={settings.task === 'denoise'
                                ? 'Try other restoration models on this image'
                                : 'Try other upscalers on this image'}
                              disabled={!canOpenTrials}
                              on:click={() => openLibrary('try', stage.fix)}>Try others…</button
                            ><HelpTip
                              label="Try others"
                              text="Runs this image once per model you pick, with the same settings. Each result is kept as its own file, and Showing in the preview lets you flip between them."
                            /></span
                          >{/if}
                      </div>{/if}
                    {#if meta || warning || stage.custom !== undefined}<p
                        class="plan-note"
                        title={(stage.custom !== undefined
                          ? settings.custom_model_path
                          : [meta, warning].filter(Boolean).join(' · ')) || undefined}
                      >
                        {#if stage.custom !== undefined}<span class="path-text"
                            >{settings.custom_model_path ||
                              'Safetensors is strongly recommended.'}</span
                          >{:else}{meta}{#if meta && warning}{' · '}{/if}{#if warning}<span
                              >{warning}</span
                            >{/if}{/if}
                      </p>{/if}
                    {#if stage.custom !== undefined || (target && !stage.installed)}<div
                        class="plan-extra"
                      >
                        {#if stage.custom !== undefined}
                          <button class="button full" on:click={chooseCustomModel}
                            >{settings.custom_model_path
                              ? 'Choose another checkpoint…'
                              : 'Choose checkpoint…'}</button
                          >
                          {#if customModelNeedsOptIn}
                            <label class="terms"
                              ><input
                                type="checkbox"
                                checked={settings.allow_unsafe_pickle_model}
                                on:change={(event) =>
                                  updateSettings({
                                    allow_unsafe_pickle_model: event.currentTarget.checked,
                                  })}
                              /> Allow this pickle-based checkpoint. It may execute code inside the inference
                              worker.</label
                            >
                          {/if}
                        {:else if stage.model && licenseReviewModel?.model_id === stage.model.model_id && !stage.installed}
                          <ModelCredit
                            model={stage.model}
                            openLicense={() => openAnyLicense(stage.model!.model_id)}
                            openSource={() => openAnySource(stage.model!.model_id)}
                          />
                        {:else if stage.kind === 'face_restore' && stage.model && !stage.installed}
                          <p class="model-description">
                            Optional companion pass for faces. The exact checkpoint's redistribution
                            and training-data rights are not verified, so LocalSR only accepts a
                            matching user-supplied copy.
                          </p>
                          {#if stage.model.terms_acceptance_required}
                            <label class="terms"
                              ><input type="checkbox" bind:checked={faceTermsAccepted} /> I understand
                              that the checkpoint rights are unresolved and will provide a copy I am permitted
                              to use.</label
                            >
                          {/if}
                          <button
                            class="button full"
                            class:primary={stage.model.automated_download_allowed}
                            disabled={Boolean(activeDownload) &&
                              activeDownload !== stage.model.model_id}
                            on:click={() => runDownload(stage.model, faceTermsAccepted)}
                          >
                            {activeDownload === stage.model.model_id
                              ? `Downloading ${Math.round(snapshot.runtime.download_progress)}% · Cancel`
                              : stage.model.automated_download_allowed
                                ? `Download companion · ${formatBytes(stage.model.size_bytes)}`
                                : 'Choose externally downloaded face checkpoint…'}
                          </button>
                        {:else if target && !stage.installed}
                          <!-- A downloadable model is fetched by the Start button; this row only
                             asks for what Start cannot do on its own. -->
                          {#if (stage.model ?? stage.editModel)?.terms_acceptance_required}
                            <label class="terms"
                              ><input
                                type="checkbox"
                                bind:checked={termsAccepted[target!.model_id]}
                              /> I reviewed the model license and restrictions.</label
                            >
                          {/if}
                          {#if !stage.canDownload || target.terms_acceptance_required || activeDownload === target.model_id}
                            <button
                              class="button full"
                              disabled={(stage.videoModel && Boolean(temporalUnavailableReason)) ||
                                preparing ||
                                (Boolean(activeDownload) && activeDownload !== target.model_id)}
                              on:click={() => runDownload(target)}
                            >
                              {activeDownload === target.model_id
                                ? `Downloading ${Math.round(snapshot.runtime.download_progress)}% · Cancel`
                                : stage.canDownload
                                  ? `Download model · ${formatBytes(stage.sizeBytes)}`
                                  : 'Choose externally downloaded checkpoint…'}
                            </button>
                          {/if}
                          {#if activeDownload === target.model_id}<div class="download-track">
                              <i style={`width:${snapshot.runtime.download_progress}%`}></i>
                            </div>{/if}
                        {/if}
                      </div>{/if}
                  </div>
                {/each}
              </div>
              {#if !fixPlanSupported && snapshot.engine}<p class="inline-warning" role="status">
                  This engine runs one fix at a time. Update LocalSR to run several.
                </p>{/if}
              {#if settings.task === 'edit'}
                {#if editModel?.terms_acceptance_required && editModel.installed}
                  <label class="terms"
                    ><input type="checkbox" bind:checked={termsAccepted[editModel.model_id]} /> I reviewed
                    the Qwen Research License and accept it for this edit.</label
                  >
                {/if}
                {#if editBlockReason}
                  <p class="inline-warning" role="status">
                    {editBlockReason}
                    {#if !editRuntime && editingDevice && editSupported}<button
                        class="link-button"
                        type="button"
                        on:click={chooseEditRuntime}>Choose runtime…</button
                      >{/if}
                  </p>
                {/if}
              {/if}
              {#if faceEnabled && faceModel}
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
                    >0% retains the resampled original face; 100% applies the strongest restoration.
                    Non-face regions keep the primary result.</small
                  >
                </div>
              {/if}
              {#if settings.fixes.includes('faces') && faceModel && !faceEngineAvailable}
                <p class="inline-warning" role="status">
                  Face-aware processing stays off: the local face detector is not packaged for this
                  platform. The normal model still works.
                </p>
              {/if}
              {#if activeQuality === 'custom' && settings.task !== 'edit'}
                <p class="model-note">Chosen by you. Pick Quick or Best to go back.</p>
              {:else if downloads.stages.length}
                <p class="model-note">
                  Models download once when you start and stay on this computer. Your {settings.task ===
                  'video'
                    ? 'videos'
                    : 'photos'} never leave it.
                </p>
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

            {#if lastFailure}
              <section
                class="control-section failure-note"
                role="alert"
                aria-label="Last error"
                tabindex="-1"
                bind:this={failureNote}
              >
                <p>{lastFailure}</p>
                {#if acceleratorFailure}
                  <button class="button" type="button" on:click={() => void useTheCpu()}
                    >Use the CPU</button
                  >
                {/if}
              </section>
            {/if}
            <AdvancedSettings
              {settings}
              {selectedModel}
              {editModel}
              capabilities={snapshot.capabilities}
              {usingTemporalVideo}
              {preservingHdr}
              interpolationSupported={snapshot.engine?.features.includes('video_interpolation') ===
                true}
              {updateSettings}
              {chooseOutput}
              refreshCapabilities={async () => {
                try {
                  await api.refreshCapabilities();
                  return true;
                } catch (error) {
                  showModal('Could not refresh the hardware', String(error));
                  return false;
                }
              }}
            />
          {/if}
        </fieldset>
      </div>
    </aside>

    <UpdateNotice
      bind:height={updateNoticeHeight}
      processing={settingsLocked || inflightMediaIds.size > 0 || Boolean(activeDownload)}
    />
  </main>

  <footer class="status-bar">
    <div class="status-copy">
      <i class:working={Boolean(snapshot.runtime.active_job_id)}></i>
      <div>
        <strong
          >{booting
            ? 'Starting'
            : cancelling && snapshot.runtime.active_job_id
              ? 'Stopping…'
              : saving
                ? 'Saving the result'
                : snapshot.runtime.status_title}{snapshot.runtime.active_job_id &&
          snapshot.runtime.progress > 0 &&
          !saving &&
          !cancelling
            ? ` · ${Math.round(snapshot.runtime.progress)}%`
            : ''}{queuedCount ? ` · ${queuedCount} queued` : ''}</strong
        ><span
          class="status-detail"
          role={activityDetail ? 'status' : undefined}
          title={booting
            ? undefined
            : activityDetail || stoppedNote || idleSummary || runningStatusDetail}
          >{booting
            ? 'Opening the trusted desktop control plane.'
            : activityDetail || stoppedNote || idleSummary || runningStatusDetail}</span
        >
        {#if hardwareNote}<span class="status-note" title={hardwareNote}>{hardwareNote}</span>{/if}
        {#if hardwareHint}<span class="status-note" title={hardwareHint.text}
            >{hardwareHint.text}
            {#if hardwareHint.downloads}<button
                class="link-button"
                type="button"
                on:click={() =>
                  attempt('Could not open the download page', () => api.openDownloadPage())}
                >Download page</button
              >{/if}</span
          >{/if}
        {#if queueEta && !saving && !cancelling}<span
            class="queue-eta"
            title="Approximate timings from measured jobs with the same model and settings. Model loading and export can change the estimate."
          >
            Current item: {shownEta.current === null ? 'measuring…' : `≈ ${shownEta.current}`} · Whole
            queue: {shownEta.queue === null
              ? `measuring ${queueEta.unknownItems} item${queueEta.unknownItems === 1 ? '' : 's'}…`
              : `≈ ${shownEta.queue}`}
          </span><span class="queue-next"
            >Next: {queueEta.nextName} · {shownEta.next === null
              ? 'time not yet measured'
              : `≈ ${shownEta.next}`}</span
          >{/if}
      </div>
    </div>
    {#if livePreviewWarning}<span class="inline-warning preview-warning" role="status"
        >Preview warning: {livePreviewWarning} Processing continues normally.</span
      >{/if}
    {#if snapshot.runtime.active_job_id}<div
        class="progress"
        role="progressbar"
        aria-label="Job progress"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow={Math.round(snapshot.runtime.progress)}
      >
        <i style={`width:${snapshot.runtime.progress}%`}></i>
      </div>{/if}
    <div class="status-actions">
      {#if snapshot.runtime.engine_retry}<button
          class="button"
          disabled={retryingEngine}
          title="Start the inference engine again"
          on:click={() => void retryEngine()}>Try again</button
        >{/if}
      {#if selectedOutputPath}<button
          class="button"
          title={`Show ${selectedOutputName} in its folder`}
          on:click={() =>
            attempt('Could not show the result', () => api.revealResult(selectedOutputPath))}
          >Reveal</button
        ><button
          class="button"
          title={`Open ${selectedOutputName}`}
          on:click={() =>
            attempt('Could not open the result', () => api.openResult(selectedOutputPath))}
          >Open</button
        ><button
          class="button"
          title="Add this result to Inputs so it can be processed again"
          on:click={() => void useResultAsInput()}>Use as input</button
        >{/if}
      {#if snapshot.runtime.active_job_id}
        <!-- A count of 0 means there is nothing to add or start: no button, and no room kept. -->
        {#if queueSelection.length}<button
            class="button queue-more"
            disabled={!canAppendToQueue}
            on:click={() => start()}
            >{batchLabel
              ? `Add ${queueSelection.length} to queue`
              : 'Add selected to queue'}</button
          >{/if}
        {#if canCancelSelectedImage}
          <button
            class="button"
            disabled={cancelling || cancellingImage}
            title="Cancel this image's queued runs and stop its running one. Other images stay queued; finished results stay."
            on:click={() => void cancelSelectedImage()}>Cancel runs for this image</button
          >
        {/if}
        <button class="button danger" disabled={cancelling} on:click={cancelWork}
          >{cancelling ? 'Stopping…' : inflightJobCount > 1 ? 'Cancel queue' : 'Cancel'}</button
        >
      {:else if preparing}
        <button class="button danger start" on:click={() => void cancelPreparation()}
          >{preparingCancelled ? 'Cancelling…' : 'Cancel download'}</button
        >
      {:else if scopeChoice}
        <button
          class="button start scope-one"
          disabled={!canStartOne && !canPrepareOne}
          title={oneTitle || undefined}
          on:click={() => void startScope(false)}>Only this one</button
        >
        <button
          class="button primary start"
          disabled={!canStartAll && !canPrepareAll}
          title={allTitle || undefined}
          on:click={() => void startScope(true)}
          >{canPrepareAll
            ? `Download ${formatBytes(downloads.bytes)}, then ${allPhrase}`
            : allPhrase.charAt(0).toUpperCase() + allPhrase.slice(1)}</button
        >
      {:else if canPrepare}
        <button
          class="button primary start"
          title={singleHint || undefined}
          on:click={() => void prepareAndStart()}
          >{`Download ${formatBytes(downloads.bytes)}, then ${startVerb}`}</button
        >
      {:else if !batchLabel || queueSelection.length}
        <button
          class="button primary start"
          disabled={!canStart}
          title={singleHint || undefined}
          on:click={() => start()}
          >{settings.task === 'video'
            ? 'Start selected video'
            : settings.task === 'denoise'
              ? 'Restore selected'
              : settings.task === 'edit'
                ? 'Edit selected'
                : 'Upscale selected'}</button
        >
      {/if}
    </div>
  </footer>
</div>

{#if libraryOpen}
  <ModelLibrary
    {snapshot}
    {settings}
    context={{
      task: settings.task,
      content: settings.content,
      quality: activeQuality,
      slot: libraryContext.slot,
      fix: libraryContext.fix,
    }}
    device={activeDevice}
    {activeDownload}
    downloadProgress={snapshot.runtime.download_progress}
    busy={settingsLocked || preparing}
    close={closeLibrary}
    {trialMedia}
    {trialBase}
    trialBusy={startingJob || preparing}
    {runTrials}
    useForJob={useModelForJob}
    rememberFix={rememberFixModel}
    forgetFix={forgetFixModel}
    {useEditModel}
    pin={pinPreset}
    download={(model, accepted) => runDownload(model, accepted ?? false)}
    importFile={(model, accepted) => runDownload(model, accepted)}
    remove={removeModel}
    chooseCustom={chooseCustomModel}
    openLicense={openAnyLicense}
    openSource={openAnySource}
  />
{/if}

{#if ffmpegNotice}
  <FFmpegNotice
    context={ffmpegNotice.context}
    mediaNames={mediaNamesFor(ffmpegNotice.paths)}
    systemCodecs={systemCodecs(snapshot.capabilities)?.label ?? ''}
    container={settings.video_container ?? 'mp4'}
    onSelected={useExternalFFmpeg}
    onClose={() => (ffmpegNotice = null)}
  />
{/if}

{#if modalTitle}
  <div class="modal-backdrop" role="presentation" on:click={closeFromBackdrop}>
    <div
      class="modal"
      class:performance-modal={modalKind === 'performance'}
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
      tabindex="-1"
      bind:this={modalElement}
      use:modal={closeModal}
    >
      <h2 id="modal-title">{modalTitle}</h2>
      <p class="modal-message">{modalMessage}</p>
      {#if modalKind === 'performance'}
        <section class="benchmark-panel" aria-labelledby="benchmark-title">
          <div class="benchmark-heading">
            <div>
              <span class="eyebrow">Benchmark v2.1</span>
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
                on:click={() =>
                  api
                    .cancelDownload('span_photo_x4')
                    .catch((error) => (benchmarkError = String(error)))}>Cancel download</button
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
                            ><FileName name={device.device_name} /></strong
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
                {formatDuration(snapshot.runtime.elapsed_seconds)} / {shownEta.job ?? '—'}
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
        {#if modalConfirm}
          <button class="button" data-autofocus on:click={closeModal}>Cancel</button>
          <button class="button danger" on:click={confirmModal}>{modalConfirm.label}</button>
        {:else}
          <button
            class="button primary"
            data-autofocus={modalKind === 'message' ? '' : undefined}
            on:click={closeModal}>OK</button
          >
        {/if}
      </div>
    </div>
  </div>
{/if}
