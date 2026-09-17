// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import WelcomeNotice from './WelcomeNotice.svelte';
import { emptyUpdateStatus, updateState } from './lib/updates';

const api = vi.hoisted(() => ({
  isTauri: vi.fn(() => true),
  openUserGuide: vi.fn(async () => {}),
}));
vi.mock('./lib/api', () => api);

const storage = new Map<string, string>();

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  storage.clear();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
  updateState.set({ ...emptyUpdateStatus, configured: true });
});

it('welcomes a first launch, discloses the update count and opens the guide', async () => {
  const user = userEvent.setup();
  render(WelcomeNotice, { anonymousUpdateCount: true });

  expect(screen.getByRole('region', { name: 'Welcome to LocalSR' })).toBeTruthy();
  expect(screen.getByText(/sends an anonymous count/)).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'User Guide' }));
  expect(api.openUserGuide).toHaveBeenCalledTimes(1);
});

it('says where the count can be turned off without a button for it', () => {
  render(WelcomeNotice, { anonymousUpdateCount: true });
  expect(screen.getByText(/turn it off under Check for Updates/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /turn off/i })).toBeNull();
});

it('says the count is off when it is', () => {
  render(WelcomeNotice, { anonymousUpdateCount: false });
  expect(screen.getByText('The anonymous update-check count is off.')).toBeTruthy();
});

it('stays closed after Got It', async () => {
  const user = userEvent.setup();
  render(WelcomeNotice, { anonymousUpdateCount: true });
  await user.click(screen.getByRole('button', { name: 'Got It' }));
  expect(screen.queryByRole('region', { name: 'Welcome to LocalSR' })).toBeNull();
  cleanup();

  render(WelcomeNotice, { anonymousUpdateCount: true });
  expect(screen.queryByRole('region', { name: 'Welcome to LocalSR' })).toBeNull();
});

it('does not mention the count where no update feed is configured', () => {
  updateState.set({ ...emptyUpdateStatus, configured: true, managed_by_store: true });
  render(WelcomeNotice, { anonymousUpdateCount: true });
  expect(screen.queryByText(/anonymous count/)).toBeNull();
});
