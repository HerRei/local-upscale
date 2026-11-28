import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { fixStageKind } from './fixStages';
import { planLine } from './planLine';
import { resolvePlan } from './plan';
import {
  WORKFLOWS,
  workflowDownload,
  workflowMatches,
  workflowPlan,
  workflowSettings,
} from './workflows';
import type { UiSettings } from './types';

const TABLE = [
  {
    id: 'workflow.old-scan',
    task: 'upscale',
    content: 'photo',
    quality: 'quick',
    scale: 2,
    line: ['Upscale 2×'],
    models: ['localsr_quick_x4_v1'],
    names: ['LocalSR Photo S'],
    bytes: 2479984,
    text: '2 MB',
  },
  {
    id: 'workflow.low-light',
    task: 'denoise',
    content: 'photo',
    quality: 'quick',
    scale: 1,
    line: ['Noise'],
    models: ['denoise_realplksr_1x'],
    names: ['RealPLKSR Denoise'],
    bytes: 29559554,
    text: '30 MB',
  },
  {
    id: 'workflow.drawing',
    task: 'upscale',
    content: 'illustration',
    quality: 'quick',
    scale: 4,
    line: ['Upscale 4×'],
    models: ['realesrgan_x4plus_anime_6b'],
    names: ['Real-ESRGAN ×4 Anime'],
    bytes: 17938799,
    text: '18 MB',
  },
  {
    id: 'workflow.web-jpeg',
    task: 'upscale',
    content: 'photo',
    quality: 'quick',
    scale: 2,
    line: ['JPEG', 'Upscale 2×'],
    models: ['fbcnn_color', 'localsr_quick_x4_v1'],
    names: ['FBCNN Color', 'LocalSR Photo S'],
    bytes: 287755111 + 2479984,
    text: '290 MB',
  },
] as const;

function start(): { snapshot: ReturnType<typeof demoSnapshot>; settings: UiSettings } {
  const snapshot = demoSnapshot();
  return { snapshot, settings: { ...snapshot.settings, preset_pins: {} } };
}

describe('workflows', () => {
  it('defines the four, in order, with the exact copy', () => {
    expect(WORKFLOWS.map((workflow) => workflow.id)).toEqual(TABLE.map((row) => row.id));
    expect(WORKFLOWS.map((workflow) => workflow.name)).toEqual([
      'Old scan',
      'Low-light phone photo',
      'Drawing or anime',
      'Web image (JPEG)',
    ]);
    expect(WORKFLOWS.map((workflow) => workflow.example)).toEqual([
      'photo-quick',
      'noise',
      'art-quick',
      'jpeg',
    ]);
    expect(WORKFLOWS[0].detail_extra).toBe(
      "Black and white? Tick Colour afterwards. The colours are the model's guess.",
    );
    expect(WORKFLOWS[3].for).toBe('For small, blocky pictures saved from websites or chats.');
    expect(WORKFLOWS[0].note).toBe(
      'Enlarges and sharpens, and smooths film grain. Scratches and dust stay unless you tick Dust & scratches and paint over them. Colours are not changed.',
    );
  });

  it('resolves the model table from the bundled catalog with nothing remembered', () => {
    const { snapshot, settings } = start();
    for (const row of TABLE) {
      const workflow = WORKFLOWS.find((entry) => entry.id === row.id)!;
      const patch = workflowSettings(workflow, snapshot, settings);
      const ui = { ...settings, ...patch } as UiSettings;
      expect(patch.task, row.id).toBe(row.task);
      expect(patch.content, row.id).toBe(row.content);
      expect(patch.quality, row.id).toBe(row.quality);
      expect(patch.output_scale, row.id).toBe(row.scale);
      const stages = resolvePlan(snapshot, ui, { faceEnabled: false, usingTemporalVideo: false });
      expect(planLine(stages, ui), row.id).toEqual(row.line);
      expect(
        stages.map((stage) => stage.model?.model_id),
        row.id,
      ).toEqual(row.models);
      expect(workflowPlan(workflow, snapshot, settings).map((entry) => entry.model)).toEqual(
        row.names,
      );
      const download = workflowDownload(workflow, snapshot, settings);
      expect(download.bytes, row.id).toBe(row.bytes);
      expect(download.text, row.id).toBe(row.text);
    }
  });

  it('names the jpeg fix a deblock stage and leaves output, hardware and tiles alone', () => {
    const { snapshot, settings } = start();
    const web = WORKFLOWS.find((entry) => entry.id === 'workflow.web-jpeg')!;
    const patch = workflowSettings(web, snapshot, settings);
    expect(patch.fix_stages).toEqual([{ fix: 'jpeg', model_id: 'fbcnn_color' }]);
    const model = snapshot.catalog.models.find((entry) => entry.model_id === 'fbcnn_color');
    expect(fixStageKind(model, 'jpeg')).toBe('deblock');
    for (const key of [
      'output_format',
      'output_directory',
      'tile_size',
      'precision',
      'device_id',
      'jpeg_quality',
      'safe_memory',
    ] as const)
      expect(key in patch, key).toBe(false);
  });

  it('respects remembered fix models and a better Quick model', () => {
    const { snapshot, settings } = start();
    const lowLight = WORKFLOWS.find((entry) => entry.id === 'workflow.low-light')!;
    const pinnedNoise = { ...settings, preset_pins: { 'fix/noise/quick': 'nafnet_sidd_width64' } };
    expect(workflowSettings(lowLight, snapshot, pinnedNoise).selected_model_id).toBe(
      'nafnet_sidd_width64',
    );
    const drawing = WORKFLOWS.find((entry) => entry.id === 'workflow.drawing')!;
    const pins = { 'upscale/illustration/quick': 'realplksr_nomoswebphoto_x4' };
    const pinned = workflowSettings(drawing, snapshot, { ...settings, preset_pins: pins });
    expect(pinned.selected_model_id).toBe('realplksr_nomoswebphoto_x4');
    expect(pinned.output_scale).toBe(4);
  });

  it('matches only while the settings equal the result', () => {
    const { snapshot, settings } = start();
    for (const workflow of WORKFLOWS) {
      expect(workflowMatches(workflow, snapshot, settings), workflow.id).toBe(false);
      const applied = {
        ...settings,
        ...workflowSettings(workflow, snapshot, settings),
      } as UiSettings;
      expect(workflowMatches(workflow, snapshot, applied), workflow.id).toBe(true);
      expect(workflowMatches(workflow, snapshot, { ...applied, quality: 'custom' })).toBe(false);
      expect(
        workflowMatches(workflow, snapshot, {
          ...applied,
          content: applied.content === 'photo' ? 'illustration' : 'photo',
        }),
      ).toBe(false);
      expect(
        workflowMatches(workflow, snapshot, {
          ...applied,
          output_scale: applied.output_scale === 1 ? 2 : 1,
        }),
      ).toBe(false);
    }
    const old = WORKFLOWS[0];
    const applied = { ...settings, ...workflowSettings(old, snapshot, settings) } as UiSettings;
    expect(
      workflowMatches(old, snapshot, {
        ...applied,
        fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
      }),
    ).toBe(false);
  });
});
