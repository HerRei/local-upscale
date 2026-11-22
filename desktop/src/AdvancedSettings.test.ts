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

it('offers 1× output scale for video upscale and hides output scale in video restore', async () => {
  await open({ task: 'video', video_task: 'upscale', output_scale: 1 }, mac);
  const scaleSelect = screen.getByLabelText('Output scale') as HTMLSelectElement;
  expect(Array.from(scaleSelect.options).map((o) => o.value)).toEqual(['1', '2', '3', '4']);
  expect(scaleSelect.value).toBe('1');
  // What 1× does is said once, in the "?" of Output scale.
  const about1x = screen.getAllByText(/At 1×/);
  expect(about1x).toHaveLength(1);
  expect(about1x[0].id).toBe('help-output-scale');

  cleanup();
  await open({ task: 'video', video_task: 'denoise', output_scale: 4 }, mac);
  expect(screen.queryByLabelText('Output scale')).toBeNull();

  cleanup();
  await open({ task: 'upscale', output_scale: 2 }, mac);
  const imageScale = screen.getByLabelText('Output scale') as HTMLSelectElement;
  expect(Array.from(imageScale.options).map((o) => o.value)).toEqual(['2', '3', '4']);
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

describe('Run at low priority', () => {
  it('shows its help tip, toggles by click or keyboard, and stays usable inside a disabled fieldset', async () => {
    const fieldset = document.body.appendChild(document.createElement('fieldset'));
    const setLowPriority = vi.fn();
    render(AdvancedSettings, {
      target: fieldset,
      props: {
        settings: { ...demoSnapshot().settings, task: 'upscale', low_priority: false },
        selectedModel: undefined,
        capabilities: base,
        usingTemporalVideo: false,
        updateSettings: vi.fn(),
        setLowPriority,
        chooseOutput: async () => undefined,
        refreshCapabilities: async () => true,
      },
    });
    await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    expect(screen.getByRole('button', { name: 'About Run at low priority' })).toBeTruthy();

    fieldset.disabled = true;
    const checkbox = screen.getByRole('checkbox', { name: 'Run at low priority' });
    expect(checkbox.getAttribute('aria-checked')).toBe('false');
    expect(checkbox.matches(':disabled')).toBe(false);

    await fireEvent.click(checkbox);
    await fireEvent.keyDown(checkbox, { key: ' ' });
    await fireEvent.keyDown(checkbox, { key: 'Enter' });
    expect(setLowPriority).toHaveBeenCalledTimes(3);
    expect(setLowPriority).toHaveBeenNthCalledWith(1, true);
    fieldset.remove();
  });
});

describe('Model folder setting', () => {
  it('shows default folder, custom reset action, moving progress, and missing folder options', async () => {
    const chooseModelFolder = vi.fn(async () => undefined);
    const resetModelFolder = vi.fn(async () => undefined);
    const view = render(AdvancedSettings, {
      settings: { ...demoSnapshot().settings, task: 'upscale', model_directory: '' },
      selectedModel: undefined,
      capabilities: base,
      usingTemporalVideo: false,
      defaultModelDirectory: '/Users/demo/Library/Application Support/LocalSR/models',
      modelDirectoryMissing: false,
      movingModels: false,
      modelMoveProgress: null,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      chooseModelFolder,
      resetModelFolder,
      refreshCapabilities: async () => true,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    expect(screen.getByText('/Users/demo/Library/Application Support/LocalSR/models')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Reset to default' })).toBeNull();

    await fireEvent.click(screen.getByRole('button', { name: /^Model folder / }));
    expect(chooseModelFolder).toHaveBeenCalledTimes(1);

    await view.rerender({
      settings: {
        ...demoSnapshot().settings,
        task: 'upscale',
        model_directory: '/Volumes/SSD/models',
      },
      selectedModel: undefined,
      capabilities: base,
      usingTemporalVideo: false,
      defaultModelDirectory: '/Users/demo/Library/Application Support/LocalSR/models',
      modelDirectoryMissing: false,
      movingModels: false,
      modelMoveProgress: null,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      chooseModelFolder,
      resetModelFolder,
      refreshCapabilities: async () => true,
    });
    expect(screen.getByText('/Volumes/SSD/models')).toBeTruthy();
    await fireEvent.click(screen.getByRole('button', { name: 'Reset to default' }));
    expect(resetModelFolder).toHaveBeenCalledTimes(1);

    await view.rerender({
      settings: {
        ...demoSnapshot().settings,
        task: 'upscale',
        model_directory: '/Volumes/SSD/models',
      },
      selectedModel: undefined,
      capabilities: base,
      usingTemporalVideo: false,
      defaultModelDirectory: '/Users/demo/Library/Application Support/LocalSR/models',
      modelDirectoryMissing: false,
      movingModels: true,
      modelMoveProgress: {
        moved_bytes: 50,
        total_bytes: 100,
        moved_files: 1,
        total_files: 2,
        progress: 50,
        current_file: 'a.pth',
      },
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      chooseModelFolder,
      resetModelFolder,
      refreshCapabilities: async () => true,
    });
    expect(screen.getByText('Moving 1 of 2 files…')).toBeTruthy();
    expect(screen.getByText('50%')).toBeTruthy();
    expect(screen.queryByText(/Moving models…/)).toBeNull();
    const progress = document.querySelector('progress.download-progress') as HTMLProgressElement;
    expect(progress).toBeTruthy();
    expect(progress.value).toBe(50);
    expect(
      (screen.getByRole('button', { name: /^Model folder / }) as HTMLButtonElement).disabled,
    ).toBe(true);

    await view.rerender({
      settings: {
        ...demoSnapshot().settings,
        task: 'upscale',
        model_directory: '/Volumes/Missing/models',
      },
      selectedModel: undefined,
      capabilities: base,
      usingTemporalVideo: false,
      defaultModelDirectory: '/Users/demo/Library/Application Support/LocalSR/models',
      modelDirectoryMissing: true,
      movingModels: false,
      modelMoveProgress: null,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      chooseModelFolder,
      resetModelFolder,
      refreshCapabilities: async () => true,
    });
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('Engine row above Hardware', () => {
  it('shows the active engine and link-button Change… above Hardware on supported builds, and hides on Mac or Store', async () => {
    const onOpenEngineManager = vi.fn();
    const engines = structuredClone(demoSnapshot().engines!);
    const view = render(AdvancedSettings, {
      settings: { ...demoSnapshot().settings, task: 'upscale' },
      selectedModel: undefined,
      capabilities: base,
      engines,
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      onOpenEngineManager,
      refreshCapabilities: async () => true,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    expect(screen.getByText('Engine')).toBeTruthy();
    expect(screen.getByText('CPU')).toBeTruthy();
    const changeBtn = screen.getByRole('button', { name: 'Change…' });
    expect(changeBtn.classList.contains('link-button')).toBe(true);
    expect(changeBtn.classList.contains('compact')).toBe(false);

    // Engine row precedes Hardware row in DOM order.
    const engineRow = document.querySelector('.engine-row') as HTMLElement;
    const hardwareRow = screen.getByLabelText('Hardware').closest('.field-row') as HTMLElement;
    expect(
      engineRow.compareDocumentPosition(hardwareRow) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await fireEvent.click(changeBtn);
    expect(onOpenEngineManager).toHaveBeenCalledTimes(1);

    await view.rerender({
      settings: { ...demoSnapshot().settings, task: 'upscale' },
      selectedModel: undefined,
      capabilities: base,
      engines: { ...engines, active_backend: 'rocm' },
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      onOpenEngineManager,
      refreshCapabilities: async () => true,
    });
    expect(screen.getByText('AMD ROCm')).toBeTruthy();

    await view.rerender({
      settings: { ...demoSnapshot().settings, task: 'upscale' },
      selectedModel: undefined,
      capabilities: base,
      engines: { ...engines, supported: false },
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      onOpenEngineManager,
      refreshCapabilities: async () => true,
    });
    expect(screen.queryByText('Engine')).toBeNull();

    await view.rerender({
      settings: { ...demoSnapshot().settings, task: 'upscale' },
      selectedModel: undefined,
      capabilities: base,
      engines: { ...engines, supported: true, managed_by_store: true },
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      chooseOutput: async () => undefined,
      onOpenEngineManager,
      refreshCapabilities: async () => true,
    });
    expect(screen.queryByText('Engine')).toBeNull();
  });
});
