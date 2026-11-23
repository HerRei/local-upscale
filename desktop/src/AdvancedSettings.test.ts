// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AdvancedSettings from './AdvancedSettings.svelte';
import { demoSnapshot } from './lib/demo';
import type { CapabilityInfo, UiSettings } from './lib/types';

const api = vi.hoisted(() => ({}));
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

describe('External FFmpeg shortcut', () => {
  it('shows detected or chosen FFmpeg status and opens Storage settings on Change…', async () => {
    const onOpenFfmpegSettings = vi.fn();
    render(AdvancedSettings, {
      settings: {
        ...demoSnapshot().settings,
        task: 'video',
        external_ffmpeg_path: '/usr/bin/ffmpeg',
      },
      selectedModel: undefined,
      capabilities: mac,
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      onOpenFfmpegSettings,
      refreshCapabilities: async () => true,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    expect(screen.getByText('/usr/bin/ffmpeg')).toBeTruthy();
    const row = screen.getByText('External FFmpeg').closest('.field-row') as HTMLElement;
    const change = row.querySelector('button.link-button') as HTMLButtonElement;
    expect(change.textContent).toBe('Change…');
    await fireEvent.click(change);
    expect(onOpenFfmpegSettings).toHaveBeenCalledTimes(1);

    cleanup();
    render(AdvancedSettings, {
      settings: { ...demoSnapshot().settings, task: 'video', external_ffmpeg_path: '' },
      selectedModel: undefined,
      capabilities: mac,
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      refreshCapabilities: async () => true,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    expect(screen.getByText('None')).toBeTruthy();
  });
});

it('explains when interpolation happens', async () => {
  await open({ task: 'video', video_interpolation: 'x2' }, mac);
  expect(screen.getByRole('button', { name: 'About Interpolate' })).toBeTruthy();
});

it('starts output scale at 2× for both photo and video upscale and hides it in restore', async () => {
  await open({ task: 'video', video_task: 'upscale', output_scale: 2 }, mac);
  const scaleSelect = screen.getByLabelText('Output scale') as HTMLSelectElement;
  expect(Array.from(scaleSelect.options).map((o) => o.value)).toEqual(['2', '3', '4']);
  expect(scaleSelect.value).toBe('2');
  expect(screen.getByText('Scale, format, tiles')).toBeTruthy();
  await fireEvent.click(screen.getByRole('button', { name: 'About Output scale' }));
  expect(screen.getByRole('tooltip').textContent).toBe(
    'How many times larger the result is than the original. A 2× or 3× choice on a 4× model resizes down afterwards. To keep the size, use Restore.',
  );

  cleanup();
  await open({ task: 'video', video_task: 'denoise', output_scale: 4 }, mac);
  expect(screen.queryByLabelText('Output scale')).toBeNull();
  expect(screen.getByText('Format, tiles')).toBeTruthy();

  cleanup();
  await open({ task: 'denoise', output_scale: 1 }, mac);
  expect(screen.queryByLabelText('Output scale')).toBeNull();
  expect(screen.getByText('Format, tiles')).toBeTruthy();

  cleanup();
  await open({ task: 'upscale', output_scale: 2 }, mac);
  const imageScale = screen.getByLabelText('Output scale') as HTMLSelectElement;
  expect(Array.from(imageScale.options).map((o) => o.value)).toEqual(['2', '3', '4']);
  await fireEvent.click(screen.getByRole('button', { name: 'About Output scale' }));
  expect(screen.getByRole('tooltip').textContent).toBe(
    'How many times larger the result is than the original. The model always works at its own 4× scale; a smaller choice resizes that result down afterwards, so it saves no time. To keep the size, use Restore.',
  );
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

describe('Read-only Hardware shortcut row', () => {
  it('shows the effective hardware label and opens Settings at Performance on Change…', async () => {
    const onOpenHardwareSettings = vi.fn();
    render(AdvancedSettings, {
      settings: { ...demoSnapshot().settings, task: 'upscale', device_id: 'openvino:80d1' },
      selectedModel: {
        ...demoSnapshot().catalog.models[0],
        architecture: 'SwinIR',
      },
      capabilities: {
        ...base,
        devices: [
          ...base.devices,
          {
            id: 'openvino:80d1',
            type: 'npu',
            name: 'Intel(R) AI Boost (NPU)',
            total_memory: 8 * 1024 ** 3,
            free_memory: 6 * 1024 ** 3,
            supports_fp16: true,
            is_integrated: true,
            recommended_tile_sizes: [64, 128],
          },
        ],
      },
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      onOpenHardwareSettings,
      refreshCapabilities: async () => true,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    expect(screen.getByText('CPU · this model does not run on the NPU')).toBeTruthy();
    const changeBtn = screen.getByRole('button', { name: 'Change…' });
    expect(changeBtn.classList.contains('link-button')).toBe(true);
    await fireEvent.click(changeBtn);
    expect(onOpenHardwareSettings).toHaveBeenCalledTimes(1);
  });
});
