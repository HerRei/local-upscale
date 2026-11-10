import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { applyWorkerEnvelope } from './workerEvents';
import { moveFixStage, toggleFixStage } from './fixStages';
import { pendingDownloads, resolvePlan } from './plan';

describe('several fixes in one pass', () => {
  const blur = { fix: 'blur' as const, model_id: 'nafnet_gopro_deblur' };
  const jpeg = { fix: 'jpeg' as const, model_id: 'fbcnn_color' };
  const noise = { fix: 'noise' as const, model_id: 'scunet_color_real_psnr' };

  it('turns a fix on at its place in the order blur, JPEG, noise and off again', () => {
    expect(toggleFixStage([], 'noise').map((stage) => stage.fix)).toEqual(['noise']);
    expect(toggleFixStage([noise], 'blur').map((stage) => stage.fix)).toEqual(['blur', 'noise']);
    expect(toggleFixStage([blur, noise], 'jpeg').map((stage) => stage.fix)).toEqual([
      'blur',
      'jpeg',
      'noise',
    ]);
    // A new fix has no model yet; resolving the recipe gives it one.
    expect(toggleFixStage([blur], 'jpeg')[1]).toEqual({ fix: 'jpeg', model_id: '' });
    // An order the user changed stays; the new fix goes before the first that follows it.
    expect(toggleFixStage([noise, blur], 'jpeg').map((stage) => stage.fix)).toEqual([
      'jpeg',
      'noise',
      'blur',
    ]);
    expect(toggleFixStage([blur, jpeg, noise], 'jpeg')).toEqual([blur, noise]);
  });

  it('moves one fix earlier or later and leaves the ends alone', () => {
    expect(moveFixStage([blur, jpeg, noise], 'noise', -1)).toEqual([blur, noise, jpeg]);
    expect(moveFixStage([blur, jpeg, noise], 'blur', 1)).toEqual([jpeg, blur, noise]);
    const stages = [blur, noise];
    expect(moveFixStage(stages, 'blur', -1)).toBe(stages);
    expect(moveFixStage(stages, 'noise', 1)).toBe(stages);
    expect(moveFixStage(stages, 'jpeg', 1)).toBe(stages);
  });

  it('keeps every ordered fix row visible, deduplicates downloads, and identifies the Restore primary', () => {
    const snapshot = demoSnapshot();
    const settings = {
      ...snapshot.settings,
      task: 'upscale' as const,
      selected_model_id: 'span_photo_x4',
      fix_stages: [blur, { fix: 'jpeg' as const, model_id: 'missing' }, noise],
    };
    const options = { faceEnabled: false, usingTemporalVideo: false };
    const plan = resolvePlan(snapshot, settings, options);
    expect(plan.map((stage) => [stage.role, stage.fix, stage.kind])).toEqual([
      ['fix', 'blur', 'restore'],
      ['fix', 'jpeg', 'deblock'],
      ['fix', 'noise', 'restore'],
      ['primary', undefined, 'upscale'],
    ]);
    // A fix without a model stays in the plan and blocks Start; it needs no file.
    expect(plan[1]).toMatchObject({
      label: 'Fix JPEG',
      missing: true,
      installed: false,
      canDownload: false,
      needsFile: false,
    });
    // A model that does not fix the problem it is listed for is no model for it.
    const misfit = resolvePlan(
      snapshot,
      { ...settings, fix_stages: [{ fix: 'blur' as const, model_id: 'fbcnn_color' }] },
      options,
    );
    expect(misfit[0]).toMatchObject({ fix: 'blur', missing: true });
    const downloads = pendingDownloads([...plan, plan[0]]);
    expect(downloads.stages.filter((stage) => stage.fix === 'blur')).toHaveLength(1);
    expect(downloads.bytes).toBe(plan[0].sizeBytes + plan[2].sizeBytes + plan[3].sizeBytes);
    const restore = resolvePlan(
      snapshot,
      { ...settings, task: 'denoise', selected_model_id: 'scunet_color_real_psnr' },
      options,
    );
    expect(restore.map((stage) => [stage.role, stage.fix, stage.label])).toEqual([
      ['fix', 'blur', 'Fix blur'],
      ['fix', 'jpeg', 'Fix JPEG'],
      ['primary', 'noise', 'Restore'],
    ]);
  });

  it('counts a model used by two stages once', () => {
    const snapshot = demoSnapshot();
    const model = snapshot.catalog.models.find((entry) => entry.model_id === 'fbcnn_color')!;
    model.installed = false;
    const plan = resolvePlan(
      snapshot,
      {
        ...snapshot.settings,
        task: 'denoise',
        selected_model_id: 'fbcnn_color',
        fix_stages: [jpeg],
      },
      { faceEnabled: false, usingTemporalVideo: false },
    );
    expect(pendingDownloads([...plan, ...plan]).bytes).toBe(model.size_bytes);
  });

  it('keeps the running stage in the title while tiles progress in a job of several stages', () => {
    const title = (index: number, count: number): string => {
      let snapshot = applyWorkerEnvelope(demoSnapshot(), {
        type: 'stage_started',
        data: {
          job_id: 'j',
          stage_index: index,
          stage_count: count,
          stage_kind: 'restore',
          model_id: 'noise',
        },
      });
      snapshot = applyWorkerEnvelope(snapshot, {
        type: 'progress',
        data: { job_id: 'j', percentage: 45, completed_tiles: 1, total_tiles: 4 },
      });
      return snapshot.runtime.status_title;
    };
    expect(title(2, 5)).toBe('Restoring · stage 3/5');
    // A one-stage job reads as before.
    expect(title(0, 1)).toBe('Enhancing');
  });
});
