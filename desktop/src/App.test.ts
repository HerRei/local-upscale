// @vitest-environment jsdom

import { emptyUpdateStatus, updateDialogOpen, updateState } from './lib/updates';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';

import App from './App.svelte';
import { demoSnapshot } from './lib/demo';
import { applyWorkerEnvelope } from './lib/workerEvents';
import type {
  AppSnapshot,
  ComparePick,
  DeviceInfo,
  EngineDownloadStatus,
  JobRecord,
  LaunchIntent,
  MediaItem,
  MediaResults,
  ModelMoveProgress,
  Recipe,
  ResultSummary,
  StartBatchInput,
  TrialEstimate,
  UiSettings,
  WorkerEnvelope,
} from './lib/types';

/** One finished attempt of an image, as list_media_results reports it. */
function row(id: string, overrides: Partial<ResultSummary> = {}): ResultSummary {
  return {
    job_id: id,
    completed_at: 1_700_000_000_000,
    status: 'completed',
    kind: 'upscale',
    output_path: `/out/${id}.png`,
    output_name: `${id}.png`,
    available: true,
    model_id: id,
    model_label: id,
    scale: 4,
    prompt: '',
    max_dimension: 0,
    stages: [],
    notes: '',
    elapsed_seconds: 12,
    ui: null,
    ...overrides,
  };
}

function historyOf(mediaId: string, rows: ResultSummary[]): MediaResults {
  const saved = rows.filter((result) => result.status === 'completed');
  const available = saved.filter((result) => result.available);
  return {
    media_id: mediaId,
    results: rows,
    total_count: rows.length,
    completed_count: saved.length,
    available_count: available.length,
    default_result_id: available[0]?.job_id ?? null,
    next_before: null,
  };
}

const api = vi.hoisted(() => ({
  isTauri: vi.fn(() => true),
  bootstrap: vi.fn(),
  refreshSnapshot: vi.fn(),
  getCacheSize: vi.fn(async () => 0),
  clearCache: vi.fn(async () => 0),
  setRunLowPriority: vi.fn(async () => undefined),
  chooseMediaFiles: vi.fn(async (): Promise<string[]> => []),
  chooseMediaFolder: vi.fn(
    async (): Promise<{ paths: string[]; outputDirectory: string } | null> => null,
  ),
  chooseOutputDirectory: vi.fn(async () => null),
  chooseModelDirectory: vi.fn(async (): Promise<string | null> => null),
  setModelDirectory: vi.fn(async (_path: string): Promise<void> => undefined),
  resetModelDirectory: vi.fn(async (): Promise<void> => undefined),
  listenForModelMove: vi.fn(async (_handler: (progress: ModelMoveProgress) => void) => () => {}),
  dismissGpuDownloadNotice: vi.fn(async (): Promise<void> => undefined),
  dismissLinuxMenuPrompt: vi.fn(async (): Promise<void> => undefined),
  installLinuxMenu: vi.fn(async (): Promise<void> => undefined),
  uninstallLinuxMenu: vi.fn(async (): Promise<void> => undefined),
  refreshEngineCatalog: vi.fn(async () => []),
  installEngine: vi.fn(async (_backend: string): Promise<void> => undefined),
  cancelEngineInstall: vi.fn(async (): Promise<void> => undefined),
  useEngine: vi.fn(async (_backend: string): Promise<void> => undefined),
  removeEngine: vi.fn(async (_id: string): Promise<void> => undefined),
  chooseEngineManifest: vi.fn(async (): Promise<string | null> => null),
  importEngine: vi.fn(async (_path: string): Promise<void> => undefined),
  listenForEngineStatus: vi.fn(
    async (_handler: (status: EngineDownloadStatus) => void): Promise<() => void> =>
      () =>
        undefined,
  ),
  chooseCustomModel: vi.fn(async () => null),
  checkpointConsent: vi.fn(async (_path: string, _allow?: boolean) => false),
  addMedia: vi.fn(async (_paths: string[], _replace: boolean): Promise<void> => undefined),
  selectMedia: vi.fn(async (_id: string): Promise<void> => undefined),
  removeMedia: vi.fn(async () => undefined),
  clearMedia: vi.fn(async () => undefined),
  clearResults: vi.fn(async () => undefined),
  saveSettings: vi.fn(async (_settings: UiSettings): Promise<void> => undefined),
  planJob: vi.fn(async () => []),
  systemAccent: vi.fn(async () => null),
  recordFrontendError: vi.fn(async (_message: string, _stack: string): Promise<void> => undefined),
  setPreference: vi.fn(
    async (
      _name: string,
      _value:
        | boolean
        | UiSettings['appearance']
        | UiSettings['interface_scale']
        | NonNullable<UiSettings['contrast']>
        | NonNullable<UiSettings['viewer_background']>
        | NonNullable<UiSettings['accent']>
        | UiSettings['stopped_job_result'],
    ): Promise<void> => undefined,
  ),
  listCrashReports: vi.fn(
    async (): Promise<{ id: string; report: { message: string; os: string } }[]> => [],
  ),
  deleteCrashReport: vi.fn(async (_id: string) => {}),
  deleteAllCrashReports: vi.fn(async () => {}),
  setPrivacyChoice: vi.fn(
    async (_choices: {
      appearance: UiSettings['appearance'];
      colourBlindPalette: boolean;
      contrast: NonNullable<UiSettings['contrast']>;
      showInMenuBar: boolean | null;
      completionSound: boolean;
      checkAtLaunch: boolean;
      anonymousCount: boolean;
      crashReports: boolean;
    }): Promise<void> => undefined,
  ),
  startJobs: vi.fn(async (_input: StartBatchInput) => undefined),
  cancelJobs: vi.fn(async (_keepPartial?: boolean) => undefined),
  retryEngine: vi.fn(async (): Promise<void> => undefined),
  retryJobOnCpu: vi.fn(async (_jobId: string): Promise<void> => undefined),
  startBenchmark: vi.fn(async () => undefined),
  exportBenchmark: vi.fn(async () => true),
  cancelVideoComparison: vi.fn(async () => {}),
  listenVideoComparisonProgress: vi.fn(async () => () => {}),
  prepareVideoComparison: vi.fn(async () => ({
    original_url: 'asset://localhost/original.mp4',
    enhanced_url: 'asset://localhost/enhanced.mp4',
  })),
  requestImageComparison: vi.fn(
    async (_mediaId: string, _pair?: { showing: string; against: string }): Promise<void> =>
      undefined,
  ),
  listMediaResults: vi.fn(async (mediaId: string): Promise<MediaResults> => historyOf(mediaId, [])),
  listAllMediaResults: vi.fn(async (): Promise<MediaResults[]> => []),
  prepareCompareVideo: vi.fn(async () => ({
    video_url: 'asset://localhost/compare.webm',
    width: 1280,
    height: 1444,
    duration: 4,
    slots: [
      { x: 0, y: 0, width: 1280, height: 720, fps: 15, duration: 4, frames: 60, audio_url: '' },
      { x: 0, y: 724, width: 1280, height: 720, fps: 60, duration: 4, frames: 240, audio_url: '' },
    ],
  })),
  requestCompare: vi.fn(async (_picks: ComparePick[]): Promise<void> => undefined),
  startModelTrials: vi.fn(
    async (_mediaId: string, _modelIds: string[], _base: StartBatchInput): Promise<string[]> => [],
  ),
  estimateModelTrials: vi.fn(
    async (
      _mediaId: string,
      _modelIds: string[],
      _base: StartBatchInput,
    ): Promise<TrialEstimate[]> => [],
  ),
  cancelJobsForMedia: vi.fn(
    async (_mediaId: string, _keepPartial?: boolean): Promise<void> => undefined,
  ),
  refreshCapabilities: vi.fn(async () => undefined),
  probePath: vi.fn(async () => undefined),
  detectExternalFFmpeg: vi.fn(async (): Promise<string[]> => []),
  chooseExternalFFmpeg: vi.fn(async (): Promise<string | null> => null),
  openFfmpegDownloadPage: vi.fn(async () => undefined),
  openDownloadPage: vi.fn(async () => undefined),
  openCustomModelTutorial: vi.fn(async () => undefined),
  ffmpegInstallHint: vi.fn(async () => null),
  openTerminalWithInstallCommand: vi.fn(async () => undefined),
  downloadModel: vi.fn(async (_id: string, _accepted: boolean) => undefined),
  cancelDownload: vi.fn(async (_id: string) => undefined),
  removeModel: vi.fn(async () => undefined),
  importCatalogModel: vi.fn(async () => undefined),
  startEdit: vi.fn(async () => undefined),
  openEditLink: vi.fn(async () => undefined),
  chooseEditRuntime: vi.fn(async (): Promise<string | null> => null),
  chooseEditModelFile: vi.fn(
    async (
      _role: 'diffusion' | 'text_encoder' | 'vision' | 'vae',
    ): Promise<{ path: string; size_bytes: number; detected_family?: string | null } | null> =>
      null,
  ),
  saveCustomEditModel: vi.fn(async () => undefined),
  saveRecipe: vi.fn(async (_recipe: Recipe) => undefined),
  deleteRecipe: vi.fn(async () => undefined),
  updateStatus: vi.fn(async () => ({
    managed_by_store: false,
    configured: false,
    channel: 'beta',
    target: '',
    stage: 'idle',
    version: '',
    notes: '',
    size: 0,
    downloaded: 0,
    message: '',
    settings_recovery: false,
  })),
  listenForUpdates: vi.fn(async () => () => {}),
  checkUpdateAtLaunch: vi.fn(async () => ({
    managed_by_store: false,
    configured: false,
    channel: 'beta',
    target: '',
    stage: 'idle',
    version: '',
    notes: '',
    size: 0,
    downloaded: 0,
    message: '',
    settings_recovery: false,
  })),
  openModelLicense: vi.fn(async () => undefined),
  openModelSource: vi.fn(async () => undefined),
  openResult: vi.fn(async () => undefined),
  openUserGuide: vi.fn(async () => undefined),
  useResultAsInput: vi.fn(async (): Promise<string> => 'new-input'),
  revealResult: vi.fn(async () => undefined),
  diagnosticSummary: vi.fn(async () => 'LocalSR diagnostics'),
  openBugReport: vi.fn(async (_url: string) => undefined),
  openOutputDirectory: vi.fn(async () => undefined),
  takeLaunchIntents: vi.fn(async (): Promise<LaunchIntent[]> => []),
  integrationStatus: vi.fn(async () => ({
    platform: 'macos',
    installed: false,
    summary: 'Optional file-manager actions are not installed.',
    command_name: 'localsr-next',
  })),
  installIntegrations: vi.fn(),
  uninstallIntegrations: vi.fn(),
  listenForWorker: vi.fn(
    async (
      _callback: (message: { type: string; data: Record<string, unknown> }) => void,
    ): Promise<() => void> =>
      () =>
        undefined,
  ),
  listenForStateChange: vi.fn(async (_callback: () => void) => () => undefined),
  listenForNativeMenu: vi.fn(async (_handler: (action: string) => void) => () => undefined),
  listenForLaunchIntent: vi.fn(async (_handler: () => void) => () => undefined),
  listenForFileDrops: vi.fn(
    async (_handlers: { hover: (active: boolean) => void; drop: (paths: string[]) => void }) =>
      () =>
        undefined,
  ),
}));

vi.mock('./lib/api', () => api);

const planFetch = vi.hoisted(() => ({
  mock: vi.fn(),
  defaultImpl: undefined as
    | ((
        snapshot: AppSnapshot,
        settings: UiSettings,
        options?: {
          face_model_id?: string | null;
          face_enabled?: boolean;
          using_temporal_video?: boolean | null;
          selected_video_model_id?: string | null;
          edit_model_id?: string | null;
          has_audio?: boolean | null;
        },
      ) => Promise<import('./lib/plan').PlanStage[]>)
    | undefined,
}));

vi.mock('./lib/jobPlanClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./lib/jobPlanClient')>();
  const { isRestoreTask, resolvePlan } = await import('./lib/plan');
  const defaultFetchPlan: NonNullable<typeof planFetch.defaultImpl> = async (
    snapshot,
    settings,
    options = {},
  ) => {
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
    return resolvePlan(snapshot, settings, {
      faceModel,
      faceEnabled: Boolean(options.face_enabled),
      usingTemporalVideo,
      selectedVideoModel,
      editModel,
      hasAudio: options.has_audio === null ? undefined : options.has_audio,
    });
  };
  planFetch.defaultImpl = defaultFetchPlan;
  planFetch.mock.mockImplementation(defaultFetchPlan);
  return {
    ...actual,
    fetchPlan: planFetch.mock,
    createPlanClient: (fetch = planFetch.mock) => actual.createPlanClient(fetch),
  };
});

function readySnapshot(media: MediaItem[] = []): AppSnapshot {
  const snapshot = demoSnapshot();
  snapshot.media = media;
  snapshot.catalog.models.forEach((model) => (model.installed = true));
  snapshot.catalog.video_models.forEach((model) => (model.installed = true));
  snapshot.capabilities.devices = [
    {
      id: 'mps',
      type: 'mps',
      name: 'Apple GPU (Metal)',
      total_memory: 12 * 1024 ** 3,
      free_memory: 8 * 1024 ** 3,
      supports_fp16: true,
      is_integrated: true,
      recommended_tile_sizes: [64, 128, 192],
    },
    {
      id: 'cpu',
      type: 'cpu',
      name: 'CPU',
      total_memory: 16 * 1024 ** 3,
      free_memory: 8 * 1024 ** 3,
      supports_fp16: false,
      is_integrated: false,
      recommended_tile_sizes: [64, 128],
    },
  ];
  snapshot.settings.device_id = 'mps';
  snapshot.engine = {
    protocol_version: 1,
    minimum_protocol_version: 1,
    engine_id: 'localsr.test',
    engine_version: '0.0.11-alpha',
    features: [
      'image',
      'image_multi_fix',
      'video_frame',
      'video_seedvr2',
      'video_interpolation',
      'video_audio_denoise',
    ],
    model_formats: ['.safetensors'],
    video_engines: ['spandrel_image', 'seedvr2'],
  };
  snapshot.runtime = {
    ...snapshot.runtime,
    worker: 'ready',
    status_title: 'Ready',
    status_detail: 'The isolated inference engine is ready.',
  };
  snapshot.engines = {
    ...snapshot.engines!,
    supported: false,
    active_backend: 'mps',
    active_engine_id: 'bundled',
    bundled_backend: 'mps',
    installed: [{ id: 'bundled', backend: 'mps', bundled: true, active: true, path: '' }],
    recommendation: { backend: 'mps', adapter_name: null },
    catalog: [],
  };
  return snapshot;
}

function image(id: string, selected = false): MediaItem {
  return {
    id,
    path: `/private/${id}.png`,
    name: `${id}.png`,
    kind: 'image',
    width: 640,
    height: 480,
    frame_count: 1,
    fps: 0,
    duration_seconds: 0,
    preview_data_url: 'data:image/png;base64,iVBORw0KGgo=',
    probe_status: 'ready',
    error: '',
    selected,
  };
}

function video(id: string, selected = false): MediaItem {
  return {
    ...image(id, selected),
    path: `/private/${id}.mp4`,
    name: `${id}.mp4`,
    kind: 'video',
    frame_count: 24,
    fps: 24,
    duration_seconds: 1,
  };
}

async function mountWith(snapshot: AppSnapshot): Promise<ReturnType<typeof userEvent.setup>> {
  api.bootstrap.mockResolvedValue(structuredClone(snapshot));
  api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
  render(App);
  // While idle the status bar shows what Start will produce, or the running downloads.
  await waitFor(() =>
    expect(document.querySelector('.status-copy strong')?.textContent ?? '').toMatch(
      snapshot.runtime.active_job_id ||
        !(snapshot.runtime.downloads?.length || snapshot.runtime.download_model_id)
        ? /^Ready/
        : /^Downloading /,
    ),
  );
  return userEvent.setup();
}

let nativeMenu: ((action: string) => void) | undefined;
let engineStatusHandler: ((status: EngineDownloadStatus) => void) | undefined;

/** Models and Performance live in the native menu bar; tests send its actions directly. */
async function chooseMenuItem(action: string): Promise<void> {
  await waitFor(() => expect(nativeMenu).toBeTruthy());
  nativeMenu!(action);
}

async function chooseTask(user: ReturnType<typeof userEvent.setup>, name: RegExp): Promise<void> {
  await user.click(screen.getByRole('button', { name }));
}

async function openPlanSheet(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  const trigger = document.querySelector('.foot-plan') as HTMLButtonElement;
  if (trigger.getAttribute('aria-expanded') !== 'true') {
    await user.click(trigger);
  }
  return screen.getByRole('group', { name: 'Plan' });
}

async function openOutputSheet(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  const trigger = document.querySelector('.foot-output') as HTMLButtonElement;
  if (trigger.getAttribute('aria-expanded') !== 'true') {
    await user.click(trigger);
  }
  return screen.getByRole('group', { name: 'Output' });
}

/** A Mac with an editing engine, the native runtime and every Qwen bundle installed. */
function editSnapshot(unifiedGb: number): AppSnapshot {
  const snapshot = readySnapshot([image('photo', true)]);
  snapshot.capabilities.system_ram_total = unifiedGb * 1024 ** 3;
  snapshot.capabilities.devices[0].total_memory = unifiedGb * 1024 ** 3;
  snapshot.engine!.features.push('image_edit');
  snapshot.edit.runtime_path = '/engine/edit/sd-cli';
  snapshot.edit.models.forEach((model) => (model.installed = true));
  return snapshot;
}

describe('Appearance from the host', () => {
  it('applies a saved menu change while processing', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.runtime.active_job_id = 'job-running';
    await mountWith(snapshot);
    await waitFor(() => expect(api.listenForStateChange).toHaveBeenCalled());
    const changed = structuredClone(snapshot);
    changed.settings.appearance = 'light';
    changed.settings.colour_blind_palette = true;
    api.refreshSnapshot.mockResolvedValue(changed);
    (api.listenForStateChange.mock.calls[0][0] as () => void)();
    await waitFor(() =>
      expect(document.documentElement.dataset).toMatchObject({
        theme: 'light',
        palette: 'colour-blind',
      }),
    );
    expect(api.saveSettings).not.toHaveBeenCalled();
  });

  it('updates viewer background from the zoom swatch and high contrast from Settings', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Viewer background: Checkerboard' }));
    await waitFor(() =>
      expect(api.setPreference).toHaveBeenCalledWith('viewer_background', 'black'),
    );
    expect(document.documentElement.dataset.viewerBg).toBe('black');

    await chooseMenuItem('app.settings');
    const dialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(dialog).getByRole('tab', { name: 'Appearance' }));
    await user.click(within(dialog).getByRole('switch', { name: 'High contrast' }));
    await waitFor(() => expect(api.setPreference).toHaveBeenCalledWith('contrast', 'high'));
    // The theme follows once the awaited host call returns.
    await waitFor(() => expect(document.documentElement.dataset.contrast).toBe('high'));
  });

  it('shows the accent switch on macOS user agents and hides it on Windows user agents', async () => {
    const uaDesc = Object.getOwnPropertyDescriptor(navigator, 'userAgent');
    onTestFinished(() => {
      if (uaDesc) Object.defineProperty(navigator, 'userAgent', uaDesc);
      else delete (navigator as unknown as Record<string, unknown>).userAgent;
    });

    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)',
      configurable: true,
    });
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.accent = 'system';
    const user = await mountWith(snapshot);
    await chooseMenuItem('app.settings');
    const dialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(dialog).getByRole('tab', { name: 'Appearance' }));
    const accentSwitch = within(dialog).getByRole('switch', {
      name: 'Use the macOS accent colour',
    }) as HTMLInputElement;
    expect(accentSwitch.checked).toBe(true);
    await user.click(accentSwitch);
    await waitFor(() => expect(api.setPreference).toHaveBeenCalledWith('accent', 'localsr'));

    cleanup();
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
      configurable: true,
    });
    const winUser = await mountWith(readySnapshot([image('photo', true)]));
    await chooseMenuItem('app.settings');
    const winDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await winUser.click(within(winDialog).getByRole('tab', { name: 'Appearance' }));
    expect(
      within(winDialog).queryByRole('switch', { name: 'Use the macOS accent colour' }),
    ).toBeNull();
  });

  it('keeps saved contrast and viewer background during first-run theme preview', async () => {
    const snapshot = readySnapshot();
    snapshot.settings.privacy_choice_made = false;
    snapshot.settings.contrast = 'high';
    snapshot.settings.viewer_background = 'grey';
    const user = await mountWith(snapshot);
    const dialog = await screen.findByRole('dialog', { name: 'Welcome to LocalSR' });
    await user.click(within(dialog).getByRole('button', { name: 'Continue' }));
    await user.click(within(dialog).getByRole('radio', { name: 'Light' }));
    expect(document.documentElement.dataset).toMatchObject({
      theme: 'light',
      contrast: 'high',
      viewerBg: 'grey',
    });
  });
});

describe('Completion sound switch', () => {
  it('saves the chosen value from Settings', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await chooseMenuItem('app.settings');
    const dialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(
      within(dialog).getByRole('switch', { name: 'Play a sound with the notification' }),
    );
    expect(api.setPreference).toHaveBeenCalledWith('completion_sound', true);
  });
});

describe('Update switches', () => {
  it('go to the host on their own, even during a job, and never through a full save', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.runtime.active_job_id = 'job-running';
    // A direct build with its own update feed shows the switches; no launch check runs.
    snapshot.settings.check_updates_at_launch = false;
    const unconfigured = api.updateStatus.getMockImplementation();
    onTestFinished(() => {
      if (unconfigured) api.updateStatus.mockImplementation(unconfigured);
    });
    api.updateStatus.mockResolvedValue({
      managed_by_store: false,
      configured: true,
      channel: 'beta',
      target: 'darwin-aarch64-mps-native',
      stage: 'idle',
      version: '',
      notes: '',
      size: 0,
      downloaded: 0,
      message: '',
      settings_recovery: false,
    });
    const user = await mountWith(snapshot);
    await chooseMenuItem('app.settings');
    const dialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(dialog).getByRole('tab', { name: 'Privacy & updates' }));
    await user.click(
      await within(dialog).findByRole('switch', {
        name: 'Send an anonymous count with each update check',
      }),
    );
    await user.click(
      within(dialog).getByRole('switch', { name: 'Check for updates when LocalSR starts' }),
    );
    expect(api.setPreference.mock.calls).toEqual([
      ['anonymous_update_count', true], // off until the user turns it on
      ['check_updates_at_launch', true], // it started off in this test
    ]);
    expect(api.saveSettings).not.toHaveBeenCalledWith(
      expect.objectContaining({ anonymous_update_count: true }),
    );
  });
});

describe('Edit task', () => {
  it('sits beside Upscale & restore, takes a prompt and sends one editing job', async () => {
    // 36 GB: the Qwen editor fits within the share of memory a default may use.
    const user = await mountWith(editSnapshot(36));
    await chooseTask(user, /^Edit$/);
    expect(api.saveSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ task: 'edit', edit_model_id: 'qwen_edit_2511_q3_k_s' }),
    );
    const stages = screen.getByRole('list', { name: 'Stages' });
    expect(within(stages).getByText('Qwen Image Edit 2511')).toBeTruthy();
    expect(within(stages).getByText('Compact')).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'Quality' })).toBeNull();
    const start = screen.getByRole('button', { name: 'Edit selected' }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    await user.type(screen.getByLabelText('What to change'), 'Make the sky dramatic');
    expect(start.disabled).toBe(false);
    await user.click(start);
    expect(api.startEdit).toHaveBeenCalledWith({
      media_id: 'photo',
      model_id: 'qwen_edit_2511_q3_k_s',
      prompt: 'Make the sky dramatic',
      runtime_path: '/engine/edit/sd-cli',
      device: 'mps',
      max_dimension: 512,
      steps: 40,
      seed: 42,
      accepted_terms: false,
      output_directory: '',
    });
    expect(api.startJobs).not.toHaveBeenCalled();
  });

  it('downloads a missing bundle from the footer before the first edit', async () => {
    const snapshot = editSnapshot(36);
    snapshot.edit.models.forEach((model) => (model.installed = false));
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Edit$/);
    await user.click(screen.getByRole('button', { name: /^Download [\d.]+ GB, then edit$/ }));
    expect(api.downloadModel).toHaveBeenCalledWith('qwen_edit_2511_q3_k_s', false);
    expect(api.startEdit).not.toHaveBeenCalled();
  });

  it('starts an edit with the runtime the host dialog returned when the build has none', async () => {
    const snapshot = editSnapshot(16);
    snapshot.edit.runtime_path = '';
    api.chooseEditRuntime.mockResolvedValueOnce('/chosen/sd-cli');
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Edit$/);
    await user.click(screen.getByRole('button', { name: 'Choose runtime…' }));
    expect(api.chooseEditRuntime).toHaveBeenCalledTimes(1);
    await user.type(screen.getByLabelText('What to change'), 'Add snow');
    await user.click(screen.getByRole('button', { name: 'Edit selected' }));
    expect(api.startEdit).toHaveBeenCalledWith(
      expect.objectContaining({ runtime_path: '/chosen/sd-cli' }),
    );
  });

  it('starts a 16 GB Mac on FLUX.2 klein Q4_0, with its own step count', async () => {
    const user = await mountWith(editSnapshot(16));
    await chooseTask(user, /^Edit$/);
    expect(api.saveSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({
        task: 'edit',
        edit_model_id: 'flux2_klein_4b_q4_0',
        edit_steps: 0,
      }),
    );
    const stages = screen.getByRole('list', { name: 'Stages' });
    expect(within(stages).getByText('FLUX.2 klein 4B')).toBeTruthy();
    expect(within(stages).getByText('Balanced')).toBeTruthy();
    await user.type(screen.getByLabelText('What to change'), 'Add snow');
    await user.click(screen.getByRole('button', { name: 'Edit selected' }));
    expect(api.startEdit).toHaveBeenCalledWith(
      expect.objectContaining({ model_id: 'flux2_klein_4b_q4_0', steps: 4, max_dimension: 512 }),
    );
  });

  it('says plainly when no bundle fits this computer', async () => {
    const user = await mountWith(editSnapshot(8));
    await chooseTask(user, /^Edit$/);
    await user.type(screen.getByLabelText('What to change'), 'Add snow');
    expect(screen.getByRole('status').textContent?.replace(/\s+/g, ' ')).toContain(
      'FLUX.2 klein 4B · Q4_0 needs 16 GB of unified memory and this Mac has 8 GB of unified memory. No listed model fits this computer yet.',
    );
    expect(
      (screen.getByRole('button', { name: 'Edit selected' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('asks for license acceptance in the library before fetching a restricted bundle', async () => {
    const snapshot = editSnapshot(26);
    snapshot.edit.models.forEach((model) => (model.installed = false));
    const restricted = snapshot.edit.models.find(
      (model) => model.model_id === 'flux2_klein_4b_q8_0',
    )!;
    restricted.terms_acceptance_required = true;
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Edit$/);
    await user.click(screen.getByRole('button', { name: 'Change the edit model' }));
    const dialog = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(dialog).getByText('Choosing for Edit')).toBeTruthy();
    expect(within(dialog).getByText('FLUX.2 klein 4B')).toBeTruthy();
    await user.click(
      within(dialog).getByRole('button', { name: /^Full detail\s*Q8_0\s*Needs 16 GB of memory/ }),
    );
    expect(within(dialog).getByText('Research license · review the terms')).toBeTruthy();
    const download = within(dialog).getByRole('button', {
      name: /^Download [\d.]+ GB$/,
    }) as HTMLButtonElement;
    expect(download.disabled).toBe(true);
    await user.click(within(dialog).getByRole('checkbox'));
    expect(download.disabled).toBe(false);
    await user.click(download);
    expect(api.downloadModel).toHaveBeenCalledWith('flux2_klein_4b_q8_0', true);
    await user.click(within(dialog).getByRole('button', { name: 'Use for this job' }));
    expect(api.saveSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ edit_model_id: 'flux2_klein_4b_q8_0' }),
    );
    expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull();
  });

  it('offers no Faces group in the model library', async () => {
    await mountWith(readySnapshot([image('photo', true)]));
    await chooseMenuItem('view.models');
    const dialog = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(dialog).getByRole('button', { name: /^Edit with a prompt/ })).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: /^Faces/ })).toBeNull();
  });

  it('shows Mac and Windows/Linux memory needs in the library and removes a bundle', async () => {
    const user = await mountWith(editSnapshot(26));
    await chooseMenuItem('view.models');
    const dialog = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(dialog).getByRole('button', { name: /^Edit with a prompt/ }));
    // Sizes that need more memory than this Mac has wait behind one link.
    expect(
      within(dialog).queryByRole('button', {
        name: /^Full detail\s*Q8_0\s*Needs a Mac with 48 GB/,
      }),
    ).toBeNull();
    await user.click(
      within(dialog).getByRole('button', { name: /^Show \d+ more that need more memory/ }),
    );
    const heavy = within(dialog).getByRole('button', {
      name: /^Full detail\s*Q8_0\s*Needs a Mac with 48 GB/,
    });
    // The row can still be chosen to read about it, so it is not dimmed like a disabled one.
    expect(heavy.classList.contains('dimmed')).toBe(false);
    await user.click(heavy);
    expect(within(dialog).getByText('48 GB memory')).toBeTruthy();
    await user.click(within(dialog).getByRole('button', { name: 'Remove from this computer' }));
    expect(api.removeModel).toHaveBeenCalledWith('qwen_edit_2511_q8_0');
  });

  it('lets the user configure, check and save their own edit model with family roles and defaults', async () => {
    const snapshot = editSnapshot(32);
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Edit$/);
    await user.click(screen.getByRole('button', { name: 'Change the edit model' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: 'Use your own edit model…' }));

    const modal = screen.getByRole('dialog', { name: 'Use your own edit model' });
    expect(within(modal).getByText(/responsible for the model's licence/i)).toBeTruthy();
    expect(within(modal).queryByText(/^Defaults:/)).toBeNull();

    // Switching to FLUX.2 klein hides Vision projector and updates the Model family HelpTip to 4 steps / CFG 1.
    const familySelect = within(modal).getByLabelText('Model family') as HTMLSelectElement;
    await user.selectOptions(familySelect, 'flux2-klein');
    expect(within(modal).queryByText('Vision projector')).toBeNull();
    expect(within(modal).queryByText(/^Defaults:/)).toBeNull();
    const familyTipButton = within(modal).getByRole('button', { name: 'About Model family' });
    await user.click(familyTipButton);
    expect(within(modal).getByRole('tooltip').textContent).toBe(
      'Tells LocalSR how to run your files and sets the defaults. For this family: 4 steps, CFG 1.',
    );

    // Picking a Qwen 2.1 GGUF auto-detects the family and shows Vision projector + CFG 6 in the HelpTip.
    api.chooseEditModelFile
      .mockResolvedValueOnce({
        path: '/local/qwen21.gguf',
        size_bytes: 12 * 1024 ** 3,
        detected_family: 'qwen-image-2.1',
      })
      .mockResolvedValueOnce({ path: '/local/te.gguf', size_bytes: 4 * 1024 ** 3 })
      .mockResolvedValueOnce({ path: '/local/mmproj.gguf', size_bytes: 1024 ** 3 })
      .mockResolvedValueOnce({ path: '/local/vae.safetensors', size_bytes: 256 * 1024 ** 2 });

    const chooseButtons = () => within(modal).getAllByRole('button', { name: 'Choose…' });
    await user.click(chooseButtons()[0]);
    expect(familySelect.value).toBe('qwen-image-2.1');
    expect(within(modal).getByText('Detected Qwen Image 2.1 from GGUF metadata.')).toBeTruthy();
    expect(within(modal).getByText('Vision projector')).toBeTruthy();
    await user.click(familyTipButton);
    expect(within(modal).getByRole('tooltip').textContent).toBe(
      'Tells LocalSR how to run your files and sets the defaults. For this family: 40 steps, CFG 6.',
    );

    // Pick text encoder, vision projector, and VAE.
    for (let i = 0; i < 3; i += 1) {
      await user.click(within(modal).getAllByRole('button', { name: 'Choose…' })[0]);
    }

    // A failed sd-cli load check keeps the dialog open and refuses with a clear error.
    api.saveCustomEditModel.mockRejectedValueOnce(
      'sd-cli could not load these model files: invalid VAE',
    );
    await user.click(within(modal).getByRole('button', { name: 'Check and save' }));
    expect(within(modal).getByRole('alert').textContent).toContain('invalid VAE');
    expect(screen.getByRole('dialog', { name: 'Use your own edit model' })).toBeTruthy();

    // A passing load check saves, refreshes the snapshot, and selects the custom edit model.
    const savedSnapshot = structuredClone(snapshot);
    savedSnapshot.settings.task = 'edit';
    savedSnapshot.settings.edit_model_id = '__custom__';
    savedSnapshot.settings.edit_steps = 0;
    savedSnapshot.settings.custom_edit_model = {
      family: 'qwen-image-2.1',
      diffusion_path: '/local/qwen21.gguf',
      text_encoder_path: '/local/te.gguf',
      vision_path: '/local/mmproj.gguf',
      vae_path: '/local/vae.safetensors',
    };
    savedSnapshot.edit.custom_model = {
      model_id: '__custom__',
      name: 'Your edit model',
      family: 'qwen-image-2.1',
      quantization: 'Custom',
      files: [
        {
          role: 'diffusion',
          filename: 'diffusion',
          size_bytes: 12 * 1024 ** 3,
          sha256: '',
          download_url: '',
        },
        {
          role: 'text_encoder',
          filename: 'text_encoder',
          size_bytes: 4 * 1024 ** 3,
          sha256: '',
          download_url: '',
        },
        { role: 'vision', filename: 'vision', size_bytes: 1024 ** 3, sha256: '', download_url: '' },
        { role: 'vae', filename: 'vae', size_bytes: 256 * 1024 ** 2, sha256: '', download_url: '' },
      ],
      min_unified_memory_gb: 32,
      min_vram_gb: 16,
      default_steps: 40,
      cfg_scale: 6,
      license_name: 'Your own file',
      license_url: '',
      source_url: '',
      terms_acceptance_required: false,
      automated_download_allowed: false,
      installed: true,
      total_size_bytes: 17 * 1024 ** 3 + 256 * 1024 ** 2,
    };
    api.saveCustomEditModel.mockResolvedValueOnce(undefined);
    api.refreshSnapshot.mockResolvedValue(savedSnapshot);

    await user.click(within(modal).getByRole('button', { name: 'Check and save' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Use your own edit model' })).toBeNull(),
    );
    expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull();
    const stages = screen.getByRole('list', { name: 'Stages' });
    expect(within(stages).getByText('Qwen Image 2.1')).toBeTruthy();
    expect(within(stages).getByText('Custom')).toBeTruthy();
  });
});

it.each(['realplksr_nomoswebphoto_x4', 'realplksr_hfa2k_anime_x4'])(
  'downloads %s using its declared license and the selected model identity',
  async (modelId) => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.selected_model_id = modelId;
    snapshot.catalog.models.find((model) => model.model_id === modelId)!.installed = false;
    const user = await mountWith(snapshot);
    expect(screen.getByRole('note', { name: 'Model license' }).textContent).toContain('CC BY 4.0');
    await user.click(screen.getByRole('button', { name: /Model source/ }));
    await user.click(screen.getByRole('button', { name: /License terms/ }));
    expect(api.openModelSource).toHaveBeenCalledWith(modelId);
    expect(api.openModelLicense).toHaveBeenCalledWith(modelId);
    // One download control: the Start button.
    expect(screen.getAllByRole('button', { name: /^Download / })).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: /^Download [\d.]+ MB, then upscale$/ }));
    await waitFor(() => expect(api.downloadModel).toHaveBeenCalledWith(modelId, false));
    expect(api.importCatalogModel).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
  },
);

beforeEach(() => {
  vi.clearAllMocks();
  api.bootstrap.mockReset();
  api.refreshSnapshot.mockReset();
  if (planFetch.defaultImpl) planFetch.mock.mockImplementation(planFetch.defaultImpl);
  api.isTauri.mockReturnValue(true);
  api.addMedia.mockResolvedValue(undefined);
  api.selectMedia.mockResolvedValue(undefined);
  api.saveSettings.mockResolvedValue(undefined);
  api.checkpointConsent.mockReset().mockResolvedValue(false);
  api.startJobs.mockResolvedValue(undefined);
  api.startBenchmark.mockReset().mockResolvedValue(undefined);
  api.downloadModel.mockReset().mockResolvedValue(undefined);
  api.listenForWorker.mockResolvedValue(() => undefined);
  api.listenForStateChange.mockResolvedValue(() => undefined);
  nativeMenu = undefined;
  engineStatusHandler = undefined;
  api.listenForNativeMenu.mockImplementation(async (handler) => {
    nativeMenu = handler;
    return () => undefined;
  });
  api.listenForEngineStatus.mockImplementation(
    async (handler: (status: EngineDownloadStatus) => void) => {
      engineStatusHandler = handler;
      return () => undefined;
    },
  );
  api.refreshEngineCatalog.mockReset().mockResolvedValue([]);
  api.installEngine.mockReset().mockResolvedValue(undefined);
  api.cancelEngineInstall.mockReset().mockResolvedValue(undefined);
  api.useEngine.mockReset().mockResolvedValue(undefined);
  api.removeEngine.mockReset().mockResolvedValue(undefined);
  api.chooseEngineManifest.mockReset().mockResolvedValue(null);
  api.importEngine.mockReset().mockResolvedValue(undefined);
  api.listenForLaunchIntent.mockResolvedValue(() => undefined);
  api.listenForFileDrops.mockResolvedValue(() => undefined);
  api.takeLaunchIntents.mockResolvedValue([]);
  api.chooseMediaFolder.mockReset().mockResolvedValue(null);
  api.requestImageComparison.mockReset().mockResolvedValue(undefined);
  api.listMediaResults.mockReset().mockImplementation(async (id: string) => historyOf(id, []));
  api.listAllMediaResults.mockReset().mockResolvedValue([]);
  api.requestCompare.mockReset().mockResolvedValue(undefined);
  api.startModelTrials.mockReset().mockResolvedValue([]);
  api.estimateModelTrials.mockReset().mockResolvedValue([]);
  api.cancelJobsForMedia.mockReset().mockResolvedValue(undefined);
  api.retryEngine.mockReset().mockResolvedValue(undefined);
  api.retryJobOnCpu.mockReset().mockResolvedValue(undefined);
  api.listCrashReports.mockReset().mockResolvedValue([]);
  api.deleteCrashReport.mockReset().mockResolvedValue(undefined);
  api.deleteAllCrashReports.mockReset().mockResolvedValue(undefined);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    clearRect: vi.fn(),
    drawImage: vi.fn(),
    fillRect: vi.fn(),
    fillStyle: '',
  } as unknown as CanvasRenderingContext2D);
});

afterEach(() => {
  updateState.set(emptyUpdateStatus);
  updateDialogOpen.set(false); // the dialog's store outlives each test's window
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('Job activity', () => {
  const mountRunning = async (snapshot: AppSnapshot) => {
    api.bootstrap.mockResolvedValue(structuredClone(snapshot));
    api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    render(App);
    await screen.findByRole('button', { name: 'Cancel' });
    await waitFor(() =>
      expect(document.querySelector('.status-copy strong')?.textContent).toMatch(/^Editing/),
    );
  };
  const running = () => {
    const snapshot = editSnapshot(26);
    snapshot.settings.task = 'edit';
    snapshot.settings.edit_model_id = 'flux2_klein_4b_q4_0';
    snapshot.runtime.active_job_id = 'job-1';
    snapshot.runtime.status_title = 'Editing';
    snapshot.runtime.status_detail = 'Loading the model';
    snapshot.runtime.progress = 5;
    snapshot.jobs = [
      {
        id: 'job-1',
        media_id: 'photo',
        media_name: 'photo.png',
        media_kind: 'image',
        status: 'running',
        progress: 5,
        output_path: '',
        error: '',
        created_at: 1,
      },
    ];
    return snapshot;
  };

  it('has one progress display for a running edit and names the model in the status bar', async () => {
    await mountRunning(running());
    expect(document.querySelector('.model-activity')).toBeNull();
    const bars = screen.getAllByRole('progressbar').filter((bar) => !bar.closest('.media-list'));
    expect(bars).toHaveLength(1);
    expect(bars[0].getAttribute('aria-valuenow')).toBe('5');
    const bar = document.querySelector('.status-bar')!;
    expect(bar.querySelector('strong')?.textContent).toBe('Editing · 5%');
    const detail = bar.querySelector('.status-detail')!;
    expect(detail.textContent).toMatch(/^FLUX\.2 klein 4B.* · Loading the model$/);
    expect(detail.getAttribute('title')).toBe(detail.textContent);
    expect(detail.getAttribute('role')).toBe('status');
    expect(screen.getAllByRole('status').filter((node) => bar.contains(node))).toHaveLength(1);
  });

  it('says Saving the result from 100 % on, with the model and no percentage or time left', async () => {
    const snapshot = running();
    snapshot.runtime.progress = 100;
    snapshot.runtime.status_title = 'Editing';
    snapshot.runtime.status_detail = 'Step 4 of 4 · ETA ≈ 3s';
    snapshot.runtime.estimated_remaining_seconds = 3;
    snapshot.jobs[0].progress = 100;
    api.bootstrap.mockResolvedValue(structuredClone(snapshot));
    api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    render(App);
    await screen.findByRole('button', { name: 'Cancel' });
    await waitFor(() =>
      expect(document.querySelector('.status-copy strong')?.textContent).toBe('Saving the result'),
    );
    const bar = document.querySelector('.status-bar')!;
    expect(bar.querySelector('.status-detail')?.textContent).toMatch(/^FLUX\.2 klein 4B/);
    expect(bar.textContent).not.toMatch(/ETA|≈|100%/);
    expect(document.querySelector('.media-pane')!.textContent).toContain('Saving…');
    expect(document.querySelector('.media-pane')!.textContent).not.toContain('100 %');
  });

  it('offers no Add to queue button while the running picture is the selected one', async () => {
    await mountRunning(running());
    expect(screen.queryByRole('button', { name: /to queue$/ })).toBeNull();
  });
});

describe('Cancel buttons while work runs', () => {
  const job = (n: number, status: JobRecord['status']): JobRecord => ({
    id: `job-${n}`,
    media_id: `p${n}`,
    media_name: `p${n}.png`,
    media_kind: 'image',
    status,
    progress: 0,
    output_path: '',
    error: '',
    created_at: 1_000,
  });
  const mount = async (jobs: JobRecord[], lanes: string[] = []) => {
    const snapshot = readySnapshot([1, 2, 3, 4].map((n) => image(`p${n}`, n === 1)));
    snapshot.settings.task = 'upscale';
    snapshot.runtime.active_job_id = 'job-1';
    snapshot.runtime.active_batch_job_ids = lanes;
    snapshot.jobs = jobs;
    api.bootstrap.mockResolvedValue(structuredClone(snapshot));
    api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    render(App);
    await screen.findAllByRole('button', { name: /^Cancel/ });
  };
  const cancels = () =>
    [...document.querySelectorAll('.status-actions .button')]
      .map((button) => button.textContent)
      .filter((text) => /^Cancel/.test(text ?? ''));

  it('says Cancel for the one job, with no queue and no per-image button', async () => {
    await mount([job(1, 'running')]);
    expect(cancels()).toEqual(['Cancel']);
  });

  it('says Cancel queue when jobs wait behind the running one', async () => {
    await mount([job(1, 'running'), job(2, 'queued'), job(3, 'queued'), job(4, 'queued')]);
    expect(screen.getByRole('button', { name: 'Cancel queue' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
  });

  it('says Cancel queue for two lanes with nothing queued', async () => {
    await mount([job(1, 'running'), job(2, 'running')], ['job-1', 'job-2']);
    expect(screen.getByRole('button', { name: 'Cancel queue' })).toBeTruthy();
  });

  it('changes to Cancel when the queue drains to its last job', async () => {
    const current = readySnapshot([image('p1', true), image('p2')]);
    current.settings.task = 'upscale';
    current.runtime.active_job_id = 'job-2';
    current.jobs = [job(2, 'running'), job(1, 'completed')];
    current.jobs.push(job(3, 'queued'));
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    render(App);
    await screen.findByRole('button', { name: 'Cancel queue' });
    current.jobs = current.jobs.filter((entry) => entry.id !== 'job-3');
    await waitFor(() => expect(api.listenForStateChange).toHaveBeenCalled());
    (api.listenForStateChange.mock.calls[0][0] as () => void)();
    await screen.findByRole('button', { name: 'Cancel' });
    expect(screen.queryByRole('button', { name: 'Cancel queue' })).toBeNull();
  });
});

describe('LocalSR desktop interface', () => {
  it('puts an imported folder batch in a LocalSR Results subfolder', async () => {
    const snapshot = readySnapshot();
    const user = await mountWith(snapshot);
    api.chooseMediaFolder.mockResolvedValueOnce({
      paths: ['/Scans/a.png', '/Scans/b.png'],
      outputDirectory: '/Scans/LocalSR Results',
    });
    await user.click(screen.getByRole('button', { name: 'Add Folder…' }));
    await waitFor(() =>
      expect(api.addMedia).toHaveBeenCalledWith(['/Scans/a.png', '/Scans/b.png'], false),
    );
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({ batch_mode: true, output_directory: '/Scans/LocalSR Results' }),
      ),
    );
  });

  it('keeps the current destination when the folder picker is cancelled', async () => {
    const snapshot = readySnapshot();
    const user = await mountWith(snapshot);
    api.saveSettings.mockClear();
    await user.click(screen.getByRole('button', { name: 'Add Folder…' }));
    expect(api.addMedia).not.toHaveBeenCalled();
    expect(api.saveSettings).not.toHaveBeenCalled();
  });

  it('loads an earlier batch result and keeps its slider while a different image runs', async () => {
    const snapshot = readySnapshot([image('first', true), image('second')]);
    snapshot.jobs = [
      {
        id: 'later-job',
        media_id: 'second',
        media_name: 'second.png',
        media_kind: 'image',
        status: 'running',
        progress: 20,
        output_path: '',
        error: '',
        created_at: 2,
      },
      {
        id: 'earlier-job',
        media_id: 'first',
        media_name: 'first.png',
        media_kind: 'image',
        status: 'completed',
        progress: 100,
        output_path: '/results/first-denoised.png',
        error: '',
        created_at: 1,
      },
    ];
    snapshot.runtime.active_job_id = 'later-job';
    api.requestImageComparison.mockImplementationOnce(async () => {
      snapshot.runtime.comparison_media_id = 'first';
      snapshot.runtime.comparison_output_path = '/results/first-denoised.png';
      snapshot.runtime.comparison_preview_data_url = 'data:image/jpeg;base64,denoised-first';
      api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    });
    await mountWith(snapshot);
    await waitFor(() => expect(api.requestImageComparison).toHaveBeenCalledWith('first'));
    const slider = await screen.findByRole('slider', { name: 'Before and after comparison' });
    await fireEvent.keyDown(slider, { key: 'End' });
    expect(slider.getAttribute('aria-valuenow')).toBe('100');
    expect(document.querySelector('.result-image')?.getAttribute('src')).toContain(
      'denoised-first',
    );
  });

  it('does not register native listeners if bootstrap finishes after the window closes', async () => {
    let finishBootstrap!: (snapshot: AppSnapshot) => void;
    api.bootstrap.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishBootstrap = resolve;
        }),
    );
    const app = render(App);
    app.unmount();
    finishBootstrap(readySnapshot());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(api.listenForWorker).not.toHaveBeenCalled();
    expect(api.listenForStateChange).not.toHaveBeenCalled();
    expect(api.takeLaunchIntents).not.toHaveBeenCalled();
  });

  it('releases a native listener that finishes registering after the window closes', async () => {
    let finishRegistration!: (unlisten: () => void) => void;
    const unlisten = vi.fn();
    api.bootstrap.mockResolvedValue(readySnapshot());
    api.listenForWorker.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishRegistration = resolve;
        }),
    );
    const app = render(App);
    await waitFor(() => expect(api.listenForWorker).toHaveBeenCalledOnce());
    app.unmount();
    finishRegistration(unlisten);
    await waitFor(() => expect(unlisten).toHaveBeenCalledOnce());
    expect(api.listenForStateChange).not.toHaveBeenCalled();
    expect(api.listenForNativeMenu).not.toHaveBeenCalled();
    expect(api.listenForLaunchIntent).not.toHaveBeenCalled();
    expect(api.listenForFileDrops).not.toHaveBeenCalled();
  });

  it('finishes booting and registers file drops and launch intents without awaiting the launch update check', async () => {
    const originalStatus = api.updateStatus.getMockImplementation();
    onTestFinished(() => {
      if (originalStatus) api.updateStatus.mockImplementation(originalStatus);
    });
    api.updateStatus.mockResolvedValue({
      managed_by_store: false,
      configured: true,
      channel: 'beta',
      target: 'darwin-aarch64-mps-native',
      stage: 'idle',
      version: '',
      notes: '',
      size: 0,
      downloaded: 0,
      message: '',
      settings_recovery: false,
    });
    api.checkUpdateAtLaunch.mockImplementationOnce(() => new Promise(() => undefined));
    api.bootstrap.mockResolvedValue(readySnapshot());
    render(App);
    await waitFor(() => {
      expect(document.querySelector('.status-copy strong')?.textContent ?? '').toMatch(/^Ready/);
      expect(api.listenForFileDrops).toHaveBeenCalledOnce();
      expect(api.takeLaunchIntents).toHaveBeenCalledOnce();
    });
    expect(api.checkUpdateAtLaunch).toHaveBeenCalledWith('beta');
  });

  it('mounts the complete three-pane workspace after native bootstrap', async () => {
    await mountWith(readySnapshot());

    expect(screen.getByRole('tablist', { name: 'Media' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Enhance' })).toBeTruthy();
    expect(screen.getByText('Drop photos or videos here')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Add Files…' })).toHaveLength(1);
    expect(
      screen.getByRole('button', { name: /drop them here or click to choose files/ }),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Add Media…' })).toBeNull();
    expect(document.querySelector('.empty-stage .shot canvas')).toBeTruthy();
  });

  it('heads the Media pane with its Inputs and Outputs tabs, each with its count', async () => {
    api.listAllMediaResults.mockResolvedValue([
      {
        media_id: 'photo',
        results: [],
        total_count: 1,
        completed_count: 1,
        available_count: 1,
        default_result_id: 'done',
        next_before: null,
      },
    ]);
    const snapshot = readySnapshot([image('photo', true), image('other')]);
    snapshot.jobs = [
      {
        id: 'done',
        media_id: 'photo',
        media_name: 'photo.png',
        media_kind: 'image',
        status: 'completed',
        progress: 100,
        output_path: '/out/done.png',
        error: '',
        created_at: 1,
      },
    ];
    await mountWith(snapshot);
    const heading = document.querySelector<HTMLElement>('.media-pane .pane-heading')!;
    const tabs = within(heading).getByRole('tablist', { name: 'Media' });
    expect(within(tabs).getByRole('tab', { name: 'Inputs 2' })).toBeTruthy();
    expect(within(heading).getByRole('button', { name: 'Add Media…' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Media' })).toBeNull();
    expect(screen.queryByText('2 inputs')).toBeNull();
    cleanup();

    await mountWith(readySnapshot());
    expect(
      within(screen.getByRole('tablist', { name: 'Media' }))
        .getAllByRole('tab')
        .map((tab) => tab.textContent?.trim()),
    ).toEqual(['Inputs', 'Outputs']);
  });

  it('names the narrow window pages Media, Viewer and Enhance, apart from the Preview tab', async () => {
    await mountWith(readySnapshot([image('photo', true)]));
    const pages = within(screen.getByRole('navigation', { name: 'Workspace' }));
    expect(pages.getAllByRole('button').map((button) => button.textContent?.trim())).toEqual([
      'Media 1',
      'Viewer',
      'Enhance',
    ]);
    expect(pages.queryByRole('button', { name: 'Preview' })).toBeNull();
  });

  it('makes the real benchmark discoverable and starts it across all devices from the View menu', async () => {
    const user = await mountWith(readySnapshot());

    await chooseMenuItem('view.performance');

    expect(await screen.findByRole('heading', { name: 'Performance & Diagnostics' })).toBeTruthy();
    expect(screen.getByText('Apple GPU (Metal)')).toBeTruthy();
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Run Benchmark' }),
    );
    await waitFor(() => expect(api.startBenchmark).toHaveBeenCalledTimes(1));
    expect(api.downloadModel).not.toHaveBeenCalled();
  });

  it('notes missing preset models and never triggers a download from the benchmark panel', async () => {
    const snapshot = readySnapshot();
    snapshot.catalog.models.find(
      (model) => model.model_id === 'realplksr_nomoswebphoto_x4',
    )!.installed = false;
    const user = await mountWith(snapshot);
    await chooseMenuItem('view.performance');
    const dialog = within(screen.getByRole('dialog'));
    expect(
      dialog.getByText(/Missing preset model:.*Only downloaded models take part/i),
    ).toBeTruthy();
    expect(dialog.queryByRole('button', { name: /Download/i })).toBeNull();
    await user.click(dialog.getByRole('button', { name: 'Run Benchmark' }));
    await waitFor(() => expect(api.startBenchmark).toHaveBeenCalledTimes(1));
    expect(api.downloadModel).not.toHaveBeenCalled();
  });

  it('blocks benchmark start when neither preset model is downloaded without offering downloads', async () => {
    const snapshot = readySnapshot();
    snapshot.catalog.models.find((model) => model.model_id === 'localsr_quick_x4_v1')!.installed =
      false;
    snapshot.catalog.models.find(
      (model) => model.model_id === 'realplksr_nomoswebphoto_x4',
    )!.installed = false;
    await mountWith(snapshot);
    await chooseMenuItem('view.performance');
    const dialog = within(screen.getByRole('dialog'));
    const runBtn = dialog.getByRole('button', { name: 'Run Benchmark' }) as HTMLButtonElement;
    expect(runBtn.disabled).toBe(true);
    expect(
      dialog.getByText(/Missing preset models:.*Download at least one preset model/i),
    ).toBeTruthy();
    expect(dialog.queryByRole('button', { name: /Download/i })).toBeNull();
    expect(api.downloadModel).not.toHaveBeenCalled();
  });

  it('shows one title and one progress bar for all model downloads', async () => {
    const snapshot = demoSnapshot();
    const [first, second] = snapshot.catalog.models;
    snapshot.runtime.downloads = [
      {
        model_id: first.model_id,
        status: 'downloading',
        progress: 50,
        downloaded_bytes: first.size_bytes / 2,
        total_bytes: first.size_bytes,
      },
      { model_id: second.model_id, status: 'queued', progress: 0 },
    ];
    await mountWith(snapshot);
    const expected = Math.round(
      (first.size_bytes / 2 / (first.size_bytes + second.size_bytes)) * 100,
    );
    expect(document.querySelector('.status-copy strong')?.textContent).toBe(
      `Downloading 2 models · ${expected}%`,
    );
    expect(document.querySelector('.status-detail')?.textContent).toBe(
      `${first.name}, ${second.name}`,
    );
    const bar = screen.getByRole('progressbar', { name: 'Download progress' });
    expect(bar.getAttribute('aria-valuenow')).toBe(String(expected));
  });

  it('explains why benchmark startup waits for another model download', async () => {
    const snapshot = readySnapshot();
    snapshot.runtime.download_model_id = 'seedvr2_3b_fp8';
    const user = await mountWith(snapshot);
    await chooseMenuItem('view.performance');
    const dialog = within(screen.getByRole('dialog'));
    expect(
      dialog.getByText('Wait for the current model download to finish before benchmarking.'),
    ).toBeTruthy();
    await user.click(dialog.getByRole('button', { name: 'Run Benchmark' }));
    expect(api.startBenchmark).not.toHaveBeenCalled();
    expect(api.downloadModel).not.toHaveBeenCalled();
    expect(api.cancelDownload).not.toHaveBeenCalled();
  });

  it('keeps benchmark launch errors visible beside a retryable button', async () => {
    api.startBenchmark.mockRejectedValueOnce(new Error('selected device unavailable'));
    const user = await mountWith(readySnapshot());
    await chooseMenuItem('view.performance');
    const dialog = within(screen.getByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Run Benchmark' }));
    expect((await dialog.findByRole('alert')).textContent).toContain('selected device unavailable');
    expect(
      (dialog.getByRole('button', { name: 'Run Benchmark' }) as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(api.downloadModel).not.toHaveBeenCalled();
  });

  it('shows benchmark progress and keeps local results copyable and exportable', async () => {
    const user = await mountWith(readySnapshot());
    vi.stubGlobal('navigator', {
      ...navigator,
      clipboard: { writeText: vi.fn(async () => undefined) },
    });
    await chooseMenuItem('view.performance');
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;

    callback({
      type: 'benchmark_started',
      data: { job_id: 'benchmark-1', warmup_count: 1, measured_frame_count: 5 },
    });
    expect(
      await within(screen.getByRole('dialog')).findByText(
        'Warming up 1 iteration · then measuring 5 frames',
      ),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Cancel Benchmark' })).toBeTruthy();

    callback({
      type: 'benchmark_progress',
      data: { job_id: 'benchmark-1', completed_frames: 3, total_frames: 5, percentage: 60 },
    });
    expect(
      await within(screen.getByRole('dialog')).findByText('Measured 3 of 5 frames'),
    ).toBeTruthy();

    callback({
      type: 'benchmark_completed',
      data: {
        job_id: 'benchmark-1',
        result: {
          workload_version: 'localsr-benchmark-v1',
          backend: 'mps',
          device: 'mps',
          model_id: 'span_photo_x4',
          model_name: 'SPAN Quick',
          scale: 4,
          input_width: 128,
          input_height: 128,
          warmup_count: 1,
          measured_frame_count: 5,
          median_inference_ms: 18.81,
          p95_inference_ms: 19.044,
          end_to_end_fps: 53.0308,
          processed_megapixels_per_second: 0.8689,
          total_elapsed_seconds: 0.0943,
          peak_memory_bytes: 448413696,
          score: 868.86,
        },
      },
    });
    expect(await screen.findByText('868.86')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Copy JSON' }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining('"workload_version": "localsr-benchmark-v1"'),
    );
    await user.click(screen.getByRole('button', { name: 'Export JSON…' }));
    expect(api.exportBenchmark).toHaveBeenCalledTimes(1);
  });

  it('makes the v2 score, confidence, reference, and device results visually prominent', async () => {
    const snapshot = readySnapshot();
    snapshot.latest_benchmark = {
      workload_version: 'localsr-benchmark-v2',
      backend: 'mps',
      device: 'mps',
      model_id: 'span_photo_x4',
      model_name: 'SPAN Quick',
      scale: 4,
      input_width: 512,
      input_height: 512,
      warmup_count: 7,
      measured_frame_count: 18,
      median_inference_ms: 0,
      p95_inference_ms: 0,
      end_to_end_fps: 0,
      processed_megapixels_per_second: 22.86,
      total_elapsed_seconds: 191.87,
      peak_memory_bytes: 512 * 1024 ** 2,
      score: 22.86,
      system_score: 22.86,
      cpu_score: 4.07,
      stable: true,
      cv_percent: 2.4,
      result_elapsed_seconds: 191.87,
      thermal_state: 'nominal',
      reference_label: 'Apple M1 Pro (16-core GPU)',
      reference_ratio: 1.07,
      device_results: [
        {
          device: 'mps',
          device_type: 'mps',
          device_name: 'Apple GPU (Metal)',
          thermal_state: 'nominal',
          warmup_iterations: 7,
          peak_memory_bytes: 512 * 1024 ** 2,
          peak_device_memory_bytes: 256 * 1024 ** 2,
          stable: true,
          cv_percent: 2.4,
          score: 22.86,
          scenes: [
            {
              scene_id: 's1-classroom',
              purpose: 'compute',
              input_width: 512,
              input_height: 512,
              output_width: 2048,
              output_height: 2048,
              iterations: 8,
              median_ms: 165.2,
              p05_ms: 162.1,
              p95_ms: 170.3,
              cv_percent: 2.1,
              megapixels_per_second: 25.4,
              encode_ms: null,
            },
          ],
        },
        {
          device: 'cpu',
          device_type: 'cpu',
          device_name: 'CPU',
          thermal_state: 'nominal',
          warmup_iterations: 5,
          peak_memory_bytes: 384 * 1024 ** 2,
          peak_device_memory_bytes: null,
          stable: true,
          cv_percent: 2.2,
          score: 4.07,
          scenes: [],
        },
      ],
    };
    const user = await mountWith(snapshot);

    await chooseMenuItem('view.performance');
    const dialog = await screen.findByRole('dialog');

    expect(dialog.classList.contains('performance-modal')).toBe(true);
    expect(dialog.querySelector('.benchmark-score-value strong')?.textContent).toBe('22.86');
    expect(within(dialog).getByText('● Stable result')).toBeTruthy();
    expect(within(dialog).getByText('1.07×')).toBeTruthy();
    expect(within(dialog).getByText('Apple M1 Pro (16-core GPU)')).toBeTruthy();
    expect(
      within(dialog).getByRole('article', { name: 'Apple GPU (Metal) benchmark result' }),
    ).toBeTruthy();
    expect(within(dialog).getByRole('article', { name: 'CPU benchmark result' })).toBeTruthy();
    expect(
      dialog.querySelector('.benchmark-device .file-name[title="Apple GPU (Metal)"]'),
    ).toBeTruthy();
    expect(within(dialog).getByText('Hardware results')).toBeTruthy();
    expect(within(dialog).getAllByText(/Older v2 benchmark/).length).toBeGreaterThan(0);
  });

  it('renders v3 multi-device benchmark results, 1 MP example, CPU threads, and failed correctness check', async () => {
    const snapshot = readySnapshot();
    snapshot.latest_benchmark = {
      workload_version: 'localsr-benchmark-v3',
      backend: 'cpu',
      device: 'cpu',
      model_id: 'localsr_quick_x4_v1',
      model_name: 'LocalSR Photo S',
      scale: 4,
      input_width: 256,
      input_height: 256,
      warmup_count: 3,
      measured_frame_count: 10,
      median_inference_ms: 200,
      p95_inference_ms: 210,
      end_to_end_fps: 5,
      processed_megapixels_per_second: 0.3,
      total_elapsed_seconds: 32.0,
      peak_memory_bytes: 256 * 1024 ** 2,
      score: 1.76,
      recommended_device: 'cpu',
      recommended_device_name: 'CPU',
      recommended_device_seconds_per_input_mp: 9.1,
      recommended_cpu_threads: 4,
      cpu_threads: {
        physical_cores: 4,
        logical_cores: 8,
        recommended_threads: 4,
        tested: true,
        physical_seconds_per_input_mp: 9.1,
        logical_seconds_per_input_mp: 11.4,
      },
      cpu_avx2: false,
      cpu_memory_format: 'nchw',
      stable: true,
      cv_percent: 2.0,
      result_elapsed_seconds: 32.0,
      thermal_state: 'nominal',
      benchmarked_models: [
        {
          preset: 'quick',
          preset_label: 'Quick',
          model_id: 'localsr_quick_x4_v1',
          model_name: 'LocalSR Photo S',
          scale: 4,
          recommended_device: 'cpu',
          recommended_device_name: 'CPU',
          recommended_seconds_per_input_mp: 9.1,
        },
      ],
      device_results: [
        {
          device: 'cpu',
          device_type: 'cpu',
          device_name: 'CPU',
          preset: 'quick',
          model_id: 'localsr_quick_x4_v1',
          model_name: 'LocalSR Photo S',
          thermal_state: 'nominal',
          warmup_iterations: 2,
          stable: true,
          cv_percent: 1.8,
          score: 1.76,
          seconds_per_input_mp: 9.1,
          matched_cpu: true,
          status: 'ok',
          recommended: true,
          scenes: [],
        },
        {
          device: 'directml:0',
          device_type: 'directml',
          device_name: 'Intel UHD 620 (DirectML)',
          preset: 'quick',
          model_id: 'localsr_quick_x4_v1',
          model_name: 'LocalSR Photo S',
          thermal_state: 'nominal',
          warmup_iterations: 2,
          stable: true,
          cv_percent: 2.0,
          score: 1.07,
          seconds_per_input_mp: 15.0,
          matched_cpu: true,
          psnr_db: 95.2,
          status: 'ok',
          recommended: false,
          scenes: [],
        },
        {
          device: 'cuda:0',
          device_type: 'cuda',
          device_name: 'Broken GPU (CUDA)',
          preset: 'quick',
          model_id: 'localsr_quick_x4_v1',
          model_name: 'LocalSR Photo S',
          thermal_state: 'nominal',
          warmup_iterations: 0,
          stable: false,
          cv_percent: null,
          score: 0,
          seconds_per_input_mp: null,
          matched_cpu: false,
          psnr_db: 28.4,
          status: 'mismatch',
          status_note: 'did not match the CPU',
          recommended: false,
          scenes: [],
        },
      ],
    };
    await mountWith(snapshot);
    await chooseMenuItem('view.performance');
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getAllByText('9.1').length).toBeGreaterThan(0);
    expect(
      within(dialog).getAllByText(/a 1 MP photo to 16 MP takes about 9 s/i).length,
    ).toBeGreaterThan(0);
    expect(within(dialog).getByText('NCHW (pre-AVX2)')).toBeTruthy();
    expect(within(dialog).getByText('Recommended CPU threads')).toBeTruthy();
    expect(within(dialog).getByText('4')).toBeTruthy();
    expect(within(dialog).getAllByText('did not match the CPU').length).toBeGreaterThan(0);
  });

  it('keeps advanced controls in a dedicated scroll region', async () => {
    const user = await mountWith(readySnapshot([image('first', true)]));

    const inspector = screen.getByRole('region', { name: 'Enhancement settings' });
    expect(inspector.classList.contains('inspector-scroll')).toBe(true);
    await openOutputSheet(user);
    expect(screen.getByLabelText('Format')).toBeTruthy();
    expect(
      screen.getByRole('checkbox', { name: /Preserve safe metadata and color profile/i }),
    ).toBeTruthy();
    await openPlanSheet(user);
    expect(screen.getByRole('checkbox', { name: /Safe memory mode/i })).toBeTruthy();
  });

  it.each([false, true])(
    'keeps pending and failed videos out of processing (batch=%s)',
    async (batch) => {
      const media = video('portrait', true);
      media.width = media.height = 0;
      media.preview_data_url = '';
      media.probe_status = 'pending';
      const snapshot = readySnapshot([media]);
      snapshot.settings.batch_mode = batch;
      await mountWith(snapshot);
      expect(screen.getByRole('heading', { name: 'Preparing preview…' })).toBeTruthy();
      expect(screen.getAllByText('Preparing preview…').length).toBe(2);
      expect(screen.queryByText('Inspecting…')).toBeNull();
      // One item is not counted as a batch, whichever mode is on.
      const button = screen.getByRole('button', { name: 'Start selected video' });
      expect(button.hasAttribute('disabled')).toBe(true);

      const failed = structuredClone(snapshot);
      failed.media[0].probe_status = 'failed';
      failed.media[0].error = 'Cannot decode this video.';
      api.refreshSnapshot.mockResolvedValue(failed);
      const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
      callback({
        type: 'media_probe_failed',
        data: { media_path: media.path, error_message: failed.media[0].error },
      });
      expect(await screen.findByRole('heading', { name: 'Preview unavailable' })).toBeTruthy();
      expect(
        screen.getByRole('button', { name: 'Start selected video' }).hasAttribute('disabled'),
      ).toBe(true);
      expect(api.startJobs).not.toHaveBeenCalled();
    },
  );

  it('shows preview encoding failures as non-fatal warnings', async () => {
    await mountWith(readySnapshot([image('first', true)]));
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;

    callback({
      type: 'live_preview_warning',
      data: { job_id: 'job-1', message: 'JPEG encoder unavailable' },
    });

    expect(await screen.findByText(/Preview warning: JPEG encoder unavailable/)).toBeTruthy();
    expect(screen.getByText(/Processing continues normally/)).toBeTruthy();
  });

  it('shows real import phases, survives refresh and labels HDR output before Start', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1_000_000);
    const media = video('portrait', true);
    media.probe_status = 'pending';
    media.preview_data_url = '';
    const snapshot = readySnapshot([media]);
    snapshot.settings.task = 'video';
    snapshot.settings.batch_mode = true;
    const user = await mountWith(snapshot);
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
    callback({
      type: 'media_probe_progress',
      data: { media_path: media.path, stage: 'decoding_video' },
    });
    expect(await screen.findByText('Reading the first video frame')).toBeTruthy();
    expect(document.querySelector('[aria-busy="true"]')).toBeTruthy();
    // Keep the display at a known second even if a busy CI VM renders slowly.
    clock.mockReturnValue(1_001_250);
    expect(await screen.findByText('1s elapsed', {}, { timeout: 2500 })).toBeTruthy();
    callback({
      type: 'media_probe_progress',
      data: { media_path: media.path, stage: 'converting_hdr' },
    });
    expect(await screen.findByText('Converting HDR to an SDR preview')).toBeTruthy();
    const refresh = api.listenForStateChange.mock.calls[0][0] as () => void;
    refresh();
    await waitFor(() => expect(api.refreshSnapshot).toHaveBeenCalled());
    expect(screen.getByText('Converting HDR to an SDR preview')).toBeTruthy();
    const ready = structuredClone(snapshot);
    Object.assign(ready.media[0], {
      probe_status: 'ready',
      hdr_format: 'HLG',
      audio_warning: 'Standard audio will be kept. The extra track will be omitted.',
      preview_data_url: 'data:image/jpeg;base64,test',
    });
    api.refreshSnapshot.mockResolvedValue(ready);
    callback({
      type: 'media_info',
      data: {
        media_path: media.path,
        width: 2160,
        height: 3840,
        hdr_format: 'HLG',
        audio_warning: 'Standard audio will be kept. The extra track will be omitted.',
        jpeg_base64: 'test',
      },
    });
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Start selected video' }).hasAttribute('disabled'),
      ).toBe(false),
    );
    expect(screen.queryByText('Converting HDR to an SDR preview')).toBeNull();
    const output = await openOutputSheet(user);
    expect(within(output).getByLabelText('Colour output')).toBeTruthy();
    const hdrTip = within(output).getByRole('button', { name: 'About Colour output' });
    expect(
      document.getElementById(hdrTip.getAttribute('aria-describedby') ?? '')?.textContent,
    ).toMatch(/exported as 8-bit SDR/);
    // The note about this video's audio is a tip on Clean up audio in the Output sheet.
    expect(screen.queryByRole('region', { name: 'Audio compatibility' })).toBeNull();
    const audioTip = within(output).getByRole('button', {
      name: "About this video's audio",
    });
    expect(
      document.getElementById(audioTip.getAttribute('aria-describedby') ?? '')?.textContent,
    ).toBe('Standard audio will be kept. The extra track will be omitted.');
  });

  async function pickUpscalerInLibrary(
    user: ReturnType<typeof userEvent.setup>,
    rowName: RegExp,
  ): Promise<void> {
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: rowName }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull());
  }

  it('switches SeedVR2 to SDR, preserves the custom HDR option, and submits the chosen resolution', async () => {
    const media = { ...video('hdr', true), width: 2160, height: 3840, hdr_format: 'HLG' as const };
    const snapshot = readySnapshot([media]);
    Object.assign(snapshot.settings, {
      task: 'video',
      selected_model_id: 'hat_s_x4',
      selected_video_model_id: 'frame_by_frame',
      video_hdr_mode: 'preserve',
    });
    snapshot.settings.device_id = 'cuda:0';
    snapshot.capabilities.devices[0] = {
      ...snapshot.capabilities.devices[0],
      id: 'cuda:0',
      type: 'rocm',
      name: 'AMD GPU',
    };
    const user = await mountWith(snapshot);
    let output = await openOutputSheet(user);
    expect((within(output).getByLabelText('Colour output') as HTMLSelectElement).value).toBe(
      'preserve',
    );
    await pickUpscalerInLibrary(user, /^SeedVR2-3B — Labs/);
    output = await openOutputSheet(user);
    const hdrAfterSeed = within(output).getByLabelText('Colour output') as HTMLSelectElement;
    expect(hdrAfterSeed.value).toBe('tone_map');
    expect(
      hdrAfterSeed.querySelector<HTMLOptionElement>('option[value="preserve"]')!.disabled,
    ).toBe(true);
    expect(screen.getByText('SDR output selected. This model cannot preserve HDR.')).toBeTruthy();
    expect((screen.getByLabelText('Output resolution') as HTMLSelectElement).value).toBe('x4');
    await user.selectOptions(screen.getByLabelText('Output resolution'), '512');
    expect(screen.getAllByText('512 × 910 · SDR').length).toBeGreaterThan(0);
    const memory = screen.getByRole('checkbox', { name: 'Reduce GPU memory' }) as HTMLInputElement;
    expect(memory.checked).toBe(true);
    await user.click(memory);
    await user.click(screen.getByRole('button', { name: 'Start selected video' }));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({
        video_model_id: 'seedvr2_3b',
        video_hdr_mode: 'tone_map',
        video_target_resolution: 512,
        video_low_memory: false,
      }),
    );
    await pickUpscalerInLibrary(user, /^HAT-S ×4/);
    output = await openOutputSheet(user);
    expect((within(output).getByLabelText('Colour output') as HTMLSelectElement).value).toBe(
      'preserve',
    );
    api.chooseCustomModel.mockResolvedValueOnce('/models/mine.safetensors' as never);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    await user.click(screen.getByRole('button', { name: /Your own file/ }));
    await user.click(screen.getByRole('button', { name: 'Choose checkpoint…' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull());
    output = await openOutputSheet(user);
    const hdrCustom = within(output).getByLabelText('Colour output') as HTMLSelectElement;
    expect(hdrCustom.querySelector<HTMLOptionElement>('option[value="preserve"]')!.disabled).toBe(
      false,
    );
    await user.selectOptions(hdrCustom, 'preserve');
    expect(hdrCustom.value).toBe('preserve');
    expect(screen.getByText(/The current adapter requires HAT/)).toBeTruthy();
  });

  it('keeps a saved HDR preference when reopening SeedVR2', async () => {
    const snapshot = readySnapshot([{ ...video('hdr', true), hdr_format: 'HLG' }]);
    Object.assign(snapshot.settings, {
      task: 'video',
      selected_video_model_id: 'seedvr2_3b',
      video_hdr_mode: 'preserve',
    });
    const user = await mountWith(snapshot);
    const output = await openOutputSheet(user);
    expect((within(output).getByLabelText('Colour output') as HTMLSelectElement).value).toBe(
      'tone_map',
    );
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({ video_hdr_mode: 'preserve' }),
      ),
    );
  });

  it('saves the current setup as a named recipe beside the built-in recipes', async () => {
    const snapshot = readySnapshot([image('first', true)]);
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await user.click(screen.getByRole('button', { name: 'Save current as recipe…' }));
    const name = screen.getByRole('textbox', { name: 'Recipe name' });
    expect(document.activeElement).toBe(name);
    await user.type(name, 'Portrait cleanup');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(api.saveRecipe).toHaveBeenCalledTimes(1));
    expect(api.saveRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Portrait cleanup', task: 'upscale' }),
    );
  }, 20_000);

  it('lights up a saved recipe while its settings are applied', async () => {
    const snapshot = readySnapshot([image('first', true)]);
    const model = snapshot.catalog.models.find((entry) => entry.native_scale === 4)!;
    snapshot.recipes = [
      {
        id: 'jens67',
        name: 'Jens67',
        task: 'upscale',
        model_id: model.model_id,
        output_scale: 4,
        tile_size: 256,
        halo: 32,
        precision: 'fp32',
        safe_memory: true,
      },
    ];
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    const recipe = screen.getByRole('button', { name: /^Jens67/ });
    expect(recipe.getAttribute('aria-pressed')).toBe('false');
    await user.click(recipe);
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Recipes' })).toBeNull());
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    expect(screen.getByRole('button', { name: /^Jens67/ }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(screen.getByRole('button', { name: /^Jens67/ }).classList.contains('active')).toBe(true);
    await user.click(screen.getByRole('button', { name: /^Quick/ }));
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Jens67/ }).getAttribute('aria-pressed')).toBe(
        'false',
      ),
    );
  });

  it('exposes image recipes, hardware controls, and installed face companions', async () => {
    const user = await mountWith(readySnapshot());
    await chooseTask(user, /^Upscale & restore$/);

    expect(screen.getByRole('button', { name: /^Quick\s*Fast, clean result/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Best\s*Finest detail, slower/i })).toBeTruthy();
    await openPlanSheet(user);
    expect(screen.getByRole('list', { name: 'Stages' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Change the upscaler' })).toBeTruthy();

    const hardware = screen.getByLabelText('Hardware');
    expect(within(hardware).getByRole('option', { name: 'Apple GPU (Metal)' })).toBeTruthy();
    expect(within(hardware).getByRole('option', { name: 'CPU' })).toBeTruthy();

    // Faces need a primary model with a companion; the default Best (RealPLKSR)
    // has none, so the row is not offered until such a model is chosen.
    expect(screen.queryByRole('checkbox', { name: 'Faces' })).toBeNull();
  });

  it.each([['hat_s_x4'], ['hat_l_x4_imagenet']])(
    'does not offer the Faces chip or companion UI for %s',
    async (primaryId) => {
      const snapshot = readySnapshot([image('portrait', true)]);
      snapshot.engine!.features.push('face_aware');
      snapshot.settings.task = 'upscale';
      snapshot.settings.selected_model_id = primaryId;
      snapshot.settings.quality = 'custom';
      await mountWith(snapshot);
      expect(screen.queryByRole('checkbox', { name: 'Faces' })).toBeNull();
      expect(
        screen.queryByRole('button', { name: 'Choose externally downloaded face checkpoint…' }),
      ).toBeNull();
      expect(screen.queryByLabelText(/Face fidelity/)).toBeNull();
      expect(
        screen.getByRole('button', { name: 'Upscale selected' }).hasAttribute('disabled'),
      ).toBe(false);
    },
  );

  it('chooses temporal video models from the library and keeps frame-by-frame options hidden', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    const user = await mountWith(snapshot);

    expect(screen.queryByLabelText('Video engine')).toBeNull();
    expect(document.querySelectorAll('.model-output-note')).toHaveLength(0);
    const oldModelId = (api.saveSettings.mock.calls.at(-1)?.[0] as UiSettings).selected_model_id;

    expect(screen.getByRole('checkbox', { name: 'De-flicker' })).toBeTruthy();
    await openOutputSheet(user);
    expect(screen.getByLabelText('Container')).toBeTruthy();
    expect(screen.getByLabelText(/Video quality · CRF/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start selected video' })).toBeTruthy();

    await pickUpscalerInLibrary(user, /^SeedVR2-3B — Labs/);
    expect(api.saveSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({
        selected_video_model_id: 'seedvr2_3b',
        quality: 'custom',
        selected_model_id: oldModelId,
      }),
    );
    await openPlanSheet(user);
    const stages = screen.getByRole('list', { name: 'Stages' });
    expect(within(stages).getByText('SeedVR2-3B')).toBeTruthy();
    expect(within(stages).getByText('Labs · several frames at once')).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Your video' })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: 'De-flicker' })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /Safe memory mode/i })).toBeNull();
  });

  it.each([false, true])(
    'preserves video options across engine switches with de-flicker %s',
    async (deflicker) => {
      const snapshot = readySnapshot([video('clip', true)]);
      snapshot.engine!.features.push('face_aware');
      Object.assign(snapshot.settings, {
        task: 'video',
        quality: 'custom',
        selected_model_id: 'hat_l_x4_imagenet',
        fixes: [],
        deflicker,
        video_interpolation: 'x2',
        enable_face_model: false,
      });
      const user = await mountWith(snapshot);
      await pickUpscalerInLibrary(user, /^SeedVR2-3B — Labs/);
      await pickUpscalerInLibrary(user, /^HAT-L ×4 ImageNet/);
      expect(
        (screen.getByRole('checkbox', { name: 'De-flicker' }) as HTMLInputElement).checked,
      ).toBe(deflicker);
      expect((screen.getByLabelText('Frame rate') as HTMLSelectElement).value).toBe('x2');
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          selected_video_model_id: 'frame_by_frame',
          deflicker,
          video_interpolation: 'x2',
          enable_face_model: false,
        }),
      );
      await user.click(screen.getByRole('button', { name: /^Quick/ }));
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ selected_video_model_id: 'frame_by_frame' }),
      );
      expect(screen.getByRole('button', { name: /^Quick/ }).getAttribute('aria-pressed')).toBe(
        'true',
      );
    },
  );

  it('disables interpolation when the engine does not advertise it', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    snapshot.engine!.features = ['video_frame'];
    await mountWith(snapshot);
    expect((screen.getByLabelText('Frame rate') as HTMLSelectElement).disabled).toBe(true);
  });

  it('allows interpolation for SDR with a saved HDR preference', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    snapshot.settings.video_hdr_mode = 'preserve';
    snapshot.settings.selected_model_id = 'hat_s_x4';
    const user = await mountWith(snapshot);
    const rate = screen.getByLabelText('Frame rate') as HTMLSelectElement;
    expect(rate.disabled).toBe(false);
    await user.selectOptions(rate, 'x2');
    expect(rate.value).toBe('x2');
  });

  it('distinguishes video recipes by interpolation target and order', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    const model = snapshot.catalog.models.find((entry) => entry.native_scale === 4)!;
    snapshot.recipes = [
      {
        id: 'one',
        name: 'Original recipe',
        task: 'video',
        model_id: model.model_id,
        output_scale: 4,
        tile_size: 256,
        halo: 32,
        precision: 'fp32',
        safe_memory: true,
        video_interpolation: 'off',
        video_interpolation_order: 'after_upscale',
      },
      {
        id: 'two',
        name: 'Motion recipe',
        task: 'video',
        model_id: model.model_id,
        output_scale: 4,
        tile_size: 256,
        halo: 32,
        precision: 'fp32',
        safe_memory: true,
        video_interpolation: 'fps120',
        video_interpolation_order: 'before_upscale',
      },
    ];
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await user.click(screen.getByRole('button', { name: /^Motion recipe/ }));
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Recipes' })).toBeNull());
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    expect(
      screen.getByRole('button', { name: /^Motion recipe/ }).getAttribute('aria-pressed'),
    ).toBe('true');
    expect(
      screen.getByRole('button', { name: /^Original recipe/ }).getAttribute('aria-pressed'),
    ).toBe('false');
  });

  it('offers frame interpolation for video, off by default, and sends it with Start', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.catalog.interpolation_models.forEach((model) => (model.installed = true));
    const user = await mountWith(snapshot);
    const rate = screen.getByLabelText('Frame rate') as HTMLSelectElement;
    expect(rate.value).toBe('off');
    expect(screen.queryByLabelText('Interpolate')).toBeNull();
    expect(screen.queryByText('Interpolate to 60 fps')).toBeNull();

    await user.selectOptions(rate, 'fps60');
    const order = screen.getByLabelText('Interpolate') as HTMLSelectElement;
    expect(order.value).toBe('after_upscale');
    await user.selectOptions(order, 'before_upscale');
    await openPlanSheet(user);
    expect(screen.getByText('Interpolate to 60 fps')).toBeTruthy();
    expect(screen.getByText(/24 → 60 fps/)).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Start selected video' }));
    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({
        task: 'video',
        video_interpolation: 'fps60',
        video_interpolation_order: 'before_upscale',
      }),
    );
  });

  it('moves interpolation before or after upscaling from the plan list', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    snapshot.settings.video_interpolation = 'x2';
    snapshot.settings.video_interpolation_order = 'after_upscale';
    snapshot.catalog.interpolation_models.forEach((model) => (model.installed = true));
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    const stages = screen.getByRole('list', { name: 'Stages' });
    expect(within(stages).getByText(/after upscaling/)).toBeTruthy();
    expect(
      within(stages).queryByRole('button', { name: 'Interpolate after upscaling' }),
    ).toBeNull();
    await user.click(within(stages).getByRole('button', { name: 'Interpolate before upscaling' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ video_interpolation_order: 'before_upscale' }),
      ),
    );
    await waitFor(() => expect(within(stages).getByText(/before upscaling/)).toBeTruthy());
    const rows = within(stages).getAllByRole('listitem');
    const order = rows.map((row) => row.textContent ?? '');
    expect(order.findIndex((text) => text.includes('RIFE'))).toBeLessThan(
      order.findIndex((text) => /Upscale/.test(text)),
    );
    expect(
      within(stages).getByRole('button', { name: 'Interpolate after upscaling' }),
    ).toBeTruthy();
  });

  it('downloads the interpolation model when Start needs it', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    snapshot.settings.video_interpolation = 'x2';
    snapshot.catalog.interpolation_models.forEach((model) => (model.installed = false));
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: 'Download 23 MB, then start video' }));
    await waitFor(() => expect(api.downloadModel).toHaveBeenCalledWith('rife_4_26', false));
  });

  it('keeps interpolation out of SeedVR2 jobs and HDR preservation', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    snapshot.settings.video_interpolation = 'x2';
    snapshot.catalog.interpolation_models.forEach((model) => (model.installed = true));
    const user = await mountWith(snapshot);
    await pickUpscalerInLibrary(user, /^SeedVR2-3B — Labs/);
    expect(screen.queryByLabelText('Frame rate')).toBeNull();
    expect(screen.queryByText('Interpolate 2×')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Start selected video' }));
    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({
        video_model_id: 'seedvr2_3b',
        video_interpolation: 'off',
        deflicker: false,
      }),
    );
  });

  it.each(['incompatible-runtime', 'directml:0', 'xpu:0', 'openvino:80d1d11eb738'])(
    'blocks unavailable SeedVR2 recipes and downloads on %s',
    async (backend) => {
      const snapshot = readySnapshot([video('clip', true)]);
      snapshot.settings.task = 'video';
      snapshot.settings.selected_video_model_id = 'seedvr2_3b';
      if (backend === 'incompatible-runtime') snapshot.engine!.video_engines = ['spandrel_image'];
      else {
        snapshot.settings.device_id = backend;
        snapshot.capabilities.devices.push(gpu(backend, backend.split(':')[0], 'Test GPU'));
      }
      snapshot.catalog.video_models.forEach((model) => {
        model.installed = false;
      });
      const user = await mountWith(snapshot);
      expect(document.querySelector('.inline-warning')?.textContent).toMatch(/SeedVR2/);
      expect(
        screen.getByRole('button', { name: 'Start selected video' }).matches(':disabled'),
      ).toBe(true);
      expect(screen.queryByRole('button', { name: /^Download / })).toBeNull();
      await pickUpscalerInLibrary(user, /^SPAN ×4/);
      expect(
        screen.getByRole('button', { name: 'Start selected video' }).matches(':disabled'),
      ).toBe(false);
      expect(api.downloadModel).not.toHaveBeenCalled();
    },
  );

  /** A Mac whose engine also lists FlashVSR, with this much unified memory. */
  function flashSnapshot(unifiedGb: number): AppSnapshot {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    snapshot.engine!.video_engines.push('flashvsr');
    snapshot.capabilities.system_ram_total = unifiedGb * 1024 ** 3;
    return snapshot;
  }

  it('runs FlashVSR on the Apple GPU of a 16 GB Mac and describes it in its own terms', async () => {
    const user = await mountWith(flashSnapshot(16));
    expect(document.querySelectorAll('.model-output-note')).toHaveLength(0);

    await openPlanSheet(user);
    await user.click(await screen.findByRole('button', { name: 'Change the upscaler' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /^FlashVSR v1\.1 — Labs/ }));
    expect(
      (within(library).getByRole('button', { name: 'Use for this job' }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull());

    const memory = screen.getByLabelText('FlashVSR memory');
    expect(memory.textContent).toMatch(
      /windows of 37 frames; each starts with 8\s+frames of run-up/,
    );
    expect(memory.textContent).not.toMatch(/SeedVR2|VAE|per clip|offload/);
    expect(screen.queryByRole('checkbox', { name: 'Reduce GPU memory' })).toBeNull();
    expect(screen.getByLabelText('Output resolution')).toBeTruthy();
    expect(screen.getByText(/above that, it runs at 4× and resizes the rest/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Advanced/ })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /De-flicker · Labs/i })).toBeNull();

    // SeedVR2 keeps its own panel and description.
    await pickUpscalerInLibrary(user, /^SeedVR2-3B — Labs/);
    expect(screen.getByLabelText('SeedVR2 memory').textContent).toMatch(
      /Up to 5 frames per clip · 128 px VAE tiles\./,
    );
    expect(screen.getByText(/Smaller output uses less memory/)).toBeTruthy();

    await pickUpscalerInLibrary(user, /^FlashVSR v1\.1 — Labs/);
    await user.click(screen.getByRole('button', { name: 'Start selected video' }));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({
        video_model_id: 'flashvsr_v11',
        fix_stages: [],
        deflicker: false,
        video_interpolation: 'off',
        video_hdr_mode: 'tone_map',
      }),
    );
  });

  it.each([
    ['the CPU', 'cpu', 16, true, 'Unavailable', /^FlashVSR cannot run on this device\./],
    [
      'an 8 GB CUDA GPU',
      'cuda:0',
      16,
      true,
      'Needs 12 GB',
      /^FlashVSR needs 12 GB of GPU memory\. This GPU has 8 GB\.$/,
    ],
    [
      'a Mac with 8 GB',
      'mps',
      8,
      true,
      'Needs 16 GB',
      /^FlashVSR needs 16 GB of memory\. This Mac has 8 GB\.$/,
    ],
    [
      'an engine that does not list it',
      'mps',
      16,
      false,
      'Unavailable',
      /^FlashVSR needs the MLX runtime/,
    ],
  ] as const)(
    'keeps FlashVSR unavailable on %s without taking SeedVR2 with it',
    async (_name, deviceId, unifiedGb, listed, tag, reason) => {
      const snapshot = flashSnapshot(unifiedGb);
      if (!listed) snapshot.engine!.video_engines = ['spandrel_image', 'seedvr2'];
      if (deviceId === 'cuda:0') {
        snapshot.capabilities.devices.push({
          ...gpu('cuda:0', 'cuda', 'GPU'),
          total_memory: 8 * 1024 ** 3,
        });
      }
      snapshot.settings.device_id = deviceId;
      snapshot.settings.selected_video_model_id = 'flashvsr_v11';
      const user = await mountWith(snapshot);
      // A saved FlashVSR choice cannot start and says why; the panel that describes this Mac
      // stays away.
      const warning = document.querySelector('.inline-warning');
      expect(warning?.textContent?.trim()).toMatch(reason);
      expect(
        screen.getByRole('button', { name: 'Start selected video' }).matches(':disabled'),
      ).toBe(true);
      expect(screen.queryByLabelText('FlashVSR memory')).toBeNull();

      await openPlanSheet(user);
      await user.click(await screen.findByRole('button', { name: 'Change the upscaler' }));
      const library = screen.getByRole('dialog', { name: 'Model library' });
      const flashRow = within(library).getByRole('button', { name: /^FlashVSR v1\.1 — Labs/ });
      expect(within(flashRow).getByText(tag)).toBeTruthy();
      expect(within(library).getByText(reason)).toBeTruthy();
      expect(
        (within(library).getByRole('button', { name: 'Use for this job' }) as HTMLButtonElement)
          .disabled,
      ).toBe(true);

      await user.click(within(library).getByRole('button', { name: /^SeedVR2-3B — Labs/ }));
      const useButton = within(library).getByRole('button', {
        name: 'Use for this job',
      }) as HTMLButtonElement;
      expect(useButton.disabled).toBe(false);
      await user.click(useButton);
      await waitFor(() =>
        expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull(),
      );
      expect(
        screen.getByRole('button', { name: 'Start selected video' }).matches(':disabled'),
      ).toBe(false);
      expect(document.querySelector('.inline-warning')).toBeNull();
    },
  );

  it('shows old temporal settings with quality quick as Custom', async () => {
    const oldSettingsSnapshot = flashSnapshot(16);
    oldSettingsSnapshot.settings.quality = 'quick';
    oldSettingsSnapshot.settings.selected_video_model_id = 'seedvr2_3b';
    const user = await mountWith(oldSettingsSnapshot);
    expect(screen.getByRole('button', { name: 'Recipes' })).toBeTruthy();
    expect(screen.getByText('Model · chosen by you')).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Quick/ }).getAttribute('aria-pressed')).toBe(
      'false',
    );
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('SeedVR2-3B'),
    ).toBeTruthy();
  });

  it('selects the active video model and marks it Chosen when opening the library', async () => {
    const flashJob = flashSnapshot(16);
    flashJob.settings.selected_video_model_id = 'flashvsr_v11';
    const user = await mountWith(flashJob);
    await openPlanSheet(user);
    await user.click(await screen.findByRole('button', { name: 'Change the upscaler' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    const flashRow = within(library).getByRole('button', { name: /^FlashVSR v1\.1 — Labs/ });
    expect(flashRow.getAttribute('aria-pressed')).toBe('true');
    expect(within(flashRow).getByText('Chosen')).toBeTruthy();
    expect(flashRow.textContent).toContain('Quality: 3 out of 4');
    expect(flashRow.textContent).toContain('Speed: 1 out of 4');
    const seedRow = within(library).getByRole('button', { name: /^SeedVR2-3B — Labs/ });
    expect(seedRow.textContent).toContain('Quality: 4 out of 4');
    expect(seedRow.textContent).toContain('Speed: 0 out of 4');
    await user.click(within(library).getByRole('button', { name: 'Close model library' }));

    // Video Same size has no video rows in For this job and no Use for this job in Video (Labs).
    await user.click(screen.getByRole('button', { name: /^Quick/ }));
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    const restoreLib = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(restoreLib).queryByText('Several frames at once · Labs')).toBeNull();
    await user.click(within(restoreLib).getByRole('button', { name: /Video \(Labs\)/ }));
    await user.click(within(restoreLib).getByRole('button', { name: /^FlashVSR v1\.1 — Labs/ }));
    expect(within(restoreLib).queryByRole('button', { name: 'Use for this job' })).toBeNull();
  });

  it('omits video rows from For this job and Use for this job in Photo upscale', async () => {
    const photoSnap = readySnapshot([image('photo', true)]);
    photoSnap.settings.task = 'upscale';
    const photoUser = await mountWith(photoSnap);
    await openPlanSheet(photoUser);
    await photoUser.click(await screen.findByRole('button', { name: 'Change the upscaler' }));
    const photoLib = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(photoLib).queryByText('Several frames at once · Labs')).toBeNull();
    await photoUser.click(within(photoLib).getByRole('button', { name: /Video \(Labs\)/ }));
    await photoUser.click(within(photoLib).getByRole('button', { name: /^SeedVR2-3B — Labs/ }));
    expect(within(photoLib).queryByRole('button', { name: 'Use for this job' })).toBeNull();
  });

  it('counts runnable video models in N more models under the plan row', async () => {
    const seedOnlyMac = readySnapshot([video('clip', true)]);
    seedOnlyMac.settings.task = 'video';
    seedOnlyMac.capabilities.system_ram_total = 16 * 1024 ** 3;
    const user = await mountWith(seedOnlyMac);
    await openPlanSheet(user);
    const seedOnlyButton = await screen.findByRole('button', {
      name: /\d+ more models? in the library/,
    });
    const seedOnlyCount = Number(seedOnlyButton.textContent!.match(/(\d+)/)![1]);

    // When FlashVSR becomes runnable on this Mac, the count grows by 1.
    const switched = structuredClone(seedOnlyMac);
    switched.engine!.video_engines.push('flashvsr');
    api.refreshSnapshot.mockResolvedValue(switched);
    (api.listenForStateChange.mock.calls[0][0] as () => void)();
    await waitFor(() => {
      const text = screen.getByRole('button', {
        name: /\d+ more models? in the library/,
      }).textContent!;
      expect(Number(text.match(/(\d+)/)![1])).toBe(seedOnlyCount + 1);
    });
    void user;
  });

  it('marks an unavailable video model in the library and says why', async () => {
    const snapshot = flashSnapshot(8);
    snapshot.catalog.video_models.forEach((model) => (model.installed = false));
    const user = await mountWith(snapshot);
    expect(document.querySelectorAll('.model-output-note')).toHaveLength(0);
    await chooseMenuItem('view.models');
    const library = await screen.findByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /Video \(Labs\)/ }));
    expect(within(library).getByText('3 models')).toBeTruthy();
    expect(within(library).queryByText('SeedVR2 engines')).toBeNull();
    const row = within(library).getByRole('button', { name: /^FlashVSR v1\.1 — Labs/ });
    expect(within(row).getByText('Needs 16 GB')).toBeTruthy();
    expect(within(library).getAllByText(/^Needs 16 GB$|^Unavailable$/)).toHaveLength(1);
    await user.click(row);
    expect(
      within(library).getByText('FlashVSR needs 16 GB of memory. This Mac has 8 GB.'),
    ).toBeTruthy();
    expect(within(library).getByText('FlashVSR')).toBeTruthy();
  });

  it('shows the precision of the CPU for a model the selected NPU does not run', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.capabilities.devices = [
      {
        id: 'openvino:80d1',
        type: 'npu',
        name: 'Intel(R) AI Boost (NPU)',
        total_memory: 16 * 1024 ** 3,
        free_memory: 8 * 1024 ** 3,
        supports_fp16: false,
        is_integrated: true,
        recommended_tile_sizes: [128, 256],
      },
      ...snapshot.capabilities.devices.filter((device) => device.id === 'cpu'),
    ];
    snapshot.settings.device_id = 'openvino:80d1';
    snapshot.settings.selected_model_id = 'hat_l_x4_imagenet';
    snapshot.settings.precision = 'fp32';
    snapshot.settings.quality = 'custom';
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    const precision = screen.getByLabelText('Precision') as HTMLSelectElement;
    expect(precision.value).toBe('fp32');
  });

  it('keeps processing settings locked while another media item is selected', async () => {
    const snapshot = readySnapshot([image('active-image'), video('next-video', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.runtime.active_job_id = 'image-job';
    const user = await mountWith(snapshot);
    expect(screen.getByRole('group', { name: 'Processing settings' }).matches(':disabled')).toBe(
      true,
    );
    expect(screen.getByRole('button', { name: 'Add Media…' }).matches(':disabled')).toBe(true);
    // The selected video cannot join an upscale queue: no button, not a disabled one.
    expect(screen.queryByRole('button', { name: 'Add selected to queue' })).toBeNull();
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    expect(api.saveSettings).not.toHaveBeenCalled();
    expect(api.startJobs).not.toHaveBeenCalled();
    // The header says it once, with the explanation as its tooltip; no second line under it.
    const note = screen.getByText('Locked while running');
    expect(note.getAttribute('title')).toBe('Finish or cancel the run to change settings.');
    expect(screen.queryByRole('button', { name: 'Recipes' })).toBeNull();
    expect(screen.queryByText(/Finish or cancel the queue/)).toBeNull();
    expect(document.querySelector('.settings-lock')).toBeNull();
    const region = screen.getByRole('region', { name: 'Enhancement settings' });
    const described = document.getElementById(region.getAttribute('aria-describedby') ?? '');
    expect(described?.textContent).toBe('Finish or cancel the run to change settings.');
    expect(described?.getAttribute('role')).toBe('status');
  });

  it('disables every control of the Enhance pane while a job runs, help tips apart', async () => {
    const snapshot = readySnapshot([image('active-image')]);
    snapshot.settings.task = 'upscale';
    snapshot.runtime.active_job_id = 'image-job';
    await mountWith(snapshot);
    const region = screen.getByRole('region', { name: 'Enhancement settings' });
    const interactive = [
      ...region.querySelectorAll(
        'button, select, input, textarea, a[href], [role=button], [tabindex]',
      ),
    ].filter((item) => !item.classList.contains('help-tip'));
    expect(interactive.length).toBeGreaterThan(10);
    const open = interactive.filter((item) => !item.matches(':disabled'));
    expect(open.map((item) => item.outerHTML.slice(0, 80))).toEqual([]);
    expect(region.querySelectorAll('.help-tip').length).toBeGreaterThan(0);
  });

  it('shows the same two tasks for photos, videos and no media; a video greys out Edit', async () => {
    const tasks = (): HTMLButtonElement[] =>
      Array.from(screen.getByRole('group', { name: 'Task' }).querySelectorAll('button'));
    const shown = (): unknown[] =>
      tasks().map((b) => [b.textContent, b.getAttribute('aria-pressed'), b.disabled]);

    const user = await mountWith(readySnapshot([image('photo', true)]));
    expect(document.querySelector('.task-caption')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'About Task' }));
    expect(screen.getByRole('tooltip').textContent).toBe(
      'Upscale & restore enlarges photos and artwork, fixes them, or both. Edit changes a photo from a description in words.',
    );
    expect(shown()).toEqual([
      ['Upscale & restore', 'true', false],
      ['Edit', 'false', false],
    ]);
    expect(screen.queryByRole('button', { name: /^Restore$/ })).toBeNull();

    cleanup();
    const videoUser = await mountWith(readySnapshot([video('clip', true)]));
    expect(shown()).toEqual([
      ['Upscale & restore', 'true', false],
      ['Edit', 'false', true],
    ]);
    expect(document.querySelector('.task-caption')).toBeNull();
    await videoUser.click(screen.getByRole('button', { name: 'About Task' }));
    expect(screen.getByRole('tooltip').textContent).toBe(
      'Upscale & restore enlarges videos, fixes them, or both. Edit is for photos.',
    );
    expect(screen.getByRole('group', { name: 'Fix' })).toBeTruthy();
    expect(screen.getByRole('radiogroup', { name: 'Size' })).toBeTruthy();
    // Pressing the task a video already has changes nothing.
    api.saveSettings.mockClear();
    await videoUser.click(tasks()[0]);
    expect(api.saveSettings).not.toHaveBeenCalled();
    expect(shown()[0]).toEqual(['Upscale & restore', 'true', false]);

    // With the video removed the row stays, and Edit can be chosen again.
    cleanup();
    const empty = readySnapshot([]);
    empty.settings.task = 'video';
    const emptyUser = await mountWith(empty);
    expect(shown()).toEqual([
      ['Upscale & restore', 'true', false],
      ['Edit', 'false', false],
    ]);
    await emptyUser.click(tasks()[1]);
    await waitFor(() => expect(tasks()[1].getAttribute('aria-pressed')).toBe('true'));
  });

  it.each([
    [
      { name: 'Intel(R) UHD Graphics 620', download: 'DirectML' },
      /Runs on the CPU\. This computer has Intel UHD Graphics 620, which the DirectML download of LocalSR can use\./,
      true,
    ],
    [
      { name: 'Intel graphics', download: 'intel-driver' },
      /Runs on the CPU\. Intel graphics can run models once Intel's compute driver, the intel-opencl-icd package, is installed\./,
      false,
    ],
  ] as const)(
    'names the download or driver for a GPU this edition cannot use in the idle status line',
    async (suggestion, text, downloads) => {
      const snapshot = readySnapshot([image('photo', true)]);
      snapshot.settings.device_id = 'cpu';
      snapshot.capabilities.gpu_suggestion = suggestion;
      const user = await mountWith(snapshot);
      const status = document.querySelector('.status-copy') as HTMLElement;
      expect(within(status).getByText(text)).toBeTruthy();
      // Another download of LocalSR gets a link; Intel's driver is a system package, named instead.
      const link = within(status).queryByRole('button', { name: 'Download page' });
      expect(Boolean(link)).toBe(downloads);
      if (link) {
        await user.click(link);
        expect(api.openDownloadPage).toHaveBeenCalledTimes(1);
      }
      // The hint moved: the Plan sheet does not repeat it.
      await chooseTask(user, /^Upscale & restore$/);
      await openPlanSheet(user);
      expect(screen.getAllByText(text)).toHaveLength(1);
    },
  );

  it('keeps the CPU hint out of the status line while a job runs or after a failure', async () => {
    const running = readySnapshot([image('photo', true)]);
    running.settings.device_id = 'cpu';
    running.capabilities.gpu_suggestion = { name: 'an NVIDIA GPU', download: 'CUDA' };
    await mountWith(running);
    expect(screen.getByText(/the CUDA download of LocalSR can use/)).toBeTruthy();
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
    callback({ type: 'job_started', data: { job_id: 'job-1' } });
    await waitFor(() =>
      expect(screen.queryByText(/the CUDA download of LocalSR can use/)).toBeNull(),
    );
    callback({
      type: 'job_failed',
      data: { job_id: 'job-1', error_message: 'The output folder is not writable.' },
    });
    await waitFor(() =>
      expect(screen.getByRole('alert', { name: 'Last error' }).textContent).toContain(
        'The output folder is not writable.',
      ),
    );
    expect(screen.queryByText(/the CUDA download of LocalSR can use/)).toBeNull();
  });

  it('preserves every executable backend id from worker discovery to job settings', async () => {
    const snapshot = readySnapshot();
    snapshot.capabilities.devices = [
      ...snapshot.capabilities.devices,
      {
        id: 'cuda:0',
        type: 'cuda',
        name: 'NVIDIA GPU (CUDA)',
        total_memory: 12,
        free_memory: 8,
        supports_fp16: true,
        is_integrated: false,
        recommended_tile_sizes: [64, 128],
      },
      {
        id: 'cuda:1',
        type: 'rocm',
        name: 'AMD GPU (ROCm)',
        total_memory: 12,
        free_memory: 8,
        supports_fp16: true,
        is_integrated: false,
        recommended_tile_sizes: [64, 128],
      },
      {
        id: 'xpu:0',
        type: 'xpu',
        name: 'Intel GPU (XPU)',
        total_memory: 8,
        free_memory: 6,
        supports_fp16: true,
        is_integrated: true,
        recommended_tile_sizes: [64, 128],
      },
      {
        id: 'directml:0',
        type: 'directml',
        name: 'Intel(R) Iris(R) Xe Graphics (DirectML)',
        total_memory: 8,
        free_memory: 6,
        supports_fp16: true,
        is_integrated: true,
        recommended_tile_sizes: [64, 128],
      },
      {
        id: 'openvino:8680677d060000000002000000000000',
        type: 'openvino',
        name: 'Intel(R) Graphics (iGPU) (OpenVINO)',
        total_memory: 8,
        free_memory: 6,
        supports_fp16: false,
        is_integrated: true,
        recommended_tile_sizes: [64, 128],
      },
    ];
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Upscale & restore$/);
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Appearance' }));
    // The saved scale is a number; the menu must still show the matching option.
    expect(
      (within(settingsDialog).getByLabelText('Interface text') as HTMLSelectElement).selectedIndex,
    ).toBe(0);

    await user.click(within(settingsDialog).getByRole('tab', { name: 'Performance' }));
    const hardware = within(settingsDialog).getByLabelText('Hardware');

    for (const name of [
      'Apple GPU (Metal)',
      'CPU',
      'NVIDIA GPU (CUDA)',
      'AMD GPU (ROCm)',
      'Intel GPU (XPU)',
      // Adapter names lose the trademark marks, OpenVINO's (iGPU) and a single route's tag.
      'Intel Iris Xe Graphics',
      'Intel Graphics',
    ]) {
      expect(within(hardware).getByRole('option', { name })).toBeTruthy();
    }

    await user.selectOptions(hardware, 'directml:0');
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ device_id: 'directml:0' }),
      ),
    );
    expect(
      within(settingsDialog).getByText(/DirectML has not been tested on real hardware yet/),
    ).toBeTruthy();
    expect(
      within(settingsDialog).getByText(/checks once that it gives the same result as the CPU/),
    ).toBeTruthy();
    // ONNX Runtime GPU routes always run FP32: the control says so and cannot change it.
    await openPlanSheet(user);
    const precision = screen.getByLabelText('Precision') as HTMLSelectElement;
    expect(precision.disabled).toBe(true);
    expect(precision.value).toBe('fp32');

    await user.selectOptions(hardware, 'openvino:8680677d060000000002000000000000');
    await waitFor(() =>
      expect(within(settingsDialog).queryByText(/DirectML has not been tested/)).toBeNull(),
    );
    expect(
      within(settingsDialog).getByText(/checks once that it gives the same result as the CPU/),
    ).toBeTruthy();
    await openPlanSheet(user);
    expect((screen.getByLabelText('Precision') as HTMLSelectElement).disabled).toBe(true);
  });

  it('shows the CPU and starts on it when the saved GPU is no longer listed', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.device_id = 'cuda:7';
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Upscale & restore$/);
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Performance' }));
    const hardware = within(settingsDialog).getByLabelText('Hardware') as HTMLSelectElement;
    expect(hardware.selectedOptions[0]?.textContent).toBe('CPU');
    await user.click(within(settingsDialog).getByRole('button', { name: 'Done' }));
    await user.click(screen.getByRole('button', { name: 'Upscale selected' }));
    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(expect.objectContaining({ device: 'cpu' }));
    // The saved choice stays until the user makes another: the GPU may be plugged in again.
    expect(api.saveSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ device_id: 'cuda:7' }),
    );
  });

  function gpu(id: string, type: string, name: string): DeviceInfo {
    return {
      id,
      type,
      name,
      total_memory: 8 * 1024 ** 3,
      free_memory: 6 * 1024 ** 3,
      supports_fp16: false,
      is_integrated: true,
      recommended_tile_sizes: [64, 128],
    };
  }

  it('groups a GPU reached through DirectML and OpenVINO under its name, with its routes as options', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.capabilities.devices = [
      ...snapshot.capabilities.devices.filter((device) => device.id === 'cpu'),
      gpu('directml:0', 'directml', 'Intel(R) UHD Graphics 620 (DirectML)'),
      // OpenVINO names the same GPU with its own (iGPU) tag.
      gpu('openvino:8680', 'openvino', 'Intel(R) UHD Graphics 620 (iGPU) (OpenVINO)'),
      gpu('openvino:80d1', 'npu', 'Intel(R) AI Boost (NPU)'),
      gpu('directml:1', 'directml', 'NVIDIA GeForce RTX 3060 (DirectML)'),
    ];
    snapshot.settings.device_id = 'directml:0';
    snapshot.settings.selected_model_id = 'span_photo_x4';
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Upscale & restore$/);
    // The status line names the GPU as the menu and the engine do.
    expect(document.querySelector('.status-detail')?.textContent).toMatch(
      /· Intel UHD Graphics 620 \(DirectML\)$/,
    );

    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Performance' }));
    const hardware = within(settingsDialog).getByLabelText('Hardware') as HTMLSelectElement;

    const groups = [...hardware.querySelectorAll('optgroup')];
    expect(groups.map((group) => group.label)).toEqual(['Intel UHD Graphics 620']);
    expect([...groups[0].querySelectorAll('option')].map((option) => option.textContent)).toEqual([
      'Intel UHD Graphics 620 · DirectML',
      'Intel UHD Graphics 620 · OpenVINO',
    ]);
    // A device reached one way is one entry; the NPU keeps its tag.
    for (const name of ['CPU', 'NVIDIA GeForce RTX 3060', 'Intel AI Boost (NPU)']) {
      expect(within(hardware).getByRole('option', { name })).toBeTruthy();
    }
    expect(hardware.selectedOptions[0].textContent).toBe('Intel UHD Graphics 620 · DirectML');
    expect(hardware.title).toBe('Intel UHD Graphics 620 · DirectML');

    await user.selectOptions(hardware, 'openvino:8680');
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ device_id: 'openvino:8680' }),
      ),
    );
    expect(hardware.title).toBe('Intel UHD Graphics 620 · OpenVINO');
    await user.selectOptions(hardware, 'openvino:80d1');
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ device_id: 'openvino:80d1' }),
      ),
    );
    expect(within(settingsDialog).getByText(/compiles a model once for the NPU/)).toBeTruthy();
  });

  it('keeps short Hardware entries without optgroups in Settings', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Performance' }));
    const hardware = within(settingsDialog).getByLabelText('Hardware') as HTMLSelectElement;
    expect(hardware.querySelector('optgroup')).toBeNull();
  });

  it('keeps the engine route note for the whole job, through a snapshot refresh', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    await mountWith(snapshot);
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
    const note =
      'RealESRGAN runs on Intel UHD Graphics 620 (DirectML), but slower than on the CPU here (412 ms against 288 ms per tile).';
    callback({ type: 'job_started', data: { job_id: 'job-1' } });
    callback({ type: 'engine_status', data: { job_id: 'job-1', message: note } });
    expect(await screen.findByText(note)).toBeTruthy();
    callback({
      type: 'progress',
      data: { job_id: 'job-1', percentage: 12, completed_tiles: 3, total_tiles: 25 },
    });
    await waitFor(() =>
      expect(document.querySelector('.status-detail')?.textContent).toContain('3 of 25 tiles'),
    );
    expect(document.querySelector('.status-note')?.textContent).toBe(note);

    // The host keeps the note in its snapshot (worker.rs), so a refresh does not drop it.
    const running = structuredClone(snapshot);
    Object.assign(running.runtime, {
      active_job_id: 'job-1',
      status_title: 'Enhancing',
      status_detail: '4 of 25 tiles',
      progress: 16,
      hardware_note: note,
    });
    api.refreshSnapshot.mockResolvedValue(running);
    const refresh = api.listenForStateChange.mock.calls[0][0] as () => void;
    refresh();
    await waitFor(() =>
      expect(document.querySelector('.status-detail')?.textContent).toContain('4 of 25 tiles'),
    );
    expect(document.querySelector('.status-note')?.textContent).toBe(note);

    api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    callback({ type: 'job_completed', data: { job_id: 'job-1', output_path: '/out.png' } });
    await waitFor(() => expect(document.querySelector('.status-note')).toBeNull());
  });

  it('shows the last failure above Advanced and offers Retry on CPU when a GPU caused it', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.capabilities.devices.push(
      gpu('directml:0', 'directml', 'Intel(R) UHD Graphics 620 (DirectML)'),
    );
    snapshot.settings.device_id = 'directml:0';
    const user = await mountWith(snapshot);
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;

    callback({ type: 'job_started', data: { job_id: 'job-1' } });
    callback({
      type: 'job_failed',
      data: { job_id: 'job-1', error_message: 'The output folder is not writable.' },
    });
    let failure = await screen.findByRole('alert', { name: 'Last error' });
    expect(failure.textContent).toContain('The output folder is not writable.');
    // Not a GPU's fault: the CPU would not help.
    expect(within(failure).queryByRole('button', { name: 'Retry on CPU' })).toBeNull();

    const rejection =
      'RealESRGAN gives different results on Intel UHD Graphics 620 (DirectML) than on the CPU, so LocalSR does not use that GPU for this model. Select the CPU for it.';
    callback({ type: 'job_started', data: { job_id: 'job-2' } });
    await waitFor(() => expect(screen.queryByRole('alert', { name: 'Last error' })).toBeNull());
    callback({ type: 'job_failed', data: { job_id: 'job-2', error_message: rejection } });
    failure = await screen.findByRole('alert', { name: 'Last error' });
    expect(failure.textContent).toContain('Select the CPU for it.');
    await user.click(within(failure).getByRole('button', { name: 'Retry on CPU' }));
    await waitFor(() => expect(api.retryJobOnCpu).toHaveBeenCalledWith('job-2'));
    // Must NOT change the app-wide hardware default or open Settings.
    expect(api.saveSettings).not.toHaveBeenCalledWith(
      expect.objectContaining({ device_id: 'cpu' }),
    );
    expect(screen.queryByRole('dialog', { name: 'Settings' })).toBeNull();
    await waitFor(() => expect(screen.queryByRole('alert', { name: 'Last error' })).toBeNull());
  });

  const driverReset =
    'The graphics driver stopped Intel UHD Graphics 620 (DirectML) while it ran RealESRGAN (device removed or reset). The engine restarts with a fresh device; if this repeats, choose a smaller tile or the CPU.';

  async function failOnTheGpu(): Promise<(message: WorkerEnvelope) => void> {
    // The newest mount's listener.
    const callback = api.listenForWorker.mock.lastCall![0] as (message: WorkerEnvelope) => void;
    callback({ type: 'job_started', data: { job_id: 'job-1' } });
    callback({
      type: 'job_failed',
      data: { job_id: 'job-1', error_message: driverReset, restart_required: true },
    });
    await screen.findByRole('alert', { name: 'Last error' });
    return callback;
  }

  function onTheGpu(): AppSnapshot {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.capabilities.devices.push(
      gpu('directml:0', 'directml', 'Intel(R) UHD Graphics 620 (DirectML)'),
    );
    snapshot.settings.device_id = 'directml:0';
    snapshot.settings.task = 'upscale';
    return snapshot;
  }

  it('keeps a GPU failure and its Retry on CPU button while the engine restarts after it', async () => {
    const snapshot = onTheGpu();
    await mountWith(snapshot);
    const callback = await failOnTheGpu();
    // The host restarts the engine for a driver reset: its snapshot says so, then Ready.
    const refresh = api.listenForStateChange.mock.calls[0][0] as () => void;
    for (const [worker, title, detail] of [
      ['starting', 'Starting engine', 'The inference process is loading.'],
      ['ready', 'Ready', 'The isolated inference engine is ready.'],
    ]) {
      const restarted = structuredClone(snapshot);
      Object.assign(restarted.runtime, { worker, status_title: title, status_detail: detail });
      api.refreshSnapshot.mockResolvedValue(restarted);
      refresh();
      await waitFor(() =>
        expect(document.querySelector('.status-copy strong')?.textContent ?? '').toMatch(
          new RegExp(`^${title}`),
        ),
      );
    }
    const failure = screen.getByRole('alert', { name: 'Last error' });
    expect(failure.textContent).toContain('device removed or reset');
    expect(within(failure).getByRole('button', { name: 'Retry on CPU' })).toBeTruthy();
    // The next job starts without it.
    callback({ type: 'job_started', data: { job_id: 'job-2' } });
    await waitFor(() => expect(screen.queryByRole('alert', { name: 'Last error' })).toBeNull());
  });

  it('re-queues only the failed job on Retry on CPU without changing device_id or opening anything else', async () => {
    const open = await mountWith(onTheGpu());
    await chooseTask(open, /^Upscale & restore$/);
    await failOnTheGpu();
    const failure = screen.getByRole('alert', { name: 'Last error' });
    await open.click(within(failure).getByRole('button', { name: 'Retry on CPU' }));
    await waitFor(() => expect(api.retryJobOnCpu).toHaveBeenCalledWith('job-1'));
    expect(api.saveSettings).not.toHaveBeenCalledWith(
      expect.objectContaining({ device_id: 'cpu' }),
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  describe('Try again for an engine the host gave up on', () => {
    async function mountStatus(snapshot: AppSnapshot): Promise<ReturnType<typeof userEvent.setup>> {
      api.bootstrap.mockResolvedValue(structuredClone(snapshot));
      api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
      render(App);
      await waitFor(() =>
        expect(document.querySelector('.status-copy strong')?.textContent).toBe(
          snapshot.runtime.status_title,
        ),
      );
      return userEvent.setup();
    }

    function givenUp(): AppSnapshot {
      const snapshot = readySnapshot([image('photo', true)]);
      Object.assign(snapshot.runtime, {
        worker: 'failed',
        status_title: 'Engine unavailable',
        status_detail: 'The inference engine repeatedly stopped. Try again, or restart LocalSR.',
        engine_retry: true,
      });
      return snapshot;
    }

    it('offers one button beside the disabled primary action and calls the command', async () => {
      const user = await mountStatus(givenUp());
      const bar = within(document.querySelector('footer.status-bar') as HTMLElement);
      expect(bar.getByText(/Try again, or restart LocalSR\./)).toBeTruthy();
      expect(
        (bar.getByRole('button', { name: /^Upscale selected/ }) as HTMLButtonElement).disabled,
      ).toBe(true);
      const button = bar.getByRole('button', { name: 'Try again' }) as HTMLButtonElement;
      expect(button.className).toBe('button');
      let finish: () => void = () => {};
      api.retryEngine.mockReturnValueOnce(new Promise<void>((resolve) => (finish = resolve)));
      await user.click(button);
      expect(api.retryEngine).toHaveBeenCalledTimes(1);
      expect(button.disabled).toBe(true);
      finish();
      await waitFor(() => expect(button.disabled).toBe(false));
    });

    it('is not offered for any other failed or stopped engine', async () => {
      for (const [worker, title] of [
        ['failed', 'Engine incompatible'],
        ['failed', 'Engine unavailable'],
        ['unavailable', 'Engine stopped'],
        ['starting', 'Starting engine'],
      ]) {
        const snapshot = givenUp();
        Object.assign(snapshot.runtime, { worker, status_title: title, engine_retry: false });
        await mountStatus(snapshot);
        expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
        cleanup();
      }
      await mountWith(readySnapshot([image('photo', true)]));
      expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    });

    it("shows the host's refusal in the dialog", async () => {
      const user = await mountStatus(givenUp());
      api.retryEngine.mockRejectedValueOnce(
        'the inference engine is not waiting to be tried again',
      );
      await user.click(screen.getByRole('button', { name: 'Try again' }));
      const dialog = await screen.findByRole('dialog');
      expect(within(dialog).getByText('Could not restart the engine')).toBeTruthy();
      expect(within(dialog).getByText(/not waiting to be tried again/)).toBeTruthy();
    });
  });

  it('shows the CPU hint only for a task the CPU runs, with the engine ready', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.device_id = 'cpu';
    snapshot.settings.task = 'upscale';
    snapshot.capabilities.npu_hint = { name: 'Intel(R) AI Boost', missing: 'compiler' };
    await mountWith(snapshot);
    expect(screen.getByText(/Runs on the CPU\. The NPU \(Intel AI Boost\)/)).toBeTruthy();
    // The engine stopped: no hint beside its status.
    const refresh = api.listenForStateChange.mock.calls[0][0] as () => void;
    const stopped = structuredClone(snapshot);
    Object.assign(stopped.runtime, {
      worker: 'unavailable',
      status_title: 'Engine unavailable',
      status_detail: 'The inference engine could not start.',
    });
    api.refreshSnapshot.mockResolvedValue(stopped);
    refresh();
    await waitFor(() =>
      expect(document.querySelector('.status-copy strong')?.textContent).toBe('Engine unavailable'),
    );
    expect(screen.queryByText(/Runs on the CPU/)).toBeNull();
    cleanup();

    // Editing needs a GPU: neither the CPU nor this NPU runs it.
    const editing = structuredClone(snapshot);
    editing.settings.task = 'edit';
    await mountWith(editing);
    expect(screen.queryByText(/Runs on the CPU/)).toBeNull();
  });

  it("drops an ended job's route note before a benchmark, even after a forced stop", async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    await mountWith(snapshot);
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
    const note =
      'RealESRGAN runs on Intel UHD Graphics 620 (DirectML), but slower than on the CPU here.';
    callback({ type: 'job_started', data: { job_id: 'job-1' } });
    callback({ type: 'engine_status', data: { job_id: 'job-1', message: note } });
    expect(await screen.findByText(note)).toBeTruthy();
    // A forced cancellation kills the engine: the host's snapshot no longer has the job.
    const stopped = structuredClone(snapshot);
    // It still carried the old job's note (worker.rs did not clear it on that path).
    Object.assign(stopped.runtime, {
      status_title: 'Cancelled',
      status_detail: 'Work stopped; temporary output removed. Restarting engine…',
      hardware_note: note,
    });
    api.refreshSnapshot.mockResolvedValue(stopped);
    const refresh = api.listenForStateChange.mock.calls[0][0] as () => void;
    refresh();
    await waitFor(() => expect(screen.queryByText(note)).toBeNull());
    callback({
      type: 'benchmark_started',
      data: {
        job_id: 'bench-1',
        workload_version: 'localsr-benchmark-v2',
        measured_frame_count: 3,
      },
    });
    await waitFor(() =>
      expect(document.querySelector('.status-copy strong')?.textContent).toMatch(/^Benchmark/),
    );
    expect(screen.queryByText(note)).toBeNull();
  });

  it('says in its row that a queue item could not finish, until a later run finishes', async () => {
    const snapshot = readySnapshot([image('photo', true), image('other')]);
    const failed: JobRecord = {
      id: 'job-1',
      media_id: 'photo',
      media_name: 'photo.png',
      media_kind: 'image',
      status: 'failed',
      progress: 0,
      output_path: '',
      error: 'The graphics driver stopped Intel UHD Graphics 620 (DirectML).',
      created_at: 100,
    };
    snapshot.jobs = [failed];
    await mountWith(snapshot);
    const label = screen.getByText('Could not finish');
    expect(label.getAttribute('title')).toBe(failed.error);
    // Sentence case: the row's state style capitalises single words, not this.
    expect(label.classList.contains('failed')).toBe(true);
    expect(screen.getAllByText('Could not finish')).toHaveLength(1);

    const later = structuredClone(snapshot);
    later.jobs = [
      {
        ...failed,
        id: 'job-2',
        status: 'completed',
        error: '',
        output_path: '/out.png',
        created_at: 200,
      },
      failed,
    ];
    api.refreshSnapshot.mockResolvedValue(later);
    const refresh = api.listenForStateChange.mock.calls[0][0] as () => void;
    refresh();
    await waitFor(() => expect(screen.queryByText('Could not finish')).toBeNull());
  });

  it.each(['mps', 'directml:1', 'xpu:0'])(
    'submits one image with the selected %s backend',
    async (device) => {
      const snapshot = readySnapshot([image('first', true)]);
      if (device !== 'mps') {
        snapshot.capabilities.devices = [
          snapshot.capabilities.devices[1],
          {
            ...snapshot.capabilities.devices[0],
            id: device,
            type: device.split(':')[0],
            name: device.startsWith('directml')
              ? 'Intel(R) Iris(R) Xe Graphics (DirectML)'
              : 'Intel GPU (XPU)',
          },
        ];
      }
      snapshot.settings.device_id = device;
      const user = await mountWith(snapshot);
      await chooseTask(user, /^Upscale & restore$/);

      const start = screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement;
      expect(start.disabled).toBe(false);
      await user.click(start);

      await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
      expect(api.startJobs).toHaveBeenCalledWith(
        expect.objectContaining({
          media_ids: ['first'],
          batch_mode: false,
          task: 'upscale',
          device,
        }),
      );
    },
  );

  describe('start buttons with several inputs', () => {
    const startButtons = () =>
      [...document.querySelectorAll('.status-actions .start')] as HTMLButtonElement[];

    it('offers Only this one, then the primary for all, and starts the chosen scope', async () => {
      const snapshot = readySnapshot([image('first', true), image('second'), image('third')]);
      snapshot.settings.task = 'upscale';
      snapshot.settings.batch_mode = true;
      const user = await mountWith(snapshot);

      expect(startButtons().map((b) => b.textContent?.trim())).toEqual([
        'Only this one',
        'Upscale all 3',
      ]);
      expect(startButtons()[0].classList).not.toContain('primary');
      expect(startButtons()[1].classList).toContain('primary');
      expect(screen.getByRole('button', { name: 'Upscale all 3' }).hasAttribute('title')).toBe(
        false,
      );

      await user.click(screen.getByRole('button', { name: 'Only this one' }));
      await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
      expect(api.startJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ media_ids: ['first'], batch_mode: false }),
      );
      // The scope used is the one File > Start and automatic starts follow afterwards.
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ batch_mode: false }),
      );
    });

    it('starts every compatible item from the primary and stores the scope', async () => {
      const snapshot = readySnapshot([image('first', true), image('second')]);
      snapshot.settings.task = 'upscale';
      snapshot.settings.batch_mode = false;
      const user = await mountWith(snapshot);

      await user.click(screen.getByRole('button', { name: 'Upscale all 2' }));
      await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
      expect(api.startJobs).toHaveBeenCalledWith(
        expect.objectContaining({ media_ids: ['first', 'second'], batch_mode: true }),
      );
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ batch_mode: true }),
      );
    });

    it('tabs from Only this one to the primary, and the primary is the last button', async () => {
      const snapshot = readySnapshot([image('first', true), image('second')]);
      snapshot.settings.task = 'upscale';
      await mountWith(snapshot);
      const buttons = [...document.querySelectorAll('.status-actions button')];
      expect(buttons.at(-1)!.textContent?.trim()).toBe('Upscale all 2');
      expect(buttons.at(-2)!.textContent?.trim()).toBe('Only this one');
    });

    it.each([
      ['denoise', 'Restore all 2', 'Restore selected'],
      ['video', 'Start all 2 videos', 'Start selected video'],
    ] as const)('names the %s task in the primary', async (task, all, single) => {
      const make = task === 'video' ? video : image;
      const snapshot = readySnapshot([make('first', true), make('second')]);
      snapshot.settings.task = task;
      await mountWith(snapshot);
      expect(startButtons().map((b) => b.textContent?.trim())).toEqual(['Only this one', all]);
      cleanup();
      const lone = readySnapshot([make('only', true)]);
      lone.settings.task = task;
      await mountWith(lone);
      expect(startButtons().map((b) => b.textContent?.trim())).toEqual([single]);
    });

    it('says how many inputs are left out and why on the primary', async () => {
      const snapshot = readySnapshot([image('selected', true), image('second'), video('clip')]);
      snapshot.settings.task = 'upscale';
      await mountWith(snapshot);
      const primary = screen.getByRole('button', { name: 'Upscale all 2' });
      expect(primary.getAttribute('title')).toBe(
        '2 image items can be queued with this setup; select the other media type to configure it separately.',
      );
    });

    it('gives one primary button with the explanation when only one item can run', async () => {
      const snapshot = readySnapshot([
        video('selected-video', true),
        image('first-image'),
        image('second-image'),
      ]);
      snapshot.settings.batch_mode = true;
      snapshot.settings.task = 'upscale';
      const user = await mountWith(snapshot);

      expect(startButtons().map((b) => b.textContent?.trim())).toEqual(['Start selected video']);
      const why =
        '1 video item can be queued with this setup; select the other media type to configure it separately.';
      const start = screen.getByRole('button', { name: 'Start selected video' });
      expect(start.getAttribute('title')).toBe(why);
      expect((screen.getByRole('button', { name: 'Edit' }) as HTMLButtonElement).disabled).toBe(
        true,
      );
      expect((screen.getByRole('radio', { name: 'Same size' }) as HTMLButtonElement).disabled).toBe(
        false,
      );
      await user.click(start);
      await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
      expect(api.startJobs).toHaveBeenCalledWith(
        expect.objectContaining({ media_ids: ['selected-video'], task: 'video' }),
      );
    });

    it('keeps Edit to one primary button that explains why', async () => {
      const snapshot = editSnapshot(32);
      snapshot.media = [image('photo', true), image('other')];
      snapshot.settings.task = 'edit';
      await mountWith(snapshot);
      expect(startButtons().map((b) => b.textContent?.trim())).toEqual(['Edit selected']);
      expect(startButtons()[0].getAttribute('title')).toBe(
        'Edit processes only “photo.png”; batches are not supported yet.',
      );
    });

    it('puts the scope after the download note and the note in the tooltip of Only this one', async () => {
      const snapshot = readySnapshot([image('first', true), image('second')]);
      snapshot.settings.task = 'upscale';
      snapshot.settings.quality = 'best';
      snapshot.settings.selected_model_id = 'realplksr_nomoswebphoto_x4';
      snapshot.catalog.models.find(
        (model) => model.model_id === 'realplksr_nomoswebphoto_x4',
      )!.installed = false;
      const user = await mountWith(snapshot);
      const [one, all] = startButtons();
      expect(one.textContent?.trim()).toBe('Only this one');
      expect(one.getAttribute('title')).toMatch(/^Downloads [\d.]+ MB first/);
      expect(all.textContent?.trim()).toMatch(/^Download [\d.]+ MB, then upscale all 2$/);
      await user.click(all);
      await waitFor(() =>
        expect(api.downloadModel).toHaveBeenCalledWith('realplksr_nomoswebphoto_x4', false),
      );
    });

    it('leaves the buttons to the one input as before', async () => {
      await mountWith(readySnapshot([image('only', true)]));
      expect(startButtons().map((b) => b.textContent?.trim())).toEqual(['Upscale selected']);
    });
  });

  it('reconciles a native photo launch with a persisted video task without requiring a preset', async () => {
    const current = readySnapshot();
    current.settings.task = 'video';
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.addMedia.mockImplementation(async () => {
      current.media.push(image('opened-photo', true));
    });
    api.selectMedia.mockImplementation(async (id: string) => {
      current.media.forEach((item) => (item.selected = item.id === id));
    });
    api.saveSettings.mockImplementation(async (settings: UiSettings) => {
      current.settings = structuredClone(settings);
    });
    api.takeLaunchIntents
      .mockResolvedValueOnce([
        { files: ['/private/opened-photo.png'], preset: null, recipe: null, auto_start: false },
      ])
      .mockResolvedValueOnce([]);

    render(App);

    await screen.findAllByText('opened-photo.png');
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ task: 'upscale' })),
    );
    expect(screen.getByRole('button', { name: /^Upscale & restore$/ }).classList).toContain(
      'active',
    );
    expect((screen.getByRole('radio', { name: 'Same size' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ task: 'upscale' }));
  });

  it('hides a status bar button whose count would be 0 and keeps no room for it', async () => {
    const queued = (id: string, status: JobRecord['status']): JobRecord => ({
      id: `job-${id}`,
      media_id: id,
      media_name: `${id}.png`,
      media_kind: 'image',
      status,
      progress: 0,
      output_path: '',
      error: '',
      created_at: 1,
    });
    const snapshot = readySnapshot([image('first', true), image('second')]);
    snapshot.settings.batch_mode = true;
    snapshot.settings.task = 'upscale';
    snapshot.runtime.active_job_id = 'job-first';
    snapshot.jobs = [queued('first', 'running'), queued('second', 'queued')];
    await mountWith(snapshot);
    expect(screen.queryByRole('button', { name: /^Add \d+ to queue$/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Cancel queue' })).toBeTruthy();
    expect(
      [...document.querySelectorAll('.status-actions .button')].map((b) => b.textContent),
    ).toEqual(['Cancel runs for this image', 'Cancel queue']);
    cleanup();

    // Between two jobs of a queue every item is queued: there is nothing to start.
    snapshot.runtime.active_job_id = '';
    snapshot.jobs = [queued('first', 'queued'), queued('second', 'queued')];
    await mountWith(snapshot);
    expect(screen.queryByRole('button', { name: /^Start \d+ items?$/ })).toBeNull();
  });

  describe('adding to a running queue', () => {
    const job = (n: number, status: JobRecord['status']): JobRecord => ({
      id: `job-${n}`,
      media_id: `p${n}`,
      media_name: `p${n}.png`,
      media_kind: 'image',
      status,
      progress: status === 'running' ? 40 : 0,
      output_path: '',
      error: status === 'failed' ? 'broke' : '',
      created_at: 1_000, // one enqueue: the jobs of a batch are created together
    });
    // p1-p3 finished, p4 runs, p5-p7 wait: the queue of one run.
    const running = (extra: MediaItem[] = [], selected = 'p4', batch = true) => {
      const media = [1, 2, 3, 4, 5, 6, 7].map((n) => image(`p${n}`, `p${n}` === selected));
      const snapshot = readySnapshot([...media, ...extra]);
      for (const item of snapshot.media) item.selected = item.id === selected;
      snapshot.settings.batch_mode = batch;
      snapshot.settings.task = 'upscale';
      snapshot.runtime.active_job_id = 'job-4';
      snapshot.jobs = [
        job(7, 'queued'),
        job(6, 'queued'),
        job(5, 'queued'),
        job(4, 'running'),
        job(3, 'completed'),
        job(2, 'completed'),
        job(1, 'completed'),
      ];
      return snapshot;
    };
    const addButton = () => screen.queryByRole('button', { name: /^Add (selected|\d+) to queue$/ });

    it('offers nothing while every input has a job in this run', async () => {
      await mountWith(running());
      expect(addButton()).toBeNull();
    });

    it('counts only the inputs added after the queue started', async () => {
      await mountWith(running([image('n1'), image('n2')]));
      expect(addButton()?.textContent).toBe('Add 2 to queue');
    });

    it('does not count an input whose job in this run failed or was cancelled', async () => {
      const snapshot = running([image('n1'), image('n2')]);
      snapshot.jobs[6] = job(1, 'failed');
      snapshot.jobs[5] = job(2, 'cancelled');
      await mountWith(snapshot);
      expect(addButton()?.textContent).toBe('Add 2 to queue');
    });

    it('counts a finished picture from an earlier run as old, but not as new input once it ran', async () => {
      const snapshot = running([image('n1')]);
      snapshot.jobs.push({ ...job(8, 'completed'), media_id: 'n1', created_at: 10 });
      await mountWith(snapshot);
      // The earlier run's job is not part of this run, so the picture is still offered.
      expect(addButton()?.textContent).toBe('Add 1 to queue');
    });

    it('offers Add selected to queue only for a selected input that is new', async () => {
      await mountWith(running([image('n1')], 'p2', false));
      expect(addButton()).toBeNull();
      cleanup();
      await mountWith(running([image('n1')], 'p4', false));
      expect(addButton()).toBeNull();
      cleanup();
      await mountWith(running([image('n1')], 'p6', false));
      expect(addButton()).toBeNull();
      cleanup();
      await mountWith(running([image('n1')], 'n1', false));
      expect(addButton()?.textContent).toBe('Add selected to queue');
    });

    it('shows the button when new inputs arrive while the queue runs', async () => {
      const current = running();
      api.bootstrap.mockResolvedValue(structuredClone(current));
      api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
      render(App);
      await screen.findByRole('button', { name: 'Cancel queue' });
      expect(addButton()).toBeNull();
      current.media.push(image('n1'), image('n2'));
      await waitFor(() => expect(api.listenForStateChange).toHaveBeenCalled());
      (api.listenForStateChange.mock.calls[0][0] as () => void)();
      await waitFor(() => expect(addButton()?.textContent).toBe('Add 2 to queue'));
    });

    it('leaves the idle counts alone once the queue has ended', async () => {
      const snapshot = running();
      snapshot.runtime.active_job_id = '';
      snapshot.jobs = snapshot.jobs.map((entry) => ({ ...entry, status: 'completed' }));
      await mountWith(snapshot);
      expect(screen.getByRole('button', { name: 'Only this one' })).toBeTruthy();
      expect(screen.getByRole('button', { name: /all 7/i })).toBeTruthy();
    });
  });

  it('has no Process line and keeps the scope buttons out of the Media pane', async () => {
    const snapshot = readySnapshot([image('first', true), image('second')]);
    snapshot.settings.task = 'upscale';
    await mountWith(snapshot);
    expect(document.querySelector('.process-scope')).toBeNull();
    expect(document.querySelector('.media-pane')!.textContent).not.toMatch(/Process|All 2/);
    expect(screen.queryByRole('button', { name: 'Selected' })).toBeNull();
  });

  it('shows no scope buttons while a queue runs', async () => {
    const snapshot = readySnapshot([image('first', true), image('second')]);
    snapshot.settings.task = 'upscale';
    snapshot.runtime.active_job_id = 'running-job';
    await mountWith(snapshot);
    expect(screen.queryByRole('button', { name: 'Only this one' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Upscale all/ })).toBeNull();
  });

  it('keeps controls locked through cancellation and unlocks after the worker stops', async () => {
    const current = readySnapshot([video('clip', true)]);
    current.settings.task = 'video';
    current.settings.selected_model_id = 'span_photo_x4';
    current.runtime.active_job_id = 'running-video';
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.cancelJobs.mockImplementation(async () => {
      current.runtime.status_title = 'Cancelling';
    });
    render(App);
    const user = userEvent.setup();
    const cancel = await screen.findByRole('button', { name: 'Cancel' });
    await user.click(cancel);
    expect(screen.getByRole('button', { name: 'Stopping…' }).matches(':disabled')).toBe(true);
    await openPlanSheet(user);
    expect(screen.getByRole('button', { name: 'Change the upscaler' }).matches(':disabled')).toBe(
      true,
    );
    current.runtime.active_job_id = '';
    current.runtime.status_title = 'Cancelled';
    const refresh = api.listenForStateChange.mock.calls[0][0] as () => void;
    refresh();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Change the upscaler' }).matches(':disabled')).toBe(
        false,
      ),
    );
    expect(api.cancelJobs).toHaveBeenCalledTimes(1);
  });

  it('keeps the settings locked while the next queued job has not shown up yet', async () => {
    const current = readySnapshot([image('first', true), image('second')]);
    current.settings.task = 'upscale';
    const queued = (id: string, media: string, status: 'running' | 'queued') => ({
      id,
      media_id: media,
      media_name: `${media}.png`,
      media_kind: 'image' as const,
      status,
      progress: 0,
      output_path: '',
      error: '',
      created_at: 1,
    });
    current.runtime.active_job_id = 'job-1';
    current.jobs = [queued('job-1', 'first', 'running'), queued('job-2', 'second', 'queued')];
    await mountWith(current);
    const listener = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
    // job-1 ends; the host already runs job-2, but the window learns of it with its next refresh.
    listener({ type: 'job_completed', data: { job_id: 'job-1', output_path: '/out/first.png' } });
    await waitFor(() =>
      expect(document.querySelector('.status-copy strong')?.textContent ?? '').toMatch(/^Complete/),
    );
    expect(screen.getByRole('button', { name: /^Best/ }).matches(':disabled')).toBe(true);
  });

  describe('after Cancel queue, while the engine still stops', () => {
    const lane = (id: string, media: string, status: JobRecord['status'], created = 10) => ({
      id,
      media_id: media,
      media_name: `${media}.png`,
      media_kind: 'image' as const,
      status,
      progress: 40,
      output_path: '',
      error: '',
      created_at: created,
      elapsed_seconds: status === 'completed' ? 31 : 0,
    });
    const stopping = () => {
      const current = readySnapshot(['a', 'b', 'c', 'd', 'e'].map((id, i) => image(id, i === 0)));
      current.settings.task = 'upscale';
      current.settings.batch_mode = true;
      current.runtime.active_job_id = 'j2';
      current.runtime.active_batch_job_ids = ['j2', 'j3'];
      current.runtime.status_title = 'Processing';
      current.runtime.status_detail = 'Finishing the other photos of this batch. · ETA ≈ 9s';
      current.runtime.progress = 40;
      current.jobs = [
        lane('j1', 'a', 'completed'),
        lane('j2', 'b', 'cancelling'),
        lane('j3', 'c', 'cancelling'),
        lane('j4', 'd', 'cancelled'),
        lane('j5', 'e', 'cancelled'),
      ];
      return current;
    };
    const strong = () => document.querySelector('.status-copy strong')?.textContent;
    const mountNow = async (current: AppSnapshot) => {
      api.bootstrap.mockResolvedValue(structuredClone(current));
      api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
      render(App);
    };
    const rowTexts = () =>
      [...document.querySelectorAll('.media-pane .media-row')].map((row) => row.textContent);

    it('shows Stopping in the bar, the buttons and the rows of a 2-lane batch', async () => {
      await mountNow(stopping());
      await waitFor(() => expect(strong()).toBe('Stopping…'));
      const detail = document.querySelector('.status-detail')!.textContent;
      expect(detail).toContain('Stopping 2 photos');
      expect(detail).toContain('up to 8 s');
      expect(document.querySelector('.status-bar')!.textContent).not.toMatch(/ETA|≈|40%/);
      expect(screen.queryByRole('button', { name: 'Cancel queue' })).toBeNull();
      expect(screen.getByRole('button', { name: 'Stopping…' }).matches(':disabled')).toBe(true);
      expect(screen.queryByRole('button', { name: /^Add \d+ to queue/ })).toBeNull();
      expect(screen.queryByRole('button', { name: /Only this one|all \d+/i })).toBeNull();
      const rows = rowTexts();
      expect(rows[0]).toContain('Done · 31 s');
      expect(rows[1]).toContain('Stopping…');
      expect(rows[2]).toContain('Stopping…');
      expect(rows[3]).not.toMatch(/Stopping|Queued|Running|Done/);
      expect(rows[4]).not.toMatch(/Stopping|Queued|Running|Done/);
    });

    it('says what a single stopping job waits for', async () => {
      const current = stopping();
      current.runtime.active_batch_job_ids = [];
      current.jobs = [lane('j2', 'b', 'cancelling')];
      await mountNow(current);
      await waitFor(() => expect(strong()).toBe('Stopping…'));
      expect(document.querySelector('.status-detail')!.textContent).toContain(
        'Waiting for the engine to stop (up to 8 s).',
      );
      expect(screen.getByRole('button', { name: 'Stopping…' }).matches(':disabled')).toBe(true);
    });

    it('returns to idle with one Stopped line and the idle buttons', async () => {
      const current = stopping();
      api.bootstrap.mockResolvedValue(structuredClone(current));
      api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
      render(App);
      await waitFor(() => expect(strong()).toBe('Stopping…'));
      current.runtime.active_job_id = '';
      current.runtime.active_batch_job_ids = [];
      current.runtime.status_title = 'Cancelled';
      current.runtime.status_detail = 'Temporary output was cleaned up.';
      current.jobs = current.jobs.map((entry) => ({
        ...entry,
        status: entry.status === 'completed' ? 'completed' : 'cancelled',
      }));
      (api.listenForStateChange.mock.calls[0][0] as () => void)();
      await waitFor(() => expect(strong()).toBe('Cancelled'));
      expect(document.querySelector('.status-detail')!.textContent).toBe(
        'Stopped. 1 of 5 finished.',
      );
      expect(screen.queryByRole('button', { name: 'Stopping…' })).toBeNull();
    });
  });

  it('asks before Clear: Cancel keeps the list, confirming clears it', async () => {
    const snapshot = readySnapshot([image('first', true), image('second', false)]);
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.getByRole('dialog').textContent).toContain('Clear all 2 items from the list?');
    expect(screen.getByRole('dialog').textContent).toContain('not deleted');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.clearMedia).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Clear' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Clear' }));
    await waitFor(() => expect(api.clearMedia).toHaveBeenCalledTimes(1));
  });

  it('appends from the main Add Media action even in single mode', async () => {
    const snapshot = readySnapshot([image('first', true)]);
    api.chooseMediaFiles.mockResolvedValueOnce(['/private/second.png']);
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Add Media…' }));

    await waitFor(() => expect(api.addMedia).toHaveBeenCalledWith(['/private/second.png'], false));
  });

  it('collects separate file-manager launches into one explicit batch', async () => {
    const current = readySnapshot();
    current.settings.task = 'upscale';
    current.settings.selected_model_id = current.catalog.models.find(
      (model) => model.native_scale > 1 && !model.purposes.includes('face'),
    )!.model_id;
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.addMedia.mockImplementation(async (paths: string[]) => {
      for (const path of paths) {
        const id = path.split('/').at(-1)!.replace('.png', '');
        current.media.push(image(id, current.media.length === 0));
      }
    });
    api.selectMedia.mockImplementation(async (id: string) => {
      current.media.forEach((item) => (item.selected = item.id === id));
    });
    api.saveSettings.mockImplementation(async (settings: UiSettings) => {
      current.settings = structuredClone(settings);
    });
    api.takeLaunchIntents
      .mockResolvedValueOnce([
        { files: ['/private/first.png'], preset: null, recipe: null, auto_start: true },
        { files: ['/private/second.png'], preset: null, recipe: null, auto_start: true },
      ])
      .mockResolvedValueOnce([]);

    render(App);

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1), { timeout: 2_000 });
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({
        media_ids: ['first', 'second'],
        batch_mode: true,
        task: 'upscale',
      }),
    );
    expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ batch_mode: true }));
  });

  it('keeps separately forwarded mixed media but cancels automatic processing', async () => {
    const current = readySnapshot();
    current.settings.task = 'upscale';
    current.settings.selected_model_id = current.catalog.models.find(
      (model) => model.native_scale > 1 && !model.purposes.includes('face'),
    )!.model_id;
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.addMedia.mockImplementation(async (paths: string[]) => {
      for (const path of paths) {
        const filename = path.split('/').at(-1)!;
        const id = filename.replace(/\.(png|mp4)$/, '');
        current.media.push(
          filename.endsWith('.mp4')
            ? video(id, current.media.length === 0)
            : image(id, current.media.length === 0),
        );
      }
    });
    api.selectMedia.mockImplementation(async (id: string) => {
      current.media.forEach((item) => (item.selected = item.id === id));
    });
    api.saveSettings.mockImplementation(async (settings: UiSettings) => {
      current.settings = structuredClone(settings);
    });
    api.takeLaunchIntents
      .mockResolvedValueOnce([
        { files: ['/private/photo.png'], preset: null, recipe: null, auto_start: true },
        { files: ['/private/clip.mp4'], preset: null, recipe: null, auto_start: true },
      ])
      .mockResolvedValueOnce([]);

    render(App);

    expect(await screen.findByRole('heading', { name: 'Mixed media needs two runs' })).toBeTruthy();
    expect(screen.getByText(/They remain in the media list/i)).toBeTruthy();
    expect(api.startJobs).not.toHaveBeenCalled();
    expect(screen.getByText('photo.png')).toBeTruthy();
    expect(screen.getAllByText('clip.mp4').length).toBeGreaterThan(0);
  });

  it('never compares newly selected media with an unrelated previous result', async () => {
    const snapshot = readySnapshot([image('replacement', true)]);
    snapshot.runtime.last_output_path = '/private/old-output.png';
    snapshot.runtime.result_preview_data_url = 'data:image/jpeg;base64,old-result';

    await mountWith(snapshot);

    expect(screen.queryByText('Enhanced')).toBeNull();
    expect(screen.queryByLabelText('Before and after comparison')).toBeNull();
  });

  it("opens and reveals the selected item's result, not the last one written", async () => {
    const snapshot = readySnapshot([image('first', true), image('second')]);
    snapshot.runtime.last_output_path = '/private/second-output.png';
    const completed = (id: string, created_at: number) => ({
      id: `job-${id}`,
      media_id: id,
      media_name: `${id}.png`,
      media_kind: 'image' as const,
      status: 'completed' as const,
      progress: 100,
      output_path: `/private/${id}-output.png`,
      error: '',
      created_at,
    });
    snapshot.jobs = [completed('second', 2), completed('first', 1)];

    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(api.openResult).toHaveBeenLastCalledWith('/private/first-output.png');
    await user.click(screen.getByRole('button', { name: 'Reveal' }));
    expect(api.revealResult).toHaveBeenLastCalledWith('/private/first-output.png');
  });

  it('hides Open and Reveal while the selected item has no result', async () => {
    const snapshot = readySnapshot([image('fresh', true), image('done')]);
    snapshot.runtime.last_output_path = '/private/done-output.png';
    snapshot.jobs = [
      {
        id: 'job-done',
        media_id: 'done',
        media_name: 'done.png',
        media_kind: 'image',
        status: 'completed',
        progress: 100,
        output_path: '/private/done-output.png',
        error: '',
        created_at: 1,
      },
    ];

    await mountWith(snapshot);

    expect(screen.queryByRole('button', { name: 'Open' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reveal' })).toBeNull();
  });

  it('shows the comparison only when the completed result belongs to the selected media', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 800,
      height: 600,
      top: 0,
      left: 0,
      right: 800,
      bottom: 600,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    const snapshot = readySnapshot([image('first', true)]);
    snapshot.runtime.last_output_path = '/private/first-output.png';
    snapshot.runtime.result_preview_data_url = 'data:image/jpeg;base64,first-result';
    snapshot.jobs = [
      {
        id: 'job-first',
        media_id: 'first',
        media_name: 'first.png',
        media_kind: 'image',
        status: 'completed',
        progress: 100,
        output_path: '/private/first-output.png',
        error: '',
        created_at: 1,
      },
    ];

    await mountWith(snapshot);

    expect(screen.getByText('Enhanced')).toBeTruthy();
    const divider = screen.getByRole('slider', { name: 'Before and after comparison' });
    await fireEvent.load(screen.getByAltText('Preview of first.png'));
    await waitFor(() =>
      expect(
        Number.parseFloat(document.querySelector<HTMLElement>('.image-stage')?.style.width ?? '0'),
      ).toBeGreaterThan(700),
    );

    await fireEvent.pointerDown(divider, {
      button: 0,
      pointerId: 1,
      clientX: 400,
      clientY: 300,
    });
    await fireEvent.pointerMove(divider, { pointerId: 1, clientX: 600, clientY: 300 });
    await fireEvent.pointerUp(divider, { pointerId: 1, clientX: 600, clientY: 300 });
    await waitFor(() => expect(Number(divider.getAttribute('aria-valuenow'))).toBe(78));

    await fireEvent.keyDown(divider, { key: 'ArrowLeft' });
    expect(Number(divider.getAttribute('aria-valuenow'))).toBe(77);
  });

  it('shows the photo of a batch that started last and keeps it while an earlier one saves', async () => {
    class DecodedImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      private value = '';

      set src(value: string) {
        this.value = value;
        queueMicrotask(() => this.onload?.());
      }

      get src(): string {
        return this.value;
      }
    }
    vi.stubGlobal('Image', DecodedImage);
    const source = image('large-batch', true);
    source.width = 3024;
    source.height = 1964;
    const snapshot = readySnapshot([source]);
    snapshot.runtime.active_job_id = 'a';
    snapshot.runtime.active_batch_job_ids = ['a', 'b'];
    await mountWith(snapshot);
    const listener = api.listenForWorker.mock.calls[0]?.[0] as (message: WorkerEnvelope) => void;
    const tile = (jobId: string, jpeg: string): WorkerEnvelope => ({
      type: 'tile_update',
      data: {
        job_id: jobId,
        phase: 'completed',
        output_x: 0,
        output_y: 0,
        output_width: 256,
        output_height: 256,
        image_width: 12096,
        image_height: 7856,
        jpeg_base64: jpeg,
      },
    });
    listener({ type: 'job_started', data: { job_id: 'a' } });
    listener({ type: 'job_started', data: { job_id: 'b' } });
    listener(tile('b', 'tile-of-b'));
    const canvas = screen.getByLabelText('Progressive tiled preview') as HTMLCanvasElement;
    const drawImage = vi.mocked(canvas.getContext('2d')!.drawImage);
    await waitFor(() => expect(drawImage).toHaveBeenCalled());
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '1:1' }));
    const zoom = screen.getByTitle(/Dynamic maximum/).textContent;
    const draws = drawImage.mock.calls.length;
    // A late tile and the save of the earlier photo leave the shown photo alone.
    listener(tile('a', 'late-tile-of-a'));
    listener({ type: 'job_completed', data: { job_id: 'a', output_path: '/output/a.png' } });
    await new Promise((resolve) => setTimeout(resolve, 120));
    expect(drawImage.mock.calls.length).toBe(draws);
    expect(screen.getByTitle(/Dynamic maximum/).textContent).toBe(zoom);
    expect(screen.getByRole('button', { name: 'Cancel queue' })).toBeTruthy();
  });

  it('keeps the progress of the photo on screen when a photo saving off screen reports too', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.runtime.active_job_id = 'b';
    snapshot.runtime.active_batch_job_ids = ['a', 'b'];
    await mountWith(snapshot);
    const listener = api.listenForWorker.mock.calls[0]?.[0] as (message: WorkerEnvelope) => void;
    listener({
      type: 'progress',
      data: { job_id: 'b', completed_tiles: 4, total_tiles: 10, percentage: 40 },
    });
    // The earlier photo's last progress lands in the same display frame.
    listener({
      type: 'progress',
      data: { job_id: 'a', completed_tiles: 10, total_tiles: 10, percentage: 100 },
    });
    expect(await screen.findByText('4 of 10 tiles')).toBeTruthy();
  });

  describe('Outputs while a queue runs', () => {
    function runningQueue(ids: string[], batch: boolean) {
      const snapshot = readySnapshot(ids.map((id, index) => image(id, index === 0)));
      snapshot.runtime.active_job_id = ids[0];
      snapshot.runtime.active_batch_job_ids = batch ? ids : [];
      snapshot.jobs = ids.map((id, index) => ({
        id: `job-${id}`,
        media_id: id,
        media_name: `${id}.png`,
        media_kind: 'image' as const,
        status: index === 0 ? ('running' as const) : ('queued' as const),
        progress: 0,
        output_path: '',
        error: '',
        created_at: 10 - index,
      }));
      snapshot.runtime.active_job_id = `job-${ids[0]}`;
      snapshot.runtime.active_batch_job_ids = batch ? ids.map((id) => `job-${id}`) : [];
      return snapshot;
    }

    /** The host's state after `finished` ended: its row is completed and its history has the result. */
    function finish(snapshot: AppSnapshot, ids: string[], finished: string[]): void {
      const next = structuredClone(snapshot);
      for (const job of next.jobs) {
        if (finished.includes(job.media_id)) {
          job.status = 'completed';
          job.progress = 100;
          job.output_path = `/out/${job.id}.png`;
        }
      }
      api.refreshSnapshot.mockResolvedValue(next);
      api.listAllMediaResults.mockResolvedValue(
        ids
          .filter((id) => finished.includes(id))
          .map((id) => historyOf(id, [row(`job-${id}`, { model_label: 'SPAN x4' })])),
      );
    }

    it.each([
      ['single queue', false],
      ['batch lanes', true],
    ])('lists a finished photo of a running %s at once, without a click', async (_name, batch) => {
      const ids = ['a', 'b', 'c'];
      const snapshot = runningQueue(ids, batch);
      let stateChanged: () => void = () => {};
      api.listenForStateChange.mockImplementation(async (handler) => {
        stateChanged = handler;
        return () => undefined;
      });
      const user = await mountWith(snapshot);
      const listener = api.listenForWorker.mock.calls[0]?.[0] as (m: WorkerEnvelope) => void;
      // The photo saving off screen in a batch, or the one running in a queue, ends while others go on.
      const ended = batch ? 'b' : 'a';
      finish(snapshot, ids, [ended]);
      listener({
        type: 'job_completed',
        data: { job_id: `job-${ended}`, output_path: `/out/job-${ended}.png` },
      });
      stateChanged();
      await user.click(screen.getByRole('tab', { name: /^Outputs/ }));
      const row1 = await screen.findByRole('button', { name: `Show SPAN x4, ${ended}.png` });
      expect(row1.textContent).toContain('SPAN x4');
      expect(screen.getByRole('tab', { name: /^Outputs/ }).textContent).toContain('1');
      // A second one follows the same way.
      const other = batch ? 'a' : 'b';
      finish(snapshot, ids, [ended, other]);
      listener({
        type: 'job_completed',
        data: { job_id: `job-${other}`, output_path: `/out/job-${other}.png` },
      });
      await waitFor(() =>
        expect(screen.getByRole('tab', { name: /^Outputs/ }).textContent).toContain('2'),
      );
    });
    it('keeps listing finished photos while the host reports a change every few milliseconds', async () => {
      // A fast batch changes state faster than one refresh takes: the list must still move on.
      const ids = ['a', 'b', 'c', 'd'];
      const snapshot = runningQueue(ids, true);
      let stateChanged: () => void = () => {};
      api.listenForStateChange.mockImplementation(async (handler) => {
        stateChanged = handler;
        return () => undefined;
      });
      const user = await mountWith(snapshot);
      const listener = api.listenForWorker.mock.calls[0]?.[0] as (m: WorkerEnvelope) => void;
      await user.click(screen.getByRole('tab', { name: /^Outputs/ }));
      api.listAllMediaResults.mockImplementation(async () => {
        const answer = structuredClone(await historiesNow());
        await new Promise((resolve) => setTimeout(resolve, 60));
        return answer;
      });
      let moving = 0;
      const historiesNow = async () =>
        ['b']
          .filter(() => moving > 0)
          .map((id) => historyOf(id, [row(`job-${id}`, { model_label: 'SPAN x4' })]));
      const stream = setInterval(() => {
        // Every tick another photo starts or ends: the job rows keep changing.
        moving += 1;
        const next = structuredClone(snapshot);
        next.jobs.forEach((job, index) => {
          if (job.media_id === 'b') {
            job.status = 'completed';
            job.output_path = '/out/job-b.png';
          } else if (index === moving % next.jobs.length) job.status = 'running';
        });
        api.refreshSnapshot.mockResolvedValue(next);
        stateChanged();
      }, 25);
      try {
        listener({
          type: 'job_completed',
          data: { job_id: 'job-b', output_path: '/out/job-b.png' },
        });
        await waitFor(
          () => expect(screen.getByRole('tab', { name: /^Outputs/ }).textContent).toContain('1'),
          { timeout: 1500 },
        );
      } finally {
        clearInterval(stream);
      }
    });
  });

  it('composites progressive tiles without exposing comparison and keeps 1:1 controls responsive', async () => {
    class DecodedImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      private value = '';

      set src(value: string) {
        this.value = value;
        queueMicrotask(() => this.onload?.());
      }

      get src(): string {
        return this.value;
      }
    }
    vi.stubGlobal('Image', DecodedImage);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 800,
      height: 600,
      top: 0,
      left: 0,
      right: 800,
      bottom: 600,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);

    const source = image('large', true);
    source.width = 3024;
    source.height = 1964;
    const snapshot = readySnapshot([source]);
    snapshot.settings.task = 'upscale';
    await mountWith(snapshot);

    const stage = document.querySelector<HTMLElement>('.image-stage');
    await waitFor(() => expect(Number.parseFloat(stage?.style.width ?? '0')).toBeCloseTo(744));
    expect(Number.parseFloat(stage?.style.height ?? '0')).toBeGreaterThan(480);
    // Pan rides inside the transform so it animates with zoom without jitter.
    expect(stage?.style.left).toBe('50%');
    expect(stage?.style.top).toBe('50%');
    expect(stage?.style.transform).toBe('translate(calc(-50% + 0px), calc(-50% + 0px)) scale(1)');

    const listener = api.listenForWorker.mock.calls[0]?.[0] as
      ((message: WorkerEnvelope) => void) | undefined;
    expect(listener).toBeTypeOf('function');
    listener?.({ type: 'job_started', data: { job_id: 'job-progressive' } });
    listener?.({
      type: 'tile_update',
      data: {
        job_id: 'job-progressive',
        phase: 'started',
        output_x: 0,
        output_y: 0,
        output_width: 256,
        output_height: 256,
        image_width: 12096,
        image_height: 7856,
      },
    });
    listener?.({
      type: 'tile_update',
      data: {
        job_id: 'job-progressive',
        phase: 'completed',
        output_x: 0,
        output_y: 0,
        output_width: 256,
        output_height: 256,
        image_width: 12096,
        image_height: 7856,
        jpeg_base64: 'completed-tile',
      },
    });

    const canvas = screen.getByLabelText('Progressive tiled preview');
    await waitFor(() => expect(canvas.classList.contains('visible')).toBe(true));
    const context = (canvas as HTMLCanvasElement).getContext('2d')!;
    const drawImage = vi.mocked(context.drawImage);
    await waitFor(() => expect(drawImage).toHaveBeenCalledTimes(2));
    expect(drawImage).toHaveBeenLastCalledWith(expect.any(DecodedImage), 0, 0, 34, 34);
    expect(screen.queryByText('Enhanced')).toBeNull();
    expect(screen.queryByLabelText('Before and after comparison')).toBeNull();

    for (let completed = 1; completed <= 100; completed += 1) {
      listener?.({
        type: 'progress',
        data: {
          job_id: 'job-progressive',
          completed_tiles: completed,
          total_tiles: 100,
          percentage: completed,
        },
      });
    }
    expect(await screen.findByText('100 of 100 tiles')).toBeTruthy();

    await new Promise((resolve) => setTimeout(resolve, 80));
    for (let tileIndex = 1; tileIndex <= 100; tileIndex += 1) {
      listener?.({
        type: 'tile_update',
        data: {
          job_id: 'job-progressive',
          phase: 'completed',
          output_x: tileIndex * 256,
          output_y: 0,
          output_width: 256,
          output_height: 256,
          image_width: 12096,
          image_height: 7856,
          jpeg_base64: `completed-tile-${tileIndex}`,
        },
      });
    }
    await waitFor(() => expect(drawImage).toHaveBeenCalledTimes(3));
    expect(drawImage.mock.calls.length).toBeLessThan(8);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '1:1' }));
    expect(screen.getByTitle(/Dynamic maximum/).textContent).toMatch(/^40[67]%$/);
    expect(screen.getByLabelText('Media comparison canvas').classList.contains('panning')).toBe(
      false,
    );
    listener?.({
      type: 'job_completed',
      data: { job_id: 'job-progressive', output_path: '/output/complete.png' },
    });
    await waitFor(() => expect(screen.getByTitle(/Dynamic maximum/).textContent).toBe('100%'));
    expect(stage?.style.left).toBe('50%');
    expect(stage?.style.top).toBe('50%');
  });

  it('preserves the colourised stage preview across media switching before the upscaler tiles run', async () => {
    class DecodedImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      private value = '';

      set src(value: string) {
        this.value = value;
        queueMicrotask(() => this.onload?.());
      }

      get src(): string {
        return this.value;
      }
    }
    vi.stubGlobal('Image', DecodedImage);
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      fillStyle: '',
      fillRect: vi.fn(),
      clearRect: vi.fn(),
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D);

    const first = image('bw', true);
    first.name = 'bw.png';
    first.width = 1982;
    first.height = 1361;
    first.preview_data_url = 'data:image/jpeg;base64,bw-source';
    const second = image('other', false);
    second.name = 'other.png';
    const snapshot = readySnapshot([first, second]);
    snapshot.settings.task = 'upscale';
    snapshot.jobs = [
      {
        id: 'job-colour-upscale',
        media_id: 'bw',
        media_name: 'bw.png',
        media_kind: 'image',
        status: 'running',
        output_path: '',
        progress: 0,
        error: '',
        created_at: 1,
        model_id: 'span_photo_x4',
      },
    ];
    snapshot.runtime.active_job_id = 'job-colour-upscale';
    api.selectMedia.mockImplementation(async (id: string) => {
      snapshot.media.forEach((item) => (item.selected = item.id === id));
      api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    });
    const user = await mountWith(snapshot);

    const listener = api.listenForWorker.mock.calls[0]?.[0] as
      ((message: WorkerEnvelope) => void) | undefined;
    expect(listener).toBeTypeOf('function');
    listener?.({ type: 'job_started', data: { job_id: 'job-colour-upscale' } });

    // User clicks another photo while stage 0 (colour) completes.
    await user.click(screen.getByRole('button', { name: /^other\.png/ }));
    await waitFor(() => expect(document.title).toContain('other.png'));
    listener?.({
      type: 'live_preview_frame',
      data: {
        job_id: 'job-colour-upscale',
        sequence: 1,
        stage_index: 0,
        preview_kind: 'tile',
        output_x: 0,
        output_y: 0,
        output_width: 1982,
        output_height: 1361,
        image_width: 1982,
        image_height: 1361,
        jpeg_base64: 'colourised-stage0',
      },
    });

    // User switches back to the running photo as stage 1 (upscale) begins.
    await user.click(screen.getByRole('button', { name: /^bw\.png/ }));
    await waitFor(() => expect(document.title).toContain('bw.png'));
    listener?.({
      type: 'tile_update',
      data: {
        job_id: 'job-colour-upscale',
        stage_index: 1,
        phase: 'started',
        active_tile_size: 256,
        output_x: 0,
        output_y: 0,
        output_width: 1024,
        output_height: 1024,
        image_width: 7928,
        image_height: 5444,
      },
    });

    const canvas = screen.getByLabelText('Progressive tiled preview') as HTMLCanvasElement;
    await waitFor(() => expect(canvas.classList.contains('visible')).toBe(true));
    const context = canvas.getContext('2d')!;
    const drawImage = vi.mocked(context.drawImage);
    const fillRect = vi.mocked(context.fillRect);
    await waitFor(() => expect(drawImage).toHaveBeenCalledTimes(1));
    const drawnImage = drawImage.mock.calls[0][0] as { src: string };
    expect(drawnImage.src).toBe('data:image/jpeg;base64,colourised-stage0');
    expect(fillRect).toHaveBeenCalledTimes(1);
  });

  it('shows the resolved plan and downloads a missing stage before starting', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.quality = 'best';
    snapshot.settings.selected_model_id = 'realplksr_nomoswebphoto_x4';
    snapshot.settings.fixes = ['jpeg'];
    snapshot.settings.fix_stages = [{ fix: 'jpeg' as const, model_id: 'fbcnn_color' }];
    snapshot.catalog.models.find((model) => model.model_id === 'fbcnn_color')!.installed = false;
    const user = await mountWith(snapshot);

    await openPlanSheet(user);
    const stages = within(screen.getByRole('list', { name: 'Stages' })).getAllByRole('listitem');
    expect(stages[0].textContent).toContain('Fix JPEG');
    expect(stages[0].textContent).toContain('FBCNN Color');
    expect(stages[1].textContent).toContain('Upscale ×4');
    expect(stages[1].textContent).toContain('RealPLKSR ×4 NomosWebPhoto');
    // A ready model needs no label; only the missing stage says anything.
    expect(stages[1].textContent).not.toMatch(/MB|Ready/);
    expect(
      screen.getByRole('button', { name: /^Best\s*Finest detail/, pressed: true }),
    ).toBeTruthy();

    const start = screen.getByRole('button', { name: /^Download \d+(\.\d+)? MB, then upscale$/ });
    expect(start.matches(':disabled')).toBe(false);
    await user.click(start);
    await waitFor(() => expect(api.downloadModel).toHaveBeenCalledWith('fbcnn_color', false));
    expect(api.downloadModel).toHaveBeenCalledTimes(1);
  });

  it('turns Start into a cancel button while it downloads, and does not start afterwards', async () => {
    const snapshot = readySnapshot([image('frame', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.quality = 'best';
    snapshot.settings.selected_model_id = 'realplksr_nomoswebphoto_x4';
    snapshot.catalog.models.find(
      (model) => model.model_id === 'realplksr_nomoswebphoto_x4',
    )!.installed = false;
    let failDownload!: (error: Error) => void;
    api.downloadModel.mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          failDownload = reject;
        }),
    );
    api.cancelDownload.mockImplementationOnce(async () => {
      failDownload(new Error('download cancelled'));
    });
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: /^Download [\d.]+ MB, then upscale$/ }));
    await user.click(await screen.findByRole('button', { name: 'Cancel download' }));
    await waitFor(() =>
      expect(api.cancelDownload).toHaveBeenCalledWith('realplksr_nomoswebphoto_x4'),
    );
    await screen.findByRole('button', { name: /^Download [\d.]+ MB, then upscale$/ });
    expect(api.startJobs).not.toHaveBeenCalled();
    expect(screen.queryByText('Download failed')).toBeNull();
  });

  it('resolves Quick and Best for illustrations and keeps the choice in the recipe', async () => {
    const snapshot = readySnapshot([image('frame', true)]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('radio', { name: 'Illustration' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          content: 'illustration',
          selected_model_id: 'realesrgan_x4plus_anime_6b',
        }),
      ),
    );
    expect(screen.queryByText('Quick · Illustration · 4×')).toBeNull();
    // An illustration model is picked outright, with no note about a stand-in.
    expect(screen.queryByText(/fully verified illustration/)).toBeNull();
    expect(screen.queryByRole('button', { name: /\(Labs\) for this job/ })).toBeNull();
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await user.click(screen.getByRole('button', { name: 'Save current as recipe…' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.saveRecipe).toHaveBeenCalledWith(
        expect.objectContaining({
          quality: 'best',
          content: 'illustration',
          model_id: 'esrgan_hfa2k_x4',
        }),
      ),
    );
  });

  it('maps the JPEG chip to a restoration stage and clears it again', async () => {
    const snapshot = readySnapshot([image('scan', true)]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('checkbox', { name: 'JPEG artifacts' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fixes: ['jpeg'],
          fix_stages: [{ fix: 'jpeg' as const, model_id: 'fbcnn_color' }],
        }),
      ),
    );
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getAllByRole('listitem'),
    ).toHaveLength(2);
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fixes: ['blur', 'jpeg'],
          fix_stages: [
            { fix: 'blur' as const, model_id: 'nafnet_gopro_deblur' },
            { fix: 'jpeg', model_id: 'fbcnn_color' },
          ],
        }),
      ),
    );
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fixes: ['jpeg'],
          fix_stages: [{ fix: 'jpeg', model_id: 'fbcnn_color' }],
        }),
      ),
    );
  });

  it('chooses the fix model without touching the upscaler and remembers it', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fixes: ['noise'],
          fix_stages: [{ fix: 'noise' as const, model_id: 'nafnet_sidd_width64' }],
        }),
      ),
    );
    await openPlanSheet(user);
    const stages = screen.getByRole('list', { name: 'Stages' });
    expect(within(stages).getByText('Fix noise')).toBeTruthy();
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the noise fix model' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(library).getByText('Choosing for Upscale · Noise')).toBeTruthy();
    // Only models that fix noise; the JPEG model is not offered for this job.
    expect(within(library).queryByRole('button', { name: /FBCNN/ })).toBeNull();
    await user.click(within(library).getByRole('button', { name: /SCUNet/ }));
    expect(within(library).queryByRole('button', { name: 'Use for this job' })).toBeNull();
    await user.click(within(library).getByRole('button', { name: 'Use for Noise · Best' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull());
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          quality: 'best',
          selected_model_id: 'realplksr_nomoswebphoto_x4',
          halo: 16,
          fix_stages: [{ fix: 'noise' as const, model_id: 'scunet_color_real_psnr' }],
          preset_pins: { 'fix/noise/best': 'scunet_color_real_psnr' },
        }),
      ),
    );
    expect(screen.queryByRole('button', { name: 'Back to Best' })).toBeNull();
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText(/Your pick/),
    ).toBeTruthy();
    await user.keyboard('{Escape}');

    // The choice survives the fix going off and on, Quick and back, and the content switch.
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fix_stages: [{ fix: 'noise' as const, model_id: 'scunet_color_real_psnr' }],
        }),
      ),
    );
    await user.click(screen.getByRole('button', { name: /^Quick/ }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fix_stages: [{ fix: 'noise' as const, model_id: 'denoise_realplksr_1x' }],
        }),
      ),
    );
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await user.click(screen.getByRole('radio', { name: 'Illustration' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          content: 'illustration',
          fix_stages: [{ fix: 'noise' as const, model_id: 'scunet_color_real_psnr' }],
        }),
      ),
    );

    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the noise fix model' }));
    const again = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(again).getByRole('button', { name: 'Back to the default' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fix_stages: [{ fix: 'noise' as const, model_id: 'nafnet_sidd_width64' }],
          preset_pins: {},
        }),
      ),
    );
  });

  it('keeps a fix chosen for a hand-picked upscaler through a chip round trip', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    let library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /HAT-L ×4 ImageNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await openPlanSheet(user);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the noise fix model' }));
    library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /SCUNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for Noise' }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          quality: 'custom',
          selected_model_id: 'hat_l_x4_imagenet',
          fix_stages: [{ fix: 'noise' as const, model_id: 'scunet_color_real_psnr' }],
        }),
      ),
    );
  });

  it('lets Same size choose its model per problem and retains the last fix as primary', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(library).getByText('Choosing for Same size · Noise')).toBeTruthy();
    expect(within(library).queryByRole('button', { name: /FBCNN/ })).toBeNull();
    await user.click(within(library).getByRole('button', { name: /SCUNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for Noise · Best' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          selected_model_id: 'scunet_color_real_psnr',
          output_scale: 1,
          halo: 16,
          preset_pins: { 'fix/noise/best': 'scunet_color_real_psnr' },
        }),
      ),
    );
    // Blur enters before noise; the chosen denoiser remains primary.
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fixes: ['blur', 'noise'],
          selected_model_id: 'scunet_color_real_psnr',
          fix_stages: [
            { fix: 'blur', model_id: 'nafnet_gopro_deblur' },
            { fix: 'noise', model_id: 'scunet_color_real_psnr' },
          ],
        }),
      ),
    );
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText(
        'Can add artifacts to sharp pictures and line art. Skipped if the result is unstable.',
      ),
    ).toBeTruthy();
  });

  it('Back to the default at Same size also replaces a model picked by hand', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    Object.assign(snapshot.settings, {
      task: 'denoise',
      output_scale: 1,
      quality: 'custom',
      selected_model_id: '__custom__',
      custom_model_path: '/models/mine.safetensors',
      custom_models: [{ path: '/models/mine.safetensors', name: 'mine' }],
    });
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    let library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /SCUNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          selected_model_id: 'scunet_color_real_psnr',
          fixes: ['noise'],
          fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }],
          quality: 'custom',
        }),
      ),
    );
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: 'Use for Noise' }));
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: 'Back to the default' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          selected_model_id: 'nafnet_sidd_width64',
          quality: 'best',
          preset_pins: {},
        }),
      ),
    );
  });

  it('warns before the blur fix that it can fail on sharp pictures', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    expect(screen.getByRole('button', { name: 'About Fix' })).toBeTruthy();
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText(
        'Can add artifacts to sharp pictures and line art. Skipped if the result is unstable.',
      ),
    ).toBeTruthy();
  });

  it('names the fix of a recipe saved before fix chips existed', async () => {
    const snapshot = readySnapshot([image('first', true)]);
    snapshot.recipes = [
      {
        id: 'old',
        name: 'Old scans',
        task: 'upscale',
        model_id: 'span_photo_x4',
        output_scale: 4,
        tile_size: 256,
        halo: 8,
        precision: 'fp32',
        safe_memory: true,
        // The host fills a missing list with [], so an old recipe arrives like this.
        fixes: [],
        stages: [
          { kind: 'deblock', model_id: 'fbcnn_color' },
          { kind: 'upscale', model_id: 'span_photo_x4' },
        ],
      },
    ];
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await user.click(screen.getByRole('button', { name: /^Old scans/ }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fixes: ['jpeg'],
          fix_stages: [{ fix: 'jpeg' as const, model_id: 'fbcnn_color' }],
        }),
      ),
    );
    expect(
      (screen.getByRole('checkbox', { name: 'JPEG artifacts' }) as HTMLInputElement).checked,
    ).toBe(true);
  });

  it('switches between HAT upscalers without offering Faces', async () => {
    const snapshot = readySnapshot([image('portrait', true)]);
    snapshot.engine!.features.push('face_aware');
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    let library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /HAT-L ×4 ImageNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    expect(screen.queryByRole('button', { name: 'Faces' })).toBeNull();
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /HAT-S ×4/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ selected_model_id: 'hat_s_x4', enable_face_model: false }),
      ),
    );
    expect(screen.queryByRole('button', { name: 'Faces' })).toBeNull();
  });

  it('names in the library which models run on the Neural Engine or an NPU', async () => {
    const mac = readySnapshot([image('photo', true)]);
    mac.capabilities.devices[0].neural_engine = true;
    const user = await mountWith(mac);
    await chooseMenuItem('view.models');
    let library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /^Upscale photos/ }));
    expect(
      within(library).getByRole('button', { name: /SPAN ×4.*Runs well here · Neural Engine/ }),
    ).toBeTruthy();
    const hatRow = within(library).getByRole('button', { name: /HAT-L ×4 ImageNet/ });
    expect(hatRow.textContent).not.toMatch(/Neural Engine/);
    expect(hatRow.textContent).toMatch(/Runs well here · Apple GPU \(Metal\)/);
    await user.click(within(library).getByRole('button', { name: /SPAN ×4/ }));
    expect(within(library).getByText('Runs on')).toBeTruthy();
    expect(library.querySelector('.detail .fit')?.textContent).toMatch(
      /Runs well here · Neural Engine/,
    );
    // The file keeps its end when the details column is narrow; the author is shown in full.
    const file = mac.catalog.models.find((model) => model.model_id === 'span_photo_x4')!;
    expect(library.querySelector(`.kv .file-name[title="${file.filename}"]`)).toBeTruthy();
    expect(library.querySelector('.kv dd.source-value')?.textContent).toContain(file.author);
    cleanup();

    const pc = readySnapshot([image('photo', true)]);
    pc.capabilities.devices = [
      {
        id: 'openvino:80d1',
        type: 'npu',
        name: 'Intel(R) AI Boost (NPU)',
        total_memory: 16 * 1024 ** 3,
        free_memory: 8 * 1024 ** 3,
        supports_fp16: false,
        is_integrated: true,
        recommended_tile_sizes: [128, 256],
      },
      ...pc.capabilities.devices.filter((device) => device.id === 'cpu'),
    ];
    pc.settings.device_id = 'openvino:80d1';
    const user2 = await mountWith(pc);
    await chooseMenuItem('view.models');
    library = screen.getByRole('dialog', { name: 'Model library' });
    await user2.click(within(library).getByRole('button', { name: /^Upscale photos/ }));
    expect(
      within(library).getByRole('button', { name: /SPAN ×4.*Runs well here · NPU/ }),
    ).toBeTruthy();
  });

  it('explains the NPU in Settings and names a missing compiler in the status line', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.capabilities.devices = [
      {
        id: 'openvino:80d1',
        type: 'npu',
        name: 'Intel(R) AI Boost (NPU)',
        total_memory: 16 * 1024 ** 3,
        free_memory: 8 * 1024 ** 3,
        supports_fp16: false,
        is_integrated: true,
        recommended_tile_sizes: [128, 256],
      },
      ...snapshot.capabilities.devices.filter((device) => device.id === 'cpu'),
    ];
    snapshot.settings.device_id = 'openvino:80d1';
    snapshot.settings.selected_model_id = 'span_photo_x4';
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    expect((screen.getByLabelText('Precision') as HTMLSelectElement).value).toBe('fp16');
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Performance' }));
    expect(within(settingsDialog).getByText(/compiles a model once for the NPU/)).toBeTruthy();
    cleanup();

    const missing = readySnapshot([image('photo', true)]);
    missing.capabilities.devices = missing.capabilities.devices.filter((d) => d.id === 'cpu');
    missing.capabilities.npu_hint = { name: 'Intel(R) AI Boost', missing: 'compiler' };
    missing.settings.device_id = 'cpu';
    const user2 = await mountWith(missing);
    // Like a GPU this edition cannot use, the missing compiler is named in the idle status line;
    // it is a system package, so there is no link to LocalSR's downloads.
    const status = document.querySelector('.status-copy') as HTMLElement;
    expect(
      within(status).getByText(
        /Runs on the CPU\. The NPU \(Intel AI Boost\) can run models once the (intel-driver-compiler-npu package|current Intel NPU driver)/,
      ),
    ).toBeTruthy();
    expect(within(status).queryByRole('button', { name: 'Download page' })).toBeNull();
    const sheet = await openPlanSheet(user2);
    expect(sheet.textContent).not.toMatch(
      /NPU compiler|intel-driver-compiler-npu|current Intel NPU driver/,
    );
  });

  it('picks a model for this job from the library and marks the recipe as custom', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(library).getByRole('button', { name: /For this job/ })).toBeTruthy();
    await user.click(within(library).getByRole('button', { name: /HAT-L ×4 ImageNet/ }));
    expect(within(library).getByText('Apache-2.0 ↗')).toBeTruthy();
    expect(
      within(library).getByRole('button', { name: /XPixel Group · model source/ }),
    ).toBeTruthy();
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull());
    expect(screen.getByText('Model · chosen by you')).toBeTruthy();
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ selected_model_id: 'hat_l_x4_imagenet', quality: 'custom' }),
      ),
    );
    await user.click(screen.getByRole('button', { name: 'Back to Best' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          selected_model_id: 'realplksr_nomoswebphoto_x4',
          quality: 'best',
        }),
      ),
    );
  });

  it('shows compatible models left and opens the library filtered to the current job', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: /more models in the library/i }));

    const library = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(library).getByText('Choosing for Upscale · Quick · Photo')).toBeTruthy();
    expect(within(library).getByRole('button', { name: /For this job/ })).toBeTruthy();
    expect(within(library).getByRole('button', { name: /HAT-L ×4 ImageNet/ })).toBeTruthy();
    expect(within(library).queryByRole('button', { name: /HAT-L ×4 Face/ })).toBeNull();
  });

  it('uses the tile overlap a library model needs', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /DAT2 ×4 BHI Real/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ selected_model_id: 'bhi_dat2_real_x4', halo: 32 }),
      ),
    );
    await user.click(screen.getByRole('button', { name: 'Back to Best' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ selected_model_id: 'realplksr_nomoswebphoto_x4', halo: 16 }),
      ),
    );
  });

  it('pins a library model to Best and removes an installed model', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    api.removeModel.mockResolvedValue(undefined);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /HAT-L ×4 ImageNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Make my Best · Photo' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          quality: 'best',
          selected_model_id: 'hat_l_x4_imagenet',
          preset_pins: { 'upscale/photo/best': 'hat_l_x4_imagenet' },
        }),
      ),
    );
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    const reopened = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(reopened).getByRole('button', { name: /^Installed/ }));
    await user.click(within(reopened).getByRole('button', { name: 'Remove SPAN ×4 NomosUni' }));
    await waitFor(() => expect(api.removeModel).toHaveBeenCalledWith('span_photo_x4'));
  });

  it('explains the one-time download on first run and fetches it from Start', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.catalog.models.forEach((model) => (model.installed = false));
    const user = await mountWith(snapshot);
    // One place offers the download: the Start button, with the note beside the model.
    expect(screen.getByText(/Models download once when you start/)).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /^(Download|Get) /i })).toHaveLength(1);
    await user.click(
      screen.getByRole('button', { name: /^Download \d+(\.\d+)? MB, then upscale$/ }),
    );
    await waitFor(() =>
      expect(api.downloadModel).toHaveBeenCalledWith('localsr_quick_x4_v1', false),
    );
  });

  it('offers the illustration model as a download on Start when it is not installed', async () => {
    const snapshot = readySnapshot([image('frame', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.content = 'illustration';
    snapshot.catalog.models.forEach((model) => (model.installed = false));
    const user = await mountWith(snapshot);
    await user.click(
      screen.getByRole('button', { name: /^Download \d+(\.\d+)? MB, then upscale$/ }),
    );
    await waitFor(() =>
      expect(api.downloadModel).toHaveBeenCalledWith('realesrgan_x4plus_anime_6b', false),
    );
  });

  it('keeps Best selected when the content type changes', async () => {
    const snapshot = readySnapshot([image('frame', true)]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          quality: 'best',
          selected_model_id: 'realplksr_nomoswebphoto_x4',
        }),
      ),
    );
    await user.click(screen.getByRole('radio', { name: 'Illustration' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          quality: 'best',
          content: 'illustration',
          selected_model_id: 'esrgan_hfa2k_x4',
        }),
      ),
    );
  });
});

it('offers the FFmpeg notice for an undecodable phone video and re-inspects it once FFmpeg is found', async () => {
  const media = video('phone', true);
  media.probe_status = 'pending';
  const snapshot = readySnapshot([media]);
  const user = await mountWith(snapshot);
  api.detectExternalFFmpeg.mockResolvedValue(['/opt/homebrew/bin/ffmpeg']);

  const failed = structuredClone(snapshot);
  failed.media[0].probe_status = 'failed';
  failed.media[0].error = 'This video uses a patent-licensed format that LocalSR does not include.';
  api.refreshSnapshot.mockResolvedValue(failed);
  const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
  callback({
    type: 'media_probe_failed',
    data: {
      media_path: media.path,
      error_message: failed.media[0].error,
      reason: 'external_ffmpeg_required',
    },
  });

  const dialog = await screen.findByRole('dialog', { name: 'This video needs FFmpeg' });
  expect(within(dialog).getByText(/Phone and camera videos need FFmpeg/)).toBeTruthy();
  expect(within(dialog).getByText(/Photos work without it/)).toBeTruthy();
  expect(within(dialog).getByText('phone.mp4')).toBeTruthy();
  await user.click(within(dialog).getByRole('button', { name: 'Install in Terminal…' }));
  expect(api.openTerminalWithInstallCommand).toHaveBeenCalledWith(
    expect.stringContaining('ffmpeg'),
  );

  await user.click(within(dialog).getByRole('button', { name: 'I installed it, find FFmpeg' }));
  await waitFor(() =>
    expect(api.saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({ external_ffmpeg_path: '/opt/homebrew/bin/ffmpeg' }),
    ),
  );
  await waitFor(() => expect(api.probePath).toHaveBeenCalledWith(media.path));
  expect(screen.queryByRole('dialog', { name: 'This video needs FFmpeg' })).toBeNull();
});

it('keeps ordinary probe failures out of the FFmpeg notice', async () => {
  const media = video('broken', true);
  await mountWith(readySnapshot([media]));
  const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
  callback({
    type: 'media_probe_failed',
    data: { media_path: media.path, error_message: 'This video contains no decodable frames.' },
  });
  await waitFor(() => expect(api.refreshSnapshot).toHaveBeenCalled());
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('asks for FFmpeg before starting an H.264 export instead of failing later', async () => {
  const snapshot = readySnapshot([video('clip', true)]);
  snapshot.settings.video_codec = 'h264';
  snapshot.settings.external_ffmpeg_path = '';
  const user = await mountWith(snapshot);
  await user.click(screen.getByRole('button', { name: 'Start selected video' }));
  expect(
    await screen.findByRole('dialog', { name: 'H.264 and HEVC export needs FFmpeg' }),
  ).toBeTruthy();
  expect(api.startJobs).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Not now' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

describe('Several fixes in one pass', () => {
  const blur = { fix: 'blur' as const, model_id: 'nafnet_gopro_deblur' };
  const jpeg = { fix: 'jpeg' as const, model_id: 'fbcnn_color' };
  const rows = () => within(screen.getByRole('list', { name: 'Stages' })).getAllByRole('listitem');
  const labels = () => rows().map((row) => row.querySelector('.stage')?.textContent);
  const lastSaved = (): UiSettings => api.saveSettings.mock.calls.at(-1)![0] as UiSettings;

  it('toggles each fix on its own, adds it at its place, moves it and changes its model', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    // One fix has nowhere to move.
    expect(screen.queryByRole('button', { name: /^Move the/ })).toBeNull();
    await user.click(screen.getByRole('checkbox', { name: 'JPEG artifacts' }));
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    expect((screen.getByRole('checkbox', { name: 'Noise' }) as HTMLInputElement).checked).toBe(
      true,
    );
    await openPlanSheet(user);
    expect(labels()).toEqual(['Fix blur', 'Fix JPEG', 'Fix noise', 'Upscale ×4']);
    const move = (name: string) => screen.getByRole('button', { name });
    // Each fix offers only the moves it can make.
    expect(screen.queryByRole('button', { name: 'Move the blur fix earlier' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Move the noise fix later' })).toBeNull();
    expect(move('Move the JPEG fix earlier')).toBeTruthy();
    expect(move('Move the JPEG fix later')).toBeTruthy();
    // One line per model: role, name, Change… and the arrows; notes and links below it.
    const jpegRow = rows()[1];
    const end = jpegRow.querySelector('.plan-end')!;
    expect([...end.querySelectorAll('button')].map((button) => button.textContent)).toEqual([
      '↑',
      '↓',
      'Change…',
    ]);
    // A fix that can move one way shows one arrow, with no empty slot beside it.
    expect(
      [...rows()[2].querySelectorAll('.plan-end button')].map((button) => button.textContent),
    ).toEqual(['↑', 'Change…']);
    expect(document.querySelector('.move-gap')).toBeNull();
    expect(move('Move the JPEG fix earlier').getAttribute('title')).toBe('Move earlier');
    expect(move('Move the JPEG fix later').getAttribute('title')).toBe('Move later');
    expect(jpegRow.querySelector('.name')?.getAttribute('title')).toBe('FBCNN Color');
    expect(rows()[0].querySelector('.plan-note')?.textContent).toMatch(/^Can add artifacts/);
    expect(screen.queryByText('Move earlier')).toBeNull();

    await user.click(move('Move the noise fix earlier'));
    expect(labels()).toEqual(['Fix blur', 'Fix noise', 'Fix JPEG', 'Upscale ×4']);
    // The keyboard stays on the fix that moved.
    expect(document.activeElement).toBe(move('Move the noise fix earlier'));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'realplksr_nomoswebphoto_x4',
        fixes: ['blur', 'noise', 'jpeg'],
        fix_stages: [blur, { fix: 'noise', model_id: 'nafnet_sidd_width64' }, jpeg],
      }),
    );

    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the noise fix model' }));
    let library = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(library).getByText('Choosing for Upscale · Noise')).toBeTruthy();
    // The library opens on the model this fix uses now.
    expect(within(library).getByRole('heading', { name: 'NAFNet SIDD Width64' })).toBeTruthy();
    await user.click(within(library).getByRole('button', { name: /SCUNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for Noise · Best' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'realplksr_nomoswebphoto_x4',
        fix_stages: [blur, { fix: 'noise', model_id: 'scunet_color_real_psnr' }, jpeg],
        preset_pins: { 'fix/noise/best': 'scunet_color_real_psnr' },
      }),
    );
    await openPlanSheet(user);
    expect(within(rows()[1]).getByText(/Your pick/)).toBeTruthy();
    expect(within(rows()[2]).queryByText(/Your pick/)).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Change the JPEG fix model' }));
    library = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(library).getByText('Choosing for Upscale · JPEG artifacts')).toBeTruthy();
    expect(within(library).getByRole('heading', { name: 'FBCNN Color' })).toBeTruthy();
    expect(within(library).queryByRole('button', { name: /SCUNet/ })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Close model library' }));

    // Turning one fix off keeps the others, their order and their models.
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        fixes: ['noise', 'jpeg'],
        fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }, jpeg],
      }),
    );
    // A fix moved to the top keeps the keyboard on its way back down.
    await openPlanSheet(user);
    await user.click(move('Move the JPEG fix earlier'));
    expect(labels()).toEqual(['Fix JPEG', 'Fix noise', 'Upscale ×4']);
    expect(document.activeElement).toBe(move('Move the JPEG fix later'));
  });

  it('moves a fix without choosing anything again', async () => {
    // A recipe's models and a scale and overlap set by hand, none of them Best's own.
    const snapshot = readySnapshot([image('photo', true)]);
    const noise = { fix: 'noise' as const, model_id: 'scunet_color_real_psnr' };
    Object.assign(snapshot.settings, {
      task: 'upscale',
      quality: 'best',
      selected_model_id: 'hat_s_x4',
      output_scale: 2,
      halo: 64,
      safe_memory: true,
      fixes: ['noise', 'blur'],
      fix_stages: [noise, blur],
    });
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Move the noise fix later' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        quality: 'best',
        selected_model_id: 'hat_s_x4',
        output_scale: 2,
        halo: 64,
        safe_memory: true,
        fixes: ['blur', 'noise'],
        fix_stages: [blur, noise],
      }),
    );
  });

  it('moves a fix in Restore and only a new last fix brings its model and overlap', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const noise = { fix: 'noise' as const, model_id: 'scunet_color_real_psnr' };
    Object.assign(snapshot.settings, {
      task: 'denoise',
      quality: 'best',
      selected_model_id: 'scunet_color_real_psnr',
      output_scale: 1,
      halo: 64,
      fixes: ['blur', 'jpeg', 'noise'],
      fix_stages: [blur, jpeg, noise],
    });
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    // The last fix stays: the Restore model and its overlap stay as they are.
    await user.click(screen.getByRole('button', { name: 'Move the blur fix later' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'scunet_color_real_psnr',
        halo: 64,
        fix_stages: [jpeg, blur, noise],
      }),
    );
    // Blur becomes last: its model is the Restore model now, with the overlap it needs.
    await user.click(screen.getByRole('button', { name: 'Move the noise fix earlier' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        quality: 'best',
        selected_model_id: 'nafnet_gopro_deblur',
        halo: 32,
        fixes: ['jpeg', 'noise', 'blur'],
        fix_stages: [jpeg, noise, blur],
      }),
    );
  });

  it('runs the fixes before the last one first at Same size, whose last fix is its model', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await user.click(screen.getByRole('button', { name: /^Quick/ }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await user.click(screen.getByRole('checkbox', { name: 'JPEG artifacts' }));
    // JPEG goes before noise; the noise fix stays the model.
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'denoise_realplksr_1x',
        output_scale: 1,
        halo: 8,
        fixes: ['jpeg', 'noise'],
        fix_stages: [jpeg, { fix: 'noise', model_id: 'denoise_realplksr_1x' }],
      }),
    );
    await openPlanSheet(user);
    expect(labels()).toEqual(['Fix JPEG', 'Fix noise']);
    // A fix before the model is chosen like one before an upscale; the model stays.
    await user.click(screen.getByRole('button', { name: 'Change the JPEG fix model' }));
    const fixLibrary = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(fixLibrary).getByText('Choosing for Same size · JPEG artifacts')).toBeTruthy();
    await user.click(
      within(fixLibrary).getByRole('button', { name: 'Use for JPEG artifacts · Quick' }),
    );
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'denoise_realplksr_1x',
        halo: 8,
        preset_pins: { 'fix/jpeg/quick': 'fbcnn_color' },
      }),
    );
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    expect(
      within(screen.getByRole('dialog', { name: 'Model library' })).getByText(
        'Choosing for Same size · Noise',
      ),
    ).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Close model library' }));

    // Now JPEG is last: FBCNN becomes the model, with the overlap it needs.
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Move the noise fix earlier' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'fbcnn_color',
        halo: 32,
        fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }, jpeg],
      }),
    );
    expect(labels()).toEqual(['Fix noise', 'Fix JPEG']);

    // The host answers the save with the settings it holds.
    snapshot.settings = structuredClone(lastSaved());
    api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await user.click(screen.getByRole('button', { name: 'Save current as recipe…' }));
    await user.type(screen.getByRole('textbox', { name: 'Recipe name' }), 'Scan clean-up');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.saveRecipe).toHaveBeenCalledTimes(1));
    expect(api.saveRecipe.mock.calls[0][0].stages).toEqual([
      { kind: 'restore', model_id: 'denoise_realplksr_1x', fix: 'noise' },
      { kind: 'restore', model_id: 'fbcnn_color', fix: 'jpeg' },
    ]);

    await user.click(screen.getByRole('button', { name: 'Restore selected' }));
    await waitFor(() =>
      expect(api.startJobs).toHaveBeenCalledWith(
        expect.objectContaining({
          task: 'denoise',
          model_id: 'fbcnn_color',
          fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }, jpeg],
        }),
      ),
    );
  });

  it('turns Use for this job at Same size into its problem chip', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    Object.assign(snapshot.settings, {
      task: 'denoise',
      output_scale: 1,
      quality: 'custom',
      selected_model_id: '__custom__',
      custom_model_path: '/models/mine.safetensors',
      custom_models: [{ path: '/models/mine.safetensors', name: 'mine' }],
    });
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /SCUNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        quality: 'custom',
        output_scale: 1,
        selected_model_id: 'scunet_color_real_psnr',
        fixes: ['noise'],
        fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }],
      }),
    );
    // A problem it cannot fix brings a model that can.
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'nafnet_gopro_deblur',
        fix_stages: [blur],
      }),
    );
  });

  it('lets your own checkpoint be the Same size model without a fix in front of it', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    api.chooseCustomModel.mockResolvedValueOnce('/models/mine.safetensors' as never);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    await user.click(screen.getByRole('button', { name: /Your own file/ }));
    await user.click(screen.getByRole('button', { name: 'Choose checkpoint…' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: '__custom__',
        fix_stages: [],
        fixes: [],
      }),
    );
    await openPlanSheet(user);
    expect(labels()).toEqual(['Fix']);
    expect((screen.getByRole('checkbox', { name: 'Blur' }) as HTMLInputElement).checked).toBe(
      false,
    );
  });

  it('saves the fixes in order in a recipe and applies them again', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Move the noise fix earlier' }));
    api.saveRecipe.mockImplementationOnce(async (recipe: Recipe) => {
      snapshot.recipes = [recipe];
      snapshot.settings = structuredClone(lastSaved());
      api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    });
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await user.click(screen.getByRole('button', { name: 'Save current as recipe…' }));
    await user.type(screen.getByRole('textbox', { name: 'Recipe name' }), 'Two fixes');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.saveRecipe).toHaveBeenCalledTimes(1));
    const recipe = api.saveRecipe.mock.calls[0][0];
    expect(recipe.fixes).toEqual(['noise', 'blur']);
    expect(recipe.stages).toEqual([
      { kind: 'restore', model_id: 'nafnet_sidd_width64', fix: 'noise' },
      { kind: 'restore', model_id: 'nafnet_gopro_deblur', fix: 'blur' },
      { kind: 'upscale', model_id: 'realplksr_nomoswebphoto_x4' },
    ]);
    const saved = await screen.findByRole('button', { name: /^Two fixes/ });
    expect(saved.getAttribute('aria-pressed')).toBe('true');

    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Move the blur fix earlier' }));
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Two fixes/ }).getAttribute('aria-pressed')).toBe(
        'false',
      ),
    );
    await user.click(screen.getByRole('button', { name: /^Two fixes/ }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        fixes: ['noise', 'blur'],
        fix_stages: [{ fix: 'noise', model_id: 'nafnet_sidd_width64' }, blur],
      }),
    );
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    expect(screen.getByRole('button', { name: /^Two fixes/ }).getAttribute('aria-pressed')).toBe(
      'true',
    );
  });

  it.each([
    {
      name: 'Restore with a chip',
      recipe: {
        task: 'denoise' as const,
        model_id: 'fbcnn_color',
        output_scale: 1,
        fixes: ['jpeg' as const],
        stages: [{ kind: 'restore' as const, model_id: 'fbcnn_color' }],
      },
      fix_stages: [jpeg],
      fixes: ['jpeg'],
    },
    {
      name: 'Restore without a chip',
      recipe: {
        task: 'denoise' as const,
        model_id: 'scunet_color_real_psnr',
        output_scale: 1,
        fixes: [],
        stages: [{ kind: 'restore' as const, model_id: 'scunet_color_real_psnr' }],
      },
      fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }],
      fixes: ['noise'],
    },
    {
      name: 'Upscale with a fix and Faces',
      recipe: {
        task: 'upscale' as const,
        model_id: 'hat_l_x4_imagenet',
        output_scale: 4,
        fixes: ['noise' as const, 'faces' as const],
        stages: [
          { kind: 'restore' as const, model_id: 'scunet_color_real_psnr' },
          { kind: 'upscale' as const, model_id: 'hat_l_x4_imagenet' },
        ],
      },
      fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }],
      fixes: ['noise'],
    },
  ])('applies a 0.3.5 recipe: $name', async ({ recipe, fix_stages, fixes }) => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.recipes = [
      {
        id: 'old',
        name: 'Old recipe',
        tile_size: 256,
        halo: 16,
        precision: 'fp32',
        safe_memory: true,
        ...recipe,
      },
    ];
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await user.click(screen.getByRole('button', { name: /^Old recipe/ }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: recipe.task,
        selected_model_id: recipe.model_id,
        fix_stages,
        fixes,
      }),
    );
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    expect(screen.getByRole('button', { name: /^Old recipe/ }).getAttribute('aria-pressed')).toBe(
      'true',
    );
  });

  it('keeps a frame-by-frame video recipe and offers fixes for frame-by-frame video', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    Object.assign(snapshot.settings, {
      task: 'video',
      video_task: 'upscale',
      quality: 'best',
      selected_model_id: 'span_photo_x4',
      selected_video_model_id: 'frame_by_frame',
    });
    const user = await mountWith(snapshot);
    // Frame-by-frame video offers fix stages; Faces only with a HAT model.
    expect(screen.getByRole('checkbox', { name: 'Noise' })).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: 'Blur' })).toBeTruthy();
    expect(screen.queryByRole('checkbox', { name: 'Faces' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await user.click(screen.getByRole('button', { name: 'Save current as recipe…' }));
    await user.type(screen.getByRole('textbox', { name: 'Recipe name' }), 'Clip');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.saveRecipe).toHaveBeenCalledTimes(1));
    expect(api.saveRecipe.mock.calls[0][0].stages).toEqual([
      { kind: 'video', model_id: 'span_photo_x4' },
    ]);
  });

  it('keeps fixes when the task becomes frame-by-frame video', async () => {
    const snapshot = readySnapshot([image('photo', true), video('clip')]);
    Object.assign(snapshot.settings, {
      task: 'upscale',
      quality: 'custom',
      selected_model_id: 'span_photo_x4',
      fixes: ['blur', 'noise'],
      fix_stages: [blur, { fix: 'noise', model_id: 'scunet_color_real_psnr' }],
    });
    await mountWith(snapshot);
    const switched = structuredClone(snapshot);
    switched.media = [image('photo'), video('clip', true)];
    api.refreshSnapshot.mockResolvedValue(switched);
    await userEvent.click(screen.getByRole('button', { name: /^▶ clip\.mp4/ }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'video',
        video_task: 'upscale',
        fix_stages: [blur, { fix: 'noise', model_id: 'scunet_color_real_psnr' }],
        fixes: ['blur', 'noise'],
      }),
    );
  });

  it('keeps a fix without a model in the plan and neither downloads nor starts', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    Object.assign(snapshot.settings, {
      task: 'upscale',
      quality: 'best',
      selected_model_id: 'span_photo_x4',
      fixes: ['blur'],
      fix_stages: [{ fix: 'blur', model_id: 'retired_model' }],
    });
    snapshot.catalog.models.find((model) => model.model_id === 'span_photo_x4')!.installed = false;
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    expect(within(rows()[0]).getByText('No model for this fix')).toBeTruthy();
    expect(rows()[0].textContent).not.toContain('Needs your file');
    expect(screen.queryByRole('button', { name: /then upscale$/ })).toBeNull();
    expect(
      (screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Change the blur fix model' }));
    expect(
      within(screen.getByRole('dialog', { name: 'Model library' })).getByText(
        'Choosing for Upscale · Blur',
      ),
    ).toBeTruthy();
  });

  it('does not blame an engine that has not answered yet', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.engine = null;
    Object.assign(snapshot.settings, {
      task: 'upscale',
      selected_model_id: 'span_photo_x4',
      fixes: ['blur', 'jpeg'],
      fix_stages: [blur, jpeg],
    });
    await mountWith(snapshot);
    expect(screen.queryByText(/runs one fix at a time/)).toBeNull();
  });

  it('refuses several fixes on an engine that runs one', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.engine!.features = snapshot.engine!.features.filter(
      (feature) => feature !== 'image_multi_fix',
    );
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    expect(screen.queryByText(/runs one fix at a time/)).toBeNull();
    expect(
      (screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement).disabled,
    ).toBe(false);
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    expect(
      screen.getByText('This engine runs one fix at a time. Update LocalSR to run several.'),
    ).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('keeps the terms acceptance of each fix model apart', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    // The catalog is shared between tests; change a copy.
    snapshot.catalog = structuredClone(snapshot.catalog);
    Object.assign(snapshot.settings, {
      task: 'upscale',
      quality: 'best',
      selected_model_id: 'span_photo_x4',
      fixes: ['blur', 'jpeg'],
      fix_stages: [blur, jpeg],
    });
    for (const id of ['nafnet_gopro_deblur', 'fbcnn_color']) {
      const model = snapshot.catalog.models.find((entry) => entry.model_id === id)!;
      model.installed = false;
      model.terms_acceptance_required = true;
    }
    const user = await mountWith(snapshot);
    const foot = document.querySelector('.panel-foot') as HTMLElement;
    const blurNeeds = within(foot)
      .getByText('Blur · NAFNet GoPro Deblur')
      .closest('.needs-you') as HTMLElement;
    const jpegNeeds = within(foot)
      .getByText('JPEG · FBCNN Color')
      .closest('.needs-you') as HTMLElement;
    const blurTerms = within(blurNeeds).getByRole('checkbox') as HTMLInputElement;
    const jpegTerms = within(jpegNeeds).getByRole('checkbox') as HTMLInputElement;
    // Start cannot fetch a model whose terms nobody accepted.
    expect(screen.queryByRole('button', { name: /then upscale$/ })).toBeNull();
    await user.click(blurTerms);
    expect(blurTerms.checked).toBe(true);
    expect(jpegTerms.checked).toBe(false);
    expect(screen.queryByRole('button', { name: /then upscale$/ })).toBeNull();
    await user.click(within(blurNeeds).getByRole('button', { name: /^Download model/ }));
    await waitFor(() =>
      expect(api.downloadModel).toHaveBeenCalledWith('nafnet_gopro_deblur', true),
    );

    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the JPEG fix model' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('checkbox', { name: /reviewed.*license/i }));
    await user.click(within(library).getByRole('button', { name: /^Download/ }));
    await waitFor(() => expect(api.downloadModel).toHaveBeenCalledWith('fbcnn_color', true));
    await user.click(screen.getByRole('button', { name: 'Close model library' }));
    expect((within(blurNeeds).getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
    expect((within(jpegNeeds).getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  });

  it('clears a remembered fix on a custom upscaler without keeping the old checkpoint', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    Object.assign(snapshot.settings, {
      task: 'upscale',
      quality: 'custom',
      selected_model_id: 'hat_s_x4',
      fixes: ['noise'],
      fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }],
      preset_pins: { 'fix/noise/best': 'scunet_color_real_psnr' },
    });
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the noise fix model' }));
    await user.click(screen.getByRole('button', { name: 'Back to the default' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'hat_s_x4',
        quality: 'custom',
        fix_stages: [{ fix: 'noise', model_id: 'nafnet_sidd_width64' }],
        preset_pins: {},
      }),
    );
  });

  it('drops saved Faces quietly on load and across Restore and Upscale', async () => {
    const snapshot = readySnapshot([image('portrait', true)]);
    snapshot.engine!.features.push('face_aware');
    Object.assign(snapshot.settings, {
      task: 'upscale',
      quality: 'best',
      selected_model_id: 'hat_l_x4_imagenet',
      preset_pins: { 'upscale/photo/best': 'hat_l_x4_imagenet' },
      fixes: ['faces'],
      enable_face_model: true,
    });
    const user = await mountWith(snapshot);
    await waitFor(() => expect(lastSaved()).toMatchObject({ enable_face_model: false, fixes: [] }));
    expect(screen.queryByRole('checkbox', { name: 'Faces' })).toBeNull();
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({ enable_face_model: false, fixes: ['noise'] }),
    );
    await user.click(screen.getByRole('radio', { name: '4×' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'upscale',
        enable_face_model: false,
        fixes: ['noise'],
      }),
    );
  });

  it.each([false, true])(
    'downloads every fix once and only starts if preparation completes (cancel: %s)',
    async (cancel) => {
      const snapshot = readySnapshot([image('photo', true)]);
      Object.assign(snapshot.settings, {
        task: 'upscale',
        quality: 'best',
        selected_model_id: 'span_photo_x4',
        fixes: ['blur', 'jpeg', 'noise'],
        fix_stages: [blur, jpeg, { fix: 'noise', model_id: 'nafnet_sidd_width64' }],
      });
      const ids = snapshot.settings.fix_stages.map((stage) => stage.model_id);
      for (const model of snapshot.catalog.models)
        if (ids.includes(model.model_id)) model.installed = false;
      let rejectDownload!: (error: Error) => void;
      api.downloadModel.mockImplementation(async (id: string) => {
        if (cancel)
          return new Promise((_, reject) => {
            rejectDownload = reject;
          });
        snapshot.catalog.models.find((model) => model.model_id === id)!.installed = true;
        api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
      });
      api.cancelDownload.mockImplementationOnce(async () => {
        rejectDownload(new Error('cancelled'));
        return undefined;
      });
      const user = await mountWith(snapshot);
      await user.click(screen.getByRole('button', { name: /^Download .*then upscale$/ }));
      if (cancel) {
        await user.click(await screen.findByRole('button', { name: 'Cancel download' }));
        await waitFor(() => expect(api.cancelDownload).toHaveBeenCalledWith(ids[0]));
        await screen.findByRole('button', { name: /^Download .*then upscale$/ });
        expect(api.startJobs).not.toHaveBeenCalled();
        expect(api.downloadModel).toHaveBeenCalledTimes(1);
      } else {
        await waitFor(() => expect(api.startJobs).toHaveBeenCalled());
        expect(api.downloadModel.mock.calls.map((call) => call[0])).toEqual(ids);
        expect(api.startJobs).toHaveBeenCalledWith(
          expect.objectContaining({ fix_stages: snapshot.settings.fix_stages }),
        );
      }
    },
  );
});

describe('Comparing saved results', () => {
  /** A host with pair slots, answering every pair request with decoded previews. */
  function pairHost(
    snapshot: AppSnapshot,
    sizes: Record<string, { width: number; height: number }> = {},
  ): AppSnapshot {
    let generation = 0;
    snapshot.runtime.comparison_pair = {
      media_id: '',
      generation: 0,
      showing: { target_id: '', preview_data_url: '' },
      against: { target_id: '', preview_data_url: '' },
    };
    const slot = (target: string) =>
      target === 'original'
        ? { target_id: target, preview_data_url: '', width: 640, height: 480 }
        : {
            target_id: target,
            preview_data_url: `data:image/jpeg;base64,${target}`,
            width: sizes[target]?.width ?? 2560,
            height: sizes[target]?.height ?? 1920,
            preview_width: Math.min(sizes[target]?.width ?? 2048, 2048),
            preview_height: Math.min(sizes[target]?.height ?? 1536, 1536),
          };
    api.requestImageComparison.mockImplementation(async (mediaId, pair) => {
      if (!pair) return;
      generation += 1;
      snapshot.runtime.comparison_pair = {
        media_id: mediaId,
        generation,
        showing: slot(pair.showing),
        against: slot(pair.against),
      };
      api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    });
    return snapshot;
  }

  function completedJob(id: string, media = 'photo') {
    return {
      id,
      media_id: media,
      media_name: `${media}.png`,
      media_kind: 'image' as const,
      status: 'completed' as const,
      progress: 100,
      output_path: `/out/${id}.png`,
      error: '',
      created_at: 1,
    };
  }

  it('waits for the history, then decodes the newest result against the Original by id', async () => {
    const snapshot = pairHost(readySnapshot([image('photo', true)]));
    snapshot.jobs = [completedJob('saved')];
    let answer: (history: MediaResults) => void = () => {};
    api.listMediaResults.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)));
    await mountWith(snapshot);
    expect(api.requestImageComparison).not.toHaveBeenCalled();
    answer(historyOf('photo', [row('saved')]));
    await waitFor(() =>
      expect(api.requestImageComparison).toHaveBeenCalledWith('photo', {
        showing: 'saved',
        against: 'original',
      }),
    );
    // One saved result keeps the compact view without choosers.
    await waitFor(() =>
      expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('saved'),
    );
    expect(screen.queryByRole('button', { name: /^Showing:/ })).toBeNull();
    expect(document.querySelector('.divider-labels')?.textContent).toContain('saved');
  });

  describe('an Edit result of a 4896 × 3264 photo', () => {
    const camera = { ...image('photo', true), width: 4896, height: 3264 };
    const open = async (size: { width: number; height: number }) => {
      const snapshot = pairHost(readySnapshot([camera]), { edited: size });
      snapshot.jobs = [completedJob('edited')];
      api.listMediaResults.mockResolvedValue(
        historyOf('photo', [row('edited', { kind: 'edit', model_label: 'Qwen Edit', scale: 0 })]),
      );
      await mountWith(snapshot);
      await waitFor(() =>
        expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('edited'),
      );
    };

    it('opens in Split with the divider and the three views, as an upscale does', async () => {
      // The engine's Edit size of 512 px gives 512 × 320 for this photo.
      await open({ width: 512, height: 320 });
      expect(screen.getByRole('slider', { name: 'Before and after comparison' })).toBeTruthy();
      const views = screen.getByRole('radiogroup', { name: 'Comparison view' });
      expect(
        within(views)
          .getAllByRole('radio')
          .map((view) => view.textContent?.trim()),
      ).toEqual(['Original', 'Split', 'Enhanced']);
      expect(within(views).getByRole('radio', { name: 'Split' }).getAttribute('aria-checked')).toBe(
        'true',
      );
      expect(document.querySelector('.comparison-note')).toBeNull();
      expect(document.querySelector('.divider-labels')?.textContent).toContain(
        'Qwen Edit · 512 × 320',
      );
      expect(document.querySelector('.source-image.contained')).toBeNull();
    });

    it('lets M peek at the original', async () => {
      await open({ width: 1024, height: 672 });
      expect(screen.getByRole('slider', { name: 'Before and after comparison' })).toBeTruthy();
      const result = document.querySelector<HTMLElement>('.result-image')!;
      const clipBefore = result.style.clipPath;
      await fireEvent.keyDown(window, { key: 'm' });
      expect(document.querySelector('.original-chip')?.textContent).toBe('Original');
      expect(document.querySelector<HTMLElement>('.result-image')!.style.clipPath).toBe(
        'inset(0 0 0 100%)',
      );
      await fireEvent.keyUp(window, { key: 'm' });
      expect(document.querySelector('.original-chip')).toBeNull();
      expect(document.querySelector<HTMLElement>('.result-image')!.style.clipPath).toBe(clipBefore);
    });

    it('stays alone, with the note, when it has another shape', async () => {
      await open({ width: 512, height: 512 });
      expect(screen.queryByRole('slider', { name: 'Before and after comparison' })).toBeNull();
      expect(screen.queryByRole('radiogroup', { name: 'Comparison view' })).toBeNull();
      expect(document.querySelector('.comparison-note')?.textContent).toContain(
        'has a different framing from the original',
      );
    });
  });

  describe('Use as input', () => {
    const useButton = () => screen.queryByRole('button', { name: 'Use as input' });

    it('is offered only while a finished result is on screen', async () => {
      const snapshot = pairHost(readySnapshot([image('photo', true)]));
      await mountWith(snapshot);
      expect(useButton()).toBeNull();
      cleanup();
      const finished = pairHost(readySnapshot([image('photo', true)]));
      finished.jobs = [completedJob('a1')];
      api.listMediaResults.mockResolvedValue(historyOf('photo', [row('a1')]));
      await mountWith(finished);
      expect(await screen.findByRole('button', { name: 'Use as input' })).toBeTruthy();
      expect(useButton()?.getAttribute('title')).toBe(
        'Add this result to Inputs so it can be processed again',
      );
    });

    it('asks the host for the shown result, then shows the Inputs tab', async () => {
      const snapshot = pairHost(readySnapshot([image('photo', true)]));
      snapshot.jobs = [completedJob('a1')];
      api.listMediaResults.mockResolvedValue(historyOf('photo', [row('a1')]));
      api.listAllMediaResults.mockResolvedValue([historyOf('photo', [row('a1')])]);
      const user = await mountWith(snapshot);
      await user.click(screen.getByRole('tab', { name: /Outputs/ }));
      await user.click(await screen.findByRole('button', { name: /^Show a1/ }));
      await user.click(await screen.findByRole('button', { name: 'Use as input' }));
      expect(api.useResultAsInput).toHaveBeenCalledWith('/out/a1.png');
      expect(screen.getByRole('tab', { name: /^Inputs/ }).getAttribute('aria-selected')).toBe(
        'true',
      );
    });

    it('shows the host sentence when the result cannot be used', async () => {
      const snapshot = pairHost(readySnapshot([image('photo', true)]));
      snapshot.jobs = [completedJob('a1')];
      api.listMediaResults.mockResolvedValue(historyOf('photo', [row('a1')]));
      api.useResultAsInput.mockRejectedValueOnce('output path was not authorized');
      const user = await mountWith(snapshot);
      await user.click(await screen.findByRole('button', { name: 'Use as input' }));
      const dialog = await screen.findByRole('dialog');
      expect(within(dialog).getByText('Could not use the result as input')).toBeTruthy();
      expect(within(dialog).getByText(/output path was not authorized/)).toBeTruthy();
    });
  });

  describe('a click on an Outputs row', () => {
    it('shows another photo result during a video run and returns to the running input', async () => {
      const snapshot = pairHost(readySnapshot([video('clip', true), image('photo')]));
      snapshot.settings.task = 'video';
      snapshot.runtime.active_job_id = 'running';
      snapshot.jobs = [
        {
          ...completedJob('running', 'clip'),
          media_kind: 'video',
          status: 'running',
          output_path: '',
        },
        completedJob('saved'),
      ];
      const history = historyOf('photo', [row('saved')]);
      api.listMediaResults.mockResolvedValue(history);
      api.listAllMediaResults.mockResolvedValue([history]);
      api.selectMedia.mockImplementation(async (id) => {
        for (const media of snapshot.media) media.selected = media.id === id;
        api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
      });
      const user = await mountWith(snapshot);
      await user.click(screen.getByRole('tab', { name: /Outputs/ }));
      await user.click(await screen.findByRole('button', { name: 'Show saved, photo.png' }));
      await waitFor(() =>
        expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('saved'),
      );
      await user.click(screen.getByRole('tab', { name: /Inputs/ }));
      await user.click(document.querySelector<HTMLButtonElement>('.media-list .media-select')!);
      await waitFor(() =>
        expect(document.querySelector('.preview-header')?.textContent).toContain('clip.mp4'),
      );
      expect(document.querySelector('.result-image')).toBeNull();
    });
    it('browses a saved video of the running input and returns to live preview from Inputs', async () => {
      const snapshot = pairHost(readySnapshot([video('clip', true)]));
      snapshot.settings.task = 'video';
      snapshot.runtime.active_job_id = 'running';
      snapshot.jobs = [
        {
          ...completedJob('running', 'clip'),
          media_kind: 'video',
          status: 'running',
          output_path: '',
        },
      ];
      const history = historyOf('clip', [
        row('old', { kind: 'video', output_path: '/out/old.mp4' }),
      ]);
      api.listAllMediaResults.mockResolvedValue([history]);
      const user = await mountWith(snapshot);
      await user.click(screen.getByRole('tab', { name: /Outputs/ }));
      await user.click(await screen.findByRole('button', { name: 'Show old, clip.mp4' }));
      await waitFor(() =>
        expect(api.prepareVideoComparison).toHaveBeenCalledWith(
          'clip',
          expect.any(String),
          false,
          'old',
          true,
        ),
      );
      expect(await screen.findByRole('group', { name: 'Video playback controls' })).toBeTruthy();
      expect(screen.getByRole('button', { name: 'Open' })).toBeTruthy();
      await user.click(screen.getByRole('tab', { name: /Inputs/ }));
      await user.click(document.querySelector<HTMLButtonElement>('.media-list .media-select')!);
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Open' })).toBeNull());
      expect(screen.queryByRole('group', { name: 'Video playback controls' })).toBeNull();
      expect(document.querySelector('.source-image')).toBeTruthy();
    });
    const open = async (answer?: (target: string) => Promise<void>) => {
      const snapshot = pairHost(readySnapshot([image('photo', true)]));
      snapshot.jobs = [completedJob('a1'), completedJob('a2')];
      const history = historyOf('photo', [row('a2'), row('a1')]);
      api.listMediaResults.mockResolvedValue(history);
      api.listAllMediaResults.mockResolvedValue([history]);
      const user = await mountWith(snapshot);
      if (answer) {
        const immediate = api.requestImageComparison.getMockImplementation()!;
        api.requestImageComparison.mockImplementation(async (mediaId, pair) => {
          if (pair) await answer(pair.showing);
          await immediate(mediaId, pair);
        });
      }
      await user.click(screen.getByRole('tab', { name: /Outputs/ }));
      return user;
    };
    const views = () =>
      within(screen.getByRole('radiogroup', { name: 'Comparison view' }))
        .getAllByRole('radio')
        .map((view) => view.textContent?.trim());

    it('on Original shows the original alone, without a divider or view options', async () => {
      const user = await open();
      await user.click(await screen.findByRole('button', { name: /^Show the original/ }));
      await waitFor(() => expect(document.querySelector('.source-image')).toBeTruthy());
      expect(screen.queryByRole('slider', { name: 'Before and after comparison' })).toBeNull();
      expect(screen.queryByRole('radiogroup', { name: 'Comparison view' })).toBeNull();
      expect(document.querySelector('.result-image')).toBeNull();
    });

    it('on a result shows the original left and that result right, then another result', async () => {
      const user = await open();
      await user.click(await screen.findByRole('button', { name: /^Show a1/ }));
      await waitFor(() =>
        expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('a1'),
      );
      expect(views()).toEqual(['Original', 'Split', 'Enhanced']);
      await user.click(screen.getByRole('button', { name: /^Show a2/ }));
      await waitFor(() =>
        expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('a2'),
      );
      expect(views()).toEqual(['Original', 'Split', 'Enhanced']);
    });

    it('keeps the previous picture with a loading note until the pair is ready, then splits', async () => {
      let release: () => void = () => {};
      const gate = new Promise<void>((resolve) => (release = resolve));
      const user = await open(async (target) => {
        if (target === 'a2') await gate;
      });
      await user.click(await screen.findByRole('button', { name: /^Show a1/ }));
      await waitFor(() =>
        expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('a1'),
      );
      await user.click(screen.getByRole('button', { name: /^Show a2/ }));
      expect(await screen.findByText('Loading a2…')).toBeTruthy();
      expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('a1');
      expect(screen.queryByRole('radiogroup', { name: 'Comparison view' })).toBeNull();
      expect(document.querySelector('.divider-labels')).toBeNull();
      release();
      await waitFor(() => expect(screen.queryByText('Loading a2…')).toBeNull());
      expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('a2');
      expect(views()).toEqual(['Original', 'Split', 'Enhanced']);
    });

    it('shows the indicator over the original when only that was shown, and ignores a late answer', async () => {
      let release: () => void = () => {};
      const gate = new Promise<void>((resolve) => (release = resolve));
      const user = await open(async (target) => {
        if (target === 'a1') await gate;
      });
      await user.click(await screen.findByRole('button', { name: /^Show the original/ }));
      await user.click(screen.getByRole('button', { name: /^Show a1/ }));
      expect(await screen.findByText('Loading a1…')).toBeTruthy();
      expect(document.querySelector('.source-image')).toBeTruthy();
      expect(document.querySelector('.result-image')).toBeNull();
      expect(screen.queryByRole('radiogroup', { name: 'Comparison view' })).toBeNull();
      await user.click(screen.getByRole('button', { name: /^Show a2/ }));
      await waitFor(() =>
        expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('a2'),
      );
      release();
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('a2');
      expect(screen.queryByText('Loading a1…')).toBeNull();
    });
  });

  describe('Clear in the Outputs tab', () => {
    const open = async (rows: ResultSummary[]) => {
      const snapshot = pairHost(readySnapshot([image('photo', true)]));
      snapshot.jobs = rows.map((entry) => completedJob(entry.job_id));
      let history = historyOf('photo', rows);
      api.listAllMediaResults.mockImplementation(async () => [history]);
      api.clearResults.mockImplementation(async () => {
        history = historyOf('photo', []);
      });
      const user = await mountWith(snapshot);
      await user.click(screen.getByRole('tab', { name: /Outputs/ }));
      await screen.findByRole('checkbox', { name: 'Original, photo.png' });
      return user;
    };

    it('asks with the count, keeps the list on Cancel and empties it on Clear', async () => {
      const user = await open([row('new'), row('old')]);
      await user.click(screen.getByRole('button', { name: 'Clear' }));
      const dialog = screen.getByRole('dialog');
      expect(dialog.textContent).toContain('Clear all 2 outputs from the list?');
      expect(dialog.textContent).toContain(
        'The files stay in their folders; only the list is emptied.',
      );
      expect(document.activeElement?.textContent).toBe('Cancel');
      await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
      expect(api.clearResults).not.toHaveBeenCalled();
      await user.click(screen.getByRole('button', { name: 'Clear' }));
      await user.keyboard('{Escape}');
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(api.clearResults).not.toHaveBeenCalled();
      await user.click(screen.getByRole('button', { name: 'Clear' }));
      await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Clear' }));
      await waitFor(() => expect(api.clearResults).toHaveBeenCalledTimes(1));
      expect(api.clearMedia).not.toHaveBeenCalled();
      await screen.findByText('Results appear here as they finish.');
    });

    it('Unpick all drops the picks only, and clearing the list drops them too', async () => {
      const user = await open([row('new'), row('old')]);
      await user.click(screen.getByRole('checkbox', { name: 'Original, photo.png' }));
      await user.click(screen.getByRole('checkbox', { name: 'old, photo.png' }));
      expect(screen.getByText(/^2 of 4 chosen/).textContent).toContain('Unpick all');
      await user.click(screen.getByRole('button', { name: 'Unpick all' }));
      expect(screen.queryByText(/of 4 chosen/)).toBeNull();
      expect(api.clearResults).not.toHaveBeenCalled();
      expect(screen.getByRole('checkbox', { name: 'old, photo.png' })).toBeTruthy();
      await user.click(screen.getByRole('checkbox', { name: 'old, photo.png' }));
      await user.click(screen.getByRole('button', { name: 'Clear' }));
      await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Clear' }));
      await screen.findByText('Results appear here as they finish.');
      expect(screen.queryByText(/of 4 chosen/)).toBeNull();
    });
  });

  it('opens video Compare only from its button, shows two players and one transport, then three boxes, and releases on closing', async () => {
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
    const snapshot = readySnapshot([image('photo', true), video('clip')]);
    api.listAllMediaResults.mockResolvedValue([
      historyOf('clip', [
        { ...row('slow'), kind: 'video', output_fps: 15 },
        { ...row('fast'), kind: 'video', output_fps: 60 },
      ]),
    ]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('tab', { name: /Outputs/ }));
    await user.click(await screen.findByRole('checkbox', { name: 'slow, clip.mp4' }));
    await user.click(screen.getByRole('checkbox', { name: 'fast, clip.mp4' }));
    expect(document.querySelector('.compare-pane')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Compare these 2' }));
    expect(document.querySelectorAll('.compare-pane video')).toHaveLength(1);
    expect(document.querySelectorAll('.compare-pane .image-stage canvas')).toHaveLength(2);
    expect(
      within(screen.getByRole('group', { name: 'Slot 1 details' })).getByText('Models: slow'),
    ).toBeTruthy();
    expect(
      within(screen.getByRole('group', { name: 'Slot 2 details' })).getByText('Models: fast'),
    ).toBeTruthy();
    expect(screen.getByRole('slider', { name: 'Divider between slot 1 and slot 2' })).toBeTruthy();
    expect(screen.getAllByRole('group', { name: 'Video playback controls' })).toHaveLength(1);
    await waitFor(() =>
      expect(document.activeElement?.getAttribute('aria-label')).toBe('Compare canvas'),
    );
    await user.click(screen.getByRole('checkbox', { name: 'Original, clip.mp4' }));
    expect(document.querySelectorAll('.compare-pane video')).toHaveLength(1);
    expect(document.querySelectorAll('.compare-pane .boxes canvas')).toHaveLength(3);
    await user.click(screen.getByRole('tab', { name: 'Preview' }));
    expect(document.querySelectorAll('video')).toHaveLength(0);
    expect(pause).toHaveBeenCalled();
    expect(load).toHaveBeenCalled();
  });

  it('compares two photo outputs of one source in slider and boxes with job details and omits missing fields', async () => {
    const first = row('first', {
      model_label: 'Upscale',
      stages: [
        { kind: 'restore', model_id: 'fix', model_label: 'Fix' },
        { kind: 'upscale', model_id: 'up', model_label: 'Upscale' },
      ],
      ui: { quality: 'best', content: 'photo', recipe_name: '', fixes: ['blur', 'noise'] },
      scale: 4,
      elapsed_seconds: 95,
      device: 'mps',
      neural_engine: true,
      completed_at: 1_700_000_000_000,
    });
    const older = row('older', {
      model_label: 'Legacy',
      stages: [],
      ui: null,
      scale: 0,
      elapsed_seconds: 0,
      device: '',
      completed_at: 0,
    });
    const snapshot = pairHost(readySnapshot([image('photo', true)]));
    snapshot.jobs = [completedJob('first'), completedJob('older')];
    const sizes: Record<string, { width: number; height: number }> = {
      first: { width: 2560, height: 1920 },
      older: { width: 1280, height: 960 },
    };
    api.requestCompare.mockImplementation(async (picks) => {
      snapshot.runtime.compare = {
        generation: 1,
        slots: picks.map((pick) => {
          const size = pick.kind === 'output' ? sizes[pick.job_id] : { width: 640, height: 480 };
          return {
            pick,
            media_kind: 'image',
            status: 'ready',
            preview_data_url: `data:image/png;base64,${pick.kind === 'output' ? pick.job_id : 'orig'}`,
            error: '',
            width: size.width,
            height: size.height,
            preview_width: size.width,
            preview_height: size.height,
          };
        }),
      };
      api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    });
    api.listAllMediaResults.mockResolvedValue([historyOf('photo', [first, older])]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('tab', { name: /Outputs/ }));
    await user.click(await screen.findByRole('checkbox', { name: 'Upscale, photo.png' }));
    await user.click(screen.getByRole('checkbox', { name: 'Legacy, photo.png' }));
    await user.click(screen.getByRole('button', { name: 'Compare these 2' }));
    expect(screen.getByRole('slider', { name: 'Divider between slot 1 and slot 2' })).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByRole('group', { name: 'Slot 1 details' }).textContent).toContain(
        'Output: 2560 × 1920',
      ),
    );
    const slot1 = screen.getByRole('group', { name: 'Slot 1 details' });
    expect(slot1.textContent).toContain('Models: Fix → Upscale');
    expect(slot1.textContent).toContain('Fixes: Blur → Noise');
    expect(slot1.textContent).toContain('Scale: 4×');
    expect(slot1.textContent).toContain('Time: 1:35');
    expect(slot1.textContent).toContain('Requested device: mps · Neural Engine enabled');
    expect(slot1.textContent).toContain(
      `Date: ${new Date(1_700_000_000_000).toLocaleDateString()}`,
    );
    const slot2 = screen.getByRole('group', { name: 'Slot 2 details' });
    expect(slot2.textContent).toContain('Models: Legacy');
    expect(slot2.textContent).toContain('Output: 1280 × 960');
    expect(slot2.textContent).not.toMatch(/unknown|Fixes:|Scale:|Time:|Requested device:|Date:/i);
    await user.click(screen.getByRole('radio', { name: 'Boxes' }));
    expect(document.querySelectorAll('.compare-pane .boxes .box')).toHaveLength(2);
    expect(screen.getByRole('group', { name: 'Slot 1 details' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Slot 2 details' })).toBeTruthy();
  });

  it('ticks grouped originals and outputs into Compare in order and keeps them across tabs', async () => {
    const snapshot = pairHost(readySnapshot([image('photo', true)]));
    snapshot.jobs = [completedJob('new'), completedJob('old')];
    api.listAllMediaResults.mockResolvedValue([historyOf('photo', [row('new'), row('old')])]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('tab', { name: /Outputs/ }));
    const original = await screen.findByRole('checkbox', { name: 'Original, photo.png' });
    const output = screen.getByRole('checkbox', { name: 'old, photo.png' });
    // No picks: Clear alone, no count and no Compare button.
    expect(document.querySelector('.compare-bar')?.textContent?.trim()).toBe('Clear');
    await user.click(original);
    expect(screen.queryByRole('button', { name: /^Compare these/ })).toBeNull();
    expect(screen.getByText(/^1 of 4 chosen/)).toBeTruthy();
    await user.click(output);
    await waitFor(() =>
      expect(api.requestCompare).toHaveBeenLastCalledWith([
        { kind: 'original', media_id: 'photo' },
        { kind: 'output', media_id: 'photo', job_id: 'old' },
      ]),
    );
    // A picked row shows its slot number at its end.
    expect(original.querySelector('.slot-number')?.textContent).toBe('1');
    expect(output.querySelector('.slot-number')?.textContent).toBe('2');
    expect(
      screen.getByRole('checkbox', { name: 'new, photo.png' }).querySelector('.slot-number'),
    ).toBeNull();
    // Picking never changes the centre: no tabs, no Compare, until the button is pressed.
    expect(screen.queryByRole('tablist', { name: 'Main pane' })).toBeNull();
    expect(screen.queryByRole('slider', { name: /Divider between slot 1 and slot 2/ })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Compare these 2' }));
    expect(screen.queryByRole('button', { name: /^Compare these|^Comparing/ })).toBeNull();
    expect(screen.getByText(/^2 of 4 chosen/)).toBeTruthy();
    expect(screen.getByRole('slider', { name: /Divider between slot 1 and slot 2/ })).toBeTruthy();
    expect(
      within(screen.getByRole('tablist', { name: 'Main pane' }))
        .getAllByRole('tab')
        .map((tab) => tab.textContent),
    ).toEqual(['Preview', 'Compare']);
    await user.click(screen.getByRole('tab', { name: 'Preview' }));
    // Back in the preview the tabs are gone, the picks stay and the button is offered again.
    expect(screen.queryByRole('tablist', { name: 'Main pane' })).toBeNull();
    expect(screen.getByText(/^2 of 4 chosen/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Compare these 2' }));
    expect(screen.getByRole('button', { name: 'Swap slots 1 and 2' })).toBeTruthy();
    await fireEvent.keyDown(screen.getByRole('tablist', { name: 'Main pane' }), {
      key: 'ArrowLeft',
    });
    expect(screen.queryByRole('tablist', { name: 'Main pane' })).toBeNull();
  });

  async function openCompareOfTwo() {
    const snapshot = pairHost(readySnapshot([image('photo', true)]));
    snapshot.jobs = [completedJob('new'), completedJob('old')];
    api.listAllMediaResults.mockResolvedValue([historyOf('photo', [row('new'), row('old')])]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('tab', { name: /Outputs/ }));
    const original = await screen.findByRole('checkbox', { name: 'Original, photo.png' });
    const output = screen.getByRole('checkbox', { name: 'old, photo.png' });
    await user.click(original);
    await user.click(output);
    await user.click(screen.getByRole('button', { name: 'Compare these 2' }));
    expect(screen.getByRole('tablist', { name: 'Main pane' })).toBeTruthy();
    return { user, original, output, snapshot };
  }

  it('leaves Compare for the preview on Escape, but not while a dialog is open', async () => {
    const { user } = await openCompareOfTwo();
    await user.click(screen.getByRole('tab', { name: /^Inputs/ }));
    await user.click(screen.getByRole('button', { name: /^Clear$/ }));
    expect(screen.getByRole('dialog')).toBeTruthy();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('tablist', { name: 'Main pane' })).toBeTruthy();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('tablist', { name: 'Main pane' })).toBeNull();
  });

  it('asks the host to compare once per change of picks, however often the state refreshes', async () => {
    let changed: () => void = () => {};
    api.listenForStateChange.mockImplementation(async (handler) => {
      changed = handler;
      return () => undefined;
    });
    const { snapshot } = await openCompareOfTwo();
    expect(api.requestCompare).toHaveBeenCalledTimes(2);
    for (let round = 0; round < 4; round++) {
      api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
      changed();
      await new Promise((resolve) => setTimeout(resolve, 120));
    }
    expect(api.requestCompare).toHaveBeenCalledTimes(2);
  });

  it('closes Compare by itself when fewer than two picks remain', async () => {
    const { user, output } = await openCompareOfTwo();
    await user.click(screen.getByRole('tab', { name: /Outputs/ }));
    await user.click(output);
    expect(screen.queryByRole('tablist', { name: 'Main pane' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Compare these/ })).toBeNull();
    await user.click(output);
    await user.click(screen.getByRole('button', { name: 'Compare these 2' }));
    await user.click(screen.getByRole('button', { name: 'Remove old from the compare' }));
    expect(screen.queryByRole('tablist', { name: 'Main pane' })).toBeNull();
  });

  it('shows the file name with its size and the view options in one row, with no tabs', async () => {
    const snapshot = pairHost(readySnapshot([image('photo', true)]));
    snapshot.jobs = [completedJob('saved')];
    api.listMediaResults.mockResolvedValue(historyOf('photo', [row('saved')]));
    await mountWith(snapshot);
    const header = document.querySelector<HTMLElement>('.preview-header')!;
    expect(within(header).queryByRole('tablist')).toBeNull();
    expect(header.firstElementChild?.classList.contains('header-file')).toBe(true);
    expect(header.querySelector('.file-name')?.getAttribute('title')).toBe('photo.png');
    expect(within(header).getByText('640 × 480')).toBeTruthy();
    expect(await within(header).findByRole('radiogroup', { name: 'Comparison view' })).toBeTruthy();
    expect(document.querySelector('.main-heading')).toBeNull();
  });

  it('keeps the end of a long file name in the list, the header and Outputs, with the full name on hover', async () => {
    const name = 'IMG_490C1E18-B4D1-47E5-AC62-FD97F5A26811.jpeg';
    const snapshot = pairHost(readySnapshot([{ ...image('photo', true), name }]));
    snapshot.jobs = [completedJob('saved')];
    api.listAllMediaResults.mockResolvedValue([historyOf('photo', [row('saved')])]);
    const user = await mountWith(snapshot);
    const named = (scope: string) =>
      document.querySelector(`${scope} .file-name[title="${name}"] .file-name-tail`)?.textContent;
    expect(named('.media-row')).toBe('26811.jpeg');
    expect(named('.preview-header')).toBe('26811.jpeg');
    await user.click(screen.getByRole('tab', { name: /Outputs/ }));
    await waitFor(() => expect(named('.group-head')).toBe('26811.jpeg'));
  });

  it('a click on an Outputs row selects its input and shows that result in the Preview', async () => {
    const current = pairHost(readySnapshot([image('first', true), image('second')]), {
      a1: { width: 1600, height: 1067 },
      b1: { width: 512, height: 320 },
    });
    current.jobs = [completedJob('a1', 'first'), completedJob('b1', 'second')];
    api.listAllMediaResults.mockResolvedValue([
      historyOf('first', [row('a1')]),
      historyOf('second', [row('b1', { kind: 'edit', model_label: 'Qwen Edit', scale: 0 })]),
    ]);
    api.listMediaResults.mockImplementation(async (id: string) =>
      id === 'first' ? historyOf('first', [row('a1')]) : historyOf('second', [row('b1')]),
    );
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.selectMedia.mockImplementation(async (id: string) => {
      current.media.forEach((item) => (item.selected = item.id === id));
      api.refreshSnapshot.mockResolvedValue(structuredClone(current));
    });
    render(App);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('tab', { name: /^Outputs/ }));
    // Two picks, Compare open: a click on a row still goes to the Preview of that picture.
    await user.click(await screen.findByRole('checkbox', { name: 'Original, first.png' }));
    await user.click(screen.getByRole('checkbox', { name: 'a1, first.png' }));
    await user.click(screen.getByRole('button', { name: 'Compare these 2' }));
    expect(screen.getByRole('tablist', { name: 'Main pane' })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Show Qwen Edit, second.png' }));
    expect(api.selectMedia).toHaveBeenCalledWith('second');
    await waitFor(() =>
      expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('b1'),
    );
    expect(screen.queryByRole('tablist', { name: 'Main pane' })).toBeNull();
    // The shown row is marked, and no pick changed.
    expect(
      screen.getByRole('button', { name: 'Show Qwen Edit, second.png' }).closest('.media-row')
        ?.classList,
    ).toContain('selected');
    expect(
      screen.getByRole('checkbox', { name: 'a1, first.png' }).getAttribute('aria-checked'),
    ).toBe('true');
    expect(
      screen.getByRole('checkbox', { name: 'Qwen Edit, second.png' }).getAttribute('aria-checked'),
    ).toBe('false');
  });

  it('drops the picks of an image that left the list, in Compare and in the request to the host', async () => {
    const snapshot = pairHost(readySnapshot([image('first', true), image('second')]));
    snapshot.jobs = [completedJob('a1', 'first'), completedJob('b1', 'second')];
    api.listAllMediaResults.mockResolvedValue([
      historyOf('first', [row('a1')]),
      historyOf('second', [row('b1')]),
    ]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('tab', { name: /Outputs/ }));
    await user.click(await screen.findByRole('checkbox', { name: 'Original, first.png' }));
    await user.click(screen.getByRole('checkbox', { name: 'b1, second.png' }));
    await waitFor(() =>
      expect(api.requestCompare).toHaveBeenLastCalledWith([expect.anything(), expect.anything()]),
    );
    api.removeMedia.mockImplementationOnce(async () => {
      const left = structuredClone(snapshot);
      left.media = left.media.filter((item) => item.id === 'first');
      left.jobs = left.jobs.filter((job) => job.media_id === 'first');
      api.refreshSnapshot.mockResolvedValue(left);
      api.listAllMediaResults.mockResolvedValue([historyOf('first', [row('a1')])]);
    });
    await user.click(screen.getByRole('tab', { name: /^Inputs/ }));
    await user.click(screen.getByRole('button', { name: 'Remove second.png' }));
    await waitFor(() =>
      expect(api.requestCompare).toHaveBeenLastCalledWith([
        { kind: 'original', media_id: 'first' },
      ]),
    );
  });

  it('drops a late history answer that belongs to another image', async () => {
    const snapshot = pairHost(readySnapshot([image('first', true), image('second')]));
    snapshot.jobs = [completedJob('a1', 'first'), completedJob('a2', 'first')];
    api.listMediaResults.mockImplementation(async () =>
      historyOf('second', [row('b1'), row('b2')]),
    );
    await mountWith(snapshot);
    await waitFor(() => expect(api.listMediaResults).toHaveBeenCalledWith('first'));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole('button', { name: /^Showing:/ })).toBeNull();
    expect(screen.queryByText(/b1/)).toBeNull();
  });

  it('cancels only the selected image runs and keeps Cancel queue for everything', async () => {
    const snapshot = readySnapshot([image('photo', true), image('other')]);
    snapshot.runtime.active_job_id = 'trial-1';
    snapshot.jobs = [
      { ...completedJob('other-job', 'other'), status: 'queued', output_path: '' },
      { ...completedJob('trial-1'), status: 'running', output_path: '' },
    ];
    const user = await mountWith(snapshot);
    await user.click(await screen.findByRole('button', { name: 'Cancel runs for this image' }));
    expect(api.cancelJobsForMedia).toHaveBeenCalledWith('photo', false);
    expect(api.cancelJobs).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Cancel queue' })).toBeTruthy();
  });

  it('tells you to download models when fewer than 2 can be compared', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.batch_mode = false;
    snapshot.catalog.models.forEach((model) => (model.installed = false));
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Upscale & restore$/);
    await openPlanSheet(user);
    await user.click(
      await screen.findByRole('button', { name: 'Try other upscalers on this image' }),
    );
    const dialog = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(dialog).getByText(/Comparing needs at least 2 installed models/)).toBeTruthy();
  });

  it('runs 2 to 4 installed models from Try others with the settings the plan card showed', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.batch_mode = false;
    api.estimateModelTrials.mockImplementation(async (_media, ids) =>
      ids.map((id, index) => ({
        model_id: id,
        model_label: id,
        seconds: index === 0 ? 30 : null,
      })),
    );
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Upscale & restore$/);
    await openPlanSheet(user);
    await user.click(
      await screen.findByRole('button', { name: 'Try other upscalers on this image' }),
    );
    const dialog = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(dialog).getByText('Try other upscalers on photo.png')).toBeTruthy();
    const boxes = within(dialog)
      .getAllByRole('checkbox')
      .filter((box) => !(box as HTMLInputElement).disabled);
    expect(boxes.length).toBeGreaterThanOrEqual(2);
    await user.click(boxes[0]);
    expect(
      (within(dialog).getByRole('button', { name: 'Run 1 model' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    await user.click(boxes[1]);
    // A total only when every model was measured; here one is not.
    expect(await within(dialog).findByText(/not yet measured for/)).toBeTruthy();
    const run = within(dialog).getByRole('button', { name: 'Run 2 models' });
    expect(run.classList.contains('primary')).toBe(false);
    await user.click(run);
    expect(api.startModelTrials).toHaveBeenCalledTimes(1);
    expect(api.startJobs).not.toHaveBeenCalled();
    const [media, ids, base] = api.startModelTrials.mock.calls[0];
    expect(media).toBe('photo');
    expect(ids).toHaveLength(2);
    expect(base).toMatchObject({
      media_ids: ['photo'],
      batch_mode: false,
      task: 'upscale',
      output_scale: snapshot.settings.output_scale,
      ui: expect.objectContaining({ content: expect.any(String) }),
    });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull());
  });
});

/** Files dropped on the window, as the native drop listener reports them. */
function dropFiles(paths: string[]): void {
  api.listenForFileDrops.mock.calls[0][0].drop(paths);
}

describe('Dialogs and refused host calls', () => {
  it('a dialog opened over the Clear question replaces it, buttons included', async () => {
    const user = await mountWith(readySnapshot([image('first', true), image('second')]));
    await user.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.getByRole('dialog').textContent).toContain('Clear all 2 items');
    api.addMedia.mockRejectedValueOnce('The file type is not supported.');
    dropFiles(['/x/bad.xyz']);
    await waitFor(() =>
      expect(screen.getByRole('dialog').textContent).toContain('Could not add dropped files'),
    );
    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.queryByRole('button', { name: 'Clear' })).toBeNull();
    expect(dialog.getByRole('button', { name: 'OK' })).toBeTruthy();
    expect(api.clearMedia).not.toHaveBeenCalled();
  });

  it('gives an error dialog the keyboard: Tab stays in, Escape closes, focus returns', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await chooseTask(user, /^Upscale & restore$/);
    api.startJobs.mockRejectedValueOnce('No GPU.');
    const start = screen.getByRole('button', { name: 'Upscale selected' });
    start.focus();
    await user.keyboard('{Enter}');
    const dialog = await screen.findByRole('dialog');
    expect(dialog.contains(document.activeElement)).toBe(true);
    for (let step = 0; step < 4; step++) {
      await user.tab();
      expect(dialog.contains(document.activeElement), `Tab ${step + 1}`).toBe(true);
    }
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(start);
  });

  it('answers the Clear question with Cancel on Escape, and puts Cancel first', async () => {
    const user = await mountWith(readySnapshot([image('first', true), image('second')]));
    await user.click(screen.getByRole('button', { name: 'Clear' }));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.clearMedia).not.toHaveBeenCalled();
  });

  it('closes the model library on Escape and gives it the keyboard', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await chooseMenuItem('view.models');
    const library = await screen.findByRole('dialog', { name: 'Model library' });
    expect(library.contains(document.activeElement)).toBe(true);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull();
  });

  it('closes the update panel on Escape', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await chooseMenuItem('app.updates');
    const panel = await screen.findByRole('dialog', { name: 'Software Update' });
    expect(panel.contains(document.activeElement)).toBe(true);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('keeps the count in the Clear question true while the list changes', async () => {
    const user = await mountWith(readySnapshot([image('first', true), image('second')]));
    await user.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.getByRole('dialog').textContent).toContain('Clear all 2 items');
    const more = readySnapshot([
      image('first', true),
      image('second'),
      image('third'),
      image('fourth'),
      image('fifth'),
    ]);
    api.refreshSnapshot.mockResolvedValue(structuredClone(more));
    (api.listenForStateChange.mock.calls[0][0] as () => void)();
    await waitFor(() =>
      expect(screen.getByRole('dialog').textContent).toContain('Clear all 5 items'),
    );
    // Nothing left to clear: the question goes away.
    api.refreshSnapshot.mockResolvedValue(structuredClone(readySnapshot([])));
    (api.listenForStateChange.mock.calls[0][0] as () => void)();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('ignores menu actions while a question is open, so a job cannot start behind it', async () => {
    const user = await mountWith(readySnapshot([image('first', true), image('second')]));
    await user.click(screen.getByRole('button', { name: 'Clear' }));
    await chooseMenuItem('file.start');
    await chooseMenuItem('file.add-media');
    expect(api.startJobs).not.toHaveBeenCalled();
    expect(api.chooseMediaFiles).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog').textContent).toContain('Clear all 2 items');
    // Cancel is never held back: it ends work instead of starting any.
    await chooseMenuItem('file.cancel');
    expect(api.cancelJobs).toHaveBeenCalledTimes(1);
  });

  it('tells the user when the host refuses a confirmed Clear', async () => {
    const user = await mountWith(readySnapshot([image('first', true), image('second')]));
    api.clearMedia.mockRejectedValueOnce(
      'wait for the current queue to finish or cancel it before changing media',
    );
    await user.click(screen.getByRole('button', { name: 'Clear' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Clear' }));
    await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain('queue'));
    expect(screen.getByRole('dialog').textContent).toContain('Could not clear the list');
  });

  it('keeps the keyboard in the list when the focused Remove button goes with its row', async () => {
    const current = readySnapshot([image('first', true), image('second'), image('third')]);
    const user = await mountWith(current);
    api.removeMedia.mockImplementationOnce(async () => {
      current.media = current.media.filter((item) => item.id !== 'second');
      api.refreshSnapshot.mockResolvedValue(structuredClone(current));
    });
    screen.getByRole('button', { name: 'Remove second.png' }).focus();
    await user.keyboard('{Enter}');
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Remove second.png' })).toBeNull(),
    );
    // The next row's Remove button takes over; after the last row it is the one before.
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Remove third.png' })),
    );
    api.removeMedia.mockImplementationOnce(async () => {
      current.media = current.media.filter((item) => item.id !== 'third');
      api.refreshSnapshot.mockResolvedValue(structuredClone(current));
    });
    await user.keyboard('{Enter}');
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Remove first.png' })),
    );
  });

  describe('when the host refuses what the user asked for', () => {
    const refusal = 'unsupported media file type';
    const shown = async (title: string) => {
      await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain(title));
      expect(screen.getByRole('dialog').textContent).toContain(refusal);
    };

    it('says so for Add Media…', async () => {
      const user = await mountWith(readySnapshot([image('first', true)]));
      api.chooseMediaFiles.mockResolvedValueOnce(['/x/photo.heic']);
      api.addMedia.mockRejectedValueOnce(refusal);
      await user.click(screen.getAllByRole('button', { name: 'Add Media…' })[0]);
      await shown('Could not add files');
    });

    it('says so for Add Folder…', async () => {
      const user = await mountWith(readySnapshot([image('first', true)]));
      api.chooseMediaFolder.mockRejectedValueOnce(refusal);
      await user.click(screen.getByRole('button', { name: 'Add Folder…' }));
      await shown('Could not add the folder');
      api.chooseMediaFolder.mockResolvedValueOnce({ paths: ['/x/a.png'], outputDirectory: '/x' });
      api.addMedia.mockRejectedValueOnce(refusal);
      await user.click(screen.getByRole('button', { name: 'OK' }));
      await user.click(screen.getByRole('button', { name: 'Add Folder…' }));
      await shown('Could not add the folder');
      expect(api.saveSettings).not.toHaveBeenCalledWith(
        expect.objectContaining({ output_directory: '/x' }),
      );
    });

    it('says so for clicking an item', async () => {
      const user = await mountWith(readySnapshot([image('first', true), image('second')]));
      api.selectMedia.mockRejectedValueOnce(refusal);
      await user.click(screen.getByRole('button', { name: /^second\.png/ }));
      await shown('Could not select the item');
    });

    it('says so for Remove', async () => {
      const user = await mountWith(readySnapshot([image('first', true), image('second')]));
      api.removeMedia.mockRejectedValueOnce(refusal);
      await user.click(screen.getByRole('button', { name: 'Remove second.png' }));
      await shown('Could not remove the item');
    });

    it('says so for Open and Reveal on a result', async () => {
      const snapshot = readySnapshot([image('first', true)]);
      snapshot.jobs = [
        {
          id: 'job-first',
          media_id: 'first',
          media_name: 'first.png',
          media_kind: 'image',
          status: 'completed',
          progress: 100,
          output_path: '/private/first-output.png',
          error: '',
          created_at: 1,
        },
      ];
      snapshot.runtime.last_output_path = '/private/first-output.png';
      const user = await mountWith(snapshot);
      api.openResult.mockRejectedValueOnce(refusal);
      await user.click(screen.getByRole('button', { name: 'Open' }));
      await shown('Could not open the result');
      await user.click(screen.getByRole('button', { name: 'OK' }));
      api.revealResult.mockRejectedValueOnce(refusal);
      await user.click(screen.getByRole('button', { name: 'Reveal' }));
      await shown('Could not show the result');
    });

    it('deletes a saved recipe only after confirmation', async () => {
      const snapshot = readySnapshot([image('first', true)]);
      const model = snapshot.catalog.models.find((entry) => entry.native_scale === 4)!;
      snapshot.recipes = [
        {
          id: 'jens67',
          name: 'Jens67',
          task: 'upscale',
          model_id: model.model_id,
          output_scale: 4,
          tile_size: 256,
          halo: 32,
          precision: 'fp32',
          safe_memory: true,
        },
      ];
      const user = await mountWith(snapshot);
      await user.click(screen.getByRole('button', { name: 'Recipes' }));
      await user.click(screen.getByRole('button', { name: 'Delete Jens67' }));
      expect(api.deleteRecipe).not.toHaveBeenCalled();
      await user.click(screen.getByRole('button', { name: 'Confirm delete Jens67' }));
      expect(api.deleteRecipe).toHaveBeenCalledWith('jens67');
    });

    it('says so for Delete on a saved recipe', async () => {
      const snapshot = readySnapshot([image('first', true)]);
      const model = snapshot.catalog.models.find((entry) => entry.native_scale === 4)!;
      snapshot.recipes = [
        {
          id: 'jens67',
          name: 'Jens67',
          task: 'upscale',
          model_id: model.model_id,
          output_scale: 4,
          tile_size: 256,
          halo: 32,
          precision: 'fp32',
          safe_memory: true,
        },
      ];
      const user = await mountWith(snapshot);
      api.deleteRecipe.mockRejectedValueOnce(refusal);
      await user.click(screen.getByRole('button', { name: 'Recipes' }));
      await user.click(screen.getByRole('button', { name: 'Delete Jens67' }));
      expect(api.deleteRecipe).not.toHaveBeenCalled();
      await user.click(screen.getByRole('button', { name: 'Cancel deleting Jens67' }));
      expect(api.deleteRecipe).not.toHaveBeenCalled();
      await user.click(screen.getByRole('button', { name: 'Delete Jens67' }));
      await user.keyboard('{Escape}');
      expect(api.deleteRecipe).not.toHaveBeenCalled();
      await user.click(screen.getByRole('button', { name: 'Delete Jens67' }));
      await user.click(screen.getByRole('button', { name: 'Confirm delete Jens67' }));
      expect(api.deleteRecipe).toHaveBeenCalledWith('jens67');
      await shown('Could not delete the recipe');
    });
  });
});

describe('automatic starts and comparison labels', () => {
  function pairHost(snapshot: AppSnapshot): AppSnapshot {
    let generation = 0;
    snapshot.runtime.comparison_pair = {
      media_id: '',
      generation: 0,
      showing: { target_id: '', preview_data_url: '' },
      against: { target_id: '', preview_data_url: '' },
    };
    const slot = (target: string) =>
      target === 'original'
        ? { target_id: target, preview_data_url: '', width: 640, height: 480 }
        : {
            target_id: target,
            preview_data_url: `data:image/jpeg;base64,${target}`,
            width: 2560,
            height: 1920,
            preview_width: 2048,
            preview_height: 1536,
          };
    api.requestImageComparison.mockImplementation(async (mediaId, pair) => {
      if (!pair) return;
      generation += 1;
      snapshot.runtime.comparison_pair = {
        media_id: mediaId,
        generation,
        showing: slot(pair.showing),
        against: slot(pair.against),
      };
      api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    });
    return snapshot;
  }

  function completedJob(id: string, media = 'photo') {
    return {
      id,
      media_id: media,
      media_name: `${media}.png`,
      media_kind: 'image' as const,
      status: 'completed' as const,
      progress: 100,
      output_path: `/out/${id}.png`,
      error: '',
      created_at: 1,
    };
  }

  it('relaunching an already loaded file with auto_start must run only that file', async () => {
    const current = readySnapshot([image('photo1', true), image('photo2')]);
    current.settings.task = 'upscale';
    current.settings.batch_mode = true;
    current.settings.selected_model_id = current.catalog.models.find(
      (model) => model.native_scale > 1 && !model.purposes.includes('face'),
    )!.model_id;
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.addMedia.mockImplementation(async () => undefined);
    api.saveSettings.mockImplementation(async (s: UiSettings) => {
      current.settings = structuredClone(s);
    });
    api.takeLaunchIntents
      .mockResolvedValueOnce([
        { files: ['/private/photo1.png'], preset: null, recipe: null, auto_start: true },
      ])
      .mockResolvedValueOnce([]);
    render(App);
    await waitFor(() => expect(api.startJobs).toHaveBeenCalled(), { timeout: 3_000 });
    const input = api.startJobs.mock.calls[0] as unknown as [StartBatchInput];
    expect(input[0].media_ids).toEqual(['photo1']);
  });

  it('a launch while the first file still probes keeps the first file queued', async () => {
    const current = readySnapshot();
    current.settings.task = 'upscale';
    current.settings.selected_model_id = current.catalog.models.find(
      (model) => model.native_scale > 1 && !model.purposes.includes('face'),
    )!.model_id;
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.addMedia.mockImplementation(async (paths: string[]) => {
      for (const path of paths) {
        const id = path.split('/').at(-1)!.replace('.png', '');
        const item = image(id, current.media.length === 0);
        if (id === 'slow') item.probe_status = 'pending';
        current.media.push(item);
      }
    });
    api.selectMedia.mockImplementation(async (id: string) => {
      current.media.forEach((item) => (item.selected = item.id === id));
    });
    api.saveSettings.mockImplementation(async (s: UiSettings) => {
      current.settings = structuredClone(s);
    });
    let listener: () => void = () => {};
    api.listenForLaunchIntent.mockImplementation(async (cb: () => void) => {
      listener = cb;
      return () => undefined;
    });
    api.takeLaunchIntents
      .mockResolvedValueOnce([
        { files: ['/private/slow.png'], preset: null, recipe: null, auto_start: true },
      ])
      .mockResolvedValueOnce([]);
    render(App);
    await waitFor(() => expect(api.addMedia).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 700));
    api.takeLaunchIntents
      .mockResolvedValueOnce([
        { files: ['/private/fast.png'], preset: null, recipe: null, auto_start: true },
      ])
      .mockResolvedValueOnce([]);
    listener();
    await new Promise((r) => setTimeout(r, 700));
    current.media.find((m) => m.id === 'slow')!.probe_status = 'ready';
    nativeMenu?.('view.refresh-hardware');
    api.refreshCapabilities.mockResolvedValue(undefined);
    await waitFor(() => expect(api.startJobs).toHaveBeenCalled(), { timeout: 3_000 });
    const ids = (api.startJobs.mock.calls[0] as unknown as [StartBatchInput])[0].media_ids;
    expect(ids.sort()).toEqual(['fast', 'slow']);
  });

  it('with two saved results the split labels the showing side with its model', async () => {
    const snapshot = pairHost(readySnapshot([image('photo', true)]));
    snapshot.jobs = [completedJob('new'), completedJob('old')];
    api.listMediaResults.mockImplementation(async (id: string) =>
      historyOf(id, [
        row('new', { model_label: 'Model NEW' }),
        row('old', { model_label: 'Model OLD' }),
      ]),
    );
    await mountWith(snapshot);
    await waitFor(() =>
      expect(document.querySelector('.result-image')?.getAttribute('src')).toContain('new'),
    );
    const labels = document.querySelector('.divider-labels');
    expect(labels?.textContent ?? '').toContain('Model NEW');
  });

  it('a saved edit size above the device limit leaves Edit usable (select shows the clamp)', async () => {
    const snapshot = editSnapshot(16);
    snapshot.settings.edit_max_dimension = 1024;
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Edit$/);
    await user.type(screen.getByLabelText('What to change'), 'Add snow');
    const select = document.querySelector('#edit-size') as HTMLSelectElement | null;
    if (select) expect(select.value).toBe('512');
    expect(
      (screen.getByRole('button', { name: 'Edit selected' }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('a failed progress listener must not break the Retry comparison button', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    api.listenVideoComparisonProgress.mockRejectedValueOnce(new Error('ipc glitch'));
    await mountWith(snapshot);
    const retry = await screen.findByRole('button', { name: 'Retry comparison' });
    api.prepareVideoComparison.mockClear();
    retry.click();
    await waitFor(() => expect(api.prepareVideoComparison).toHaveBeenCalled(), { timeout: 2_000 });
  });

  it('auto start must not start a video job whose interpolation model is missing', async () => {
    const current = readySnapshot();
    current.settings.task = 'video';
    current.settings.video_interpolation = 'x2' as never;
    current.catalog.interpolation_models.forEach((m) => (m.installed = false));
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.addMedia.mockImplementation(async (paths: string[]) => {
      for (const path of paths) {
        const id = path.split('/').at(-1)!.replace('.mp4', '');
        current.media.push(video(id, current.media.length === 0));
      }
    });
    api.selectMedia.mockImplementation(async (id: string) => {
      current.media.forEach((item) => (item.selected = item.id === id));
    });
    api.saveSettings.mockImplementation(async (s: UiSettings) => {
      current.settings = structuredClone(s);
    });
    api.takeLaunchIntents
      .mockResolvedValueOnce([
        { files: ['/private/clip.mp4'], preset: null, recipe: null, auto_start: true },
      ])
      .mockResolvedValueOnce([]);
    render(App);
    await new Promise((r) => setTimeout(r, 1200));
    expect(api.startJobs).not.toHaveBeenCalled();
  });
});

it('waits for hardware detection before automatically starting an unavailable saved device', async () => {
  const current = readySnapshot([image('photo', true)]);
  current.settings.task = 'upscale';
  current.settings.selected_model_id = current.catalog.models.find(
    (model) => model.native_scale > 1 && !model.purposes.includes('face'),
  )!.model_id;
  current.capabilities.system_ram_total = 0;
  current.capabilities.devices = current.capabilities.devices.filter(
    (device) => device.id !== current.settings.device_id,
  );
  api.bootstrap.mockResolvedValue(structuredClone(current));
  api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
  api.takeLaunchIntents
    .mockResolvedValueOnce([
      { files: ['/private/photo.png'], preset: null, recipe: null, auto_start: true },
    ])
    .mockResolvedValueOnce([]);
  render(App);
  await new Promise((resolve) => setTimeout(resolve, 700));
  expect(api.startJobs).not.toHaveBeenCalled();
  current.capabilities.system_ram_total = 16 * 1024 ** 3;
  await chooseMenuItem('view.refresh-hardware');
  await waitFor(() => expect(api.startJobs).toHaveBeenCalled());
});

it('explains why the menu cannot start when the worker is unavailable', async () => {
  const snapshot = readySnapshot([image('photo', true)]);
  snapshot.runtime.worker = 'unavailable';
  await mountWith(snapshot);
  await chooseMenuItem('file.start');
  expect(screen.getByRole('dialog', { name: 'Cannot start yet' }).textContent).toMatch(
    /engine|worker/i,
  );
});

it('asks for a task before applying a preset', async () => {
  await mountWith(readySnapshot());
  await chooseMenuItem('preset.quick');
  expect(screen.getByRole('dialog', { name: 'Choose a task' }).textContent).toContain(
    'Choose a task before applying a preset.',
  );
});

it('does not ask for a catalog model under Your own file', async () => {
  const user = await mountWith(readySnapshot([image('photo', true)]));
  await user.click(screen.getByRole('radio', { name: 'Same size' }));
  await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
  await openPlanSheet(user);
  await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
  await user.click(screen.getByRole('button', { name: /Your own file/ }));
  expect(
    screen.queryByText('Select a model to see what it is for and where it comes from.'),
  ).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Watch the tutorial' }));
  expect(api.openCustomModelTutorial).toHaveBeenCalledTimes(1);
});

it('saves Run at low priority, Show in menu bar and Notify via setPreference even while a job is running', async () => {
  const snapshot = readySnapshot([image('photo', true)]);
  snapshot.settings.task = 'upscale';
  const user = await mountWith(snapshot);
  // Start a job so processing settings lock.
  const running = structuredClone(snapshot);
  running.runtime.active_job_id = 'job-1';
  api.refreshSnapshot.mockResolvedValue(running);
  (api.listenForStateChange.mock.calls[0][0] as () => void)();
  await waitFor(() =>
    expect(screen.getByRole('group', { name: 'Processing settings' }).matches(':disabled')).toBe(
      true,
    ),
  );

  await chooseMenuItem('app.settings');
  const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
  await user.click(within(settingsDialog).getByRole('switch', { name: /Show LocalSR in the/ }));
  expect(api.setPreference).toHaveBeenCalledWith('show_in_menu_bar', false);
  await user.click(
    within(settingsDialog).getByRole('switch', { name: 'Notify when a file finishes' }),
  );
  expect(api.setPreference).toHaveBeenCalledWith('notify_on_finish', false);

  await user.click(within(settingsDialog).getByRole('tab', { name: 'Performance' }));
  // The host stores the preference before it answers, so a refresh that lands after the
  // click (a slow CI runner) must see it; a stale mock here made the test flaky.
  api.refreshSnapshot.mockResolvedValue({
    ...running,
    settings: { ...running.settings, low_priority: true },
  });
  await user.click(within(settingsDialog).getByRole('switch', { name: 'Run at low priority' }));
  expect(api.setPreference).toHaveBeenCalledWith('low_priority', true);
  expect(api.saveSettings).not.toHaveBeenCalledWith(
    expect.objectContaining({ low_priority: true }),
  );
  await user.click(
    within(settingsDialog).getByRole('switch', { name: 'Keep the computer awake while working' }),
  );
  expect(api.setPreference).toHaveBeenCalledWith('keep_awake', false);
  await waitFor(() =>
    expect(document.querySelector('.status-bar .status-note')?.textContent).toBe('Low priority'),
  );
});

describe('first-launch setup screen', () => {
  it('shows defaults, previews live, saves everything on Done', async () => {
    const snapshot = readySnapshot();
    snapshot.settings.privacy_choice_made = false;
    const user = await mountWith(snapshot);
    const dialog = await screen.findByRole('dialog', { name: 'Welcome to LocalSR' });
    expect(
      (
        within(dialog).getByRole('switch', {
          name: 'Check for updates when LocalSR starts',
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
    await user.click(within(dialog).getByRole('button', { name: 'Continue' }));
    await user.click(within(dialog).getByRole('radio', { name: 'Light' }));
    await user.click(within(dialog).getByRole('switch', { name: 'Colour-blind friendly colours' }));
    await user.click(within(dialog).getByRole('switch', { name: 'High contrast' }));
    expect(document.documentElement.dataset).toMatchObject({
      theme: 'light',
      palette: 'colour-blind',
      contrast: 'high',
    });
    expect(api.setPrivacyChoice).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(api.setPrivacyChoice).toHaveBeenCalledTimes(1));
    expect(api.setPrivacyChoice).toHaveBeenCalledWith({
      appearance: 'light',
      colourBlindPalette: true,
      contrast: 'high',
      checkAtLaunch: true,
      anonymousCount: false,
      crashReports: false,
      showInMenuBar: true,
      completionSound: false,
    });
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Welcome to LocalSR' })).toBeNull(),
    );
  });

  it('saves the tray choice in the atomic setup command', async () => {
    const snapshot = readySnapshot();
    snapshot.settings.privacy_choice_made = false;
    const user = await mountWith(snapshot);
    const dialog = await screen.findByRole('dialog', { name: 'Welcome to LocalSR' });
    await user.click(within(dialog).getByRole('button', { name: 'Continue' }));
    await user.click(within(dialog).getByRole('switch', { name: /Show LocalSR in the/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Done' }));
    await waitFor(() =>
      expect(api.setPrivacyChoice).toHaveBeenCalledWith(
        expect.objectContaining({ showInMenuBar: false }),
      ),
    );
    expect(api.saveSettings).not.toHaveBeenCalledWith(
      expect.objectContaining({ show_in_menu_bar: false }),
    );
  });

  it('keeps choices on page two if saving fails', async () => {
    const snapshot = readySnapshot();
    snapshot.settings.privacy_choice_made = false;
    api.setPrivacyChoice.mockRejectedValueOnce(new Error('save failed'));
    const user = await mountWith(snapshot);
    const dialog = await screen.findByRole('dialog', { name: 'Welcome to LocalSR' });
    await user.click(within(dialog).getByRole('button', { name: 'Continue' }));
    await user.click(
      within(dialog).getByRole('switch', { name: 'Play a sound when a file finishes' }),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Done' }));
    expect((await within(dialog).findByRole('alert')).textContent).toContain('save failed');
    expect(
      (
        within(dialog).getByRole('switch', {
          name: 'Play a sound when a file finishes',
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
  });

  it('enables finish notifications when sound is chosen', async () => {
    const snapshot = readySnapshot();
    snapshot.settings.privacy_choice_made = false;
    snapshot.settings.notify_on_finish = false;
    const user = await mountWith(snapshot);
    const dialog = await screen.findByRole('dialog', { name: 'Welcome to LocalSR' });
    await user.click(within(dialog).getByRole('button', { name: 'Continue' }));
    await user.click(
      within(dialog).getByRole('switch', { name: 'Play a sound when a file finishes' }),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Done' }));
    await waitFor(() =>
      expect(api.setPrivacyChoice).toHaveBeenCalledWith(
        expect.objectContaining({ completionSound: true }),
      ),
    );
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Welcome to LocalSR' })).toBeNull(),
    );
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    expect(
      (
        within(settingsDialog).getByRole('switch', {
          name: 'Notify when a file finishes',
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
    expect(
      (
        within(settingsDialog).getByRole('switch', {
          name: 'Play a sound with the notification',
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
  });

  it('shows only crash reports for a Store build', async () => {
    const snapshot = readySnapshot();
    snapshot.settings.privacy_choice_made = false;
    const original = api.updateStatus.getMockImplementation();
    onTestFinished(() => {
      if (original) api.updateStatus.mockImplementation(original);
    });
    api.updateStatus.mockResolvedValue({ ...(await api.updateStatus()), managed_by_store: true });
    await mountWith(snapshot);
    const dialog = await screen.findByRole('dialog', { name: 'Welcome to LocalSR' });
    expect(within(dialog).getAllByRole('switch')).toHaveLength(1);
    expect(within(dialog).getByRole('switch', { name: 'Offer to report crashes' })).toBeTruthy();
  });

  it('does not show after a choice', async () => {
    await mountWith(readySnapshot());
    expect(screen.queryByRole('dialog', { name: 'Welcome to LocalSR' })).toBeNull();
  });
});

describe('report a problem', () => {
  it('previews diagnostics and includes them only when selected', async () => {
    api.diagnosticSummary.mockResolvedValueOnce(
      JSON.stringify({ platform: 'linux', file: '/home/alice/private.jpg' }),
    );
    const user = await mountWith(readySnapshot());
    nativeMenu?.('help.report-problem');
    const dialog = await screen.findByRole('dialog', { name: 'Report a problem' });
    expect(
      within(dialog).getByText(
        'This opens a new GitHub issue in your browser. Nothing is sent until you submit it there.',
      ),
    ).toBeTruthy();
    await user.type(within(dialog).getByLabelText('What happened?'), 'It stopped');
    await user.click(within(dialog).getByRole('button', { name: 'Open GitHub Issue' }));
    await waitFor(() => expect(api.openBugReport).toHaveBeenCalled());
    let url = new URL(api.openBugReport.mock.calls.at(-1)![0]);
    expect(url.searchParams.get('what-happened')).toBe('It stopped');
    expect(url.searchParams.get('hardware')).not.toContain('alice');
    await user.click(within(dialog).getByLabelText('Include diagnostics'));
    await user.click(within(dialog).getByRole('button', { name: 'Open GitHub Issue' }));
    url = new URL(api.openBugReport.mock.calls.at(-1)![0]);
    expect(url.searchParams.has('hardware')).toBe(false);
  });
});

it('copies long report diagnostics and tells the user to paste them', async () => {
  api.diagnosticSummary.mockResolvedValueOnce(
    JSON.stringify({ platform: 'linux', detail: 'detail '.repeat(1600) }),
  );
  const user = await mountWith(readySnapshot());
  const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
  nativeMenu?.('help.report-problem');
  const dialog = await screen.findByRole('dialog', { name: 'Report a problem' });
  await user.click(within(dialog).getByRole('button', { name: 'Open GitHub Issue' }));
  await waitFor(() => expect(writeText).toHaveBeenCalled());
  expect(
    await within(dialog).findByText('Diagnostics were copied to your clipboard; paste them here.'),
  ).toBeTruthy();
  const url = new URL(api.openBugReport.mock.calls.at(-1)![0]);
  expect(url.searchParams.get('hardware')).toBe(
    'Diagnostics were copied to your clipboard; paste them here.',
  );
});

describe('crash reports', () => {
  const sampleReport = {
    id: 'c5d9cc41-a9a8-4679-8e7f-a2ca008b9a04',
    report: {
      app_version: '0.4.0-beta',
      build_target: 'aarch64-macos',
      os: 'macos',
      os_version: '24.3.0',
      architecture: 'aarch64',
      source: 'engine',
      message: 'The inference engine ended unexpectedly',
      exit: 'signal: 11',
      limitation: 'A native host crash such as a segmentation fault may not be captured.',
    },
  };

  it('shows the crash notice when enabled, lets the user review and send, then deletes the report', async () => {
    const snap = readySnapshot();
    snap.settings.crash_reports = true;
    api.listCrashReports.mockResolvedValueOnce([sampleReport]).mockResolvedValue([]);
    const user = await mountWith(snap);

    const notice = await screen.findByRole('dialog', {
      name: 'LocalSR quit unexpectedly last time.',
    });
    await user.click(within(notice).getByRole('button', { name: 'Review report…' }));

    const reportDialog = await screen.findByRole('dialog', { name: 'Report a problem' });
    expect(
      (within(reportDialog).getByLabelText('What happened?') as HTMLTextAreaElement).value,
    ).toBe('Crash: The inference engine ended unexpectedly');
    const preview = reportDialog.querySelector('textarea[readonly]') as HTMLTextAreaElement;
    expect(preview.value).toBe(JSON.stringify(sampleReport.report, null, 2));

    await user.click(within(reportDialog).getByRole('button', { name: 'Open GitHub Issue' }));
    await waitFor(() => expect(api.openBugReport).toHaveBeenCalledTimes(1));
    expect(api.deleteCrashReport).toHaveBeenCalledWith(sampleReport.id);

    const url = new URL(api.openBugReport.mock.calls[0][0]);
    expect(url.searchParams.get('title')).toBe('Crash: The inference engine ended unexpectedly');
    expect(url.searchParams.get('operating-system')).toBe('macOS');
    expect(url.searchParams.get('hardware')).toContain('signal: 11');
  });

  it('redacts paths in the crash summary when reviewing an unredacted report', async () => {
    const snap = readySnapshot();
    snap.settings.crash_reports = true;
    api.listCrashReports.mockResolvedValueOnce([
      {
        ...sampleReport,
        report: {
          ...sampleReport.report,
          message: 'Failed while reading /Users/alice/Pictures/vacation.png',
        },
      },
    ]);
    const user = await mountWith(snap);

    const notice = await screen.findByRole('dialog', {
      name: 'LocalSR quit unexpectedly last time.',
    });
    await user.click(within(notice).getByRole('button', { name: 'Review report…' }));

    const reportDialog = await screen.findByRole('dialog', { name: 'Report a problem' });
    expect(
      (within(reportDialog).getByLabelText('What happened?') as HTMLTextAreaElement).value,
    ).toBe('Crash: Failed while reading ~/<path>');
  });

  it("Don't Send deletes the report without opening an issue", async () => {
    const snap = readySnapshot();
    snap.settings.crash_reports = true;
    api.listCrashReports.mockResolvedValueOnce([sampleReport]).mockResolvedValue([]);
    const user = await mountWith(snap);

    const notice = await screen.findByRole('dialog', {
      name: 'LocalSR quit unexpectedly last time.',
    });
    await user.click(within(notice).getByRole('button', { name: "Don't Send" }));
    await waitFor(() => expect(api.deleteCrashReport).toHaveBeenCalledWith(sampleReport.id));
    expect(api.openBugReport).not.toHaveBeenCalled();
    expect(
      screen.queryByRole('dialog', { name: 'LocalSR quit unexpectedly last time.' }),
    ).toBeNull();
  });

  it('does not show the crash notice when crash reports are off, but keeps stored reports in Diagnostics', async () => {
    const snap = readySnapshot();
    snap.settings.crash_reports = false;
    api.listCrashReports.mockResolvedValue([sampleReport]);
    await mountWith(snap);
    expect(
      screen.queryByRole('dialog', { name: 'LocalSR quit unexpectedly last time.' }),
    ).toBeNull();
    nativeMenu?.('view.performance');
    const dialog = await screen.findByRole('dialog', { name: 'Performance & Diagnostics' });
    expect(within(dialog).getByText('Stored crash reports: 1')).toBeTruthy();
    expect(
      within(dialog).getByText('Crash reports are off. Turn them on in Settings.'),
    ).toBeTruthy();
  });

  it('manages crash reports in Performance & Diagnostics and links to Settings when off', async () => {
    const snap = readySnapshot();
    snap.settings.crash_reports = true;
    api.listCrashReports.mockResolvedValue([sampleReport]);
    const user = await mountWith(snap);
    await screen.findByRole('dialog', {
      name: 'LocalSR quit unexpectedly last time.',
    });
    await user.keyboard('{Escape}');
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: 'LocalSR quit unexpectedly last time.' }),
      ).toBeNull(),
    );

    nativeMenu?.('view.performance');
    const dialog = await screen.findByRole('dialog', { name: 'Performance & Diagnostics' });
    const section = within(dialog).getByRole('region', { name: 'Crash reports' });
    expect(within(section).queryByLabelText('Offer to Report Crashes')).toBeNull();
    expect(within(section).getByText('Stored crash reports: 1')).toBeTruthy();

    api.listCrashReports.mockResolvedValueOnce([]).mockResolvedValue([]);
    await user.click(within(section).getByRole('button', { name: 'Delete All' }));
    await waitFor(() => expect(api.deleteAllCrashReports).toHaveBeenCalledTimes(1));
    await user.keyboard('{Escape}');
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Performance & Diagnostics' })).toBeNull(),
    );

    // Toggle crash reports off in Settings -> Privacy & updates, then re-open Diagnostics.
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Privacy & updates' }));
    await user.click(
      within(settingsDialog).getByRole('switch', { name: 'Offer to report crashes' }),
    );
    await waitFor(() => expect(api.setPreference).toHaveBeenCalledWith('crash_reports', false));
    await user.click(within(settingsDialog).getByRole('button', { name: 'Done' }));

    nativeMenu?.('view.performance');
    const offDialog = await screen.findByRole('dialog', { name: 'Performance & Diagnostics' });
    expect(
      within(offDialog).getByText('Crash reports are off. Turn them on in Settings.'),
    ).toBeTruthy();
    await user.click(within(offDialog).getByRole('button', { name: 'Open Settings' }));
    const reopened = await screen.findByRole('dialog', { name: 'Settings' });
    expect(
      within(reopened)
        .getByRole('tab', { name: 'Privacy & updates' })
        .getAttribute('aria-selected'),
    ).toBe('true');
  });

  it('toggles Clean up audio with help tip, downloads DeepFilterNet3 on start, disables when silent, and saves in recipes', async () => {
    const media = { ...video('clip', true), has_audio: true };
    const snap = readySnapshot([media]);
    snap.settings.task = 'video';
    snap.settings.selected_model_id = 'span_photo_x4';
    const df3 = snap.catalog.audio_models?.[0];
    if (df3) df3.installed = false;
    const dpdf = snap.catalog.audio_models?.find((m) => m.model_id === 'dpdfnet8_48khz_hr');
    if (dpdf) dpdf.installed = false;
    const upscaler = snap.catalog.models.find((m) => m.model_id === 'span_photo_x4');
    if (upscaler) upscaler.installed = true;

    const user = await mountWith(snap);
    let output = await openOutputSheet(user);
    const toggle = within(output).getByRole('checkbox', {
      name: 'Clean up audio',
    }) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    expect(toggle.disabled).toBe(false);
    expect(within(output).queryByLabelText('Audio model')).toBeNull();

    await user.click(within(output).getByRole('button', { name: 'About Clean up audio' }));
    expect(
      await screen.findByText(/Removes background noise such as hum, wind and hiss from speech/i),
    ).toBeTruthy();

    await user.click(toggle);
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ video_clean_audio: true }),
      ),
    );
    const picker = within(output).getByLabelText('Audio model') as HTMLSelectElement;
    expect(picker.value).toBe('deepfilternet3');
    expect(within(output).getByText('Speech noise removal')).toBeTruthy();
    await openPlanSheet(user);
    const stages = screen.getByRole('list', { name: 'Stages' });
    expect(within(stages).getByText('DeepFilterNet3')).toBeTruthy();
    await user.keyboard('{Escape}');

    // Switching the picker to DPDFNet saves video_audio_model, updates summary & stages.
    output = await openOutputSheet(user);
    await user.selectOptions(within(output).getByLabelText('Audio model'), 'dpdfnet8_48khz_hr');
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ video_audio_model: 'dpdfnet8_48khz_hr' }),
      ),
    );
    expect(within(output).getByText('Stronger speech noise removal')).toBeTruthy();
    await openPlanSheet(user);
    expect(within(screen.getByRole('list', { name: 'Stages' })).getByText('DPDFNet')).toBeTruthy();

    // Save as recipe preserves video_clean_audio and video_audio_model.
    const toggledSnap = structuredClone(snap);
    toggledSnap.settings.video_clean_audio = true;
    toggledSnap.settings.video_audio_model = 'dpdfnet8_48khz_hr';
    api.refreshSnapshot.mockResolvedValue(toggledSnap);
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await user.click(screen.getByRole('button', { name: 'Save current as recipe…' }));
    await user.type(screen.getByPlaceholderText('Name this setup'), 'Clean Speech');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.saveRecipe).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Clean Speech',
          video_clean_audio: true,
          video_audio_model: 'dpdfnet8_48khz_hr',
        }),
      ),
    );

    // Download on first use and start when clicking Download & start.
    const nextSnap = structuredClone(toggledSnap);
    const nextDpdf = nextSnap.catalog.audio_models?.find((m) => m.model_id === 'dpdfnet8_48khz_hr');
    if (nextDpdf) nextDpdf.installed = true;
    api.refreshSnapshot.mockResolvedValue(nextSnap);
    await user.click(screen.getByRole('button', { name: /Download .* start video/i }));
    await waitFor(() => expect(api.downloadModel).toHaveBeenCalledWith('dpdfnet8_48khz_hr', false));
    await waitFor(() =>
      expect(api.startJobs).toHaveBeenCalledWith(
        expect.objectContaining({
          task: 'video',
          video_clean_audio: true,
          video_audio_model: 'dpdfnet8_48khz_hr',
        }),
      ),
    );

    // Open Model Library -> Audio tab lists both models with their licences.
    nativeMenu?.('view.models');
    const dialog = await screen.findByRole('dialog', { name: 'Model library' });
    await user.click(within(dialog).getByRole('button', { name: /^Audio/ }));
    expect(within(dialog).getByRole('heading', { name: 'DeepFilterNet3' })).toBeTruthy();
    expect(within(dialog).getByText('MIT OR Apache-2.0')).toBeTruthy();
    expect(within(dialog).getByText('Apache-2.0')).toBeTruthy();
    expect(within(dialog).getByText(/about 100 MB/i)).toBeTruthy();
    await user.click(within(dialog).getByRole('button', { name: 'MIT OR Apache-2.0 ↗' }));
    expect(api.openModelLicense).toHaveBeenCalledWith('deepfilternet3');
    await user.click(within(dialog).getByRole('button', { name: /^DPDFNet\b/ }));
    expect(within(dialog).getByRole('heading', { name: 'DPDFNet' })).toBeTruthy();
    await user.click(within(dialog).getByRole('button', { name: 'Apache-2.0 ↗' }));
    expect(api.openModelLicense).toHaveBeenCalledWith('dpdfnet8_48khz_hr');
    await user.click(within(dialog).getByRole('button', { name: 'Close model library' }));

    // Video without audio disables Clean up audio with a note.
    const silentSnap = readySnapshot([{ ...video('silent', true), has_audio: false }]);
    silentSnap.settings.task = 'video';
    silentSnap.settings.video_clean_audio = true;
    cleanup();
    const silentUser = await mountWith(silentSnap);
    const silentOutput = await openOutputSheet(silentUser);
    const silentToggle = within(silentOutput).getByRole('checkbox', {
      name: 'Clean up audio',
    }) as HTMLInputElement;
    expect(silentToggle.disabled).toBe(true);
    expect(silentToggle.checked).toBe(false);
    expect(within(silentOutput).getByText('This video has no audio track.')).toBeTruthy();
  });
});

describe('Concurrent model downloads in the Model Library', () => {
  it('runs multiple downloads with per-model progress, cancel, queue state, and isolated errors', async () => {
    const snapshot = readySnapshot();
    const ids = ['span_photo_x4', 'realplksr_nomoswebphoto_x4', 'swinir_m_real_x4_gan', 'hat_s_x4'];
    for (const model of snapshot.catalog.models) {
      if (ids.includes(model.model_id)) {
        model.installed = false;
        model.terms_acceptance_required = false;
      }
    }
    const pending = new Map<string, { resolve: () => void; reject: (error: Error) => void }>();
    api.downloadModel.mockImplementation(
      (id: string) =>
        new Promise<undefined>((resolve, reject) => {
          pending.set(id, { resolve: () => resolve(undefined), reject });
        }),
    );
    let hostSnapshot = structuredClone(snapshot);
    const user = await mountWith(snapshot);
    api.refreshSnapshot.mockImplementation(async () => structuredClone(hostSnapshot));
    const emitWorker = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
    const sendWorker = (message: WorkerEnvelope) => {
      hostSnapshot = applyWorkerEnvelope(hostSnapshot, message);
      emitWorker(message);
    };
    api.cancelDownload.mockImplementation(async (id: string) => {
      sendWorker({ type: 'download-cancelled', data: { model_id: id } });
      pending.get(id)?.reject(new Error('download cancelled'));
      pending.delete(id);
      return undefined;
    });
    await chooseMenuItem('view.models');
    const dialog = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(dialog).getByRole('button', { name: /^Upscale photos/ }));

    const clickRowAndDownload = async (rowPattern: RegExp) => {
      await user.click(within(dialog).getByRole('button', { name: rowPattern }));
      const button = within(dialog).getByRole('button', { name: /^Download / });
      expect((button as HTMLButtonElement).disabled).toBe(false);
      await user.click(button);
    };

    await clickRowAndDownload(/SPAN ×4/);
    sendWorker({ type: 'download-started', data: { model_id: 'span_photo_x4', total_bytes: 100 } });
    sendWorker({
      type: 'download-progress',
      data: { model_id: 'span_photo_x4', downloaded_bytes: 40, total_bytes: 100, progress: 40 },
    });

    await clickRowAndDownload(/RealPLKSR ×4 NomosWebPhoto/);
    sendWorker({
      type: 'download-started',
      data: { model_id: 'realplksr_nomoswebphoto_x4', total_bytes: 100 },
    });
    sendWorker({
      type: 'download-progress',
      data: {
        model_id: 'realplksr_nomoswebphoto_x4',
        downloaded_bytes: 75,
        total_bytes: 100,
        progress: 75,
      },
    });

    await clickRowAndDownload(/SwinIR ×4 Real/);
    sendWorker({
      type: 'download-started',
      data: { model_id: 'swinir_m_real_x4_gan', total_bytes: 100 },
    });

    await clickRowAndDownload(/HAT-S ×4/);
    sendWorker({ type: 'download-queued', data: { model_id: 'hat_s_x4', total_bytes: 100 } });

    expect(api.downloadModel).toHaveBeenCalledTimes(4);

    const downloadsPanel = () => within(dialog).getByRole('region', { name: 'Model downloads' });
    await waitFor(() =>
      expect(downloadsPanel().textContent).toContain('Downloads · 3 active · 1 waiting'),
    );
    expect(within(downloadsPanel()).getByText('40%')).toBeTruthy();
    expect(within(downloadsPanel()).getByText('75%')).toBeTruthy();
    expect(within(downloadsPanel()).getByText(/Waiting/)).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Waiting in queue · Cancel' })).toBeTruthy();

    // Cancel one active download while others keep running.
    await user.click(within(downloadsPanel()).getByRole('button', { name: /Cancel .*SwinIR/ }));
    expect(api.cancelDownload).toHaveBeenCalledWith('swinir_m_real_x4_gan');
    await waitFor(() => expect(within(downloadsPanel()).queryByText(/SwinIR/)).toBeNull());
    // No failure modal or error banner when cancelled intentionally.
    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(within(downloadsPanel()).getByText('40%')).toBeTruthy();
    expect(within(downloadsPanel()).getByText('75%')).toBeTruthy();

    // Fail one download: shows its own inline error without blocking others with a modal.
    sendWorker({
      type: 'download-failed',
      data: { model_id: 'realplksr_nomoswebphoto_x4', error_message: 'download checksum mismatch' },
    });
    pending.get('realplksr_nomoswebphoto_x4')?.reject(new Error('download checksum mismatch'));
    const alert = await within(downloadsPanel()).findByRole('alert');
    expect(alert.textContent).toContain('download checksum mismatch');
    expect(screen.queryByRole('dialog', { name: 'Download failed' })).toBeNull();
    // Dismiss error cleanly.
    await user.click(within(alert).getByRole('button', { name: /Dismiss/ }));
    expect(within(downloadsPanel()).queryByRole('alert')).toBeNull();

    // Clean up remaining pending downloads.
    sendWorker({ type: 'download-completed', data: { model_id: 'span_photo_x4' } });
    pending.get('span_photo_x4')?.resolve();
    sendWorker({ type: 'download-completed', data: { model_id: 'hat_s_x4' } });
    pending.get('hat_s_x4')?.resolve();
  });
});

describe('video Same size, Fix, and Output resolution', () => {
  const lastSaved = (): UiSettings => api.saveSettings.mock.calls.at(-1)![0] as UiSettings;

  it('switches a selected video to Same size without auto-ticking Noise, saves a recipe, and starts jobs', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.catalog.models.forEach((m) => (m.installed = true));
    Object.assign(snapshot.settings, {
      task: 'video',
      video_task: 'upscale',
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: 'span_photo_x4',
      output_scale: 4,
    });
    const persisted = structuredClone(snapshot);
    const user = await mountWith(snapshot);
    // Mirror the real host: saved settings survive addRecipe's refresh().
    api.saveSettings.mockImplementation(async (next: UiSettings) => {
      persisted.settings = structuredClone(next);
    });
    api.refreshSnapshot.mockImplementation(async () => structuredClone(persisted));

    // Switch video to Same size: nothing ticks itself.
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'video',
        video_task: 'denoise',
        fixes: [],
        selected_model_id: '',
      }),
    );

    // Tick Noise, then Compression (prefix fix) before Noise (primary restore).
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await user.click(screen.getByRole('checkbox', { name: 'Compression' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'video',
        video_task: 'denoise',
        fixes: ['jpeg', 'noise'],
      }),
    );

    // Save recipe as a video same-size recipe.
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    await user.click(screen.getByRole('button', { name: 'Save current as recipe…' }));
    await user.type(screen.getByRole('textbox', { name: 'Recipe name' }), 'Video Restore Fix');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.saveRecipe).toHaveBeenCalledTimes(1));
    const savedRecipe = api.saveRecipe.mock.calls.at(-1)![0] as Recipe;
    expect(savedRecipe).toMatchObject({
      name: 'Video Restore Fix',
      task: 'video',
      video_task: 'denoise',
      output_scale: 1,
      fixes: ['jpeg', 'noise'],
    });
    expect(savedRecipe.stages).toHaveLength(2);
    expect(savedRecipe.stages![0]).toMatchObject({
      kind: 'deblock',
      fix: 'jpeg',
    });
    expect(savedRecipe.stages![1]).toMatchObject({
      kind: 'restore',
      fix: 'noise',
    });

    // Start selected video with Same size + prefix fix.
    await user.click(screen.getByRole('button', { name: 'Start selected video' }));
    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs.mock.calls.at(-1)![0]).toMatchObject({
      task: 'video',
      video_task: 'denoise',
      fix_stages: [
        expect.objectContaining({ fix: 'jpeg' }),
        expect.objectContaining({ fix: 'noise' }),
      ],
    });
  });

  it('removes Output scale from Advanced and runs video Same size options-only without a chip', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.catalog.models.forEach((m) => (m.installed = true));
    Object.assign(snapshot.settings, {
      task: 'video',
      video_task: 'upscale',
      selected_video_model_id: 'frame_by_frame',
      selected_model_id: 'span_photo_x4',
      output_scale: 4,
      video_clean_audio: false,
      deflicker: false,
      video_interpolation: 'off',
    });
    const user = await mountWith(snapshot);

    // Advanced is gone for video, and Output scale is not offered.
    expect(screen.queryByRole('button', { name: /^Advanced/ })).toBeNull();
    expect(screen.queryByLabelText('Output scale')).toBeNull();

    // Switch to Same size with nothing ticked: Noise stays off, Start disabled with video note.
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'video',
        video_task: 'denoise',
        fixes: [],
        selected_model_id: '',
      }),
    );
    expect((screen.getByRole('checkbox', { name: 'Noise' }) as HTMLInputElement).checked).toBe(
      false,
    );
    expect(
      screen.getByText(
        'Nothing to run. Pick what to fix, or turn on De-flicker, Frame rate or Clean up audio.',
      ),
    ).toBeTruthy();
    const startBtn = screen.getByRole('button', {
      name: 'Start selected video',
    }) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
    await openPlanSheet(user);
    expect(screen.queryByRole('list', { name: 'Stages' })?.textContent?.trim()).toBe('');
    await user.keyboard('{Escape}');

    // Turning on De-flicker enables Start with no restore model.
    await user.click(screen.getByRole('checkbox', { name: 'De-flicker' }));
    await waitFor(() => expect(startBtn.disabled).toBe(false));
    await user.click(startBtn);
    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs.mock.calls.at(-1)![0]).toMatchObject({
      task: 'video',
      video_task: 'denoise',
      model_id: '',
      fix_stages: [],
      deflicker: true,
    });
  });

  it('migrates saved Upscale 1×, no-chip denoise states on load, and keeps Fix/Size tips and order', async () => {
    // 1) Saved photo Upscale 1× loads as Same size with the same fixes and last fix as model.
    const photo1x = readySnapshot([image('photo', true)]);
    Object.assign(photo1x.settings, {
      task: 'upscale',
      output_scale: 1,
      selected_model_id: 'span_photo_x4',
      fixes: ['blur', 'faces'],
      fix_stages: [{ fix: 'blur', model_id: 'nafnet_gopro_deblur' }],
      enable_face_model: true,
    });
    const user1 = await mountWith(photo1x);
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'denoise',
        output_scale: 1,
        selected_model_id: 'nafnet_gopro_deblur',
        fixes: ['blur'],
        enable_face_model: false,
      }),
    );
    expect(screen.getByRole('radio', { name: 'Same size' }).getAttribute('aria-checked')).toBe(
      'true',
    );
    const sameSizeFixes = Array.from(
      screen.getByRole('group', { name: 'Fix' }).querySelectorAll('.fix-name'),
    ).map((el) => el.textContent?.trim());
    expect(sameSizeFixes).toEqual(['Blur', 'JPEG artifacts', 'Noise', 'Colour']);
    await user1.click(screen.getByRole('button', { name: 'About Fix' }));
    expect(screen.getByRole('tooltip').textContent).toBe(
      'Tick what is wrong with the picture. Each problem gets its own fix at the original size, before the picture is enlarged. Quick picks a fast model for each problem, Best the most careful one. A ticked row names its model; click the name to change it, and LocalSR remembers your choice for that problem. The plan line at the bottom shows the order and lets you change it.',
    );

    // Enlarging uses the same Fix rows.
    await user1.click(screen.getByRole('radio', { name: '4×' }));
    const upscaleFixes = Array.from(
      screen.getByRole('group', { name: 'Fix' }).querySelectorAll('.fix-name'),
    ).map((el) => el.textContent?.trim());
    expect(upscaleFixes).toEqual(['Blur', 'JPEG artifacts', 'Noise', 'Colour']);
    await user1.click(screen.getByRole('button', { name: 'About Size' }));
    expect(screen.getByRole('tooltip').textContent).toBe(
      'Same size keeps the pixel size and runs only the fixes you picked. 2× to 4× enlarges the result after the fixes. The model always works at its own 4× scale; a smaller choice resizes that result down afterwards, so it saves no time.',
    );
    cleanup();

    // 2) Saved video Upscale 1× loads as video Same size, and recipes at 1× display "same size" / "video · same size".
    const video1x = readySnapshot([video('clip', true)]);
    Object.assign(video1x.settings, {
      task: 'video',
      video_task: 'upscale',
      output_scale: 1,
      selected_model_id: 'span_photo_x4',
      fixes: ['jpeg'],
      fix_stages: [{ fix: 'jpeg', model_id: 'fbcnn_color' }],
    });
    video1x.recipes = [
      {
        id: 'old-photo-1x',
        name: 'Old photo 1x',
        task: 'upscale',
        model_id: 'span_photo_x4',
        output_scale: 1,
        tile_size: 256,
        halo: 16,
        precision: 'fp32',
        safe_memory: true,
        fixes: ['blur'],
        stages: [
          { kind: 'restore', model_id: 'nafnet_gopro_deblur', fix: 'blur' },
          { kind: 'upscale', model_id: 'span_photo_x4' },
        ],
      },
      {
        id: 'old-vid-1x',
        name: 'Old video 1x',
        task: 'video',
        video_task: 'upscale',
        model_id: 'span_photo_x4',
        output_scale: 1,
        tile_size: 256,
        halo: 16,
        precision: 'fp16',
        safe_memory: true,
        fixes: ['jpeg'],
        stages: [
          { kind: 'deblock', model_id: 'fbcnn_color', fix: 'jpeg' },
          { kind: 'video', model_id: 'span_photo_x4' },
        ],
      },
    ];
    const user2 = await mountWith(video1x);
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'video',
        video_task: 'denoise',
        output_scale: 1,
        selected_model_id: 'fbcnn_color',
        fixes: ['jpeg'],
      }),
    );
    await user2.click(screen.getByRole('button', { name: 'Recipes' }));
    const oldPhotoBtn = screen.getByRole('button', { name: /^Old photo 1x/ });
    expect(oldPhotoBtn.textContent).toContain('same size · FP32');
    expect(oldPhotoBtn.textContent).not.toContain('1×');
    const oldVidBtn = screen.getByRole('button', { name: /^Old video 1x/ });
    expect(oldVidBtn.textContent).toContain('video · same size · FP16');
    expect(oldVidBtn.textContent).not.toContain('1×');
    expect(oldVidBtn.getAttribute('aria-pressed')).toBe('false');
    await user2.click(oldVidBtn);
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Recipes' })).toBeNull());
    await user2.click(screen.getByRole('button', { name: 'Recipes' }));
    expect(screen.getByRole('button', { name: /^Old video 1x/ }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    await user2.click(screen.getByRole('button', { name: 'About Size' }));
    expect(screen.getByRole('tooltip').textContent).toContain(
      'With nothing picked, only De-flicker, Frame rate and Clean up audio run.',
    );
    cleanup();

    // 3) Saved no-chip video denoise gets the chip its model fits, else Noise.
    const noChipVideo = readySnapshot([video('clip', true)]);
    Object.assign(noChipVideo.settings, {
      task: 'video',
      video_task: 'denoise',
      output_scale: 1,
      selected_model_id: 'fbcnn_color',
      fixes: [],
      fix_stages: [],
    });
    await mountWith(noChipVideo);
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'video',
        video_task: 'denoise',
        selected_model_id: 'fbcnn_color',
        fixes: ['jpeg'],
        fix_stages: [{ fix: 'jpeg', model_id: 'fbcnn_color' }],
      }),
    );
  });
});

describe('Model folder and Windows CPU GPU download notice', () => {
  it('starts a ready launch after a model folder move finishes', async () => {
    const current = readySnapshot();
    current.settings.task = 'upscale';
    current.settings.selected_model_id = current.catalog.models.find(
      (model) => model.native_scale > 1 && !model.purposes.includes('face'),
    )!.model_id;
    let listener: () => void = () => {};
    api.listenForLaunchIntent.mockImplementation(async (cb) => {
      listener = cb;
      return () => undefined;
    });
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.addMedia.mockImplementation(async () => {
      current.media.push(image('photo', true));
    });
    api.selectMedia.mockImplementation(async (id) => {
      current.media.forEach((item) => (item.selected = item.id === id));
    });
    api.chooseModelDirectory.mockResolvedValueOnce('/Volumes/SSD/models');
    let finishMove: () => void = () => {};
    api.setModelDirectory.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finishMove = resolve;
        }),
    );
    const user = await mountWith(current);
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Storage' }));
    await user.click(within(settingsDialog).getByRole('button', { name: /^Model folder / }));
    await waitFor(() => expect(api.setModelDirectory).toHaveBeenCalled());
    api.takeLaunchIntents
      .mockResolvedValueOnce([
        { files: ['/private/photo.png'], preset: null, recipe: null, auto_start: true },
      ])
      .mockResolvedValueOnce([]);
    listener();
    await waitFor(() => expect(api.addMedia).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 700));
    expect(api.startJobs).not.toHaveBeenCalled();
    finishMove();
    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1), { timeout: 3000 });
  });

  it('chooses and resets the model folder from Settings', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.default_model_directory = '/Users/demo/Library/Application Support/LocalSR/models';
    const moved = structuredClone(snapshot);
    moved.settings.model_directory = '/Volumes/SSD/models';

    const user = await mountWith(snapshot);
    api.chooseModelDirectory.mockResolvedValueOnce('/Volumes/SSD/models');
    api.refreshSnapshot.mockResolvedValueOnce(moved).mockResolvedValueOnce(snapshot);

    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Storage' }));

    await user.click(within(settingsDialog).getByRole('button', { name: /^Model folder / }));
    await waitFor(() => expect(api.setModelDirectory).toHaveBeenCalledWith('/Volumes/SSD/models'));
    expect(await within(settingsDialog).findByText('/Volumes/SSD/models')).toBeTruthy();

    await user.click(within(settingsDialog).getByRole('button', { name: 'Reset to default' }));
    await waitFor(() => expect(api.resetModelDirectory).toHaveBeenCalledTimes(1));
    expect(
      await within(settingsDialog).findByText(
        '/Users/demo/Library/Application Support/LocalSR/models',
      ),
    ).toBeTruthy();
  });

  it('shows the missing-folder message once with two buttons and recovery actions in Model library', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.quality = 'quick';
    snapshot.settings.selected_model_id = 'span_photo_x4';
    snapshot.settings.model_directory = '/Volumes/Unplugged/models';
    snapshot.model_directory_missing = true;

    const user = await mountWith(snapshot);

    // Shown once in the panel body, even after opening the Output sheet.
    await openOutputSheet(user);
    const alerts = screen.getAllByRole('alert', { name: 'Model folder unavailable' });
    expect(alerts).toHaveLength(1);
    expect(alerts[0].textContent?.replace(/\s+/g, ' ')).toContain(
      'Model folder not found: /Volumes/Unplugged/models. Reconnect the drive or choose another folder.',
    );
    const chooseBtn = within(alerts[0]).getByRole('button', { name: 'Choose folder…' });
    const defaultBtn = within(alerts[0]).getByRole('button', { name: 'Use default folder' });
    expect(chooseBtn.classList.contains('button')).toBe(true);
    expect(defaultBtn.classList.contains('button')).toBe(true);

    await chooseMenuItem('view.models');
    const dialog = await screen.findByRole('dialog', { name: 'Model library' });
    const libraryAlert = within(dialog).getByRole('alert', { name: 'Model folder unavailable' });
    expect(libraryAlert.textContent).toContain('Reconnect the drive');
    await within(libraryAlert).getByRole('button', { name: 'Reset to default' }).click();
    await waitFor(() => expect(api.resetModelDirectory).toHaveBeenCalledTimes(1));
  });

  it('shows moving progress inside the card, prevents closing Settings, and disables Upscale selected', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.quality = 'quick';
    snapshot.settings.selected_model_id = 'span_photo_x4';
    snapshot.default_model_directory = '/Users/demo/Library/Application Support/LocalSR/models';

    let moveHandler:
      | ((p: {
          moved_bytes: number;
          total_bytes: number;
          moved_files: number;
          total_files: number;
          progress: number;
          current_file: string;
        }) => void)
      | undefined;
    api.listenForModelMove.mockImplementationOnce(async (cb) => {
      moveHandler = cb as typeof moveHandler;
      return () => undefined;
    });
    api.chooseModelDirectory.mockResolvedValueOnce('/Volumes/SSD/models');
    let resolveMove: () => void = () => undefined;
    api.setModelDirectory.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          resolveMove = resolve;
        }),
    );

    const user = await mountWith(snapshot);
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Storage' }));
    await user.click(within(settingsDialog).getByRole('button', { name: /^Model folder / }));

    moveHandler?.({
      moved_bytes: 47,
      total_bytes: 100,
      moved_files: 7,
      total_files: 15,
      progress: 46.8,
      current_file: 'model.pth',
    });

    expect(await within(settingsDialog).findByText('Moving 7 of 15 files…')).toBeTruthy();
    expect(within(settingsDialog).getByText('47%')).toBeTruthy();
    expect(settingsDialog.querySelector('progress.download-progress')).toBeTruthy();
    expect(
      (within(settingsDialog).getByRole('button', { name: 'Done' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: 'Settings' })).toBeTruthy();
    expect(document.querySelector('.enhance-pane .pane-heading span')?.textContent).toBe(
      'Moving models',
    );
    expect(
      (screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement).disabled,
    ).toBe(true);

    resolveMove();
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    expect(
      (within(settingsDialog).getByRole('button', { name: 'Done' }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('shows the dismissable GPU download notice, opens the download page, and remembers dismissal', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.gpu_download_notice =
      'Your AMD Radeon RX 7900 XT can run LocalSR much faster with the DirectML edition.';

    const user = await mountWith(snapshot);
    const notice = screen.getByText(
      'Your AMD Radeon RX 7900 XT can run LocalSR much faster with the DirectML edition.',
    );
    expect(notice).toBeTruthy();

    const bar = notice.closest('.gpu-download-notice') as HTMLElement;
    await user.click(within(bar).getByRole('button', { name: 'Open download page' }));
    expect(api.openDownloadPage).toHaveBeenCalledTimes(1);

    await user.click(within(bar).getByRole('button', { name: 'Not now' }));
    await waitFor(() => expect(api.dismissGpuDownloadNotice).toHaveBeenCalledTimes(1));
    expect(
      screen.queryByText(
        'Your AMD Radeon RX 7900 XT can run LocalSR much faster with the DirectML edition.',
      ),
    ).toBeNull();
  });

  it('offers Colour under Fix for photos and video across sizes', async () => {
    const snapshot = readySnapshot([image('photo', true), video('clip')]);
    const user = await mountWith(snapshot);
    const prevSelect = api.selectMedia.getMockImplementation();
    const prevSave = api.saveSettings.getMockImplementation();
    const prevRefresh = api.refreshSnapshot.getMockImplementation();
    onTestFinished(() => {
      if (prevSelect) api.selectMedia.mockImplementation(prevSelect);
      if (prevSave) api.saveSettings.mockImplementation(prevSave);
      if (prevRefresh) api.refreshSnapshot.mockImplementation(prevRefresh);
    });
    api.saveSettings.mockImplementation(async (next) => {
      snapshot.settings = structuredClone(next);
    });
    api.selectMedia.mockImplementation(async (id) => {
      snapshot.media = snapshot.media.map((m) => ({ ...m, selected: m.id === id }));
    });
    api.refreshSnapshot.mockImplementation(async () => structuredClone(snapshot));

    // Upscale offers Colour row with title, resolves DDColor for Quick and Best, and shows HelpTip.
    const colourBox = screen.getByRole('checkbox', { name: 'Colour' }) as HTMLInputElement;
    expect(colourBox.checked).toBe(false);
    expect(colourBox.closest('.fix-row')?.getAttribute('title')).toBe(
      'Adds colour to black-and-white photos',
    );
    await user.click(colourBox);
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fixes: ['colour'],
          fix_stages: [{ fix: 'colour', model_id: 'ddcolor_modelscope' }],
        }),
      ),
    );
    await openPlanSheet(user);
    const colourTip = screen.getByRole('button', { name: 'About Colour' });
    await user.click(colourTip);
    expect(screen.getByRole('tooltip').textContent).toBe(
      "Adds colour to black-and-white photos. The colours are the model's guess. A photo that already has colour is recoloured too.",
    );

    expect(screen.queryByRole('button', { name: 'Change the colour fix model' })).toBeNull();
    expect(within(screen.getByRole('list', { name: 'Stages' })).getByText('DDColor')).toBeTruthy();
    await user.keyboard('{Escape}');

    // Best uses the same model, and Same size runs it as the primary row labelled "Colour".
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fix_stages: [{ fix: 'colour', model_id: 'ddcolor_modelscope' }],
        }),
      ),
    );

    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    expect((screen.getByRole('checkbox', { name: 'Colour' }) as HTMLInputElement).checked).toBe(
      true,
    );
    await openPlanSheet(user);
    const stages = screen.getByRole('list', { name: 'Stages' });
    expect(within(stages).getByText('Colour')).toBeTruthy();
    expect(within(stages).queryByText('Restore')).toBeNull();
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          task: 'denoise',
          output_scale: 1,
          selected_model_id: 'ddcolor_modelscope',
        }),
      ),
    );

    // Switching to a video keeps Colour available under Fix, but the photo's
    // Colour choice is not carried over: a colour video would be recoloured.
    await user.click(screen.getByRole('button', { name: /▶\s*clip\.mp4/ }));
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Colour' })).toBeTruthy());
    expect((screen.getByRole('checkbox', { name: 'Colour' }) as HTMLInputElement).checked).toBe(
      false,
    );
    expect(api.saveSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ task: 'video', fixes: [] }),
    );
    await user.click(screen.getByRole('radio', { name: '4×' }));
    expect(
      screen.getByRole('checkbox', { name: 'Colour' }).closest('.fix-row')?.getAttribute('title'),
    ).toBe('Adds colour to black-and-white videos');
    await user.click(screen.getByRole('button', { name: 'About Fix' }));
    expect(screen.getByRole('tooltip').textContent).toContain(
      'De-flicker runs after the frames are processed.',
    );
  });
});

describe('Engine manager UI and recommendation card', () => {
  function windowsCpuSnapshot(): AppSnapshot {
    const snapshot = readySnapshot([image('photo', true)]);
    const demoEngines = structuredClone(demoSnapshot().engines!);
    snapshot.engines = demoEngines;
    return snapshot;
  }

  it('shows the GPU engine recommendation card only after first-launch setup, and remembers Not now', async () => {
    const snapshot = windowsCpuSnapshot();
    snapshot.settings.privacy_choice_made = false;

    const user = await mountWith(snapshot);
    expect(screen.getByRole('dialog', { name: 'Welcome to LocalSR' })).toBeTruthy();
    expect(screen.queryByText(/can run LocalSR much faster/)).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Welcome to LocalSR' })).toBeNull(),
    );

    const cardText =
      'Your NVIDIA GeForce RTX 4070 can run LocalSR much faster with the NVIDIA engine (4.3 GB).';
    const card = (await screen.findByText(cardText)).closest(
      '.engine-recommendation-card',
    ) as HTMLElement;
    expect(within(card).getByRole('button', { name: 'Download' })).toBeTruthy();
    await user.click(within(card).getByRole('button', { name: 'Not now' }));

    await waitFor(() => expect(api.dismissGpuDownloadNotice).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(cardText)).toBeNull();

    // Still reachable via Settings -> Performance -> Engine -> Change…
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Performance' }));
    expect(within(settingsDialog).getByText('Engine')).toBeTruthy();
    await user.click(within(settingsDialog).getByRole('button', { name: 'Change…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Engine' });
    expect(within(dialog).getByText('NVIDIA CUDA')).toBeTruthy();
    expect(within(dialog).getByText('Recommended')).toBeTruthy();
  });

  it('hides the engine recommendation card while the update notice is visible', async () => {
    const snapshot = windowsCpuSnapshot();
    const user = await mountWith(snapshot);
    const cardText =
      'Your NVIDIA GeForce RTX 4070 can run LocalSR much faster with the NVIDIA engine (4.3 GB).';
    expect(screen.getByText(cardText)).toBeTruthy();

    updateState.set({
      configured: true,
      managed_by_store: false,
      managed_by_package_manager: false,
      channel: 'beta',
      target: 'windows-x86_64-cpu-native',
      stage: 'available',
      version: '0.6.0-beta',
      notes: '',
      size: 250 * 1024 ** 2,
      downloaded: 0,
      message: '',
      settings_recovery: false,
    });
    expect(await screen.findByText('LocalSR 0.6.0-beta is available')).toBeTruthy();
    expect(screen.queryByText(cardText)).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Close update notice' }));
    expect(await screen.findByText(cardText)).toBeTruthy();
  });

  it('shows fallback intro with HelpTip when no discrete GPU is recommended', async () => {
    const snapshot = windowsCpuSnapshot();
    snapshot.engines!.recommendation = { backend: 'cpu', adapter_name: null };

    const user = await mountWith(snapshot);
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Performance' }));
    await user.click(within(settingsDialog).getByRole('button', { name: 'Change…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Engine' });
    expect(
      within(dialog).getByText('Choose the engine LocalSR uses on this computer.'),
    ).toBeTruthy();
    await user.click(within(dialog).getByRole('button', { name: 'About Engine' }));
    expect(within(dialog).getByRole('tooltip').textContent).toBe(
      'NVIDIA CUDA is fastest on NVIDIA cards. DirectML works with AMD, Intel and NVIDIA cards on Windows. CPU works everywhere and is slowest.',
    );
  });

  it('shows download progress, cancel, and plain-word errors on the recommendation card', async () => {
    const snapshot = windowsCpuSnapshot();
    let resolveInstall: () => void = () => undefined;
    api.installEngine.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          resolveInstall = resolve;
        }),
    );

    const user = await mountWith(snapshot);
    const card = screen
      .getByText(
        'Your NVIDIA GeForce RTX 4070 can run LocalSR much faster with the NVIDIA engine (4.3 GB).',
      )
      .closest('.engine-recommendation-card') as HTMLElement;
    await user.click(within(card).getByRole('button', { name: 'Download' }));
    expect(api.installEngine).toHaveBeenCalledWith('cuda');

    engineStatusHandler?.({
      stage: 'downloading',
      backend: 'cuda',
      engine_id: 'windows-x86_64-cuda-demo',
      downloaded: 2_150_000_000,
      total: 4_574_000_000,
      error: null,
    });
    expect(await screen.findByText(/Downloading NVIDIA engine…/)).toBeTruthy();
    expect(card.querySelector('progress')).toBeTruthy();

    await user.click(within(card).getByRole('button', { name: 'Cancel' }));
    expect(api.cancelEngineInstall).toHaveBeenCalledTimes(1);
    engineStatusHandler?.({
      stage: 'cancelled',
      backend: 'cuda',
      engine_id: 'windows-x86_64-cuda-demo',
      downloaded: 2_150_000_000,
      total: 4_574_000_000,
      error: 'Engine download cancelled',
    });
    resolveInstall();
    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Download' })).toBeTruthy(),
    );

    // Error state shows plain message and allows retry.
    api.installEngine.mockRejectedValueOnce(
      'network error: error sending request for url (https://example.com)',
    );
    await user.click(within(card).getByRole('button', { name: 'Download' }));
    const alert = await within(card).findByRole('alert');
    expect(alert.textContent).toBe(
      'Could not reach the download server. Check your internet connection and try again.',
    );
  });

  it('supports Download, Use, Remove, Cancel, error-in-row with Try again, and Import in the Engine dialog', async () => {
    const snapshot = windowsCpuSnapshot();
    snapshot.engines!.installed.push({
      id: 'windows-x86_64-directml-demo',
      backend: 'directml',
      bundled: false,
      active: false,
      path: '/engines/directml',
    });
    snapshot.engines!.catalog[1].installed = true;

    const afterUse = structuredClone(snapshot);
    afterUse.engines!.active_backend = 'directml';
    afterUse.engines!.installed[0].active = false;
    afterUse.engines!.installed[1].active = true;
    afterUse.engines!.catalog[1].active = true;
    afterUse.engines!.catalog[2].active = false;

    const afterRemove = structuredClone(snapshot);
    afterRemove.engines!.installed = [snapshot.engines!.installed[0]];
    afterRemove.engines!.catalog[1].installed = false;

    const afterImport = structuredClone(snapshot);
    afterImport.engines!.installed.push({
      id: 'windows-x86_64-cuda-imported',
      backend: 'cuda',
      bundled: false,
      active: true,
      path: '/engines/cuda',
    });
    afterImport.engines!.active_backend = 'cuda';
    afterImport.engines!.installed[0].active = false;
    afterImport.engines!.catalog[0].installed = true;
    afterImport.engines!.catalog[0].active = true;

    const user = await mountWith(snapshot);
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Performance' }));
    await user.click(within(settingsDialog).getByRole('button', { name: 'Change…' }));

    const dialog = await screen.findByRole('dialog', { name: 'Engine' });
    const list = within(dialog).getByRole('list', { name: 'Available engines' });
    expect(within(list).getByText('NVIDIA CUDA')).toBeTruthy();
    expect(within(list).getByText('4.3 GB · Not installed')).toBeTruthy();
    expect(within(list).getByText('DirectML')).toBeTruthy();
    expect(within(list).getByText('430 MB · Installed')).toBeTruthy();
    expect(within(list).getByText('CPU')).toBeTruthy();
    expect(within(list).getByText('317 MB · Built in')).toBeTruthy();

    // Only Recommended and Active stay as badges.
    const badges = [...list.querySelectorAll('.engine-badge')].map((el) => el.textContent);
    expect(badges).toEqual(['Recommended', 'Active']);

    // Button classes: only recommended Download is primary, Use is plain compact, Remove is compact ghost danger.
    const cudaRowInitial = within(list).getByText('NVIDIA CUDA').closest('li') as HTMLElement;
    const directmlRow = within(list).getByText('DirectML').closest('li') as HTMLElement;
    const cudaDownloadBtn = within(cudaRowInitial).getByRole('button', { name: 'Download' });
    const directmlUseBtn = within(directmlRow).getByRole('button', { name: 'Use' });
    const directmlRemoveBtn = within(directmlRow).getByRole('button', { name: 'Remove' });
    expect(cudaDownloadBtn.classList.contains('primary')).toBe(true);
    expect(directmlUseBtn.classList.contains('primary')).toBe(false);
    expect(directmlUseBtn.classList.contains('compact')).toBe(true);
    expect(directmlRemoveBtn.classList.contains('ghost')).toBe(true);
    expect(directmlRemoveBtn.classList.contains('danger')).toBe(true);

    // Switch to DirectML
    api.refreshSnapshot.mockResolvedValueOnce(structuredClone(afterUse));
    await user.click(directmlUseBtn);
    await waitFor(() => expect(api.useEngine).toHaveBeenCalledWith('directml'));
    await waitFor(() => expect(within(directmlRow).getByText('Active')).toBeTruthy());

    // Switch back to CPU, then Remove DirectML
    api.refreshSnapshot.mockResolvedValueOnce(structuredClone(snapshot));
    const cpuRow = within(list).getByText('CPU').closest('li') as HTMLElement;
    await waitFor(() =>
      expect(
        (within(cpuRow).getByRole('button', { name: 'Use' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    await user.click(within(cpuRow).getByRole('button', { name: 'Use' }));
    await waitFor(() => expect(api.useEngine).toHaveBeenCalledWith('cpu'));

    api.refreshSnapshot.mockResolvedValueOnce(structuredClone(afterRemove));
    await waitFor(() =>
      expect(
        (within(directmlRow).getByRole('button', { name: 'Remove' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    await user.click(within(directmlRow).getByRole('button', { name: 'Remove' }));
    await waitFor(() =>
      expect(api.removeEngine).toHaveBeenCalledWith('windows-x86_64-directml-demo'),
    );

    // Import from file: cancel file picker does nothing, then valid pick imports and activates
    api.chooseEngineManifest.mockResolvedValueOnce(null);
    await waitFor(() =>
      expect(
        (
          within(dialog).getByRole('button', {
            name: 'Import engine from file…',
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Import engine from file…' }));
    expect(api.importEngine).not.toHaveBeenCalled();

    api.chooseEngineManifest.mockResolvedValueOnce('/downloads/cuda.engine-payload.json');
    api.refreshSnapshot.mockResolvedValueOnce(structuredClone(afterImport));
    await user.click(within(dialog).getByRole('button', { name: 'Import engine from file…' }));
    await waitFor(() =>
      expect(api.importEngine).toHaveBeenCalledWith('/downloads/cuda.engine-payload.json'),
    );
    const cudaRow = within(list).getByText('NVIDIA CUDA').closest('li') as HTMLElement;
    await waitFor(() => expect(within(cudaRow).getByText('Active')).toBeTruthy());

    // Error on remove displays inside the failing row boxed with .download-error and button reads Try again
    api.removeEngine.mockRejectedValueOnce('Not enough space on disk');
    api.refreshSnapshot.mockResolvedValueOnce(structuredClone(snapshot));
    await waitFor(() =>
      expect(
        (within(cpuRow).getByRole('button', { name: 'Use' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    await user.click(within(cpuRow).getByRole('button', { name: 'Use' }));
    await waitFor(() => expect(api.useEngine).toHaveBeenCalledTimes(3));
    const updatedDirectmlRow = within(list).getByText('DirectML').closest('li') as HTMLElement;
    await waitFor(() =>
      expect(
        (within(updatedDirectmlRow).getByRole('button', { name: 'Remove' }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    await user.click(within(updatedDirectmlRow).getByRole('button', { name: 'Remove' }));
    const removeAlert = await within(updatedDirectmlRow).findByRole('alert');
    expect(removeAlert.classList.contains('download-error')).toBe(true);
    expect(removeAlert.textContent).toBe('Not enough space on disk');
    expect(within(updatedDirectmlRow).getByRole('button', { name: 'Try again' })).toBeTruthy();

    // Download hides size meta during progress, supports Cancel, and renders download error inside row with Try again
    let resolveDialogInstall: () => void = () => undefined;
    api.installEngine.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          resolveDialogInstall = resolve;
        }),
    );
    await waitFor(() =>
      expect(
        (within(cudaRow).getByRole('button', { name: 'Download' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    await user.click(within(cudaRow).getByRole('button', { name: 'Download' }));
    expect(api.installEngine).toHaveBeenCalledWith('cuda');
    engineStatusHandler?.({
      stage: 'downloading',
      backend: 'cuda',
      engine_id: 'windows-x86_64-cuda-demo',
      downloaded: 2_150_000_000,
      total: 4_574_000_000,
      error: null,
    });
    expect(await within(cudaRow).findByText(/Downloading…/)).toBeTruthy();
    expect(within(cudaRow).queryByText('4.3 GB · Not installed')).toBeNull();
    expect(within(cudaRow).queryByText('4.3 GB')).toBeNull();
    expect(cudaRow.querySelector('progress')).toBeTruthy();
    await user.click(within(cudaRow).getByRole('button', { name: 'Cancel' }));
    expect(api.cancelEngineInstall).toHaveBeenCalledTimes(1);
    engineStatusHandler?.({
      stage: 'cancelled',
      backend: 'cuda',
      engine_id: 'windows-x86_64-cuda-demo',
      downloaded: 2_150_000_000,
      total: 4_574_000_000,
      error: 'Engine download cancelled',
    });
    resolveDialogInstall();
    await waitFor(() =>
      expect(within(cudaRow).getByRole('button', { name: 'Download' })).toBeTruthy(),
    );

    // Download error renders inside cudaRow with .download-error and button reads Try again
    api.installEngine.mockRejectedValueOnce(
      'network error: error sending request for url (https://example.com)',
    );
    await user.click(within(cudaRow).getByRole('button', { name: 'Download' }));
    const downloadAlert = await within(cudaRow).findByRole('alert');
    expect(downloadAlert.classList.contains('download-error')).toBe(true);
    expect(downloadAlert.textContent).toBe(
      'Could not reach the download server. Check your internet connection and try again.',
    );
    expect(within(cudaRow).getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('Linux AppImage menu prompt', () => {
  it('offers to add LocalSR to the applications menu only after first-launch setup, and remembers Not now', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.privacy_choice_made = false;
    snapshot.linux_menu_offer = true;

    const user = await mountWith(snapshot);
    expect(screen.getByRole('dialog', { name: 'Welcome to LocalSR' })).toBeTruthy();
    expect(screen.queryByText('Add LocalSR to your applications menu?')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Welcome to LocalSR' })).toBeNull(),
    );

    const promptText = await screen.findByText('Add LocalSR to your applications menu?');
    const banner = promptText.closest('.linux-menu-notice') as HTMLElement;
    await user.click(within(banner).getByRole('button', { name: 'Not now' }));

    await waitFor(() => expect(api.dismissLinuxMenuPrompt).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('Add LocalSR to your applications menu?')).toBeNull();
  });

  it('installs the applications menu entry when accepted and removes it from Settings', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.linux_menu_offer = true;

    const user = await mountWith(snapshot);
    const banner = screen
      .getByText('Add LocalSR to your applications menu?')
      .closest('.linux-menu-notice') as HTMLElement;
    await user.click(within(banner).getByRole('button', { name: 'Add to menu' }));

    await waitFor(() => expect(api.installLinuxMenu).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('Add LocalSR to your applications menu?')).toBeNull();

    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(api.uninstallLinuxMenu).toHaveBeenCalledTimes(1));
  });
});

describe('custom checkpoint library', () => {
  it('names a checkpoint, renames it and selects it from the custom group', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    api.chooseCustomModel.mockResolvedValueOnce('/models/my_model.safetensors' as never);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    await user.click(screen.getByRole('button', { name: /Your own file/ }));
    await user.click(screen.getByRole('button', { name: 'Choose checkpoint…' }));
    await openPlanSheet(user);
    expect(await screen.findByText('my model')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    await user.click(screen.getByRole('button', { name: /Your own file/ }));
    await user.click(screen.getByRole('button', { name: 'Rename my model' }));
    const name = screen.getByRole('textbox', { name: 'Model name' });
    await user.clear(name);
    await user.type(name, 'My favourite');
    await user.click(screen.getByRole('button', { name: 'Save name' }));
    expect(api.saveSettings.mock.calls.at(-1)?.[0]).toMatchObject({
      custom_models: [{ path: '/models/my_model.safetensors', name: 'My favourite' }],
    });
    await user.click(screen.getByRole('button', { name: 'Use My favourite' }));
    await openPlanSheet(user);
    expect(await screen.findByText('My favourite')).toBeTruthy();
  });
});

describe('remembered custom checkpoint consent', () => {
  it('checks consent again when reselecting the same checkpoint', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    Object.assign(snapshot.settings, {
      selected_model_id: '__custom__',
      custom_model_path: '/models/mine.pth',
      custom_models: [{ path: '/models/mine.pth', name: 'Mine' }],
      quality: 'custom',
    });
    api.checkpointConsent.mockResolvedValue(true);
    const user = await mountWith(snapshot);
    await waitFor(() => expect(api.checkpointConsent).toHaveBeenCalledWith('/models/mine.pth'));
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    await user.click(screen.getByRole('button', { name: /Your own file/ }));
    await user.click(screen.getByRole('button', { name: 'Use Mine' }));
    await waitFor(() =>
      expect(
        (screen.getByRole('checkbox', { name: /Allow this pickle-based/ }) as HTMLInputElement)
          .checked,
      ).toBe(true),
    );
  });
});

describe('older custom checkpoint settings', () => {
  it('shows a cleaned filename when the saved entry has no name', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    Object.assign(snapshot.settings, {
      task: 'upscale',
      selected_model_id: '__custom__',
      custom_model_path: '/models/old_checkpoint.pth',
      custom_models: [{ path: '/models/old_checkpoint.pth' }],
      quality: 'custom',
    });
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    expect(await screen.findByText('old checkpoint')).toBeTruthy();
  });
});

describe('checkpoint consent after a running job', () => {
  it('checks remembered consent once settings unlock', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    Object.assign(snapshot.settings, {
      task: 'upscale',
      selected_model_id: '__custom__',
      custom_model_path: '/models/mine.pth',
      custom_models: [{ path: '/models/mine.pth', name: 'Mine' }],
      quality: 'custom',
      allow_unsafe_pickle_model: false,
    });
    snapshot.runtime.active_job_id = 'running';
    api.checkpointConsent.mockResolvedValue(true);
    await mountWith(snapshot);
    await waitFor(() => expect(api.listenForStateChange).toHaveBeenCalled());
    const stopped = structuredClone(snapshot);
    stopped.runtime.active_job_id = '';
    api.refreshSnapshot.mockResolvedValue(stopped);
    (api.listenForStateChange.mock.calls[0][0] as () => void)();
    await waitFor(() =>
      expect(
        (screen.getByRole('checkbox', { name: /Allow this pickle-based/ }) as HTMLInputElement)
          .checked,
      ).toBe(true),
    );
  });
});

describe('Settings sheet', () => {
  it('opens from app.settings and is ignored while another dialog or first-launch setup is open', async () => {
    const setupSnap = readySnapshot([image('photo', true)]);
    setupSnap.settings.privacy_choice_made = false;
    await mountWith(setupSnap);
    expect(await screen.findByRole('dialog', { name: 'Welcome to LocalSR' })).toBeTruthy();
    await chooseMenuItem('app.settings');
    expect(screen.queryByRole('dialog', { name: 'Settings' })).toBeNull();
    cleanup();

    const user = await mountWith(readySnapshot([image('photo', true)]));
    await chooseMenuItem('view.models');
    expect(await screen.findByRole('dialog', { name: 'Model library' })).toBeTruthy();
    await chooseMenuItem('app.settings');
    expect(screen.queryByRole('dialog', { name: 'Settings' })).toBeNull();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull());

    await chooseMenuItem('app.settings');
    const dialog = await screen.findByRole('dialog', { name: 'Settings' });
    expect(dialog).toBeTruthy();
    await user.click(within(dialog).getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Settings' })).toBeNull());
  });

  it('stacks System Integrations above Settings and routes External FFmpeg Change… to Storage with focus', async () => {
    const snap = readySnapshot([video('clip', true)]);
    snap.settings.task = 'video';
    const user = await mountWith(snap);

    // Clicking External FFmpeg Change… in Output sheet opens Settings > Storage with #settings-ffmpeg focused.
    const output = await openOutputSheet(user);
    const ffmpegRow = within(output)
      .getByText('External FFmpeg')
      .closest('.field-row') as HTMLElement;
    await user.click(within(ffmpegRow).getByRole('button', { name: 'Change…' }));
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    expect(document.activeElement?.id).toBe('settings-ffmpeg');

    // Opening System Integrations from Settings > General stacks above Settings and closes back to it on Esc.
    await user.click(within(settingsDialog).getByRole('tab', { name: 'General' }));
    await user.click(within(settingsDialog).getByRole('button', { name: 'Manage…' }));
    const integrationsDialog = await screen.findByRole('dialog', { name: 'System Integrations' });
    expect(integrationsDialog).toBeTruthy();
    await user.keyboard('{Escape}');
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'System Integrations' })).toBeNull(),
    );
    expect(screen.getByRole('dialog', { name: 'Settings' })).toBeTruthy();
  });
});

describe('Hardware and low priority in Advanced', () => {
  it('apply to this session and the next start without changing the defaults in Settings', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.device_id = 'mps';
    snapshot.settings.low_priority = false;
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.selectOptions(screen.getByLabelText('Hardware'), 'cpu');
    await user.click(screen.getByRole('checkbox', { name: 'Run at low priority' }));
    expect(api.setRunLowPriority).toHaveBeenCalledWith(true);
    expect(api.saveSettings).not.toHaveBeenCalledWith(
      expect.objectContaining({ device_id: 'cpu' }),
    );
    expect(api.setPreference).not.toHaveBeenCalledWith('low_priority', true);
    expect(document.getElementById('advanced-session-choice')?.textContent).toContain(
      'For this session only',
    );

    await user.click(screen.getByRole('button', { name: 'Upscale selected' }));
    await waitFor(() => expect(api.startJobs).toHaveBeenCalled());
    const input = api.startJobs.mock.calls.at(-1)?.[0] as { device: string };
    expect(input.device).toBe('cpu');

    // Settings still shows the defaults.
    await chooseMenuItem('app.settings');
    const settingsDialog = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settingsDialog).getByRole('tab', { name: 'Performance' }));
    expect(
      (
        within(settingsDialog).getByRole('switch', {
          name: 'Run at low priority',
        }) as HTMLInputElement
      ).checked,
    ).toBe(false);
  });
});

describe('Clearing the model cache', () => {
  it('asks first, and only Clear cache clears', async () => {
    api.getCacheSize.mockResolvedValue(3 * 1024 * 1024 * 1024);
    api.clearCache.mockResolvedValue(0);
    const user = await mountWith(readySnapshot([]));
    await chooseMenuItem('app.settings');
    const settings = await screen.findByRole('dialog', { name: 'Settings' });
    await user.click(within(settings).getByRole('tab', { name: 'Storage' }));
    await user.click(await within(settings).findByRole('button', { name: 'Clear cache' }));
    const question = await screen.findByRole('dialog', { name: 'Clear the model cache?' });
    expect(question.textContent).toContain('3.0 GB');
    await user.click(within(question).getByRole('button', { name: 'Cancel' }));
    expect(api.clearCache).not.toHaveBeenCalled();

    await user.click(within(settings).getByRole('button', { name: 'Clear cache' }));
    const again = await screen.findByRole('dialog', { name: 'Clear the model cache?' });
    await user.click(within(again).getByRole('button', { name: 'Clear cache' }));
    await waitFor(() => expect(api.clearCache).toHaveBeenCalledTimes(1));
  });
});

describe('Stopping a running job', () => {
  function runningVideoSnapshot(overrides: Partial<AppSnapshot['runtime']> = {}) {
    const clip = {
      ...video('clip', true),
      name: 'alpine-lake.mp4',
      frame_count: 288,
      fps: 24,
      duration_seconds: 12,
    };
    const snapshot = readySnapshot([clip]);
    snapshot.settings.task = 'video';
    snapshot.settings.stopped_job_result = 'ask';
    snapshot.runtime = {
      ...snapshot.runtime,
      active_job_id: 'video-job-1',
      progress: 40,
      frames_processed: 115,
      total_frames: 288,
      can_keep_partial: true,
      ...overrides,
    };
    snapshot.jobs = [
      {
        id: 'video-job-1',
        media_id: 'clip',
        media_name: 'alpine-lake.mp4',
        media_kind: 'video',
        status: 'running',
        progress: 40,
        output_path: '',
        error: '',
        created_at: 1,
      },
    ];
    return snapshot;
  }

  it('asks whether to keep or discard for a keepable video with ask, and Esc keeps the job running', async () => {
    const user = await mountWith(runningVideoSnapshot());
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop alpine-lake.mp4?' });
    expect(dialog.textContent).toContain('0:05 of 0:12 is finished.');
    expect(api.cancelJobs).not.toHaveBeenCalled();

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(api.cancelJobs).not.toHaveBeenCalled();
    expect(api.setPreference).not.toHaveBeenCalled();
  });

  it('falls back to Part of the video is finished when duration is unknown, and backdrop click keeps running', async () => {
    const snap = runningVideoSnapshot({ total_frames: 0 });
    snap.media[0].duration_seconds = 0;
    snap.media[0].frame_count = 0;
    const user = await mountWith(snap);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop alpine-lake.mp4?' });
    expect(dialog.textContent).toContain('Part of the video is finished.');

    const backdrop = dialog.parentElement as HTMLElement;
    await fireEvent.click(backdrop);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(api.cancelJobs).not.toHaveBeenCalled();
  });

  it('asks after real frame messages, where each finished frame is followed at once by the next start', async () => {
    // The window keeps only the newest progress message per 100 ms, so a frame's
    // completed message is usually replaced by the next frame's start.
    const snap = runningVideoSnapshot({ frames_processed: 0, can_keep_partial: undefined });
    const user = await mountWith(snap);
    const emitWorker = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
    for (let frame = 0; frame < 5; frame += 1) {
      emitWorker({
        type: 'video_frame_completed',
        data: { job_id: 'video-job-1', frames_processed: frame + 1, total_frames: 288 },
      });
      emitWorker({
        type: 'video_frame_started',
        data: { job_id: 'video-job-1', frame_index: frame + 1, total_frames: 288 },
      });
      emitWorker({
        type: 'video_tile_progress',
        data: {
          job_id: 'video-job-1',
          frame_index: frame + 1,
          completed_tiles: 3,
          total_tiles: 80,
          total_frames: 288,
        },
      });
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByRole('dialog', { name: 'Stop alpine-lake.mp4?' })).toBeTruthy();
    expect(api.cancelJobs).not.toHaveBeenCalled();
  });

  it('counts a finished part of under a second in frames', async () => {
    const user = await mountWith(runningVideoSnapshot({ frames_processed: 2 }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop alpine-lake.mp4?' });
    expect(dialog.textContent).toContain('2 of 288 frames are finished.');
    expect(dialog.textContent).not.toContain('0:00');
  });

  it('asks for a video-model job (SeedVR) too', async () => {
    const snap = runningVideoSnapshot();
    snap.jobs[0].model_id = snap.catalog.video_models[0]?.model_id ?? 'seedvr2_3b';
    snap.runtime.video_memory = {
      peak_bytes: 1,
    } as unknown as AppSnapshot['runtime']['video_memory'];
    const user = await mountWith(snap);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByRole('dialog', { name: 'Stop alpine-lake.mp4?' })).toBeTruthy();
  });

  it('Discard and Keep finished part call cancelJobs with keepPartial false/true and save preference when Dont ask again is ticked', async () => {
    const user = await mountWith(runningVideoSnapshot());
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop alpine-lake.mp4?' });
    await user.click(within(dialog).getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(false));
    expect(api.setPreference).not.toHaveBeenCalled();

    cleanup();
    api.cancelJobs.mockClear();
    api.setPreference.mockClear();

    const user2 = await mountWith(runningVideoSnapshot());
    await user2.click(screen.getByRole('button', { name: 'Cancel' }));
    const dialog2 = await screen.findByRole('dialog', { name: 'Stop alpine-lake.mp4?' });
    await user2.click(within(dialog2).getByRole('checkbox', { name: "Don't ask again" }));
    await user2.click(within(dialog2).getByRole('button', { name: 'Keep finished part' }));
    await waitFor(() =>
      expect(api.setPreference).toHaveBeenCalledWith('stopped_job_result', 'keep'),
    );
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(true));
  });

  it('opens the modal for Cancel runs for this file on an active keepable video in a queue', async () => {
    const snap = runningVideoSnapshot();
    const second = { ...video('clip2'), name: 'second.mp4' };
    snap.media.push(second);
    snap.jobs.push({
      id: 'video-job-2',
      media_id: 'clip2',
      media_name: 'second.mp4',
      media_kind: 'video',
      status: 'queued',
      progress: 0,
      output_path: '',
      error: '',
      created_at: 2,
    });
    const user = await mountWith(snap);
    await user.click(await screen.findByRole('button', { name: 'Cancel runs for this file' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop alpine-lake.mp4?' });
    await user.click(within(dialog).getByRole('checkbox', { name: "Don't ask again" }));
    await user.click(within(dialog).getByRole('button', { name: 'Discard' }));
    await waitFor(() =>
      expect(api.setPreference).toHaveBeenCalledWith('stopped_job_result', 'discard'),
    );
    await waitFor(() => expect(api.cancelJobsForMedia).toHaveBeenCalledWith('clip', false));
  });

  it('cancels at once without modal for photos, 0 finished frames, FFV1-master, temporal engines, or keep/discard settings', async () => {
    // Photo job cancels at once.
    const photoSnap = readySnapshot([image('photo', true)]);
    photoSnap.settings.stopped_job_result = 'ask';
    photoSnap.runtime.active_job_id = 'img-1';
    photoSnap.jobs = [
      {
        id: 'img-1',
        media_id: 'photo',
        media_name: 'photo.png',
        media_kind: 'image',
        status: 'running',
        progress: 50,
        output_path: '',
        error: '',
        created_at: 1,
      },
    ];
    let user = await mountWith(photoSnap);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(false));
    cleanup();
    api.cancelJobs.mockClear();

    // 0 finished frames cancels at once.
    user = await mountWith(runningVideoSnapshot({ frames_processed: 0 }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(false));
    cleanup();
    api.cancelJobs.mockClear();

    // Non-keepable video (FFV1-master or temporal) cancels at once.
    user = await mountWith(runningVideoSnapshot({ can_keep_partial: false }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(false));
    cleanup();
    api.cancelJobs.mockClear();

    // Setting 'keep' cancels at once with keepPartial true.
    const keepSnap = runningVideoSnapshot();
    keepSnap.settings.stopped_job_result = 'keep';
    user = await mountWith(keepSnap);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(true));
    cleanup();
    api.cancelJobs.mockClear();

    // Setting 'discard' cancels at once with keepPartial false.
    const discardSnap = runningVideoSnapshot();
    discardSnap.settings.stopped_job_result = 'discard';
    user = await mountWith(discardSnap);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(false));
  });
});

describe('Stopping a running photo', () => {
  function runningPhotoSnapshot(overrides: Partial<AppSnapshot['runtime']> = {}) {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.stopped_job_result = 'ask';
    snapshot.runtime = {
      ...snapshot.runtime,
      active_job_id: 'img-1',
      progress: 30,
      can_keep_partial: true,
      ...overrides,
    };
    snapshot.jobs = [
      {
        id: 'img-1',
        media_id: 'photo',
        media_name: 'photo.png',
        media_kind: 'image',
        status: 'running',
        progress: 30,
        output_path: '',
        error: '',
        created_at: 1,
      },
    ];
    return snapshot;
  }
  /** The engine's tile count, as the window receives it (one pulse per 100 ms is kept). */
  async function reportTiles(completed: number, total: number, unit?: string): Promise<void> {
    const emitWorker = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
    emitWorker({
      type: 'progress',
      data: {
        job_id: 'img-1',
        percentage: 30,
        completed_tiles: completed,
        total_tiles: total,
        ...(unit ? { unit } : {}),
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 150));
  }

  it('asks once a tile is finished, says how many are, and keeps or discards as answered', async () => {
    const user = await mountWith(runningPhotoSnapshot());
    await reportTiles(12, 40);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop photo.png?' });
    expect(dialog.textContent).toContain(
      '12 of 40 tiles are finished. If you keep it, the photo is saved at full size with the rest left as in the original.',
    );
    expect(api.cancelJobs).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('button', { name: 'Keep finished part' }));
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(true));
    expect(api.setPreference).not.toHaveBeenCalled();
  });

  it('says "is" of a single finished tile', async () => {
    const user = await mountWith(runningPhotoSnapshot());
    await reportTiles(1, 40);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop photo.png?' });
    expect(dialog.textContent).toContain('1 of 40 tiles is finished. If you keep it,');
  });

  it('keeps the tile count through a refresh of the snapshot, which has none', async () => {
    const user = await mountWith(runningPhotoSnapshot());
    await reportTiles(3, 40);
    (api.listenForStateChange.mock.calls[0][0] as () => void)();
    await new Promise((resolve) => setTimeout(resolve, 150));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop photo.png?' });
    expect(dialog.textContent).toContain('3 of 40 tiles are finished.');
    await user.click(within(dialog).getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(false));
  });

  it('cancels at once before the first tile, for the steps of an edit, and when the host cannot keep a photo', async () => {
    // No tile is finished yet: there is nothing to keep.
    let user = await mountWith(runningPhotoSnapshot());
    await reportTiles(0, 40);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(false));
    cleanup();
    api.cancelJobs.mockClear();
    api.listenForWorker.mockClear();

    // An Edit counts steps, not tiles.
    user = await mountWith(runningPhotoSnapshot());
    await reportTiles(12, 40, 'steps');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(false));
    cleanup();
    api.cancelJobs.mockClear();
    api.listenForWorker.mockClear();

    // A host that does not keep photos says nothing, or says no.
    for (const can_keep_partial of [undefined, false]) {
      user = await mountWith(runningPhotoSnapshot({ can_keep_partial }));
      await reportTiles(12, 40);
      await user.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByRole('dialog')).toBeNull();
      await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(false));
      cleanup();
      api.cancelJobs.mockClear();
      api.listenForWorker.mockClear();
    }
  });

  it('follows the saved answer without asking', async () => {
    const keep = runningPhotoSnapshot();
    keep.settings.stopped_job_result = 'keep';
    let user = await mountWith(keep);
    await reportTiles(12, 40);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(true));
    cleanup();
    api.cancelJobs.mockClear();
    api.listenForWorker.mockClear();

    const discard = runningPhotoSnapshot();
    discard.settings.stopped_job_result = 'discard';
    user = await mountWith(discard);
    await reportTiles(12, 40);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(api.cancelJobs).toHaveBeenCalledWith(false));
  });

  it('says what was kept when the stopped photo is saved, not that it finished', async () => {
    const current = runningPhotoSnapshot();
    current.settings.stopped_job_result = 'keep';
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    render(App);
    const user = userEvent.setup();
    const strong = () => document.querySelector('.status-copy strong')?.textContent ?? '';
    await waitFor(() => expect(strong()).toMatch(/^Ready/));
    await reportTiles(12, 40);
    // The host marks the job as stopping and, once the engine has saved the part, as completed.
    api.cancelJobs.mockImplementation(async () => {
      current.jobs[0].status = 'cancelling';
    });
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(strong()).toBe('Stopping…'));
    current.runtime.active_job_id = '';
    current.jobs[0] = { ...current.jobs[0], status: 'completed', output_path: '/out/photo.png' };
    (api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void)({
      type: 'job_completed',
      data: {
        job_id: 'img-1',
        output_path: '/out/photo_partial.png',
        partial: true,
        finished_tiles: Array.from({ length: 12 }, (_, index) => [index * 256, 0, 256, 256]),
        total_tiles: 40,
      },
    });
    await waitFor(() => expect(strong()).toBe('Stopped, kept 12 of 40 tiles'));
    await waitFor(() =>
      expect(document.querySelector('.status-detail')!.textContent).toBe(
        'Stopped. 0 of 1 finished, 1 kept unfinished.',
      ),
    );
  });
});

describe('Size decides', () => {
  const lastSaved = (): UiSettings => api.saveSettings.mock.calls.at(-1)![0] as UiSettings;

  it('switches photo between 4×, Same size (with and without fixes) and 2×', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);

    // Noise ticked, Size -> Same size: Content disabled, Upscale row gone, last row Fix noise, Restore selected.
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'denoise',
        output_scale: 1,
        selected_model_id: 'denoise_realplksr_1x',
      }),
    );
    const contentSame = screen.getByRole('radiogroup', { name: 'Content' });
    expect(contentSame.getAttribute('title')).toBe(
      'Content chooses the upscaler. Nothing is enlarged at Same size.',
    );
    for (const label of ['Photo', 'Illustration']) {
      expect(
        (within(contentSame).getByRole('radio', { name: label }) as HTMLButtonElement).disabled,
      ).toBe(true);
    }
    await openPlanSheet(user);
    expect(within(screen.getByRole('list', { name: 'Stages' })).queryByText(/Upscale/)).toBeNull();
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('Fix noise'),
    ).toBeTruthy();
    await user.keyboard('{Escape}');
    expect(screen.getByText('Fast clean-up')).toBeTruthy();
    expect(screen.getByText('Most careful, slower')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Restore selected' })).toBeTruthy();

    // Untick Noise at Same size: nothing to run, Start disabled, no card download lines, no header Change….
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        fix_stages: [],
        selected_model_id: '',
      }),
    );
    expect((screen.getByRole('checkbox', { name: 'Noise' }) as HTMLInputElement).checked).toBe(
      false,
    );
    expect(
      screen.getByText('Nothing to run. Pick what to fix, or choose a larger size.'),
    ).toBeTruthy();
    const startBtn = screen.getByRole('button', { name: 'Restore selected' }) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
    expect(document.querySelectorAll('.recipe-grid small')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: 'Change…' })).toBeNull();

    // Tick Blur: Start enabled, row Fix blur, selected_model_id nafnet_gopro_deblur.
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'nafnet_gopro_deblur',
      }),
    );
    expect(startBtn.disabled).toBe(false);
    await openPlanSheet(user);
    expect(within(screen.getByRole('list', { name: 'Stages' })).getByText('Fix blur')).toBeTruthy();
    await user.keyboard('{Escape}');

    // Size -> 2×: Blur stays ticked, Content enabled again, row Upscale ×2.
    await user.click(screen.getByRole('radio', { name: '2×' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'upscale',
        output_scale: 2,
        fixes: ['blur'],
      }),
    );
    const content2x = screen.getByRole('radiogroup', { name: 'Content' });
    for (const label of ['Photo', 'Illustration']) {
      expect(
        (within(content2x).getByRole('radio', { name: label }) as HTMLButtonElement).disabled,
      ).toBe(false);
    }
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('Upscale ×2'),
    ).toBeTruthy();
    await user.keyboard('{Escape}');

    // Best then Illustration at 2× keep output_scale at 2.
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await waitFor(() => expect(lastSaved()).toMatchObject({ quality: 'best', output_scale: 2 }));
    await user.click(screen.getByRole('radio', { name: 'Illustration' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({ content: 'illustration', output_scale: 2 }),
    );
  });

  it('clamps scale to a 2× upscaler and disables 3× and 4× with a tooltip', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /Real-ESRGAN ×2/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'realesrgan_x2plus',
        output_scale: 2,
      }),
    );
    for (const label of ['3×', '4×']) {
      const radio = screen.getByRole('radio', { name: label }) as HTMLButtonElement;
      expect(radio.disabled).toBe(true);
      expect(radio.getAttribute('title')).toBe('This model enlarges up to 2×.');
    }
  });

  it('remembers a hand-picked upscaler across Same size and forgets it when a preset is clicked there', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /HAT-L ×4 ImageNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'hat_l_x4_imagenet',
        quality: 'custom',
      }),
    );

    // Same size falls to best (not quick), keeping nafnet_sidd_width64.
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'denoise',
        quality: 'best',
        selected_model_id: 'nafnet_sidd_width64',
      }),
    );

    // Back to 4× brings the hand-picked upscaler and Model · chosen by you.
    await user.click(screen.getByRole('radio', { name: '4×' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'upscale',
        output_scale: 4,
        quality: 'custom',
        selected_model_id: 'hat_l_x4_imagenet',
      }),
    );
    expect(screen.getByText('Model · chosen by you')).toBeTruthy();

    // Clicking Quick at Same size forgets the remembered upscaler.
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await user.click(screen.getByRole('button', { name: /^Quick/ }));
    await user.click(screen.getByRole('radio', { name: '4×' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'upscale',
        quality: 'quick',
        selected_model_id: 'localsr_quick_x4_v1',
      }),
    );
  });

  it('switches fix models between Quick and Best, shows Same size downloads, and honours Use for Noise · Quick', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    for (const id of ['denoise_realplksr_1x', 'nafnet_sidd_width64']) {
      snapshot.catalog.models.find((m) => m.model_id === id)!.installed = false;
    }
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    expect(
      within(screen.getByRole('button', { name: /^Quick/ })).getByText('28 MB download'),
    ).toBeTruthy();
    expect(
      within(screen.getByRole('button', { name: /^Best/ })).getByText('443 MB download'),
    ).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('NAFNet SIDD Width64'),
    ).toBeTruthy();
    await user.keyboard('{Escape}');

    await user.click(screen.getByRole('button', { name: /^Quick/ }));
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('RealPLKSR Denoise'),
    ).toBeTruthy();

    // Pin SCUNet Color for Noise · Quick, switch Best -> Quick, then Back to the default.
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    let library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /SCUNet Color/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for Noise · Quick' }));
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('SCUNet Color'),
    ).toBeTruthy();
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText(/Your pick/),
    ).toBeTruthy();
    await user.keyboard('{Escape}');

    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('NAFNet SIDD Width64'),
    ).toBeTruthy();
    await user.keyboard('{Escape}');

    await user.click(screen.getByRole('button', { name: /^Quick/ }));
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('SCUNet Color'),
    ).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: 'Back to the default' }));
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('RealPLKSR Denoise'),
    ).toBeTruthy();
    expect(lastSaved().preset_pins).toEqual({});
  });

  it('handles video Same size with interpolation only and all video options off', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.catalog.interpolation_models.forEach((m) => (m.installed = true));
    Object.assign(snapshot.settings, {
      task: 'video',
      video_task: 'upscale',
      output_scale: 4,
      video_clean_audio: false,
      deflicker: false,
      video_interpolation: 'off',
    });
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    const startBtn = screen.getByRole('button', {
      name: 'Start selected video',
    }) as HTMLButtonElement;
    expect((screen.getByRole('checkbox', { name: 'Noise' }) as HTMLInputElement).checked).toBe(
      false,
    );
    expect(startBtn.disabled).toBe(true);
    expect(
      screen.getByText(
        'Nothing to run. Pick what to fix, or turn on De-flicker, Frame rate or Clean up audio.',
      ),
    ).toBeTruthy();

    await user.selectOptions(screen.getByLabelText('Frame rate'), 'x2');
    await waitFor(() => expect(startBtn.disabled).toBe(false));
    await user.click(startBtn);
    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs.mock.calls.at(-1)![0]).toMatchObject({
      task: 'video',
      video_task: 'denoise',
      model_id: '',
      fix_stages: [],
      video_interpolation: 'x2',
    });
  });

  it('keeps 2× and Same size across a round trip through Edit', async () => {
    const snapshot = editSnapshot(36);
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('radio', { name: '2×' }));
    await chooseTask(user, /^Edit$/);
    await chooseTask(user, /^Upscale & restore$/);
    await waitFor(() => expect(lastSaved()).toMatchObject({ task: 'upscale', output_scale: 2 }));
    expect(screen.getByRole('radio', { name: '2×' }).getAttribute('aria-checked')).toBe('true');

    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    await chooseTask(user, /^Edit$/);
    await chooseTask(user, /^Upscale & restore$/);
    await waitFor(() => expect(lastSaved()).toMatchObject({ task: 'denoise', output_scale: 1 }));
    expect(screen.getByRole('radio', { name: 'Same size' }).getAttribute('aria-checked')).toBe(
      'true',
    );
  });

  it('applies a task: denoise recipe and migrates loaded no-chip photo denoise settings', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.recipes = [
      {
        id: 'denoise-r',
        name: 'Clean photo',
        task: 'denoise',
        quality: 'quick',
        model_id: 'denoise_realplksr_1x',
        output_scale: 1,
        tile_size: 256,
        halo: 8,
        precision: 'fp16',
        safe_memory: false,
        fixes: ['noise'],
        stages: [{ kind: 'restore', model_id: 'denoise_realplksr_1x', fix: 'noise' }],
      },
    ];
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: 'Recipes' }));
    const recipeBtn = screen.getByRole('button', { name: /^Clean photo/ });
    expect(recipeBtn.textContent).toContain('same size · FP16');
    await user.click(recipeBtn);
    expect(screen.getByRole('radio', { name: 'Same size' }).getAttribute('aria-checked')).toBe(
      'true',
    );
    cleanup();

    const loaded = readySnapshot([image('photo', true)]);
    Object.assign(loaded.settings, {
      task: 'denoise',
      output_scale: 1,
      selected_model_id: 'denoise_realplksr_1x',
      fixes: [],
      fix_stages: [],
    });
    const loadedUser = await mountWith(loaded);
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'denoise',
        fixes: ['noise'],
        fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
      }),
    );
    expect((screen.getByRole('checkbox', { name: 'Noise' }) as HTMLInputElement).checked).toBe(
      true,
    );
    await openPlanSheet(loadedUser);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('Fix noise'),
    ).toBeTruthy();
    await loadedUser.keyboard('{Escape}');
    expect(
      (screen.getByRole('button', { name: 'Restore selected' }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('shows scale choices in Output resolution when SeedVR2 is chosen and hides Size', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    const user = await mountWith(snapshot);
    await openPlanSheet(user);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /^SeedVR2-3B — Labs/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));

    expect(screen.queryByRole('radiogroup', { name: 'Size' })).toBeNull();
    const res = screen.getByLabelText('Output resolution') as HTMLSelectElement;
    expect(Array.from(res.options).map((o) => o.value)).toEqual([
      'x2',
      'x3',
      'x4',
      '256',
      '512',
      '720',
      '1080',
      '1440',
      '2160',
    ]);
    await user.selectOptions(res, 'x3');
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({ output_scale: 3, video_target_resolution: 0 }),
    );
    await user.selectOptions(res, '512');
    await waitFor(() => expect(lastSaved()).toMatchObject({ video_target_resolution: 512 }));
    expect(
      screen.getByText(
        'Chosen by you. Fix, Same size, De-flicker and Frame rate need a frame-by-frame model: pick Quick or Best to go back.',
      ),
    ).toBeTruthy();
  });
});

describe('host job plan client in App', () => {
  it('shows an error with Try again, disables Start, and recovers when Try again succeeds', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.selected_model_id = 'span_photo_x4';
    planFetch.mock.mockRejectedValueOnce(new Error('host unavailable'));

    const user = await mountWith(snapshot);
    const note = await screen.findByText('Could not work out the plan.');
    expect(note.getAttribute('role')).toBe('status');
    expect(note.getAttribute('title')).toBe('host unavailable');

    const startBtn = screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
    expect(startBtn.title).toBe('Could not work out the plan. Try again.');
    expect(api.recordFrontendError).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(startBtn.disabled).toBe(false));
    expect(screen.queryByText('Could not work out the plan.')).toBeNull();
    await openPlanSheet(user);
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText('Upscale ×4'),
    ).toBeTruthy();
  });

  it('waits for a pending plan before calling startJobs once when Start is clicked', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.selected_model_id = 'span_photo_x4';

    const user = await mountWith(snapshot);
    const foot = document.querySelector('.panel-foot') as HTMLElement;
    await waitFor(() => expect(within(foot).getByText('Upscale 4×')).toBeTruthy());

    let resolveNext!: (stages: import('./lib/plan').PlanStage[]) => void;
    const pending = new Promise<import('./lib/plan').PlanStage[]>((resolve) => {
      resolveNext = resolve;
    });
    const baseImpl = planFetch.defaultImpl!;
    planFetch.mock.mockImplementationOnce(() => pending);

    await user.click(screen.getByRole('radio', { name: '2×' }));
    const startBtn = screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(false);

    await user.click(startBtn);
    expect(api.startJobs).not.toHaveBeenCalled();

    const nextStages = await baseImpl(
      snapshot,
      { ...snapshot.settings, output_scale: 2 },
      { face_enabled: false, has_audio: false },
    );
    resolveNext(nextStages);

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs.mock.calls[0][0]).toMatchObject({ output_scale: 2 });
  });
});

describe('Enhance panel head, Recipes menu, and Task row', () => {
  it('shows Recipes in the head instead of Quick · Photo · 4×, and Locked while running when busy', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    expect(screen.queryByText('Quick · Photo · 4×')).toBeNull();
    const trigger = screen.getByRole('button', { name: 'Recipes' });
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(trigger.hasAttribute('aria-haspopup')).toBe(false);

    await user.click(trigger);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    const menu = screen.getByRole('group', { name: 'Recipes' });
    expect(trigger.getAttribute('aria-controls')).toBe(menu.id);
    expect(within(menu).getByText('No recipes yet.')).toBeTruthy();

    cleanup();
    const running = readySnapshot([image('photo', true)]);
    running.runtime.active_job_id = 'job-1';
    await mountWith(running);
    expect(screen.getByText('Locked while running')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Recipes' })).toBeNull();
  });

  it('opens Recipes with a saved recipe, applies and closes, saves on Enter, cancels field on Escape, and confirms delete', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.recipes = [
      {
        id: 'phone',
        name: 'Phone photos',
        task: 'upscale',
        model_id: 'span_photo_x4',
        output_scale: 4,
        tile_size: 256,
        halo: 16,
        precision: 'fp16',
        safe_memory: true,
      },
    ];
    const user = await mountWith(snapshot);
    const trigger = screen.getByRole('button', { name: 'Recipes' });

    // Keyboard open moves focus inside; Escape closes and returns focus to the trigger.
    trigger.focus();
    await user.keyboard('{Enter}');
    const menu = screen.getByRole('group', { name: 'Recipes' });
    const phoneBtn = within(menu).getByRole('button', { name: /^Phone photos/ });
    expect(phoneBtn.textContent).toContain('upscale · 4× · FP16');
    expect(document.activeElement).toBe(phoneBtn);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('group', { name: 'Recipes' })).toBeNull();
    expect(document.activeElement).toBe(trigger);

    // Clicking the recipe applies it and closes the menu.
    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: /^Phone photos/ }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          selected_model_id: 'span_photo_x4',
          precision: 'fp16',
        }),
      ),
    );
    expect(screen.queryByRole('group', { name: 'Recipes' })).toBeNull();

    // Save current as recipe… focuses the Recipe name field; Escape closes the field, Enter saves.
    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: 'Save current as recipe…' }));
    let nameField = screen.getByRole('textbox', { name: 'Recipe name' });
    expect(document.activeElement).toBe(nameField);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('textbox', { name: 'Recipe name' })).toBeNull();
    expect(screen.getByRole('group', { name: 'Recipes' })).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Save current as recipe…' }));
    nameField = screen.getByRole('textbox', { name: 'Recipe name' });
    await user.type(nameField, 'Crisp 4x{Enter}');
    await waitFor(() =>
      expect(api.saveRecipe).toHaveBeenCalledWith(expect.objectContaining({ name: 'Crisp 4x' })),
    );

    // Delete asks first and supports Cancel and Confirm delete.
    await user.click(screen.getByRole('button', { name: 'Delete Phone photos' }));
    expect(screen.getByText('Delete Phone photos?')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Cancel deleting Phone photos' }));
    expect(screen.queryByText('Delete Phone photos?')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Delete Phone photos' }));
    await user.click(screen.getByRole('button', { name: 'Confirm delete Phone photos' }));
    expect(api.deleteRecipe).toHaveBeenCalledWith('phone');
  });

  it('shows the Task row with Edit disabled on video, no caption paragraph, and the video tip', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    const user = await mountWith(snapshot);
    const group = screen.getByRole('group', { name: 'Task' });
    expect(
      (within(group).getByRole('button', { name: 'Edit' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(document.querySelector('.task-caption')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'About Task' }));
    expect(screen.getByRole('tooltip').textContent).toBe(
      'Upscale & restore enlarges videos, fixes them, or both. Edit is for photos.',
    );
  });
});

describe('Enhance panel foot', () => {
  it('shows the plan line in run order with its accessible name for Noise and Blur at 4×', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.output_scale = 4;
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));
    const foot = document.querySelector('.panel-foot') as HTMLElement;
    const planEl = await within(foot).findByText('Blur → Noise → Upscale 4×');
    expect(planEl.closest('.foot-plan')?.getAttribute('aria-label')).toBe(
      'Plan: Blur, Noise, Upscale 4×',
    );
  });

  it('shows Nothing to run and its note at Same size with no fixes, and disables Start', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('radio', { name: 'Same size' }));
    const foot = document.querySelector('.panel-foot') as HTMLElement;
    expect(
      await within(foot).findByText('Nothing to run. Pick what to fix, or choose a larger size.'),
    ).toBeTruthy();
    expect(within(foot).getByText('Nothing to run')).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Restore selected' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('shows terms and download controls in the foot under Upscale · <name> and gates Start', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.catalog = structuredClone(snapshot.catalog);
    snapshot.settings.task = 'upscale';
    snapshot.settings.selected_model_id = 'hat_s_x4';
    const model = snapshot.catalog.models.find((entry) => entry.model_id === 'hat_s_x4')!;
    model.installed = false;
    model.terms_acceptance_required = true;
    const user = await mountWith(snapshot);
    const foot = document.querySelector('.panel-foot') as HTMLElement;
    expect(within(foot).getByText('Upscale · HAT-S ×4')).toBeTruthy();
    const terms = within(foot).getByRole('checkbox', {
      name: 'I reviewed the model license and restrictions.',
    }) as HTMLInputElement;
    expect(terms.checked).toBe(false);
    expect(within(foot).getByRole('button', { name: /^Download model/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /then upscale$/ })).toBeNull();
    expect(
      (screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement).disabled,
    ).toBe(true);

    await user.click(terms);
    expect(terms.checked).toBe(true);
    const downloadAndStart = (await screen.findByRole('button', {
      name: /then upscale$/,
    })) as HTMLButtonElement;
    expect(downloadAndStart.disabled).toBe(false);
    await user.click(within(foot).getByRole('button', { name: /^Download model/ }));
    await waitFor(() => expect(api.downloadModel).toHaveBeenCalledWith('hat_s_x4', true));
  });

  it('shows pending download cost and the download privacy tip in the foot', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.catalog = structuredClone(snapshot.catalog);
    snapshot.settings.task = 'upscale';
    snapshot.catalog.models.forEach((model) => (model.installed = false));
    const user = await mountWith(snapshot);
    const foot = document.querySelector('.panel-foot') as HTMLElement;
    expect(within(foot).getByText(/\d+(\.\d+)? MB to download/)).toBeTruthy();
    await user.click(within(foot).getByRole('button', { name: 'About Downloads' }));
    expect(within(foot).getByRole('tooltip').textContent).toBe(
      'Models download once when you start and stay on this computer. Your photos never leave it.',
    );
  });

  it('shows Working out the plan… on first load and dims the plan line after 400 ms on reload', async () => {
    let resolveFirst!: (stages: import('./lib/plan').PlanStage[]) => void;
    const firstPromise = new Promise<import('./lib/plan').PlanStage[]>((resolve) => {
      resolveFirst = resolve;
    });
    const baseImpl = planFetch.defaultImpl!;
    planFetch.mock.mockImplementationOnce(() => firstPromise);
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.selected_model_id = 'span_photo_x4';

    await mountWith(snapshot);
    const foot = document.querySelector('.panel-foot') as HTMLElement;
    expect(within(foot).getByText('Working out the plan…')).toBeTruthy();

    const firstStages = await baseImpl(snapshot, snapshot.settings, {
      face_enabled: false,
      has_audio: false,
    });
    resolveFirst(firstStages);
    await waitFor(() => expect(within(foot).getByText('Upscale 4×')).toBeTruthy());

    let resolveSecond!: (stages: import('./lib/plan').PlanStage[]) => void;
    const secondPromise = new Promise<import('./lib/plan').PlanStage[]>((resolve) => {
      resolveSecond = resolve;
    });
    planFetch.mock.mockImplementationOnce(() => secondPromise);
    vi.useFakeTimers();
    try {
      await fireEvent.click(screen.getByRole('checkbox', { name: 'Noise' }));
      await tick();
      await Promise.resolve();
      await tick();
      const planEl = within(foot).getByText('Upscale 4×').closest('.foot-plan') as HTMLElement;
      expect(planEl.getAttribute('aria-busy')).toBe('true');
      expect(planEl.classList.contains('dimmed')).toBe(false);

      await vi.advanceTimersByTimeAsync(400);
      await tick();
      expect(planEl.classList.contains('dimmed')).toBe(true);
    } finally {
      vi.useRealTimers();
    }

    const secondStages = await baseImpl(
      snapshot,
      {
        ...snapshot.settings,
        fixes: ['noise'],
        fix_stages: [{ fix: 'noise', model_id: 'nafnet_sidd_width64' }],
      },
      { face_enabled: false, has_audio: false },
    );
    resolveSecond(secondStages);
    await waitFor(() => expect(within(foot).getByText('Noise → Upscale 4×')).toBeTruthy());
    const planEl = within(foot)
      .getByText('Noise → Upscale 4×')
      .closest('.foot-plan') as HTMLElement;
    expect(planEl.hasAttribute('aria-busy')).toBe(false);
    expect(planEl.classList.contains('dimmed')).toBe(false);
  });
});

describe('Plan sheet', () => {
  it('opens from the plan line with Noise and Blur, reorders stages while keeping the Fix list, and closes on Escape', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.output_scale = 4;
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('checkbox', { name: 'Noise' }));
    await user.click(screen.getByRole('checkbox', { name: 'Blur' }));

    const trigger = document.querySelector('.foot-plan') as HTMLButtonElement;
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    await user.click(trigger);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');

    const sheet = screen.getByRole('group', { name: 'Plan' });
    expect(trigger.getAttribute('aria-controls')).toBe(sheet.id);
    expect(within(sheet).getByText('Plan')).toBeTruthy();
    expect(within(sheet).getByText('This run')).toBeTruthy();
    const stages = within(sheet).getByRole('list', { name: 'Stages' });
    expect(within(stages).getAllByRole('listitem')).toHaveLength(3);

    await user.click(within(sheet).getByRole('button', { name: 'Move the blur fix later' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ fixes: ['noise', 'blur'] }),
      ),
    );
    const fixLabels = Array.from(
      screen.getByRole('group', { name: 'Fix' }).querySelectorAll('.fix-name'),
    ).map((el) => el.textContent?.trim());
    expect(fixLabels).toEqual(['Blur', 'JPEG artifacts', 'Noise', 'Colour']);

    // This run controls live inside the sheet.
    for (const label of ['Hardware', 'Tile', 'Halo', 'Precision']) {
      expect(within(sheet).getByLabelText(label)).toBeTruthy();
    }
    expect(within(sheet).getByRole('checkbox', { name: 'Safe memory mode' })).toBeTruthy();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('group', { name: 'Plan' })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('opens while a job is running with selects disabled and Run at low priority active', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.runtime.active_job_id = 'job-1';
    const user = await mountWith(snapshot);

    const sheet = await openPlanSheet(user);
    for (const label of ['Hardware', 'Tile', 'Halo', 'Precision']) {
      expect((within(sheet).getByLabelText(label) as HTMLSelectElement).disabled).toBe(true);
    }
    expect(
      (within(sheet).getByRole('checkbox', { name: 'Safe memory mode' }) as HTMLInputElement)
        .disabled,
    ).toBe(true);
    const lowPriority = within(sheet).getByRole('checkbox', { name: 'Run at low priority' });
    expect(lowPriority.matches(':disabled')).toBe(false);
    await user.click(lowPriority);
    expect(api.setRunLowPriority).toHaveBeenCalledWith(true);
  });
});

describe('Output sheet and line', () => {
  it('shows PNG · Desktop for photo, opens Format/metadata/Save to, and updates to JPEG 98 · Desktop', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.output_format = 'png';
    snapshot.settings.jpeg_quality = 98;
    snapshot.settings.output_directory = '/Users/demo/Desktop';
    const user = await mountWith(snapshot);

    const trigger = document.querySelector('.foot-output') as HTMLButtonElement;
    expect(trigger.textContent).toContain('Output');
    expect(trigger.textContent).toContain('PNG · Desktop');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');

    const sheet = await openOutputSheet(user);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(trigger.getAttribute('aria-controls')).toBe(sheet.id);
    expect(within(sheet).getByLabelText('Format')).toBeTruthy();
    expect(
      within(sheet).getByRole('checkbox', { name: 'Preserve safe metadata and color profile' }),
    ).toBeTruthy();
    expect(within(sheet).getByRole('button', { name: 'Save to /Users/demo/Desktop' })).toBeTruthy();
    expect(within(sheet).queryByLabelText(/quality/)).toBeNull();

    await user.selectOptions(within(sheet).getByLabelText('Format'), 'jpg');
    expect(within(sheet).getByLabelText(/^JPEG quality/)).toBeTruthy();
    expect(trigger.textContent).toContain('JPEG 98 · Desktop');

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('group', { name: 'Output' })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('shows MP4 · AV1 · CRF 18 · Desktop for video and adds sound cleaned when Clean up audio is ticked', async () => {
    const snapshot = readySnapshot([{ ...video('clip', true), has_audio: true }]);
    snapshot.settings.task = 'video';
    snapshot.settings.video_container = 'mp4';
    snapshot.settings.video_codec = 'av1';
    snapshot.settings.video_crf = 18;
    snapshot.settings.output_directory = '/Users/demo/Desktop';
    const user = await mountWith(snapshot);

    const trigger = document.querySelector('.foot-output') as HTMLButtonElement;
    expect(trigger.textContent).toContain('MP4 · AV1 · CRF 18 · Desktop');

    const sheet = await openOutputSheet(user);
    expect(within(sheet).getByLabelText('Container')).toBeTruthy();
    expect(within(sheet).getByLabelText('Video format')).toBeTruthy();
    expect(within(sheet).getByText('External FFmpeg')).toBeTruthy();
    expect(within(sheet).getByLabelText(/Video quality · CRF/)).toBeTruthy();
    const cleanAudio = within(sheet).getByRole('checkbox', { name: 'Clean up audio' });
    await user.click(cleanAudio);
    expect(within(sheet).getByLabelText('Audio model')).toBeTruthy();
    expect(trigger.textContent).toContain('MP4 · AV1 · CRF 18 · sound cleaned · Desktop');
  });

  it('offers Colour output in the Output sheet for HDR video and saves Preserve with FP32 and deflicker off', async () => {
    const media = { ...video('hdr', true), hdr_format: 'HLG' as const };
    const snapshot = readySnapshot([media]);
    snapshot.settings.task = 'video';
    snapshot.settings.selected_model_id = 'hat_s_x4';
    snapshot.settings.video_hdr_mode = 'tone_map';
    snapshot.settings.deflicker = true;
    const user = await mountWith(snapshot);

    const sheet = await openOutputSheet(user);
    const colour = within(sheet).getByLabelText('Colour output') as HTMLSelectElement;
    await user.selectOptions(colour, 'preserve');
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          video_hdr_mode: 'preserve',
          precision: 'fp32',
          deflicker: false,
        }),
      ),
    );
  });
});

describe('Edit panel layout', () => {
  it('shows prompt, model line, and Advanced Size, steps, seed with no plan or Output line', async () => {
    const snapshot = editSnapshot(36);
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Edit$/);

    expect(screen.getByLabelText('What to change')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Change the edit model' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Advanced.*Size, steps, seed/ })).toBeTruthy();
    expect(document.querySelector('.foot-plan')).toBeNull();
    expect(document.querySelector('.foot-output')).toBeNull();
  });
});

describe('Fix list', () => {
  it('lists photo checkboxes in engine order without Faces, and shows row value buttons when ticked', async () => {
    const snapshot = readySnapshot([
      { ...image('photo', true), name: 'lake.jpg', width: 1600, height: 1067 },
    ]);
    const user = await mountWith(snapshot);

    const section = screen.getByRole('region', { name: 'Your photo' });
    expect(within(section).getByText('Your photo')).toBeTruthy();
    expect(within(section).getByText('1600 × 1067 · JPEG')).toBeTruthy();

    const group = screen.getByRole('group', { name: 'Fix' });
    const boxes = within(group).getAllByRole('checkbox') as HTMLInputElement[];
    expect(
      boxes.map((box) => box.closest('.fix-row')?.querySelector('.fix-name')?.textContent),
    ).toEqual(['Blur', 'JPEG artifacts', 'Noise', 'Colour']);
    expect(boxes.every((box) => !box.checked)).toBe(true);
    expect(within(group).queryByRole('checkbox', { name: 'Faces' })).toBeNull();

    await user.click(within(group).getByRole('checkbox', { name: 'Noise' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fix_stages: [expect.objectContaining({ fix: 'noise' })],
        }),
      ),
    );
    const noiseBtn = await within(group).findByRole('button', { name: 'RealPLKSR Denoise' });
    expect(noiseBtn.textContent?.trim()).toBe('RealPLKSR Denoise');

    await user.click(within(group).getByRole('checkbox', { name: 'Blur' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fix_stages: [
            expect.objectContaining({ fix: 'blur' }),
            expect.objectContaining({ fix: 'noise' }),
          ],
        }),
      ),
    );
    expect(
      Array.from(group.querySelectorAll('.fix-name')).map((el) => el.textContent?.trim()),
    ).toEqual(['Blur', 'JPEG artifacts', 'Noise', 'Colour']);
  });

  it('shows download size on a ticked row whose model is missing', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.catalog = structuredClone(snapshot.catalog);
    const denoise = snapshot.catalog.models.find((m) => m.model_id === 'denoise_realplksr_1x')!;
    denoise.installed = false;
    denoise.size_bytes = 28 * 1024 * 1024;
    const user = await mountWith(snapshot);

    const group = screen.getByRole('group', { name: 'Fix' });
    await user.click(within(group).getByRole('checkbox', { name: 'Noise' }));
    expect(
      await within(group).findByRole('button', { name: 'RealPLKSR Denoise · 28 MB' }),
    ).toBeTruthy();
  });

  it('shows Compression, De-flicker with Labs, Video/Animation Content, and disables De-flicker when HDR is preserved', async () => {
    const media = {
      ...video('clip', true),
      width: 1920,
      height: 1080,
      fps: 24,
      duration_seconds: 12,
      hdr_format: 'HLG' as const,
    };
    const snapshot = readySnapshot([media]);
    snapshot.settings.task = 'video';
    snapshot.settings.selected_model_id = 'hat_s_x4';
    const user = await mountWith(snapshot);

    const section = screen.getByRole('region', { name: 'Your video' });
    expect(within(section).getByText('Your video')).toBeTruthy();
    expect(within(section).getByText('1920 × 1080 · 24 fps · 12s · HLG')).toBeTruthy();
    const content = within(section).getByRole('radiogroup', { name: 'Content' });
    expect(within(content).getByRole('radio', { name: 'Video' })).toBeTruthy();
    expect(within(content).getByRole('radio', { name: 'Animation' })).toBeTruthy();

    const group = screen.getByRole('group', { name: 'Fix' });
    expect(within(group).getByRole('checkbox', { name: 'Compression' })).toBeTruthy();
    const deflicker = within(group).getByRole('checkbox', {
      name: 'De-flicker',
    }) as HTMLInputElement;
    expect(deflicker.closest('.fix-row')?.querySelector('.fix-tag')?.textContent).toBe('Labs');
    expect(deflicker.disabled).toBe(false);

    await user.click(deflicker);
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ deflicker: true }),
      ),
    );

    const sheet = await openOutputSheet(user);
    await user.selectOptions(within(sheet).getByLabelText('Colour output'), 'preserve');
    await waitFor(() => expect(deflicker.disabled).toBe(true));
    expect(deflicker.checked).toBe(false);
    expect(deflicker.closest('.fix-row')?.getAttribute('title')).toBe(
      'Not available while HDR is preserved.',
    );
  });

  it('keeps all rows visible and shows the one-fix engine note in the foot', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.engine!.features = snapshot.engine!.features.filter(
      (feature) => feature !== 'image_multi_fix',
    );
    const user = await mountWith(snapshot);

    const group = screen.getByRole('group', { name: 'Fix' });
    await user.click(within(group).getByRole('checkbox', { name: 'Noise' }));
    await user.click(within(group).getByRole('checkbox', { name: 'Blur' }));

    const foot = document.querySelector('.panel-foot') as HTMLElement;
    expect(
      within(foot).getByText('This engine runs one fix at a time. Update LocalSR to run several.'),
    ).toBeTruthy();
  });
});
