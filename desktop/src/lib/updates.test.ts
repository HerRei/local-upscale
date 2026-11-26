import { beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  isTauri: vi.fn(() => true),
  updateStatus: vi.fn(),
  listenForUpdates: vi.fn(async () => () => {}),
  checkUpdateAtLaunch: vi.fn(),
}));
vi.mock('./api', () => api);

import { get } from 'svelte/store';
import { startUpdateWatcher, updateState } from './updates';

beforeEach(() => {
  vi.clearAllMocks();
  api.updateStatus.mockResolvedValue({
    configured: true,
    managed_by_store: false,
    channel: 'beta',
    target: 'darwin-aarch64-mps-native',
    stage: 'idle',
    version: '',
    notes: '',
    size: 0,
    downloaded: 0,
    message: '',
    settings_recovery: false,
  });
  api.checkUpdateAtLaunch.mockResolvedValue({ stage: 'idle' });
});

it('checks the feed at launch only while the setting is on', async () => {
  await startUpdateWatcher(false);
  expect(api.checkUpdateAtLaunch).not.toHaveBeenCalled();
  // It still follows progress, so Check for Updates… keeps working.
  expect(api.listenForUpdates).toHaveBeenCalledTimes(1);

  await startUpdateWatcher(true);
  expect(api.checkUpdateAtLaunch).toHaveBeenCalledWith('beta');
});

it('returns before the launch check resolves and updates state when it finishes', async () => {
  let resolveCheck!: (value: { stage: string; version: string }) => void;
  api.checkUpdateAtLaunch.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveCheck = resolve;
      }),
  );
  const onLaunchCheck = vi.fn();
  const stop = await startUpdateWatcher(true, onLaunchCheck);
  expect(typeof stop).toBe('function');
  expect(api.checkUpdateAtLaunch).toHaveBeenCalledWith('beta');
  expect(onLaunchCheck).not.toHaveBeenCalled();

  resolveCheck({ stage: 'available', version: '0.6.3' });
  await Promise.resolve();
  await Promise.resolve();
  expect(get(updateState)).toMatchObject({ stage: 'available', version: '0.6.3' });
  expect(onLaunchCheck).toHaveBeenCalledOnce();
});

it.each([
  'windows-x86_64-cuda-native',
  'windows-x86_64-directml-native',
  'linux-x86_64-rocm-native',
])('a %s build follows the launch-check setting as the Mac build does', async (target) => {
  api.updateStatus.mockResolvedValue({ ...(await api.updateStatus()), target });
  await startUpdateWatcher(false);
  expect(api.checkUpdateAtLaunch).not.toHaveBeenCalled();
  await startUpdateWatcher(true);
  expect(api.checkUpdateAtLaunch).toHaveBeenCalledWith('beta');
});
