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
  chooseCustomModel,
  chooseEditRuntime,
  chooseExternalFFmpeg,
  chooseMediaFiles,
  chooseMediaFolder,
  chooseModelDirectory,
  chooseOutputDirectory,
  dismissGpuDownloadNotice,
  exportBenchmark,
  listenForModelMove,
  listenForWorker,
  resetModelDirectory,
  setModelDirectory,
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
it('resolves a Compare slot through the single-video descriptor route using only its generation and index', async () => {
  invoke.mockResolvedValueOnce({
    original_path: '/host/only.webm',
    original_url: 'http://127.0.0.1/authorised',
    playback_note: 'Compatible SDR playback copy',
  });
  expect(await prepareCompareVideo(7, 2, 'request', true, true)).toEqual({
    original_url: 'http://127.0.0.1/authorised',
    enhanced_url: '',
    playback_note: 'Compatible SDR playback copy',
  });
  expect(invoke).toHaveBeenCalledWith('prepare_video_comparison', {
    mediaId: '',
    requestId: 'request',
    forceCompatible: true,
    deferConversion: true,
    compareGeneration: 7,
    compareSlot: 2,
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
