/**
 * The FFmpeg notice. LocalSR ships only royalty-free video formats (AV1, VP9,
 * FFV1 and more); H.264/HEVC need an FFmpeg the user installs and selects.
 * LocalSR never downloads or bundles that program.
 */
import type { UiSettings } from './types';

/** `reason` value the worker sends with a probe failure a user's FFmpeg would fix. */
export const EXTERNAL_FFMPEG_REQUIRED = 'external_ffmpeg_required';

export type FfmpegPlatform = 'macos' | 'windows' | 'linux';

/** One example install command per platform; the user runs it themselves. */
export const INSTALL_COMMANDS: Record<FfmpegPlatform, string> = {
  macos: 'brew install ffmpeg',
  windows: 'winget install ffmpeg',
  linux: 'sudo apt install ffmpeg',
};

export function detectPlatform(userAgent = navigator.userAgent): FfmpegPlatform {
  if (/windows/i.test(userAgent)) return 'windows';
  if (/mac os|macintosh/i.test(userAgent)) return 'macos';
  return 'linux';
}

/** A failed media probe that a user-installed FFmpeg would turn into a preview. */
export function probeNeedsExternalFFmpeg(data: Record<string, unknown>): boolean {
  if (String(data.reason ?? '') === EXTERNAL_FFMPEG_REQUIRED) return true;
  // Older workers send only the message text.
  return /patent-licensed format/i.test(String(data.error_message ?? ''));
}

/** An H.264/HEVC export was requested while no FFmpeg is selected. */
export function exportNeedsExternalFFmpeg(settings: UiSettings): boolean {
  const codec = settings.video_codec ?? 'av1';
  return (
    settings.task === 'video' &&
    (codec === 'h264' || codec === 'hevc') &&
    !(settings.external_ffmpeg_path ?? '').trim()
  );
}
