import { describe, expect, it } from 'vitest';

import { demoSnapshot } from './demo';
import {
  codecsForContainer,
  containerChoices,
  containerPatch,
  codecPatch,
  imageFormatChoices,
  imageQualityName,
} from './outputFormats';
import type { CapabilityInfo } from './types';

const base = demoSnapshot().capabilities;
const mac: CapabilityInfo = {
  ...base,
  media: {
    system_codecs: {
      label: 'macOS',
      decode: ['h264', 'hevc', 'aac'],
      encode: ['h264', 'hevc', 'prores', 'aac'],
      containers: ['mp4', 'mov'],
    },
    image_output: ['heic'],
    external_ffmpeg: { detected: [], candidates: [] },
  },
};
const linuxWithFfmpeg: CapabilityInfo = {
  ...base,
  media: {
    system_codecs: null,
    image_output: [],
    external_ffmpeg: { detected: ['/usr/bin/ffmpeg'], candidates: [] },
  },
};

describe('image formats', () => {
  it('offer HEIC only where the engine announces it', () => {
    expect(imageFormatChoices(mac).map((choice) => choice.value)).toEqual([
      'png',
      'jpg',
      'tif',
      'webp',
      'heic',
    ]);
    for (const capabilities of [base, linuxWithFfmpeg, undefined]) {
      expect(imageFormatChoices(capabilities).map((choice) => choice.value)).toEqual([
        'png',
        'jpg',
        'tif',
        'webp',
      ]);
    }
  });

  it('keep a saved choice in the menu so it never shows blank', () => {
    expect(imageFormatChoices(base, 'heic').map((choice) => choice.value)).toContain('heic');
  });

  it('name the lossy formats whose quality slider is shown', () => {
    expect(imageQualityName('jpg')).toBe('JPEG');
    expect(imageQualityName('webp')).toBe('WebP');
    expect(imageQualityName('heic')).toBe('HEIC');
    expect(imageQualityName('png')).toBeNull();
    expect(imageQualityName('tif')).toBeNull();
  });
});

describe('video containers and codecs', () => {
  it('offer MOV only with the macOS ProRes and MOV encoders', () => {
    expect(containerChoices(mac).map((choice) => choice.value)).toEqual(['mp4', 'mkv', 'mov']);
    for (const capabilities of [base, linuxWithFfmpeg, undefined]) {
      expect(containerChoices(capabilities).map((choice) => choice.value)).toEqual(['mp4', 'mkv']);
    }
    const withoutProres: CapabilityInfo = {
      ...mac,
      media: {
        ...mac.media,
        system_codecs: {
          label: 'macOS',
          decode: [],
          encode: ['h264', 'hevc'],
          containers: ['mp4'],
        },
      },
    };
    expect(containerChoices(withoutProres).map((choice) => choice.value)).toEqual(['mp4', 'mkv']);
  });

  it('list only the codecs the chosen container holds', () => {
    expect(codecsForContainer('mov')).toEqual(['hevc', 'prores']);
    expect(codecsForContainer('mp4')).toEqual(['auto', 'av1', 'vp9', 'ffv1', 'h264', 'hevc']);
    expect(codecsForContainer('mkv')).toEqual(['auto', 'av1', 'vp9', 'ffv1', 'h264', 'hevc']);
    expect(codecsForContainer('mp4')).not.toContain('prores');
  });

  it('change both settings together so the host never sees a pair it rejects', () => {
    expect(containerPatch('mov', 'auto')).toEqual({ video_container: 'mov', video_codec: 'hevc' });
    expect(containerPatch('mov', 'av1')).toEqual({ video_container: 'mov', video_codec: 'hevc' });
    expect(containerPatch('mov', 'prores')).toEqual({ video_container: 'mov' });
    expect(containerPatch('mov', 'hevc')).toEqual({ video_container: 'mov' });
    expect(containerPatch('mp4', 'prores')).toEqual({
      video_container: 'mp4',
      video_codec: 'auto',
    });
    expect(containerPatch('mkv', 'prores')).toEqual({
      video_container: 'mkv',
      video_codec: 'auto',
    });
    expect(containerPatch('mkv', 'h264')).toEqual({ video_container: 'mkv' });
    expect(codecPatch('ffv1')).toEqual({ video_codec: 'ffv1', video_container: 'mkv' });
    expect(codecPatch('prores')).toEqual({ video_codec: 'prores', video_container: 'mov' });
    expect(codecPatch('hevc')).toEqual({ video_codec: 'hevc' });
  });
});
