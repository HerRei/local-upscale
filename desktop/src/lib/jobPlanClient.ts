import { writable, type Readable } from 'svelte/store';
import { isTauri, planJob, type HostPlanStage, type PlanJobOptions } from './api';
import { isRestoreTask, resolvePlan, type PlanStage } from './plan';
import type { AppSnapshot, UiSettings } from './types';

export type PlanState =
  | { status: 'loading'; stages: PlanStage[]; first: boolean }
  | { status: 'ready'; stages: PlanStage[] }
  | { status: 'error'; stages: []; message: string };

export type PlanFetcher = (
  snapshot: AppSnapshot,
  settings: UiSettings,
  options: PlanJobOptions,
) => Promise<PlanStage[]>;

const PLAN_TIMEOUT_MS = 5000;

export function toHostStage(stage: PlanStage): HostPlanStage {
  return {
    kind: stage.kind,
    role: stage.role,
    ...(stage.fix !== undefined ? { fix: stage.fix } : {}),
    ...(stage.remembered !== undefined ? { remembered: stage.remembered } : {}),
    ...(stage.missing !== undefined ? { missing: stage.missing } : {}),
    label: stage.label,
    ...(stage.model ? { model_id: stage.model.model_id } : {}),
    ...(stage.videoModel ? { video_model_id: stage.videoModel.model_id } : {}),
    ...(stage.interpolationModel
      ? { interpolation_model_id: stage.interpolationModel.model_id }
      : {}),
    ...(stage.audioModel ? { audio_model_id: stage.audioModel.model_id } : {}),
    ...(stage.editModel ? { edit_model_id: stage.editModel.model_id } : {}),
    ...(stage.custom !== undefined ? { custom: stage.custom } : {}),
    installed: stage.installed,
    can_download: stage.canDownload,
    needs_file: stage.needsFile,
    size_bytes: stage.sizeBytes,
  };
}

export function hydrateStage(stage: HostPlanStage, snapshot: AppSnapshot): PlanStage {
  const find = <T extends { model_id: string }>(
    list: T[] | undefined,
    id: string,
    kind: string,
  ): T => {
    const found = list?.find((entry) => entry.model_id === id);
    if (!found) throw new Error(`Snapshot is missing ${kind} model "${id}"`);
    return found;
  };

  return {
    kind: stage.kind,
    role: stage.role,
    ...(stage.fix !== undefined ? { fix: stage.fix } : {}),
    ...(stage.remembered !== undefined ? { remembered: stage.remembered } : {}),
    ...(stage.missing !== undefined ? { missing: stage.missing } : {}),
    label: stage.label,
    ...(stage.model_id !== undefined
      ? { model: find(snapshot.catalog.models, stage.model_id, 'catalog') }
      : {}),
    ...(stage.video_model_id !== undefined
      ? { videoModel: find(snapshot.catalog.video_models, stage.video_model_id, 'video') }
      : {}),
    ...(stage.interpolation_model_id !== undefined
      ? {
          interpolationModel: find(
            snapshot.catalog.interpolation_models,
            stage.interpolation_model_id,
            'interpolation',
          ),
        }
      : {}),
    ...(stage.audio_model_id !== undefined
      ? { audioModel: find(snapshot.catalog.audio_models, stage.audio_model_id, 'audio') }
      : {}),
    ...(stage.edit_model_id !== undefined
      ? {
          editModel:
            stage.edit_model_id === '__custom__'
              ? (() => {
                  if (!snapshot.edit.custom_model) {
                    throw new Error('Snapshot is missing custom edit model');
                  }
                  return snapshot.edit.custom_model;
                })()
              : find(snapshot.edit.models, stage.edit_model_id, 'edit'),
        }
      : {}),
    ...(stage.custom !== undefined ? { custom: stage.custom } : {}),
    installed: stage.installed,
    canDownload: stage.can_download,
    needsFile: stage.needs_file,
    sizeBytes: stage.size_bytes,
  };
}

function resolveOptions(snapshot: AppSnapshot, settings: UiSettings, options: PlanJobOptions) {
  const faceModel = options.face_model_id
    ? snapshot.catalog.models.find((model) => model.model_id === options.face_model_id)
    : undefined;
  const videoId = options.selected_video_model_id ?? settings.selected_video_model_id;
  const selectedVideoModel = snapshot.catalog.video_models.find(
    (model) => model.model_id === videoId,
  );
  const editId = options.edit_model_id ?? settings.edit_model_id;
  const editModel =
    editId === '__custom__'
      ? (snapshot.edit.custom_model ?? undefined)
      : snapshot.edit.models.find((model) => model.model_id === editId);
  const usingTemporalVideo =
    options.using_temporal_video ??
    (settings.task === 'video' &&
      !isRestoreTask(settings) &&
      settings.selected_video_model_id !== 'frame_by_frame');
  return {
    faceModel,
    faceEnabled: Boolean(options.face_enabled),
    usingTemporalVideo,
    selectedVideoModel,
    editModel,
    hasAudio: options.has_audio === null ? undefined : options.has_audio,
  };
}

export function planLocally(
  snapshot: AppSnapshot,
  settings: UiSettings,
  options: PlanJobOptions = {},
): HostPlanStage[] {
  return resolvePlan(snapshot, settings, resolveOptions(snapshot, settings, options)).map(
    toHostStage,
  );
}

export function planKey(
  snapshot: AppSnapshot,
  settings: UiSettings,
  options: PlanJobOptions = {},
): string {
  const installedIds = (list: { model_id: string; installed: boolean }[] | undefined): string[] =>
    (list ?? []).filter((model) => model.installed).map((model) => model.model_id);
  const sortedSettings = Object.fromEntries(
    Object.keys(settings)
      .sort()
      .map((key) => [key, settings[key as keyof UiSettings]]),
  );
  return JSON.stringify({
    settings: sortedSettings,
    options: {
      face_model_id: options.face_model_id ?? null,
      face_enabled: Boolean(options.face_enabled),
      using_temporal_video: options.using_temporal_video ?? null,
      selected_video_model_id: options.selected_video_model_id ?? null,
      edit_model_id: options.edit_model_id ?? null,
      has_audio: options.has_audio ?? null,
    },
    installed: {
      models: installedIds(snapshot.catalog.models),
      video: installedIds(snapshot.catalog.video_models),
      interpolation: installedIds(snapshot.catalog.interpolation_models),
      audio: installedIds(snapshot.catalog.audio_models),
      edit: installedIds(snapshot.edit.models),
      editCustom: snapshot.edit.custom_model
        ? [snapshot.edit.custom_model.installed, snapshot.edit.custom_model.total_size_bytes]
        : null,
    },
    devices: snapshot.capabilities.devices.map((device) => device.id),
  });
}

export async function fetchPlan(
  snapshot: AppSnapshot,
  settings: UiSettings,
  options: PlanJobOptions = {},
): Promise<PlanStage[]> {
  if (!isTauri()) {
    return resolvePlan(snapshot, settings, resolveOptions(snapshot, settings, options));
  }
  const stages = await planJob(settings, options);
  return stages.map((stage) => hydrateStage(stage, snapshot));
}

export function createPlanClient(fetch: PlanFetcher = fetchPlan): {
  state: Readable<PlanState>;
  request(snapshot: AppSnapshot, settings: UiSettings, options?: PlanJobOptions): void;
  settled(): Promise<PlanState>;
  retry(): void;
} {
  let current: PlanState = { status: 'loading', stages: [], first: true };
  const store = writable<PlanState>(current);
  const setState = (next: PlanState) => {
    current = next;
    store.set(next);
  };

  interface PendingRequest {
    key: string;
    snapshot: AppSnapshot;
    settings: UiSettings;
    options: PlanJobOptions;
  }

  let newestKey: string | null = null;
  let settledKey: string | null = null;
  let lastValidStages: PlanStage[] | null = null;
  let lastRequest: PendingRequest | null = null;
  let queued: PendingRequest | null = null;
  let inflight: PendingRequest | null = null;
  let flushScheduled = false;
  let waiters: ((state: PlanState) => void)[] = [];

  const notifyWaiters = () => {
    if (queued || inflight || flushScheduled) return;
    const pending = waiters;
    waiters = [];
    for (const resolve of pending) resolve(current);
  };

  const scheduleFlush = () => {
    if (flushScheduled || inflight || !queued) return;
    flushScheduled = true;
    queueMicrotask(() => {
      flushScheduled = false;
      void flush();
    });
  };

  async function runRequest(req: PendingRequest): Promise<void> {
    inflight = req;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const stages = await Promise.race([
        fetch(req.snapshot, req.settings, req.options),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            reject(new Error('Could not work out the plan in time.'));
          }, PLAN_TIMEOUT_MS);
        }),
      ]);
      if (req.key === newestKey && !queued) {
        settledKey = req.key;
        lastValidStages = stages;
        setState({ status: 'ready', stages });
      }
    } catch (error) {
      if (req.key === newestKey && !queued) {
        settledKey = req.key;
        lastValidStages = null;
        const message = error instanceof Error ? error.message : String(error);
        setState({ status: 'error', stages: [], message });
      }
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      inflight = null;
      if (queued) {
        void flush();
      } else {
        notifyWaiters();
      }
    }
  }

  async function flush(): Promise<void> {
    if (inflight || !queued) {
      notifyWaiters();
      return;
    }
    const next = queued;
    queued = null;
    await runRequest(next);
  }

  return {
    state: { subscribe: store.subscribe },
    request(snapshot: AppSnapshot, settings: UiSettings, options: PlanJobOptions = {}): void {
      const key = planKey(snapshot, settings, options);
      const entry: PendingRequest = { key, snapshot, settings, options };
      lastRequest = entry;
      newestKey = key;
      if (queued && key === queued.key) return;
      if (!inflight && key === settledKey) {
        queued = null;
        if (lastValidStages !== null && current.status !== 'ready') {
          setState({ status: 'ready', stages: lastValidStages });
        }
        notifyWaiters();
        return;
      }
      if (inflight && key === inflight.key) {
        queued = null;
        return;
      }
      queued = entry;
      setState({
        status: 'loading',
        stages: lastValidStages ?? [],
        first: lastValidStages === null,
      });
      scheduleFlush();
    },
    settled(): Promise<PlanState> {
      if (!queued && !inflight && !flushScheduled) {
        return Promise.resolve(current);
      }
      return new Promise<PlanState>((resolve) => {
        waiters.push(resolve);
      });
    },
    retry(): void {
      if (!lastRequest) return;
      settledKey = null;
      newestKey = lastRequest.key;
      queued = lastRequest;
      setState({
        status: 'loading',
        stages: lastValidStages ?? [],
        first: lastValidStages === null,
      });
      if (!inflight) {
        flushScheduled = false;
        void flush();
      }
    },
  };
}
