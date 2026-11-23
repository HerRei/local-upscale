// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import UpdateNotice from './UpdateNotice.svelte';
import {
  emptyUpdateStatus,
  startUpdateWatcher,
  updateDialogOpen,
  updateState,
} from './lib/updates';

const api = vi.hoisted(() => ({
  isTauri: vi.fn(() => true),
  updateStatus: vi.fn(),
  listenForUpdates: vi.fn(async () => () => {}),
  checkUpdateAtLaunch: vi.fn(),
  skipUpdate: vi.fn(),
  openUpdateDiscover: vi.fn(),
  downloadUpdate: vi.fn(async () => {}),
  installUpdate: vi.fn(async () => {}),
}));
vi.mock('./lib/api', () => api);

const available = {
  ...emptyUpdateStatus,
  configured: true,
  stage: 'available',
  version: '0.0.13-beta.2',
  size: 300 * 1024 ** 2,
};

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage?.clear();
  updateState.set(emptyUpdateStatus);
  updateDialogOpen.set(false);
});

it('checks the feed once at launch and stays quiet when nothing is configured', async () => {
  api.updateStatus.mockResolvedValue({ ...emptyUpdateStatus, configured: false });
  await startUpdateWatcher();
  expect(api.checkUpdateAtLaunch).not.toHaveBeenCalled();
  render(UpdateNotice);
  expect(screen.queryByRole('status')).toBeNull();
});

it('announces an available update at launch and updates now', async () => {
  api.updateStatus
    .mockResolvedValueOnce({ ...emptyUpdateStatus, configured: true })
    .mockResolvedValue({ ...available, stage: 'downloaded', downloaded: available.size });
  api.checkUpdateAtLaunch.mockResolvedValue(available);
  await startUpdateWatcher();
  expect(api.checkUpdateAtLaunch).toHaveBeenCalledWith('beta');
  const user = userEvent.setup();
  render(UpdateNotice);
  expect(await screen.findByText('LocalSR 0.0.13-beta.2 is available')).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'Update' }));
  await waitFor(() => expect(api.installUpdate).toHaveBeenCalledTimes(1));
  expect(api.downloadUpdate).toHaveBeenCalledTimes(1);
});

it('downloads during processing and installs once the queue finishes', async () => {
  updateState.set(available);
  api.updateStatus.mockResolvedValue({ ...available, stage: 'downloaded' });
  const user = userEvent.setup();
  const view = render(UpdateNotice, { processing: true });
  await user.click(screen.getByRole('button', { name: 'Update' }));
  await waitFor(() => expect(api.downloadUpdate).toHaveBeenCalledTimes(1));
  expect(
    await screen.findByText('It installs as soon as the current queue finishes.'),
  ).toBeTruthy();
  expect(api.installUpdate).not.toHaveBeenCalled();
  await view.rerender({ processing: false });
  await waitFor(() => expect(api.installUpdate).toHaveBeenCalledTimes(1));
});

it('hides a dismissed version until a newer one appears', async () => {
  updateState.set(available);
  const user = userEvent.setup();
  render(UpdateNotice);
  await user.click(screen.getByRole('button', { name: 'Close update notice' }));
  expect(screen.queryByText('LocalSR 0.0.13-beta.2 is available')).toBeNull();
  updateState.set({ ...available, version: '0.0.13-beta.3' });
  expect(await screen.findByText('LocalSR 0.0.13-beta.3 is available')).toBeTruthy();
});

it('skips only the selected version and hides behind the update dialog', async () => {
  updateState.set({ ...available, version: '1.0.0' });
  api.skipUpdate.mockResolvedValue({ ...available, version: '1.0.0', skipped_version: '1.0.0' });
  const user = userEvent.setup();
  render(UpdateNotice);
  await user.click(screen.getByRole('button', { name: 'Skip This Version' }));
  await waitFor(() => expect(screen.queryByText('LocalSR 1.0.0 is available')).toBeNull());
  updateState.set({ ...available, version: '1.1.0', skipped_version: '1.0.0' });
  expect(await screen.findByText('LocalSR 1.1.0 is available')).toBeTruthy();
  updateDialogOpen.set(true);
  await waitFor(() => expect(screen.queryByText('LocalSR 1.1.0 is available')).toBeNull());
});

it('explains how to recover unreadable settings before updating', () => {
  updateState.set({ ...available, stage: 'downloaded', settings_recovery: true });
  render(UpdateNotice);
  expect(
    screen.getByText('Settings could not be read. Open Details to recover them before updating.'),
  ).toBeTruthy();
  expect(
    (screen.getByRole('button', { name: 'Restart to Update' }) as HTMLButtonElement).disabled,
  ).toBe(true);
});
