// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AdvancedSettings from './AdvancedSettings.svelte';
import { demoSnapshot } from './lib/demo';
import type { CapabilityInfo, UiSettings } from './lib/types';

const api = vi.hoisted(() => ({
  chooseExternalFFmpeg: vi.fn(),
  detectExternalFFmpeg: vi.fn(),
}));
vi.mock('./lib/api', () => api);

afterEach(cleanup);

const base = demoSnapshot().capabilities;
const mac: CapabilityInfo = {
  ...base,
  media: {
    system_codecs: {
      label: 'macOS',
      decode: ['h264', 'hevc', 'aac'],
      encode: ['h264', 'hevc', 'prores', 'aac'],
      containers: ['mp4', 'mov'],
    },
    image_output: ['heic'],
    external_ffmpeg: { detected: [], candidates: [] },
  },
};

async function open(
  settings: Partial<UiSettings>,
  capabilities: CapabilityInfo,
  updateSettings = vi.fn(),
) {
  render(AdvancedSettings, {
    settings: { ...demoSnapshot().settings, ...settings },
    selectedModel: undefined,
    capabilities,
    usingTemporalVideo: false,
    updateSettings,
    chooseOutput: async () => undefined,
    refreshCapabilities: async () => true,
  });
  await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
  return updateSettings;
}

function options(label: string): string[] {
  return Array.from(screen.getByLabelText(label).querySelectorAll('option')).map(
    (option) => option.value,
  );
}

describe('HEIC image output', () => {
  it('is offered with a quality slider where the engine announces it', async () => {
    const update = await open({ task: 'upscale', output_format: 'heic' }, mac);
    expect(options('Format')).toEqual(['png', 'jpg', 'tif', 'webp', 'heic']);
    const slider = screen.getByLabelText(/^HEIC quality/);
    await fireEvent.change(slider, { target: { value: '85' } });
    expect(update).toHaveBeenCalledWith({ jpeg_quality: 85 });
  });

  it('is hidden everywhere else, and PNG shows no quality slider', async () => {
    await open({ task: 'upscale', output_format: 'png' }, base);
    expect(options('Format')).toEqual(['png', 'jpg', 'tif', 'webp']);
    expect(screen.queryByLabelText(/quality/)).toBeNull();
  });
});

describe('MOV video output', () => {
  it('lists only HEVC and ProRes for MOV and switches the pair together', async () => {
    const update = await open({ task: 'video', video_container: 'mov', video_codec: 'hevc' }, mac);
    expect(options('Container')).toEqual(['mp4', 'mkv', 'mov']);
    expect(options('Video format')).toEqual(['hevc', 'prores']);
    await fireEvent.change(screen.getByLabelText('Video format'), { target: { value: 'prores' } });
    expect(update).toHaveBeenLastCalledWith({ video_codec: 'prores', video_container: 'mov' });
    await fireEvent.change(screen.getByLabelText('Container'), { target: { value: 'mp4' } });
    expect(update).toHaveBeenLastCalledWith({ video_container: 'mp4' });
  });

  it('keeps the existing codecs for MP4 and moves to a MOV codec on choosing MOV', async () => {
    const update = await open({ task: 'video', video_container: 'mp4', video_codec: 'av1' }, mac);
    expect(options('Video format')).toEqual(['auto', 'av1', 'vp9', 'ffv1', 'h264', 'hevc']);
    await fireEvent.change(screen.getByLabelText('Container'), { target: { value: 'mov' } });
    expect(update).toHaveBeenLastCalledWith({ video_container: 'mov', video_codec: 'hevc' });
  });

  it('is hidden where the system codecs are missing', async () => {
    await open({ task: 'video', video_container: 'mp4', video_codec: 'av1' }, base);
    expect(options('Container')).toEqual(['mp4', 'mkv']);
    expect(options('Video format')).not.toContain('prores');
  });
});

describe('FFmpeg buttons', () => {
  it('show the host message when looking for or choosing a program fails', async () => {
    await open({ task: 'video', video_container: 'mp4', video_codec: 'h264' }, mac);
    api.detectExternalFFmpeg.mockRejectedValueOnce('The search was refused.');
    await fireEvent.click(screen.getByRole('button', { name: 'Find installed' }));
    expect(await screen.findByText(/The search was refused\./)).toBeTruthy();
    api.chooseExternalFFmpeg.mockRejectedValueOnce('The file picker failed.');
    await fireEvent.click(screen.getByRole('button', { name: /Choose FFmpeg/ }));
    expect(await screen.findByText(/The file picker failed\./)).toBeTruthy();
  });
});

it('places the FFmpeg chooser beside the path and offers quiet actions', async () => {
  await open({ task: 'video', external_ffmpeg_path: '/usr/bin/ffmpeg' }, mac);
  const path = screen.getByTitle('/usr/bin/ffmpeg');
  expect(path.closest('.directory-field')?.textContent).toContain('Choose…');
  expect(
    screen.getByRole('button', { name: 'Find installed' }).classList.contains('link-button'),
  ).toBe(true);
  expect(screen.getByRole('button', { name: 'Don’t use' }).classList.contains('link-button')).toBe(
    true,
  );
});

it('explains when interpolation happens', async () => {
  await open({ task: 'video', video_interpolation: 'x2' }, mac);
  expect(screen.getByRole('button', { name: 'About Interpolate' })).toBeTruthy();
});

describe('Refresh in the memory line', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  async function mount(refresh: () => Promise<boolean>) {
    render(AdvancedSettings, {
      settings: { ...demoSnapshot().settings, task: 'upscale' },
      selectedModel: undefined,
      capabilities: base,
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      refreshCapabilities: refresh,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    return screen.getByRole('button', { name: /^Refresh/ });
  }
  // The wider word is always laid out, hidden, so the button never changes width.
  const label = (button: HTMLElement) => button.querySelector('.refresh-now')?.textContent;

  it('is disabled while the call is in flight, then reads Refreshed for 1.5 seconds', async () => {
    let finish: (done: boolean) => void = () => undefined;
    const call = vi.fn(() => new Promise<boolean>((resolve) => (finish = resolve)));
    const button = await mount(call);
    await fireEvent.click(button);
    expect(call).toHaveBeenCalledTimes(1);
    expect(button.getAttribute('aria-disabled')).toBe('true');
    expect(label(button)).toBe('Refresh');
    await fireEvent.click(button);
    expect(call).toHaveBeenCalledTimes(1);
    finish(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(button.getAttribute('aria-disabled')).not.toBe('true');
    expect(label(button)).toBe('Refreshed');
    expect(screen.getByRole('status').textContent).toBe('Memory status refreshed');
    await vi.advanceTimersByTimeAsync(1400);
    expect(label(button)).toBe('Refreshed');
    await vi.advanceTimersByTimeAsync(200);
    expect(label(button)).toBe('Refresh');
    expect(screen.getByRole('status').textContent).toBe('');
  });

  it('shows no confirmation when the call fails', async () => {
    const button = await mount(async () => false);
    await fireEvent.click(button);
    await vi.advanceTimersByTimeAsync(0);
    expect(label(button)).toBe('Refresh');
    expect(screen.getByRole('status').textContent).toBe('');
    expect(button.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('works from the keyboard', async () => {
    const call = vi.fn(async () => true);
    const button = await mount(call);
    await fireEvent.keyDown(button, { key: 'Enter' });
    await fireEvent.keyDown(button, { key: ' ' });
    await vi.advanceTimersByTimeAsync(0);
    expect(call).toHaveBeenCalledTimes(2);
  });

  it('stays usable inside a disabled fieldset, as while a job runs', async () => {
    const fieldset = document.body.appendChild(document.createElement('fieldset'));
    const call = vi.fn(async () => true);
    render(AdvancedSettings, {
      target: fieldset,
      props: {
        settings: { ...demoSnapshot().settings, task: 'upscale' },
        selectedModel: undefined,
        capabilities: base,
        usingTemporalVideo: false,
        updateSettings: vi.fn(),
        chooseOutput: async () => undefined,
        refreshCapabilities: call,
      },
    });
    await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    fieldset.disabled = true;
    const button = screen.getByRole('button', { name: /^Refresh/ });
    expect(button.matches(':disabled')).toBe(false);
    await fireEvent.click(button);
    expect(call).toHaveBeenCalledTimes(1);
    fieldset.remove();
  });
});
