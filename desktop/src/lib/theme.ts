import type { UiSettings } from './types';

export type Appearance = UiSettings['appearance'];

/** Apply the saved choice and follow system changes until the window is closed. */
export function createThemeController(
  media: Pick<
    MediaQueryList,
    'matches' | 'addEventListener' | 'removeEventListener'
  > = typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: light)')
    : { matches: false, addEventListener() {}, removeEventListener() {} },
  nativeTheme?: (theme: 'dark' | 'light' | null) => void,
): {
  apply: (settings: Pick<UiSettings, 'appearance' | 'colour_blind_palette'>) => void;
  dispose: () => void;
} {
  let appearance: Appearance = 'dark';
  let colourBlind = false;
  let lastNative: Appearance | undefined;
  const render = () => {
    const theme = appearance === 'system' ? (media.matches ? 'light' : 'dark') : appearance;
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.palette = colourBlind ? 'colour-blind' : 'default';
    document.documentElement.style.colorScheme = theme;
  };
  media.addEventListener('change', render);
  return {
    apply(settings) {
      appearance = settings.appearance ?? 'dark';
      colourBlind = settings.colour_blind_palette ?? false;
      render();
      if (appearance !== lastNative) {
        lastNative = appearance;
        nativeTheme?.(appearance === 'system' ? null : appearance);
      }
    },
    dispose: () => media.removeEventListener('change', render),
  };
}
