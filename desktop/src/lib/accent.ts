import { systemAccent, type SystemAccent } from './api';
import type { UiSettings } from './types';

export type ThemeMode = 'dark' | 'light';

type AccentSettings = Partial<Pick<UiSettings, 'accent' | 'colour_blind_palette' | 'contrast'>>;

const TOKENS = [
  '--accent',
  '--accent-hi',
  '--accent-press',
  '--on-accent',
  '--accent-text',
  '--accent-edge',
] as const;

const PANEL: Record<ThemeMode, string> = { dark: '#1b1b1b', light: '#fafafa' };
const LINK_SURFACE: Record<ThemeMode, string> = { dark: '#2a2a2a', light: '#e8e8e8' };

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.startsWith('#') ? hex.slice(1) : hex;
  const full = clean.length === 3 ? [...clean].map((c) => c + c).join('') : clean;
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

function roundHalfToEven(value: number): number {
  const floor = Math.floor(value);
  const diff = value - floor;
  if (Math.abs(diff - 0.5) < 1e-9) {
    return floor % 2 === 0 ? floor : floor + 1;
  }
  return Math.round(value);
}

function rgbToHex(rgb: [number, number, number]): string {
  return (
    '#' +
    rgb
      .map((c) =>
        Math.max(0, Math.min(255, roundHalfToEven(c)))
          .toString(16)
          .padStart(2, '0'),
      )
      .join('')
  );
}

function mixHex(from: string, to: string, amount: number): string {
  const a = hexToRgb(from);
  const b = hexToRgb(to);
  return rgbToHex([
    a[0] + (b[0] - a[0]) * amount,
    a[1] + (b[1] - a[1]) * amount,
    a[2] + (b[2] - a[2]) * amount,
  ]);
}

function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex)
    .map((v) => v / 255)
    .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

export function onAccent(accent: string): '#ffffff' | '#000000' {
  return contrastRatio('#ffffff', accent) >= 4.5 ? '#ffffff' : '#000000';
}

export function shade(accent: string, on: string, amount: number): string {
  const target = on.toLowerCase() === '#ffffff' ? '#000000' : '#ffffff';
  return mixHex(accent, target, amount);
}

export function edge(accent: string, panel: string, theme: ThemeMode): string {
  if (contrastRatio(accent, panel) >= 3) return 'transparent';
  const target = theme === 'dark' ? '#ffffff' : '#000000';
  for (let step = 5; step <= 100; step += 5) {
    const mixed = mixHex(accent, target, step / 100);
    if (contrastRatio(mixed, panel) >= 3) return mixed;
  }
  return target;
}

export function accentText(accent: string, surface: string, theme: ThemeMode): string {
  const target = theme === 'dark' ? '#ffffff' : '#000000';
  for (let step = 0; step <= 100; step += 5) {
    const mixed = step === 0 ? accent.toLowerCase() : mixHex(accent, target, step / 100);
    if (contrastRatio(mixed, surface) >= 4.5) return mixed;
  }
  return target;
}

export function createAccentController(read: () => Promise<SystemAccent | null> = systemAccent): {
  apply: (settings: AccentSettings) => void;
  dispose: () => void;
} {
  let pair: SystemAccent | null = null;
  let hasApplied = false;
  let lastSettings: AccentSettings = {};
  let disposed = false;
  let readSeq = 0;

  const render = () => {
    const root = document.documentElement;
    const theme: ThemeMode = root.dataset.theme === 'light' ? 'light' : 'dark';
    const palette =
      root.dataset.palette ?? (lastSettings.colour_blind_palette ? 'colour-blind' : 'default');
    const contrast =
      root.dataset.contrast ?? (lastSettings.contrast === 'high' ? 'high' : 'standard');
    const accentPref = lastSettings.accent ?? 'system';

    if (
      !hasApplied ||
      !pair ||
      accentPref !== 'system' ||
      palette !== 'default' ||
      contrast !== 'standard'
    ) {
      for (const name of TOKENS) root.style.removeProperty(name);
      return;
    }

    const accent = theme === 'light' ? pair.light : pair.dark;
    const on = onAccent(accent);
    root.style.setProperty('--accent', accent);
    root.style.setProperty('--accent-hi', shade(accent, on, 0.08));
    root.style.setProperty('--accent-press', shade(accent, on, 0.16));
    root.style.setProperty('--on-accent', on);
    root.style.setProperty('--accent-text', accentText(accent, LINK_SURFACE[theme], theme));
    root.style.setProperty('--accent-edge', edge(accent, PANEL[theme], theme));
  };

  const refresh = async () => {
    const seq = ++readSeq;
    let next: SystemAccent | null = null;
    try {
      next = await read();
    } catch {
      next = null;
    }
    if (disposed || seq !== readSeq) return;
    pair = next;
    render();
  };

  const onFocus = () => {
    void refresh();
  };
  window.addEventListener('focus', onFocus);

  const observer =
    typeof MutationObserver !== 'undefined' ? new MutationObserver(() => render()) : null;
  observer?.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme', 'data-contrast', 'data-palette'],
  });

  void refresh();

  return {
    apply(settings) {
      hasApplied = true;
      lastSettings = settings;
      render();
    },
    dispose() {
      disposed = true;
      window.removeEventListener('focus', onFocus);
      observer?.disconnect();
    },
  };
}
