// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AdvancedSettings from './AdvancedSettings.svelte';
import { demoSnapshot } from './lib/demo';
import type { CapabilityInfo, UiSettings } from './lib/types';

vi.mock('./lib/api', () => ({ chooseExternalFFmpeg: vi.fn(), detectExternalFFmpeg: vi.fn() }));

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
// Windows and Linux: no system codecs, no HEIC.
const other: CapabilityInfo = { ...base, system_memory_pressure_level: 'moderate' };

interface Variant {
  name: string;
  settings: Partial<UiSettings>;
  capabilities: CapabilityInfo;
  temporal?: boolean;
  interpolation?: boolean;
  /** Labels that must be found, so an enumeration that finds nothing cannot pass. */
  expected: string[];
}

const IMAGE = ['Output scale', 'Format', 'Preserve safe metadata and color profile'];
const TAIL = ['Save to', 'Hardware', 'Interface text', 'Memory pressure'];
const GRID = ['Tile', 'Halo', 'Precision', 'Safe memory mode'];
const VIDEO = ['Container', 'Video format', 'External FFmpeg', 'Video quality · CRF'];

const variants: Variant[] = [
  {
    name: 'upscale on a Mac with HEIC',
    settings: { task: 'upscale', output_format: 'heic' },
    capabilities: mac,
    expected: [...IMAGE, 'HEIC quality', ...TAIL, ...GRID],
  },
  {
    name: 'upscale elsewhere with JPEG',
    settings: { task: 'upscale', output_format: 'jpg' },
    capabilities: other,
    expected: [...IMAGE, 'JPEG quality', ...TAIL, ...GRID],
  },
  {
    name: 'restore with PNG',
    settings: { task: 'denoise', output_format: 'png' },
    capabilities: other,
    expected: ['Format', ...TAIL, ...GRID],
  },
  {
    name: 'video frame by frame on a Mac',
    settings: { task: 'video', video_interpolation: 'x2' },
    capabilities: mac,
    interpolation: true,
    expected: [
      'Output scale',
      ...VIDEO,
      'De-flicker · Labs',
      'Frame rate',
      'Interpolate',
      ...TAIL,
      ...GRID,
    ],
  },
  {
    name: 'video frame by frame elsewhere',
    settings: { task: 'video', video_interpolation: 'off' },
    capabilities: other,
    interpolation: true,
    expected: ['Output scale', ...VIDEO, 'De-flicker · Labs', 'Frame rate', ...TAIL, ...GRID],
  },
  {
    name: 'video with a temporal model',
    settings: { task: 'video', video_target_resolution: 1080 },
    capabilities: mac,
    temporal: true,
    expected: [...VIDEO, ...TAIL],
  },
  {
    name: 'edit on a Mac',
    settings: { task: 'edit' },
    capabilities: mac,
    expected: ['Edit size', 'Steps', 'Seed', ...TAIL],
  },
];

/** A control that needs no tip, keyed by its label, with the reason. */
const withoutTip: Record<string, string> = {};

async function open(variant: Variant): Promise<HTMLElement> {
  render(AdvancedSettings, {
    settings: { ...demoSnapshot().settings, ...variant.settings },
    selectedModel: undefined,
    capabilities: variant.capabilities,
    usingTemporalVideo: variant.temporal ?? false,
    interpolationSupported: variant.interpolation ?? false,
    updateSettings: () => undefined,
    chooseOutput: async () => undefined,
    refreshCapabilities: async () => true,
  });
  await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
  return document.querySelector('.advanced-controls') as HTMLElement;
}

/** Every labelled control: fields, checkboxes, sliders, the folder button and the memory line. */
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
    const texts = new Set<string>();
    for (const variant of variants) {
      const root = await open(variant);
      for (const tip of root.querySelectorAll('[role=tooltip]')) {
        // The Hardware tip, written earlier, is the one longer text.
        if (tip.id !== 'help-hardware') texts.add(tip.textContent ?? '');
      }
      cleanup();
    }
    expect(texts.size).toBeGreaterThan(20);
    for (const text of texts) {
      const words = text.trim().split(/\s+/).length;
      expect(words, text).toBeLessThanOrEqual(46);
      expect(text, text).not.toMatch(/!|\b(simply|just|easily)\b/i);
      expect(text, text).not.toMatch(/colour/);
    }
  });
});

describe('while a job runs, the whole Advanced section is locked', () => {
  // Help tips and Refresh stay usable; every other interactive thing must be disabled, so a
  // control added later that sits outside the locked fieldset fails here.
  const usable = (element: Element) =>
    element.classList.contains('help-tip') || element.classList.contains('memory-refresh');

  for (const variant of variants) {
    it(variant.name, async () => {
      const fieldset = document.body.appendChild(document.createElement('fieldset'));
      render(AdvancedSettings, {
        target: fieldset,
        props: {
          settings: { ...demoSnapshot().settings, ...variant.settings },
          selectedModel: undefined,
          capabilities: variant.capabilities,
          usingTemporalVideo: variant.temporal ?? false,
          interpolationSupported: variant.interpolation ?? false,
          updateSettings: () => undefined,
          chooseOutput: async () => undefined,
          refreshCapabilities: async () => true,
        },
      });
      await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
      fieldset.disabled = true;
      const interactive = [
        ...fieldset.querySelectorAll(
          'button, select, input, textarea, a[href], [role=button], [tabindex]',
        ),
      ];
      expect(interactive.length).toBeGreaterThan(8);
      const open = interactive.filter((item) => !usable(item) && !item.matches(':disabled'));
      expect(open.map((item) => item.outerHTML.slice(0, 80))).toEqual([]);
      expect(interactive.filter(usable).length).toBeGreaterThan(3);
      fieldset.remove();
    });
  }
});
