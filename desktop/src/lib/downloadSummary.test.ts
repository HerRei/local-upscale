import { describe, expect, it } from 'vitest';
import { DownloadRun } from './downloadSummary';

const catalog: Record<string, { name: string; size: number }> = {
  ddcolor: { name: 'DDColor', size: 900_000_000 },
  span: { name: 'SPAN ×4 NomosUni', size: 100_000_000 },
  blur: { name: 'NAFNet GoPro Deblur', size: 272_000_000 },
  noise: { name: 'RealPLKSR Denoise', size: 30_000_000 },
  hat: { name: 'HAT-L ×4', size: 166_000_000 },
};
const name = (id: string) => catalog[id]?.name ?? id;
const size = (id: string) => catalog[id]?.size ?? 0;

describe('DownloadRun', () => {
  it('is null when empty', () => {
    const run = new DownloadRun();
    run.observe([], 0);
    expect(run.line(0, name, size)).toBeNull();
  });

  it('reports one model, half done', () => {
    const run = new DownloadRun();
    run.observe(
      [
        {
          model_id: 'span',
          status: 'downloading',
          progress: 50,
          downloaded_bytes: 50_000_000,
          total_bytes: 100_000_000,
        },
      ],
      0,
    );
    expect(run.line(0, name, size)).toEqual({
      title: 'Downloading SPAN ×4 NomosUni',
      percent: 50,
      detail: '50 of 100 MB',
      names: '',
      count: 1,
    });
    expect(run.detail('span', 0)).toBe('50 of 100 MB');
  });

  it('weights progress across running and queued models', () => {
    const run = new DownloadRun();
    run.observe(
      [
        {
          model_id: 'ddcolor',
          status: 'downloading',
          progress: 50,
          downloaded_bytes: 450_000_000,
          total_bytes: 900_000_000,
        },
        { model_id: 'span', status: 'queued', progress: 0 },
      ],
      0,
    );
    const line = run.line(0, name, size)!;
    expect(line.title).toBe('Downloading 2 models');
    expect(line.percent).toBeCloseTo(45);
    expect(line.count).toBe(2);
    expect(line.detail).toBe('450 MB of 1.0 GB');
    expect(run.detail('span', 0)).toBe('100 MB');
  });

  it('keeps completed models in the run so progress does not drop', () => {
    const run = new DownloadRun();
    run.observe(
      [
        {
          model_id: 'span',
          status: 'downloading',
          progress: 90,
          downloaded_bytes: 90_000_000,
          total_bytes: 100_000_000,
        },
        {
          model_id: 'noise',
          status: 'downloading',
          progress: 10,
          downloaded_bytes: 3_000_000,
          total_bytes: 30_000_000,
        },
        { model_id: 'hat', status: 'queued', progress: 0 },
      ],
      0,
    );
    const before = run.line(0, name, size)!.percent;

    run.completed('span');
    run.observe(
      [
        {
          model_id: 'noise',
          status: 'downloading',
          progress: 10,
          downloaded_bytes: 3_000_000,
          total_bytes: 30_000_000,
        },
        { model_id: 'hat', status: 'queued', progress: 0 },
      ],
      1,
    );
    const after = run.line(1, name, size)!;
    expect(after.title).toBe('Downloading 3 models');
    expect(after.count).toBe(3);
    expect(after.percent).toBeGreaterThanOrEqual(before);
    expect(after.percent).toBeCloseTo((103 / 296) * 100);
  });

  it('removes dropped models from the run sums and count', () => {
    const run = new DownloadRun();
    run.observe(
      [
        {
          model_id: 'span',
          status: 'downloading',
          progress: 50,
          downloaded_bytes: 50_000_000,
          total_bytes: 100_000_000,
        },
        {
          model_id: 'noise',
          status: 'downloading',
          progress: 50,
          downloaded_bytes: 15_000_000,
          total_bytes: 30_000_000,
        },
        { model_id: 'hat', status: 'queued', progress: 0 },
      ],
      0,
    );
    run.dropped('span');
    run.observe(
      [
        {
          model_id: 'noise',
          status: 'downloading',
          progress: 50,
          downloaded_bytes: 15_000_000,
          total_bytes: 30_000_000,
        },
        { model_id: 'hat', status: 'queued', progress: 0 },
      ],
      1,
    );
    const line = run.line(1, name, size)!;
    expect(line.title).toBe('Downloading 2 models');
    expect(line.count).toBe(2);
    expect(line.percent).toBeCloseTo((15 / 196) * 100);
  });

  it('falls back to mean percentage with no size in detail when a size is unknown', () => {
    const run = new DownloadRun();
    run.observe(
      [
        { model_id: 'mystery', status: 'downloading', progress: 20 },
        {
          model_id: 'span',
          status: 'downloading',
          progress: 60,
          downloaded_bytes: 60_000_000,
          total_bytes: 100_000_000,
        },
      ],
      0,
    );
    const line = run.line(0, name, size)!;
    expect(line.percent).toBeCloseTo(40);
    expect(line.detail).toBe('');
  });

  it('starts a fresh run after the list empties', () => {
    const run = new DownloadRun();
    run.observe(
      [
        {
          model_id: 'span',
          status: 'downloading',
          progress: 100,
          downloaded_bytes: 100_000_000,
          total_bytes: 100_000_000,
        },
        {
          model_id: 'noise',
          status: 'downloading',
          progress: 50,
          downloaded_bytes: 15_000_000,
          total_bytes: 30_000_000,
        },
      ],
      0,
    );
    run.completed('span');
    run.observe([], 1);
    expect(run.line(1, name, size)).toBeNull();

    run.observe(
      [
        {
          model_id: 'hat',
          status: 'downloading',
          progress: 25,
          downloaded_bytes: 41_500_000,
          total_bytes: 166_000_000,
        },
      ],
      2,
    );
    const line = run.line(2, name, size)!;
    expect(line.title).toBe('Downloading HAT-L ×4');
    expect(line.count).toBe(1);
    expect(line.percent).toBeCloseTo(25);
  });

  it('lists each model state in names', () => {
    const run = new DownloadRun();
    run.observe(
      [
        {
          model_id: 'blur',
          status: 'downloading',
          progress: 80,
          downloaded_bytes: 217_600_000,
          total_bytes: 272_000_000,
        },
        {
          model_id: 'noise',
          status: 'downloading',
          phase: 'verifying',
          progress: 100,
          downloaded_bytes: 30_000_000,
          total_bytes: 30_000_000,
        },
        { model_id: 'hat', status: 'queued', progress: 0 },
      ],
      0,
    );
    const line = run.line(0, name, size)!;
    expect(line.names).toBe(
      ['NAFNet GoPro Deblur · 80%', 'RealPLKSR Denoise · verifying', 'HAT-L ×4 · waiting'].join(
        '\n',
      ),
    );
  });
});
