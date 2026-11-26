// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';

const invoke = vi.hoisted(() => vi.fn());
vi.mock('@tauri-apps/api/core', () => ({ invoke, convertFileSrc: vi.fn() }));
const listen = vi.hoisted(() => vi.fn());
vi.mock('@tauri-apps/api/event', () => ({ listen }));
vi.mock('@tauri-apps/api/path', () => ({
  join: vi.fn(async (...parts: string[]) => parts.join('/')),
}));

import {
  cancelEngineInstall,
  chooseCustomModel,
  chooseEditModelFile,
  chooseEditRuntime,
  chooseEngineManifest,
  chooseExternalFFmpeg,
  chooseMediaFiles,
  chooseMediaFolder,
  chooseModelDirectory,
  chooseOutputDirectory,
  dismissGpuDownloadNotice,
  dismissLinuxMenuPrompt,
  exportBenchmark,
  importEngine,
  installEngine,
  installLinuxMenu,
  listenForEngineStatus,
  listenForModelMove,
  listenForWorker,
  refreshEngineCatalog,
  removeEngine,
  saveCustomEditModel,
  resetModelDirectory,
  setModelDirectory,
  uninstallLinuxMenu,
  useEngine,
} from './api';

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  listen.mockReset();
});

describe('worker messages from the host', () => {
  it('reach the window with an empty data object when the engine sent none', async () => {
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    let deliver: (event: { payload: unknown }) => void = () => {};
    listen.mockImplementation(async (_name: string, handler: typeof deliver) => {
      deliver = handler;
      return () => {};
    });
    const received: unknown[] = [];
    await listenForWorker((message) => received.push(message));
    deliver({ payload: { type: 'progress', data: null } });
    deliver({ payload: { type: 'worker_ready' } });
    deliver({ payload: { type: 'progress', data: { job_id: 'a' } } });
    expect(received).toEqual([
      { type: 'progress', data: {} },
      { type: 'worker_ready', data: {} },
      { type: 'progress', data: { job_id: 'a' } },
    ]);
  });
});

it('asks the host to choose and grant the external FFmpeg path', async () => {
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  invoke.mockResolvedValueOnce('/chosen/ffmpeg');
  expect(await chooseExternalFFmpeg('/old/ffmpeg')).toBe('/chosen/ffmpeg');
  expect(invoke).toHaveBeenCalledWith('choose_external_ffmpeg', { current: '/old/ffmpeg' });
});

it('asks the host to choose and grant the editing runtime', async () => {
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  invoke.mockResolvedValueOnce('/chosen/sd-cli');
  expect(await chooseEditRuntime()).toBe('/chosen/sd-cli');
  expect(invoke).toHaveBeenCalledWith('choose_edit_runtime');
  invoke.mockResolvedValueOnce(null);
  expect(await chooseEditRuntime()).toBeNull();
});

it('picks custom edit model files and asks the host to verify and save them', async () => {
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  invoke.mockResolvedValueOnce({
    path: '/models/q21.gguf',
    size_bytes: 1234,
    detected_family: 'qwen-image-2.1',
  });
  expect(await chooseEditModelFile('diffusion')).toEqual({
    path: '/models/q21.gguf',
    size_bytes: 1234,
    detected_family: 'qwen-image-2.1',
  });
  expect(invoke).toHaveBeenCalledWith('choose_edit_model_file', { role: 'diffusion' });

  invoke.mockResolvedValueOnce(null);
  const custom = {
    family: 'qwen-image-2.1',
    diffusion_path: '/models/q21.gguf',
    text_encoder_path: '/models/te.gguf',
    vision_path: '/models/mmproj.gguf',
    vae_path: '/models/vae.safetensors',
  };
  await saveCustomEditModel(custom, '/engine/sd-cli');
  expect(invoke).toHaveBeenCalledWith('save_custom_edit_model', {
    custom,
    runtimePath: '/engine/sd-cli',
  });
});

it('asks the host to pick and grant media, output folders, checkpoints, and exports', async () => {
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  invoke.mockResolvedValueOnce(['/chosen/a.png']);
  expect(await chooseMediaFiles()).toEqual(['/chosen/a.png']);
  expect(invoke).toHaveBeenCalledWith('choose_media_files');

  invoke.mockResolvedValueOnce('/chosen/folder');
  invoke.mockResolvedValueOnce(['/chosen/folder/a.png']);
  expect(await chooseMediaFolder()).toEqual({
    paths: ['/chosen/folder/a.png'],
    outputDirectory: '/chosen/folder/LocalSR Results',
  });
  expect(invoke).toHaveBeenCalledWith('choose_media_folder');
  expect(invoke).toHaveBeenCalledWith('scan_media_folder', { path: '/chosen/folder' });

  invoke.mockResolvedValueOnce('/chosen/out');
  expect(await chooseOutputDirectory('/old/out')).toBe('/chosen/out');
  expect(invoke).toHaveBeenCalledWith('choose_output_directory', { current: '/old/out' });

  invoke.mockResolvedValueOnce('/chosen/model.safetensors');
  expect(await chooseCustomModel()).toBe('/chosen/model.safetensors');
  expect(invoke).toHaveBeenCalledWith('choose_custom_model');

  invoke.mockResolvedValueOnce('/chosen/bench.json');
  invoke.mockResolvedValueOnce(null);
  expect(await exportBenchmark()).toBe(true);
  expect(invoke).toHaveBeenCalledWith('choose_benchmark_export');
  expect(invoke).toHaveBeenCalledWith('export_benchmark', { destination: '/chosen/bench.json' });
});

import { prepareCompareVideo } from './api';
it('resolves a combined Compare video descriptor by generation', async () => {
  invoke.mockResolvedValueOnce({
    video_path: '/host/combined.webm',
    video_url: 'http://127.0.0.1/combined',
    playback_note: 'Compatible SDR playback copy',
    width: 1280,
    height: 1444,
    duration: 3,
    slots: [
      {
        x: 0,
        y: 0,
        width: 1280,
        height: 720,
        fps: 30,
        duration: 3,
        frames: 90,
        audio_path: '/host/audio-0.webm',
        audio_url: 'http://127.0.0.1/audio-0',
      },
    ],
  });
  expect(await prepareCompareVideo(7, 'request', true, true)).toEqual({
    video_url: 'http://127.0.0.1/combined',
    playback_note: 'Compatible SDR playback copy',
    width: 1280,
    height: 1444,
    duration: 3,
    slots: [
      {
        x: 0,
        y: 0,
        width: 1280,
        height: 720,
        fps: 30,
        duration: 3,
        frames: 90,
        audio_url: 'http://127.0.0.1/audio-0',
      },
    ],
  });
  expect(invoke).toHaveBeenCalledWith('prepare_compare_video', {
    generation: 7,
    requestId: 'request',
    forceCompatible: true,
    deferConversion: true,
  });
});

it('forwards model folder commands, move progress events, and GPU notice dismissal', async () => {
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  invoke.mockResolvedValueOnce('/Volumes/SSD/models');
  expect(await chooseModelDirectory('/old/models')).toBe('/Volumes/SSD/models');
  expect(invoke).toHaveBeenCalledWith('choose_model_directory', { current: '/old/models' });

  invoke.mockResolvedValueOnce(undefined);
  await setModelDirectory('/Volumes/SSD/models');
  expect(invoke).toHaveBeenCalledWith('set_model_directory', { path: '/Volumes/SSD/models' });

  invoke.mockResolvedValueOnce(undefined);
  await resetModelDirectory();
  expect(invoke).toHaveBeenCalledWith('reset_model_directory');

  invoke.mockResolvedValueOnce(undefined);
  await dismissGpuDownloadNotice();
  expect(invoke).toHaveBeenCalledWith('dismiss_gpu_download_notice');

  invoke.mockResolvedValueOnce(undefined);
  await dismissLinuxMenuPrompt();
  expect(invoke).toHaveBeenCalledWith('dismiss_linux_menu_prompt');

  invoke.mockResolvedValueOnce(undefined);
  await installLinuxMenu();
  expect(invoke).toHaveBeenCalledWith('install_linux_menu');

  invoke.mockResolvedValueOnce(undefined);
  await uninstallLinuxMenu();
  expect(invoke).toHaveBeenCalledWith('uninstall_linux_menu');

  let deliver: (event: { payload: unknown }) => void = () => {};
  listen.mockImplementation(async (_name: string, handler: typeof deliver) => {
    deliver = handler;
    return () => {};
  });
  const updates: unknown[] = [];
  await listenForModelMove((progress) => updates.push(progress));
  deliver({
    payload: {
      moved_bytes: 50,
      total_bytes: 100,
      moved_files: 1,
      total_files: 2,
      progress: 50,
      current_file: 'm.pth',
    },
  });
  expect(updates).toHaveLength(1);
});

it('forwards engine manager commands and status events', async () => {
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  invoke.mockResolvedValueOnce([]);
  await refreshEngineCatalog('beta');
  expect(invoke).toHaveBeenCalledWith('refresh_engine_catalog', { channel: 'beta' });

  invoke.mockResolvedValueOnce(undefined);
  await installEngine('cuda');
  expect(invoke).toHaveBeenCalledWith('install_engine', { backend: 'cuda' });

  invoke.mockResolvedValueOnce(undefined);
  await cancelEngineInstall();
  expect(invoke).toHaveBeenCalledWith('cancel_engine_install');

  invoke.mockResolvedValueOnce(undefined);
  await useEngine('directml');
  expect(invoke).toHaveBeenCalledWith('use_engine', { backend: 'directml' });

  invoke.mockResolvedValueOnce(undefined);
  await removeEngine('windows-x86_64-directml-1');
  expect(invoke).toHaveBeenCalledWith('remove_engine', { id: 'windows-x86_64-directml-1' });

  invoke.mockResolvedValueOnce('/tmp/win.engine-payload.json');
  expect(await chooseEngineManifest()).toBe('/tmp/win.engine-payload.json');
  expect(invoke).toHaveBeenCalledWith('choose_engine_manifest');

  invoke.mockResolvedValueOnce(undefined);
  await importEngine('/tmp/win.engine-payload.json');
  expect(invoke).toHaveBeenCalledWith('import_engine', { path: '/tmp/win.engine-payload.json' });

  let deliver: (event: { payload: unknown }) => void = () => {};
  listen.mockImplementation(async (_name: string, handler: typeof deliver) => {
    deliver = handler;
    return () => {};
  });
  const statuses: unknown[] = [];
  await listenForEngineStatus((status) => statuses.push(status));
  deliver({
    payload: {
      stage: 'downloading',
      backend: 'cuda',
      engine_id: 'windows-x86_64-cuda-1',
      downloaded: 100,
      total: 1000,
      error: null,
    },
  });
  expect(statuses).toHaveLength(1);
});
