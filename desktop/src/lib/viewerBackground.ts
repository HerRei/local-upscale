import type { UiSettings } from './types';

export type ViewerBackground = NonNullable<UiSettings['viewer_background']>;

export const VIEWER_BACKGROUNDS: { id: ViewerBackground; label: string }[] = [
  { id: 'checker', label: 'Checkerboard' },
  { id: 'black', label: 'Black' },
  { id: 'grey', label: 'Grey' },
  { id: 'white', label: 'White' },
];

export function backgroundLabel(id: ViewerBackground): string {
  return VIEWER_BACKGROUNDS.find((item) => item.id === id)?.label ?? 'Checkerboard';
}

export function nextBackground(id: ViewerBackground): ViewerBackground {
  const index = VIEWER_BACKGROUNDS.findIndex((item) => item.id === id);
  return VIEWER_BACKGROUNDS[(index + 1) % VIEWER_BACKGROUNDS.length].id;
}
