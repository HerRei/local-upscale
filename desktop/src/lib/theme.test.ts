// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { createThemeController } from './theme';

function mockQuery(matches = false) {
  let listener: (() => void) | undefined;
  return {
    matches,
    addEventListener: vi.fn((_event, fn) => {
      listener = fn as () => void;
    }),
    removeEventListener: vi.fn(),
    fire(next?: boolean) {
      if (next !== undefined) this.matches = next;
      listener?.();
    },
  };
}

describe('appearance', () => {
  it('applies saved settings, follows system changes, and releases its listener', () => {
    let change: (() => void) | undefined;
    const media = {
      matches: false,
      addEventListener: vi.fn((_event, listener) => {
        change = listener as () => void;
      }),
      removeEventListener: vi.fn(),
    };
    const native = vi.fn();
    const theme = createThemeController(media, native);
    theme.apply({ appearance: 'light', colour_blind_palette: true });
    expect(document.documentElement.dataset).toMatchObject({
      theme: 'light',
      palette: 'colour-blind',
    });
    expect(document.documentElement.style.colorScheme).toBe('light');
    expect(native).toHaveBeenLastCalledWith('light');
    theme.apply({ appearance: 'dark', colour_blind_palette: false });
    expect(document.documentElement.dataset).toMatchObject({ theme: 'dark', palette: 'default' });
    theme.apply({ appearance: 'system', colour_blind_palette: false });
    expect(native).toHaveBeenLastCalledWith(null);
    expect(document.documentElement.dataset.theme).toBe('dark');
    media.matches = true;
    change?.();
    expect(document.documentElement.dataset.theme).toBe('light');
    theme.dispose();
    expect(media.removeEventListener).toHaveBeenCalledWith('change', change);
  });

  it('resolves contrast and viewer background and reacts to system contrast queries', () => {
    const media = mockQuery(false);
    const prefersContrast = mockQuery(false);
    const forcedColors = mockQuery(false);
    const theme = createThemeController(media, undefined, [prefersContrast, forcedColors]);

    // Settings without contrast and viewer_background default to standard and checker.
    theme.apply({ appearance: 'dark', colour_blind_palette: false });
    expect(document.documentElement.dataset.contrast).toBe('standard');
    expect(document.documentElement.dataset.viewerBg).toBe('checker');

    // System contrast follows prefers-contrast: more.
    theme.apply({ appearance: 'dark', colour_blind_palette: false, contrast: 'system' });
    expect(document.documentElement.dataset.contrast).toBe('standard');
    prefersContrast.fire(true);
    expect(document.documentElement.dataset.contrast).toBe('high');

    // Only forced-colors true also resolves to high.
    prefersContrast.fire(false);
    expect(document.documentElement.dataset.contrast).toBe('standard');
    forcedColors.fire(true);
    expect(document.documentElement.dataset.contrast).toBe('high');

    // Explicit standard ignores active system contrast queries.
    prefersContrast.fire(true);
    theme.apply({ appearance: 'dark', colour_blind_palette: false, contrast: 'standard' });
    expect(document.documentElement.dataset.contrast).toBe('standard');

    // Explicit high stays high when both system queries are false.
    prefersContrast.fire(false);
    forcedColors.fire(false);
    theme.apply({
      appearance: 'dark',
      colour_blind_palette: false,
      contrast: 'high',
      viewer_background: 'grey',
    });
    expect(document.documentElement.dataset.contrast).toBe('high');
    expect(document.documentElement.dataset.viewerBg).toBe('grey');

    // Dispose removes listeners from all three queries.
    theme.dispose();
    expect(media.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function));
    expect(prefersContrast.removeEventListener).toHaveBeenCalledWith(
      'change',
      expect.any(Function),
    );
    expect(forcedColors.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function));
  });
});
