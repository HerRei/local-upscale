import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import {
  duplicateName,
  MAX_RECIPE_PINS,
  MAX_RECIPE_RECENT,
  menuRows,
  recipeDetails,
  recipePlanLine,
  recordUse,
  searchRecipes,
  sortRecipes,
  togglePin,
  type WorkflowEntry,
} from './recipeList';
import type { Recipe } from './types';

function makeRecipe(id: string, overrides: Partial<Recipe> = {}): Recipe {
  return {
    id,
    name: id.toUpperCase(),
    task: 'upscale',
    model_id: 'localsr_quick_x4_v1',
    output_scale: 4,
    tile_size: 256,
    halo: 32,
    precision: 'fp16',
    safe_memory: false,
    ...overrides,
  };
}

describe('recipeList', () => {
  it('shows all six recipes with pinned first and no separator when six or fewer', () => {
    const recipes = ['a', 'b', 'c', 'd', 'e', 'f'].map((id) => makeRecipe(id));
    const rows = menuRows(recipes, { pins: ['e', 'b'], mediaKind: 'image' });

    expect(rows.some((row) => row.kind === 'separator')).toBe(false);
    expect(rows.map((row) => (row.kind === 'recipe' ? [row.id, row.pinned] : row.kind))).toEqual([
      ['e', true],
      ['b', true],
      ['a', false],
      ['c', false],
      ['d', false],
      ['f', false],
    ]);
  });

  it('shows pinned first, a separator, then recent fitting recipes up to six when seven or more', () => {
    const recipes = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i'].map((id) => makeRecipe(id));
    const rows = menuRows(recipes, {
      pins: ['a', 'b'],
      recent: ['d', 'a', 'e', 'f', 'g'],
      mediaKind: 'image',
    });

    expect(rows.map((row) => (row.kind === 'separator' ? '---' : row.id))).toEqual([
      'a',
      'b',
      '---',
      'd',
      'e',
      'f',
      'g',
    ]);
    expect(
      rows.filter((row) => row.kind !== 'separator').map((row) => 'pinned' in row && row.pinned),
    ).toEqual([true, true, false, false, false, false]);
  });

  it('leaves out a video recipe among recents when a photo is open, while keeping mismatched pins disabled', () => {
    const recipes = [
      makeRecipe('a', { task: 'video', video_task: 'upscale' }),
      makeRecipe('b'),
      makeRecipe('c'),
      makeRecipe('d'),
      makeRecipe('e'),
      makeRecipe('f'),
      makeRecipe('v', { task: 'video', video_task: 'upscale' }),
    ];
    const rows = menuRows(recipes, {
      pins: ['a', 'b'],
      recent: ['v', 'c', 'd', 'e', 'f'],
      mediaKind: 'image',
    });

    expect(
      rows.map((row) =>
        row.kind === 'separator' ? '---' : `${row.id}${row.disabled ? ':disabled' : ''}`,
      ),
    ).toEqual(['a:disabled', 'b', '---', 'c', 'd', 'e', 'f']);
  });

  it('falls back to the first six fitting recipes when nothing is pinned or recent', () => {
    const recipes = [
      makeRecipe('v', { task: 'video', video_task: 'upscale' }),
      ...['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((id) => makeRecipe(id)),
    ];
    const rows = menuRows(recipes, { pins: [], recent: ['v'], mediaKind: 'image' });

    expect(rows.map((row) => row.id)).toEqual(['a', 'b', 'c', 'd', 'e', 'f']);
    expect(rows.every((row) => row.kind === 'recipe' && !row.pinned && !row.disabled)).toBe(true);
  });

  it('caps pinned rows at six when eight recipes are pinned', () => {
    const ids = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
    const recipes = ids.map((id) => makeRecipe(id));
    const rows = menuRows(recipes, { pins: ids, recent: ['i'], mediaKind: 'image' });

    expect(rows).toHaveLength(6);
    expect(rows.every((row) => row.kind === 'recipe' && row.pinned)).toBe(true);
    expect(rows.map((row) => row.id)).toEqual(['a', 'b', 'c', 'd', 'e', 'f']);
  });

  it('returns fitting workflows when there are no user recipes and no pins', () => {
    const workflows: WorkflowEntry[] = [
      { id: 'workflow.old-scan', name: 'Old scan', media: 'image' },
      { id: 'workflow.video', name: 'Video flow', media: 'video' },
    ];
    const rows = menuRows([], { workflows, mediaKind: 'image' });
    expect(rows).toEqual([
      {
        kind: 'workflow',
        id: 'workflow.old-scan',
        workflow: workflows[0],
        pinned: false,
        disabled: false,
      },
    ]);
  });

  it('names a saved fix strength in the recipe plan line', () => {
    const { catalog } = demoSnapshot();
    expect(
      recipePlanLine(
        makeRecipe('light', {
          task: 'upscale',
          output_scale: 4,
          fixes: ['noise'],
          stages: [
            {
              kind: 'restore',
              model_id: 'denoise_realplksr_1x',
              fix: 'noise',
              params: { strength: 0.4 },
            },
            { kind: 'upscale', model_id: 'localsr_quick_x4_v1' },
          ],
        }),
        catalog,
      ),
    ).toBe('Noise (light) → Upscale 4×');
  });

  it('formats recipePlanLine for upscale with fixes, restore, video, and same size without fixes', () => {
    const { catalog } = demoSnapshot();

    expect(
      recipePlanLine(
        makeRecipe('combo', {
          task: 'upscale',
          output_scale: 4,
          fixes: ['jpeg', 'noise'],
          stages: [
            { kind: 'deblock', model_id: 'fbcnn_color', fix: 'jpeg' },
            { kind: 'restore', model_id: 'denoise_realplksr_1x', fix: 'noise' },
            { kind: 'upscale', model_id: 'localsr_quick_x4_v1' },
          ],
        }),
        catalog,
      ),
    ).toBe('JPEG → Noise → Upscale 4×');

    expect(
      recipePlanLine(
        makeRecipe('restore', {
          task: 'denoise',
          output_scale: 1,
          model_id: 'denoise_realplksr_1x',
          fixes: ['noise'],
          stages: [{ kind: 'restore', model_id: 'denoise_realplksr_1x', fix: 'noise' }],
        }),
        catalog,
      ),
    ).toBe('Noise');

    expect(
      recipePlanLine(
        makeRecipe('vid', {
          task: 'video',
          video_task: 'upscale',
          output_scale: 2,
          video_interpolation: 'x2',
          video_interpolation_order: 'after_upscale',
          fixes: ['noise'],
          stages: [
            { kind: 'restore', model_id: 'denoise_realplksr_1x', fix: 'noise' },
            { kind: 'video', model_id: 'localsr_quick_x4_v1' },
          ],
        }),
        catalog,
      ),
    ).toBe('Noise → Upscale 2× → 2× frames');

    expect(
      recipePlanLine(
        makeRecipe('slow', {
          task: 'video',
          video_task: 'upscale',
          output_scale: 2,
          video_interpolation: 'x2',
          video_interpolation_order: 'after_upscale',
          video_slow_motion: true,
          video_clean_audio: true,
          fixes: ['noise'],
          stages: [
            { kind: 'restore', model_id: 'denoise_realplksr_1x', fix: 'noise' },
            { kind: 'video', model_id: 'localsr_quick_x4_v1' },
          ],
        }),
        catalog,
      ),
    ).toBe('Noise → Upscale 2× → Slow motion 2×');

    expect(
      recipePlanLine(
        makeRecipe('empty-same-size', {
          task: 'denoise',
          output_scale: 1,
          model_id: '',
          fixes: [],
          stages: [],
        }),
        catalog,
      ),
    ).toBe('Same size');
  });

  it('matches searchRecipes when every typed word appears across name, plan line, or model names', () => {
    const { catalog } = demoSnapshot();
    const oldSlides = makeRecipe('slides', {
      name: 'Old slides',
      fixes: ['jpeg'],
      stages: [
        { kind: 'deblock', model_id: 'fbcnn_color', fix: 'jpeg' },
        { kind: 'upscale', model_id: 'localsr_quick_x4_v1' },
      ],
    });
    const scanSlides = makeRecipe('scan-slides', {
      name: 'Slide scans',
      fixes: ['noise'],
      stages: [
        { kind: 'restore', model_id: 'denoise_realplksr_1x', fix: 'noise' },
        { kind: 'upscale', model_id: 'localsr_quick_x4_v1' },
      ],
    });
    const jpegBanner = makeRecipe('banner', {
      name: 'Web banners',
      fixes: ['jpeg'],
      stages: [
        { kind: 'deblock', model_id: 'fbcnn_color', fix: 'jpeg' },
        { kind: 'upscale', model_id: 'localsr_quick_x4_v1' },
      ],
    });
    const fbcnnSlides = makeRecipe('fbcnn-slides', {
      name: 'Slides archive',
      fixes: ['jpeg'],
      stages: [
        { kind: 'deblock', model_id: 'fbcnn_color', fix: 'jpeg' },
        { kind: 'upscale', model_id: 'localsr_quick_x4_v1' },
      ],
    });

    expect(
      searchRecipes([oldSlides, scanSlides, jpegBanner, fbcnnSlides], 'jpeg slides', catalog).map(
        (recipe) => recipe.id,
      ),
    ).toEqual(['slides', 'fbcnn-slides']);

    expect(
      searchRecipes([oldSlides, scanSlides], 'fbcnn slides', catalog).map((recipe) => recipe.id),
    ).toEqual(['slides']);
  });

  it('sorts pinned in pin order, then recent in use order, then by name', () => {
    const recipes = [
      makeRecipe('z', { name: 'Zebra' }),
      makeRecipe('a', { name: 'Apple' }),
      makeRecipe('m', { name: 'Mango' }),
      makeRecipe('b', { name: 'Banana' }),
      makeRecipe('p', { name: 'Pear' }),
    ];
    expect(sortRecipes(recipes, ['p', 'b'], ['z', 'a']).map((recipe) => recipe.id)).toEqual([
      'p',
      'b',
      'z',
      'a',
      'm',
    ]);
  });

  it('produces duplicateName copy and copy 2 within 60 characters', () => {
    expect(duplicateName('Old slides', ['Old slides'])).toBe('Old slides copy');
    expect(duplicateName('Old slides', ['Old slides', 'Old slides copy'])).toBe(
      'Old slides copy 2',
    );
    expect(duplicateName('Old slides copy', ['Old slides', 'Old slides copy'])).toBe(
      'Old slides copy 2',
    );
    const long = 'A'.repeat(60);
    expect(duplicateName(long, [long]).length).toBeLessThanOrEqual(60);
  });

  it('updates recent and pin lists with recordUse and togglePin within host limits', () => {
    let recent: string[] = [];
    for (let i = 0; i < MAX_RECIPE_RECENT + 3; i += 1) {
      recent = recordUse(recent, `r${i}`);
    }
    expect(recent).toHaveLength(MAX_RECIPE_RECENT);
    expect(recent[0]).toBe(`r${MAX_RECIPE_RECENT + 2}`);

    recent = recordUse(recent, recent[3]);
    expect(recent[0]).toBe(`r${MAX_RECIPE_RECENT - 1}`);
    expect(new Set(recent).size).toBe(MAX_RECIPE_RECENT);

    let pins: string[] = [];
    for (let i = 0; i < MAX_RECIPE_PINS + 2; i += 1) {
      pins = togglePin(pins, `p${i}`);
    }
    expect(pins).toHaveLength(MAX_RECIPE_PINS);
    expect(pins.includes(`p${MAX_RECIPE_PINS}`)).toBe(false);

    pins = togglePin(pins, 'p2');
    expect(pins).toHaveLength(MAX_RECIPE_PINS - 1);
    expect(pins.includes('p2')).toBe(false);
  });

  it('builds recipeDetails with step model names, download total, and settings summary', () => {
    const snapshot = demoSnapshot();
    const detail = recipeDetails(
      makeRecipe('slides', {
        output_scale: 2,
        quality: 'quick',
        output_format: 'png',
        tile_size: 256,
        halo: 32,
        precision: 'fp16',
        safe_memory: true,
        fixes: ['jpeg'],
        stages: [
          { kind: 'deblock', model_id: 'fbcnn_color', fix: 'jpeg' },
          { kind: 'upscale', model_id: 'localsr_quick_x4_v1' },
        ],
      }),
      snapshot,
      snapshot.settings,
    );
    expect(detail.steps).toEqual([
      {
        name: 'JPEG',
        modelName: 'FBCNN Color',
        downloadBytes: 287755111,
        needsFile: false,
      },
      {
        name: 'Upscale 2×',
        modelName: 'LocalSR Photo S',
        downloadBytes: 2479984,
        needsFile: false,
      },
    ]);
    expect(detail.downloadBytes).toBe(290235095);
    expect(detail.output).toBe('PNG');
    expect(detail.quality).toBe('Quick');
    expect(detail.run).toBe('Tile 256 · Halo 32 · FP16 · Safe memory');
  });
});
