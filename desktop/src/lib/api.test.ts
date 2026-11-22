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
  chooseEditModelFile,
  chooseEditRuntime,
  chooseExternalFFmpeg,
  chooseMediaFiles,
  chooseMediaFolder,
  chooseOutputDirectory,
  exportBenchmark,
  listenForWorker,
  saveCustomEditModel,
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
