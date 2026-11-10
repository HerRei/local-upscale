// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import FFmpegNotice from './FFmpegNotice.svelte';

const api = vi.hoisted(() => ({
  chooseExternalFFmpeg: vi.fn(),
  detectExternalFFmpeg: vi.fn(),
  ffmpegInstallHint: vi.fn(async () => null),
  isTauri: vi.fn(() => true),
  openFfmpegDownloadPage: vi.fn(),
  openTerminalWithInstallCommand: vi.fn(async () => undefined),
}));
vi.mock('./lib/api', () => api);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const notice = () => render(FFmpegNotice, { onSelected: vi.fn(), onClose: vi.fn() });

describe('FFmpeg notice', () => {
  it('shows the host message when looking for FFmpeg fails', async () => {
    api.detectExternalFFmpeg.mockRejectedValueOnce('The search was refused.');
    const user = userEvent.setup();
    notice();
    await user.click(screen.getByRole('button', { name: 'I installed it, find FFmpeg' }));
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('refused'));
    expect(
      (screen.getByRole('button', { name: /find FFmpeg/ }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('shows the host message when choosing a program fails', async () => {
    api.chooseExternalFFmpeg.mockRejectedValueOnce('The file picker failed.');
    const user = userEvent.setup();
    notice();
    await user.click(screen.getByRole('button', { name: 'Choose FFmpeg…' }));
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('picker failed'));
  });

  it('shows the host message when the download page cannot be opened', async () => {
    api.isTauri.mockReturnValue(false);
    api.openFfmpegDownloadPage.mockRejectedValueOnce('No browser.');
    const user = userEvent.setup();
    notice();
    await user.click(screen.getByRole('button', { name: 'Install FFmpeg…' }));
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('No browser'));
  });
});
