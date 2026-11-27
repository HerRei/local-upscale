// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AdvancedSettings from './AdvancedSettings.svelte';
import { demoSnapshot } from './lib/demo';
import type { CapabilityInfo, EditModel, UiSettings } from './lib/types';

vi.mock('./lib/api', () => ({}));

afterEach(cleanup);

const base = demoSnapshot().capabilities;
const editModel: EditModel = demoSnapshot().edit.models[0];

async function open(
  settings: Partial<UiSettings> = {},
  capabilities: CapabilityInfo = base,
  updateSettings = vi.fn(),
  chooseOutput = vi.fn(async () => undefined),
) {
  render(AdvancedSettings, {
    settings: { ...demoSnapshot().settings, task: 'edit', ...settings },
    editModel,
    capabilities,
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
});
