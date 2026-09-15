// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import UpdateMenuItem from './UpdateMenuItem.svelte';
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
  checkUpdate: vi.fn(),
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
  expect(api.checkUpdate).not.toHaveBeenCalled();
  render(UpdateNotice);
  expect(screen.queryByRole('status')).toBeNull();
});

it('announces an available update at launch and updates now', async () => {
  api.updateStatus
    .mockResolvedValueOnce({ ...emptyUpdateStatus, configured: true })
    .mockResolvedValue({ ...available, stage: 'downloaded', downloaded: available.size });
  api.checkUpdate.mockResolvedValue(available);
  await startUpdateWatcher();
  expect(api.checkUpdate).toHaveBeenCalledWith('beta');
  const user = userEvent.setup();
  render(UpdateNotice);
  expect(await screen.findByText('LocalSR 0.0.13-beta.2 is available')).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'Update Now' }));
  await waitFor(() => expect(api.installUpdate).toHaveBeenCalledTimes(1));
  expect(api.downloadUpdate).toHaveBeenCalledTimes(1);
});

it('downloads during processing and installs once the queue finishes', async () => {
  updateState.set(available);
  api.updateStatus.mockResolvedValue({ ...available, stage: 'downloaded' });
  const user = userEvent.setup();
  const view = render(UpdateNotice, { processing: true });
  await user.click(screen.getByRole('button', { name: 'Update Now' }));
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
  await user.click(screen.getByRole('button', { name: 'Later' }));
  expect(screen.queryByText('LocalSR 0.0.13-beta.2 is available')).toBeNull();
  updateState.set({ ...available, version: '0.0.13-beta.3' });
  expect(await screen.findByText('LocalSR 0.0.13-beta.3 is available')).toBeTruthy();
});

it('shows update state in the app menu and opens Software Update', async () => {
  updateState.set(available);
  const onOpen = vi.fn();
  const user = userEvent.setup();
  render(UpdateMenuItem, { onOpen });
  const item = screen.getByRole('menuitem', { name: /Update LocalSR…/ });
  expect(item.textContent).toContain('0.0.13-beta.2');
  await user.click(item);
  expect(onOpen).toHaveBeenCalled();
  let open = false;
  updateDialogOpen.subscribe((value) => (open = value))();
  expect(open).toBe(true);
});
