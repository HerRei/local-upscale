// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';
import { themeColor } from './themeColor';

it('reads canvas colours from the active theme tokens', () => {
  const style = document.createElement('style');
  style.textContent = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8');
  document.head.append(style);
  expect(themeColor('--preview-grid-overlay-a')).toBe('#10172138');
  document.documentElement.dataset.theme = 'light';
  expect(themeColor('--preview-grid-overlay-a')).toBe('#ffffff38');
  delete document.documentElement.dataset.theme;
  style.remove();
});
