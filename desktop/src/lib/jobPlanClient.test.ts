import fs from 'node:fs';
import path from 'node:path';
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HostPlanStage } from './api';
import { demoSnapshot } from './demo';
import type { CatalogModel, EditModel, UiSettings } from './types';

const api = vi.hoisted(() => ({
  isTauri: vi.fn(() => true),
  planJob: vi.fn(async (): Promise<HostPlanStage[]> => []),
}));

vi.mock('./api', () => api);

import {
  createPlanClient,
  fetchPlan,
  hydrateStage,
  planKey,
  planLocally,
  toHostStage,
  type PlanState,
} from './jobPlanClient';
import * as planModule from './plan';

const FIXTURE_PATH = path.resolve(__dirname, '../../src-tauri/tests/fixtures/job_plan_cases.json');

function customEditModel(installed = false): EditModel {
  return {
    model_id: '__custom__',
    name: 'Custom Qwen',
    family: 'qwen-image-2.1',
    quantization: 'custom',
    files: [],
    min_unified_memory_gb: 24,
    min_vram_gb: 12,
    default_steps: 20,
    cfg_scale: 4,
    license_name: 'Custom',
    license_url: '',
    source_url: '',
    terms_acceptance_required: false,
    automated_download_allowed: false,
    installed,
    total_size_bytes: 1234,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('jobPlanClient', () => {
  beforeEach(() => {
    api.isTauri.mockReset();
    api.isTauri.mockReturnValue(true);
    api.planJob.mockReset();
    api.planJob.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('hydrates each stage kind from the snapshot while keeping host availability flags', () => {
    const snapshot = demoSnapshot();
    snapshot.edit.custom_model = customEditModel(false);
    const catalogModel = snapshot.catalog.models.find((m) => m.model_id === 'span_photo_x4')!;
    const videoModel = snapshot.catalog.video_models.find((m) => m.model_id === 'flashvsr_v11')!;
    const interpModel = snapshot.catalog.interpolation_models[0];
    const audioModel = snapshot.catalog.audio_models![0];
    const editModel = snapshot.edit.models[0];

    // Flip snapshot installed flags the opposite way of the host stages to prove host wins.
    catalogModel.installed = false;
    videoModel.installed = true;
    interpModel.installed = false;
    audioModel.installed = true;
    editModel.installed = false;

    const hydratedCatalog = hydrateStage(
      {
        kind: 'upscale',
        role: 'primary',
        label: 'Upscale ×4',
        model_id: catalogModel.model_id,
        installed: true,
        can_download: false,
        needs_file: false,
        size_bytes: 0,
      },
      snapshot,
    );
    expect(hydratedCatalog.model).toBe(catalogModel);
    expect(hydratedCatalog).toMatchObject({
      installed: true,
      canDownload: false,
      needsFile: false,
      sizeBytes: 0,
    });

    const hydratedVideo = hydrateStage(
      {
        kind: 'video',
        role: 'primary',
        label: 'Upscale ×4',
        video_model_id: videoModel.model_id,
        installed: false,
        can_download: true,
        needs_file: false,
        size_bytes: 999,
      },
      snapshot,
    );
    expect(hydratedVideo.videoModel).toBe(videoModel);
    expect(hydratedVideo).toMatchObject({
      installed: false,
      canDownload: true,
      needsFile: false,
      sizeBytes: 999,
    });

    const hydratedInterp = hydrateStage(
      {
        kind: 'interpolate',
        role: 'interpolate',
        label: 'Interpolate 2×',
        interpolation_model_id: interpModel.model_id,
        installed: true,
        can_download: false,
        needs_file: false,
        size_bytes: 0,
      },
      snapshot,
    );
    expect(hydratedInterp.interpolationModel).toBe(interpModel);
    expect(hydratedInterp.installed).toBe(true);

    const hydratedAudio = hydrateStage(
      {
        kind: 'audio_denoise',
        role: 'audio_denoise',
        label: 'Clean up audio',
        audio_model_id: audioModel.model_id,
        installed: false,
        can_download: true,
        needs_file: false,
        size_bytes: 777,
      },
      snapshot,
    );
    expect(hydratedAudio.audioModel).toBe(audioModel);
    expect(hydratedAudio.sizeBytes).toBe(777);

    const hydratedEdit = hydrateStage(
      {
        kind: 'edit',
        role: 'primary',
        label: 'Edit',
        edit_model_id: editModel.model_id,
        installed: true,
        can_download: false,
        needs_file: false,
        size_bytes: 0,
      },
      snapshot,
    );
    expect(hydratedEdit.editModel).toBe(editModel);
    expect(hydratedEdit.installed).toBe(true);

    const hydratedCustomEdit = hydrateStage(
      {
        kind: 'edit',
        role: 'primary',
        label: 'Edit',
        edit_model_id: '__custom__',
        installed: true,
        can_download: false,
        needs_file: false,
        size_bytes: 0,
      },
      snapshot,
    );
    expect(hydratedCustomEdit.editModel).toBe(snapshot.edit.custom_model);
    expect(hydratedCustomEdit.installed).toBe(true);

    const hydratedCustom = hydrateStage(
      {
        kind: 'upscale',
        role: 'primary',
        label: 'Upscale ×4',
        custom: '/models/own.safetensors',
        installed: true,
        can_download: false,
        needs_file: false,
        size_bytes: 0,
      },
      snapshot,
    );
    expect(hydratedCustom.custom).toBe('/models/own.safetensors');
  });

  it('throws when hydrateStage encounters a model id missing from the snapshot', () => {
    const snapshot = demoSnapshot();
    expect(() =>
      hydrateStage(
        {
          kind: 'upscale',
          role: 'primary',
          label: 'Upscale ×4',
          model_id: 'does_not_exist',
          installed: false,
          can_download: true,
          needs_file: false,
          size_bytes: 100,
        },
        snapshot,
      ),
    ).toThrow();
  });

  it('plans the Lightning file for Quick only on an engine that runs it', () => {
    const snapshot = demoSnapshot();
    const settings: UiSettings = {
      ...snapshot.settings,
      task: 'edit',
      edit_model_id: 'qwen_edit_2511_q2_k',
    };
    expect(planLocally(snapshot, settings).map((stage) => stage.kind)).toEqual(['edit']);
    snapshot.engine = {
      protocol_version: 1,
      minimum_protocol_version: 1,
      engine_id: 'demo',
      engine_version: '0.7.0',
      features: ['image_edit', 'edit_lightning'],
      model_formats: [],
      video_engines: [],
    };
    const quick = planLocally(snapshot, settings);
    expect(quick.map((stage) => [stage.kind, stage.edit_model_id])).toEqual([
      ['edit', 'qwen_edit_2511_q2_k'],
      ['edit_addon', 'qwen_edit_2511_lightning_4step'],
    ]);
    expect(hydrateStage(quick[1], snapshot).editModel?.kind).toBe('speed_addon');
    expect(planLocally(snapshot, { ...settings, edit_speed: 'full' })).toHaveLength(1);
  });

  it('round-trips every stage in the parity fixture through hydrateStage and toHostStage', () => {
    const raw = JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf8')) as {
      cases: {
        installed_ids: string[];
        extra_models?: CatalogModel[];
        expected_plan: HostPlanStage[];
      }[];
    };
    const base = demoSnapshot();
    for (const entry of raw.cases) {
      // Models that exist only in a case are in its catalog, as the plan saw them.
      const snapshot = entry.extra_models
        ? {
            ...base,
            catalog: { ...base.catalog, models: [...base.catalog.models, ...entry.extra_models] },
          }
        : base;
      for (const stage of entry.expected_plan) {
        expect(toHostStage(hydrateStage(stage, snapshot))).toEqual(stage);
      }
    }
  });

  it('computes planKey ignoring runtime progress and varying on install, setting, or has_audio changes', () => {
    const base = demoSnapshot();
    const settings: UiSettings = { ...base.settings, task: 'video', video_clean_audio: true };
    const options = { face_enabled: false, has_audio: true };
    const keyBase = planKey(base, settings, options);

    const progressChanged = demoSnapshot();
    progressChanged.runtime.progress = 73;
    expect(planKey(progressChanged, settings, options)).toBe(keyBase);

    const modelInstalled = demoSnapshot();
    modelInstalled.catalog.models[0].installed = true;
    expect(planKey(modelInstalled, settings, options)).not.toBe(keyBase);

    expect(planKey(base, { ...settings, output_scale: 2 }, options)).not.toBe(keyBase);
    expect(planKey(base, settings, { face_enabled: false, has_audio: false })).not.toBe(keyBase);
  });

  it('calls planJob once inside Tauri without invoking resolvePlan and transitions loading -> ready', async () => {
    const snapshot = demoSnapshot();
    const settings: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      selected_model_id: 'span_photo_x4',
    };
    const hostStages = planLocally(snapshot, settings, { face_enabled: false, has_audio: false });
    const pending = deferred<HostPlanStage[]>();
    api.planJob.mockReturnValueOnce(pending.promise);
    const resolveSpy = vi.spyOn(planModule, 'resolvePlan');

    const client = createPlanClient();
    const seen: PlanState[] = [];
    const unsubscribe = client.state.subscribe((value) => seen.push(value));

    client.request(snapshot, settings, { face_enabled: false, has_audio: false });
    expect(get(client.state)).toEqual({ status: 'loading', stages: [], first: true });

    await Promise.resolve();
    expect(api.planJob).toHaveBeenCalledTimes(1);
    expect(api.planJob).toHaveBeenCalledWith(settings, {
      face_enabled: false,
      has_audio: false,
    });
    expect(resolveSpy).not.toHaveBeenCalled();

    pending.resolve(hostStages);
    await client.settled();

    expect(get(client.state)).toEqual({
      status: 'ready',
      stages: hostStages.map((stage) => hydrateStage(stage, snapshot)),
    });
    expect(seen.map((s) => s.status)).toEqual(['loading', 'loading', 'ready']);
    unsubscribe();
  });

  it('coalesces three requests in one tick into one planJob call with the last settings', async () => {
    const snapshot = demoSnapshot();
    const s1: UiSettings = { ...snapshot.settings, task: 'upscale', output_scale: 2 };
    const s2: UiSettings = { ...snapshot.settings, task: 'upscale', output_scale: 3 };
    const s3: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      output_scale: 4,
      selected_model_id: 'span_photo_x4',
    };
    api.planJob.mockResolvedValueOnce(planLocally(snapshot, s3, {}));

    const client = createPlanClient();
    client.request(snapshot, s1, {});
    client.request(snapshot, s2, {});
    client.request(snapshot, s3, {});

    await client.settled();
    expect(api.planJob).toHaveBeenCalledTimes(1);
    expect(api.planJob).toHaveBeenCalledWith(s3, {});
  });

  it('queues a newer request while one is in flight, drops the stale answer, and ends on the second plan', async () => {
    const snapshot = demoSnapshot();
    const s1: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      output_scale: 2,
      selected_model_id: 'span_photo_x4',
    };
    const s2: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      output_scale: 4,
      selected_model_id: 'span_photo_x4',
    };
    const p1 = deferred<HostPlanStage[]>();
    const p2 = deferred<HostPlanStage[]>();
    api.planJob.mockReturnValueOnce(p1.promise).mockReturnValueOnce(p2.promise);

    const client = createPlanClient();
    client.request(snapshot, s1, {});
    await Promise.resolve();
    expect(api.planJob).toHaveBeenCalledTimes(1);

    client.request(snapshot, s2, {});
    expect(api.planJob).toHaveBeenCalledTimes(1);

    p1.resolve(planLocally(snapshot, s1, {}));
    await vi.waitFor(() => expect(api.planJob).toHaveBeenCalledTimes(2));
    expect(api.planJob).toHaveBeenLastCalledWith(s2, {});
    expect(get(client.state).status).toBe('loading');

    const expected2 = planLocally(snapshot, s2, {});
    p2.resolve(expected2);
    const final = await client.settled();
    expect(final).toEqual({
      status: 'ready',
      stages: expected2.map((stage) => hydrateStage(stage, snapshot)),
    });
  });

  it('does not call planJob again when requested with the already shown key', async () => {
    const snapshot = demoSnapshot();
    const settings: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      selected_model_id: 'span_photo_x4',
    };
    api.planJob.mockResolvedValue(planLocally(snapshot, settings, {}));

    const client = createPlanClient();
    client.request(snapshot, settings, {});
    await client.settled();
    expect(api.planJob).toHaveBeenCalledTimes(1);

    client.request(snapshot, settings, {});
    await client.settled();
    expect(api.planJob).toHaveBeenCalledTimes(1);
  });

  it('reports rejection as error with empty stages and allows retry()', async () => {
    const snapshot = demoSnapshot();
    const settings: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      selected_model_id: 'span_photo_x4',
    };
    const expected = planLocally(snapshot, settings, {});
    api.planJob
      .mockRejectedValueOnce(new Error('host plan failed'))
      .mockResolvedValueOnce(expected);

    const client = createPlanClient();
    client.request(snapshot, settings, {});
    const errState = await client.settled();
    expect(errState).toEqual({ status: 'error', stages: [], message: 'host plan failed' });

    client.retry();
    const okState = await client.settled();
    expect(api.planJob).toHaveBeenCalledTimes(2);
    expect(okState).toEqual({
      status: 'ready',
      stages: expected.map((stage) => hydrateStage(stage, snapshot)),
    });
  });

  it('fails after 5 s without an answer', async () => {
    vi.useFakeTimers();
    const snapshot = demoSnapshot();
    const settings: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      selected_model_id: 'span_photo_x4',
    };
    api.planJob.mockImplementationOnce(() => new Promise<HostPlanStage[]>(() => {}));

    const client = createPlanClient();
    client.request(snapshot, settings, {});
    await vi.advanceTimersByTimeAsync(0);
    expect(api.planJob).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5000);
    expect(get(client.state)).toMatchObject({ status: 'error', stages: [] });
  });

  it('settled() resolves after an in-flight request completes', async () => {
    const snapshot = demoSnapshot();
    const settings: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      selected_model_id: 'span_photo_x4',
    };
    const pending = deferred<HostPlanStage[]>();
    api.planJob.mockReturnValueOnce(pending.promise);

    const client = createPlanClient();
    client.request(snapshot, settings, {});

    let settledResolved = false;
    const settledPromise = client.settled().then((state) => {
      settledResolved = true;
      return state;
    });
    await Promise.resolve();
    expect(settledResolved).toBe(false);

    const expected = planLocally(snapshot, settings, {});
    pending.resolve(expected);
    const result = await settledPromise;
    expect(settledResolved).toBe(true);
    expect(result.status).toBe('ready');
  });

  it('uses resolvePlan outside Tauri and never calls planJob', async () => {
    api.isTauri.mockReturnValue(false);
    const snapshot = demoSnapshot();
    const settings: UiSettings = {
      ...snapshot.settings,
      task: 'upscale',
      selected_model_id: 'span_photo_x4',
    };
    const expected = await fetchPlan(snapshot, settings, { face_enabled: false });

    const client = createPlanClient();
    client.request(snapshot, settings, { face_enabled: false });
    const final = await client.settled();

    expect(api.planJob).not.toHaveBeenCalled();
    expect(final).toEqual({ status: 'ready', stages: expected });
  });
});
