/** Redact paths and file names from values supplied by the diagnostic host. */
export function redactDiagnostics(text: string): string {
  const redact = (value: unknown): unknown => {
    if (typeof value === 'string') {
      return value
        .replace(/(?:\/Users\/|\/home\/)[^/"\\]+/g, '~')
        .replace(/[A-Za-z]:\\Users\\[^\\"]+/gi, '~')
        .replace(/~(?:[\/\\][^\s"\\/]+)+/g, '~/[file]')
        .replace(/(?:\/[A-Za-z0-9_. -]+|[A-Za-z]:)(?:[\/\\][^\s"\\/]+)+/g, '[path]')
        .replace(/\b[^\s/\\"']+\.[a-z]{2,8}\b/gi, '[file]');
    }
    if (Array.isArray(value)) return value.map(redact);
    if (value && typeof value === 'object')
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redact(item)]));
    return value;
  };
  try {
    return JSON.stringify(redact(JSON.parse(text)), null, 2);
  } catch {
    return String(redact(text));
  }
}

export function reportUrl(
  happened: string,
  version: string,
  platform: string,
  diagnostics: string,
): string {
  const url = new URL('https://github.com/HerRei/local-upscale/issues/new');
  url.searchParams.set('template', 'bug_report.yml');
  url.searchParams.set('what-happened', happened);
  url.searchParams.set('version', version);
  url.searchParams.set('operating-system', platform);
  if (diagnostics) url.searchParams.set('hardware', diagnostics);
  return url.toString();
}
