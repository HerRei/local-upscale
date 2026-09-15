// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import UpdatePanel from './UpdatePanel.svelte';
import { updateDialogOpen } from './lib/updates';
const api = vi.hoisted(() => ({
  updateStatus: vi.fn(),
  listenForUpdates: vi.fn(async () => () => {}),
  openStoreUpdates: vi.fn(async () => {}),
  installUpdate: vi.fn(async () => {}),
  checkUpdate: vi.fn(),
  downloadUpdate: vi.fn(),
  discardUpdate: vi.fn(),
  cancelUpdate: vi.fn(),
  recoverUpdateSettings: vi.fn(),
}));
vi.mock('./lib/api', () => api);
afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  updateDialogOpen.set(true);
  api.updateStatus.mockResolvedValue({
    configured: true,
    channel: 'beta',
    target: 'linux-x86_64-rocm-portable',
    stage: 'downloaded',
    version: '0.0.13-beta.2',
    notes: 'Cancellation fixes',
    size: 1024,
    downloaded: 1024,
    message: 'Signature verified.',
    settings_recovery: false,
  });
});
it('keeps install disabled throughout processing and cancellation', async () => {
  const user = userEvent.setup();
  const view = render(UpdatePanel, { processing: true });
  const install = await screen.findByRole('button', { name: 'Install and restart' });
  expect(install.matches(':disabled')).toBe(true);
  await user.click(install);
  expect(api.installUpdate).not.toHaveBeenCalled();
  await view.rerender({ processing: false });
  await user.click(install);
  await waitFor(() => expect(api.installUpdate).toHaveBeenCalledTimes(1));
});
it('explains an unconfigured feed without pretending the preview is current', async () => {
  api.updateStatus.mockResolvedValue({
    configured: false,
    channel: 'beta',
    stage: 'idle',
    message: 'Updates are not published for this preview yet.',
    settings_recovery: false,
  });
  const user = userEvent.setup();
  render(UpdatePanel);
  await screen.findByText('Updates are not published for this preview yet.');
  expect(screen.getByRole('button', { name: 'Check for updates' }).matches(':disabled')).toBe(true);
  expect(api.checkUpdate).not.toHaveBeenCalled();
});

it('uses Store updates and waits for the active queue to finish', async () => {
  api.updateStatus.mockResolvedValue({
    managed_by_store: true,
    configured: false,
    channel: '',
    stage: 'store',
    message: 'App and inference engine updates are provided together through Microsoft Store.',
    settings_recovery: false,
  });
  const user = userEvent.setup();
  const view = render(UpdatePanel, { processing: true });
  const button = await screen.findByRole('button', { name: 'Open Microsoft Store updates' });
  expect(button.matches(':disabled')).toBe(true);
  expect(screen.queryByLabelText('Release channel')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Check for updates' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Install and restart' })).toBeNull();
  await user.click(button);
  expect(api.openStoreUpdates).not.toHaveBeenCalled();
  await view.rerender({ processing: false });
  await user.click(button);
  await waitFor(() => expect(api.openStoreUpdates).toHaveBeenCalledTimes(1));
  expect(api.checkUpdate).not.toHaveBeenCalled();
  expect(api.downloadUpdate).not.toHaveBeenCalled();
  expect(api.installUpdate).not.toHaveBeenCalled();
});
