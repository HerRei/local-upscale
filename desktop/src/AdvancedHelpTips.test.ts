// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AdvancedSettings from './AdvancedSettings.svelte';
import { demoSnapshot } from './lib/demo';
import type { CapabilityInfo, UiSettings } from './lib/types';

vi.mock('./lib/api', () => ({}));

afterEach(cleanup);

const GIB = 1024 ** 3;
const base = demoSnapshot().capabilities;
const device = { ...base.devices[0], free_memory: 0, recommended_tile_sizes: [64, 128] };
const macGpu = {
  ...device,
  id: 'mps',
  type: 'mps',
  name: 'Apple GPU (Metal)',
  total_memory: 16 * GIB,
};
const mac: CapabilityInfo = {
  ...base,
  system_ram_total: 16 * GIB,
  system_memory_pressure_level: 'low',
  system_memory_pressure_percent: 40,
  devices: [macGpu, base.devices[0]],
};
const cudaGpu = {
  ...device,
  id: 'cuda:0',
  type: 'cuda',
  name: 'NVIDIA GeForce RTX 3060 (CUDA)',
  is_integrated: false,
  total_memory: 12 * GIB,
  free_memory: Math.round(10.66 * GIB),
};
const cuda: CapabilityInfo = {
  ...base,
  system_ram_total: 32 * GIB,
  system_memory_pressure_level: 'low',
  system_memory_pressure_percent: 40,
  devices: [base.devices[0], cudaGpu],
};
const other: CapabilityInfo = { ...base, system_memory_pressure_level: 'moderate' };
const EXPERT_FEATURES = ['image_edit', 'edit_placement', 'edit_expert'];

interface Variant {
  name: string;
  settings: Partial<UiSettings>;
  capabilities: CapabilityInfo;
  features?: string[];
  expected: string[];
}

const variants: Variant[] = [
  {
    name: 'edit on a Mac',
    settings: { task: 'edit', device_id: 'mps' },
    capabilities: mac,
    features: EXPERT_FEATURES,
    expected: ['Edit size', 'Steps', 'Seed', 'Save to', 'Memory pressure', 'Text encoder', 'VAE'],
  },
  {
    name: 'edit on CUDA',
    settings: { task: 'edit', device_id: 'cuda:0' },
    capabilities: cuda,
    features: EXPERT_FEATURES,
    expected: [
      'Edit size',
      'Steps',
      'Seed',
      'Save to',
      'Memory pressure',
      'Text encoder',
      'VAE',
      'GPU memory',
      'Allow any edit size',
    ],
  },
  {
    name: 'edit elsewhere',
    settings: { task: 'edit' },
    capabilities: other,
    expected: ['Edit size', 'Steps', 'Seed', 'Save to', 'Memory pressure'],
  },
];

/** A control that needs no tip, keyed by its label, with the reason. */
const withoutTip: Record<string, string> = {};

async function open(variant: Variant): Promise<HTMLElement> {
  render(AdvancedSettings, {
    settings: { ...demoSnapshot().settings, ...variant.settings },
    capabilities: variant.capabilities,
    features: variant.features ?? [],
    updateSettings: () => undefined,
    chooseOutput: async () => undefined,
    refreshCapabilities: async () => true,
  });
  await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
  const expert = screen.queryByRole('button', { name: /Expert/ });
  if (expert) await fireEvent.click(expert);
  return document.querySelector('.advanced-controls') as HTMLElement;
}

/** Every labelled control: fields, checkboxes, the folder button and the memory line. */
function controls(root: HTMLElement): { label: string; row: Element }[] {
  const found: { label: string; row: Element }[] = [];
  for (const field of root.querySelectorAll<HTMLInputElement | HTMLSelectElement>(
    'select, input',
  )) {
    const label = field.labels?.[0];
    const text = (label?.cloneNode(true) as HTMLElement | undefined) ?? undefined;
    text?.querySelectorAll('input, b, button, [role=tooltip]').forEach((node) => node.remove());
    const name = text?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
    expect(name, `an unlabelled ${field.tagName} (${field.id || field.type})`).not.toBe('');
    const row =
      field.closest('.check-line') ??
      field.closest('.field-row, .range-row, .field-grid > div, label.check-row') ??
      field;
    found.push({ label: name, row });
  }
  for (const field of root.querySelectorAll('.directory-field')) {
    const label = field.querySelector('small')?.textContent?.trim();
    if (label) found.push({ label, row: field });
  }
  for (const box of root.querySelectorAll('[role=checkbox]')) {
    const label = box.textContent?.replace(/\s+/g, ' ').trim();
    const row = box.closest('.check-line') ?? box;
    if (label) found.push({ label, row });
  }
  const memory = root.querySelector('.hardware-card');
  if (memory) found.push({ label: 'Memory pressure', row: memory });
  return found;
}

describe('every labelled control in Advanced has a help tip or a reason not to', () => {
  for (const variant of variants) {
    it(variant.name, async () => {
      const found = controls(await open(variant));
      const labels = found.map((item) => item.label);
      for (const expected of variant.expected) expect(labels).toContain(expected);
      const missing = found
        .filter((item) => !item.row.querySelector('.help-tip') && !(item.label in withoutTip))
        .map((item) => item.label);
      expect(missing).toEqual([]);
    });
  }

  it('keeps no stale entries in the list of controls without a tip', async () => {
    const seen = new Set<string>();
    for (const variant of variants) {
      for (const item of controls(await open(variant))) seen.add(item.label);
      cleanup();
    }
    for (const label of Object.keys(withoutTip)) expect(seen.has(label)).toBe(true);
  });
});

describe('the wording of the help tips', () => {
  it('stays short and plain in every variant', async () => {
    const texts = new Map<string, string>();
    for (const variant of variants) {
      const root = await open(variant);
      for (const tip of root.querySelectorAll('[role=tooltip]')) {
        // The Hardware tip, written earlier, is longer; the four Expert tips explain
        // Automatic, GPU and CPU in up to 73 words.
        if (tip.id !== 'help-hardware') texts.set(tip.textContent ?? '', tip.id);
      }
      cleanup();
    }
    expect(texts.size).toBeGreaterThanOrEqual(6);
    const expertIds = new Set([
      'help-text-encoder',
      'help-vae',
      'help-gpu-memory',
      'help-allow-any-edit-size',
    ]);
    for (const [text, id] of texts) {
      const words = text.trim().split(/\s+/).length;
      expect(words, text).toBeLessThanOrEqual(expertIds.has(id) ? 75 : 46);
      expect(text, text).not.toMatch(/!|\b(simply|just|easily)\b/i);
      expect(text, text).not.toMatch(/colour/);
    }
  });
});

describe('while a job runs, the whole Advanced section is locked', () => {
  const usable = (element: Element) =>
    element.classList.contains('help-tip') ||
    element.classList.contains('memory-refresh') ||
    element.classList.contains('live-setting');

  for (const variant of variants) {
    it(variant.name, async () => {
      const fieldset = document.body.appendChild(document.createElement('fieldset'));
      render(AdvancedSettings, {
        target: fieldset,
        props: {
          settings: { ...demoSnapshot().settings, ...variant.settings },
          capabilities: variant.capabilities,
          features: variant.features ?? [],
          updateSettings: () => undefined,
          chooseOutput: async () => undefined,
          refreshCapabilities: async () => true,
        },
      });
      await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
      const expert = screen.queryByRole('button', { name: /Expert/ });
      if (expert) await fireEvent.click(expert);
      fieldset.disabled = true;
      const interactive = [
        ...fieldset.querySelectorAll(
          'button, select, input, textarea, a[href], [role=button], [tabindex]',
        ),
      ];
      expect(interactive.length).toBeGreaterThanOrEqual(3);
      const openControls = interactive.filter(
        (item) => !usable(item) && !item.matches(':disabled'),
      );
      expect(openControls.map((item) => item.outerHTML.slice(0, 80))).toEqual([]);
      expect(interactive.filter(usable).length).toBeGreaterThanOrEqual(2);
      fieldset.remove();
    });
  }
});
