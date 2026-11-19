import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
function palette(light: boolean, accessible: boolean): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [, rawSelector, body] of rules) {
    const selector = rawSelector
      .trim()
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .trim()
      .replaceAll("'", '"');
    if (
      ![
        ':root',
        ...(light ? ['[data-theme="light"]'] : []),
        ...(accessible ? ['[data-palette="colour-blind"]'] : []),
        ...(light && accessible ? ['[data-theme="light"][data-palette="colour-blind"]'] : []),
      ].includes(selector)
    )
      continue;
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
    if (light || accessible) {
      for (const border of ['--danger-border', '--warning-border']) {
        expect(ratio(rgb(p[border]), rgb(p['--panel'])), border).toBeGreaterThanOrEqual(3);
      }
    }
    const panel = rgb(p['--panel']);
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
