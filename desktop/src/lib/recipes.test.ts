import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { recipeFixStages, recipeMatches } from './recipes';
import type { Recipe, UiSettings } from './types';

describe('recipeFixStages and recipeMatches', () => {
  it('derives the fix chip for a photo Same size recipe saved without fixes', () => {
    const snapshot = demoSnapshot();
    const recipe: Recipe = {
      id: 'photo-restore-no-chip',
      name: 'Old denoise',
      task: 'denoise',
      quality: 'quick',
      model_id: 'denoise_realplksr_1x',
      output_scale: 1,
      tile_size: 256,
      halo: 8,
      precision: 'fp32',
      safe_memory: false,
      fixes: [],
      stages: [{ kind: 'restore', model_id: 'denoise_realplksr_1x' }],
    };
    expect(recipeFixStages(recipe, snapshot.catalog)).toEqual([
      { fix: 'noise', model_id: 'denoise_realplksr_1x' },
    ]);

    const current: UiSettings = {
      ...snapshot.settings,
      task: 'denoise',
      quality: 'quick',
      selected_model_id: 'denoise_realplksr_1x',
      output_scale: 1,
      tile_size: 256,
      halo: 8,
      precision: 'fp32',
      safe_memory: false,
      enable_face_model: false,
      fixes: ['noise'],
      fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
    };
    expect(recipeMatches(recipe, current, snapshot.catalog)).toBe(true);

    // A recipe that recorded its output matches only that output; an older one any output.
    const withOutput: Recipe = { ...recipe, output_format: 'tif', output_bit_depth: 16 };
    expect(recipeMatches(withOutput, current, snapshot.catalog)).toBe(false);
    const tiff16: UiSettings = { ...current, output_format: 'tif', output_bit_depth: 16 };
    expect(recipeMatches(withOutput, tiff16, snapshot.catalog)).toBe(true);
    expect(recipeMatches(withOutput, { ...tiff16, output_bit_depth: 8 }, snapshot.catalog)).toBe(
      false,
    );
    expect(recipeMatches(recipe, tiff16, snapshot.catalog)).toBe(true);
  });

  it('keeps upscale recipes with fixes unchanged', () => {
    const snapshot = demoSnapshot();
    const recipe: Recipe = {
      id: 'up-fixes',
      name: 'Clean upscale',
      task: 'upscale',
      quality: 'best',
      model_id: 'realplksr_nomoswebphoto_x4',
      output_scale: 4,
      tile_size: 256,
      halo: 32,
      precision: 'fp16',
      safe_memory: true,
      fixes: ['blur', 'noise'],
      stages: [
        { kind: 'restore', model_id: 'nafnet_gopro_deblur', fix: 'blur' },
        { kind: 'restore', model_id: 'nafnet_sidd_width64', fix: 'noise' },
        { kind: 'upscale', model_id: 'realplksr_nomoswebphoto_x4' },
      ],
    };
    expect(recipeFixStages(recipe, snapshot.catalog)).toEqual([
      { fix: 'blur', model_id: 'nafnet_gopro_deblur' },
      { fix: 'noise', model_id: 'nafnet_sidd_width64' },
    ]);
  });
});
