// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import RunSettings from './RunSettings.svelte';
import { demoSnapshot } from './lib/demo';
import type { CapabilityInfo, UiSettings } from './lib/types';

afterEach(cleanup);

const base = demoSnapshot().capabilities;

function options(label: string): string[] {
  return Array.from(screen.getByLabelText(label).querySelectorAll('option')).map(
    (option) => option.value,
  );
}

describe('Refresh in the memory line', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  async function mount(refresh: () => Promise<boolean>) {
    render(RunSettings, {
      settings: { ...demoSnapshot().settings, task: 'upscale' },
      selectedModel: undefined,
      capabilities: base,
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      refreshCapabilities: refresh,
    });
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

  it('stays usable when locked, as while a job runs', async () => {
    const call = vi.fn(async () => true);
    render(RunSettings, {
      settings: { ...demoSnapshot().settings, task: 'upscale' },
      selectedModel: undefined,
      capabilities: base,
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      refreshCapabilities: call,
      disabled: true,
    });
    const button = screen.getByRole('button', { name: /^Refresh/ });
    expect(button.matches(':disabled')).toBe(false);
    await fireEvent.click(button);
    expect(call).toHaveBeenCalledTimes(1);
    expect((screen.getByLabelText('Hardware') as HTMLSelectElement).disabled).toBe(true);
    expect(
      (screen.getByRole('checkbox', { name: 'Safe memory mode' }) as HTMLInputElement).disabled,
    ).toBe(true);
    expect(screen.getByRole('checkbox', { name: 'Run at low priority' }).matches(':disabled')).toBe(
      false,
    );
  });
});

describe('Hardware and low priority for this session', () => {
  it('changes hardware and low priority through updateSettings and offers the default back', async () => {
    const updateSettings = vi.fn();
    const onUseDefaults = vi.fn();
    const settings = { ...demoSnapshot().settings, task: 'upscale' as const, device_id: 'cpu' };
    const capabilities: CapabilityInfo = {
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
    };
    render(RunSettings, {
      settings,
      defaults: { ...settings, device_id: 'openvino:80d1', low_priority: false },
      selectedModel: { ...demoSnapshot().catalog.models[0], architecture: 'SPAN' },
      capabilities,
      usingTemporalVideo: false,
      updateSettings,
      onUseDefaults,
      refreshCapabilities: async () => true,
    });
    const select = screen.getByLabelText('Hardware') as HTMLSelectElement;
    expect(select.value).toBe('cpu');
    await fireEvent.change(select, { target: { value: 'openvino:80d1' } });
    expect(updateSettings).toHaveBeenCalledWith({ device_id: 'openvino:80d1' });

    await fireEvent.click(screen.getByRole('checkbox', { name: 'Run at low priority' }));
    expect(updateSettings).toHaveBeenCalledWith({ low_priority: true });

    const note = document.getElementById('advanced-session-choice') as HTMLElement;
    expect(note.textContent).toContain('For this session only. Default in Settings:');
    expect(note.textContent).toContain('NPU');
    await fireEvent.click(screen.getByRole('button', { name: 'Use default' }));
    expect(onUseDefaults).toHaveBeenCalledTimes(1);
  });

  it('shows no session note while the choices match the defaults', async () => {
    const settings = { ...demoSnapshot().settings, task: 'upscale' as const };
    render(RunSettings, {
      settings,
      defaults: settings,
      selectedModel: demoSnapshot().catalog.models[0],
      capabilities: base,
      usingTemporalVideo: false,
      updateSettings: vi.fn(),
      refreshCapabilities: async () => true,
    });
    expect(document.getElementById('advanced-session-choice')).toBeNull();
  });
});

describe('Precision selection', () => {
  it('defaults to Auto as the first option and updates precision through updateSettings', async () => {
    expect(demoSnapshot().settings.precision).toBe('auto');
    const update = vi.fn();
    render(RunSettings, {
      settings: { ...demoSnapshot().settings, task: 'upscale' },
      selectedModel: undefined,
      capabilities: base,
      usingTemporalVideo: false,
      updateSettings: update,
      refreshCapabilities: async () => true,
    });
    expect(options('Precision')).toEqual(['auto', 'fp32', 'fp16']);
    const select = screen.getByLabelText('Precision') as HTMLSelectElement;
    expect(select.value).toBe('auto');
    await fireEvent.change(select, { target: { value: 'fp32' } });
    expect(update).toHaveBeenLastCalledWith({ precision: 'fp32' });
    await fireEvent.change(select, { target: { value: 'auto' } });
    expect(update).toHaveBeenLastCalledWith({ precision: 'auto' });
    expect(screen.getByRole('button', { name: 'About Precision' })).toBeTruthy();
  });
});

describe('help tips in RunSettings', () => {
  it.each([
    [
      'upscale',
      false,
      [
        'Hardware',
        'Run at low priority',
        'Tile',
        'Halo',
        'Precision',
        'Safe memory mode',
        'Memory pressure',
      ],
    ],
    ['video', true, ['Hardware', 'Run at low priority', 'Memory pressure']],
  ] as const)(
    'every control in %s (temporal=%s) has a short plain tip',
    (task, temporal, expected) => {
      render(RunSettings, {
        settings: { ...demoSnapshot().settings, task },
        selectedModel: undefined,
        capabilities: base,
        usingTemporalVideo: temporal,
        updateSettings: vi.fn(),
        refreshCapabilities: async () => true,
      });
      for (const label of expected) {
        expect(screen.getByRole('button', { name: `About ${label}` })).toBeTruthy();
      }
      for (const tip of document.querySelectorAll('[role=tooltip]')) {
        if (tip.id !== 'help-hardware') {
          const words = (tip.textContent ?? '').trim().split(/\s+/).length;
          expect(words, tip.textContent ?? '').toBeLessThanOrEqual(46);
        }
      }
    },
  );
});
