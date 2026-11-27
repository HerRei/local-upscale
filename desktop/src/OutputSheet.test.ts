// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import OutputSheet from './OutputSheet.svelte';
import { demoSnapshot } from './lib/demo';
import { outputSummary } from './lib/enhancePanel';
import type { CapabilityInfo, UiSettings } from './lib/types';

vi.mock('./lib/api', () => ({}));

afterEach(cleanup);

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

function renderSheet(
  settings: Partial<UiSettings>,
  capabilities: CapabilityInfo = base,
  extra: Partial<{
    updateSettings: (patch: Partial<UiSettings>) => void;
    chooseOutput: () => Promise<void>;
    onOpenFfmpegSettings: () => void;
    selectedMedia: ReturnType<typeof demoSnapshot>['media'][number];
    usingTemporalVideo: boolean;
    preservingHdr: boolean;
    hdrPreservationAvailable: boolean;
    audioDisabled: boolean;
    disabled: boolean;
  }> = {},
) {
  const snapshot = demoSnapshot();
  const fullSettings = { ...snapshot.settings, ...settings };
  const updateSettings = extra.updateSettings ?? vi.fn();
  const audioModels = snapshot.catalog.audio_models ?? [];
  const selectedAudioModel =
    audioModels.find((m) => m.model_id === fullSettings.video_audio_model) ?? audioModels[0];
  render(OutputSheet, {
    settings: fullSettings,
    selectedMedia: extra.selectedMedia,
    capabilities,
    usingTemporalVideo: extra.usingTemporalVideo ?? false,
    preservingHdr: extra.preservingHdr ?? false,
    hdrPreservationAvailable: extra.hdrPreservationAvailable ?? false,
    audioDisabled: extra.audioDisabled ?? false,
    audioModels,
    selectedAudioModel,
    disabled: extra.disabled ?? false,
    updateSettings,
    chooseOutput: extra.chooseOutput ?? (async () => undefined),
    onOpenFfmpegSettings: extra.onOpenFfmpegSettings,
  });
  return updateSettings;
}

function options(label: string): string[] {
  return Array.from(screen.getByLabelText(label).querySelectorAll('option')).map(
    (option) => option.value,
  );
}

describe('outputSummary', () => {
  const baseSettings = {
    ...demoSnapshot().settings,
    output_directory: '/Users/hermes/Desktop',
  };

  it('formats photo summaries for lossless, lossy, and unset folders', () => {
    expect(outputSummary({ ...baseSettings, task: 'upscale', output_format: 'png' })).toBe(
      'PNG · Desktop',
    );
    expect(
      outputSummary({
        ...baseSettings,
        task: 'upscale',
        output_format: 'jpg',
        jpeg_quality: 98,
      }),
    ).toBe('JPEG 98 · Desktop');
    expect(
      outputSummary({
        ...baseSettings,
        task: 'upscale',
        output_format: 'png',
        output_directory: '',
      }),
    ).toBe('PNG · choose a folder');
  });

  it('formats video summaries with codec names, HDR, audio cleanup, and CRF', () => {
    expect(
      outputSummary({
        ...baseSettings,
        task: 'video',
        video_container: 'mp4',
        video_codec: 'av1',
        video_crf: 18,
      }),
    ).toBe('MP4 · AV1 · CRF 18 · Desktop');

    expect(
      outputSummary(
        {
          ...baseSettings,
          task: 'video',
          video_container: 'mp4',
          video_codec: 'hevc',
          video_crf: 18,
          video_clean_audio: true,
        },
        { preservingHdr: true, audioDisabled: false },
      ),
    ).toBe('MP4 · HEVC · HDR · CRF 18 · sound cleaned · Desktop');

    expect(
      outputSummary(
        {
          ...baseSettings,
          task: 'video',
          video_container: 'mp4',
          video_codec: 'auto',
          video_crf: 20,
          video_clean_audio: true,
        },
        { audioDisabled: true },
      ),
    ).toBe('MP4 · Automatic · CRF 20 · Desktop');

    expect(
      outputSummary({
        ...baseSettings,
        task: 'video',
        video_container: 'mkv',
        video_codec: 'ffv1',
      }),
    ).toBe('MKV · FFV1 · Desktop');
  });
});

describe('HEIC image output', () => {
  it('is offered with a quality slider where the engine announces it', async () => {
    const update = renderSheet({ task: 'upscale', output_format: 'heic' }, mac);
    expect(options('Format')).toEqual(['png', 'jpg', 'tif', 'webp', 'heic']);
    const slider = screen.getByLabelText(/^HEIC quality/);
    await fireEvent.change(slider, { target: { value: '85' } });
    expect(update).toHaveBeenCalledWith({ jpeg_quality: 85 });
  });

  it('is hidden everywhere else, and PNG shows no quality slider', () => {
    renderSheet({ task: 'upscale', output_format: 'png' }, base);
    expect(options('Format')).toEqual(['png', 'jpg', 'tif', 'webp']);
    expect(screen.queryByLabelText(/quality/)).toBeNull();
  });
});

describe('MOV video output', () => {
  it('lists only HEVC and ProRes for MOV and switches the pair together', async () => {
    const update = renderSheet({ task: 'video', video_container: 'mov', video_codec: 'hevc' }, mac);
    expect(options('Container')).toEqual(['mp4', 'mkv', 'mov']);
    expect(options('Video format')).toEqual(['hevc', 'prores']);
    await fireEvent.change(screen.getByLabelText('Video format'), { target: { value: 'prores' } });
    expect(update).toHaveBeenLastCalledWith({ video_codec: 'prores', video_container: 'mov' });
    await fireEvent.change(screen.getByLabelText('Container'), { target: { value: 'mp4' } });
    expect(update).toHaveBeenLastCalledWith({ video_container: 'mp4' });
  });

  it('keeps the existing codecs for MP4 and moves to a MOV codec on choosing MOV', async () => {
    const update = renderSheet({ task: 'video', video_container: 'mp4', video_codec: 'av1' }, mac);
    expect(options('Video format')).toEqual(['auto', 'av1', 'vp9', 'ffv1', 'h264', 'hevc']);
    await fireEvent.change(screen.getByLabelText('Container'), { target: { value: 'mov' } });
    expect(update).toHaveBeenLastCalledWith({ video_container: 'mov', video_codec: 'hevc' });
  });

  it('is hidden where the system codecs are missing', () => {
    renderSheet({ task: 'video', video_container: 'mp4', video_codec: 'av1' }, base);
    expect(options('Container')).toEqual(['mp4', 'mkv']);
    expect(options('Video format')).not.toContain('prores');
  });
});

describe('External FFmpeg shortcut', () => {
  it('shows detected or chosen FFmpeg status and opens Storage settings on Change…', async () => {
    const onOpenFfmpegSettings = vi.fn();
    renderSheet({ task: 'video', external_ffmpeg_path: '/usr/bin/ffmpeg' }, mac, {
      onOpenFfmpegSettings,
    });
    expect(screen.getByText('/usr/bin/ffmpeg')).toBeTruthy();
    const row = screen.getByText('External FFmpeg').closest('.field-row') as HTMLElement;
    const change = row.querySelector('button.link-button') as HTMLButtonElement;
    expect(change.textContent).toBe('Change…');
    await fireEvent.click(change);
    expect(onOpenFfmpegSettings).toHaveBeenCalledTimes(1);

    cleanup();
    renderSheet({ task: 'video', external_ffmpeg_path: '' }, mac);
    expect(screen.getByText('None')).toBeTruthy();
  });
});

describe('HDR colour output and audio cleanup', () => {
  it('shows Colour output only for an HDR source and updates HDR mode with FP32 and deflicker off', async () => {
    renderSheet({ task: 'video' }, mac);
    expect(screen.queryByLabelText('Colour output')).toBeNull();

    cleanup();
    const update = renderSheet({ task: 'video', video_hdr_mode: 'tone_map' }, mac, {
      hdrPreservationAvailable: true,
      selectedMedia: {
        id: 'hdr',
        name: 'hdr.mp4',
        path: '/hdr.mp4',
        kind: 'video',
        width: 1920,
        height: 1080,
        frame_count: 240,
        fps: 24,
        duration_seconds: 10,
        preview_data_url: '',
        probe_status: 'ready',
        error: '',
        selected: true,
        hdr_format: 'HLG',
      },
    });
    const select = screen.getByLabelText('Colour output') as HTMLSelectElement;
    await fireEvent.change(select, { target: { value: 'preserve' } });
    expect(update).toHaveBeenCalledWith({
      video_hdr_mode: 'preserve',
      precision: 'fp32',
      deflicker: false,
    });
  });

  it('disables Clean up audio when a video has no audio track', () => {
    renderSheet({ task: 'video', video_clean_audio: true }, mac, { audioDisabled: true });
    const checkbox = screen.getByRole('checkbox', { name: 'Clean up audio' }) as HTMLInputElement;
    expect(checkbox.disabled).toBe(true);
    expect(checkbox.checked).toBe(false);
    expect(screen.getByText('This video has no audio track.')).toBeTruthy();
  });
});
