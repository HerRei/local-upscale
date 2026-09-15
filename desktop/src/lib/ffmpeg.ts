/**
 * The FFmpeg notice. LocalSR ships only royalty-free video formats (AV1, VP9,
 * FFV1 and more). On macOS the system's own codecs open and write H.264/HEVC;
 * elsewhere, and for formats macOS cannot read, an FFmpeg the user installed
 * does it. LocalSR never downloads or bundles that program.
 */
import type { CapabilityInfo, UiSettings } from './types';

/** `reason` value the worker sends with a probe failure a user's FFmpeg would fix. */
export const EXTERNAL_FFMPEG_REQUIRED = 'external_ffmpeg_required';

export type FfmpegPlatform = 'macos' | 'windows' | 'linux';

/** One example install command per platform; the host refines it per distribution. */
export const INSTALL_COMMANDS: Record<FfmpegPlatform, string> = {
  macos: 'brew install ffmpeg',
  windows: 'winget install Gyan.FFmpeg',
  linux: 'sudo apt install ffmpeg',
};

export function detectPlatform(userAgent = navigator.userAgent): FfmpegPlatform {
  if (/windows/i.test(userAgent)) return 'windows';
  if (/mac os|macintosh/i.test(userAgent)) return 'macos';
  return 'linux';
}

/** The system codecs the worker reported, if this platform has any. */
export function systemCodecs(capabilities?: CapabilityInfo | null) {
  return capabilities?.media?.system_codecs ?? null;
}

/** FFmpeg programs the worker found on this computer and uses automatically. */
export function detectedFfmpeg(capabilities?: CapabilityInfo | null): string[] {
  return capabilities?.media?.external_ffmpeg?.detected ?? [];
}

/** Whether something other than a user-selected FFmpeg writes H.264/HEVC into `container`. */
export function externalCodecsAvailable(
  capabilities?: CapabilityInfo | null,
  container = 'mp4',
): boolean {
  const system = systemCodecs(capabilities);
  if (system && (system.containers ?? ['mp4']).includes(container)) return true;
  return detectedFfmpeg(capabilities).length > 0;
}

/** A failed media probe that a user-installed FFmpeg would turn into a preview. */
export function probeNeedsExternalFFmpeg(data: Record<string, unknown>): boolean {
  if (String(data.reason ?? '') === EXTERNAL_FFMPEG_REQUIRED) return true;
  // Older workers send only the message text.
  return /patent-licensed format/i.test(String(data.error_message ?? ''));
}

/** An H.264/HEVC export was requested and nothing on this computer can write it. */
export function exportNeedsExternalFFmpeg(
  settings: UiSettings,
  capabilities?: CapabilityInfo | null,
): boolean {
  const codec = settings.video_codec ?? 'av1';
  return (
    settings.task === 'video' &&
    (codec === 'h264' || codec === 'hevc') &&
    !(settings.external_ffmpeg_path ?? '').trim() &&
    !externalCodecsAvailable(capabilities, settings.video_container ?? 'mp4')
  );
}
