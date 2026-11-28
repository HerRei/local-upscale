import { describe, expect, it } from 'vitest';
import { detailLines, outputGroups, resultDetails } from './compareOutputs';
import { groupedHardware } from './hardwareRoutes';
import { hardwareChoices } from './neuralEngine';
import type {
  CapabilityInfo,
  ComparePick,
  DeviceInfo,
  MediaItem,
  MediaResults,
  ResultSummary,
} from './types';

const hardware = groupedHardware(
  hardwareChoices({
    devices: [
      { id: 'cpu', type: 'cpu', name: 'CPU' },
      { id: 'mps', type: 'gpu', name: 'Apple M4 Pro', neural_engine: true },
    ] as DeviceInfo[],
  } as CapabilityInfo),
);

describe('Outputs list histories', () => {
  it('groups all completed results by image in input order and keeps unavailable results visible', () => {
    const media = [
      { id: 'a', name: 'A', kind: 'image' },
      { id: 'v', name: 'Video', kind: 'video' },
      { id: 'b', name: 'B', kind: 'image' },
      { id: 'empty', kind: 'image' },
    ] as MediaItem[];
    const result = (job_id: string, status: string, available: boolean): ResultSummary =>
      ({ job_id, status, available, model_label: job_id }) as ResultSummary;
    const histories = [
      { media_id: 'b', results: [result('b1', 'completed', true)] },
      { media_id: 'v', results: [result('v1', 'completed', true)] },
      {
        media_id: 'a',
        results: [
          result('new', 'completed', true),
          result('failed', 'failed', false),
          result('old', 'completed', false),
        ],
      },
    ] as MediaResults[];
    const groups = outputGroups(media, histories);
    expect(groups.map((group) => group.media.id)).toEqual(['a', 'v', 'b']);
    expect(groups[0].original).toEqual({ kind: 'original', media_id: 'a' });
    expect(groups[0].outputs.map(({ result }) => result.job_id)).toEqual(['new', 'old']);
    expect(groups[0].outputs[1]).toEqual({
      pick: { kind: 'output', media_id: 'a', job_id: 'old' },
      result: histories[2].results[2],
    });
  });
});

it('formats recorded details in order and leaves absent older fields out', () => {
  const saved = {
    stages: [
      { kind: 'restore', model_id: 'a', model_label: 'Fix' },
      { kind: 'upscale', model_id: 'b', model_label: 'Upscale' },
    ],
    ui: { fixes: ['blur', 'noise'] },
    scale: 4,
    elapsed_seconds: 31,
    device: 'cuda:0',
    completed_at: 1700000000000,
  } as ResultSummary;
  expect(resultDetails(saved, 400, 300)).toEqual([
    'Models: Fix → Upscale',
    'Fixes: Blur → Noise',
    'Scale: 4×',
    'Output: 400 × 300',
    'Time: 31 s',
    'Device: cuda:0',
    `Date: ${new Date(saved.completed_at).toLocaleDateString()}`,
  ]);
  expect(
    resultDetails(
      { model_label: 'SPAN', device: 'mps', neural_engine: true } as ResultSummary,
      0,
      0,
      hardware,
    ),
  ).toEqual(['Model: SPAN', 'Device: Neural Engine']);
  expect(
    resultDetails(
      { model_label: 'SPAN', device: 'mps', neural_engine: false } as ResultSummary,
      0,
      0,
      hardware,
    ),
  ).toEqual(['Model: SPAN', 'Device: Apple M4 Pro']);
  expect(resultDetails({ model_label: 'Unknown model' } as ResultSummary)).toEqual([]);
  expect(
    resultDetails({
      kind: 'video',
      model_label: 'Frame by frame',
      partial: true,
      kept_seconds: 5,
      output_duration_seconds: 12,
    } as ResultSummary),
    // The Unfinished mark heads the slot's details; it is not one of the facts.
  ).toEqual(['Model: Frame by frame']);
});

describe('the lines of the details strip', () => {
  const facts = [
    'Models: NAFNet GoPro Deblur → SPAN ×4 NomosUni',
    'Fixes: Blur',
    'Scale: 4×',
    'Output: 5760 × 7680',
    'Time: 1:40',
    'Device: Neural Engine',
    'Date: 11/23/2026',
  ];
  it('gives models and fixes a line each and shares the rest over what is left', () => {
    expect(detailLines(facts)).toEqual([
      [facts[0]],
      [facts[1]],
      [facts[2], facts[3], facts[4]],
      [facts[5], facts[6]],
    ]);
    // Without fixes the short facts spread over three lines.
    expect(detailLines(facts.filter((fact) => !fact.startsWith('Fixes')))).toEqual([
      [facts[0]],
      [facts[2], facts[3]],
      [facts[4], facts[5]],
      [facts[6]],
    ]);
    expect(detailLines(['Size: 1440 × 1920'])).toEqual([['Size: 1440 × 1920']]);
    expect(detailLines([])).toEqual([]);
  });
  it('never takes more lines than the strip has, whatever a job recorded', () => {
    const long = `Models: ${Array.from({ length: 40 }, (_, index) => `Model ${index}`).join(' → ')}`;
    for (const lines of [3, 4]) {
      for (let count = 0; count <= 12; count += 1) {
        const many = [long, 'Fixes: Noise → JPEG artifacts → Blur → Colour → Faces'].concat(
          Array.from({ length: count }, (_, index) => `Fact ${index}: value`),
        );
        const packed = detailLines(many, lines);
        expect(packed.length).toBeLessThanOrEqual(lines);
        expect(packed.flat()).toEqual(many);
      }
    }
  });
});

import { pickKey, toggleSlot, swapSlots, sliderBlock } from './compareOutputs';

describe('Compare controls', () => {
  const picks = [
    { kind: 'original', media_id: 'a' },
    { kind: 'output', media_id: 'a', job_id: 'one' },
    { kind: 'output', media_id: 'b', job_id: 'two' },
    { kind: 'original', media_id: 'b' },
    { kind: 'output', media_id: 'b', job_id: 'three' },
  ] as const;
  it('assigns slots in tick order, compacts removals and refuses a fifth', () => {
    let selected: ComparePick[] = picks.slice(0, 4);
    expect(toggleSlot(selected, picks[4])).toEqual(selected);
    selected = toggleSlot(selected, picks[1]);
    expect(selected.map(pickKey)).toEqual([
      pickKey(picks[0]),
      pickKey(picks[2]),
      pickKey(picks[3]),
    ]);
    expect(toggleSlot(selected, picks[4]).at(-1)).toEqual(picks[4]);
  });
  it('swaps occupied slots and requires matching input for the slider', () => {
    expect(swapSlots([...picks.slice(0, 3)], 1, 2)).toEqual([picks[0], picks[2], picks[1]]);
    expect(sliderBlock(picks.slice(0, 2))).toBe('');
    expect(sliderBlock([picks[0], picks[2]])).toContain('same image');
    expect(sliderBlock([picks[0]])).toContain('two');
  });
});

import { visibleRegion } from './compareRegions';

describe('visible compare regions', () => {
  it('requests only when the displayed preview is magnified, in source coordinates', () => {
    const input = {
      image: { width: 4000, height: 2000 },
      preview: { width: 1000, height: 500 },
      cell: { width: 500, height: 500 },
      fit: { width: 500, height: 250 },
      zoom: 4,
      pan: { x: 250, y: 0 },
    };
    expect(visibleRegion({ ...input, zoom: 1 })).toBeNull();
    expect(visibleRegion({ ...input, zoom: 1.5, pixelRatio: 2 })).not.toBeNull();
    expect(visibleRegion(input)).toEqual({
      x: 1000,
      y: 500,
      width: 1000,
      height: 1000,
      target_width: 500,
      target_height: 500,
    });
  });
});

import { pickKindBlock } from './compareOutputs';
it('keeps video and image slots homogeneous, resets after clearing and names video slider restrictions', () => {
  const media = [
    { id: 'v', kind: 'video' },
    { id: 'a', kind: 'image' },
  ];
  const v: ComparePick = { kind: 'original', media_id: 'v' };
  const a: ComparePick = { kind: 'original', media_id: 'a' };
  expect(pickKindBlock([v], a, media)).toBe('Compare videos with videos');
  expect(pickKindBlock([a], v, media)).toBe('Compare images with images');
  expect(toggleSlot([v], a, media)).toEqual([v]);
  expect(toggleSlot([], a, media)).toEqual([a]);
  expect(sliderBlock([v, a], 'video')).toContain('same video');
});
