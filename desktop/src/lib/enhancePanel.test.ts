import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { mediaFacts, outputSummary, outputTitle } from './enhancePanel';
import type { MediaItem } from './types';

function makeMedia(overrides: Partial<MediaItem> = {}): MediaItem {
  return {
    id: 'm1',
    path: '/tmp/photo.jpg',
    name: 'photo.jpg',
    kind: 'image',
    width: 1600,
    height: 1067,
    frame_count: 1,
    fps: 0,
    duration_seconds: 0,
    preview_data_url: '',
    probe_status: 'ready',
    error: '',
    selected: true,
    ...overrides,
  };
}

describe('mediaFacts', () => {
  it('returns empty before probe is ready', () => {
    expect(mediaFacts(undefined)).toBe('');
    expect(mediaFacts(makeMedia({ probe_status: 'pending' }))).toBe('');
    expect(mediaFacts(makeMedia({ probe_status: 'failed' }))).toBe('');
  });

  it('formats photo dimensions and mapped extensions', () => {
    expect(mediaFacts(makeMedia({ name: 'lake.jpg' }))).toBe('1600 × 1067 · JPEG');
    expect(mediaFacts(makeMedia({ name: 'lake.jpeg' }))).toBe('1600 × 1067 · JPEG');
    expect(mediaFacts(makeMedia({ name: 'lake.png' }))).toBe('1600 × 1067 · PNG');
    expect(mediaFacts(makeMedia({ name: 'lake.tiff' }))).toBe('1600 × 1067 · TIFF');
    expect(mediaFacts(makeMedia({ name: 'lake.webp' }))).toBe('1600 × 1067 · WebP');
    expect(mediaFacts(makeMedia({ name: 'lake.heic' }))).toBe('1600 × 1067 · HEIC');
    expect(mediaFacts(makeMedia({ name: 'lake.bmp' }))).toBe('1600 × 1067 · BMP');
  });

  it('formats video dimensions, rate, duration and optional HDR format', () => {
    expect(
      mediaFacts(
        makeMedia({
          kind: 'video',
          name: 'clip.mp4',
          width: 1920,
          height: 1080,
          fps: 24,
          duration_seconds: 12,
        }),
      ),
    ).toBe('1920 × 1080 · 24 fps · 12 s');

    expect(
      mediaFacts(
        makeMedia({
          kind: 'video',
          name: 'clip.mov',
          width: 3840,
          height: 2160,
          fps: 23.976024,
          duration_seconds: 75,
          hdr_format: 'HLG',
        }),
      ),
    ).toBe('3840 × 2160 · 23.976 fps · 1:15 · HLG');
  });
});

describe('outputSummary', () => {
  it('adds transparent and explains a replaced format in the title', () => {
    const { settings } = demoSnapshot();
    settings.task = 'upscale';
    settings.output_directory = '/tmp/Desktop';
    settings.step_stages = [{ step: 'remove_background', model_id: 'birefnet_general' }];
    settings.output_format = 'jpg';
    expect(outputSummary(settings)).toBe('PNG · transparent · Desktop');
    expect(outputTitle(settings)).toBe(
      'PNG · transparent · Desktop · Saved as PNG to keep the transparent background.',
    );
    settings.output_format = 'tif';
    expect(outputSummary(settings)).toBe('TIFF · transparent · Desktop');
    expect(outputTitle(settings)).toBe('TIFF · transparent · Desktop');
    settings.output_format = 'webp';
    expect(outputSummary(settings)).toBe(`WebP ${settings.jpeg_quality} · transparent · Desktop`);
  });

  it('omits the folder when includeFolder is false', () => {
    const { settings } = demoSnapshot();
    settings.task = 'upscale';
    settings.output_format = 'png';
    settings.output_directory = '/tmp/Results';
    expect(outputSummary(settings)).toBe('PNG · Results');
    expect(outputSummary(settings, { includeFolder: false })).toBe('PNG');
  });
});
