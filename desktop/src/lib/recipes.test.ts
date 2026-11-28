import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { recipeFixStages, recipeMatches, sameFixStages } from './recipes';
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

describe('recipes, strength', () => {
  const snapshot = demoSnapshot();
  const recipe = (strength?: unknown): Recipe => ({
    id: 'strength',
    name: 'Strength',
    task: 'upscale',
    quality: 'quick',
    model_id: 'localsr_quick_x4_v1',
    output_scale: 4,
    tile_size: 256,
    halo: 8,
    precision: 'fp32',
    safe_memory: false,
    fixes: ['noise'],
    stages: [
      {
        kind: 'restore',
        model_id: 'denoise_realplksr_1x',
        fix: 'noise',
        ...(strength === undefined ? {} : { params: { strength, other: 1 } }),
      },
      { kind: 'upscale', model_id: 'localsr_quick_x4_v1' },
    ],
  });

  it('copies a strength under 1 to the entry and nothing else', () => {
    expect(recipeFixStages(recipe(0.4), snapshot.catalog)).toEqual([
      { fix: 'noise', model_id: 'denoise_realplksr_1x', params: { strength: 0.4 } },
    ]);
    expect(recipeFixStages(recipe(1), snapshot.catalog)).toEqual([
      { fix: 'noise', model_id: 'denoise_realplksr_1x' },
    ]);
    expect(recipeFixStages(recipe(), snapshot.catalog)).toEqual([
      { fix: 'noise', model_id: 'denoise_realplksr_1x' },
    ]);
  });

  it('compares strengths: 0.4 differs from 0.7, absent equals 1', () => {
    const entry = (strength?: number) => ({
      fix: 'noise' as const,
      model_id: 'm',
      ...(strength === undefined ? {} : { params: { strength } }),
    });
    expect(sameFixStages([entry(0.4)], [entry(0.7)])).toBe(false);
    expect(sameFixStages([entry(0.4)], [entry(0.4)])).toBe(true);
    expect(sameFixStages([entry()], [entry(1)])).toBe(true);
  });
});
