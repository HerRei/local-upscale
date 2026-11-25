import type { UiSettings } from './types';

export type Appearance = UiSettings['appearance'];
type Contrast = NonNullable<UiSettings['contrast']>;
type ViewerBackground = NonNullable<UiSettings['viewer_background']>;
type Query = Pick<MediaQueryList, 'matches' | 'addEventListener' | 'removeEventListener'>;
type ThemeSettings = Pick<
  UiSettings,
  'appearance' | 'colour_blind_palette' | 'contrast' | 'viewer_background'
>;

const query = (text: string): Query =>
  typeof window.matchMedia === 'function'
    ? window.matchMedia(text)
    : { matches: false, addEventListener() {}, removeEventListener() {} };

/** Apply the saved choice and follow system changes until the window is closed. */
export function createThemeController(
  media: Query = query('(prefers-color-scheme: light)'),
  nativeTheme?: (theme: 'dark' | 'light' | null) => void,
  systemContrast: Query[] = [query('(prefers-contrast: more)'), query('(forced-colors: active)')],
): {
  apply: (settings: ThemeSettings) => void;
  dispose: () => void;
} {
  let appearance: Appearance = 'dark';
  let colourBlind = false;
  let contrast: Contrast = 'system';
  let viewerBackground: ViewerBackground = 'checker';
  let lastNative: Appearance | undefined;
  const render = () => {
    const theme = appearance === 'system' ? (media.matches ? 'light' : 'dark') : appearance;
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.palette = colourBlind ? 'colour-blind' : 'default';
    document.documentElement.dataset.contrast =
      contrast === 'system'
        ? systemContrast.some((q) => q.matches)
          ? 'high'
          : 'standard'
        : contrast;
    document.documentElement.dataset.viewerBg = viewerBackground;
    document.documentElement.style.colorScheme = theme;
  };
  media.addEventListener('change', render);
  for (const q of systemContrast) q.addEventListener('change', render);
  return {
    apply(settings) {
      appearance = settings.appearance ?? 'dark';
      colourBlind = settings.colour_blind_palette ?? false;
      contrast = settings.contrast ?? 'system';
      viewerBackground = settings.viewer_background ?? 'checker';
      render();
      if (appearance !== lastNative) {
        lastNative = appearance;
        nativeTheme?.(appearance === 'system' ? null : appearance);
      }
    },
    dispose: () => {
      media.removeEventListener('change', render);
      for (const q of systemContrast) q.removeEventListener('change', render);
    },
  };
}
