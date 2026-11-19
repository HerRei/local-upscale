// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { createThemeController } from './theme';

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
});
