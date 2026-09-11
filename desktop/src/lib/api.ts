import { convertFileSrc, invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
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
  WorkerEnvelope
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
          'png', 'jpg', 'jpeg', 'webp', 'bmp', 'tif', 'tiff', 'dng',
          'mp4', 'mov', 'm4v', 'avi', 'mkv', 'webm'
        ]
      }
    ]
  });
  if (!selection) return [];
  return Array.isArray(selection) ? selection : [selection];
}

export async function chooseMediaFolder(): Promise<string[]> {
  if (!isTauri()) return [];
  const selection = await open({ multiple: false, directory: true });
  if (!selection || Array.isArray(selection)) return [];
  return invoke<string[]>('scan_media_folder', { path: selection });
}

export async function chooseOutputDirectory(current: string): Promise<string | null> {
  if (!isTauri()) return null;
  const selection = await open({ multiple: false, directory: true, defaultPath: current || undefined });
  return typeof selection === 'string' ? selection : null;
}

export async function chooseCustomModel(): Promise<string | null> {
  if (!isTauri()) return null;
  const selection = await open({
    multiple: false,
    directory: false,
    filters: [{ name: 'Model checkpoints', extensions: ['safetensors', 'pth', 'pt', 'ckpt'] }]
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
export const startJobs = (input: StartBatchInput): Promise<void> =>
  invoke('start_jobs', { input });
export const cancelJobs = (): Promise<void> => invoke('cancel_jobs');
export const startBenchmark = (device: string): Promise<void> =>
  invoke('start_benchmark', { input: { device } });
export const exportBenchmark = async (): Promise<boolean> => {
  if (!isTauri()) return false;
  const destination = await save({
    defaultPath: 'localsr-benchmark-v1.json',
    filters: [{ name: 'JSON', extensions: ['json'] }]
  });
  if (!destination) return false;
  await invoke('export_benchmark', { destination });
  return true;
};
export const prepareVideoComparison = async (mediaId: string): Promise<VideoComparisonSources> => {
  const paths = await invoke<{ original_path: string; enhanced_path: string }>(
    'prepare_video_comparison',
    { mediaId }
  );
  return {
    original_url: convertFileSrc(paths.original_path),
    enhanced_url: convertFileSrc(paths.enhanced_path)
  };
};
export const refreshCapabilities = (): Promise<void> => invoke('refresh_capabilities');
export const probePath = (path: string): Promise<void> => invoke('probe_path', { path });
export const downloadModel = (modelId: string, acceptedTerms: boolean): Promise<void> =>
  invoke('download_model', { modelId, acceptedTerms });
export const cancelDownload = (modelId: string): Promise<void> =>
  invoke('cancel_download', { modelId });
export const importCatalogModel = (
  modelId: string,
  sourcePath: string,
  acceptedTerms: boolean
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
export const uninstallIntegrations = (): Promise<IntegrationStatus> => invoke('uninstall_integrations');

export async function listenForWorker(
  handler: (message: WorkerEnvelope) => void
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
