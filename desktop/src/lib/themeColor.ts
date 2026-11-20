// Canvas does not resolve CSS variables, so read the current value from the document.
export function themeColor(name: string): string {
  return typeof document === 'undefined'
    ? ''
    : getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
