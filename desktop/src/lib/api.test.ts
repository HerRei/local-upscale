// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';

const invoke = vi.hoisted(() => vi.fn());
vi.mock('@tauri-apps/api/core', () => ({ invoke, convertFileSrc: vi.fn() }));
const listen = vi.hoisted(() => vi.fn());
vi.mock('@tauri-apps/api/event', () => ({ listen }));

import { chooseExternalFFmpeg, listenForWorker } from './api';

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
