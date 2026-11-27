// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SystemAccent } from './api';
import {
  accentText,
  contrastRatio,
  createAccentController,
  edge,
  onAccent,
  shade,
  type ThemeMode,
} from './accent';

const SYSTEM_ACCENT_ROWS: {
  name: string;
  accent: string;
  theme: ThemeMode;
  on: '#ffffff' | '#000000';
  ratio: string;
  againstPanel: string;
  edge: string;
  link: string;
}[] = [
  {
    name: 'Blue',
    accent: '#007aff',
    theme: 'dark',
    on: '#000000',
    ratio: '5.23',
    againstPanel: '4.29',
    edge: 'transparent',
    link: '#3395ff',
  },
  {
    name: 'Blue',
    accent: '#007aff',
    theme: 'light',
    on: '#000000',
    ratio: '5.23',
    againstPanel: '3.85',
    edge: 'transparent',
    link: '#0062cc',
  },
  {
    name: 'Purple',
    accent: '#a550a7',
    theme: 'dark',
    on: '#ffffff',
    ratio: '4.88',
    againstPanel: '3.53',
    edge: 'transparent',
    link: '#bc7cbd',
  },
  {
    name: 'Purple',
    accent: '#953d96',
    theme: 'light',
    on: '#ffffff',
    ratio: '6.20',
    againstPanel: '5.94',
    edge: 'transparent',
    link: '#953d96',
  },
  {
    name: 'Pink',
    accent: '#f74f9e',
    theme: 'dark',
    on: '#000000',
    ratio: '6.57',
    againstPanel: '5.39',
    edge: 'transparent',
    link: '#f758a3',
  },
  {
    name: 'Pink',
    accent: '#f74f9e',
    theme: 'light',
    on: '#000000',
    ratio: '6.57',
    againstPanel: '3.06',
    edge: 'transparent',
    link: '#ad376f',
  },
  {
    name: 'Red',
    accent: '#ff5257',
    theme: 'dark',
    on: '#000000',
    ratio: '6.60',
    againstPanel: '5.41',
    edge: 'transparent',
    link: '#ff5257',
  },
  {
    name: 'Red',
    accent: '#e0383e',
    theme: 'light',
    on: '#000000',
    ratio: '4.80',
    againstPanel: '4.19',
    edge: 'transparent',
    link: '#be3035',
  },
  {
    name: 'Orange',
    accent: '#f7821b',
    theme: 'dark',
    on: '#000000',
    ratio: '8.16',
    againstPanel: '6.70',
    edge: 'transparent',
    link: '#f7821b',
  },
  {
    name: 'Orange',
    accent: '#f7821b',
    theme: 'light',
    on: '#000000',
    ratio: '8.16',
    againstPanel: '2.46',
    edge: '#de7518',
    link: '#a15412',
  },
  {
    name: 'Yellow',
    accent: '#ffc600',
    theme: 'dark',
    on: '#000000',
    ratio: '13.33',
    againstPanel: '10.93',
    edge: 'transparent',
    link: '#ffc600',
  },
  {
    name: 'Yellow',
    accent: '#ffc726',
    theme: 'light',
    on: '#000000',
    ratio: '13.45',
    againstPanel: '1.50',
    edge: '#b28b1b',
    link: '#806413',
  },
  {
    name: 'Green',
    accent: '#62ba46',
    theme: 'dark',
    on: '#000000',
    ratio: '8.63',
    againstPanel: '7.08',
    edge: 'transparent',
    link: '#62ba46',
  },
  {
    name: 'Green',
    accent: '#62ba46',
    theme: 'light',
    on: '#000000',
    ratio: '8.63',
    againstPanel: '2.33',
    edge: '#539e3c',
    link: '#3b702a',
  },
  {
    name: 'Graphite',
    accent: '#8c8c8c',
    theme: 'dark',
    on: '#000000',
    ratio: '6.25',
    againstPanel: '5.12',
    edge: 'transparent',
    link: '#929292',
  },
  {
    name: 'Graphite',
    accent: '#989898',
    theme: 'light',
    on: '#000000',
    ratio: '7.28',
    againstPanel: '2.76',
    edge: '#909090',
    link: '#636363',
  },
];

const PANEL = { dark: '#1b1b1b', light: '#fafafa' } as const;
const SURFACE = { dark: '#2a2a2a', light: '#e8e8e8' } as const;
const TOKENS = [
  '--accent',
  '--accent-hi',
  '--accent-press',
  '--on-accent',
  '--accent-text',
  '--accent-edge',
];

afterEach(() => {
  const root = document.documentElement;
  delete root.dataset.theme;
  delete root.dataset.palette;
  delete root.dataset.contrast;
  for (const name of TOKENS) root.style.removeProperty(name);
});

describe('system accent tokens', () => {
  it.each(SYSTEM_ACCENT_ROWS)(
    'computes text, ratio, edge, link and shades for $name ($theme)',
    ({ accent, theme, on, ratio, againstPanel, edge: expectedEdge, link }) => {
      const picked = onAccent(accent);
      expect(picked).toBe(on);
      expect(contrastRatio(picked, accent).toFixed(2)).toBe(ratio);
      expect(contrastRatio(accent, PANEL[theme]).toFixed(2)).toBe(againstPanel);
      expect(edge(accent, PANEL[theme], theme)).toBe(expectedEdge);
      expect(accentText(accent, SURFACE[theme], theme)).toBe(link);

      const base = contrastRatio(picked, accent);
      const hover = contrastRatio(picked, shade(accent, picked, 0.08));
      const press = contrastRatio(picked, shade(accent, picked, 0.16));
      expect(hover).toBeGreaterThan(base);
      expect(press).toBeGreaterThan(hover);
    },
  );
});

describe('createAccentController', () => {
  it('sets the six tokens when all four conditions hold and clears them otherwise', async () => {
    document.documentElement.dataset.theme = 'light';
    document.documentElement.dataset.palette = 'default';
    document.documentElement.dataset.contrast = 'standard';

    const read = vi.fn<() => Promise<SystemAccent | null>>(async () => ({
      light: '#ffc726',
      dark: '#ffc600',
    }));
    const controller = createAccentController(read);
    controller.apply({ accent: 'system', colour_blind_palette: false, contrast: 'system' });
    await vi.waitFor(() => {
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#ffc726');
    });
    expect(document.documentElement.style.getPropertyValue('--on-accent')).toBe('#000000');
    expect(document.documentElement.style.getPropertyValue('--accent-text')).toBe('#806413');
    expect(document.documentElement.style.getPropertyValue('--accent-edge')).toBe('#b28b1b');
    expect(document.documentElement.style.getPropertyValue('--accent-hi')).not.toBe('');
    expect(document.documentElement.style.getPropertyValue('--accent-press')).not.toBe('');

    // Palette colour-blind removes the inline properties.
    document.documentElement.dataset.palette = 'colour-blind';
    controller.apply({ accent: 'system', colour_blind_palette: true, contrast: 'system' });
    for (const token of TOKENS) {
      expect(document.documentElement.style.getPropertyValue(token)).toBe('');
    }

    // Restoring default palette puts them back.
    document.documentElement.dataset.palette = 'default';
    controller.apply({ accent: 'system', colour_blind_palette: false, contrast: 'system' });
    expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#ffc726');

    // High contrast removes them (including via data-contrast attribute change).
    document.documentElement.dataset.contrast = 'high';
    await vi.waitFor(() => {
      for (const token of TOKENS) {
        expect(document.documentElement.style.getPropertyValue(token)).toBe('');
      }
    });

    // Standard contrast + dark theme re-applies using the dark colour.
    document.documentElement.dataset.contrast = 'standard';
    document.documentElement.dataset.theme = 'dark';
    await vi.waitFor(() => {
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#ffc600');
    });
    expect(document.documentElement.style.getPropertyValue('--accent-edge')).toBe('transparent');

    // Setting accent to 'localsr' removes them.
    controller.apply({ accent: 'localsr', colour_blind_palette: false, contrast: 'system' });
    for (const token of TOKENS) {
      expect(document.documentElement.style.getPropertyValue(token)).toBe('');
    }

    // Back to 'system', then window focus reads the host again (returning null clears them).
    controller.apply({ accent: 'system', colour_blind_palette: false, contrast: 'system' });
    expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#ffc600');
    read.mockResolvedValueOnce(null);
    window.dispatchEvent(new Event('focus'));
    await vi.waitFor(() => {
      expect(read).toHaveBeenCalledTimes(2);
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe('');
    });

    // Focus again with a new colour applies it.
    read.mockResolvedValueOnce({ light: '#953d96', dark: '#a550a7' });
    window.dispatchEvent(new Event('focus'));
    await vi.waitFor(() => {
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#a550a7');
    });
    expect(document.documentElement.style.getPropertyValue('--on-accent')).toBe('#ffffff');

    controller.dispose();
    window.dispatchEvent(new Event('focus'));
    expect(read).toHaveBeenCalledTimes(3);
  });
});
