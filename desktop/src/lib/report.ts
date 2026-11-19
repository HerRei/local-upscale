const MEDIA_EXT_RE =
  /\.(?:jpe?g|png|webp|tiff?|dng|heic|raw|mp4|mov|mkv|avi|webm|gif|bmp|arw|nef|cr2|cr3)$/i;

function isMediaName(text: string): boolean {
  const trimmed = text.replace(/^[^A-Za-z0-9.]+|[^A-Za-z0-9.]+$/g, '');
  const dot = trimmed.lastIndexOf('.');
  return dot > 0 && MEDIA_EXT_RE.test(trimmed.slice(dot));
}

function isBoundaryBefore(line: string, index: number): boolean {
  return index === 0 || /[\s(:="'[,<]/.test(line[index - 1]);
}

function pathPlaceholderAt(line: string, index: number): string | null {
  const rest = line.slice(index);
  if (rest.startsWith('~/<path>')) return null;
  if (/^file:\/\//i.test(rest)) {
    const after = rest.slice(7).replace(/^\/+/, '');
    const isHome = /^(?:users\/|home\/|[A-Za-z]:[/\\]users[/\\])/i.test(after);
    return isHome ? '~/<path>' : '<path>';
  }
  if (!isBoundaryBefore(line, index)) return null;
  if (/^(?:\/Users\/|\/home\/|~[/\\]|[A-Za-z]:[/\\]Users[/\\])/i.test(rest)) {
    return '~/<path>';
  }
  if (/^(?:[A-Za-z]:[/\\]|\\\\[^\s\\]|\/[^\s/<])/.test(rest)) {
    return '<path>';
  }
  return null;
}

function isEmail(word: string): boolean {
  const at = word.indexOf('@');
  if (at <= 0 || at !== word.lastIndexOf('@')) return false;
  const local = word.slice(0, at);
  const domain = word.slice(at + 1);
  return (
    !local.includes('/') &&
    !domain.includes('/') &&
    domain.includes('.') &&
    !domain.startsWith('.') &&
    !domain.endsWith('.')
  );
}

function isSecretToken(token: string): boolean {
  if (token.length < 24) return false;
  if (/^[0-9a-fA-F]+$/.test(token)) return true;
  if (/^eyJ[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+$/.test(token)) return true;
  if (!/^[A-Za-z0-9+/=_-]+$/.test(token)) return false;
  if (token.includes('/') && !token.includes('+') && !token.includes('=')) return false;
  if (token.includes('_') && !token.endsWith('=')) {
    const match = /^(ghp|gho|ghu|ghs|sk|pk|rk)_([^_]+)$/.exec(token);
    if (!match || match[2].length < 16) return false;
  }
  const hasDigit = /\d/.test(token);
  const hasLetter = /[A-Za-z]/.test(token);
  return (hasDigit && hasLetter) || token.includes('+') || token.includes('=');
}

function redactWord(word: string): string {
  const leadMatch = /^[("'<[]*/.exec(word);
  const lead = leadMatch ? leadMatch[0] : '';
  const afterLead = word.slice(lead.length);
  const trailMatch = /[)\]>"',;:]*$/.exec(afterLead);
  const trail = trailMatch ? trailMatch[0] : '';
  const core = afterLead.slice(0, afterLead.length - trail.length);
  if (!core) return word;
  if (/^https?:\/\//i.test(core)) return `${lead}<url>${trail}`;
  if (isEmail(core)) return `${lead}<email>${trail}`;
  if (isSecretToken(core)) return `${lead}<token>${trail}`;
  const colon = core.indexOf(':');
  if (colon > 0) {
    const filePart = core.slice(0, colon);
    const suffix = core.slice(colon + 1);
    if (/^[0-9:]+$/.test(suffix) && isMediaName(filePart)) {
      return `${lead}<file>:${suffix}${trail}`;
    }
  }
  if (isMediaName(core)) return `${lead}<file>${trail}`;
  return word;
}

function redactLine(line: string): string {
  let out = '';
  let i = 0;
  while (i < line.length) {
    const rest = line.slice(i);
    if (rest.startsWith('~/<path>')) {
      out += '~/<path>';
      i += '~/<path>'.length;
      continue;
    }
    const placeholder = pathPlaceholderAt(line, i);
    if (placeholder) {
      out += placeholder;
      const openQuote = i > 0 && (line[i - 1] === '"' || line[i - 1] === "'") ? line[i - 1] : '';
      if (openQuote) {
        const end = rest.indexOf(openQuote);
        if (end >= 0) {
          i += end;
          continue;
        }
      }
      break;
    }
    const ch = rest[0];
    if ((ch === '"' || ch === "'") && rest.indexOf(ch, 1) > 0) {
      const end = rest.indexOf(ch, 1);
      const inner = rest.slice(1, end);
      if (isMediaName(inner) && !pathPlaceholderAt(inner, 0) && !/^https?:\/\//i.test(inner)) {
        out += `${ch}<file>${ch}`;
        i += end + 1;
        continue;
      }
    }
    if (/\s/.test(ch)) {
      out += ch;
      i += 1;
      continue;
    }
    const ws = /\s/.exec(rest);
    const wordEnd = ws ? ws.index : rest.length;
    const word = rest.slice(0, wordEnd);
    let splitAt = -1;
    for (let k = 1; k < word.length; k += 1) {
      if (pathPlaceholderAt(word, k)) {
        splitAt = k;
        break;
      }
    }
    if (splitAt > 0) {
      out += word.slice(0, splitAt);
      i += splitAt;
      continue;
    }
    out += redactWord(word);
    i += wordEnd;
  }
  return out;
}

/** Redact paths, media file names, contacts, URLs and tokens before showing or sending diagnostics. */
export function redactDiagnostics(text: string): string {
  const redact = (value: unknown): unknown => {
    if (typeof value === 'string') {
      return value.split('\n').slice(0, 40).map(redactLine).join('\n');
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
  title = '',
): string {
  const url = new URL('https://github.com/HerRei/local-upscale/issues/new');
  url.searchParams.set('template', 'bug_report.yml');
  if (title) url.searchParams.set('title', title);
  url.searchParams.set('what-happened', happened);
  url.searchParams.set('version', version);
  url.searchParams.set('operating-system', platform);
  if (diagnostics) url.searchParams.set('hardware', diagnostics);
  return url.toString();
}
