// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AdvancedSettings from './AdvancedSettings.svelte';
import { demoSnapshot } from './lib/demo';
import type { CapabilityInfo, EditModel, UiSettings } from './lib/types';

vi.mock('./lib/api', () => ({}));

afterEach(cleanup);

const GIB = 1024 ** 3;
const base = demoSnapshot().capabilities;
const models = demoSnapshot().edit.models;
const editModel: EditModel = models[0];
const qwenQ3: EditModel = models.find((model) => model.model_id === 'qwen_edit_2511_q3_k_s')!;
const mageFlow: EditModel = models.find((model) => model.model_id === 'mage_flow_edit_turbo_q6_k')!;
const EXPERT_FEATURES = ['image_edit', 'edit_placement', 'edit_expert'];

const cudaCaps: CapabilityInfo = {
  ...base,
  system_ram_total: 32 * GIB,
  devices: [
    base.devices[0],
    {
      ...base.devices[0],
      id: 'cuda:0',
      type: 'cuda',
      name: 'NVIDIA GeForce RTX 3060 (CUDA)',
      is_integrated: false,
      total_memory: 12 * GIB,
      free_memory: Math.round(10.66 * GIB),
      supports_fp16: true,
    },
  ],
};

const macCaps: CapabilityInfo = {
  ...base,
  system_ram_total: 16 * GIB,
  devices: [
    {
      ...base.devices[0],
      id: 'mps',
      type: 'mps',
      name: 'Apple GPU',
      is_integrated: true,
      total_memory: 16 * GIB,
      free_memory: 12 * GIB,
      supports_fp16: true,
    },
    base.devices[0],
  ],
};

async function open(
  settings: Partial<UiSettings> = {},
  capabilities: CapabilityInfo = base,
  updateSettings = vi.fn(),
  chooseOutput = vi.fn(async () => undefined),
  options: { model?: EditModel; features?: string[] } = {},
) {
  render(AdvancedSettings, {
    settings: { ...demoSnapshot().settings, task: 'edit', ...settings },
    editModel: options.model ?? editModel,
    capabilities,
    features: options.features ?? [],
    updateSettings,
    chooseOutput,
    refreshCapabilities: async () => true,
  });
  await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
  return { updateSettings, chooseOutput };
}

describe('AdvancedSettings for Edit', () => {
  it('shows the Size, steps, seed summary and omits photo/video output controls', async () => {
    await open();
    expect(screen.getByText('Size, steps, seed')).toBeTruthy();
    expect(screen.queryByText('Format, tiles')).toBeNull();
    expect(screen.queryByLabelText('Format')).toBeNull();
    expect(screen.queryByLabelText('Container')).toBeNull();
    expect(screen.queryByLabelText('Frame rate')).toBeNull();
    expect(screen.queryByLabelText('Tile')).toBeNull();
  });

  it('clamps Edit size to the device memory limit and updates edit_max_dimension', async () => {
    const { updateSettings } = await open({ edit_max_dimension: 1024 });
    const size = screen.getByLabelText('Edit size') as HTMLSelectElement;
    expect(size.value).toBe('512');
    expect(size.querySelector<HTMLOptionElement>('option[value="1024"]')?.disabled).toBe(true);
    await fireEvent.change(size, { target: { value: '512' } });
    expect(updateSettings).toHaveBeenCalledWith({ edit_max_dimension: 512 });
  });

  it('clamps Steps and Seed inputs and saves updates', async () => {
    const { updateSettings } = await open({ edit_steps: 40, edit_seed: 42 });
    const steps = screen.getByLabelText('Steps') as HTMLInputElement;
    const seed = screen.getByLabelText('Seed') as HTMLInputElement;
    await fireEvent.change(steps, { target: { value: '80' } });
    expect(updateSettings).toHaveBeenLastCalledWith({ edit_steps: 60 });
    await fireEvent.change(seed, { target: { value: '-5' } });
    expect(updateSettings).toHaveBeenLastCalledWith({ edit_seed: 0 });
  });

  it('renders Save to and RunSettings inside Advanced', async () => {
    const { chooseOutput } = await open({ output_directory: '/Users/demo/Desktop' });
    await fireEvent.click(screen.getByRole('button', { name: 'Save to /Users/demo/Desktop' }));
    expect(chooseOutput).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('Hardware')).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: 'Run at low priority' })).toBeTruthy();
  });

  it('keeps Expert closed by default and updates its summary when a setting is saved', async () => {
    await open({ device_id: 'cuda:0' }, cudaCaps, vi.fn(), vi.fn(), {
      features: EXPERT_FEATURES,
    });
    const expert = screen.getByRole('button', { name: /Expert/ });
    expect(expert.getAttribute('aria-expanded')).toBe('false');
    expect(expert.textContent).toContain('Automatic');
    expect(screen.queryByLabelText('Text encoder')).toBeNull();

    cleanup();
    await open({ device_id: 'cuda:0', edit_vae: 'cpu' }, cudaCaps, vi.fn(), vi.fn(), {
      features: EXPERT_FEATURES,
    });
    expect(screen.getByRole('button', { name: /Expert/ }).textContent).toContain('Changed');
  });

  it('shows all four Expert controls on CUDA and omits GPU memory and any-size on a Mac', async () => {
    await open({ device_id: 'cuda:0' }, cudaCaps, vi.fn(), vi.fn(), {
      features: EXPERT_FEATURES,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Expert/ }));
    expect(screen.getByLabelText('Text encoder')).toBeTruthy();
    expect(screen.getByLabelText('VAE')).toBeTruthy();
    expect(screen.getByLabelText('GPU memory')).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: 'Allow any edit size' })).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Reset to automatic' }) as HTMLButtonElement).disabled,
    ).toBe(true);

    cleanup();
    await open({ device_id: 'mps' }, macCaps, vi.fn(), vi.fn(), {
      features: EXPERT_FEATURES,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Expert/ }));
    expect(screen.getByLabelText('Text encoder')).toBeTruthy();
    expect(screen.getByLabelText('VAE')).toBeTruthy();
    expect(screen.queryByLabelText('GPU memory')).toBeNull();
    expect(screen.queryByRole('checkbox', { name: 'Allow any edit size' })).toBeNull();
  });

  it('saves placement, GPU memory limit, allow-any-size and reset updates', async () => {
    const { updateSettings } = await open({ device_id: 'cuda:0' }, cudaCaps, vi.fn(), vi.fn(), {
      features: EXPERT_FEATURES,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Expert/ }));
    await fireEvent.change(screen.getByLabelText('Text encoder'), { target: { value: 'cpu' } });
    expect(updateSettings).toHaveBeenLastCalledWith({ edit_text_encoder: 'cpu' });
    await fireEvent.change(screen.getByLabelText('GPU memory'), { target: { value: '8192' } });
    expect(updateSettings).toHaveBeenLastCalledWith({ edit_vram_limit_mb: 8192 });

    cleanup();
    const { updateSettings: tickUpdate } = await open(
      { device_id: 'cuda:0', edit_max_dimension: 1024 },
      cudaCaps,
      vi.fn(),
      vi.fn(),
      { model: qwenQ3, features: EXPERT_FEATURES },
    );
    await fireEvent.click(screen.getByRole('button', { name: /Expert/ }));
    await fireEvent.click(screen.getByRole('checkbox', { name: 'Allow any edit size' }));
    expect(tickUpdate).toHaveBeenCalledTimes(1);
    expect(tickUpdate).toHaveBeenCalledWith({
      edit_allow_any_size: true,
      edit_max_dimension: 512,
    });

    cleanup();
    await open(
      { device_id: 'cuda:0', edit_max_dimension: 1024, edit_allow_any_size: true },
      cudaCaps,
      vi.fn(),
      vi.fn(),
      { model: qwenQ3, features: EXPERT_FEATURES },
    );
    const size = screen.getByLabelText('Edit size') as HTMLSelectElement;
    expect(size.value).toBe('1024');
    expect([...size.options].some((option) => option.disabled)).toBe(false);
    expect(size.querySelector('option[value="1024"]')?.textContent).toBe('1024 px · may not fit');

    cleanup();
    const { updateSettings: resetUpdate } = await open(
      { device_id: 'cuda:0', edit_text_encoder: 'cpu', edit_vram_limit_mb: 8192 },
      cudaCaps,
      vi.fn(),
      vi.fn(),
      { features: EXPERT_FEATURES },
    );
    await fireEvent.click(screen.getByRole('button', { name: /Expert/ }));
    await fireEvent.click(screen.getByRole('button', { name: 'Reset to automatic' }));
    expect(resetUpdate).toHaveBeenCalledTimes(1);
    expect(resetUpdate).toHaveBeenCalledWith({
      edit_text_encoder: 'auto',
      edit_vae: 'auto',
      edit_vram_limit_mb: 0,
      edit_allow_any_size: false,
    });
  });

  it('hides Expert without edit_placement and disables Mac Mage-Flow placements', async () => {
    await open({ device_id: 'cuda:0' }, cudaCaps, vi.fn(), vi.fn(), {
      features: ['image_edit'],
    });
    expect(screen.queryByRole('button', { name: /Expert/ })).toBeNull();

    cleanup();
    await open({ device_id: 'mps', edit_vae: 'gpu' }, macCaps, vi.fn(), vi.fn(), {
      model: mageFlow,
      features: EXPERT_FEATURES,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Expert/ }));
    const te = screen.getByLabelText('Text encoder') as HTMLSelectElement;
    const vae = screen.getByLabelText('VAE') as HTMLSelectElement;
    expect(te.disabled).toBe(true);
    expect(vae.disabled).toBe(true);
    expect(te.value).toBe('auto');
    expect(vae.value).toBe('auto');
    expect(te.title).toBe('Mage-Flow runs on the CPU on a Mac.');
    expect(vae.title).toBe('Mage-Flow runs on the CPU on a Mac.');
  });
});
