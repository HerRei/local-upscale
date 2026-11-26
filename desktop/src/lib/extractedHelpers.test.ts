import { describe, expect, it } from 'vitest';
import {
  benchmarkDeviceBarWidth,
  benchmarkOneMpExample,
  benchmarkSceneLabel,
  benchmarkSpeedBarWidth,
} from './benchmark';
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
    expect(benchmarkOneMpExample(25.1, 4)).toBe('a 1 MP photo to 16 MP takes about 25 s');
    expect(benchmarkOneMpExample(0.4, 4)).toBe('a 1 MP photo to 16 MP takes about 0.4 s');
    expect(
      benchmarkSpeedBarWidth(10, [
        {
          device: 'cpu',
          device_type: 'cpu',
          device_name: 'CPU',
          thermal_state: 'nominal',
          warmup_iterations: 1,
          stable: true,
          cv_percent: 1,
          score: 1,
          seconds_per_input_mp: 5,
          scenes: [],
        },
      ]),
    ).toBe(50);
  });

  it('removes a key without changing its source and resolves media names', () => {
    const record = { a: 1, b: 2 };
    expect(without(record, 'a')).toEqual({ b: 2 });
    expect(record).toEqual({ a: 1, b: 2 });
    const media = demoSnapshot().media;
    expect(mediaNamesFor(['/tmp/file.png'], media)).toEqual(['file.png']);
  });

  it('counts no restore model in a video Restore preset download without a chip', () => {
    const snapshot = demoSnapshot();
    snapshot.catalog.models.forEach((model) => (model.installed = false));
    const video = {
      ...snapshot.settings,
      task: 'video' as const,
      video_task: 'upscale' as const,
      selected_video_model_id: 'frame_by_frame',
      fix_stages: [],
    };
    expect(presetDownload('quick', snapshot, { ...video, output_scale: 2 })).toMatch(/download$/);
    expect(presetDownload('quick', snapshot, { ...video, video_task: 'denoise' })).toBe('');
    // A fix chip in video Restore still counts its model.
    expect(
      presetDownload('quick', snapshot, {
        ...video,
        video_task: 'denoise',
        fix_stages: [{ fix: 'blur', model_id: '' }],
      }),
    ).toMatch(/download$/);
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
