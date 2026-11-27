<script lang="ts">
  import { detectPlatform } from './lib/platform';
  import { downloadFailureText, type DownloadFailure } from './lib/downloadProgress';
  import { DownloadRun } from './lib/downloadSummary';
  import { modelDisplayName, modelDownloadSize } from './lib/modelNames';
  import { createAccentController } from './lib/accent';
  import { createThemeController } from './lib/theme';

  import { tabFromKey } from './lib/keyboard';
  import { isRestoreRecipe, recipeFixStages, recipeMatches } from './lib/recipes';
  import {
    benchmarkDeviceBarWidth,
    benchmarkOneMpExample,
    benchmarkSceneLabel,
    benchmarkSpeedBarWidth,
  } from './lib/benchmark';
  import { without, mediaNamesFor as mediaNamesForPaths } from './lib/collections';
  import {
    presetDownload,
    resolveAudioModel,
    supportsHdrPreservation as catalogSupportsHdrPreservation,
  } from './lib/plan';
  import { onMount, tick } from 'svelte';
  import PreviewPane from './PreviewPane.svelte';
  import ComparePane from './ComparePane.svelte';
  import {
    outputGroups,
    resultDetails,
    pickKey,
    toggleSlot,
    swapSlots,
    type CompareItem,
  } from './lib/compareOutputs';
  import { keptTilesText, unfinishedMark, type PaneUnfinished } from './lib/unfinished';
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
  import EnhancePanel from './EnhancePanel.svelte';
  import type { EnhancePanelActions, EnhancePanelView } from './lib/enhancePanel';
  import FileName from './FileName.svelte';
  import FFmpegNotice from './FFmpegNotice.svelte';
  import { exportNeedsExternalFFmpeg, probeNeedsExternalFFmpeg, systemCodecs } from './lib/ffmpeg';
  import UpdateNotice from './UpdateNotice.svelte';
  import GpuDownloadNotice from './GpuDownloadNotice.svelte';
  import LinuxMenuNotice from './LinuxMenuNotice.svelte';
  import EngineRecommendationCard from './EngineRecommendationCard.svelte';
  import EngineManagerDialog from './EngineManagerDialog.svelte';
  import SettingsSheet from './SettingsSheet.svelte';
  import UpdatePanel from './UpdatePanel.svelte';
  import SetupScreen from './SetupScreen.svelte';
  import WelcomeNotice from './WelcomeNotice.svelte';
  import { startUpdateWatcher, updateDialogOpen, updateState } from './lib/updates';
  import { redactDiagnostics, redactDiagnosticText, reportUrl } from './lib/report';
  import BenchmarkStudio from './BenchmarkStudio.svelte';
  import { customModels, defaultCustomModelName } from './lib/customModels';
  import CustomEditModelModal from './CustomEditModelModal.svelte';
  import * as api from './lib/api';
  import ModelLibrary from './ModelLibrary.svelte';
  import {
    editDevice,
    editDimensions,
    editExpert,
    editFitFor,
    editHardwareLabel,
    editModelFits,
    editRecommendedSize,
    editSizeFor,
    recommendAnyEditModel,
  } from './lib/editing';
  import {
    activeStepStages,
    choosePresetModel,
    effectiveVideoTask,
    fixesOf,
    isRestoreTask,
    modelsForTask,
    pendingDownloads,
    stepStagesReady,
    withKeptBytes,
    presetSlot,
  } from './lib/plan';
  import { createPlanClient, fetchPlan } from './lib/jobPlanClient';
  import {
    chooseFixModel,
    fitsFix,
    fixQuality,
    fixSlot,
    fixStageKind,
    moveFixStage,
    toggleFixStage,
  } from './lib/fixStages';
  import { DEFAULT_FIX_ORDER, FIX_NAMES, MULTI_FIX_FEATURE, stepFeature } from './lib/steps';
  import { applyWorkerEnvelope, resultPreviewForSelectedMedia } from './lib/workerEvents';
  import { stepLabel, stepName } from './lib/jobSteps';
  import { formatBytes, formatDuration, formatVideoClock } from './lib/format';
  import { plainEngineError, recommendedEngineOffer } from './lib/engines';
  import { videoAvailability } from './lib/videoEngines';
  import { buildStartInput } from './lib/startInput';
  import { demoSnapshot } from './lib/demo';
  import { modal } from './lib/modal';
  import {
    colourHardwareLabel,
    engineLabel,
    hardwarePatch,
    interpolationHardwareLabel,
    withListedDevice,
  } from './lib/neuralEngine';
  import { cpuHint, deviceLabel } from './lib/hardwareRoutes';
  import type {
    AppSnapshot,
    ComparePick,
    BenchmarkDeviceResult,
    BenchmarkRender,
    CatalogModel,
    CatalogVideoModel,
    EditModel,
    EngineDownloadStatus,
    FixKind,
    FixStageSelection,
    IntegrationStatus,
    JobStep,
    LaunchIntent,
    MediaItem,
    MediaResults,
    ModelDownloadStatus,
    ModelMoveProgress,
    PairChoice,
    Quality,
    Recipe,
    ResultSummary,
    SettingsSection,
    StartBatchInput,
    TaskKind,
    UiSettings,
    WorkerEnvelope,
  } from './lib/types';

  let snapshot: AppSnapshot = demoSnapshot();
  let booting = true;
  let editPrompt = '';
  let editRuntimeOverride = '';
  let customEditOpen = false;
  let benchmarkRenders: BenchmarkRender[] = [];
  let benchmarkSetup: 'idle' | 'starting' = 'idle';
  let benchmarkError = '';
  let benchmarkTile: WorkerEnvelope | undefined;
  let downloadErrors: Record<string, string> = {};
  let downloadFailures: Record<string, DownloadFailure> = {};
  /** What an interrupted or cancelled download kept per model id, until it completes. */
  let keptDownloadBytes: Record<string, number> = {};
  let downloadAcceptedTerms: Record<string, boolean> = {};
  let rememberedFailureIds: string[] = [];
  const cancelledDownloads = new Set<string>();
  let cancellingDownloads = false;
  const downloadRun = new DownloadRun();
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
      media_kind: media?.kind,
      fps: result?.output_fps || media?.fps || 30,
      pick,
      media_name: media?.name ?? 'Image',
      label: pick.kind === 'original' ? 'Original' : result?.model_label || 'Earlier result',
      meta: result
        ? [
            result.partial ? 'Unfinished' : '',
            result.ui?.quality,
            result.scale ? `${result.scale}×` : '',
            result.elapsed_seconds ? formatDuration(result.elapsed_seconds) : '',
          ]
            .filter(Boolean)
            .join(' · ')
        : `${media?.width ?? 0} × ${media?.height ?? 0}`,
      details: result
        ? resultDetails(result, slot?.width, slot?.height)
        : media?.width && media?.height
          ? [`Size: ${media.width} × ${media.height}`]
          : [],
      unfinished: unfinishedMark(result, {
        duration: media?.duration_seconds,
        fps: media?.fps,
        decoded: slot,
        source: media,
      }),
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
  let deletingRecipeId: string | null = null;
  let recipeName = '';
  // Model terms are accepted per model: several fixes can each need their own.
  let termsAccepted: Record<string, boolean> = {};
  let previewPane: PreviewPane | undefined;
  let inspectorScroll: HTMLDivElement | undefined;
  let lastLivePreviewSequence = 0;
  $: activeStepPreview =
    snapshot.runtime.job_steps?.reduceRight((found, step) => found || step.preview_url, '') ?? '';
  let skippedStepLabel = '';
  let skippedStepTimer: ReturnType<typeof setTimeout> | undefined;
  let trackedStepJobId = '';
  let trackedStepStates: JobStep['state'][] = [];

  function clearSkippedStepLabel(): void {
    if (skippedStepTimer) clearTimeout(skippedStepTimer);
    skippedStepTimer = undefined;
    skippedStepLabel = '';
  }

  function observeJobSteps(
    jobId: string,
    steps: readonly JobStep[],
    currentSettings: UiSettings,
    isVideo: boolean,
  ): void {
    if (!jobId || steps.length < 2) {
      trackedStepJobId = '';
      trackedStepStates = [];
      clearSkippedStepLabel();
      return;
    }
    const nextStates = steps.map((step) => step.state);
    if (trackedStepJobId !== jobId || trackedStepStates.length !== steps.length) {
      trackedStepJobId = jobId;
      trackedStepStates = nextStates;
      clearSkippedStepLabel();
      return;
    }
    // Keep a newly skipped step in the viewer corner for 4 s before the next step's label.
    for (let index = 0; index < steps.length; index += 1) {
      if (steps[index].state === 'skipped' && trackedStepStates[index] !== 'skipped') {
        const name = stepName(steps[index], currentSettings, isVideo);
        skippedStepLabel = `Step ${index + 1} of ${steps.length} · ${name} skipped`;
        if (skippedStepTimer) clearTimeout(skippedStepTimer);
        skippedStepTimer = setTimeout(() => {
          skippedStepTimer = undefined;
          skippedStepLabel = '';
        }, 4000);
      }
    }
    trackedStepStates = nextStates;
  }
  let diagnostics = '';
  let reportOpen = false;
  let reportDiagnostics = '';
  let reportLoading = false;
  let reportIncludeDiagnostics = true;
  let reportHappened = '';
  let reportMessage = '';
  let crashReports: api.StoredCrashReport[] = [];
  let crashNotice = false;
  let reviewedCrash: api.StoredCrashReport | null = null;

  async function showReport(): Promise<void> {
    if (modalTitle) closeModal();
    reviewedCrash = null;
    reportOpen = true;
    reportIncludeDiagnostics = true;
    reportHappened = '';
    reportMessage = '';
    reportDiagnostics = '';
    reportLoading = true;
    try {
      reportDiagnostics = redactDiagnostics(await api.diagnosticSummary());
    } catch (error) {
      reportDiagnostics = '';
      reportMessage = String(error);
    } finally {
      reportLoading = false;
    }
  }
  async function refreshCrashReports(): Promise<void> {
    crashReports = await api.listCrashReports();
  }
  function closeReport(): void {
    reportOpen = false;
    reviewedCrash = null;
  }
  function crashTitle(crash: api.StoredCrashReport): string {
    const shortError =
      redactDiagnosticText(crash.report.message).replace(/\s+/g, ' ').trim().slice(0, 120) ||
      'Unexpected error';
    return `Crash: ${shortError}`;
  }
  function reviewCrash(): void {
    const crash = crashReports[0];
    if (!crash) return;
    if (modalTitle) closeModal();
    crashNotice = false;
    reviewedCrash = crash;
    reportOpen = true;
    reportIncludeDiagnostics = true;
    reportHappened = crashTitle(crash);
    reportDiagnostics = redactDiagnostics(JSON.stringify(crash.report, null, 2));
    reportMessage = '';
    reportLoading = false;
  }
  async function discardCrash(): Promise<void> {
    const crash = crashReports[0];
    if (!crash) return;
    try {
      await api.deleteCrashReport(crash.id);
      await refreshCrashReports();
      crashNotice = false;
    } catch (error) {
      showModal('Could not delete report', String(error));
    }
  }
  async function removeAllCrashes(): Promise<void> {
    try {
      await api.deleteAllCrashReports();
      await refreshCrashReports();
      crashNotice = false;
    } catch (error) {
      showModal('Could not delete reports', String(error));
    }
  }
  async function openReport(): Promise<void> {
    try {
      let platform = '';
      try {
        const parsed = JSON.parse(reportDiagnostics);
        const value = parsed.platform ?? parsed.os;
        platform =
          value === 'macos'
            ? 'macOS'
            : value === 'windows'
              ? 'Windows'
              : value === 'linux'
                ? 'Linux'
                : '';
      } catch {
        /* No diagnostic platform available. */
      }
      const title = reviewedCrash
        ? reportHappened.startsWith('Crash:')
          ? reportHappened
          : crashTitle(reviewedCrash)
        : '';
      let body = reportIncludeDiagnostics ? reportDiagnostics : '';
      let url = reportUrl(reportHappened, snapshot.app_version, platform, body, title);
      if (url.length > 7000 && body) {
        await navigator.clipboard.writeText(body);
        body = 'Diagnostics were copied to your clipboard; paste them here.';
        reportMessage = body;
        url = reportUrl(reportHappened, snapshot.app_version, platform, body, title);
      }
      await api.openBugReport(url);
      if (reviewedCrash) {
        await api.deleteCrashReport(reviewedCrash.id);
        await refreshCrashReports();
        reviewedCrash = null;
        reportOpen = false;
      }
    } catch (error) {
      reportMessage = String(error);
    }
  }
  let setupAppearance: UiSettings['appearance'] = 'dark';
  let setupColourBlind = false;
  let setupContrast: NonNullable<UiSettings['contrast']> = 'system';
  let setupSound = false;
  let setupCheckAtLaunch = true;
  let setupMenuBar = true;
  let setupAnonymousCount = true;
  let setupCrashReports = true;
  let privacySaving = false;
  let privacyError = '';

  function previewSetupTheme(
    appearance: UiSettings['appearance'],
    colourBlindPalette: boolean,
    contrast: NonNullable<UiSettings['contrast']>,
  ): void {
    setupAppearance = appearance;
    setupColourBlind = colourBlindPalette;
    setupContrast = contrast;
    themeForRefresh({
      ...snapshot.settings,
      appearance,
      colour_blind_palette: colourBlindPalette,
      contrast,
    });
  }

  async function finishSetup(choices: {
    appearance: UiSettings['appearance'];
    colourBlind: boolean;
    checkAtLaunch: boolean;
    anonymousCount: boolean;
    crashReports: boolean;
    menuBar: boolean | null;
    contrast: NonNullable<UiSettings['contrast']>;
    sound: boolean;
  }): Promise<void> {
    privacySaving = true;
    privacyError = '';
    try {
      await api.setPrivacyChoice({
        appearance: choices.appearance,
        colourBlindPalette: choices.colourBlind,
        contrast: choices.contrast,
        showInMenuBar: choices.menuBar,
        completionSound: choices.sound,
        checkAtLaunch: choices.checkAtLaunch,
        anonymousCount: choices.anonymousCount,
        crashReports: choices.crashReports,
      });
      snapshot = {
        ...snapshot,
        settings: {
          ...snapshot.settings,
          appearance: choices.appearance,
          colour_blind_palette: choices.colourBlind,
          contrast: choices.contrast,
          show_in_menu_bar: choices.menuBar ?? snapshot.settings.show_in_menu_bar,
          completion_sound: choices.sound,
          notify_on_finish: choices.sound ? true : snapshot.settings.notify_on_finish,
          privacy_choice_made: true,
          check_updates_at_launch: choices.checkAtLaunch,
          anonymous_update_count: choices.anonymousCount,
          crash_reports: choices.crashReports,
        },
      };
      themeForRefresh(snapshot.settings);
      if (choices.checkAtLaunch) {
        const status = await api.updateStatus();
        if (status.configured && !status.managed_by_store && status.stage === 'idle') {
          await api.checkUpdateAtLaunch(status.channel).catch(() => undefined);
          if (
            snapshot.engines?.supported &&
            !snapshot.engines.managed_by_store &&
            !snapshot.engines.catalog.length
          ) {
            await refresh();
          }
        }
      }
    } catch (error) {
      privacyError = String(error);
    } finally {
      privacySaving = false;
    }
  }
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
  let stopPrompt: {
    scope: 'queue' | 'media';
    mediaId: string;
    jobId: string;
    fileName: string;
  } | null = null;
  let stopDontAskAgain = false;
  $: if (stopPrompt && snapshot.runtime.active_job_id !== stopPrompt.jobId) {
    stopPrompt = null;
    stopDontAskAgain = false;
  }
  $: stopPromptBody = (() => {
    if (!stopPrompt) return '';
    const activeJob = snapshot.jobs.find((job) => job.id === stopPrompt?.jobId);
    const media = activeJob
      ? snapshot.media.find((item) => item.id === activeJob.media_id)
      : undefined;
    if (activeJob?.media_kind === 'image') {
      const finished = snapshot.runtime.tiles_completed ?? 0;
      const tiles = keptTilesText(finished, snapshot.runtime.tiles_total ?? 0);
      return `${tiles ? `${tiles} ${finished === 1 ? 'is' : 'are'} finished.` : 'Part of the photo is finished.'} If you keep it, the photo is saved at full size with the rest left as in the original.`;
    }
    const done = snapshot.runtime.frames_processed ?? 0;
    const totalFrames = snapshot.runtime.total_frames || media?.frame_count || 0;
    const totalDuration = media?.duration_seconds ?? 0;
    if (done > 0 && totalFrames > 0 && totalDuration > 0) {
      const finished = Math.min(totalDuration, Math.max(0, (done / totalFrames) * totalDuration));
      // Under a second a clock would read 0:00, so the frames are counted.
      return finished < 1
        ? `${done} of ${totalFrames} frames ${done === 1 ? 'is' : 'are'} finished.`
        : `${formatVideoClock(finished)} of ${formatVideoClock(totalDuration)} is finished.`;
    }
    return 'Part of the video is finished.';
  })();
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
  let upscalerBeforeSameSize = '';
  let livePreviewWarning = '';
  let preparing = false;
  let preparingCancelled = false;
  let preparingThen = '';
  let preparingTotalBytes = 0;
  let preparingIds: string[] = [];
  let preparingPendingIds: string[] = [];
  const isMac = typeof navigator !== 'undefined' && /Mac/.test(navigator.platform);
  let libraryOpen = false;
  let libraryContext: {
    slot: 'primary' | 'fix' | 'face' | 'browse' | 'edit' | 'try';
    fix?: Exclude<FixKind, 'faces'>;
    select?: string;
  } = { slot: 'browse' };
  let settingsOpen = false;
  let settingsSection: SettingsSection = 'general';
  let settingsFocusTarget: 'hardware' | 'ffmpeg' | null = null;
  function openSettings(
    section: SettingsSection = 'general',
    focusTarget: 'hardware' | 'ffmpeg' | null = null,
  ): void {
    settingsSection = section;
    settingsFocusTarget = focusTarget;
    settingsOpen = true;
  }
  let startingJob = false;
  let requestingCancel = false;
  let movingModels = false;
  let cacheSize: number | null = null;
  let clearingCache = false;

  async function loadCacheSize(): Promise<void> {
    try {
      cacheSize = await api.getCacheSize();
    } catch (error) {
      cacheSize = null;
      showModal('Could not read the cache', String(error));
    }
  }

  /** Clearing is safe but slows the next start of each model, so it asks first. */
  async function askClearCache(): Promise<void> {
    if (clearingCache) return;
    const size = cacheSize ? ` (${formatBytes(cacheSize)})` : '';
    showModal(
      'Clear the model cache?',
      `This deletes the compiled model copies${size}. Your models, settings and results stay. Each model starts more slowly once, while its copy is rebuilt.`,
    );
    modalConfirm = { label: 'Clear cache', run: clearCache };
  }

  async function clearCache(): Promise<void> {
    if (clearingCache) return;
    clearingCache = true;
    try {
      cacheSize = await api.clearCache();
    } catch (error) {
      showModal('Could not clear the cache', String(error));
    } finally {
      clearingCache = false;
    }
  }
  let modelMoveProgress: ModelMoveProgress | null = null;
  // Between two jobs of a queue the host already runs the next one while the window still
  // shows none: its row is queued or starting until the next refresh brings it up.
  $: settingsLocked =
    startingJob ||
    movingModels ||
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
  /** Jobs that ended as a kept, unfinished result in this session. */
  const keptJobIds = new Set<string>();
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
      // A kept part is saved as a completed job; it is not a finished one.
      const kept = own.filter((job) => keptJobIds.has(job.id)).length;
      const finished = own.filter(
        (job) => job.status === 'completed' && !keptJobIds.has(job.id),
      ).length;
      stoppedNote = `Stopped. ${finished} of ${own.length} finished${kept ? `, ${kept} kept unfinished` : ''}.`;
    }
    stopWasOn = now;
  }
  $: if (snapshot.runtime.active_job_id && !cancelling) stoppedNote = '';

  /** Hardware and low priority chosen in Advanced for this session; Settings saves the
   * defaults. A new default in Settings ends the session choice for that setting. */
  let sessionChoice: Partial<Pick<UiSettings, 'device_id' | 'neural_engine' | 'low_priority'>> = {};
  const sessionKeys = ['device_id', 'neural_engine', 'low_priority'] as const;
  $: defaultSettings = withListedDevice(snapshot.settings, snapshot.capabilities);
  $: settings = withListedDevice({ ...snapshot.settings, ...sessionChoice }, snapshot.capabilities);
  // Editing bundle, device and capacity checks.
  $: editModels = snapshot.edit.models;
  $: editingDevice = editDevice(snapshot.capabilities, settings);
  $: editModel =
    settings.edit_model_id === '__custom__'
      ? (snapshot.edit.custom_model ?? undefined)
      : editModels.find((model) => model.model_id === settings.edit_model_id);
  $: editFit = editModel ? editFitFor(editModel, snapshot.capabilities, editingDevice) : 'unknown';
  $: editExpertState = editExpert(
    settings,
    editModel,
    editingDevice,
    snapshot.engine?.features ?? [],
  );
  $: editSizeAllowed = editRecommendedSize(
    editModel,
    snapshot.capabilities,
    editingDevice,
    editExpertState,
  );
  $: editRuntime = editRuntimeOverride || snapshot.edit.runtime_path;
  $: editSupported = snapshot.engine?.features.includes(stepFeature('edit')) === true;
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
              : settings.edit_model_id === '__custom__' && !editModel.installed
                ? 'Choose your custom edit model files again under Change….'
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
  $: videoTask = effectiveVideoTask(settings);
  $: restoring = isRestoreTask(settings);
  $: compatibleModels = modelsForTask(snapshot, settings.task, videoTask);
  $: selectedModel = compatibleModels.find(
    (model) => model.model_id === settings.selected_model_id,
  );
  $: selectedVideoModel = snapshot.catalog.video_models.find(
    (model) => model.model_id === settings.selected_video_model_id,
  );
  // The fixes this job runs, in order. In Restore the last one is the model itself.
  $: imageFixes =
    !usingTemporalVideo && settings.task !== 'edit' && Boolean(settings.task)
      ? settings.fix_stages
      : [];
  // Engines without image_multi_fix run one fix at a time.
  $: fixPlanSupported =
    imageFixes.length <= 1 || snapshot.engine?.features.includes(MULTI_FIX_FEATURE) === true;
  $: usingTemporalVideo =
    settings.task === 'video' &&
    !restoring &&
    settings.selected_video_model_id !== 'frame_by_frame';
  $: noRestoreModel =
    restoring && imageFixes.length === 0 && settings.selected_model_id !== '__custom__';
  $: sizeUpscaler = restoring
    ? upscalerBeforeSameSize === '__custom__'
      ? undefined
      : upscalerBeforeSameSize
        ? snapshot.catalog.models.find((model) => model.model_id === upscalerBeforeSameSize)
        : choosePresetModel(
            snapshot,
            settings.task === 'video' ? 'video' : 'upscale',
            fixQuality(activeQuality),
            settings.content,
            settings.preset_pins,
            'upscale',
          )
    : selectedModel;
  $: sizeMaxScale = sizeUpscaler?.native_scale ?? 4;
  $: sizeResult =
    selectedMedia && selectedMedia.width > 0 && selectedMedia.height > 0
      ? restoring
        ? `${selectedMedia.width} × ${selectedMedia.height}`
        : `${selectedMedia.width * settings.output_scale} × ${selectedMedia.height * settings.output_scale}`
      : '';
  // Each video model is judged by its own engine: the worker must list it and the device must suit it.
  $: videoAvailable = videoAvailability(
    snapshot.catalog.video_models,
    snapshot.engine,
    snapshot.capabilities,
    settings.device_id,
  );
  $: temporalUnavailableReason =
    (selectedVideoModel && videoAvailable[selectedVideoModel.model_id]?.reason) ?? '';
  $: flashVideo = usingTemporalVideo && selectedVideoModel?.engine_kind === 'flashvsr';
  $: videoMemory = snapshot.runtime.video_memory;
  $: videoMemoryJob = snapshot.jobs.find((job) => job.id === videoMemory?.job_id);
  $: selectedMemory = videoMemoryJob?.media_id === selectedMedia?.id ? videoMemory : undefined;
  // Readings stay in the terms of the engine that produced them, whichever is selected now.
  $: videoMemoryEngine =
    snapshot.catalog.video_models.find((model) => model.model_id === videoMemoryJob?.model_id)
      ?.engine_kind ?? '';
  $: preservingHdr =
    settings.task === 'video' &&
    settings.video_hdr_mode === 'preserve' &&
    !usingTemporalVideo &&
    Boolean(selectedMedia?.hdr_format);
  $: hdrPreservationAvailable = supportsHdrPreservation(settings);
  $: hdrModelCompatible = !preservingHdr || hdrPreservationAvailable;
  $: licenseReviewModel =
    !usingTemporalVideo &&
    selectedModel &&
    ['realplksr_hfa2k_anime_x4', 'realplksr_nomoswebphoto_x4'].includes(selectedModel.model_id)
      ? selectedModel
      : undefined;
  $: activeDownloads =
    snapshot.runtime.downloads && snapshot.runtime.downloads.length > 0
      ? snapshot.runtime.downloads
      : snapshot.runtime.download_model_id
        ? [
            {
              model_id: snapshot.runtime.download_model_id,
              status: 'downloading' as const,
              progress: snapshot.runtime.download_progress,
            },
          ]
        : [];
  $: downloadMap = new Map<string, ModelDownloadStatus>(
    activeDownloads.map((item) => [item.model_id, item]),
  );
  const downloadClock = (_downloads: ModelDownloadStatus[], _second: number) =>
    performance.now() / 1000;
  $: activeDownload = activeDownloads[0]?.model_id ?? '';
  $: if (activeDownloads.length === 0) cancellingDownloads = false;
  $: downloadNow = downloadClock(activeDownloads, etaSecond);
  $: downloadRun.observe(activeDownloads, downloadNow);
  // Downloads own the status line while no job runs; when a job runs, an extra line names them.
  $: downloadStatus = downloadRun.line(
    downloadNow,
    (id) => modelDisplayName(snapshot, id),
    (id) => modelDownloadSize(snapshot, id),
  );
  $: downloadDetailText = downloadStatus
    ? [downloadStatus.detail, preparing && preparingThen ? `then ${preparingThen}` : '']
        .filter(Boolean)
        .join(' · ')
    : preparing
      ? [
          preparingTotalBytes > 0 ? formatBytes(preparingTotalBytes) : '',
          preparingThen ? `then ${preparingThen}` : '',
        ]
          .filter(Boolean)
          .join(' · ')
      : '';
  $: downloadDetails =
    downloadStatus || activeDownloads.length
      ? Object.fromEntries(
          activeDownloads.map((item) => [
            item.model_id,
            downloadRun.detail(item.model_id, downloadNow),
          ]),
        )
      : ({} as Record<string, string>);
  $: benchmarkRunning = snapshot.runtime.active_job_id.startsWith('benchmark-');
  $: benchmarkQuickModel = choosePresetModel(snapshot, 'upscale', 'quick', 'photo', {});
  $: benchmarkBestModel = choosePresetModel(snapshot, 'upscale', 'best', 'photo', {});
  $: benchmarkPresetModels = [
    benchmarkQuickModel ? { preset: 'quick', label: 'Quick', model: benchmarkQuickModel } : null,
    benchmarkBestModel && benchmarkBestModel.model_id !== benchmarkQuickModel?.model_id
      ? { preset: 'best', label: 'Best Quality', model: benchmarkBestModel }
      : null,
  ].filter(
    (
      entry,
    ): entry is {
      preset: string;
      label: string;
      model: NonNullable<typeof benchmarkQuickModel>;
    } => entry !== null,
  );
  $: benchmarkInstalledPresets = benchmarkPresetModels.filter((entry) => entry.model.installed);
  $: benchmarkMissingPresets = benchmarkPresetModels.filter((entry) => !entry.model.installed);
  $: benchmarkStartBlockReason =
    snapshot.runtime.worker !== 'ready'
      ? 'Waiting for the inference engine to become ready.'
      : snapshot.runtime.active_job_id && !benchmarkRunning
        ? 'Finish or cancel the current work before benchmarking.'
        : activeDownload
          ? 'Wait for the current model download to finish before benchmarking.'
          : !benchmarkPresetModels.length
            ? 'The benchmark preset models are unavailable in this catalog.'
            : !benchmarkInstalledPresets.length
              ? `Missing preset models: ${benchmarkMissingPresets
                  .map((entry) => `${entry.model.name} (${entry.label})`)
                  .join(' and ')}. Download at least one preset model before benchmarking.`
              : '';
  $: customModelNeedsOptIn =
    settings.selected_model_id === '__custom__' &&
    Boolean(settings.custom_model_path) &&
    !settings.custom_model_path.toLowerCase().endsWith('.safetensors');
  $: audioHasSource =
    settings.task !== 'video'
      ? false
      : settings.batch_mode
        ? runnableMedia.length === 0 || runnableMedia.some((media) => media.has_audio !== false)
        : selectedMedia?.has_audio !== false;
  $: audioDisabled = settings.task === 'video' && !audioHasSource;
  $: audioModels = snapshot.catalog.audio_models ?? [];
  $: selectedAudioModel = resolveAudioModel(audioModels, settings.video_audio_model);
  $: interpolationSupported =
    snapshot.engine?.features.includes(stepFeature('interpolate')) === true;
  $: hasVideoOnlyWork =
    settings.task === 'video' &&
    ((Boolean(settings.deflicker) && !preservingHdr) ||
      ((settings.video_interpolation ?? 'off') !== 'off' && interpolationSupported) ||
      (Boolean(settings.video_clean_audio) && !audioDisabled));
  // Steps a job adds by id are work of their own: Restore with nothing picked runs them alone.
  $: hasStepWork = activeStepStages(settings, usingTemporalVideo).length > 0;
  $: hasNothingToRun = noRestoreModel && !hasVideoOnlyWork && !hasStepWork;
  $: nothingToRunReason =
    settings.task === 'video'
      ? 'Nothing to run. Pick what to fix, or turn on De-flicker, Frame rate or Clean up audio.'
      : 'Nothing to run. Pick what to fix, or choose a larger size.';
  $: modelReady =
    settings.task === 'edit'
      ? editReady
      : noRestoreModel
        ? hasVideoOnlyWork || hasStepWork
        : usingTemporalVideo
          ? !temporalUnavailableReason && Boolean(selectedVideoModel?.installed)
          : settings.selected_model_id === '__custom__'
            ? Boolean(settings.custom_model_path) &&
              (!customModelNeedsOptIn || settings.allow_unsafe_pickle_model)
            : Boolean(selectedModel?.installed);
  $: interpolationStage = plan.find((stage) => stage.kind === 'interpolate');
  $: interpolationReady = !interpolationStage || interpolationStage.installed;
  $: audioStage = plan.find((stage) => stage.kind === 'audio_denoise');
  $: audioReady = !audioStage || audioStage.installed;
  $: stepsReady = stepStagesReady(plan);
  $: fixesReady =
    fixPlanSupported &&
    imageFixes.every((selection) => {
      const model = snapshot.catalog.models.find((entry) => entry.model_id === selection.model_id);
      return Boolean(model?.installed && fitsFix(model, selection.fix));
    }) &&
    (!restoring ||
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
  $: activeJob = snapshot.jobs.find((job) => job.id === snapshot.runtime.active_job_id);
  $: activeJobMediaId = activeJob?.media_id ?? '';
  $: videoEngineBusy = activeJob?.media_kind === 'video';
  $: activeJobIsVideo = activeJob ? activeJob.media_kind === 'video' : settings.task === 'video';
  $: activeJobSteps = snapshot.runtime.job_steps ?? [];
  $: observeJobSteps(snapshot.runtime.active_job_id, activeJobSteps, settings, activeJobIsVideo);
  $: viewerStepLabel =
    activeJobMediaId && selectedMedia?.id === activeJobMediaId && activeJobSteps.length >= 2
      ? skippedStepLabel || stepLabel(activeJobSteps, settings, activeJobIsVideo)
      : '';
  $: activeStepTitle =
    snapshot.runtime.active_job_id &&
    activeJobSteps.length >= 2 &&
    !snapshot.runtime.status_title.startsWith('Step ') &&
    ['Processing', 'Enhancing', ''].includes(snapshot.runtime.status_title)
      ? stepLabel(activeJobSteps, settings, activeJobIsVideo)
      : '';
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
      : noRestoreModel
        ? (plan.find((s) => s.interpolationModel)?.interpolationModel?.name ??
          plan.find((s) => s.audioModel)?.audioModel?.name ??
          (settings.task !== 'video'
            ? (plan.find((s) => s.role === 'step')?.label ?? 'Model')
            : settings.deflicker
              ? 'De-flicker'
              : 'Video'))
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
    !movingModels &&
    !cancelling &&
    $planState.status !== 'error' &&
    !($planState.status === 'loading' && $planState.first) &&
    snapshot.runtime.worker === 'ready' &&
    Boolean(settings.task) &&
    modelReady &&
    hdrModelCompatible &&
    fixesReady &&
    interpolationReady &&
    audioReady &&
    stepsReady &&
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
  $: if ((snapshot.runtime.active_job_id || activeDownloads.length > 0) && !etaTimer) {
    etaTimer = setInterval(() => (etaSecond += 1), 1000);
  } else if (!snapshot.runtime.active_job_id && activeDownloads.length === 0 && etaTimer) {
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
  // A stopped job's kept result is marked Unfinished wherever it shows. A photo's finished
  // tiles are measured in pixels of its saved file, which the decoded side reports.
  $: showingRow = pairShown
    ? resultRows.find((row) => row.job_id === choice.showing)
    : bulkHistories
        .find((entry) => entry.media_id === completedImageJob?.media_id)
        ?.results.find((row) => row.output_path === completedImageJob?.output_path);
  $: againstRow = referenceIsResult
    ? resultRows.find((row) => row.job_id === choice.against)
    : undefined;
  const paneMark = (
    row: ResultSummary | undefined,
    about: Parameters<typeof unfinishedMark>[1],
  ): PaneUnfinished | null => {
    const mark = unfinishedMark(row, about);
    return row && mark ? { id: row.job_id, ...mark } : null;
  };
  $: videoMark = completedVideoOutput
    ? paneMark(videoResult, { duration: selectedMedia?.duration_seconds, fps: selectedMedia?.fps })
    : null;
  $: paneView = {
    mediaId: selectedImage?.id ?? '',
    resultPreview,
    referenceIsResult,
    referencePreview: referenceIsResult && againstSide?.state === 'ready' ? againstSide.src : '',
    referenceLabel: referenceIsResult ? (againstSide?.label ?? 'Original') : 'Original',
    // Without a result list the newest result is shown on its own terms, not as the
    // chosen side, which then still reads Original.
    showingLabel: pairMode && history ? (showingSide?.label ?? '') : '',
    showingWidth: showingSide?.width ?? 0,
    showingHeight: showingSide?.height ?? 0,
    showingPreviewWidth: showingSide?.previewWidth ?? 0,
    showingPreviewHeight: showingSide?.previewHeight ?? 0,
    showingRegion: showingSide?.region ?? null,
    referenceWidth: referenceIsResult ? (againstSide?.width ?? 0) : 0,
    referenceHeight: referenceIsResult ? (againstSide?.height ?? 0) : 0,
    referencePreviewWidth: referenceIsResult ? (againstSide?.previewWidth ?? 0) : 0,
    referencePreviewHeight: referenceIsResult ? (againstSide?.previewHeight ?? 0) : 0,
    referenceRegion: referenceIsResult ? (againstSide?.region ?? null) : null,
    singleFrame,
    showingUnfinished:
      videoMark ??
      paneMark(showingRow, {
        decoded: pairShown ? showingSide : undefined,
        source: selectedImage,
      }),
    referenceUnfinished: paneMark(againstRow, { decoded: againstSide, source: selectedImage }),
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
          showingWidth: 0,
          showingHeight: 0,
          showingPreviewWidth: 0,
          showingPreviewHeight: 0,
          showingRegion: null,
          referenceWidth: 0,
          referenceHeight: 0,
          referencePreviewWidth: 0,
          referencePreviewHeight: 0,
          referenceRegion: null,
          singleFrame: null,
          showingUnfinished: null,
          referenceUnfinished: null,
        }
      : loadingTarget
        ? heldView
        : paneView;
  $: pairGeneration =
    selectedImage && snapshot.runtime.comparison_pair?.media_id === selectedImage.id
      ? snapshot.runtime.comparison_pair.generation
      : 0;
  $: originalRegion =
    selectedImage && snapshot.runtime.preview_original?.media_id === selectedImage.id
      ? (snapshot.runtime.preview_original.region ?? null)
      : null;
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
  $: videoHistory =
    selectedMedia?.kind === 'video'
      ? bulkHistories.find((entry) => entry.media_id === selectedMedia?.id)
      : undefined;
  $: videoChoice = effectiveChoice(
    videoHistory,
    selectedMedia ? pairChoices[selectedMedia.id] : undefined,
  );
  $: videoResult = videoHistory?.results.find(
    (row) => row.job_id === videoChoice.showing && row.available,
  );
  $: selectedOutputPath =
    selectedMedia?.kind === 'video'
      ? selectedMedia.id === activeJobMediaId && !videoChoice.pinned
        ? ''
        : (videoResult?.output_path ?? '')
      : pairShown
        ? (resultRows.find((row) => row.job_id === choice.showing && row.available)?.output_path ??
          '')
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
    selectedMedia?.kind === 'video' && (selectedMedia.id !== activeJobMediaId || videoChoice.pinned)
      ? (videoResult?.output_path ?? '')
      : '';
  $: videoResultId = completedVideoOutput ? videoChoice.showing : '';
  $: outputDimensions = selectedMedia
    ? settings.task === 'edit'
      ? `${editDimensions(selectedMedia.width, selectedMedia.height, editSizeFor(settings.edit_max_dimension, editSizeAllowed, editExpertState)).join(' × ')} · edited`
      : restoring
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
    ? choosePresetModel(
        snapshot,
        settings.task,
        'quick',
        settings.content,
        settings.preset_pins,
        videoTask,
      )
    : undefined;
  $: bestModel = settings.task
    ? choosePresetModel(
        snapshot,
        settings.task,
        'best',
        settings.content,
        settings.preset_pins,
        videoTask,
      )
    : undefined;
  $: quickDownload = presetDownload('quick', snapshot, settings);
  $: bestDownload = presetDownload('best', snapshot, settings);
  // Settings saved before the model library exist without a quality; infer it
  // from the selection so Quick/Best light up correctly after an update.
  $: activeQuality = ((): Quality => {
    if (usingTemporalVideo) return 'custom';
    if (settings.quality) return settings.quality;
    if (settings.selected_model_id === '__custom__') return 'custom';
    if (settings.fix_stages.length) return 'custom';
    if (bestModel && settings.selected_model_id === bestModel.model_id) return 'best';
    if (quickModel && settings.selected_model_id === quickModel.model_id) return 'quick';
    return 'custom';
  })();
  const planClient = createPlanClient(fetchPlan);
  const planState = planClient.state;
  let planErrorRecorded = false;
  $: if (!booting) {
    planClient.request(snapshot, settings, {
      face_enabled: false,
      has_audio: audioHasSource,
    });
  }
  $: plan = $planState.stages;
  $: if ($planState.status === 'error' && !planErrorRecorded) {
    planErrorRecorded = true;
    void api.recordFrontendError(`plan_job: ${$planState.message}`, '').catch(() => {});
  }
  $: downloads = pendingDownloads(plan, keptDownloadBytes);
  const stageModelId = (stage: (typeof plan)[number]): string =>
    (
      stage.model ??
      stage.videoModel ??
      stage.interpolationModel ??
      stage.audioModel ??
      stage.editModel
    )?.model_id ?? '';
  $: anyDownloadBlockingStart = Boolean(activeDownload) && downloads.stages.length > 0;
  $: downloadFailureLine = (() => {
    const failures = rememberedFailureIds
      .map((id) => downloadFailures[id])
      .filter((entry): entry is DownloadFailure => Boolean(entry));
    if (!failures.length) return null;
    const first = failures[0];
    const copy = downloadFailureText(first, true);
    const moreCount = failures.length - 1;
    return {
      modelId: first.modelId,
      title: copy.title,
      detail: moreCount > 0 ? `${copy.detail} ${moreCount} more failed.` : copy.detail,
      tooltip: failures
        .map((entry) => entry.message)
        .filter(Boolean)
        .join('\n'),
      inPlan: downloads.stages.some((stage) => stageModelId(stage) === first.modelId),
    };
  })();
  $: planNeedsFile = plan.some((stage) => stage.needsFile);
  $: planMissing = plan.some((stage) => stage.missing);
  $: prepareReady =
    !snapshot.runtime.active_job_id &&
    !activeDownload &&
    !preparing &&
    !startingJob &&
    !movingModels &&
    !cancelling &&
    $planState.status !== 'error' &&
    !($planState.status === 'loading' && $planState.first) &&
    !hasNothingToRun &&
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
    (['Ready', 'Model ready', 'Download paused', 'Download stopped', 'Cancelled'].includes(
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
              : colourHardwareLabel(
                  interpolationHardwareLabel(
                    engineLabel(
                      settings,
                      snapshot.capabilities,
                      selectedModel?.architecture,
                      activeDevice ? deviceLabel(activeDevice) : 'detecting hardware',
                    ),
                    settings.device_id,
                    settings.task === 'video' && (settings.video_interpolation ?? 'off') !== 'off',
                  ),
                  settings.device_id,
                  selectedModel?.architecture !== 'DDColor' &&
                    imageFixes.some((selection) => selection.fix === 'colour'),
                )
        }`
      : '';
  // The last failure also stands above Advanced, next to the setting that can fix it. It stays
  // until the next job starts, also while the engine restarts after it (a driver reset) and
  // the status line moves on to "Starting engine" and "Ready".
  let lastFailure = '';
  let lastFailureJobId = '';
  $: if (snapshot.runtime.active_job_id) {
    lastFailure = '';
    lastFailureJobId = '';
  } else if (snapshot.runtime.status_title === 'Could not finish') {
    lastFailure = snapshot.runtime.status_detail;
  }
  let failureNote: HTMLElement | undefined;
  $: failedJobIdForRetry =
    lastFailureJobId || snapshot.jobs.find((job) => job.status === 'failed')?.id || '';
  // A GPU or NPU that failed (a rejected check, a driver reset) can be retried once on the CPU;
  // editing needs a GPU, so it gets no such button.
  $: acceleratorFailure =
    settings.task !== 'edit' &&
    settings.device_id !== 'cpu' &&
    Boolean(failedJobIdForRetry) &&
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
    !downloadFailureLine &&
    snapshot.runtime.status_title !== 'Warning'
      ? cpuHint(snapshot.capabilities, detectPlatform())
      : null;
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
    $planState.status === 'error'
      ? 'Could not work out the plan. Try again.'
      : hasNothingToRun
        ? nothingToRunReason
        : anyDownloadBlockingStart
          ? 'Wait for the download to finish.'
          : snapshot.media.length < 2
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
  $: oneTitle =
    $planState.status === 'error'
      ? 'Could not work out the plan. Try again.'
      : anyDownloadBlockingStart
        ? 'Wait for the download to finish.'
        : oneUnready
          ? preparingNote(oneUnready)
          : hasNothingToRun
            ? nothingToRunReason
            : mismatchNote ||
              (canPrepareOne ? `Downloads ${formatBytes(downloads.bytes)} first.` : '');
  $: allTitle =
    $planState.status === 'error'
      ? 'Could not work out the plan. Try again.'
      : anyDownloadBlockingStart
        ? 'Wait for the download to finish.'
        : allUnready
          ? preparingNote(allUnready)
          : hasNothingToRun
            ? nothingToRunReason
            : incompatibleNote;
  // What the primary button says it does to all: "Upscale all 52", "Start all 5 videos".
  $: allPhrase = `${
    startVerb === 'start video' ? 'start' : startVerb
  } all ${queueableMedia.length}${settings.task === 'video' ? ' videos' : ''}`;
  $: lockReason = cancelling
    ? 'Stopping your queue… Controls unlock when processing has stopped and temporary output is removed.'
    : 'Finish or cancel the run to change settings.';
  $: startBlockReason = snapshot.runtime.active_job_id
    ? 'Wait for the current job to finish.'
    : movingModels
      ? 'Wait for models to finish moving.'
      : anyDownloadBlockingStart
        ? 'Wait for the download to finish.'
        : $planState.status === 'error'
          ? 'Could not work out the plan. Try again.'
          : $planState.status === 'loading' && $planState.first
            ? 'Working out the plan…'
            : snapshot.runtime.worker !== 'ready'
              ? 'The processing engine is not ready yet.'
              : !settings.task
                ? 'Choose a task before starting.'
                : !queueSelection.length
                  ? 'Select a compatible item before starting.'
                  : unreadyMedia
                    ? scopeHint
                    : hasNothingToRun
                      ? nothingToRunReason
                      : editBlockReason ||
                        (!modelReady || !fixesReady || !interpolationReady || !stepsReady
                          ? 'Choose or download the required models before starting.'
                          : 'The current settings are not ready to start.');

  let themeForRefresh: (
    settings: Pick<
      UiSettings,
      'appearance' | 'colour_blind_palette' | 'contrast' | 'viewer_background' | 'accent'
    >,
  ) => void = () => {};

  onMount(() => {
    const theme = createThemeController(undefined, (value) => {
      if (api.isTauri()) {
        void import('@tauri-apps/api/window')
          .then(({ getCurrentWindow }) => getCurrentWindow().setTheme(value))
          .catch((error) => console.error('Could not set window theme', error));
      }
    });
    const accent = createAccentController();
    themeForRefresh = (nextSettings) => {
      theme.apply(nextSettings);
      accent.apply(nextSettings);
    };
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
        normalizeLoadedSettings();
        setupAppearance = snapshot.settings.appearance ?? 'dark';
        setupColourBlind = snapshot.settings.colour_blind_palette ?? false;
        setupContrast = snapshot.settings.contrast ?? 'system';
        setupMenuBar = snapshot.settings.show_in_menu_bar ?? true;
        setupSound = snapshot.settings.completion_sound ?? false;
        setupCheckAtLaunch = snapshot.settings.check_updates_at_launch ?? true;
        setupAnonymousCount = snapshot.settings.anonymous_update_count ?? true;
        setupCrashReports = snapshot.settings.crash_reports ?? true;
        if (snapshot.settings.crash_reports) {
          await refreshCrashReports();
          crashNotice = crashReports.length > 0;
        }
        theme.apply(snapshot.settings);
        accent.apply(snapshot.settings);
        document.documentElement.classList.remove('theme-loading');
        await tick();
        if (disposed) return;
        ensureTaskMatchesSelection();
        chooseInitialModel();
        if (
          settings.task === 'video' &&
          settings.video_hdr_mode === 'preserve' &&
          settings.selected_video_model_id === 'frame_by_frame' &&
          !supportsHdrPreservation(settings)
        ) {
          updateSettings({ video_hdr_mode: 'tone_map' });
        }
        booting = false;
        await subscribe(() => api.listenForWorker(handleWorkerMessage));
        if (disposed) return;
        const checkUpdatesAtLaunch =
          (snapshot.settings.privacy_choice_made ?? true) &&
          (snapshot.settings.check_updates_at_launch ?? true);
        await Promise.all([
          subscribe(() => api.listenForStateChange(scheduleRefresh)),
          subscribe(() => api.listenForNativeMenu((action) => void handleNativeMenuAction(action))),
          subscribe(() => api.listenForLaunchIntent(() => void consumeLaunchIntents())),
          subscribe(() => api.listenForModelMove((progress) => (modelMoveProgress = progress))),
          subscribe(() => api.listenForEngineStatus(handleEngineStatus)),
          subscribe(() =>
            startUpdateWatcher(checkUpdatesAtLaunch, () => {
              if (
                !disposed &&
                snapshot.engines?.supported &&
                !snapshot.engines.managed_by_store &&
                !snapshot.engines.catalog.length
              ) {
                void refresh();
              }
            }),
          ),
          subscribe(() =>
            api.listenForFileDrops({
              hover: (active) => (filesDragging = active),
              drop: (paths) => void addDroppedMedia(paths),
            }),
          ),
        ]);
        if (!disposed) await consumeLaunchIntents();
      } catch (error) {
        document.documentElement.classList.remove('theme-loading');
        if (!disposed) showModal('Could not start LocalSR', String(error));
      } finally {
        booting = false;
      }
    })();

    return () => {
      disposed = true;
      theme.dispose();
      accent.dispose();
      if (stateRefresh) clearTimeout(stateRefresh);
      if (runtimePulseTimer) clearTimeout(runtimePulseTimer);
      if (skippedStepTimer) clearTimeout(skippedStepTimer);
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
      // The window counts a photo's finished tiles from its progress; the host's snapshot
      // has no such count to replace it with.
      if (
        latest.runtime.active_job_id &&
        latest.runtime.active_job_id === snapshot.runtime.active_job_id
      ) {
        latest.runtime.tiles_completed ??= snapshot.runtime.tiles_completed;
        latest.runtime.tiles_total ??= snapshot.runtime.tiles_total;
        latest.runtime.job_steps ??= snapshot.runtime.job_steps;
      }
      snapshot = latest;
      if (!latest.settings.crash_reports) {
        crashNotice = false;
      }
      if (modalKind === 'performance') {
        void refreshCrashReports();
      }
      themeForRefresh(
        latest.settings.privacy_choice_made === false
          ? {
              ...latest.settings,
              appearance: setupAppearance,
              colour_blind_palette: setupColourBlind,
              contrast: setupContrast,
            }
          : latest.settings,
      );
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
      if (selectedMedia?.kind === 'video' && videoChoice.pinned) return;
      queueProgressiveTile(message);
      return;
    }
    if (message.type === 'live_preview_frame') {
      const jobId = String(message.data.job_id ?? '');
      if (selectedMedia?.kind === 'video' && videoChoice.pinned) return;
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
    if (
      (message.type === 'job_completed' || message.type === 'video_job_completed') &&
      message.data.partial
    )
      keptJobIds.add(messageJob);
    snapshot = applyWorkerEnvelope(snapshot, message);
    // A pinned pair keeps its zoom, pan and divider while other runs start and finish.
    const pinned = Boolean(selectedMedia && pairChoices[selectedMedia.id]?.pinned);
    if (message.type === 'job_started') {
      rememberedFailureIds = [];
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
      if (message.type === 'job_failed') lastFailureJobId = messageJob;
    }
    if (message.type === 'media_info') {
      if (
        String(message.data.media_path ?? '') === selectedMedia?.path &&
        (message.data.preview_url || message.data.jpeg_base64)
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
    if (
      message.type === 'download-queued' ||
      message.type === 'download-started' ||
      message.type === 'download-completed' ||
      message.type === 'download-cancelled'
    ) {
      const id = String(message.data.model_id ?? '');
      if (message.type === 'download-queued' || message.type === 'download-started') {
        rememberedFailureIds = [];
      } else if (message.type === 'download-completed' && id) {
        downloadRun.completed(id);
      } else if (message.type === 'download-cancelled' && id) {
        downloadRun.dropped(id);
      }
      if (id && (message.type === 'download-completed' || message.type === 'download-cancelled')) {
        keptDownloadBytes = withKeptBytes(keptDownloadBytes, id, message.data.downloaded_bytes);
      }
      if (id && id in downloadErrors) {
        const nextErrors = { ...downloadErrors };
        delete nextErrors[id];
        downloadErrors = nextErrors;
      }
      if (id && id in downloadFailures) {
        const nextFailures = { ...downloadFailures };
        delete nextFailures[id];
        downloadFailures = nextFailures;
      }
      if (message.type === 'download-completed') scheduleRefresh();
    } else if (message.type === 'download-failed') {
      const id = String(message.data.model_id ?? '');
      if (id) {
        downloadRun.dropped(id);
        keptDownloadBytes = withKeptBytes(keptDownloadBytes, id, message.data.downloaded_bytes);
        const errorMessage = String(message.data.error_message ?? 'Download failed.');
        downloadErrors = {
          ...downloadErrors,
          [id]: errorMessage,
        };
        downloadFailures = {
          ...downloadFailures,
          [id]: {
            modelId: id,
            name: modelDisplayName(snapshot, id),
            reason: parseFailureReason(message.data.reason),
            done: Number(message.data.downloaded_bytes ?? 0) || 0,
            total: Number(message.data.total_bytes ?? 0) || modelDownloadSize(snapshot, id),
            message: errorMessage,
          },
        };
        if (!rememberedFailureIds.includes(id)) {
          rememberedFailureIds = [...rememberedFailureIds, id];
        }
      }
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
    if (snapshot.settings.edit_model_id === '__custom__' && snapshot.edit.custom_model) return;
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

  /** Migrate loaded Upscale 1×, no-chip video Restore, and Faces settings. */
  function normalizeLoadedSettings(): void {
    const current = snapshot.settings;
    const byId = (id: string): CatalogModel | undefined =>
      snapshot.catalog.models.find((model) => model.model_id === id);
    const quality: 'quick' | 'best' =
      current.quality === 'quick' || current.quality === 'best' ? current.quality : 'best';
    const patch: Partial<UiSettings> = {};
    let changed = false;

    const cleanFixes = current.fixes.filter((fix) => fix !== 'faces');
    if (cleanFixes.length !== current.fixes.length) {
      patch.fixes = cleanFixes;
      changed = true;
    }
    if (current.enable_face_model) {
      patch.enable_face_model = false;
      changed = true;
    }

    const videoUpscale1x =
      current.task === 'video' &&
      (current.video_task || 'upscale') !== 'denoise' &&
      current.output_scale === 1 &&
      (current.selected_video_model_id === 'frame_by_frame' ||
        !(current.video_target_resolution ?? 0));
    const photoUpscale1x = current.task === 'upscale' && current.output_scale === 1;

    if (photoUpscale1x) {
      patch.task = 'denoise';
      patch.output_scale = 1;
      patch.enable_face_model = false;
      const stages = current.fix_stages.map((selection) => {
        const own = byId(selection.model_id);
        if (own && fitsFix(own, selection.fix)) return selection;
        return {
          ...selection,
          model_id:
            chooseFixModel(snapshot, selection.fix, quality, current.preset_pins)?.model_id ?? '',
        };
      });
      patch.fix_stages = stages;
      patch.fixes = stages.map((selection) => selection.fix);
      const last = stages.at(-1);
      if (last) {
        patch.selected_model_id = last.model_id;
        const lastModel = byId(last.model_id);
        if (lastModel) patch.halo = lastModel.recommended_halo;
      } else {
        const currentModel = byId(current.selected_model_id);
        if (!currentModel || currentModel.native_scale !== 1) {
          const fallback = choosePresetModel(
            snapshot,
            'denoise',
            quality,
            current.content,
            current.preset_pins,
          );
          if (fallback) {
            patch.selected_model_id = fallback.model_id;
            patch.halo = fallback.recommended_halo;
          }
        }
      }
      changed = true;
    } else if (videoUpscale1x) {
      patch.video_task = 'denoise';
      patch.selected_video_model_id = 'frame_by_frame';
      patch.output_scale = 1;
      patch.enable_face_model = false;
      const stages = current.fix_stages.map((selection) => {
        const own = byId(selection.model_id);
        if (own && fitsFix(own, selection.fix)) return selection;
        return {
          ...selection,
          model_id:
            chooseFixModel(snapshot, selection.fix, quality, current.preset_pins)?.model_id ?? '',
        };
      });
      patch.fix_stages = stages;
      patch.fixes = stages.map((selection) => selection.fix);
      const last = stages.at(-1);
      patch.selected_model_id = last ? last.model_id : '';
      if (last) {
        const lastModel = byId(last.model_id);
        if (lastModel) patch.halo = lastModel.recommended_halo;
      }
      changed = true;
    } else if (
      current.task === 'denoise' &&
      current.fix_stages.length === 0 &&
      current.selected_model_id !== '__custom__' &&
      current.selected_model_id
    ) {
      const picked = byId(current.selected_model_id);
      const fit = picked ? DEFAULT_FIX_ORDER.find((fix) => fitsFix(picked, fix)) : undefined;
      if (fit && picked) {
        patch.fix_stages = [{ fix: fit, model_id: picked.model_id }];
        patch.fixes = [fit];
        patch.halo = picked.recommended_halo;
      } else {
        patch.selected_model_id = '';
      }
      changed = true;
    } else if (current.task === 'video' && current.video_task === 'denoise') {
      const videoStages = current.fix_stages;
      const hasVideoOption =
        Boolean(current.deflicker) ||
        (current.video_interpolation ?? 'off') !== 'off' ||
        Boolean(current.video_clean_audio);
      if (
        videoStages.length === 0 &&
        current.selected_model_id !== '__custom__' &&
        (current.selected_model_id || !hasVideoOption)
      ) {
        const picked = byId(current.selected_model_id);
        const fit = picked ? DEFAULT_FIX_ORDER.find((fix) => fitsFix(picked, fix)) : undefined;
        if (fit && picked) {
          patch.fix_stages = [{ fix: fit, model_id: picked.model_id }];
          patch.fixes = [fit];
          patch.halo = picked.recommended_halo;
        } else {
          const noiseModel = chooseFixModel(snapshot, 'noise', quality, current.preset_pins);
          patch.fix_stages = [{ fix: 'noise', model_id: noiseModel?.model_id ?? '' }];
          patch.fixes = ['noise'];
          patch.selected_model_id = noiseModel?.model_id ?? '';
          if (noiseModel) patch.halo = noiseModel.recommended_halo;
        }
        changed = true;
      }
    }

    if (changed) updateSettings(patch);
  }

  function chooseInitialModel(): void {
    const task = snapshot.settings.task;
    if (task === 'edit') {
      ensureEditModel();
      return;
    }
    if (!task || snapshot.settings.selected_model_id) return;
    if (isRestoreTask(snapshot.settings)) return;
    resolveRecipe({ quality: snapshot.settings.quality || 'quick' }, { keepVideoEngine: true });
  }

  /**
   * Resolve the plan for the active recipe from the catalog. Reads
   * `snapshot.settings` (updated synchronously by `updateSettings`) so callers
   * can chain it after a task or content change within the same tick.
   */
  function resolveRecipe(
    patch: Partial<Pick<UiSettings, 'quality' | 'content' | 'fixes' | 'fix_stages' | 'video_task'>>,
    options: { keepVideoEngine?: boolean; keepScale?: boolean } = {},
  ): void {
    const current = { ...snapshot.settings, ...patch };
    const task = current.task;
    if (!task || task === 'edit') return;
    const quality: 'quick' | 'best' =
      current.quality === 'quick' || current.quality === 'best' ? current.quality : 'best';
    const byId = (id: string): CatalogModel | undefined =>
      snapshot.catalog.models.find((model) => model.model_id === id);
    const keepPrimary = current.quality === 'custom';
    const currentVideoTask = effectiveVideoTask(current);
    const isRestoring = isRestoreTask(current);
    const temporal =
      task === 'video' &&
      !isRestoring &&
      current.selected_video_model_id !== 'frame_by_frame' &&
      Boolean(options.keepVideoEngine);
    // Quick and Best choose every fix model again; a job set up by hand keeps each one
    // that still fixes its problem.
    const chosen = new Set<Exclude<FixKind, 'faces'>>();
    const fix_stages = (temporal ? [] : current.fix_stages).map((selection, index, all) => {
      const own = byId(selection.model_id);
      if (keepPrimary && own && fitsFix(own, selection.fix)) return selection;
      // A Restore model picked by hand becomes the new last fix when it can do that job.
      const picked = byId(current.selected_model_id);
      if (
        isRestoring &&
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
    const patchSettings: Partial<UiSettings> = {
      quality: current.quality || quality,
      content: current.content,
      ...(temporal
        ? {}
        : {
            fix_stages,
            fixes: fix_stages.map((selection) => selection.fix),
          }),
    };
    if (isRestoring) {
      // Same size's model is its last fix, or your own file when nothing is ticked.
      const final = fix_stages.at(-1);
      const kept =
        keepPrimary &&
        (final
          ? final.model_id === current.selected_model_id
          : current.selected_model_id === '__custom__');
      if (!kept) {
        if (final) {
          const model = byId(final.model_id);
          patchSettings.selected_model_id = final.model_id;
          if (model) patchSettings.halo = model.recommended_halo;
          // A pick by hand that cannot fix the problem gives way to the preset's model.
          if (keepPrimary && chosen.has(final.fix)) patchSettings.quality = quality;
        } else {
          patchSettings.selected_model_id = '';
        }
      }
      if (task === 'video') patchSettings.selected_video_model_id = 'frame_by_frame';
      patchSettings.output_scale = 1;
      patchSettings.enable_face_model = false;
    } else {
      const primary = keepPrimary
        ? undefined
        : choosePresetModel(
            snapshot,
            task,
            quality,
            current.content,
            current.preset_pins,
            currentVideoTask,
          );
      if (primary) {
        patchSettings.selected_model_id = primary.model_id;
        // Quick and Best are frame-by-frame recipes; a saved SeedVR2 or FlashVSR
        // choice survives only the initial resolution and task changes.
        if (!options.keepVideoEngine) patchSettings.selected_video_model_id = 'frame_by_frame';
        const minScale = 2;
        if (
          !options.keepScale ||
          current.output_scale < minScale ||
          current.output_scale > primary.native_scale
        ) {
          patchSettings.output_scale = primary.native_scale;
        }
        patchSettings.halo = primary.recommended_halo;
        patchSettings.safe_memory = quality === 'best' ? current.safe_memory : false;
      }
      patchSettings.enable_face_model = false;
    }
    updateSettings(patchSettings);
    termsAccepted = {};
  }

  function setContent(content: UiSettings['content']): void {
    if (settings.content === content) return;
    resolveRecipe({ content, quality: activeQuality }, { keepScale: true });
  }

  function toggleFix(fix: FixKind): void {
    if (fix === 'faces') return;
    // Noise, JPEG and blur each run as their own fix before the main model.
    resolveRecipe(
      { fix_stages: toggleFixStage(settings.fix_stages, fix), quality: activeQuality },
      { keepScale: true },
    );
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
      fixes: fix_stages.map((selection) => selection.fix),
      ...(restoring && final.fix !== settings.fix_stages.at(-1)?.fix
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
    select?: string,
  ): void {
    // Restore's model is the fix itself, so with a chip on it is chosen like one.
    libraryContext =
      slot === 'fix' || ((slot === 'primary' || slot === 'try') && restoring && fix)
        ? { slot, fix, select }
        : { slot, select };
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
      ...(restoring && fix_stages.at(-1)?.fix === fix
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
      quality: restoring ? fixQuality(activeQuality) : activeQuality,
    });
    libraryOpen = false;
  }

  async function removeModel(model: CatalogModel | CatalogVideoModel | EditModel): Promise<void> {
    try {
      await api.removeModel(model.model_id);
      await refresh();
    } catch (error) {
      showModal('Could not remove the model', String(error));
    }
  }

  const FAILURE_REASONS = new Set<DownloadFailure['reason']>([
    'offline',
    'server',
    'damaged',
    'disk_full',
    'folder',
    'other',
  ]);

  function parseFailureReason(raw: unknown): DownloadFailure['reason'] {
    return typeof raw === 'string' && FAILURE_REASONS.has(raw as DownloadFailure['reason'])
      ? (raw as DownloadFailure['reason'])
      : 'other';
  }

  function errorMessageText(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  function recordDownloadFailure(modelId: string, error: unknown): void {
    const message = errorMessageText(error);
    if (!(modelId in downloadFailures)) {
      downloadFailures = {
        ...downloadFailures,
        [modelId]: {
          modelId,
          name: modelDisplayName(snapshot, modelId),
          reason: 'other',
          done: 0,
          total: modelDownloadSize(snapshot, modelId),
          message,
        },
      };
    }
    if (!rememberedFailureIds.includes(modelId)) {
      rememberedFailureIds = [...rememberedFailureIds, modelId];
    }
  }

  async function downloadModels(
    models: (CatalogModel | CatalogVideoModel | EditModel | undefined)[],
    thenText = startVerb,
  ): Promise<boolean> {
    const targets = models.filter((model): model is CatalogModel | CatalogVideoModel | EditModel =>
      Boolean(model && !model.installed && model.automated_download_allowed),
    );
    if (!targets.length) return true;
    preparing = true;
    preparingCancelled = false;
    preparingThen = thenText;
    preparingTotalBytes = targets.reduce((sum, model) => {
      const size = 'size_bytes' in model ? model.size_bytes : model.total_size_bytes;
      return sum + (size || 0);
    }, 0);
    preparingIds = targets.map((model) => model.model_id);
    preparingPendingIds = [...preparingIds];
    rememberedFailureIds = [];
    let results: PromiseSettledResult<void>[] = [];
    try {
      results = await Promise.allSettled(
        targets.map(async (model) => {
          dismissDownloadError(model.model_id);
          cancelledDownloads.delete(model.model_id);
          try {
            const accepted = Boolean(termsAccepted[model.model_id]);
            downloadAcceptedTerms[model.model_id] = accepted;
            await api.downloadModel(model.model_id, accepted);
          } catch (error) {
            const message = errorMessageText(error);
            const wasCancelled =
              preparingCancelled ||
              cancelledDownloads.delete(model.model_id) ||
              message.includes('download cancelled');
            if (!wasCancelled) {
              downloadErrors = { ...downloadErrors, [model.model_id]: message };
              recordDownloadFailure(model.model_id, error);
            }
            throw error;
          } finally {
            preparingPendingIds = preparingPendingIds.filter((id) => id !== model.model_id);
          }
        }),
      );
    } finally {
      preparing = false;
      preparingThen = '';
      preparingTotalBytes = 0;
      preparingIds = [];
      preparingPendingIds = [];
    }
    const ok = !preparingCancelled && results.every((result) => result.status === 'fulfilled');
    await refresh();
    return ok;
  }

  /** Stop the downloads Start began; nothing starts afterwards. */
  async function cancelPreparation(): Promise<void> {
    preparingCancelled = true;
    const remaining = new Set(
      preparingIds.length ? preparingIds : activeDownloads.map((item) => item.model_id),
    );
    for (const id of remaining) cancelledDownloads.add(id);
    // The host registers a download a moment after Start asks for it.
    for (let attempt = 0; attempt < 30 && preparing && remaining.size > 0; attempt += 1) {
      await Promise.all(
        [...remaining].map(async (modelId) => {
          if (!preparingPendingIds.includes(modelId) && !downloadMap.has(modelId)) {
            remaining.delete(modelId);
            return;
          }
          try {
            await api.cancelDownload(modelId);
            remaining.delete(modelId);
          } catch {
            // Not registered yet; try again shortly.
          }
        }),
      );
      if (remaining.size === 0 || !preparing) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }

  async function prepareAndStart(thenText?: string): Promise<void> {
    const settled = await planClient.settled();
    if (settled.status === 'error') return;
    await tick();
    if (!canPrepare) return;
    const targets = downloads.stages.map(
      (stage) =>
        stage.model ??
        stage.videoModel ??
        stage.editModel ??
        stage.interpolationModel ??
        stage.audioModel,
    );
    const label = thenText ?? (scopeChoice && settings.batch_mode ? allPhrase : startVerb);
    if (!(await downloadModels(targets, label))) return;
    await tick();
    if (canStart) await start();
  }

  /** The status bar's two start buttons. The scope used is kept in the settings, so File > Start
   *  and automatic starts repeat it. */
  async function startScope(batch: boolean): Promise<void> {
    const settled = await planClient.settled();
    if (settled.status === 'error') return;
    if (settings.batch_mode !== batch) updateSettings({ batch_mode: batch });
    await tick();
    if (settings.batch_mode !== batch) return;
    const settledAfter = await planClient.settled();
    if (settledAfter.status === 'error') return;
    await tick();
    if (canPrepare) await prepareAndStart(batch ? allPhrase : startVerb);
    else await start();
  }

  /** Re-queue only the failed job on CPU, leaving the saved device preference unchanged. */
  async function retryOnCpu(): Promise<void> {
    const jobId = failedJobIdForRetry;
    if (!jobId) return;
    await attempt('Could not retry on CPU', async () => {
      await api.retryJobOnCpu(jobId);
      lastFailure = '';
      lastFailureJobId = '';
      if (api.isTauri()) await refresh();
    });
  }

  function supportsHdrPreservation(candidate: UiSettings): boolean {
    return catalogSupportsHdrPreservation(candidate, snapshot.catalog);
  }

  function updateSettings(patch: Partial<UiSettings>): void {
    if (settingsLocked) return;
    const next = { ...snapshot.settings, ...patch };
    // Apply the model/output contract for selectors, saved recipes and launch presets.
    if (
      next.task === 'video' &&
      next.video_hdr_mode === 'preserve' &&
      next.selected_video_model_id === 'frame_by_frame' &&
      patch.selected_video_model_id === undefined
    ) {
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
   * App-wide preferences are saved by the host on its own and may change during a job.
   * The window shows the host's value afterwards, also when saving failed.
   */
  /** Advanced: hardware and low priority hold for this session, the rest is saved. */
  function chooseInAdvanced(patch: Partial<UiSettings>): void {
    const rest: Partial<UiSettings> = { ...patch };
    let next = { ...sessionChoice };
    for (const key of sessionKeys) {
      if (!(key in patch)) continue;
      delete rest[key];
      if (patch[key] === snapshot.settings[key]) {
        const { [key]: _dropped, ...kept } = next;
        next = kept;
      } else {
        next = { ...next, [key]: patch[key] };
      }
    }
    if ('low_priority' in patch) {
      void api
        .setRunLowPriority(Boolean(patch.low_priority))
        .catch((error) => showModal('Could not change the priority', String(error)));
    }
    sessionChoice = next;
    if (Object.keys(rest).length) updateSettings(rest);
  }

  function useDefaultsForSession(): void {
    if ('low_priority' in sessionChoice) {
      void api
        .setRunLowPriority(Boolean(snapshot.settings.low_priority))
        .catch((error) => showModal('Could not change the priority', String(error)));
    }
    sessionChoice = {};
  }

  /** Settings: a new default also replaces the session choice for that setting. */
  function updateDefaults(patch: Partial<UiSettings>): void {
    let next = { ...sessionChoice };
    for (const key of sessionKeys) {
      if (key in patch) {
        const { [key]: _dropped, ...kept } = next;
        next = kept;
      }
    }
    sessionChoice = next;
    updateSettings(patch);
  }

  async function setPreference(
    name: api.PreferenceName,
    value:
      | boolean
      | UiSettings['appearance']
      | UiSettings['interface_scale']
      | NonNullable<UiSettings['contrast']>
      | NonNullable<UiSettings['viewer_background']>
      | NonNullable<UiSettings['accent']>
      | NonNullable<UiSettings['stopped_job_result']>,
  ): Promise<void> {
    if (!api.isTauri()) {
      snapshot = { ...snapshot, settings: { ...snapshot.settings, [name]: value } };
      if (
        name === 'appearance' ||
        name === 'colour_blind_palette' ||
        name === 'contrast' ||
        name === 'viewer_background' ||
        name === 'accent'
      ) {
        themeForRefresh(snapshot.settings);
      }
      return;
    }
    try {
      await api.setPreference(name, value);
      if (name === 'low_priority') {
        const { low_priority: _ended, ...kept } = sessionChoice;
        sessionChoice = kept;
      }
      snapshot = { ...snapshot, settings: { ...snapshot.settings, [name]: value } };
      if (
        name === 'appearance' ||
        name === 'colour_blind_palette' ||
        name === 'contrast' ||
        name === 'viewer_background' ||
        name === 'accent'
      ) {
        themeForRefresh(snapshot.settings);
      }
    } catch (error) {
      showModal('Could not save settings', String(error));
    } finally {
      scheduleRefresh();
      if (name === 'crash_reports') void refreshCrashReports();
    }
  }

  function setSize(scale: 1 | 2 | 3 | 4): void {
    if (scale === 1) {
      if (restoring) return;
      const remembered = activeQuality === 'custom' ? settings.selected_model_id : '';
      setTask(settings.task === 'video' ? 'video' : 'denoise', 'denoise');
      upscalerBeforeSameSize = remembered;
      return;
    }
    if (restoring) {
      setTask(settings.task === 'video' ? 'video' : 'upscale', 'upscale', {
        scale,
        modelId: upscalerBeforeSameSize,
      });
      return;
    }
    if (settings.output_scale !== scale) updateSettings({ output_scale: scale });
  }

  function radioGroupKeydown(event: KeyboardEvent): void {
    const step =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0;
    if (!step && event.key !== 'Home' && event.key !== 'End') return;
    const button = event.currentTarget as HTMLButtonElement;
    const group = button.closest('[role="radiogroup"]');
    if (!group) return;
    const buttons = Array.from(
      group.querySelectorAll<HTMLButtonElement>('button[role="radio"]:not(:disabled)'),
    );
    if (!buttons.length) return;
    event.preventDefault();
    const current = buttons.indexOf(button);
    const nextIndex =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? buttons.length - 1
          : ((current < 0 ? 0 : current) + step + buttons.length) % buttons.length;
    buttons[nextIndex].focus();
    buttons[nextIndex].click();
  }

  function setTask(
    task: TaskKind,
    nextVideoTask?: 'upscale' | 'denoise',
    enlarge?: { scale: number; modelId: string },
  ): void {
    if (selectedMedia && (task === 'video') !== (selectedMedia.kind === 'video')) return;
    upscalerBeforeSameSize = '';
    if (task !== 'video') lastImageTask = task;
    if (task === 'edit') {
      // Editing leaves the upscale and restore setup untouched for when you switch back.
      updateSettings({ task, enable_face_model: false });
      termsAccepted = {};
      ensureEditModel();
      return;
    }
    const wasRestoring = restoring;
    const prevQuality = activeQuality;
    const targetVideoTask = task === 'video' ? (nextVideoTask ?? videoTask) : undefined;
    const targetRestoring =
      task === 'denoise' || (task === 'video' && targetVideoTask === 'denoise');
    // Colour picked for photos is not carried to a video: on a colour video it would
    // replace the real colours, so a video gets Colour only when it is chosen there.
    const enteringVideo = task === 'video' && settings.task !== 'video';
    const videoFixes = enteringVideo
      ? settings.fix_stages.filter((selection) => selection.fix !== 'colour')
      : settings.fix_stages;
    const models = modelsForTask(snapshot, task, targetVideoTask);
    const enlargeCustom =
      !targetRestoring && enlarge?.modelId === '__custom__' && Boolean(settings.custom_model_path);
    const enlargeCatalog =
      !targetRestoring && enlarge?.modelId && !enlargeCustom
        ? models.find((model) => model.model_id === enlarge.modelId)
        : undefined;
    // Keep a manual choice that still fits the task (or the upscaler remembered before Same size);
    // otherwise the active recipe (Quick until one is chosen) resolves the plan for the new task.
    const kept = enlargeCustom
      ? undefined
      : enlargeCatalog
        ? enlargeCatalog
        : prevQuality === 'custom'
          ? models.find((model) => model.model_id === settings.selected_model_id)
          : undefined;
    const keptId = enlargeCustom ? '__custom__' : (kept?.model_id ?? '');
    const targetScale = enlarge
      ? enlarge.scale
      : settings.output_scale >= 2
        ? settings.output_scale
        : 4;
    updateSettings({
      task,
      ...(task === 'video' ? { video_task: targetVideoTask } : {}),
      ...(videoFixes !== settings.fix_stages
        ? { fix_stages: videoFixes, fixes: videoFixes.map((selection) => selection.fix) }
        : {}),
      ...(targetRestoring ? { selected_video_model_id: 'frame_by_frame' } : {}),
      ...(enlargeCustom || enlargeCatalog ? { quality: 'custom' } : {}),
      ...(enlargeCatalog ? { halo: enlargeCatalog.recommended_halo } : {}),
      selected_model_id: keptId,
      output_scale: targetRestoring
        ? 1
        : Math.min(kept?.native_scale ?? 4, Math.max(2, targetScale)),
      enable_face_model: false,
    });
    termsAccepted = {};
    if (!keptId) {
      const fallbackQuality =
        wasRestoring !== targetRestoring
          ? fixQuality(prevQuality)
          : prevQuality === 'quick' || prevQuality === 'best'
            ? prevQuality
            : 'quick';
      resolveRecipe(
        {
          quality: fallbackQuality,
          fix_stages: videoFixes,
          fixes: videoFixes.map((selection) => selection.fix),
          ...(task === 'video' ? { video_task: targetVideoTask } : {}),
        },
        { keepVideoEngine: !targetRestoring, keepScale: true },
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

  async function selectQueueMedia(id: string, restoreLive = true): Promise<void> {
    if (restoreLive && id === activeJobMediaId && pairChoices[id]?.pinned)
      pairChoices = without(pairChoices, id);
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
    if (mainTab !== 'preview') return null;
    if (
      selectedMedia?.kind === 'video' &&
      videoHistory &&
      (selectedMedia.id !== activeJobMediaId || videoChoice.pinned)
    )
      return videoChoice.showing === ORIGINAL
        ? { kind: 'original', media_id: selectedMedia.id }
        : { kind: 'output', media_id: selectedMedia.id, job_id: videoChoice.showing };
    if (!selectedImage || !pairShown) return null;
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
    await selectQueueMedia(pick.media_id, false);
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

  async function chooseModelFolder(): Promise<void> {
    if (movingModels || settingsLocked || Boolean(activeDownload)) return;
    try {
      const path = await api.chooseModelDirectory(settings.model_directory ?? '');
      if (!path) return;
      movingModels = true;
      modelMoveProgress = null;
      await api.setModelDirectory(path);
      await refresh();
    } catch (error) {
      showModal('Could not move the model folder', String(error));
    } finally {
      movingModels = false;
      modelMoveProgress = null;
      await maybeAutoStart();
    }
  }

  async function resetModelFolder(): Promise<void> {
    if (movingModels || settingsLocked || Boolean(activeDownload)) return;
    try {
      movingModels = true;
      modelMoveProgress = null;
      await api.resetModelDirectory();
      await refresh();
    } catch (error) {
      showModal('Could not reset the model folder', String(error));
    } finally {
      movingModels = false;
      modelMoveProgress = null;
      await maybeAutoStart();
    }
  }

  let updateNoticeVisible = false;
  let engineDialogOpen = false;
  let engineActionBusy = false;
  let engineRefreshingCatalog = false;
  let engineError = '';
  let engineErrorBackend = '';
  let engineErrorAction: 'download' | 'use' | 'remove' | 'verify' | '' = '';
  let engineVerifiedBackend = '';
  $: engineOffer = recommendedEngineOffer(snapshot.engines, settings);

  function handleEngineStatus(status: EngineDownloadStatus): void {
    if (!snapshot.engines) return;
    snapshot = {
      ...snapshot,
      engines: {
        ...snapshot.engines,
        download: status,
      },
    };
    if (status.stage === 'error') {
      engineError = status.error ? plainEngineError(status.error) : 'Engine installation failed.';
      engineErrorBackend = status.backend;
      engineErrorAction = 'download';
    } else if (status.stage === 'cancelled' || status.stage === 'downloading') {
      engineError = '';
      engineErrorBackend = '';
      engineErrorAction = '';
    } else if (status.stage === 'installed') {
      engineError = '';
      engineErrorBackend = '';
      engineErrorAction = '';
      void refresh();
    }
  }

  async function refreshEngineCatalogList(): Promise<void> {
    if (engineRefreshingCatalog) return;
    engineRefreshingCatalog = true;
    engineError = '';
    engineErrorBackend = '';
    engineErrorAction = '';
    try {
      const catalog = await api.refreshEngineCatalog();
      if (snapshot.engines) {
        snapshot = {
          ...snapshot,
          engines: {
            ...snapshot.engines,
            catalog,
            catalog_error: null,
          },
        };
      }
      await refresh();
    } catch (error) {
      engineError = plainEngineError(error);
    } finally {
      engineRefreshingCatalog = false;
    }
  }

  async function openEngineManager(): Promise<void> {
    engineError = '';
    engineErrorBackend = '';
    engineErrorAction = '';
    engineVerifiedBackend = '';
    engineDialogOpen = true;
    if (
      snapshot.engines?.supported &&
      snapshot.engines.configured &&
      !snapshot.engines.catalog.length
    ) {
      await refreshEngineCatalogList();
    }
  }

  async function downloadEngine(backend: string): Promise<void> {
    engineError = '';
    engineErrorBackend = '';
    engineErrorAction = '';
    try {
      await api.installEngine(backend);
      await refresh();
    } catch (error) {
      if (snapshot.engines?.download.stage !== 'cancelled' && !/cancelled/i.test(String(error))) {
        engineError = plainEngineError(error);
        engineErrorBackend = backend;
        engineErrorAction = 'download';
      }
      await refresh().catch(() => undefined);
    }
  }

  async function cancelEngineDownload(): Promise<void> {
    try {
      await api.cancelEngineInstall();
    } catch (error) {
      engineError = plainEngineError(error);
    }
  }

  async function activateEngine(backend: string): Promise<void> {
    if (engineActionBusy) return;
    engineActionBusy = true;
    engineError = '';
    engineErrorBackend = '';
    engineErrorAction = '';
    engineVerifiedBackend = '';
    try {
      await api.useEngine(backend);
      await refresh();
    } catch (error) {
      engineError = plainEngineError(error);
      engineErrorBackend = backend;
      engineErrorAction = 'use';
    } finally {
      engineActionBusy = false;
    }
  }

  async function verifyInstalledEngine(id: string): Promise<void> {
    if (engineActionBusy) return;
    const targetBackend = snapshot.engines?.installed.find((item) => item.id === id)?.backend ?? '';
    engineActionBusy = true;
    engineError = '';
    engineErrorBackend = '';
    engineErrorAction = '';
    engineVerifiedBackend = '';
    try {
      await api.verifyEngine(id);
      engineVerifiedBackend = targetBackend;
      await refresh();
    } catch (error) {
      engineError = plainEngineError(error);
      engineErrorBackend = targetBackend;
      engineErrorAction = 'verify';
      await refresh().catch(() => undefined);
    } finally {
      engineActionBusy = false;
    }
  }

  async function deleteEngine(id: string): Promise<void> {
    if (engineActionBusy) return;
    const targetBackend = snapshot.engines?.installed.find((item) => item.id === id)?.backend ?? '';
    engineActionBusy = true;
    engineError = '';
    engineErrorBackend = '';
    engineErrorAction = '';
    engineVerifiedBackend = '';
    try {
      await api.removeEngine(id);
      await refresh();
    } catch (error) {
      engineError = plainEngineError(error);
      engineErrorBackend = targetBackend;
      engineErrorAction = 'remove';
    } finally {
      engineActionBusy = false;
    }
  }

  async function importEngineFromFile(): Promise<void> {
    if (engineActionBusy) return;
    engineError = '';
    engineErrorBackend = '';
    engineErrorAction = '';
    let path: string | null = null;
    try {
      path = await api.chooseEngineManifest();
    } catch (error) {
      engineError = plainEngineError(error);
      return;
    }
    if (!path) return;
    engineActionBusy = true;
    try {
      await api.importEngine(path);
      await refresh();
    } catch (error) {
      engineError = plainEngineError(error);
    } finally {
      engineActionBusy = false;
    }
  }

  let linuxMenuBusy = false;

  async function acceptLinuxMenu(): Promise<void> {
    if (linuxMenuBusy) return;
    linuxMenuBusy = true;
    try {
      await api.installLinuxMenu();
      snapshot = {
        ...snapshot,
        linux_menu_offer: false,
        linux_menu_installed: true,
        settings: { ...snapshot.settings, linux_menu_prompt_dismissed: true },
      };
    } catch (error) {
      showModal('Could not add to applications menu', String(error));
    } finally {
      linuxMenuBusy = false;
    }
  }

  async function removeLinuxMenu(): Promise<void> {
    if (linuxMenuBusy) return;
    linuxMenuBusy = true;
    try {
      await api.uninstallLinuxMenu();
      snapshot = {
        ...snapshot,
        linux_menu_installed: false,
        settings: { ...snapshot.settings, linux_menu_prompt_dismissed: true },
      };
    } catch (error) {
      showModal('Could not remove from applications menu', String(error));
    } finally {
      linuxMenuBusy = false;
    }
  }

  async function dismissLinuxMenu(): Promise<void> {
    snapshot = {
      ...snapshot,
      linux_menu_offer: false,
      settings: { ...snapshot.settings, linux_menu_prompt_dismissed: true },
    };
    if (api.isTauri()) {
      await api
        .dismissLinuxMenuPrompt()
        .catch((error) => showModal('Could not save settings', String(error)));
    }
  }

  async function dismissGpuDownloadNotice(): Promise<void> {
    snapshot = {
      ...snapshot,
      settings: { ...snapshot.settings, gpu_download_notice_dismissed: true },
      gpu_download_notice: null,
    };
    if (api.isTauri()) {
      await api
        .dismissGpuDownloadNotice()
        .catch((error) => showModal('Could not save settings', String(error)));
    }
  }

  let consentPath = '';
  let consentRequest = 0;
  $: if (!settingsLocked && settings.custom_model_path !== consentPath) {
    consentPath = settings.custom_model_path;
    void refreshCheckpointConsent(consentPath);
  }

  async function refreshCheckpointConsent(path: string): Promise<void> {
    const request = ++consentRequest;
    if (!path || path.toLowerCase().endsWith('.safetensors')) return;
    updateSettings({ allow_unsafe_pickle_model: false });
    try {
      const allowed = await api.checkpointConsent(path);
      if (request === consentRequest && settings.custom_model_path === path) {
        if (settingsLocked) consentPath = '';
        else updateSettings({ allow_unsafe_pickle_model: allowed });
      }
    } catch {
      if (request === consentRequest && settings.custom_model_path === path) {
        if (settingsLocked) consentPath = '';
        else updateSettings({ allow_unsafe_pickle_model: false });
      }
    }
  }

  async function changeCheckpointConsent(allow: boolean): Promise<void> {
    const path = settings.custom_model_path;
    const request = ++consentRequest;
    try {
      const allowed = await api.checkpointConsent(path, allow);
      if (request === consentRequest && settings.custom_model_path === path)
        updateSettings({ allow_unsafe_pickle_model: allowed });
    } catch (error) {
      if (request === consentRequest && settings.custom_model_path === path) {
        updateSettings({ allow_unsafe_pickle_model: false });
        showModal('Could not save checkpoint confirmation', String(error));
      }
    }
  }

  function selectCustomModel(path: string): void {
    const models = customModels(settings);
    if (!models.some((model) => model.path === path)) {
      models.push({ path, name: defaultCustomModelName(path) });
    }
    updateSettings({
      selected_model_id: '__custom__',
      ...(settings.task === 'video' && settings.selected_video_model_id !== 'frame_by_frame'
        ? { selected_video_model_id: 'frame_by_frame' }
        : {}),
      custom_model_path: path,
      custom_models: models,
      allow_unsafe_pickle_model: false,
      enable_face_model: false,
      quality: 'custom',
      ...(restoring ? { fix_stages: [], fixes: [] } : {}),
    });
    if (consentPath === path) void refreshCheckpointConsent(path);
    libraryOpen = false;
    termsAccepted = {};
  }

  function renameCustomModel(path: string, name: string): void {
    updateSettings({
      custom_models: customModels(settings).map((model) =>
        model.path === path
          ? { ...model, name: name.trim() || defaultCustomModelName(path) }
          : model,
      ),
    });
  }

  async function chooseCustomModel(): Promise<void> {
    const path = await api.chooseCustomModel().catch((error) => {
      showModal('Could not choose the model', String(error));
      return null;
    });
    if (path) selectCustomModel(path);
  }

  function useVideoModelForJob(model: CatalogVideoModel | undefined): void {
    if (!model) return;
    updateSettings({
      selected_video_model_id: model.model_id,
      quality: 'custom',
      output_scale: Math.min(4, Math.max(2, settings.output_scale)),
      enable_face_model: false,
    });
    termsAccepted = {};
    libraryOpen = false;
  }

  function selectPrimaryModel(modelId: string): void {
    const model = snapshot.catalog.models.find((candidate) => candidate.model_id === modelId);
    if (restoring && model) {
      const fix = fixesOf(model).find(
        (entry): entry is Exclude<FixKind, 'faces'> => entry !== 'faces',
      );
      if (fix) {
        updateSettings({
          selected_model_id: modelId,
          ...(settings.task === 'video' && settings.selected_video_model_id !== 'frame_by_frame'
            ? { selected_video_model_id: 'frame_by_frame' }
            : {}),
          fix_stages: [{ fix, model_id: modelId }],
          fixes: [fix],
          halo: model.recommended_halo,
          output_scale: 1,
          allow_unsafe_pickle_model: false,
          enable_face_model: false,
          quality: 'custom',
        });
        termsAccepted = {};
        return;
      }
    }
    updateSettings({
      selected_model_id: modelId,
      ...(settings.task === 'video' && settings.selected_video_model_id !== 'frame_by_frame'
        ? { selected_video_model_id: 'frame_by_frame' }
        : {}),
      output_scale: restoring
        ? 1
        : model
          ? Math.min(model.native_scale, Math.max(2, settings.output_scale))
          : settings.output_scale,
      // As the presets do: some models need a wider overlap between tiles.
      halo: model ? model.recommended_halo : settings.halo,
      allow_unsafe_pickle_model: false,
      enable_face_model: false,
      quality: 'custom',
    });
    termsAccepted = {};
  }

  function applyPreset(kind: 'quick' | 'best'): void {
    if (!settings.task || settings.task === 'edit') {
      showModal('Choose a task', 'Choose a task before applying a preset.');
      return;
    }
    upscalerBeforeSameSize = '';
    // Keep the current size: Quick and Best choose the upscaler and every fix model.
    resolveRecipe({ quality: kind }, { keepScale: true });
  }

  function dismissDownloadError(modelId: string): void {
    if (modelId in downloadErrors) {
      const nextErrors = { ...downloadErrors };
      delete nextErrors[modelId];
      downloadErrors = nextErrors;
    }
    if (modelId in downloadFailures) {
      const nextFailures = { ...downloadFailures };
      delete nextFailures[modelId];
      downloadFailures = nextFailures;
    }
    if (rememberedFailureIds.includes(modelId)) {
      rememberedFailureIds = rememberedFailureIds.filter((id) => id !== modelId);
    }
  }

  async function cancelDownloadById(modelId: string): Promise<void> {
    cancelledDownloads.add(modelId);
    await attempt('Could not cancel the download', () => api.cancelDownload(modelId));
  }

  async function cancelAllDownloads(): Promise<void> {
    const ids = activeDownloads.map((item) => item.model_id);
    if (!ids.length) return;
    cancellingDownloads = true;
    for (const id of ids) cancelledDownloads.add(id);
    await Promise.allSettled(ids.map((id) => api.cancelDownload(id)));
    cancellingDownloads = false;
  }

  async function runDownload(
    target: CatalogModel | CatalogVideoModel | EditModel | undefined = currentDownloadTarget,
    accepted?: boolean,
  ): Promise<void> {
    if (!target) return;
    if (downloadMap.has(target.model_id)) {
      await cancelDownloadById(target.model_id);
      return;
    }
    accepted ??= Boolean(termsAccepted[target.model_id]);
    if (target.terms_acceptance_required && !accepted) {
      showModal('Review the model terms', 'Confirm the model-specific license before downloading.');
      return;
    }
    if (target.automated_download_allowed) {
      downloadAcceptedTerms[target.model_id] = accepted;
    }
    dismissDownloadError(target.model_id);
    rememberedFailureIds = [];
    cancelledDownloads.delete(target.model_id);
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
      const message = errorMessageText(error);
      const wasCancelled =
        cancelledDownloads.delete(target.model_id) || message.includes('download cancelled');
      if (wasCancelled) {
        await refresh();
        return;
      }
      downloadErrors = { ...downloadErrors, [target.model_id]: message };
      if (!target.automated_download_allowed) {
        showModal('Import failed', message);
        return;
      }
      recordDownloadFailure(target.model_id, error);
    }
  }

  function findDownloadModel(
    modelId: string,
  ): CatalogModel | CatalogVideoModel | EditModel | undefined {
    return (
      snapshot.catalog.models.find((item) => item.model_id === modelId) ??
      snapshot.catalog.video_models.find((item) => item.model_id === modelId) ??
      snapshot.catalog.interpolation_models?.find((item) => item.model_id === modelId) ??
      snapshot.catalog.audio_models?.find((item) => item.model_id === modelId) ??
      snapshot.edit.models.find((item) => item.model_id === modelId)
    );
  }

  async function retryDownload(modelId: string): Promise<void> {
    const target = findDownloadModel(modelId);
    if (!target || !target.automated_download_allowed) return;
    await runDownload(target, downloadAcceptedTerms[modelId] ?? Boolean(termsAccepted[modelId]));
  }

  function makeStartInput(ids: string[], task: TaskKind, batchMode: boolean): StartBatchInput {
    return buildStartInput(settings, {
      mediaIds: ids,
      task,
      batchMode,
      hasAudio: audioHasSource,
      recipeName: snapshot.recipes.find((recipe) => activeRecipeIds.has(recipe.id))?.name ?? '',
    });
  }

  async function start(mediaIds?: string[]): Promise<void> {
    const settled = await planClient.settled();
    if (settled.status === 'error') return;
    await tick();
    if (movingModels || (!mediaIds && !canQueue) || !selectedMedia || !settings.task) return;
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
    rememberedFailureIds = [];
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
    rememberedFailureIds = [];
    startingJob = true;
    try {
      await api.startEdit({
        media_id: selectedMedia.id,
        model_id: editModel.model_id,
        prompt: editPrompt.trim(),
        runtime_path: editRuntime,
        device: editingDevice.id,
        max_dimension: editSizeFor(settings.edit_max_dimension, editSizeAllowed, editExpertState),
        steps: settings.edit_steps || editModel.default_steps,
        seed: settings.edit_seed,
        accepted_terms: Boolean(termsAccepted[editModel.model_id]),
        output_directory: settings.output_directory,
        edit_text_encoder: editExpertState.textEncoder,
        edit_vae: editExpertState.vae,
        edit_vram_limit_mb: editExpertState.vramLimitMb,
        edit_allow_any_size: editExpertState.allowAnySize,
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
  $: selectedInflight = selectedMedia
    ? snapshot.jobs.filter(
        (job) =>
          job.media_id === selectedMedia?.id &&
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

  function activeJobSupportsPartial(): boolean {
    const activeId = snapshot.runtime.active_job_id;
    if (!activeId) return false;
    const activeJob = snapshot.jobs.find((job) => job.id === activeId);
    if (!activeJob) return false;
    // A photo is kept only when the host says so; an older host never does.
    if (activeJob.media_kind !== 'video') return snapshot.runtime.can_keep_partial === true;
    if (snapshot.runtime.can_keep_partial !== undefined) {
      return snapshot.runtime.can_keep_partial;
    }
    const codec = settings.video_codec ?? 'auto';
    if (codec === 'h264' || codec === 'hevc' || codec === 'prores') {
      const system = systemCodecs(snapshot.capabilities);
      const container = settings.video_container ?? 'mp4';
      const media = snapshot.media.find((item) => item.id === activeJob.media_id);
      const preserving =
        (settings.video_hdr_mode ?? 'tone_map') === 'preserve' && Boolean(media?.hdr_format);
      if (
        !system ||
        preserving ||
        !system.encode.includes(codec) ||
        !system.containers.includes(container)
      ) {
        return false;
      }
    }
    return true;
  }

  /** Something is finished that could be kept: a frame of a video, a tile of a photo. */
  function activeJobCanKeepPartial(): boolean {
    if (!activeJobSupportsPartial()) return false;
    const activeJob = snapshot.jobs.find((job) => job.id === snapshot.runtime.active_job_id);
    return activeJob?.media_kind === 'video'
      ? (snapshot.runtime.frames_processed ?? 0) > 0
      : (snapshot.runtime.tiles_completed ?? 0) > 0;
  }

  async function performCancelSelectedImage(mediaId: string, keepPartial: boolean): Promise<void> {
    cancellingImage = true;
    try {
      await api.cancelJobsForMedia(mediaId, keepPartial);
      await refresh();
    } catch (error) {
      showModal('Could not cancel', String(error));
    } finally {
      cancellingImage = false;
    }
  }

  async function cancelSelectedImage(): Promise<void> {
    if (!selectedMedia || stopPrompt) return;
    const activeId = snapshot.runtime.active_job_id;
    const stopsActive = Boolean(activeId && activeJobMediaId === selectedMedia.id);
    const choice = settings.stopped_job_result ?? 'ask';
    if (stopsActive && activeJobCanKeepPartial() && choice === 'ask') {
      stopDontAskAgain = false;
      stopPrompt = {
        scope: 'media',
        mediaId: selectedMedia.id,
        jobId: activeId,
        fileName: selectedMedia.name,
      };
      return;
    }
    const keepPartial = stopsActive && activeJobSupportsPartial() && choice === 'keep';
    await performCancelSelectedImage(selectedMedia.id, keepPartial);
  }

  async function performCancelWork(keepPartial: boolean): Promise<void> {
    if (cancelling) return;
    requestingCancel = true;
    discardRuntimePulse();
    try {
      await api.cancelJobs(keepPartial);
      await refresh();
    } catch (error) {
      showModal('Could not cancel', String(error));
    } finally {
      requestingCancel = false;
    }
  }

  async function cancelWork(): Promise<void> {
    if (cancelling || stopPrompt) return;
    const activeId = snapshot.runtime.active_job_id;
    const choice = settings.stopped_job_result ?? 'ask';
    if (activeId && activeJobCanKeepPartial() && choice === 'ask') {
      const activeJob = snapshot.jobs.find((job) => job.id === activeId);
      const media = activeJob
        ? snapshot.media.find((item) => item.id === activeJob.media_id)
        : undefined;
      stopDontAskAgain = false;
      stopPrompt = {
        scope: 'queue',
        mediaId: activeJob?.media_id ?? '',
        jobId: activeId,
        fileName:
          media?.name ||
          activeJob?.media_name ||
          (activeJob?.media_kind === 'image' ? 'this photo' : 'this video'),
      };
      return;
    }
    const keepPartial = activeJobSupportsPartial() && choice === 'keep';
    await performCancelWork(keepPartial);
  }

  function closeStopModal(): void {
    stopPrompt = null;
    stopDontAskAgain = false;
  }

  async function confirmStopModal(keepPartial: boolean): Promise<void> {
    const prompt = stopPrompt;
    const remember = stopDontAskAgain;
    if (!prompt) return;
    stopPrompt = null;
    stopDontAskAgain = false;
    if (remember) {
      await setPreference('stopped_job_result', keepPartial ? 'keep' : 'discard');
    }
    if (prompt.scope === 'media') {
      await performCancelSelectedImage(prompt.mediaId, keepPartial);
    } else {
      await performCancelWork(keepPartial);
    }
  }

  async function addRecipe(): Promise<void> {
    const name = recipeName.trim() || `My Recipe ${snapshot.recipes.length + 1}`;
    if (!settings.task) return;
    // The fixes that run before the model; a fix without a model has nothing to keep.
    const recipeFixPrefix = (
      usingTemporalVideo ? [] : restoring ? imageFixes.slice(0, -1) : imageFixes
    ).filter((selection) => selection.model_id);
    const recipe: Recipe = {
      id: crypto.randomUUID(),
      name,
      task: settings.task,
      ...(settings.task === 'video' ? { video_task: videoTask } : {}),
      model_id: settings.selected_model_id,
      output_scale: restoring ? 1 : settings.output_scale,
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
      output_bit_depth: settings.output_bit_depth ?? 8,
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
      enable_face_model: false,
      face_fidelity: settings.face_fidelity,
      video_interpolation: settings.video_interpolation ?? 'off',
      video_interpolation_order: settings.video_interpolation_order ?? 'after_upscale',
      video_clean_audio: Boolean(settings.video_clean_audio),
      video_audio_model: settings.video_audio_model ?? '',
      quality: activeQuality,
      content: settings.content,
      fixes: settings.fixes.filter((fix) => fix !== 'faces'),
      stages: noRestoreModel
        ? []
        : [
            ...recipeFixPrefix.map((selection) => ({
              kind: fixStageKind(
                snapshot.catalog.models.find((model) => model.model_id === selection.model_id),
                selection.fix,
              ),
              model_id: selection.model_id,
              fix: selection.fix,
            })),
            {
              kind: restoring
                ? ('restore' as const)
                : settings.task === 'video'
                  ? ('video' as const)
                  : ('upscale' as const),
              model_id:
                settings.task === 'video' && usingTemporalVideo
                  ? settings.selected_video_model_id
                  : settings.selected_model_id,
              // Restore's model is its last fix.
              ...(restoring && imageFixes.length
                ? { fix: imageFixes[imageFixes.length - 1].fix }
                : {}),
            },
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
    document.querySelector<HTMLInputElement>('.enhance-pane .recipe-input input')?.focus();
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
    upscalerBeforeSameSize = '';
    const fix_stages = recipeFixStages(recipe, snapshot.catalog);
    const recipeModel = snapshot.catalog.models.find((model) => model.model_id === recipe.model_id);
    const targetRestoring = isRestoreRecipe(recipe);
    const targetTask: TaskKind =
      recipe.task === 'upscale' && targetRestoring ? 'denoise' : recipe.task;
    const targetVideoTask =
      targetTask === 'video' ? (targetRestoring ? 'denoise' : 'upscale') : 'upscale';
    if (targetTask !== 'video') lastImageTask = targetTask;
    const migratedFromUpscale1x =
      targetRestoring &&
      recipe.task !== 'denoise' &&
      !(recipe.task === 'video' && recipe.video_task === 'denoise');
    const recipeQuality: 'quick' | 'best' =
      recipe.quality === 'quick' || recipe.quality === 'best' ? recipe.quality : 'best';
    const targetModelId = migratedFromUpscale1x
      ? (fix_stages.at(-1)?.model_id ??
        (targetTask === 'video'
          ? ''
          : recipeModel?.native_scale === 1
            ? recipe.model_id
            : (choosePresetModel(
                snapshot,
                'denoise',
                recipeQuality,
                recipe.content || settings.content,
                settings.preset_pins,
              )?.model_id ?? '')))
      : recipe.model_id;
    updateSettings({
      task: targetTask,
      ...(targetTask === 'video' ? { video_task: targetVideoTask } : {}),
      selected_model_id: targetModelId,
      selected_video_model_id: targetRestoring
        ? 'frame_by_frame'
        : recipe.video_model_id || 'frame_by_frame',
      fix_stages,
      custom_model_path: recipe.custom_model_path ?? '',
      output_scale: targetRestoring
        ? 1
        : Math.min(recipeModel?.native_scale ?? recipe.output_scale, recipe.output_scale),
      output_format: recipe.output_format ?? settings.output_format,
      output_bit_depth: recipe.output_bit_depth ?? settings.output_bit_depth,
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
      video_clean_audio: recipe.video_clean_audio ?? false,
      video_audio_model: recipe.video_audio_model ?? '',
      enable_face_model: false,
      face_fidelity: recipe.face_fidelity ?? settings.face_fidelity,
      allow_unsafe_pickle_model: false,
      quality: recipe.quality || 'custom',
      content: recipe.content || settings.content,
      fixes: fix_stages.map((selection) => selection.fix),
    });
    termsAccepted = {};
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
    if (!pendingAutoStartIds.length || movingModels) return;
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
      fixesReady &&
      interpolationReady &&
      stepsReady;
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
      (modalTitle ||
        stopPrompt ||
        reportOpen ||
        settings.privacy_choice_made === false ||
        libraryOpen ||
        settingsOpen ||
        ffmpegNotice ||
        $updateDialogOpen) &&
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
          else await cancelWork();
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
        case 'help.report-problem':
          await showReport();
          break;
        case 'app.settings':
          openSettings('general');
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
    void refreshCrashReports();
    showModal(
      'Performance & Diagnostics',
      'Live values come from the isolated inference worker. Temperature is shown only when a supported backend reports it.',
      'performance',
    );
  }

  async function showIntegrations(): Promise<void> {
    try {
      integration = await api.integrationStatus();
      showModal('System Integrations', integration.summary, 'integrations');
    } catch (error) {
      showModal('System Integrations', String(error));
    }
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
    benchmarkError = '';
    try {
      benchmarkSetup = 'starting';
      await api.startBenchmark();
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
        'Wait for the running job to finish, then select FFmpeg under Settings → Storage.',
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

  function deleteRecipe(recipeId: string): void {
    deletingRecipeId = null;
    void attempt('Could not delete the recipe', async () => {
      await api.deleteRecipe(recipeId);
      await refresh();
    });
  }

  const enhanceActions: EnhancePanelActions = {
    setTask,
    updateSettings,
    openRecipeEditor,
    applyPreset,
    deleteRecipe,
    applyRecipe,
    addRecipe,
    setContent,
    radioGroupKeydown,
    toggleFix,
    setSize,
    openLibrary,
    moveFix,
    chooseCustomModel,
    changeCheckpointConsent,
    openAnyLicense,
    openAnySource,
    openCustomEdit: () => {
      customEditOpen = true;
    },
    runDownload,
    retryPlan: () => planClient.retry(),
    chooseEditRuntime,
    retryOnCpu,
    chooseModelFolder,
    resetModelFolder,
    chooseInAdvanced,
    useDefaultsForSession,
    chooseOutput,
    openSettings,
    refreshCapabilities: async () => {
      try {
        await api.refreshCapabilities();
        return true;
      } catch (error) {
        showModal('Could not refresh the hardware', String(error));
        return false;
      }
    },
  };

  let enhanceView: EnhancePanelView;
  $: enhanceView = {
    page,
    snapshot,
    settings,
    defaultSettings,
    selectedMedia,
    settingsLocked,
    movingModels,
    lockReason,
    activeQuality,
    restoring,
    usingTemporalVideo,
    videoTask,
    selectedModel,
    selectedVideoModel,
    editModel,
    editFit,
    editRuntime,
    editingDevice,
    editSupported,
    editBlockReason,
    preservingHdr,
    hdrPreservationAvailable,
    hdrModelCompatible,
    audioDisabled,
    audioModels,
    selectedAudioModel,
    quickDownload,
    bestDownload,
    activeRecipeIds,
    sizeResult,
    sizeMaxScale,
    bestModel,
    imageFixes,
    noRestoreModel,
    planState: $planState,
    plan,
    videoAvailable,
    activeDevice,
    downloadMap,
    canOpenTrials,
    canTryOthers,
    customModelNeedsOptIn,
    licenseReviewModel,
    preparing,
    hasNothingToRun,
    nothingToRunReason,
    temporalUnavailableReason,
    fixPlanSupported,
    downloads,
    flashVideo,
    videoMemoryEngine,
    selectedMemory,
    outputDimensions,
    lastFailure,
    acceleratorFailure,
    interpolationSupported,
  };
</script>

<svelte:window
  on:click={(event) => {
    if (deletingRecipeId === null) return;
    // The path is fixed when the click starts. The X that opened the question is already
    // gone from the page when this runs, so its row must not be looked up from the target.
    const inRow = event
      .composedPath()
      .some(
        (node) =>
          node instanceof Element && node.getAttribute('data-recipe-id') === deletingRecipeId,
      );
    if (!inRow) deletingRecipeId = null;
  }}
  on:keydown={(event) => {
    if (event.key === 'Escape') deletingRecipeId = null;
  }}
/>

<svelte:head
  ><title>{selectedMedia ? `${selectedMedia.name} — LocalSR` : 'LocalSR'}</title></svelte:head
>

<div
  class="app-shell"
  class:booting
  class:mac={isMac}
  class:queue-timing-visible={Boolean(queueEta)}
  class:status-extra={Boolean(hardwareNote || hardwareHint)}
  style={`--ui-scale:${settings.interface_scale / 100}`}
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
  />
  <WelcomeNotice
    anonymousUpdateCount={settings.anonymous_update_count ?? false}
    checkAtLaunch={settings.check_updates_at_launch ?? true}
  />

  <div class="top-notices">
    <UpdateNotice
      bind:visible={updateNoticeVisible}
      processing={settingsLocked || inflightMediaIds.size > 0 || Boolean(activeDownload)}
    />
    {#if !updateNoticeVisible}
      {#if engineOffer}
        <EngineRecommendationCard
          offer={engineOffer}
          download={snapshot.engines?.download ?? {
            stage: 'idle',
            backend: '',
            engine_id: '',
            downloaded: 0,
            total: 0,
          }}
          processing={settingsLocked || inflightMediaIds.size > 0 || Boolean(activeDownload)}
          error={engineError}
          onDownload={() => void downloadEngine(engineOffer.backend)}
          onCancel={() => void cancelEngineDownload()}
          onDismiss={() => void dismissGpuDownloadNotice()}
        />
      {:else if settings.privacy_choice_made !== false && snapshot.gpu_download_notice && !settings.gpu_download_notice_dismissed}
        <GpuDownloadNotice
          message={snapshot.gpu_download_notice}
          onOpenDownloadPage={() =>
            attempt('Could not open the download page', () => api.openDownloadPage())}
          onDismiss={() => void dismissGpuDownloadNotice()}
        />
      {/if}
      {#if settings.privacy_choice_made !== false && snapshot.linux_menu_offer && !settings.linux_menu_prompt_dismissed}
        <LinuxMenuNotice
          busy={linuxMenuBusy}
          onAdd={() => void acceptLinuxMenu()}
          onDismiss={() => void dismissLinuxMenu()}
        />
      {/if}
    {/if}
  </div>
  <main class="workspace">
    <MediaQueue
      bind:this={mediaQueue}
      groups={compareGroups}
      picks={comparePicks}
      togglePick={(pick) => setComparePicks(toggleSlot(comparePicks, pick, snapshot.media))}
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
          engineBusy={videoEngineBusy}
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
          viewerBackground={snapshot.settings.viewer_background ?? 'checker'}
          setViewerBackground={(value) => void setPreference('viewer_background', value)}
          asTab
        >
          <svelte:fragment slot="tabs">{@render paneTabs()}</svelte:fragment>
        </ComparePane>
      {:else}
        <PreviewPane
          bind:this={previewPane}
          bind:liveFrame
          viewerBackground={snapshot.settings.viewer_background ?? 'checker'}
          setViewerBackground={(value) => void setPreference('viewer_background', value)}
          editing={settings.task === 'edit'}
          processing={Boolean(
            activeJobMediaId &&
            selectedMedia?.id === activeJobMediaId &&
            !(selectedMedia.kind === 'video' && videoChoice.pinned),
          )}
          {videoEngineBusy}
          activeTileSize={snapshot.runtime.active_tile_size}
          stagePreview={!activeJobMediaId || selectedMedia?.id === activeJobMediaId
            ? activeStepPreview
            : ''}
          stepLabel={viewerStepLabel}
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
          {pairGeneration}
          showingWidth={shownView.showingWidth}
          showingHeight={shownView.showingHeight}
          showingPreviewWidth={shownView.showingPreviewWidth}
          showingPreviewHeight={shownView.showingPreviewHeight}
          showingRegion={shownView.showingRegion}
          referenceWidth={shownView.referenceWidth}
          referenceHeight={shownView.referenceHeight}
          referencePreviewWidth={shownView.referencePreviewWidth}
          referencePreviewHeight={shownView.referencePreviewHeight}
          referenceRegion={shownView.referenceRegion}
          {originalRegion}
          requestRegions={(mediaId, generation, regions) =>
            api.isTauri()
              ? api.requestPreviewRegions(mediaId, generation, regions)
              : Promise.resolve()}
          clearRegions={() => (api.isTauri() ? api.clearPreviewRegions() : Promise.resolve())}
          singleFrame={shownView.singleFrame}
          showingUnfinished={shownView.showingUnfinished}
          referenceUnfinished={shownView.referenceUnfinished}
          videoFps={(completedVideoOutput && videoResult?.output_fps) || selectedMedia?.fps || 0}
          loadingLabel={pairLoadingLabel}
          {comparisonNote}
          showLiveTiles={selectedMedia?.kind === 'video' ? !videoChoice.pinned : !choice.pinned}
          comparisonError={selectedComparisonError}
          {completedVideoOutput}
          {videoResultId}
          compactHidden={page !== 'preview'}
          addFiles={() => void addFiles()}
          dropping={filesDragging}
        />
      {/if}
    </div>

    <EnhancePanel
      view={enhanceView}
      actions={enhanceActions}
      bind:editPrompt
      bind:recipeName
      bind:recipeEditorOpen
      bind:deletingRecipeId
      bind:termsAccepted
      bind:inspectorScroll
      bind:failureNote
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
                : snapshot.runtime.active_job_id
                  ? activeStepTitle || snapshot.runtime.status_title
                  : downloadStatus
                    ? `${downloadStatus.title} · ${Math.round(downloadStatus.percent)}%`
                    : !preparing && downloadFailureLine
                      ? downloadFailureLine.title
                      : snapshot.runtime.status_title}{snapshot.runtime.active_job_id &&
          snapshot.runtime.progress > 0 &&
          !saving &&
          !cancelling
            ? ` · ${Math.round(snapshot.runtime.progress)}%`
            : ''}{queuedCount ? ` · ${queuedCount} queued` : ''}</strong
        ><span
          class="status-detail"
          role={activityDetail
            ? 'status'
            : !snapshot.runtime.active_job_id &&
                !downloadStatus &&
                !preparing &&
                downloadFailureLine
              ? 'alert'
              : undefined}
          title={booting
            ? undefined
            : snapshot.runtime.active_job_id
              ? activityDetail || stoppedNote || idleSummary || runningStatusDetail || undefined
              : downloadStatus || preparing
                ? downloadStatus?.names || undefined
                : downloadFailureLine
                  ? downloadFailureLine.tooltip || undefined
                  : stoppedNote || idleSummary || runningStatusDetail || undefined}
          >{#if booting}Opening the trusted desktop control plane.{:else if snapshot.runtime.active_job_id}{activityDetail ||
              stoppedNote ||
              idleSummary ||
              runningStatusDetail}{:else if downloadStatus || preparing}{downloadDetailText}{:else if downloadFailureLine}{downloadFailureLine.detail}{#if !downloadFailureLine.inPlan}{' '}<button
                class="link-button"
                type="button"
                on:click={() => openLibrary('browse', undefined, downloadFailureLine.modelId)}
                >Open library</button
              >{/if}{:else}{stoppedNote || idleSummary || runningStatusDetail}{/if}</span
        >
        {#if snapshot.runtime.active_job_id && downloadStatus}<span
            class="status-note"
            title={downloadStatus.detail || undefined}
            >{downloadStatus.count === 1
              ? `Also downloading ${downloadStatus.title.slice('Downloading '.length)}`
              : `Also downloading ${downloadStatus.count} models`}</span
          >{/if}
        {#if settings.low_priority}<span class="status-note">Low priority</span>{/if}
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
      </div>{:else if downloadStatus}<div
        class="progress"
        role="progressbar"
        aria-label="Download progress"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow={Math.round(downloadStatus.percent)}
        aria-valuetext={[
          `${Math.round(downloadStatus.percent)}%`,
          downloadStatus.detail.replace(/ · /g, ', '),
        ]
          .filter(Boolean)
          .join(', ')}
      >
        <i style={`width:${downloadStatus.percent}%`}></i>
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
            title="Cancel this file's queued runs and stop its running one. Other files stay queued; finished results stay."
            on:click={() => void cancelSelectedImage()}
            >{selectedMedia?.kind === 'video'
              ? 'Cancel runs for this file'
              : 'Cancel runs for this image'}</button
          >
        {/if}
        <button class="button danger" disabled={cancelling} on:click={cancelWork}
          >{cancelling ? 'Stopping…' : inflightJobCount > 1 ? 'Cancel queue' : 'Cancel'}</button
        >
      {:else if preparing}
        <button
          class="button danger start"
          title="Stops the download. What is already downloaded is kept, and the next download continues from there."
          on:click={() => void cancelPreparation()}
          >{preparingCancelled
            ? 'Cancelling…'
            : Math.max(activeDownloads.length, preparingPendingIds.length) > 1
              ? 'Cancel downloads'
              : 'Cancel download'}</button
        >
      {:else}
        {#if downloadStatus}<button
            class="button"
            title="Stops the download. What is already downloaded is kept, and the next download continues from there."
            on:click={() => void cancelAllDownloads()}
            >{cancellingDownloads
              ? 'Cancelling…'
              : activeDownloads.length > 1
                ? 'Cancel downloads'
                : 'Cancel download'}</button
          >{/if}
        {#if scopeChoice}
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
      videoTask,
      content: settings.content,
      quality: activeQuality,
      slot: libraryContext.slot,
      fix: libraryContext.fix,
      select: libraryContext.select,
    }}
    device={activeDevice}
    downloads={activeDownloads}
    {downloadErrors}
    {downloadFailures}
    {downloadDetails}
    cancelDownload={cancelDownloadById}
    {retryDownload}
    {dismissDownloadError}
    busy={settingsLocked}
    folderBusy={settingsLocked || preparing}
    modelDirectoryMissing={Boolean(snapshot.model_directory_missing)}
    {chooseModelFolder}
    {resetModelFolder}
    close={closeLibrary}
    {trialMedia}
    {trialBase}
    trialBusy={startingJob || preparing}
    {runTrials}
    useForJob={useModelForJob}
    useVideoForJob={useVideoModelForJob}
    rememberFix={rememberFixModel}
    forgetFix={forgetFixModel}
    {useEditModel}
    pin={pinPreset}
    download={(model, accepted) => runDownload(model, accepted ?? false)}
    importFile={(model, accepted) => runDownload(model, accepted)}
    remove={removeModel}
    chooseCustom={chooseCustomModel}
    {selectCustomModel}
    {renameCustomModel}
    openCustomEdit={() => (customEditOpen = true)}
    openLicense={openAnyLicense}
    openSource={openAnySource}
  />
{/if}

{#if settingsOpen}
  <SettingsSheet
    settings={defaultSettings}
    capabilities={snapshot.capabilities}
    engines={snapshot.engines}
    section={settingsSection}
    focusTarget={settingsFocusTarget}
    {settingsLocked}
    defaultModelDirectory={snapshot.default_model_directory ?? ''}
    modelDirectoryMissing={Boolean(snapshot.model_directory_missing)}
    {movingModels}
    {modelMoveProgress}
    {cacheSize}
    cacheBusy={clearingCache ||
      settingsLocked ||
      Boolean(snapshot.runtime.active_job_id) ||
      Boolean(snapshot.runtime.active_batch_job_ids?.length)}
    linuxMenuAvailable={Boolean(snapshot.linux_menu_available)}
    linuxMenuOffer={Boolean(snapshot.linux_menu_offer)}
    linuxMenuInstalled={Boolean(snapshot.linux_menu_installed)}
    {linuxMenuBusy}
    setPreference={(name, value) => void setPreference(name, value)}
    updateSettings={updateDefaults}
    {chooseModelFolder}
    {resetModelFolder}
    clearCache={askClearCache}
    loadCacheSize={() => void loadCacheSize()}
    refreshCapabilities={async () => {
      try {
        await api.refreshCapabilities();
        return true;
      } catch (error) {
        showModal('Could not refresh the hardware', String(error));
        return false;
      }
    }}
    onOpenEngineManager={() => void openEngineManager()}
    onOpenIntegrations={() => void showIntegrations()}
    onOpenUpdates={() => updateDialogOpen.set(true)}
    onInstallLinuxMenu={acceptLinuxMenu}
    onUninstallLinuxMenu={removeLinuxMenu}
    onClose={() => {
      settingsOpen = false;
      settingsFocusTarget = null;
    }}
  />
{/if}

{#if customEditOpen}
  <CustomEditModelModal
    initial={settings.custom_edit_model}
    {editRuntime}
    chooseRuntime={chooseEditRuntime}
    saved={async () => {
      await refresh();
      customEditOpen = false;
      libraryOpen = false;
      termsAccepted = {};
    }}
    close={() => (customEditOpen = false)}
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

{#if crashNotice && settings.crash_reports && !reportOpen && !booting && settings.privacy_choice_made !== false}
  <div class="modal-backdrop" role="presentation">
    <div
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="crash-title"
      tabindex="-1"
      use:modal={() => (crashNotice = false)}
    >
      <h2 id="crash-title">LocalSR quit unexpectedly last time.</h2>
      <p>
        A crash report is stored on this computer. Review it before choosing whether to open an
        issue.
      </p>
      <div class="modal-actions">
        <button class="button" on:click={discardCrash}>Don't Send</button>
        <button class="button primary" on:click={reviewCrash}>Review report…</button>
      </div>
    </div>
  </div>
{/if}

{#if reportOpen}
  <div class="modal-backdrop" role="presentation">
    <div
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="report-title"
      tabindex="-1"
      use:modal={closeReport}
    >
      <h2 id="report-title">Report a problem</h2>
      <p>
        This opens a new GitHub issue in your browser. Nothing is sent until you submit it there.
      </p>
      <label>What happened?<textarea maxlength="400" bind:value={reportHappened}></textarea></label>
      <p>
        <label
          ><input type="checkbox" bind:checked={reportIncludeDiagnostics} /> Include diagnostics</label
        >
      </p>
      {#if reportIncludeDiagnostics}<details open={!!reviewedCrash}>
          <summary>Preview diagnostics</summary><textarea readonly value={reportDiagnostics}
          ></textarea>
        </details>{/if}
      {#if reportMessage}<p role="status">{reportMessage}</p>{/if}
      <div class="modal-actions">
        <button class="button" on:click={closeReport}>Cancel</button><button
          class="button"
          disabled={reportLoading}
          on:click={openReport}>Open GitHub Issue</button
        >
      </div>
    </div>
  </div>
{/if}

{#if engineDialogOpen && snapshot.engines?.supported && !snapshot.engines.managed_by_store}
  <EngineManagerDialog
    engines={snapshot.engines}
    processing={settingsLocked || inflightMediaIds.size > 0 || Boolean(activeDownload)}
    actionBusy={engineActionBusy}
    refreshingCatalog={engineRefreshingCatalog}
    errorMessage={engineError}
    errorBackend={engineErrorBackend}
    errorAction={engineErrorAction}
    verifiedBackend={engineVerifiedBackend}
    onClose={() => {
      engineDialogOpen = false;
      engineError = '';
      engineErrorBackend = '';
      engineErrorAction = '';
      engineVerifiedBackend = '';
    }}
    onRefreshCatalog={() => void refreshEngineCatalogList()}
    onDownload={(backend) => void downloadEngine(backend)}
    onCancelDownload={() => void cancelEngineDownload()}
    onUse={(backend) => void activateEngine(backend)}
    onVerify={(id) => void verifyInstalledEngine(id)}
    onRemove={(id) => void deleteEngine(id)}
    onImport={() => void importEngineFromFile()}
  />
{/if}

{#if !booting && settings.privacy_choice_made === false}
  <SetupScreen
    bind:appearance={setupAppearance}
    bind:colourBlind={setupColourBlind}
    bind:contrast={setupContrast}
    bind:sound={setupSound}
    storeBuild={Boolean($updateState.managed_by_store)}
    bind:checkAtLaunch={setupCheckAtLaunch}
    bind:anonymousCount={setupAnonymousCount}
    bind:crashReports={setupCrashReports}
    bind:menuBar={setupMenuBar}
    saving={privacySaving}
    error={privacyError}
    on:preview={(event) =>
      previewSetupTheme(event.detail.appearance, event.detail.colourBlind, event.detail.contrast)}
    on:finish={(event) => void finishSetup(event.detail)}
  />
{/if}

{#if stopPrompt}
  <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
  <div
    class="modal-backdrop"
    role="presentation"
    on:click={(event) => {
      if (event.target === event.currentTarget) closeStopModal();
    }}
  >
    <div
      class="modal stop-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="stop-modal-title"
      tabindex="-1"
      use:modal={closeStopModal}
    >
      <h2 id="stop-modal-title">Stop {stopPrompt.fileName}?</h2>
      <p class="modal-message">{stopPromptBody}</p>
      <label class="stop-dont-ask">
        <input type="checkbox" bind:checked={stopDontAskAgain} />
        <span>Don't ask again</span>
      </label>
      <div class="modal-actions">
        <button type="button" class="button" on:click={() => void confirmStopModal(false)}
          >Discard</button
        >
        <button
          type="button"
          class="button primary"
          data-autofocus
          on:click={() => void confirmStopModal(true)}>Keep finished part</button
        >
      </div>
    </div>
  </div>
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
        <section class="crash-reports-panel" aria-label="Crash reports">
          {#if !settings.crash_reports}
            <div class="crash-reports-row">
              <span>Crash reports are off. Turn them on in Settings.</span>
              <button
                type="button"
                class="button"
                on:click={() => {
                  closeModal();
                  openSettings('privacy');
                }}>Open Settings</button
              >
            </div>
          {/if}
          <div class="crash-reports-row">
            <span>Stored crash reports: {crashReports.length}</span>
            <div class="crash-reports-actions">
              <button class="button" disabled={!crashReports.length} on:click={reviewCrash}
                >Review</button
              >
              <button class="button" disabled={!crashReports.length} on:click={removeAllCrashes}
                >Delete All</button
              >
            </div>
          </div>
        </section>
        <section class="benchmark-panel" aria-labelledby="benchmark-title">
          <div class="benchmark-heading">
            <div>
              <span class="eyebrow">Benchmark v3</span>
              <h3 id="benchmark-title">CPU & GPU Benchmarks</h3>
            </div>
            <span class="benchmark-private">100% local</span>
          </div>
          <p class="benchmark-intro">
            Measures every available processor side by side on your downloaded Quick and Best
            Quality preset models. Each device’s output is checked against the CPU before its speed
            counts.
          </p>

          {#if benchmarkMissingPresets.length && benchmarkInstalledPresets.length}
            <p class="benchmark-intro">
              Missing preset model{benchmarkMissingPresets.length > 1 ? 's' : ''}: {benchmarkMissingPresets
                .map((entry) => `${entry.model.name} (${entry.label})`)
                .join(', ')}. Only downloaded models take part.
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
                  : benchmarkSetup === 'starting'
                    ? 'Starting benchmark…'
                    : 'Run Benchmark'}</button
            >
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
            <p role="status">Starting the benchmark across all available devices…</p>
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
            {#if snapshot.latest_benchmark.workload_version.startsWith('localsr-benchmark-v3')}
              <div class:unstable={!snapshot.latest_benchmark.stable} class="benchmark-hero">
                <div class="benchmark-score-block">
                  <div class="benchmark-score-label">
                    <span>Recommended device</span>
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
                  {#if snapshot.latest_benchmark.recommended_device_name && snapshot.latest_benchmark.recommended_device_seconds_per_input_mp != null}
                    <div class="benchmark-score-value">
                      <strong
                        >{snapshot.latest_benchmark.recommended_device_seconds_per_input_mp.toFixed(
                          1,
                        )}</strong
                      >
                      <span>s / input MP</span>
                    </div>
                    <p>
                      {snapshot.latest_benchmark.recommended_device_name} · {benchmarkOneMpExample(
                        snapshot.latest_benchmark.recommended_device_seconds_per_input_mp,
                        snapshot.latest_benchmark.scale || 4,
                      )}
                    </p>
                  {:else}
                    <div class="benchmark-score-value text-score">
                      <strong>No verified device</strong>
                    </div>
                    <p>No device matched the CPU output within the accuracy threshold.</p>
                  {/if}
                </div>

                <div class="benchmark-comparison">
                  <span>Recommended CPU threads</span>
                  <strong>{snapshot.latest_benchmark.recommended_cpu_threads ?? '—'}</strong>
                  {#if snapshot.latest_benchmark.cpu_threads?.tested}
                    <b>
                      {snapshot.latest_benchmark.cpu_threads.physical_cores} physical ({snapshot.latest_benchmark.cpu_threads.physical_seconds_per_input_mp?.toFixed(
                        1,
                      )} s/MP) vs {snapshot.latest_benchmark.cpu_threads.logical_cores} logical ({snapshot.latest_benchmark.cpu_threads.logical_seconds_per_input_mp?.toFixed(
                        1,
                      )} s/MP)
                    </b>
                  {:else if snapshot.latest_benchmark.cpu_threads}
                    <b>
                      {snapshot.latest_benchmark.cpu_threads.physical_cores} physical / {snapshot
                        .latest_benchmark.cpu_threads.logical_cores} logical cores
                    </b>
                  {:else}
                    <b>Lower s / input MP is faster</b>
                  {/if}
                </div>
              </div>

              <dl class="benchmark-facts">
                <div>
                  <dt>Consistency</dt>
                  <dd class:warning-value={!snapshot.latest_benchmark.stable}>
                    {snapshot.latest_benchmark.cv_percent == null
                      ? 'Too few repetitions'
                      : `${snapshot.latest_benchmark.cv_percent.toFixed(1)}% spread`}
                  </dd>
                </div>
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
                  <dt>CPU tensor layout</dt>
                  <dd>
                    {snapshot.latest_benchmark.cpu_avx2 === false
                      ? 'NCHW (pre-AVX2)'
                      : (snapshot.latest_benchmark.cpu_memory_format ?? 'nchw').toUpperCase()}
                  </dd>
                </div>
              </dl>

              {#if snapshot.latest_benchmark.missing_presets?.length}
                <p class="benchmark-note">
                  Skipped (not downloaded): {snapshot.latest_benchmark.missing_presets
                    .map((entry) => `${entry.model_name} (${entry.preset_label})`)
                    .join(', ')}.
                </p>
              {/if}

              {#each snapshot.latest_benchmark.benchmarked_models?.length ? snapshot.latest_benchmark.benchmarked_models : [{ preset: 'quick', preset_label: 'Preset', model_id: snapshot.latest_benchmark.model_id, model_name: snapshot.latest_benchmark.model_name, scale: snapshot.latest_benchmark.scale || 4 }] as modelSummary (modelSummary.model_id)}
                {@const modelDevices = benchmarkScores.filter(
                  (entry) => !entry.model_id || entry.model_id === modelSummary.model_id,
                )}
                {#if modelDevices.length}
                  <div class="benchmark-section-title">
                    <strong>{modelSummary.preset_label} · {modelSummary.model_name}</strong>
                    <span
                      >{modelSummary.recommended_device_name &&
                      modelSummary.recommended_seconds_per_input_mp != null
                        ? `Fastest: ${modelSummary.recommended_device_name} · ${benchmarkOneMpExample(modelSummary.recommended_seconds_per_input_mp, modelSummary.scale)}`
                        : 'Seconds per input megapixel (lower is faster)'}</span
                    >
                  </div>
                  <div
                    class="benchmark-devices"
                    aria-label={`${modelSummary.model_name} per-device benchmark results`}
                  >
                    {#each modelDevices as device (`${modelSummary.model_id}:${device.device}`)}
                      {@const mismatch =
                        device.matched_cpu === false || device.status === 'mismatch'}
                      {@const validSpeed = !mismatch && device.seconds_per_input_mp != null}
                      <article
                        class="benchmark-device"
                        class:cpu={device.device_type === 'cpu'}
                        class:recommended={Boolean(device.recommended)}
                        aria-label={`${device.device_name} benchmark result`}
                      >
                        <div class="benchmark-device-heading">
                          <div>
                            <span
                              >{device.recommended
                                ? 'Recommended'
                                : device.device_type === 'cpu'
                                  ? 'CPU'
                                  : device.device_type === 'coreml'
                                    ? 'Neural Engine'
                                    : device.device_type === 'npu'
                                      ? 'NPU'
                                      : 'GPU'}</span
                            ><strong><FileName name={device.device_name} /></strong>
                          </div>
                          <div class="benchmark-device-score">
                            {#if mismatch}
                              <strong class="warning-value">No score</strong>
                              <span>did not match the CPU</span>
                            {:else if validSpeed && device.seconds_per_input_mp != null}
                              <strong>{device.seconds_per_input_mp.toFixed(1)}</strong>
                              <span>s / input MP</span>
                            {:else}
                              <strong>—</strong>
                              <span>{device.status_note ?? 'Unavailable'}</span>
                            {/if}
                          </div>
                        </div>
                        <div class="benchmark-device-track" aria-hidden="true">
                          <i
                            style={`width:${validSpeed ? benchmarkSpeedBarWidth(device.seconds_per_input_mp, modelDevices) : 0}%`}
                          ></i>
                        </div>
                        {#if validSpeed && device.seconds_per_input_mp != null}
                          <div class="benchmark-device-example">
                            {benchmarkOneMpExample(
                              device.seconds_per_input_mp,
                              modelSummary.scale || 4,
                            )}
                          </div>
                        {/if}
                        <div class="benchmark-device-meta">
                          {#if mismatch}
                            <span class="warning-value">did not match the CPU</span>
                            {#if device.psnr_db != null}
                              <span>{device.psnr_db.toFixed(1)} dB</span>
                            {/if}
                          {:else if validSpeed}
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
                            {#if device.psnr_db != null}
                              <span>{device.psnr_db.toFixed(1)} dB vs CPU</span>
                            {:else if device.device_type !== 'cpu' && device.matched_cpu}
                              <span>Exact CPU match</span>
                            {/if}
                            <span>{device.thermal_state}</span>
                          {:else}
                            <span class="warning-value"
                              >{device.status_note ?? device.status ?? 'Skipped'}</span
                            >
                          {/if}
                        </div>
                        {#if device.scenes.length}
                          <details class="benchmark-scenes">
                            <summary>Scene breakdown</summary>
                            {#each device.scenes as scene (scene.scene_id)}
                              <div class="benchmark-scene">
                                <span class="scene-name">{benchmarkSceneLabel(scene.scene_id)}</span
                                >
                                <strong
                                  >{scene.seconds_per_input_mp != null
                                    ? `${scene.seconds_per_input_mp.toFixed(1)} s/MP`
                                    : `${scene.megapixels_per_second.toFixed(2)} MP/s`}</strong
                                >
                                <span class="scene-metric">{scene.median_ms.toFixed(0)} ms</span>
                                <span class="scene-metric subtle">{scene.iterations} runs</span>
                              </div>
                            {/each}
                          </details>
                        {/if}
                      </article>
                    {/each}
                  </div>
                {/if}
              {/each}
              <p class="benchmark-note">
                Lower seconds per input megapixel is faster. Devices that fail the CPU output check
                receive no score and are not recommended.
              </p>
            {:else if snapshot.latest_benchmark.workload_version.startsWith('localsr-benchmark-v2')}
              <div class:unstable={!snapshot.latest_benchmark.stable} class="benchmark-hero">
                <div class="benchmark-score-block">
                  <div class="benchmark-score-label">
                    <span
                      >Latest {snapshot.latest_benchmark.device_results?.[0]?.device_type === 'cpu'
                        ? 'CPU'
                        : 'GPU'} score · Older v2 benchmark</span
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
                  <dd>Older v2 benchmark · SPAN</dd>
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
                This is an older v2 benchmark (SPAN only). Run the benchmark again to compare every
                device on your current Quick and Best preset models.
              </p>
            {:else}
              <div class="benchmark-hero legacy-score">
                <div class="benchmark-score-block">
                  <div class="benchmark-score-label">
                    <span>Legacy score</span><span class="benchmark-status-pill"
                      >Older v1 benchmark</span
                    >
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
                This is an older v1 result. Run the benchmark again to compare every device on your
                current Quick and Best preset models.
              </p>
            {/if}
          {:else if !benchmarkRunning}
            <div class="benchmark-empty">
              <div class="benchmark-empty-gauge" aria-hidden="true"><i></i></div>
              <div>
                <strong>No benchmark result yet</strong><span
                  >Run the v3 benchmark to measure every processor and recommend the fastest one.</span
                >
              </div>
            </div>
          {/if}
        </section>

        {#if benchmarkRunning}
          <div class="performance-section-heading">
            <strong>Benchmark scope</strong><span>All available devices</span>
          </div>
          <dl class="performance-grid">
            <div>
              <dt>Devices</dt>
              <dd>{snapshot.capabilities.devices.map((device) => device.name).join(', ')}</dd>
            </div>
            <div>
              <dt>Preset models</dt>
              <dd>{benchmarkInstalledPresets.map((entry) => entry.model.name).join(', ')}</dd>
            </div>
            <div>
              <dt>Timing and memory</dt>
              <dd>Measurements appear in the completed results.</dd>
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
        {#if modalKind === 'performance'}<button class="button" on:click={showReport}
            >Report a problem…</button
          ><button class="button" on:click={copyDiagnostics}>Copy diagnostics</button>{/if}
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
