export type FfmpegPlatform = 'macos' | 'windows' | 'linux';

export function detectPlatform(userAgent = navigator.userAgent): FfmpegPlatform {
  if (/windows/i.test(userAgent)) return 'windows';
  if (/mac os|macintosh/i.test(userAgent)) return 'macos';
  return 'linux';
}
