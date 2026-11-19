import { expect, it } from 'vitest';
import { redactDiagnostics, reportUrl } from './report';

it('redacts private paths and media filenames in diagnostic values', () => {
  const result = redactDiagnostics(
    JSON.stringify({
      details: '/Users/alice/Pictures/holiday.jpg',
      other: 'C:\\Users\\bob\\Pictures\\photo.png',
      spaced: '/Users/Alice Smith/Pictures/private.dng',
    }),
  );
  expect(result).not.toContain('alice');
  expect(result).not.toContain('bob');
  expect(result).not.toContain('holiday.jpg');
  expect(result).not.toContain('photo.png');
  expect(result).not.toContain('Alice Smith');
  expect(result).not.toContain('private.dng');
});

it('builds the issue form URL with only the requested fields', () => {
  const url = new URL(reportUrl('failed & stopped', '0.4', 'macOS', 'diagnostics'));
  expect(url.origin + url.pathname).toBe('https://github.com/HerRei/local-upscale/issues/new');
  expect(url.searchParams.get('template')).toBe('bug_report.yml');
  expect(url.searchParams.get('what-happened')).toBe('failed & stopped');
  expect(url.searchParams.get('version')).toBe('0.4');
  expect(url.searchParams.get('operating-system')).toBe('macOS');
  expect(url.searchParams.get('hardware')).toBe('diagnostics');
  expect(new URL(reportUrl('', '0.4', 'Linux', '')).searchParams.has('hardware')).toBe(false);
});
