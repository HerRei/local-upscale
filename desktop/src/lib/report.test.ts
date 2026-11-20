import { expect, it } from 'vitest';
import { redactDiagnostics, reportUrl } from './report';

it('redacts private paths, media filenames, contacts, URLs and tokens', () => {
  const cases = [
    'Error /Users/Élodie Smith/Pictures/family photo.jpg',
    'at C:\\Users\\Alice Smith\\Pictures\\photo.png:12',
    'File "C:\\Users\\Alice Smith\\app.py", line 12',
    'File C:/Users/Bob/video.mp4',
    'at /home/李 明/private data/model.bin',
    'Trace (/opt/Private Folder/user name/model.py:22)',
    'UNC \\\\fileserver\\share\\family photo.png',
    'mail alice@example.com',
    'https://example.com/?secret=1234',
    'token abcdef1234567890abcdef1234567890',
    'bearer SGVsbG8gd29ybGQgdGhpcyBpcyBhIHRlc3Q=',
    // A made-up token, assembled here so secret scanners do not flag the fixture.
    'jwt ' +
      [
        'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9',
        'eyJzdWIiOiIxMjM0NTY3ODkwIn0',
        'dozjgNryP4J3jVmNHl0w5N',
      ].join('.'),
    'Could not decode holiday.jpg',
    'Could not open "my vacation.heic"',
  ];
  const result = redactDiagnostics(JSON.stringify({ lines: cases }));
  for (const secret of [
    'Élodie',
    'Smith',
    'Alice',
    'Bob',
    '李',
    'Private',
    'fileserver',
    'holiday',
    'vacation',
    'photo',
    'video',
    'alice@example',
    'secret=',
    'abcdef1234567890',
    'SGVsbG8',
    'eyJhbGci',
  ]) {
    expect(result).not.toContain(secret);
  }
  expect(redactDiagnostics('Error /Users/Élodie Smith/Pictures/family photo.jpg')).toBe(
    'Error ~/<path>',
  );
  expect(redactDiagnostics('File "C:\\Users\\Alice Smith\\app.py", line 12')).toBe(
    'File "~/<path>", line 12',
  );
  expect(redactDiagnostics('https://example.com/?secret=1234')).toBe('<url>');
  expect(redactDiagnostics('src-tauri/src/commands.rs:42')).toBe('src-tauri/src/commands.rs:42');
  expect(redactDiagnostics('realesrgan_x4plus_anime_6b')).toBe('realesrgan_x4plus_anime_6b');
  expect(redactDiagnostics('Error ~/<path>')).toBe('Error ~/<path>');
  expect(redactDiagnostics('package @tauri-apps/api')).toBe('package @tauri-apps/api');
});

it('builds the issue form URL with optional title and diagnostics', () => {
  const url = new URL(
    reportUrl('failed & stopped', '0.4', 'macOS', 'diagnostics', 'Crash: Engine stopped'),
  );
  expect(url.origin + url.pathname).toBe('https://github.com/HerRei/local-upscale/issues/new');
  expect(url.searchParams.get('template')).toBe('bug_report.yml');
  expect(url.searchParams.get('title')).toBe('Crash: Engine stopped');
  expect(url.searchParams.get('what-happened')).toBe('failed & stopped');
  expect(url.searchParams.get('version')).toBe('0.4');
  expect(url.searchParams.get('operating-system')).toBe('macOS');
  expect(url.searchParams.get('hardware')).toBe('diagnostics');
  const bare = new URL(reportUrl('', '0.4', 'Linux', ''));
  expect(bare.searchParams.has('hardware')).toBe(false);
  expect(bare.searchParams.has('title')).toBe(false);
});
