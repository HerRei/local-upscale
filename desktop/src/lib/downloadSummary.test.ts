import { describe, expect, it } from 'vitest';
import { downloadSummary } from './downloadSummary';

const catalog: Record<string, { name: string; size: number }> = {
  ddcolor: { name: 'DDColor', size: 900 },
  span: { name: 'SPAN ×4 NomosUni', size: 100 },
};
const lookup = (id: string) => catalog[id];

describe('downloadSummary', () => {
  it('is empty without downloads', () => {
    expect(downloadSummary([], lookup)).toBeNull();
  });

  it('names a single model', () => {
    const summary = downloadSummary(
      [
        {
          model_id: 'span',
          status: 'downloading',
          progress: 40,
          downloaded_bytes: 40,
          total_bytes: 100,
        },
      ],
      lookup,
    );
    expect(summary).toEqual({
      title: 'Downloading SPAN ×4 NomosUni',
      names: 'SPAN ×4 NomosUni',
      percent: 40,
    });
  });

  it('gives one progress for all downloads, weighted by size, queued ones included', () => {
    const summary = downloadSummary(
      [
        {
          model_id: 'ddcolor',
          status: 'downloading',
          progress: 50,
          downloaded_bytes: 450,
          total_bytes: 900,
        },
        { model_id: 'span', status: 'queued', progress: 0 },
      ],
      lookup,
    )!;
    expect(summary.title).toBe('Downloading 2 models');
    expect(summary.names).toBe('DDColor, SPAN ×4 NomosUni');
    expect(summary.percent).toBeCloseTo(45);
  });

  it('falls back to the mean when a size is unknown', () => {
    const summary = downloadSummary(
      [
        { model_id: 'mystery', status: 'downloading', progress: 20 },
        {
          model_id: 'span',
          status: 'downloading',
          progress: 60,
          downloaded_bytes: 60,
          total_bytes: 100,
        },
      ],
      lookup,
    )!;
    expect(summary.names).toBe('mystery, SPAN ×4 NomosUni');
    expect(summary.percent).toBeCloseTo(40);
  });
});
