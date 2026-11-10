// @vitest-environment jsdom

import { updateDialogOpen } from './lib/updates';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';

import App from './App.svelte';
import { demoSnapshot } from './lib/demo';
import type {
  AppSnapshot,
  DeviceInfo,
  JobRecord,
  LaunchIntent,
  MediaItem,
  MediaResults,
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
  chooseMediaFiles: vi.fn(async (): Promise<string[]> => []),
  chooseMediaFolder: vi.fn(
    async (): Promise<{ paths: string[]; outputDirectory: string } | null> => null,
  ),
  chooseOutputDirectory: vi.fn(async () => null),
  chooseCustomModel: vi.fn(async () => null),
  addMedia: vi.fn(async (_paths: string[], _replace: boolean): Promise<void> => undefined),
  selectMedia: vi.fn(async (_id: string): Promise<void> => undefined),
  removeMedia: vi.fn(async () => undefined),
  clearMedia: vi.fn(async () => undefined),
  saveSettings: vi.fn(async (_settings: UiSettings): Promise<void> => undefined),
  setUpdateSwitch: vi.fn(async (_name: string, _value: boolean): Promise<void> => undefined),
  startJobs: vi.fn(async () => undefined),
  cancelJobs: vi.fn(async () => undefined),
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
  requestCompare: vi.fn(async (): Promise<void> => undefined),
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
  cancelJobsForMedia: vi.fn(async (_mediaId: string): Promise<void> => undefined),
  refreshCapabilities: vi.fn(async () => undefined),
  probePath: vi.fn(async () => undefined),
  detectExternalFFmpeg: vi.fn(async (): Promise<string[]> => []),
  chooseExternalFFmpeg: vi.fn(async (): Promise<string | null> => null),
  openFfmpegDownloadPage: vi.fn(async () => undefined),
  openDownloadPage: vi.fn(async () => undefined),
  ffmpegInstallHint: vi.fn(async () => null),
  openTerminalWithInstallCommand: vi.fn(async () => undefined),
  downloadModel: vi.fn(async (_id: string, _accepted: boolean) => undefined),
  cancelDownload: vi.fn(async () => undefined),
  removeModel: vi.fn(async () => undefined),
  importCatalogModel: vi.fn(async () => undefined),
  startEdit: vi.fn(async () => undefined),
  openEditLink: vi.fn(async () => undefined),
  chooseEditRuntime: vi.fn(async (): Promise<string | null> => null),
  saveRecipe: vi.fn(async (_recipe: Recipe) => undefined),
  deleteRecipe: vi.fn(async () => undefined),
  updateStatus: vi.fn(async () => ({
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
  openModelLicense: vi.fn(async () => undefined),
  openModelSource: vi.fn(async () => undefined),
  openResult: vi.fn(async () => undefined),
  openUserGuide: vi.fn(async () => undefined),
  revealResult: vi.fn(async () => undefined),
  diagnosticSummary: vi.fn(async () => 'LocalSR diagnostics'),
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
    features: ['image', 'image_multi_fix', 'video_frame', 'video_seedvr2', 'video_interpolation'],
    model_formats: ['.safetensors'],
    video_engines: ['spandrel_image', 'seedvr2'],
  };
  snapshot.runtime = {
    ...snapshot.runtime,
    worker: 'ready',
    status_title: 'Ready',
    status_detail: 'The isolated inference engine is ready.',
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
  // While idle the status bar shows what Start will produce, so wait for the title.
  await waitFor(() =>
    expect(document.querySelector('.status-copy strong')?.textContent ?? '').toMatch(/^Ready/),
  );
  return userEvent.setup();
}

let nativeMenu: ((action: string) => void) | undefined;

/** Models and Performance live in the native menu bar; tests send its actions directly. */
async function chooseMenuItem(action: string): Promise<void> {
  await waitFor(() => expect(nativeMenu).toBeTruthy());
  nativeMenu!(action);
}

async function chooseTask(user: ReturnType<typeof userEvent.setup>, name: RegExp): Promise<void> {
  await user.click(screen.getByRole('button', { name }));
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
    await chooseMenuItem('app.updates');
    await user.click(
      await screen.findByLabelText(
        'Send an anonymous update-check count (version, platform, channel)',
      ),
    );
    await user.click(screen.getByLabelText('Check for updates when LocalSR starts'));
    expect(api.setUpdateSwitch.mock.calls).toEqual([
      ['anonymous_update_count', false],
      ['check_updates_at_launch', true], // it started off in this test
    ]);
    expect(api.saveSettings).not.toHaveBeenCalledWith(
      expect.objectContaining({ anonymous_update_count: false }),
    );
    // The menu's own items are the host's: the window does not act on them.
    await chooseMenuItem('app.update-count');
    expect(api.setUpdateSwitch).toHaveBeenCalledTimes(2);
  });
});

describe('Edit task', () => {
  it('sits beside Upscale and Restore, takes a prompt and sends one editing job', async () => {
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
  api.isTauri.mockReturnValue(true);
  api.addMedia.mockResolvedValue(undefined);
  api.selectMedia.mockResolvedValue(undefined);
  api.saveSettings.mockResolvedValue(undefined);
  api.startJobs.mockResolvedValue(undefined);
  api.startBenchmark.mockReset().mockResolvedValue(undefined);
  api.downloadModel.mockReset().mockResolvedValue(undefined);
  api.listenForWorker.mockResolvedValue(() => undefined);
  api.listenForStateChange.mockResolvedValue(() => undefined);
  nativeMenu = undefined;
  api.listenForNativeMenu.mockImplementation(async (handler) => {
    nativeMenu = handler;
    return () => undefined;
  });
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
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    clearRect: vi.fn(),
    drawImage: vi.fn(),
    fillRect: vi.fn(),
    fillStyle: '',
  } as unknown as CanvasRenderingContext2D);
});

afterEach(() => {
  updateDialogOpen.set(false); // the dialog's store outlives each test's window
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
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

  it('makes the real benchmark discoverable and starts it from the View menu', async () => {
    const user = await mountWith(readySnapshot());

    await chooseMenuItem('view.performance');

    expect(await screen.findByRole('heading', { name: 'Performance & Diagnostics' })).toBeTruthy();
    expect(screen.getByText('Apple GPU (Metal)')).toBeTruthy();
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Run Benchmark' }),
    );
    await waitFor(() => expect(api.startBenchmark).toHaveBeenCalledWith('mps'));
  });

  it('runs the selected CPU independently of the enhancement GPU', async () => {
    const user = await mountWith(readySnapshot());
    await chooseMenuItem('view.performance');
    const dialog = await screen.findByRole('dialog');
    await user.selectOptions(
      within(dialog).getByRole('combobox', { name: 'Benchmark device' }),
      'cpu',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Run Benchmark' }));
    await waitFor(() => expect(api.startBenchmark).toHaveBeenCalledWith('cpu'));
  });

  it('downloads the missing benchmark model once before starting the selected device', async () => {
    const snapshot = readySnapshot();
    snapshot.catalog.models.find((model) => model.model_id === 'span_photo_x4')!.installed = false;
    let finishDownload!: () => void;
    api.downloadModel.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishDownload = () => resolve(undefined);
        }),
    );
    const user = await mountWith(snapshot);
    await chooseMenuItem('view.performance');
    const dialog = within(screen.getByRole('dialog'));
    const device = dialog.getByRole('combobox', { name: 'Benchmark device' });
    await user.selectOptions(device, 'cpu');
    await user.click(dialog.getByRole('button', { name: 'Download & run benchmark' }));
    expect(api.downloadModel).toHaveBeenCalledWith('span_photo_x4', false);
    expect(api.startBenchmark).not.toHaveBeenCalled();
    expect((device as HTMLSelectElement).disabled).toBe(true);
    await user.click(dialog.getByRole('button', { name: /Downloading SPAN/ }));
    expect(api.downloadModel).toHaveBeenCalledOnce();
    finishDownload();
    await waitFor(() => expect(api.startBenchmark).toHaveBeenCalledWith('cpu'));
  });

  it('shows a failed or cancelled model download beside the benchmark button and allows retry', async () => {
    const snapshot = readySnapshot();
    snapshot.catalog.models.find((model) => model.model_id === 'span_photo_x4')!.installed = false;
    api.downloadModel.mockRejectedValueOnce(new Error('download cancelled'));
    const user = await mountWith(snapshot);
    await chooseMenuItem('view.performance');
    const dialog = within(screen.getByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Download & run benchmark' }));
    expect((await dialog.findByRole('alert')).textContent).toContain('download cancelled');
    expect(api.startBenchmark).not.toHaveBeenCalled();
    await user.click(dialog.getByRole('button', { name: 'Download & run benchmark' }));
    await waitFor(() => expect(api.startBenchmark).toHaveBeenCalledWith('mps'));
    expect(dialog.queryByRole('alert')).toBeNull();
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
  });

  it('keeps advanced controls in a dedicated scroll region', async () => {
    const user = await mountWith(readySnapshot([image('first', true)]));
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));

    const inspector = screen.getByRole('region', { name: 'Enhancement settings' });
    expect(inspector.classList.contains('inspector-scroll')).toBe(true);
    expect(screen.getByLabelText('Interface text')).toBeTruthy();
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
      const user = await mountWith(snapshot);
      await chooseTask(user, /^Upscale$/);
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
    await chooseTask(user, /^Upscale$/);
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
    expect(await screen.findByRole('region', { name: 'HDR conversion' })).toBeTruthy();
    expect(screen.getByText(/exported as 8-bit SDR/)).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Audio compatibility' })).toBeTruthy();
    expect(screen.queryByText('Converting HDR to an SDR preview')).toBeNull();
    expect(
      screen.getByRole('button', { name: 'Start selected video' }).hasAttribute('disabled'),
    ).toBe(false);
  });

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
    const hdr = screen.getByLabelText('Colour output') as HTMLSelectElement;
    expect(hdr.value).toBe('preserve');
    await user.selectOptions(screen.getByLabelText('Video engine'), 'seedvr2_3b');
    expect(hdr.value).toBe('tone_map');
    expect(hdr.querySelector<HTMLOptionElement>('option[value="preserve"]')!.disabled).toBe(true);
    expect(screen.getByText('SDR output selected. This model cannot preserve HDR.')).toBeTruthy();
    expect((screen.getByLabelText('Output resolution') as HTMLSelectElement).selectedIndex).toBe(0);
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
    await user.selectOptions(screen.getByLabelText('Video engine'), 'frame_by_frame');
    api.chooseCustomModel.mockResolvedValueOnce('/models/mine.safetensors' as never);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    await user.click(screen.getByRole('button', { name: /Your own file/ }));
    await user.click(screen.getByRole('button', { name: 'Choose checkpoint…' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Model library' })).toBeNull());
    expect(hdr.querySelector<HTMLOptionElement>('option[value="preserve"]')!.disabled).toBe(false);
    await user.selectOptions(hdr, 'preserve');
    expect(hdr.value).toBe('preserve');
    expect(screen.getByText(/The current adapter requires HAT/)).toBeTruthy();
  });

  it('normalizes a saved incompatible HDR setting when reopening SeedVR2', async () => {
    const snapshot = readySnapshot([{ ...video('hdr', true), hdr_format: 'HLG' }]);
    Object.assign(snapshot.settings, {
      task: 'video',
      selected_video_model_id: 'seedvr2_3b',
      video_hdr_mode: 'preserve',
    });
    await mountWith(snapshot);
    expect((screen.getByLabelText('Colour output') as HTMLSelectElement).value).toBe('tone_map');
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({ video_hdr_mode: 'tone_map' }),
      ),
    );
  });

  it('saves the current setup as a named recipe beside the built-in recipes', async () => {
    const snapshot = readySnapshot([image('first', true)]);
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Save as recipe…' }));
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
    const recipe = screen.getByRole('button', { name: /^Jens67/ });
    expect(recipe.getAttribute('aria-pressed')).toBe('false');
    await user.click(recipe);
    await waitFor(() => expect(recipe.getAttribute('aria-pressed')).toBe('true'));
    expect(recipe.classList.contains('active')).toBe(true);
    await user.click(screen.getByRole('button', { name: /^Quick/ }));
    await waitFor(() => expect(recipe.getAttribute('aria-pressed')).toBe('false'));
  });

  it('exposes image recipes, hardware controls, and installed face companions', async () => {
    const user = await mountWith(readySnapshot());
    await chooseTask(user, /^Upscale$/);

    expect(screen.getByRole('button', { name: /^Quick\s*Fast, clean result/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Best\s*Finest detail, slower/i })).toBeTruthy();
    expect(screen.getByRole('list', { name: 'Stages' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Change the upscaler' })).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    const hardware = screen.getByLabelText('Hardware');
    expect(within(hardware).getByRole('option', { name: 'Apple GPU (Metal)' })).toBeTruthy();
    expect(within(hardware).getByRole('option', { name: 'CPU' })).toBeTruthy();

    // Faces need a primary model with a companion; the default Best (RealPLKSR)
    // has none, so the chip is disabled until such a model is chosen.
    expect(
      (screen.getByRole('button', { name: 'Faces', pressed: false }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it.each([
    ['hat_s_x4', 'hat_s_x4_face'],
    ['hat_l_x4_imagenet', 'hat_l_x4_face'],
  ])(
    'imports the matching face companion for %s without downloading',
    async (primaryId, faceId) => {
      const snapshot = readySnapshot([image('portrait', true)]);
      snapshot.engine!.features.push('face_aware');
      const face = snapshot.catalog.models.find((model) => model.model_id === faceId)!;
      face.installed = false;
      api.chooseCustomModel.mockResolvedValueOnce(`/models/${face.filename}` as never);
      snapshot.settings.task = 'upscale';
      snapshot.settings.selected_model_id = primaryId;
      snapshot.settings.quality = 'custom';
      const user = await mountWith(snapshot);
      await user.click(screen.getByRole('button', { name: 'Faces', pressed: false }));
      expect(screen.getByRole('button', { name: 'Faces', pressed: true })).toBeTruthy();
      expect(screen.getByText('Needs your file')).toBeTruthy();
      expect(
        screen.getByRole('button', { name: 'Upscale selected' }).hasAttribute('disabled'),
      ).toBe(true);
      await user.click(
        screen.getByRole('checkbox', {
          name: /I understand that the checkpoint rights are unresolved/i,
        }),
      );
      await user.click(
        screen.getByRole('button', { name: 'Choose externally downloaded face checkpoint…' }),
      );
      await waitFor(() =>
        expect(api.importCatalogModel).toHaveBeenCalledWith(
          faceId,
          `/models/${face.filename}`,
          true,
        ),
      );
      expect(api.downloadModel).not.toHaveBeenCalled();
    },
  );

  it('labels experimental engines separately from standard video', async () => {
    const user = await mountWith(readySnapshot([video('clip', true)]));
    await chooseTask(user, /^Upscale$/);

    const engine = screen.getByLabelText('Video engine');
    expect(within(engine).getByRole('option', { name: /Frame-by-frame/i })).toBeTruthy();
    expect(within(engine).getByRole('option', { name: /SeedVR2-3B — Labs/i })).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    expect(screen.getByLabelText('Container')).toBeTruthy();
    expect(screen.getByLabelText(/Video quality · CRF/i)).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: /De-flicker · Labs/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start selected video' })).toBeTruthy();
    await user.selectOptions(engine, 'seedvr2_3b');
    expect(screen.queryByRole('checkbox', { name: /De-flicker · Labs/i })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /Safe memory mode/i })).toBeNull();
  });

  it('disables interpolation when the engine does not advertise it', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    snapshot.engine!.features = ['video_frame'];
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    expect((screen.getByLabelText('Frame rate') as HTMLSelectElement).disabled).toBe(true);
  });

  it('allows interpolation for SDR with a saved HDR preference', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.settings.task = 'video';
    snapshot.settings.video_hdr_mode = 'preserve';
    snapshot.settings.selected_model_id = 'hat_s_x4';
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
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
    const original = screen.getByRole('button', { name: /^Original recipe/ });
    const motion = screen.getByRole('button', { name: /^Motion recipe/ });
    await user.click(motion);
    await waitFor(() => expect(motion.getAttribute('aria-pressed')).toBe('true'));
    expect(original.getAttribute('aria-pressed')).toBe('false');
  });

  it('offers frame interpolation for video, off by default, and sends it with Start', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    snapshot.catalog.interpolation_models.forEach((model) => (model.installed = true));
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Upscale$/);
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    const rate = screen.getByLabelText('Frame rate') as HTMLSelectElement;
    expect(rate.value).toBe('off');
    expect(screen.queryByLabelText('Interpolate')).toBeNull();
    expect(screen.queryByText('Interpolate to 60 fps')).toBeNull();

    await user.selectOptions(rate, 'fps60');
    const order = screen.getByLabelText('Interpolate') as HTMLSelectElement;
    expect(order.value).toBe('after_upscale');
    expect(screen.getByText('Interpolate to 60 fps')).toBeTruthy();
    expect(screen.getByText(/24 → 60 fps/)).toBeTruthy();
    await user.selectOptions(order, 'before_upscale');

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
    await user.selectOptions(screen.getByLabelText('Video engine'), 'seedvr2_3b');
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    expect(screen.queryByLabelText('Frame rate')).toBeNull();
    expect(screen.queryByText('Interpolate 2×')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Start selected video' }));
    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({ video_model_id: 'seedvr2_3b', video_interpolation: 'off' }),
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
      const option = screen.getByRole('option', { name: /SeedVR2-3B — Labs.*Unavailable/ });
      expect((option as HTMLOptionElement).disabled).toBe(true);
      expect(
        screen.getByRole('button', { name: 'Start selected video' }).matches(':disabled'),
      ).toBe(true);
      expect(screen.queryByRole('button', { name: /^Download / })).toBeNull();
      await user.selectOptions(screen.getByLabelText('Video engine'), 'frame_by_frame');
      expect(
        screen.getByRole('button', { name: 'Start selected video' }).matches(':disabled'),
      ).toBe(false);
      expect(api.downloadModel).not.toHaveBeenCalled();
    },
  );

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
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
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
    expect(screen.getByRole('button', { name: 'Add selected to queue' }).matches(':disabled')).toBe(
      true,
    );
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    expect(api.saveSettings).not.toHaveBeenCalled();
    expect(api.startJobs).not.toHaveBeenCalled();
    // One quiet line under the header instead of a block above Task; the full
    // explanation stays with the settings region for screen readers.
    expect(screen.getByText('Locked during processing')).toBeTruthy();
    expect(screen.getByText('Finish or cancel the queue to change settings.')).toBeTruthy();
    expect(document.querySelector('.inspector-scroll .settings-lock')).toBeNull();
    const region = screen.getByRole('region', { name: 'Enhancement settings' });
    const described = document.getElementById(region.getAttribute('aria-describedby') ?? '');
    expect(described?.textContent).toMatch(/^Settings locked\. Your queue uses the settings/);
    expect(described?.closest('[role="status"]')).toBeTruthy();
  });

  it('disables media-incompatible task cards', async () => {
    await mountWith(readySnapshot([image('photo', true)]));
    expect(screen.getByText('Enlarges photos and artwork with new detail.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Restore' }) as HTMLButtonElement).disabled).toBe(
      false,
    );

    cleanup();
    await mountWith(readySnapshot([video('clip', true)]));
    // A selected video turns Upscale into video upscaling; the image-only tasks step aside.
    expect((screen.getByRole('button', { name: 'Upscale' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(screen.getByText('Enlarges video, SDR or HDR.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Restore' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
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
      // The hint moved: Advanced does not repeat it.
      await chooseTask(user, /^Upscale$/);
      await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
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
    await chooseTask(user, /^Upscale$/);
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    const hardware = screen.getByLabelText('Hardware');

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
    expect(screen.getByText(/DirectML has not been tested on real hardware yet/)).toBeTruthy();
    expect(screen.getByText(/checks once that it gives the same result as the CPU/)).toBeTruthy();
    // The saved scale is a number; the menu must still show the matching option.
    expect((screen.getByLabelText('Interface text') as HTMLSelectElement).selectedIndex).toBe(0);
    // ONNX Runtime GPU routes always run FP32: the control says so and cannot change it.
    const precision = screen.getByLabelText('Precision') as HTMLSelectElement;
    expect(precision.disabled).toBe(true);
    expect(precision.value).toBe('fp32');

    await user.selectOptions(hardware, 'openvino:8680677d060000000002000000000000');
    await waitFor(() => expect(screen.queryByText(/DirectML has not been tested/)).toBeNull());
    expect(screen.getByText(/checks once that it gives the same result as the CPU/)).toBeTruthy();
    expect((screen.getByLabelText('Precision') as HTMLSelectElement).disabled).toBe(true);
  });

  it('shows the CPU and starts on it when the saved GPU is no longer listed', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.device_id = 'cuda:7';
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Upscale$/);
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    const hardware = screen.getByLabelText('Hardware') as HTMLSelectElement;
    expect(hardware.selectedOptions[0]?.textContent).toBe('CPU');
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
    await chooseTask(user, /^Upscale$/);
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    const hardware = screen.getByLabelText('Hardware') as HTMLSelectElement;

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
    // The closed menu shows the whole name and route: the menu gets the row's full width,
    // and its tooltip repeats the entry.
    expect(hardware.selectedOptions[0].textContent).toBe('Intel UHD Graphics 620 · DirectML');
    expect(hardware.title).toBe('Intel UHD Graphics 620 · DirectML');
    expect(hardware.closest('.field-row')?.classList.contains('stacked')).toBe(true);
    // The status line names the GPU as the menu and the engine do.
    expect(document.querySelector('.status-detail')?.textContent).toMatch(
      /· Intel UHD Graphics 620 \(DirectML\)$/,
    );

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
    expect(screen.getByText(/compiles a model once for the NPU/)).toBeTruthy();
  });

  it('keeps short Hardware entries beside their label', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await chooseTask(user, /^Upscale$/);
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    const hardware = screen.getByLabelText('Hardware') as HTMLSelectElement;
    expect(hardware.querySelector('optgroup')).toBeNull();
    expect(hardware.closest('.field-row')?.classList.contains('stacked')).toBe(false);
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

  it('shows the last failure above Advanced and offers the CPU when a GPU caused it', async () => {
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
    expect(within(failure).queryByRole('button', { name: 'Use the CPU' })).toBeNull();

    const rejection =
      'RealESRGAN gives different results on Intel UHD Graphics 620 (DirectML) than on the CPU, so LocalSR does not use that GPU for this model. Select the CPU for it.';
    callback({ type: 'job_started', data: { job_id: 'job-2' } });
    await waitFor(() => expect(screen.queryByRole('alert', { name: 'Last error' })).toBeNull());
    callback({ type: 'job_failed', data: { job_id: 'job-2', error_message: rejection } });
    failure = await screen.findByRole('alert', { name: 'Last error' });
    expect(failure.textContent).toContain('Select the CPU for it.');
    await user.click(within(failure).getByRole('button', { name: 'Use the CPU' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ device_id: 'cpu' }),
      ),
    );
    expect(
      within(screen.getByRole('alert', { name: 'Last error' })).queryByRole('button', {
        name: 'Use the CPU',
      }),
    ).toBeNull();
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

  it('keeps a GPU failure and its CPU shortcut while the engine restarts after it', async () => {
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
    expect(within(failure).getByRole('button', { name: 'Use the CPU' })).toBeTruthy();
    // The next job starts without it.
    callback({ type: 'job_started', data: { job_id: 'job-2' } });
    await waitFor(() => expect(screen.queryByRole('alert', { name: 'Last error' })).toBeNull());
  });

  it('keeps keyboard focus on a control after Use the CPU', async () => {
    const user = await mountWith(onTheGpu());
    await failOnTheGpu();
    let failure = screen.getByRole('alert', { name: 'Last error' });
    await user.click(within(failure).getByRole('button', { name: 'Use the CPU' }));
    // Advanced is closed: the error stays and takes the focus.
    await waitFor(() => expect(document.activeElement).toBe(failure));
    cleanup();

    const open = await mountWith(onTheGpu());
    await chooseTask(open, /^Upscale$/);
    await open.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    await failOnTheGpu();
    failure = screen.getByRole('alert', { name: 'Last error' });
    await open.click(within(failure).getByRole('button', { name: 'Use the CPU' }));
    // Advanced is open: the Hardware menu that now says CPU takes it.
    await waitFor(() => expect(document.activeElement?.id).toBe('device'));
    expect((document.activeElement as HTMLSelectElement).value).toBe('cpu');
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
      await chooseTask(user, /^Upscale$/);

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

  it('submits every compatible item when batch mode is selected', async () => {
    const snapshot = readySnapshot([image('first', true), image('second')]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: 'All 2' }));
    await chooseTask(user, /^Upscale$/);

    const start = screen.getByRole('button', { name: 'Start 2 items' });
    await user.click(start);

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({ media_ids: ['first', 'second'], batch_mode: true }),
    );
  });

  it('routes a mixed batch by the selected media type', async () => {
    const snapshot = readySnapshot([
      video('selected-video', true),
      image('first-image'),
      image('second-image'),
    ]);
    snapshot.settings.batch_mode = true;
    snapshot.settings.task = 'upscale';
    const user = await mountWith(snapshot);

    const start = screen.getByRole('button', { name: 'Start 1 item' }) as HTMLButtonElement;
    expect(start.disabled).toBe(false);
    // The images are left out; the line under the tabs says why, and so does All on hover.
    const why =
      '1 video item can be queued with this setup; select the other media type to configure it separately.';
    expect(screen.getByRole('button', { name: 'All 1' }).getAttribute('title')).toBe(why);
    // Nothing trails the line: the reason is its tooltip and the choice's description.
    const scope = document.querySelector('.process-scope')!;
    expect(scope.getAttribute('title')).toBe(why);
    expect(scope.textContent?.replace(/\s+/g, ' ').trim()).toBe(`Process Selected All 1 ${why}`);
    expect(document.querySelector('.process-hint')).toBeNull();
    const describedBy = scope.querySelector('.view-switch')!.getAttribute('aria-describedby')!;
    expect(document.getElementById(describedBy)?.textContent).toBe(why);
    expect(document.getElementById(describedBy)?.classList).toContain('visually-hidden');
    expect((screen.getByRole('button', { name: /^Restore$/ }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByRole('button', { name: /^Upscale$/ }) as HTMLButtonElement).disabled).toBe(
      false,
    );
    await user.click(start);

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({
        media_ids: ['selected-video'],
        batch_mode: true,
        task: 'video',
      }),
    );
  });

  it('switches from batch to a non-destructive selected-item single scope', async () => {
    const snapshot = readySnapshot([image('first', true), image('second')]);
    snapshot.settings.batch_mode = true;
    snapshot.settings.task = 'upscale';
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Selected' }));

    expect(screen.getAllByText('first.png').length).toBeGreaterThan(0);
    expect(screen.getByText('second.png')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Selected' }).classList).toContain('active');
    // Selected says it all: no sentence under it, and All needs no explanation.
    expect(document.querySelector('.process-hint')).toBeNull();
    expect(screen.getByRole('button', { name: 'All 2' }).hasAttribute('title')).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Upscale selected' }));

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({ media_ids: ['first'], batch_mode: false }),
    );
  });

  it('automatically chooses the compatible task and disables mismatched task cards', async () => {
    const snapshot = readySnapshot([video('selected-video', true), image('available-image')]);
    snapshot.settings.batch_mode = true;
    snapshot.settings.task = 'upscale';
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Selected' }));

    expect(screen.getByRole('button', { name: 'All 1' }).getAttribute('title')).toMatch(
      /select the other media type/,
    );
    expect((screen.getByRole('button', { name: /^Restore$/ }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(
      (screen.getByRole('button', { name: 'Start selected video' }) as HTMLButtonElement).disabled,
    ).toBe(false);
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
    expect(screen.getByRole('button', { name: /^Upscale$/ }).classList).toContain('active');
    expect((screen.getByRole('button', { name: /^Restore$/ }) as HTMLButtonElement).disabled).toBe(
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

  it('locks the single/batch scope for an active queue', async () => {
    const snapshot = readySnapshot([image('first', true), image('second')]);
    snapshot.settings.task = 'upscale';
    snapshot.runtime.active_job_id = 'running-job';
    await mountWith(snapshot);

    expect((screen.getByRole('button', { name: 'Selected' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByRole('button', { name: 'All 2' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('keeps controls locked through cancellation and unlocks after the worker stops', async () => {
    const current = readySnapshot([video('clip', true)]);
    current.settings.task = 'video';
    current.runtime.active_job_id = 'running-video';
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.cancelJobs.mockImplementation(async () => {
      current.runtime.status_title = 'Cancelling';
    });
    render(App);
    const user = userEvent.setup();
    const cancel = await screen.findByRole('button', { name: 'Cancel queue' });
    await user.click(cancel);
    expect(screen.getByRole('button', { name: 'Cancelling…' }).matches(':disabled')).toBe(true);
    expect(screen.getByLabelText('Video engine').matches(':disabled')).toBe(true);
    current.runtime.active_job_id = '';
    current.runtime.status_title = 'Cancelled';
    const refresh = api.listenForStateChange.mock.calls[0][0] as () => void;
    refresh();
    await waitFor(() =>
      expect(screen.getByLabelText('Video engine').matches(':disabled')).toBe(false),
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

  it('shows the resolved plan and downloads a missing stage before starting', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.settings.quality = 'best';
    snapshot.settings.selected_model_id = 'realplksr_nomoswebphoto_x4';
    snapshot.settings.fixes = ['jpeg'];
    snapshot.settings.fix_stages = [{ fix: 'jpeg' as const, model_id: 'fbcnn_color' }];
    snapshot.catalog.models.find((model) => model.model_id === 'fbcnn_color')!.installed = false;
    const user = await mountWith(snapshot);

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
          selected_model_id: 'span_photo_x4',
        }),
      ),
    );
    expect(screen.getByText('Quick · Illustration')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await user.click(screen.getByRole('button', { name: 'Save as recipe…' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.saveRecipe).toHaveBeenCalledWith(
        expect.objectContaining({
          quality: 'best',
          content: 'illustration',
          model_id: 'realplksr_nomoswebphoto_x4',
        }),
      ),
    );
  });

  it('maps the JPEG chip to a restoration stage and clears it again', async () => {
    const snapshot = readySnapshot([image('scan', true)]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: 'JPEG artifacts', pressed: false }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fixes: ['jpeg'],
          fix_stages: [{ fix: 'jpeg' as const, model_id: 'fbcnn_color' }],
        }),
      ),
    );
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getAllByRole('listitem'),
    ).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Blur', pressed: false }));
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
    await user.click(screen.getByRole('button', { name: 'Blur', pressed: true }));
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
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fixes: ['noise'],
          fix_stages: [{ fix: 'noise' as const, model_id: 'nafnet_sidd_width64' }],
        }),
      ),
    );
    const stages = screen.getByRole('list', { name: 'Stages' });
    expect(within(stages).getByText('Fix noise')).toBeTruthy();
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
    expect(screen.getByText('Model')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Back to Best' })).toBeNull();
    expect(within(stages).getByText(/Your pick/)).toBeTruthy();

    // The choice survives the chip going off and on, Quick and back, and the content switch.
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: true }));
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
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
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    let library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /HAT-L ×4 ImageNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    await user.click(screen.getByRole('button', { name: 'Change the noise fix model' }));
    library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /SCUNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for Noise' }));
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: true }));
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
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

  it('lets Restore choose its model per problem and retains the last fix as primary', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Restore/);
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(library).getByText('Choosing for Restore · Noise')).toBeTruthy();
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
    await user.click(screen.getByRole('button', { name: 'Blur', pressed: false }));
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
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText(
        'Can add artifacts to sharp pictures and line art. Skipped if the result is unstable.',
      ),
    ).toBeTruthy();
  });

  it('Back to the default in Restore also replaces a model picked by hand', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Restore/);
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    let library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /SCUNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: 'Use for Noise' }));
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
    await user.click(screen.getByRole('button', { name: 'Blur', pressed: false }));
    expect(
      within(screen.getByRole('list', { name: 'Stages' })).getByText(
        'Can add artifacts to sharp pictures and line art. Skipped if the result is unstable.',
      ),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'About Fix first' })).toBeTruthy();
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
    await user.click(screen.getByRole('button', { name: /^Old scans/ }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fixes: ['jpeg'],
          fix_stages: [{ fix: 'jpeg' as const, model_id: 'fbcnn_color' }],
        }),
      ),
    );
    expect(screen.getByRole('button', { name: 'JPEG artifacts', pressed: true })).toBeTruthy();
  });

  it('keeps Faces on when the newly picked upscaler has a face companion', async () => {
    const snapshot = readySnapshot([image('portrait', true)]);
    snapshot.engine!.features.push('face_aware');
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    let library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /HAT-L ×4 ImageNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await user.click(screen.getByRole('button', { name: 'Faces', pressed: false }));
    await user.click(screen.getByRole('button', { name: 'Change the upscaler' }));
    library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /HAT-S ×4/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ selected_model_id: 'hat_s_x4', enable_face_model: true }),
      ),
    );
    expect(screen.getByRole('button', { name: 'Faces', pressed: true })).toBeTruthy();
  });

  it('names in the library which models run on the Neural Engine or an NPU', async () => {
    const mac = readySnapshot([image('photo', true)]);
    mac.capabilities.devices[0].neural_engine = true;
    const user = await mountWith(mac);
    await chooseMenuItem('view.models');
    let library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /^Upscale photos/ }));
    expect(within(library).getByRole('button', { name: /SPAN ×4.*Neural Engine/ })).toBeTruthy();
    expect(
      within(library).getByRole('button', { name: /HAT-L ×4 ImageNet/ }).textContent,
    ).not.toMatch(/Neural Engine/);
    await user.click(within(library).getByRole('button', { name: /SPAN ×4/ }));
    expect(within(library).getByText('Runs on')).toBeTruthy();
    // The file and the author keep their ends when the details column is narrow.
    const file = mac.catalog.models.find((model) => model.model_id === 'span_photo_x4')!;
    expect(library.querySelector(`.kv .file-name[title="${file.filename}"]`)).toBeTruthy();
    expect(library.querySelector(`.kv .file-name[title="${file.author}"]`)).toBeTruthy();
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
    pc.settings.device_id = 'cpu';
    const user2 = await mountWith(pc);
    await chooseMenuItem('view.models');
    library = screen.getByRole('dialog', { name: 'Model library' });
    await user2.click(within(library).getByRole('button', { name: /^Upscale photos/ }));
    expect(within(library).getByRole('button', { name: /SPAN ×4.*NPU/ })).toBeTruthy();
  });

  it('explains the NPU in Advanced and names a missing compiler in the status line', async () => {
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
    await user.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    expect((screen.getByLabelText('Precision') as HTMLSelectElement).value).toBe('fp16');
    expect(screen.getByText(/compiles a model once for the NPU/)).toBeTruthy();
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
    await user2.click(screen.getByRole('button', { name: /Advanced.*Scale, format, hardware/i }));
    expect(document.querySelector('.advanced-controls')?.textContent).not.toMatch(
      /NPU compiler|intel-driver-compiler-npu|current Intel NPU driver/,
    );
  });

  it('picks a model for this job from the library and marks the recipe as custom', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
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
    await waitFor(() => expect(api.downloadModel).toHaveBeenCalledWith('span_photo_x4', false));
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
          selected_model_id: 'realplksr_nomoswebphoto_x4',
        }),
      ),
    );
    expect(screen.getByText('Best · Illustration')).toBeTruthy();
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
  await chooseTask(user, /^Upscale$/);
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
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    // One fix has nowhere to move.
    expect(screen.queryByRole('button', { name: /^Move the/ })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'JPEG artifacts', pressed: false }));
    await user.click(screen.getByRole('button', { name: 'Blur', pressed: false }));
    expect(screen.getByRole('button', { name: 'Noise', pressed: true })).toBeTruthy();
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
    expect(within(rows()[1]).getByText(/Your pick/)).toBeTruthy();
    expect(within(rows()[2]).queryByText(/Your pick/)).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Change the JPEG fix model' }));
    library = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(library).getByText('Choosing for Upscale · JPEG artifacts')).toBeTruthy();
    expect(within(library).getByRole('heading', { name: 'FBCNN Color' })).toBeTruthy();
    expect(within(library).queryByRole('button', { name: /SCUNet/ })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Close model library' }));

    // Turning one fix off keeps the others, their order and their models.
    await user.click(screen.getByRole('button', { name: 'Blur', pressed: true }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        fixes: ['noise', 'jpeg'],
        fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }, jpeg],
      }),
    );
    // A fix moved to the top keeps the keyboard on its way back down.
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

  it('runs the fixes before the last one first in Restore, whose last fix is its model', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Restore/);
    await user.click(screen.getByRole('button', { name: /^Quick/ }));
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    await user.click(screen.getByRole('button', { name: 'JPEG artifacts', pressed: false }));
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
    expect(labels()).toEqual(['Fix JPEG', 'Restore']);
    // A fix before the model is chosen like one before an upscale; the model stays.
    await user.click(screen.getByRole('button', { name: 'Change the JPEG fix model' }));
    const fixLibrary = screen.getByRole('dialog', { name: 'Model library' });
    expect(within(fixLibrary).getByText('Choosing for Restore · JPEG artifacts')).toBeTruthy();
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
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    expect(
      within(screen.getByRole('dialog', { name: 'Model library' })).getByText(
        'Choosing for Restore · Noise',
      ),
    ).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Close model library' }));

    // Now JPEG is last: FBCNN becomes the model, with the overlap it needs.
    await user.click(screen.getByRole('button', { name: 'Move the noise fix earlier' }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'fbcnn_color',
        halo: 32,
        fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }, jpeg],
      }),
    );
    expect(labels()).toEqual(['Fix noise', 'Restore']);

    // The host answers the save with the settings it holds.
    snapshot.settings = structuredClone(lastSaved());
    api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    await user.click(screen.getByRole('button', { name: 'Save as recipe…' }));
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

  it('keeps a model picked by hand when it fixes the first problem chosen in Restore', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await chooseTask(user, /^Restore/);
    await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('button', { name: /SCUNet/ }));
    await user.click(within(library).getByRole('button', { name: 'Use for this job' }));
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        quality: 'custom',
        selected_model_id: 'scunet_color_real_psnr',
        fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }],
      }),
    );
    // A problem it cannot fix brings a model that can, as one fix did before.
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: true }));
    await user.click(screen.getByRole('button', { name: 'Blur', pressed: false }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        selected_model_id: 'nafnet_gopro_deblur',
        fix_stages: [blur],
      }),
    );
  });

  it('lets your own checkpoint be the Restore model without a fix in front of it', async () => {
    const user = await mountWith(readySnapshot([image('photo', true)]));
    await chooseTask(user, /^Restore/);
    await user.click(screen.getByRole('button', { name: 'Blur', pressed: false }));
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    api.chooseCustomModel.mockResolvedValueOnce('/models/mine.safetensors' as never);
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
    expect(screen.getByRole('button', { name: 'Blur', pressed: false })).toBeTruthy();
  });

  it('saves the fixes in order in a recipe and applies them again', async () => {
    const snapshot = readySnapshot([image('photo', true)]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: /^Best/ }));
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    await user.click(screen.getByRole('button', { name: 'Blur', pressed: false }));
    await user.click(screen.getByRole('button', { name: 'Move the noise fix earlier' }));
    api.saveRecipe.mockImplementationOnce(async (recipe: Recipe) => {
      snapshot.recipes = [recipe];
      snapshot.settings = structuredClone(lastSaved());
      api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
    });
    await user.click(screen.getByRole('button', { name: 'Save as recipe…' }));
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

    await user.click(screen.getByRole('button', { name: 'Move the blur fix earlier' }));
    await waitFor(() => expect(saved.getAttribute('aria-pressed')).toBe('false'));
    await user.click(saved);
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        fixes: ['noise', 'blur'],
        fix_stages: [{ fix: 'noise', model_id: 'nafnet_sidd_width64' }, blur],
      }),
    );
    expect(saved.getAttribute('aria-pressed')).toBe('true');
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
      fix_stages: [],
      fixes: [],
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
      fixes: ['noise', 'faces'],
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
    await user.click(screen.getByRole('button', { name: /^Old recipe/ }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: recipe.task,
        selected_model_id: recipe.model_id,
        fix_stages,
        fixes,
      }),
    );
    expect(screen.getByRole('button', { name: /^Old recipe/ }).getAttribute('aria-pressed')).toBe(
      'true',
    );
  });

  it('keeps a frame-by-frame video recipe and shows no fixes for video', async () => {
    const snapshot = readySnapshot([video('clip', true)]);
    Object.assign(snapshot.settings, {
      task: 'video',
      quality: 'best',
      selected_model_id: 'span_photo_x4',
      selected_video_model_id: 'frame_by_frame',
    });
    const user = await mountWith(snapshot);
    // Video runs no fixes, so it offers none; Faces stays.
    expect(screen.queryByRole('button', { name: 'Noise' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Blur' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Faces' })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Save as recipe…' }));
    await user.type(screen.getByRole('textbox', { name: 'Recipe name' }), 'Clip');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.saveRecipe).toHaveBeenCalledTimes(1));
    expect(api.saveRecipe.mock.calls[0][0].stages).toEqual([
      { kind: 'video', model_id: 'span_photo_x4' },
    ]);
  });

  it('drops the fixes when the task becomes video', async () => {
    const snapshot = readySnapshot([image('photo', true), video('clip')]);
    Object.assign(snapshot.settings, {
      task: 'upscale',
      quality: 'custom',
      selected_model_id: 'span_photo_x4',
      fixes: ['blur', 'noise'],
      fix_stages: [blur, { fix: 'noise', model_id: 'scunet_color_real_psnr' }],
    });
    const user = await mountWith(snapshot);
    const switched = structuredClone(snapshot);
    switched.media = [image('photo'), video('clip', true)];
    api.refreshSnapshot.mockResolvedValue(switched);
    await user.click(screen.getByRole('button', { name: /^▶ clip\.mp4/ }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({ task: 'video', fix_stages: [], fixes: [] }),
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
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    expect(screen.queryByText(/runs one fix at a time/)).toBeNull();
    expect(
      (screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement).disabled,
    ).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Blur', pressed: false }));
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
    const blurTerms = within(rows()[0]).getByRole('checkbox') as HTMLInputElement;
    const jpegTerms = within(rows()[1]).getByRole('checkbox') as HTMLInputElement;
    // Start cannot fetch a model whose terms nobody accepted.
    expect(screen.queryByRole('button', { name: /then upscale$/ })).toBeNull();
    await user.click(blurTerms);
    expect(blurTerms.checked).toBe(true);
    expect(jpegTerms.checked).toBe(false);
    expect(screen.queryByRole('button', { name: /then upscale$/ })).toBeNull();
    await user.click(within(rows()[0]).getByRole('button', { name: /^Download model/ }));
    await waitFor(() =>
      expect(api.downloadModel).toHaveBeenCalledWith('nafnet_gopro_deblur', true),
    );

    await user.click(screen.getByRole('button', { name: 'Change the JPEG fix model' }));
    const library = screen.getByRole('dialog', { name: 'Model library' });
    await user.click(within(library).getByRole('checkbox', { name: /reviewed.*license/i }));
    await user.click(within(library).getByRole('button', { name: /^Download/ }));
    await waitFor(() => expect(api.downloadModel).toHaveBeenCalledWith('fbcnn_color', true));
    await user.click(screen.getByRole('button', { name: 'Close model library' }));
    expect((within(rows()[0]).getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
    expect((within(rows()[1]).getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
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

  it('keeps Faces for the way back from Restore', async () => {
    const snapshot = readySnapshot([image('portrait', true)]);
    snapshot.engine!.features.push('face_aware');
    Object.assign(snapshot.settings, {
      task: 'upscale',
      quality: 'best',
      selected_model_id: 'hat_l_x4_imagenet',
      // Best brings HAT-L back, whose face companion Faces needs.
      preset_pins: { 'upscale/photo/best': 'hat_l_x4_imagenet' },
      fixes: ['faces'],
      enable_face_model: true,
    });
    const user = await mountWith(snapshot);
    await chooseTask(user, /^Restore/);
    await user.click(screen.getByRole('button', { name: 'Noise', pressed: false }));
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({ enable_face_model: false, fixes: ['noise', 'faces'] }),
    );
    await chooseTask(user, /^Upscale/);
    await waitFor(() =>
      expect(lastSaved()).toMatchObject({
        task: 'upscale',
        enable_face_model: true,
        fixes: ['noise', 'faces'],
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

  it('ticks grouped originals and outputs into Compare in order and keeps them across tabs', async () => {
    const snapshot = pairHost(readySnapshot([image('photo', true)]));
    snapshot.jobs = [completedJob('new'), completedJob('old')];
    api.listAllMediaResults.mockResolvedValue([historyOf('photo', [row('new'), row('old')])]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('tab', { name: /Outputs/ }));
    const original = await screen.findByRole('checkbox', { name: 'Original, photo.png' });
    const output = screen.getByRole('checkbox', { name: 'old, photo.png' });
    // No picks, no Compare bar; one pick offers only Clear.
    expect(document.querySelector('.compare-bar')).toBeNull();
    await user.click(original);
    expect(screen.queryByRole('button', { name: /^Compare these/ })).toBeNull();
    expect(screen.getByText('1 of 4 chosen')).toBeTruthy();
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
    await user.click(screen.getByRole('button', { name: 'Compare these 2' }));
    expect(screen.queryByRole('button', { name: /^Compare these|^Comparing/ })).toBeNull();
    expect(screen.getByText('2 of 4 chosen')).toBeTruthy();
    expect(screen.getByRole('slider', { name: /Divider between slot 1 and slot 2/ })).toBeTruthy();
    await user.click(screen.getByRole('tab', { name: 'Preview' }));
    // The tab names the mode only: how many are picked shows in the chips and in Outputs.
    await user.click(screen.getByRole('tab', { name: 'Compare' }));
    expect(screen.getByRole('button', { name: 'Swap slots 1 and 2' })).toBeTruthy();
    await fireEvent.keyDown(screen.getByRole('tablist', { name: 'Main pane' }), {
      key: 'ArrowLeft',
    });
    const preview = screen.getByRole('tab', { name: 'Preview' });
    expect(preview.getAttribute('aria-selected')).toBe('true');
    await waitFor(() => expect(document.activeElement).toBe(preview));
  });

  it('shows the centre tabs, the file name with its size and the view options in one row', async () => {
    const snapshot = pairHost(readySnapshot([image('photo', true)]));
    snapshot.jobs = [completedJob('saved')];
    api.listMediaResults.mockResolvedValue(historyOf('photo', [row('saved')]));
    await mountWith(snapshot);
    const header = document.querySelector<HTMLElement>('.preview-header')!;
    const tabs = within(header).getByRole('tablist', { name: 'Main pane' });
    expect(
      within(tabs)
        .getAllByRole('tab')
        .map((tab) => tab.textContent),
    ).toEqual(['Preview', 'Compare']);
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
    expect(api.cancelJobsForMedia).toHaveBeenCalledWith('photo');
    expect(api.cancelJobs).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Cancel queue' })).toBeTruthy();
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
    await chooseTask(user, /^Upscale$/);
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
    await chooseTask(user, /^Upscale$/);
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
      await user.click(screen.getByRole('button', { name: 'Delete Jens67' }));
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

it('mentions video when asking for a task before applying a preset', async () => {
  await mountWith(readySnapshot());
  await chooseMenuItem('preset.quick');
  expect(screen.getByRole('dialog', { name: 'Choose a task' }).textContent).toContain('Video');
});

it('does not ask for a catalog model under Your own file', async () => {
  const user = await mountWith(readySnapshot([image('photo', true)]));
  await chooseTask(user, /^Restore/);
  await user.click(screen.getByRole('button', { name: 'Change the restore model' }));
  await user.click(screen.getByRole('button', { name: /Your own file/ }));
  expect(
    screen.queryByText('Select a model to see what it is for and where it comes from.'),
  ).toBeNull();
});
