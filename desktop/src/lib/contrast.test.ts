import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];

function splitSelectors(rawSelector: string): string[] {
  return rawSelector
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replaceAll("'", '"')
    .split(',')
    .map((s) => s.trim().replace(/\s+/g, ' '))
    .filter(Boolean);
}

function tokensForSelector(target: string): string[] {
  const names: string[] = [];
  for (const [, rawSelector, body] of rules) {
    if (!splitSelectors(rawSelector).includes(target)) continue;
    for (const [, name] of body.matchAll(/(--[\w-]+):\s*([^;]+);/g)) {
      names.push(name);
    }
  }
  return names;
}

function palette(light: boolean, accessible: boolean, high = false): Record<string, string> {
  const wanted = [
    ':root',
    ...(light ? ['[data-theme="light"]'] : []),
    ...(accessible ? ['[data-palette="colour-blind"]'] : []),
    ...(light && accessible ? ['[data-theme="light"][data-palette="colour-blind"]'] : []),
    ...(high ? ['[data-contrast="high"]'] : []),
    ...(light && high ? ['[data-theme="light"][data-contrast="high"]'] : []),
    ...(accessible && high ? ['[data-contrast="high"][data-palette="colour-blind"]'] : []),
    ...(light && accessible && high
      ? ['[data-theme="light"][data-contrast="high"][data-palette="colour-blind"]']
      : []),
  ];
  const result: Record<string, string> = {};
  for (const [, rawSelector, body] of rules) {
    const selectors = splitSelectors(rawSelector);
    if (!selectors.some((selector) => wanted.includes(selector))) continue;
    for (const [, name, value] of body.matchAll(/(--[\w-]+):\s*([^;]+);/g))
      result[name] = value.trim();
  }
  return result;
}

function rgb(value: string, background = [255, 255, 255]): number[] {
  if (value.startsWith('#')) {
    let hex = value.slice(1);
    if (hex.length === 3) hex = [...hex].map((c) => c + c).join('');
    return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  }
  const parts = value.match(/[\d.]+/g)!.map(Number);
  return parts
    .slice(0, 3)
    .map((v, i) => v * (parts[3] ?? 1) + background[i] * (1 - (parts[3] ?? 1)));
}

function ratio(a: number[], b: number[]): number {
  const luminance = (color: number[]) =>
    color
      .map((v) => v / 255)
      .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
      .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
  const values = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (values[1] + 0.05) / (values[0] + 0.05);
}

describe.each([
  [false, false],
  [true, false],
  [false, true],
  [true, true],
])('contrast light=%s accessible=%s', (light, accessible) => {
  it('keeps text legible on its surfaces', () => {
    const p = palette(light, accessible);
    const pairs = ['--base', '--panel', '--raised'].flatMap((surface) =>
      ['--text', '--secondary'].map((text) => [text, surface]),
    );
    pairs.push(
      ['--on-accent', '--accent'],
      ['--recipe-button-text', '--accent'],
      ['--danger-text-warm', '--danger-surface'],
      ['--warning-text', '--base'],
      ['--success-strong', '--base'],
    );
    const panel = rgb(p['--panel']);
    expect(
      ratio(rgb(p['--danger-border'], panel), panel),
      '--danger-border',
    ).toBeGreaterThanOrEqual(3);
    if (light || accessible) {
      expect(
        ratio(rgb(p['--warning-border'], panel), panel),
        '--warning-border',
      ).toBeGreaterThanOrEqual(3);
    }
    for (const [foreground, fill] of [
      ['--danger-text', '--danger-fill'],
      ['--warning-text', '--warning-fill'],
      ['--success-strong', '--success-fill'],
    ]) {
      expect(
        ratio(rgb(p[foreground]), rgb(p[fill], panel)),
        `${foreground} on ${fill}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
    for (const [foreground, background] of pairs) {
      expect(
        ratio(rgb(p[foreground]), rgb(p[background])),
        `${foreground} on ${background}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe.each([
  [false, false],
  [true, false],
  [false, true],
  [true, true],
])('high contrast light=%s accessible=%s', (light, accessible) => {
  it('meets 7:1 text contrast and 3:1 UI boundary contrast', () => {
    const p = palette(light, accessible, true);
    const surfaces = ['--base', '--panel', '--raised', '--hover'];
    const textPairs: [string, string][] = ['--base', '--panel', '--raised'].flatMap((surface) =>
      ['--text', '--secondary'].map((text): [string, string] => [text, surface]),
    );
    for (const surface of surfaces) {
      textPairs.push(['--tertiary', surface]);
    }
    textPairs.push(
      ['--secondary', '--hover'],
      ['--chip-text', '--chip-bg'],
      ['--hud-text', '--hud-bg'],
      ['--on-accent', '--accent'],
      ['--on-accent', '--accent-hi'],
      ['--recipe-button-text', '--accent'],
      ['--danger-text-warm', '--danger-surface'],
      ['--warning-text', '--base'],
      ['--success-strong', '--base'],
    );
    const panel = rgb(p['--panel']);
    for (const [foreground, fill] of [
      ['--danger-text', '--danger-fill'],
      ['--warning-text', '--warning-fill'],
      ['--success-strong', '--success-fill'],
    ]) {
      expect(
        ratio(rgb(p[foreground]), rgb(p[fill], panel)),
        `${foreground} on ${fill}`,
      ).toBeGreaterThanOrEqual(7);
    }
    for (const [foreground, background] of textPairs) {
      expect(
        ratio(rgb(p[foreground]), rgb(p[background])),
        `${foreground} on ${background}`,
      ).toBeGreaterThanOrEqual(7);
    }

    const uiPairs: [string, string][] = [];
    for (const surface of surfaces) {
      for (const border of ['--border', '--chip-border', '--accent-line', '--focus', '--accent']) {
        uiPairs.push([border, surface]);
      }
    }
    uiPairs.push(
      ['--accent-hi', '--well-checker'],
      ['--accent-hi', '--divider'],
      ['--scrubber-fill', '--scrubber-track'],
      ['--danger-border', '--panel'],
      ['--warning-border', '--panel'],
    );
    for (const [foreground, background] of uiPairs) {
      const bg = rgb(p[background]);
      expect(
        ratio(rgb(p[foreground], bg), bg),
        `${foreground} against ${background}`,
      ).toBeGreaterThanOrEqual(3);
    }
  });
});

describe('high contrast token sets', () => {
  it('keep dark/light and colour-blind token sets symmetric and defined in :root', () => {
    const rootTokens = new Set(tokensForSelector(':root'));
    const block1 = new Set(tokensForSelector('[data-contrast="high"]'));
    const block2 = new Set(tokensForSelector('[data-theme="light"][data-contrast="high"]'));
    const block3 = new Set(
      tokensForSelector('[data-contrast="high"][data-palette="colour-blind"]'),
    );
    const block4 = new Set(
      tokensForSelector('[data-theme="light"][data-contrast="high"][data-palette="colour-blind"]'),
    );
    const existingCb = new Set(tokensForSelector('[data-palette="colour-blind"]'));

    expect(block1.size).toBe(117);
    expect(block2).toEqual(block1);
    expect(block3.size).toBe(30);
    expect(block4).toEqual(block3);
    expect(block3).toEqual(existingCb);

    for (const set of [block1, block2, block3, block4]) {
      for (const token of set) {
        expect(rootTokens.has(token), `${token} missing from :root`).toBe(true);
      }
    }
  });
});
