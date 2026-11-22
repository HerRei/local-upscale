import { convertFileSrc, invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { join } from '@tauri-apps/api/path';
import { demoSnapshot } from './demo';
import type {
  AppSnapshot,
  CustomEditModel,
  EngineCatalogEntry,
  EngineDownloadStatus,
  IntegrationStatus,
  LaunchIntent,
  ModelMoveProgress,
  Recipe,
  StartBatchInput,
  UiSettings,
  VideoComparisonSources,
  VideoComparisonProgress,
  WorkerEnvelope,
  StartEditInput,
  MediaResults,
  TrialEstimate,
  ComparePick,
} from './types';

export const isTauri = (): boolean => '__TAURI_INTERNALS__' in window;

export async function bootstrap(): Promise<AppSnapshot> {
  if (!isTauri()) return demoSnapshot();
  return invoke<AppSnapshot>('bootstrap');
}

export async function refreshSnapshot(): Promise<AppSnapshot> {
  if (!isTauri()) return demoSnapshot();
  return invoke<AppSnapshot>('get_snapshot');
}

export async function chooseMediaFiles(): Promise<string[]> {
  if (!isTauri()) return [];
  return invoke<string[]>('choose_media_files');
}

export async function chooseMediaFolder(): Promise<{
  paths: string[];
  outputDirectory: string;
} | null> {
  if (!isTauri()) return null;
  const selection = await invoke<string | null>('choose_media_folder');
  if (!selection) return null;
  const paths = await invoke<string[]>('scan_media_folder', { path: selection });
  return { paths, outputDirectory: await join(selection, 'LocalSR Results') };
}

export async function chooseOutputDirectory(current: string): Promise<string | null> {
  if (!isTauri()) return null;
  return invoke<string | null>('choose_output_directory', { current });
}

export async function chooseModelDirectory(current: string): Promise<string | null> {
  if (!isTauri()) return null;
  return invoke<string | null>('choose_model_directory', { current });
}

export const setModelDirectory = (path: string): Promise<void> =>
  invoke('set_model_directory', { path });

export const resetModelDirectory = (): Promise<void> => invoke('reset_model_directory');

export async function listenForModelMove(
  handler: (progress: ModelMoveProgress) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen<ModelMoveProgress>('model-move-progress', (event) => handler(event.payload));
}

export async function dismissGpuDownloadNotice(): Promise<void> {
  if (isTauri()) await invoke('dismiss_gpu_download_notice');
}

export async function dismissLinuxMenuPrompt(): Promise<void> {
  if (isTauri()) await invoke('dismiss_linux_menu_prompt');
}

export async function installLinuxMenu(): Promise<void> {
  if (isTauri()) await invoke('install_linux_menu');
}

export async function uninstallLinuxMenu(): Promise<void> {
  if (isTauri()) await invoke('uninstall_linux_menu');
}

export async function refreshEngineCatalog(channel?: string | null): Promise<EngineCatalogEntry[]> {
  if (!isTauri()) return [];
  return invoke<EngineCatalogEntry[]>('refresh_engine_catalog', { channel: channel ?? null });
}

export const installEngine = (backend: string): Promise<void> =>
  invoke('install_engine', { backend });

export const cancelEngineInstall = (): Promise<void> => invoke('cancel_engine_install');

export const useEngine = (backend: string): Promise<void> => invoke('use_engine', { backend });

export const removeEngine = (id: string): Promise<void> => invoke('remove_engine', { id });

export async function chooseEngineManifest(): Promise<string | null> {
  if (!isTauri()) return null;
  return invoke<string | null>('choose_engine_manifest');
}

export const importEngine = (path: string): Promise<void> => invoke('import_engine', { path });

export async function listenForEngineStatus(
  handler: (status: EngineDownloadStatus) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen<EngineDownloadStatus>('engine-status', (event) => handler(event.payload));
}

export async function chooseCustomModel(): Promise<string | null> {
  if (!isTauri()) return null;
  return invoke<string | null>('choose_custom_model');
}

export const addMedia = (paths: string[], replace: boolean): Promise<void> =>
  invoke('add_media', { paths, replace });
export const selectMedia = (id: string): Promise<void> => invoke('select_media', { id });
export const removeMedia = (id: string): Promise<void> => invoke('remove_media', { id });
export const clearMedia = (): Promise<void> => invoke('clear_media');
export const clearResults = (): Promise<void> => invoke('clear_results');
export const saveSettings = (settings: UiSettings): Promise<void> =>
  invoke('save_settings', { settings });

/** One update switch, saved by the host on its own: a full settings save never changes it. */
export const setUpdateSwitch = (
  name: 'check_updates_at_launch' | 'anonymous_update_count' | 'crash_reports' | 'low_priority',
  value: boolean,
): Promise<void> => invoke('set_update_switch', { name, value });
export interface SetupChoices {
  appearance: UiSettings['appearance'];
  colourBlindPalette: boolean;
  checkAtLaunch: boolean;
  anonymousCount: boolean;
  crashReports: boolean;
}

export const setPrivacyChoice = (choices: SetupChoices): Promise<void> =>
  invoke('set_privacy_choice', { ...choices });
export const startJobs = (input: StartBatchInput): Promise<void> => invoke('start_jobs', { input });
export const startEdit = (input: StartEditInput): Promise<void> => invoke('start_edit', { input });
export const openEditLink = (modelId: string, link: 'license' | 'source'): Promise<void> =>
  invoke('open_edit_link', { modelId, link });
/** The host's own file dialog for the editing runtime; the host remembers the pick. */
export async function chooseEditRuntime(): Promise<string | null> {
  if (!isTauri()) return null;
  return invoke<string | null>('choose_edit_runtime');
}
export interface EditModelFilePick {
  path: string;
  size_bytes: number;
  detected_family?: string | null;
}
export async function chooseEditModelFile(
  role: 'diffusion' | 'text_encoder' | 'vision' | 'vae',
): Promise<EditModelFilePick | null> {
  if (!isTauri()) return null;
  return invoke<EditModelFilePick | null>('choose_edit_model_file', { role });
}
export const saveCustomEditModel = (custom: CustomEditModel, runtimePath: string): Promise<void> =>
  invoke('save_custom_edit_model', { custom, runtimePath });
export const retryEngine = (): Promise<void> => invoke('retry_engine');
export const cancelJobs = (): Promise<void> => invoke('cancel_jobs');
export const startBenchmark = (device: string): Promise<void> =>
  invoke('start_benchmark', { input: { device } });
export const exportBenchmark = async (): Promise<boolean> => {
  if (!isTauri()) return false;
  const destination = await invoke<string | null>('choose_benchmark_export');
  if (!destination) return false;
  await invoke('export_benchmark', { destination });
  return true;
};
/** Suggest FFmpeg executables installed on this computer; LocalSR never bundles one. */
export async function detectExternalFFmpeg(): Promise<string[]> {
  if (!isTauri()) return [];
  return invoke<string[]>('detect_external_ffmpeg');
}

export async function openUserGuide(): Promise<void> {
  if (isTauri()) await invoke('open_user_guide');
}

/** LocalSR's download page, for the editions that use a GPU this one cannot (CUDA, DirectML, ROCm). */
export async function openDownloadPage(): Promise<void> {
  if (isTauri()) await invoke('open_download_page');
}

/** In-app tutorial for loading a custom checkpoint file. */
export async function openCustomModelTutorial(): Promise<void> {
  if (isTauri()) await invoke('open_custom_model_tutorial');
}

/** Open ffmpeg.org's download page; installing FFmpeg stays the user's own action. */
export async function openFfmpegDownloadPage(): Promise<void> {
  if (isTauri()) await invoke('open_ffmpeg_download_page');
}

export interface FfmpegInstallHint {
  command: string;
  note: string;
  system: string;
}

export async function ffmpegInstallHint(): Promise<FfmpegInstallHint | null> {
  if (!isTauri()) return null;
  return invoke<FfmpegInstallHint>('ffmpeg_install_hint');
}

/** Open a terminal with the install command typed in; the user runs it. */
export async function openTerminalWithInstallCommand(command: string): Promise<void> {
  if (isTauri()) await invoke('open_terminal_with_install_command', { command });
}

export async function chooseExternalFFmpeg(current: string): Promise<string | null> {
  if (!isTauri()) return null;
  return invoke<string | null>('choose_external_ffmpeg', { current });
}

export const prepareVideoComparison = async (
  mediaId: string,
  requestId: string,
  forceCompatible = false,
  resultId?: string,
  deferConversion = false,
): Promise<VideoComparisonSources> => {
  const paths = await invoke<{
    playback_note: string;
    original_path: string;
    enhanced_path: string;
    original_url?: string | null;
    enhanced_url?: string | null;
  }>('prepare_video_comparison', {
    mediaId,
    requestId,
    forceCompatible,
    resultId,
    deferConversion,
  });
  return {
    playback_note: paths.playback_note,
    original_url: paths.original_url ?? convertFileSrc(paths.original_path),
    // Empty before a job finishes: the player then shows the source alone.
    enhanced_url:
      paths.enhanced_url ?? (paths.enhanced_path ? convertFileSrc(paths.enhanced_path) : ''),
  };
};
/** A slot index and generation, never a path, selects a host-authorised video. */
export const prepareCompareVideo = async (
  generation: number,
  slot: number,
  requestId: string,
  forceCompatible = false,
  deferConversion = false,
): Promise<VideoComparisonSources> => {
  const paths = await invoke<{
    original_path: string;
    original_url?: string | null;
    playback_note: string;
  }>('prepare_video_comparison', {
    mediaId: '',
    requestId,
    forceCompatible,
    deferConversion,
    compareGeneration: generation,
    compareSlot: slot,
  });
  return {
    original_url: paths.original_url ?? convertFileSrc(paths.original_path),
    enhanced_url: '',
    playback_note: paths.playback_note,
  };
};
export const cancelVideoComparison = (requestId: string): Promise<void> =>
  invoke('cancel_video_comparison', { requestId });
export const listenVideoComparisonProgress = (
  callback: (data: VideoComparisonProgress) => void,
): Promise<UnlistenFn> =>
  listen<VideoComparisonProgress>('video-comparison-progress', (event) => callback(event.payload));
export const refreshCapabilities = (): Promise<void> => invoke('refresh_capabilities');
/** With a pair, both targets ("original" or a job id of this image) are decoded
 * into the runtime's pair slots; without one, the newest result is decoded alone. */
export const requestImageComparison = (
  mediaId: string,
  pair?: { showing: string; against: string },
): Promise<void> =>
  pair
    ? invoke('request_image_comparison', { mediaId, showing: pair.showing, against: pair.against })
    : invoke('request_image_comparison', { mediaId });
export const listAllMediaResults = (): Promise<MediaResults[]> => invoke('list_all_media_results');
export const requestCompare = (picks: ComparePick[]): Promise<void> =>
  invoke('request_compare', { picks });
export const listMediaResults = (
  mediaId: string,
  limit = 50,
  before: string | null = null,
): Promise<MediaResults> => invoke('list_media_results', { mediaId, limit, before });
export const startModelTrials = (
  mediaId: string,
  modelIds: string[],
  baseSettings: StartBatchInput,
): Promise<string[]> => invoke('start_model_trials', { mediaId, modelIds, baseSettings });
export const estimateModelTrials = (
  mediaId: string,
  modelIds: string[],
  baseSettings: StartBatchInput,
): Promise<TrialEstimate[]> => invoke('estimate_model_trials', { mediaId, modelIds, baseSettings });
export const cancelJobsForMedia = (mediaId: string): Promise<void> =>
  invoke('cancel_jobs_for_media', { mediaId });
export const probePath = (path: string): Promise<void> => invoke('probe_path', { path });
export const downloadModel = (modelId: string, acceptedTerms: boolean): Promise<void> =>
  invoke('download_model', { modelId, acceptedTerms });
export const removeModel = (modelId: string): Promise<void> => invoke('remove_model', { modelId });
export const cancelDownload = (modelId: string): Promise<void> =>
  invoke('cancel_download', { modelId });
export const importCatalogModel = (
  modelId: string,
  sourcePath: string,
  acceptedTerms: boolean,
): Promise<void> => invoke('import_catalog_model', { modelId, sourcePath, acceptedTerms });
export const saveRecipe = (recipe: Recipe): Promise<void> => invoke('save_recipe', { recipe });
export const deleteRecipe = (id: string): Promise<void> => invoke('delete_recipe', { id });
export const openResult = (path: string): Promise<void> => invoke('open_result', { path });
export const revealResult = (path: string): Promise<void> => invoke('reveal_result', { path });
/** Adds a finished result as an input and selects it; the host checks the path like Reveal does. */
export const useResultAsInput = (path: string): Promise<string> =>
  invoke('use_result_as_input', { path });
export const openBugReport = (url: string): Promise<void> => invoke('open_bug_report', { url });
export const diagnosticSummary = (): Promise<string> => invoke('diagnostic_summary');
export const openOutputDirectory = (): Promise<void> => invoke('open_output_directory');
export const takeLaunchIntents = (): Promise<LaunchIntent[]> => invoke('take_launch_intents');
export const integrationStatus = (): Promise<IntegrationStatus> => invoke('integration_status');
export const installIntegrations = (): Promise<IntegrationStatus> => invoke('install_integrations');
export const uninstallIntegrations = (): Promise<IntegrationStatus> =>
  invoke('uninstall_integrations');

export async function listenForWorker(
  handler: (message: WorkerEnvelope) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  // The host forwards a message the engine sent without data as null.
  return listen<WorkerEnvelope>('worker-message', (event) =>
    handler({ ...event.payload, data: event.payload.data ?? {} }),
  );
}

export async function listenForStateChange(handler: () => void): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen('state-changed', handler);
}

export async function listenForNativeMenu(handler: (action: string) => void): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen<string>('native-menu-action', (event) => handler(event.payload));
}

/** Files dragged from Finder, Explorer or a file manager onto the LocalSR window. */
export async function listenForFileDrops(handlers: {
  hover: (active: boolean) => void;
  drop: (paths: string[]) => void;
}): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  const { getCurrentWebview } = await import('@tauri-apps/api/webview');
  return getCurrentWebview().onDragDropEvent((event) => {
    const payload = event.payload;
    if (payload.type === 'enter' || payload.type === 'over') handlers.hover(true);
    else if (payload.type === 'leave') handlers.hover(false);
    else if (payload.type === 'drop') {
      handlers.hover(false);
      if (payload.paths.length) handlers.drop(payload.paths);
    }
  });
}

export async function listenForLaunchIntent(handler: () => void): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen('launch-intent-available', handler);
}

export async function openModelLicense(modelId: string): Promise<void> {
  if (isTauri()) await invoke('open_model_license', { modelId });
}

export async function openModelSource(modelId: string): Promise<void> {
  if (isTauri()) await invoke('open_model_license', { modelId, source: true });
}

export interface UpdateStatus {
  configured: boolean;
  managed_by_store: boolean;
  channel: string;
  target: string;
  stage: string;
  version: string;
  notes: string;
  discover_url?: string;
  skipped_version?: string;
  size: number;
  downloaded: number;
  message: string;
  settings_recovery: boolean;
}
export const updateStatus = (): Promise<UpdateStatus> => invoke('update_status');
export const skipUpdate = (): Promise<UpdateStatus> => invoke('skip_update');
export const openUpdateDiscover = (): Promise<void> => invoke('open_update_discover');
export const openStoreUpdates = (): Promise<void> => invoke('open_store_updates');
export const checkUpdate = (channel: string): Promise<UpdateStatus> =>
  invoke('check_update', { channel });
export const checkUpdateAtLaunch = (channel: string): Promise<UpdateStatus> =>
  invoke('check_update_at_launch', { channel });
export const downloadUpdate = (): Promise<void> => invoke('download_update');
export const installUpdate = (): Promise<void> => invoke('install_update');
export const cancelUpdate = (): Promise<void> => invoke('cancel_update');
export const discardUpdate = (): Promise<void> => invoke('discard_update');
export const recoverUpdateSettings = (): Promise<void> => invoke('recover_update_settings');
export const listenForUpdates = (callback: (status: UpdateStatus) => void): Promise<UnlistenFn> =>
  listen<UpdateStatus>('update-status', (event) => callback(event.payload));

export const requestCompareRegions = (
  generation: number,
  regions: (import('./compareRegions').Region | null)[],
): Promise<void> => invoke('request_compare_regions', { generation, regions });
export const clearCompareRegions = (): Promise<void> => invoke('clear_compare_regions');

export interface StoredCrashReport {
  id: string;
  report: { message: string; os: string; [key: string]: unknown };
}
export const listCrashReports = (): Promise<StoredCrashReport[]> =>
  isTauri() ? invoke('list_crash_reports') : Promise.resolve([]);
export const deleteCrashReport = (id: string): Promise<void> =>
  invoke('delete_crash_report', { id });
export const deleteAllCrashReports = (): Promise<void> => invoke('delete_all_crash_reports');
export const recordFrontendError = (message: string, stack: string): Promise<void> =>
  isTauri() ? invoke('record_frontend_error', { message, stack }) : Promise.resolve();
