import { describe, expect, it } from 'vitest';
import {
  DownloadDetail,
  SingleDownloadLine,
  TimeLeftLabel,
  downloadFailureText,
  progressBytes,
  timeLeftText,
  type DownloadFailure,
} from './downloadProgress';

describe('progressBytes', () => {
  it('formats done and total with decimal units and drops repeated units', () => {
    expect(progressBytes(454_000_000, 4_900_000_000)).toBe('454 MB of 4.9 GB');
    expect(progressBytes(1_200_000_000, 4_900_000_000)).toBe('1.2 of 4.9 GB');
    expect(progressBytes(476_000_000, 5_293_871_164)).toBe('476 MB of 5.3 GB');
    expect(progressBytes(0, 4_900_000_000)).toBe('4.9 GB');
    expect(progressBytes(4_900_000_000, 4_900_000_000)).toBe('4.9 GB');
    expect(progressBytes(12_000_000, 0)).toBe('12 MB');
    expect(progressBytes(0, 0)).toBe('');
  });
});

describe('timeLeftText and TimeLeftLabel', () => {
  it('rounds and formats remaining seconds in plain words', () => {
    expect([30, 44, 90, 330, 3540, 3600, 4800, 7200, 12600].map(timeLeftText)).toEqual([
      'less than a minute left',
      'less than a minute left',
      'about 2 min left',
      'about 6 min left',
      'about 59 min left',
      'about 1 h left',
      'about 1 h 20 min left',
      'about 2 h left',
      'about 4 h left',
    ]);
  });

  it('holds the shown value until the estimate drifts beyond 0.7 of a step', () => {
    const label = new TimeLeftLabel();
    expect(label.text(330)).toBe('about 6 min left');
    expect(label.text(318)).toBe('about 6 min left');
    expect(label.text(280)).toBe('about 5 min left');
    expect(label.text(null)).toBeNull();
  });
});

describe('DownloadDetail', () => {
  it('waits 3 s before reporting speed and time left', () => {
    const detail = new DownloadDetail();
    const total = 4_900_000_000;
    detail.observe({ done: 0, total, received: 0, phase: 'downloading' }, 0);
    detail.observe({ done: 12_000_000, total, received: 12_000_000, phase: 'downloading' }, 1);
    detail.observe({ done: 24_000_000, total, received: 24_000_000, phase: 'downloading' }, 2);

    expect(detail.speed(2)).toBeNull();
    expect(detail.text(2)).toBe('24 MB of 4.9 GB');

    detail.observe({ done: 36_000_000, total, received: 36_000_000, phase: 'downloading' }, 3);
    detail.observe({ done: 48_000_000, total, received: 48_000_000, phase: 'downloading' }, 4);
    expect(detail.speed(4)).toBe(12_000_000);
    expect(detail.text(4)).toBe('48 MB of 4.9 GB · about 7 min left');
  });

  it('measures speed from received bytes so a resumed download is not inflated', () => {
    const detail = new DownloadDetail();
    const total = 5_000_000_000;
    const resumed = 1_000_000_000;
    for (let sec = 0; sec <= 4; sec += 1) {
      detail.observe(
        {
          done: resumed + sec * 10_000_000,
          total,
          received: sec * 10_000_000,
          phase: 'downloading',
        },
        sec,
      );
    }
    expect(detail.speed(4)).toBe(10_000_000);
    expect(detail.text(4)).toBe('1.0 of 5.0 GB · about 7 min left');
  });

  it('drops samples < 0.5 s apart, clears on byte drop, and shows waiting after 8 s silence', () => {
    const detail = new DownloadDetail();
    const total = 4_900_000_000;
    detail.observe({ done: 0, total, received: 0, phase: 'downloading' }, 0);
    detail.observe({ done: 12_000_000, total, received: 12_000_000, phase: 'downloading' }, 1);
    detail.observe({ done: 24_000_000, total, received: 24_000_000, phase: 'downloading' }, 2);
    detail.observe({ done: 36_000_000, total, received: 36_000_000, phase: 'downloading' }, 3);
    expect(detail.speed(3)).toBe(12_000_000);

    expect(detail.speed(12)).toBeNull();
    expect(detail.text(12)).toBe('36 MB of 4.9 GB · Waiting for the connection…');

    // Bytes going down resets speed measurement.
    detail.observe({ done: 10_000_000, total, received: 10_000_000, phase: 'downloading' }, 13);
    expect(detail.speed(13)).toBeNull();
  });

  it('delays Verifying… by 1 s and omits the size once all bytes are present', () => {
    const detail = new DownloadDetail();
    const total = 4_900_000_000;
    for (let sec = 0; sec <= 4; sec += 1) {
      detail.observe(
        {
          done: 400_000_000 + sec * 12_000_000,
          total,
          received: sec * 12_000_000,
          phase: 'downloading',
        },
        sec,
      );
    }
    detail.observe({ done: 448_000_000, total, received: 48_000_000, phase: 'verifying' }, 5);
    expect(detail.speed(5.5)).toBeNull();
    expect(detail.text(5.5)).toBe('448 MB of 4.9 GB · about 6 min left');
    expect(detail.text(6.2)).toBe('448 MB of 4.9 GB · Verifying…');

    detail.observe({ done: total, total, received: 48_000_000, phase: 'verifying' }, 7);
    expect(detail.text(8.2)).toBe('Verifying…');
  });

  it('shows only the total while waiting', () => {
    const detail = new DownloadDetail();
    detail.observe({ done: 0, total: 159_000_000, received: 0, phase: 'waiting' }, 0);
    expect(detail.text(0)).toBe('159 MB');
  });

  it('omits connection-waiting text when disabled and resets on key change', () => {
    const line = new SingleDownloadLine();
    const total = 2_400_000_000;
    expect(line.update(true, 'cuda', 0, total, 0)).toBe('2.4 GB');
    expect(line.update(true, 'cuda', 100_000_000, total, 10)).toBe(
      '100 MB of 2.4 GB · about 4 min left',
    );
    expect(line.update(true, 'cuda', 100_000_000, total, 20)).toBe('100 MB of 2.4 GB');
    expect(line.update(true, 'rocm', 50_000_000, 1_000_000_000, 21)).toBe('50 MB of 1.0 GB');
    expect(line.update(false, '', 0, 0, 22)).toBe('');
  });
});

describe('downloadFailureText', () => {
  const base: DownloadFailure = {
    modelId: 'flux2_klein_4b_q4_0',
    name: 'FLUX.2 klein 4B',
    reason: 'offline',
    done: 1_200_000_000,
    total: 4_900_000_000,
    message: 'raw error',
  };

  it('formats each failure cause with and without the model name', () => {
    expect(downloadFailureText(base, true)).toEqual({
      title: 'Download interrupted',
      detail:
        'No connection to the model server. FLUX.2 klein 4B is kept at 1.2 of 4.9 GB and continues from there.',
    });
    expect(downloadFailureText(base, false)).toEqual({
      title: 'Download interrupted',
      detail: 'No connection to the model server. Kept at 1.2 of 4.9 GB.',
    });

    expect(downloadFailureText({ ...base, done: 0 }, true)).toEqual({
      title: 'Download interrupted',
      detail: 'No connection to the model server. Check the internet connection and start again.',
    });
    expect(downloadFailureText({ ...base, done: 0 }, false)).toEqual({
      title: 'Download interrupted',
      detail: 'No connection to the model server. Check the internet connection and start again.',
    });

    expect(downloadFailureText({ ...base, reason: 'server' }, true)).toEqual({
      title: 'Download failed',
      detail: 'The model server did not deliver FLUX.2 klein 4B. Try again later.',
    });
    expect(downloadFailureText({ ...base, reason: 'server' }, false)).toEqual({
      title: 'Download failed',
      detail: 'The model server did not deliver this model. Try again later.',
    });

    expect(downloadFailureText({ ...base, reason: 'damaged' }, true)).toEqual({
      title: 'Download failed',
      detail:
        'FLUX.2 klein 4B arrived damaged and was removed. Start again to download it once more.',
    });
    expect(downloadFailureText({ ...base, reason: 'damaged' }, false)).toEqual({
      title: 'Download failed',
      detail: 'Arrived damaged and was removed. Start again to download it once more.',
    });

    expect(
      downloadFailureText(
        {
          ...base,
          reason: 'disk_full',
          message:
            'not enough disk space: the download needs about 5630 MB free, but only 2100 MB is available',
        },
        true,
      ),
    ).toEqual({
      title: 'Download failed',
      detail:
        'Not enough disk space: the download needs about 5630 MB free, but only 2100 MB is available.',
    });

    expect(
      downloadFailureText(
        { ...base, reason: 'folder', message: 'the model folder is unavailable.' },
        false,
      ),
    ).toEqual({
      title: 'Download failed',
      detail: 'The model folder is unavailable.',
    });

    expect(
      downloadFailureText({ ...base, reason: 'other', message: 'unexpected error' }, true),
    ).toEqual({
      title: 'Download failed',
      detail: 'Unexpected error.',
    });
  });
});
