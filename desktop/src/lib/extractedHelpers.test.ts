import { describe, expect, it } from 'vitest';
import { benchmarkDeviceBarWidth, benchmarkSceneLabel } from './benchmark';
import { mediaNamesFor, without } from './collections';
import { demoSnapshot } from './demo';
import { presetDownload, supportsHdrPreservation } from './plan';
import { recipeFixStages, recipeMatches, sameFixStages } from './recipes';

describe('extracted helpers', () => {
  it('compares fix stages in order', () => {
    const fixes = [{ fix: 'blur' as const, model_id: 'a' }];
    expect(sameFixStages(fixes, [...fixes])).toBe(true);
    expect(sameFixStages(fixes, [{ fix: 'blur', model_id: 'b' }])).toBe(false);
    expect(sameFixStages(fixes, [])).toBe(false);
  });

  it('uses the catalog for recipe stages and matching', () => {
    const snapshot = demoSnapshot();
    const recipe = {
      ...snapshot.settings,
      id: 'r',
      name: 'Recipe',
      task: 'video' as const,
      model_id: snapshot.settings.selected_model_id,
      stages: [],
    };
    expect(recipeFixStages(recipe, snapshot.catalog)).toEqual([]);
    expect(recipeMatches(recipe, { ...snapshot.settings, task: 'edit' }, snapshot.catalog)).toBe(
      false,
    );
  });

  it('formats benchmark scores and scene names', () => {
    expect(benchmarkDeviceBarWidth(0, [])).toBe(5);
    expect(benchmarkDeviceBarWidth(2, [])).toBe(100);
    expect(benchmarkSceneLabel('s2-gallery')).toBe('Gallery · tiled');
    expect(benchmarkSceneLabel('other')).toBe('other');
  });

  it('removes a key without changing its source and resolves media names', () => {
    const record = { a: 1, b: 2 };
    expect(without(record, 'a')).toEqual({ b: 2 });
    expect(record).toEqual({ a: 1, b: 2 });
    const media = demoSnapshot().media;
    expect(mediaNamesFor(['/tmp/file.png'], media)).toEqual(['file.png']);
  });

  it('handles edit preset downloads and HDR capability', () => {
    const snapshot = demoSnapshot();
    expect(presetDownload('quick', snapshot, { ...snapshot.settings, task: 'edit' })).toBe('');
    expect(
      supportsHdrPreservation(
        { ...snapshot.settings, selected_video_model_id: 'other' },
        snapshot.catalog,
      ),
    ).toBe(false);
    expect(
      supportsHdrPreservation(
        {
          ...snapshot.settings,
          selected_video_model_id: 'frame_by_frame',
          selected_model_id: '__custom__',
        },
        snapshot.catalog,
      ),
    ).toBe(true);
  });
});
