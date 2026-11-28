// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import OutputSheet from './OutputSheet.svelte';
import { demoSnapshot } from './lib/demo';
import type { CapabilityInfo, MediaItem, UiSettings } from './lib/types';

vi.mock('./lib/api', () => ({}));

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

const hdrVideo: MediaItem = {
  id: 'hdr',
  name: 'hdr.mp4',
  path: '/hdr.mp4',
  kind: 'video',
  width: 1920,
  height: 1080,
  frame_count: 240,
  fps: 24,
  duration_seconds: 10,
  preview_data_url: '',
  probe_status: 'ready',
  error: '',
  selected: true,
  hdr_format: 'HLG',
};

interface Variant {
  name: string;
  settings: Partial<UiSettings>;
  capabilities: CapabilityInfo;
  selectedMedia?: MediaItem;
  preservingHdr?: boolean;
  expected: string[];
}

const IMAGE = ['Format', 'Preserve safe metadata and colour profile'];
const TAIL = ['Save to'];
const VIDEO = ['Container', 'Video format', 'Video quality · CRF', 'Clean up audio'];

const variants: Variant[] = [
  {
    name: 'upscale on a Mac with HEIC',
    settings: { task: 'upscale', output_format: 'heic' },
    capabilities: mac,
    expected: [...IMAGE, 'HEIC quality', ...TAIL],
  },
  {
    name: 'upscale elsewhere with JPEG',
    settings: { task: 'upscale', output_format: 'jpg' },
    capabilities: base,
    expected: [...IMAGE, 'JPEG quality', ...TAIL],
  },
  {
    name: 'restore with PNG',
    settings: { task: 'denoise', output_format: 'png' },
    capabilities: base,
    expected: ['Format', ...TAIL],
  },
  {
    name: 'video on a Mac with audio cleanup',
    settings: { task: 'video', video_clean_audio: true },
    capabilities: mac,
    expected: [...VIDEO, 'Audio model', ...TAIL],
  },
  {
    name: 'HDR video with Colour output',
    settings: { task: 'video', video_hdr_mode: 'preserve' },
    capabilities: mac,
    selectedMedia: hdrVideo,
    preservingHdr: true,
    expected: [...VIDEO, 'Colour output', ...TAIL],
  },
];

/** A control that needs no tip, keyed by its label, with the reason. */
const withoutTip: Record<string, string> = {
  'Audio model': 'Summary text below the select describes the selected model.',
};

function mountVariant(variant: Variant, disabled = false): HTMLElement {
  const snapshot = demoSnapshot();
  const audioModels = snapshot.catalog.audio_models ?? [];
  render(OutputSheet, {
    settings: { ...snapshot.settings, ...variant.settings },
    selectedMedia: variant.selectedMedia,
    capabilities: variant.capabilities,
    usingTemporalVideo: false,
    preservingHdr: variant.preservingHdr ?? false,
    hdrPreservationAvailable: true,
    audioDisabled: false,
    audioModels,
    selectedAudioModel: audioModels[0],
    disabled,
    updateSettings: () => undefined,
    chooseOutput: async () => undefined,
  });
  return document.querySelector('.output-settings') as HTMLElement;
}

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
      field.closest('.field-row, .range-row, label.check-row') ??
      field;
    found.push({ label: name, row });
  }
  for (const field of root.querySelectorAll('.directory-field')) {
    const label = field.querySelector('small')?.textContent?.trim();
    if (label) found.push({ label, row: field });
  }
  return found;
}

describe('every labelled control in OutputSheet has a help tip or a reason not to', () => {
  for (const variant of variants) {
    it(variant.name, () => {
      const found = controls(mountVariant(variant));
      const labels = found.map((item) => item.label);
      for (const expected of variant.expected) expect(labels).toContain(expected);
      const missing = found
        .filter((item) => !item.row.querySelector('.help-tip') && !(item.label in withoutTip))
        .map((item) => item.label);
      expect(missing).toEqual([]);
    });
  }

  it('keeps no stale entries in the list of controls without a tip', () => {
    const seen = new Set<string>();
    for (const variant of variants) {
      for (const item of controls(mountVariant(variant))) seen.add(item.label);
      cleanup();
    }
    for (const label of Object.keys(withoutTip)) expect(seen.has(label)).toBe(true);
  });
});

describe('the wording of the help tips in OutputSheet', () => {
  it('stays short and plain', () => {
    const texts = new Set<string>();
    for (const variant of variants) {
      const root = mountVariant(variant);
      for (const tip of root.querySelectorAll('[role=tooltip]')) {
        if (tip.id !== 'help-colour-output') texts.add(tip.textContent ?? '');
      }
      cleanup();
    }
    expect(texts.size).toBeGreaterThanOrEqual(7);
    for (const text of texts) {
      const words = text.trim().split(/\s+/).length;
      expect(words, text).toBeLessThanOrEqual(46);
      expect(text, text).not.toMatch(/!|\b(simply|just|easily)\b/i);
      // The metadata tip says "colour profile" to match the sheet's British spelling.
      expect(text, text).not.toMatch(/colour(?! profile)/);
    }
  });
});

describe('while a job runs, OutputSheet controls are disabled while help tips stay open', () => {
  for (const variant of variants) {
    it(variant.name, () => {
      const root = mountVariant(variant, true);
      expect(root.classList.contains('locked')).toBe(true);
      const interactive = [
        ...root.querySelectorAll(
          'button, select, input, textarea, a[href], [role=button], [tabindex]',
        ),
      ];
      expect(interactive.length).toBeGreaterThanOrEqual(3);
      const open = interactive.filter(
        (item) => !item.classList.contains('help-tip') && !item.matches(':disabled'),
      );
      expect(open.map((item) => item.outerHTML.slice(0, 80))).toEqual([]);
      expect(screen.getAllByRole('button', { name: /^About / }).length).toBeGreaterThanOrEqual(2);
    });
  }
});
