import { beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  isTauri: vi.fn(() => true),
  updateStatus: vi.fn(),
  listenForUpdates: vi.fn(async () => () => {}),
  checkUpdate: vi.fn(),
}));
vi.mock('./api', () => api);

import { startUpdateWatcher } from './updates';

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
  api.checkUpdate.mockResolvedValue({ stage: 'idle' });
});

it('checks the feed at launch only while the setting is on', async () => {
  await startUpdateWatcher(false);
  expect(api.checkUpdate).not.toHaveBeenCalled();
  // It still follows progress, so Check for Updates… keeps working.
  expect(api.listenForUpdates).toHaveBeenCalledTimes(1);

  await startUpdateWatcher(true);
  expect(api.checkUpdate).toHaveBeenCalledWith('beta');
});
