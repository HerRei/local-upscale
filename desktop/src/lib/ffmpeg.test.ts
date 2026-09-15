import { describe, expect, it } from 'vitest';

import { demoSnapshot } from './demo';
import {
  detectPlatform,
  exportNeedsExternalFFmpeg,
  INSTALL_COMMANDS,
  probeNeedsExternalFFmpeg,
} from './ffmpeg';

describe('FFmpeg notice helpers', () => {
  it('recognises the worker reason and falls back to the message text', () => {
    expect(probeNeedsExternalFFmpeg({ reason: 'external_ffmpeg_required' })).toBe(true);
    expect(
      probeNeedsExternalFFmpeg({
        error_message: 'This video uses a patent-licensed format that LocalSR does not include.',
      }),
    ).toBe(true);
    expect(probeNeedsExternalFFmpeg({ error_message: 'The file is truncated.' })).toBe(false);
    expect(probeNeedsExternalFFmpeg({})).toBe(false);
  });

  it('asks for FFmpeg only for H.264/HEVC video exports without a selected program', () => {
    const settings = { ...demoSnapshot().settings, task: 'video' as const };
    expect(exportNeedsExternalFFmpeg({ ...settings, video_codec: 'h264' })).toBe(true);
    expect(exportNeedsExternalFFmpeg({ ...settings, video_codec: 'hevc' })).toBe(true);
    expect(exportNeedsExternalFFmpeg({ ...settings, video_codec: 'av1' })).toBe(false);
    expect(
      exportNeedsExternalFFmpeg({
        ...settings,
        video_codec: 'h264',
        external_ffmpeg_path: '/opt/homebrew/bin/ffmpeg',
      }),
    ).toBe(false);
    expect(exportNeedsExternalFFmpeg({ ...settings, task: 'upscale', video_codec: 'h264' })).toBe(
      false,
    );
  });

  it('does not ask for FFmpeg when the system codecs or a found FFmpeg can write the export', () => {
    const settings = {
      ...demoSnapshot().settings,
      task: 'video' as const,
      video_codec: 'hevc' as const,
    };
    const capabilities = demoSnapshot().capabilities;
    const mac = {
      ...capabilities,
      media: {
        system_codecs: {
          label: 'macOS',
          decode: ['h264'],
          encode: ['h264', 'hevc'],
          containers: ['mp4'],
        },
        external_ffmpeg: { detected: [], candidates: [] },
      },
    };
    expect(exportNeedsExternalFFmpeg({ ...settings, video_container: 'mp4' }, mac)).toBe(false);
    expect(exportNeedsExternalFFmpeg({ ...settings, video_container: 'mkv' }, mac)).toBe(true);
    const linux = {
      ...capabilities,
      media: {
        system_codecs: null,
        external_ffmpeg: { detected: ['/usr/bin/ffmpeg'], candidates: [] },
      },
    };
    expect(exportNeedsExternalFFmpeg({ ...settings, video_container: 'mkv' }, linux)).toBe(false);
    expect(exportNeedsExternalFFmpeg(settings, { ...capabilities, media: null })).toBe(true);
  });

  it('picks the install command for the platform', () => {
    expect(INSTALL_COMMANDS[detectPlatform('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)')]).toBe(
      'brew install ffmpeg',
    );
    expect(INSTALL_COMMANDS[detectPlatform('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')]).toBe(
      'winget install Gyan.FFmpeg',
    );
    expect(INSTALL_COMMANDS[detectPlatform('Mozilla/5.0 (X11; Linux x86_64)')]).toBe(
      'sudo apt install ffmpeg',
    );
  });
});
