import { convertFileSrc, invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { join } from '@tauri-apps/api/path';
import { open, save } from '@tauri-apps/plugin-dialog';
import { demoSnapshot } from './demo';
import type {
  AppSnapshot,
  IntegrationStatus,
  LaunchIntent,
  Recipe,
  StartBatchInput,
  UiSettings,
  VideoComparisonSources,
  VideoComparisonProgress,
  WorkerEnvelope,
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
  const selection = await open({
    multiple: true,
    directory: false,
    filters: [
      {
        name: 'Images and video',
        extensions: [
          'png',
          'jpg',
          'jpeg',
          'webp',
          'bmp',
          'tif',
          'tiff',
          'dng',
          'mp4',
          'mov',
          'm4v',
          'avi',
          'mkv',
          'webm',
          'mpg',
          'mpeg',
          'mpe',
          'vob',
          'ts',
          'mts',
          'm2ts',
          'wmv',
          'asf',
          'flv',
          'f4v',
          '3gp',
          '3g2',
          'ogv',
          'divx',
        ],
      },
    ],
  });
  if (!selection) return [];
  return Array.isArray(selection) ? selection : [selection];
}

export async function chooseMediaFolder(): Promise<{
  paths: string[];
  outputDirectory: string;
} | null> {
  if (!isTauri()) return null;
  const selection = await open({ multiple: false, directory: true });
  if (!selection || Array.isArray(selection)) return null;
  const paths = await invoke<string[]>('scan_media_folder', { path: selection });
  return { paths, outputDirectory: await join(selection, 'LocalSR Results') };
}

export async function chooseOutputDirectory(current: string): Promise<string | null> {
  if (!isTauri()) return null;
  const selection = await open({
    multiple: false,
    directory: true,
    defaultPath: current || undefined,
  });
  return typeof selection === 'string' ? selection : null;
}

export async function chooseCustomModel(): Promise<string | null> {
  if (!isTauri()) return null;
  const selection = await open({
    multiple: false,
    directory: false,
    filters: [{ name: 'Model checkpoints', extensions: ['safetensors', 'pth', 'pt', 'ckpt'] }],
  });
  return typeof selection === 'string' ? selection : null;
}

export const addMedia = (paths: string[], replace: boolean): Promise<void> =>
  invoke('add_media', { paths, replace });
export const selectMedia = (id: string): Promise<void> => invoke('select_media', { id });
export const removeMedia = (id: string): Promise<void> => invoke('remove_media', { id });
export const clearMedia = (): Promise<void> => invoke('clear_media');
export const saveSettings = (settings: UiSettings): Promise<void> =>
  invoke('save_settings', { settings });
export const startJobs = (input: StartBatchInput): Promise<void> => invoke('start_jobs', { input });
export const cancelJobs = (): Promise<void> => invoke('cancel_jobs');
export const startBenchmark = (device: string): Promise<void> =>
  invoke('start_benchmark', { input: { device } });
export const exportBenchmark = async (): Promise<boolean> => {
  if (!isTauri()) return false;
  const destination = await save({
    defaultPath: 'localsr-benchmark-v1.json',
    filters: [{ name: 'JSON', extensions: ['json'] }],
  });
  if (!destination) return false;
  await invoke('export_benchmark', { destination });
  return true;
};
/** Suggest FFmpeg executables installed on this computer; LocalSR never bundles one. */
export async function detectExternalFFmpeg(): Promise<string[]> {
  if (!isTauri()) return [];
  return invoke<string[]>('detect_external_ffmpeg');
}

export async function chooseExternalFFmpeg(current: string): Promise<string | null> {
  if (!isTauri()) return null;
  const selection = await open({
    multiple: false,
    directory: false,
    defaultPath: current || undefined,
    title: 'Select the FFmpeg program installed on this computer',
  });
  return typeof selection === 'string' ? selection : null;
}

export const prepareVideoComparison = async (
  mediaId: string,
  requestId: string,
  forceCompatible = false,
): Promise<VideoComparisonSources> => {
  const paths = await invoke<{
    playback_note: string;
    original_path: string;
    enhanced_path: string;
    original_url?: string | null;
    enhanced_url?: string | null;
  }>('prepare_video_comparison', { mediaId, requestId, forceCompatible });
  return {
    playback_note: paths.playback_note,
    original_url: paths.original_url ?? convertFileSrc(paths.original_path),
    enhanced_url: paths.enhanced_url ?? convertFileSrc(paths.enhanced_path),
  };
};
export const cancelVideoComparison = (requestId: string): Promise<void> =>
  invoke('cancel_video_comparison', { requestId });
export const listenVideoComparisonProgress = (
  callback: (data: VideoComparisonProgress) => void,
): Promise<UnlistenFn> =>
  listen<VideoComparisonProgress>('video-comparison-progress', (event) => callback(event.payload));
export const refreshCapabilities = (): Promise<void> => invoke('refresh_capabilities');
export const requestImageComparison = (mediaId: string): Promise<void> =>
  invoke('request_image_comparison', { mediaId });
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
  return listen<WorkerEnvelope>('worker-message', (event) => handler(event.payload));
}

export async function listenForStateChange(handler: () => void): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen('state-changed', handler);
}

export async function listenForNativeMenu(handler: (action: string) => void): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen<string>('native-menu-action', (event) => handler(event.payload));
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
  size: number;
  downloaded: number;
  message: string;
  settings_recovery: boolean;
}
export const updateStatus = (): Promise<UpdateStatus> => invoke('update_status');
export const openStoreUpdates = (): Promise<void> => invoke('open_store_updates');
export const checkUpdate = (channel: string): Promise<UpdateStatus> =>
  invoke('check_update', { channel });
export const downloadUpdate = (): Promise<void> => invoke('download_update');
export const installUpdate = (): Promise<void> => invoke('install_update');
export const cancelUpdate = (): Promise<void> => invoke('cancel_update');
export const discardUpdate = (): Promise<void> => invoke('discard_update');
export const recoverUpdateSettings = (): Promise<void> => invoke('recover_update_settings');
export const listenForUpdates = (callback: (status: UpdateStatus) => void): Promise<UnlistenFn> =>
  listen<UpdateStatus>('update-status', (event) => callback(event.payload));
