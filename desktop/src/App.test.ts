// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App.svelte';
import { demoSnapshot } from './lib/demo';
import type {
  AppSnapshot,
  LaunchIntent,
  MediaItem,
  UiSettings,
  WorkerEnvelope
} from './lib/types';

const api = vi.hoisted(() => ({
  isTauri: vi.fn(() => true),
  bootstrap: vi.fn(),
  refreshSnapshot: vi.fn(),
  chooseMediaFiles: vi.fn(async (): Promise<string[]> => []),
  chooseMediaFolder: vi.fn(async (): Promise<string[]> => []),
  chooseOutputDirectory: vi.fn(async () => null),
  chooseCustomModel: vi.fn(async () => null),
  addMedia: vi.fn(async (_paths: string[], _replace: boolean): Promise<void> => undefined),
  selectMedia: vi.fn(async (_id: string): Promise<void> => undefined),
  removeMedia: vi.fn(async () => undefined),
  clearMedia: vi.fn(async () => undefined),
  saveSettings: vi.fn(async (_settings: UiSettings): Promise<void> => undefined),
  startJobs: vi.fn(async () => undefined),
  cancelJobs: vi.fn(async () => undefined),
  startBenchmark: vi.fn(async () => undefined),
  exportBenchmark: vi.fn(async () => true),
  prepareVideoComparison: vi.fn(async () => ({
    original_url: 'asset://localhost/original.mp4',
    enhanced_url: 'asset://localhost/enhanced.mp4'
  })),
  clearVideoComparison: vi.fn(async () => undefined),
  refreshCapabilities: vi.fn(async () => undefined),
  probePath: vi.fn(async () => undefined),
  downloadModel: vi.fn(async () => undefined),
  cancelDownload: vi.fn(async () => undefined),
  importCatalogModel: vi.fn(async () => undefined),
  saveRecipe: vi.fn(async () => undefined),
  deleteRecipe: vi.fn(async () => undefined),
  openResult: vi.fn(async () => undefined),
  revealResult: vi.fn(async () => undefined),
  diagnosticSummary: vi.fn(async () => 'LocalSR diagnostics'),
  openOutputDirectory: vi.fn(async () => undefined),
  takeLaunchIntents: vi.fn(async (): Promise<LaunchIntent[]> => []),
  integrationStatus: vi.fn(async () => ({
    platform: 'macos',
    installed: false,
    summary: 'Optional file-manager actions are not installed.',
    command_name: 'localsr-next'
  })),
  installIntegrations: vi.fn(),
  uninstallIntegrations: vi.fn(),
  listenForWorker: vi.fn(
    async (_callback: (message: { type: string; data: Record<string, unknown> }) => void): Promise<() => void> =>
      () => undefined
  ),
  listenForStateChange: vi.fn(async (_callback: () => void) => () => undefined),
  listenForNativeMenu: vi.fn(async () => () => undefined),
  listenForLaunchIntent: vi.fn(async () => () => undefined)
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
      recommended_tile_sizes: [64, 128, 192]
    },
    {
      id: 'cpu',
      type: 'cpu',
      name: 'CPU',
      total_memory: 16 * 1024 ** 3,
      free_memory: 8 * 1024 ** 3,
      supports_fp16: false,
      is_integrated: false,
      recommended_tile_sizes: [64, 128]
    }
  ];
  snapshot.settings.device_id = 'mps';
  snapshot.engine = {
    protocol_version: 1,
    minimum_protocol_version: 1,
    engine_id: 'localsr.test',
    engine_version: '0.0.11-alpha',
    features: ['image', 'video_frame', 'video_seedvr2'],
    model_formats: ['.safetensors'],
    video_engines: ['spandrel_image', 'seedvr2']
  };
  snapshot.runtime = {
    ...snapshot.runtime,
    worker: 'ready',
    status_title: 'Ready',
    status_detail: 'The isolated inference engine is ready.'
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
    selected
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
    duration_seconds: 1
  };
}

async function mountWith(snapshot: AppSnapshot): Promise<ReturnType<typeof userEvent.setup>> {
  api.bootstrap.mockResolvedValue(structuredClone(snapshot));
  api.refreshSnapshot.mockResolvedValue(structuredClone(snapshot));
  render(App);
  await screen.findByText('The isolated inference engine is ready.');
  return userEvent.setup();
}

async function chooseTask(user: ReturnType<typeof userEvent.setup>, name: RegExp): Promise<void> {
  await user.click(screen.getByRole('button', { name }));
}

beforeEach(() => {
  vi.clearAllMocks();
  api.isTauri.mockReturnValue(true);
  api.addMedia.mockResolvedValue(undefined);
  api.selectMedia.mockResolvedValue(undefined);
  api.saveSettings.mockResolvedValue(undefined);
  api.startJobs.mockResolvedValue(undefined);
  api.listenForWorker.mockResolvedValue(() => undefined);
  api.listenForStateChange.mockResolvedValue(() => undefined);
  api.listenForNativeMenu.mockResolvedValue(() => undefined);
  api.listenForLaunchIntent.mockResolvedValue(() => undefined);
  api.takeLaunchIntents.mockResolvedValue([]);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    clearRect: vi.fn(),
    drawImage: vi.fn(),
    fillRect: vi.fn(),
    fillStyle: ''
  } as unknown as CanvasRenderingContext2D);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('LocalSR desktop interface', () => {
  it('does not register native listeners if bootstrap finishes after the window closes', async () => {
    let finishBootstrap!: (snapshot: AppSnapshot) => void;
    api.bootstrap.mockImplementationOnce(() => new Promise(resolve => { finishBootstrap = resolve; }));
    const app = render(App);
    app.unmount();
    finishBootstrap(readySnapshot());
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(api.listenForWorker).not.toHaveBeenCalled();
    expect(api.listenForStateChange).not.toHaveBeenCalled();
    expect(api.takeLaunchIntents).not.toHaveBeenCalled();
  });

  it('releases a native listener that finishes registering after the window closes', async () => {
    let finishRegistration!: (unlisten: () => void) => void;
    const unlisten = vi.fn();
    api.bootstrap.mockResolvedValue(readySnapshot());
    api.listenForWorker.mockImplementationOnce(() => new Promise(resolve => { finishRegistration = resolve; }));
    const app = render(App);
    await waitFor(() => expect(api.listenForWorker).toHaveBeenCalledOnce());
    app.unmount();
    finishRegistration(unlisten);
    await waitFor(() => expect(unlisten).toHaveBeenCalledOnce());
    expect(api.listenForStateChange).not.toHaveBeenCalled();
    expect(api.listenForNativeMenu).not.toHaveBeenCalled();
    expect(api.listenForLaunchIntent).not.toHaveBeenCalled();
  });

  it('mounts the complete three-pane workspace after native bootstrap', async () => {
    await mountWith(readySnapshot());

    expect(screen.getByRole('heading', { name: 'Media' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Enhance' })).toBeTruthy();
    expect(screen.getByText('Choose an image or video to enhance')).toBeTruthy();
    expect(screen.getByRole('button', { name: '＋ Add Media' })).toBeTruthy();
    expect(document.querySelector('.media-illustration svg .play-mark')).toBeTruthy();
  });

  it('makes the real benchmark discoverable and starts it from the toolbar', async () => {
    const user = await mountWith(readySnapshot());

    const shortcut = screen.getByRole('button', { name: 'Run Benchmark' });
    shortcut.focus();
    await user.keyboard('{Enter}');

    expect(await screen.findByRole('heading', { name: 'Performance & Diagnostics' })).toBeTruthy();
    expect(screen.getByText('Apple GPU (Metal)')).toBeTruthy();
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Run Benchmark' })
    );
    await waitFor(() => expect(api.startBenchmark).toHaveBeenCalledWith('mps'));
  });

  it('runs the selected CPU independently of the enhancement GPU', async () => {
    const user = await mountWith(readySnapshot());
    await user.click(screen.getByRole('button', { name: 'Run Benchmark' }));
    const dialog = await screen.findByRole('dialog');
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Benchmark device' }), 'cpu');
    await user.click(within(dialog).getByRole('button', { name: 'Run Benchmark' }));
    await waitFor(() => expect(api.startBenchmark).toHaveBeenCalledWith('cpu'));
  });

  it('shows benchmark progress and keeps local results copyable and exportable', async () => {
    const user = await mountWith(readySnapshot());
    vi.stubGlobal('navigator', {
      ...navigator,
      clipboard: { writeText: vi.fn(async () => undefined) }
    });
    await user.click(screen.getByRole('button', { name: 'Run Benchmark' }));
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;

    callback({
      type: 'benchmark_started',
      data: { job_id: 'benchmark-1', warmup_count: 1, measured_frame_count: 5 }
    });
    expect(
      await within(screen.getByRole('dialog')).findByText(
        'Warming up 1 iteration · then measuring 5 frames'
      )
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Cancel Benchmark' })).toBeTruthy();

    callback({
      type: 'benchmark_progress',
      data: { job_id: 'benchmark-1', completed_frames: 3, total_frames: 5, percentage: 60 }
    });
    expect(
      await within(screen.getByRole('dialog')).findByText('Measured 3 of 5 frames')
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
          score: 868.86
        }
      }
    });
    expect(await screen.findByText('868.86')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Copy JSON' }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining('"workload_version": "localsr-benchmark-v1"')
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
              encode_ms: null
            }
          ]
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
          scenes: []
        }
      ]
    };
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Run Benchmark' }));
    const dialog = await screen.findByRole('dialog');

    expect(dialog.classList.contains('performance-modal')).toBe(true);
    expect(dialog.querySelector('.benchmark-score-value strong')?.textContent).toBe('22.86');
    expect(within(dialog).getByText('● Stable result')).toBeTruthy();
    expect(within(dialog).getByText('1.07×')).toBeTruthy();
    expect(within(dialog).getByText('Apple M1 Pro (16-core GPU)')).toBeTruthy();
    expect(within(dialog).getByRole('article', { name: 'Apple GPU (Metal) benchmark result' })).toBeTruthy();
    expect(within(dialog).getByRole('article', { name: 'CPU benchmark result' })).toBeTruthy();
    expect(within(dialog).getByText('Hardware results')).toBeTruthy();
  });

  it('keeps advanced controls in a dedicated scroll region', async () => {
    const user = await mountWith(readySnapshot([image('first', true)]));
    await user.click(screen.getByRole('button', { name: /Advanced.*Model, output, hardware/i }));

    const inspector = screen.getByRole('region', { name: 'Enhancement settings' });
    expect(inspector.classList.contains('inspector-scroll')).toBe(true);
    expect(screen.getByLabelText('Interface text')).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: /Safe memory mode/i })).toBeTruthy();
  });

  it.each([false, true])('keeps pending and failed videos out of processing (batch=%s)', async (batch) => {
    const media = video('portrait', true);
    media.width = media.height = 0;
    media.preview_data_url = '';
    media.probe_status = 'pending';
    const snapshot = readySnapshot([media]);
    snapshot.settings.batch_mode = batch;
    const user = await mountWith(snapshot);
    await chooseTask(user, /Upscale Video\s*Local video · HLG \/ PQ \/ SDR/i);
    expect(screen.getByRole('heading', { name: 'Preparing preview…' })).toBeTruthy();
    expect(screen.getAllByText('Preparing preview…').length).toBe(2);
    expect(screen.queryByText('Inspecting…')).toBeNull();
    const button = screen.getByRole('button', { name: batch ? 'Start 1 item' : 'Start selected video' });
    expect(button.hasAttribute('disabled')).toBe(true);

    const failed = structuredClone(snapshot);
    failed.media[0].probe_status = 'failed';
    failed.media[0].error = 'Cannot decode this video.';
    api.refreshSnapshot.mockResolvedValue(failed);
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
    callback({ type: 'media_probe_failed', data: { media_path: media.path, error_message: failed.media[0].error } });
    expect(await screen.findByRole('heading', { name: 'Preview unavailable' })).toBeTruthy();
    expect(screen.getByRole('button', { name: batch ? 'Start 1 item' : 'Start selected video' }).hasAttribute('disabled')).toBe(true);
    expect(api.startJobs).not.toHaveBeenCalled();
  });

  it('shows preview encoding failures as non-fatal warnings', async () => {
    await mountWith(readySnapshot([image('first', true)]));
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;

    callback({
      type: 'live_preview_warning',
      data: { job_id: 'job-1', message: 'JPEG encoder unavailable' }
    });

    expect(await screen.findByText(/Preview warning: JPEG encoder unavailable/)).toBeTruthy();
    expect(screen.getByText(/Processing continues normally/)).toBeTruthy();
  });

  it('shows real import phases, survives refresh and labels HDR output before Start', async () => {
    const media = video('portrait', true);
    media.probe_status = 'pending';
    media.preview_data_url = '';
    const snapshot = readySnapshot([media]);
    snapshot.settings.task = 'video';
    snapshot.settings.batch_mode = true;
    const user = await mountWith(snapshot);
    await chooseTask(user, /Upscale Video\s*Local video · HLG \/ PQ \/ SDR/i);
    const callback = api.listenForWorker.mock.calls[0][0] as (message: WorkerEnvelope) => void;
    callback({ type: 'media_probe_progress', data: { media_path: media.path, stage: 'decoding_video' } });
    expect(await screen.findByText('Reading the first video frame')).toBeTruthy();
    expect(document.querySelector('[aria-busy="true"]')).toBeTruthy();
    expect(await screen.findByText('1s elapsed', {}, { timeout: 2500 })).toBeTruthy();
    callback({ type: 'media_probe_progress', data: { media_path: media.path, stage: 'converting_hdr' } });
    expect(await screen.findByText('Converting HDR to an SDR preview')).toBeTruthy();
    const refresh = api.listenForStateChange.mock.calls[0][0] as () => void;
    refresh();
    await waitFor(() => expect(api.refreshSnapshot).toHaveBeenCalled());
    expect(screen.getByText('Converting HDR to an SDR preview')).toBeTruthy();
    const ready = structuredClone(snapshot);
    Object.assign(ready.media[0], { probe_status: 'ready', hdr_format: 'HLG', audio_warning: 'Standard audio will be kept. The extra track will be omitted.', preview_data_url: 'data:image/jpeg;base64,test' });
    api.refreshSnapshot.mockResolvedValue(ready);
    callback({ type: 'media_info', data: { media_path: media.path, width: 2160, height: 3840, hdr_format: 'HLG', audio_warning: 'Standard audio will be kept. The extra track will be omitted.', jpeg_base64: 'test' } });
    expect(await screen.findByRole('region', { name: 'HDR conversion' })).toBeTruthy();
    expect(screen.getByText(/exported as 8-bit SDR/)).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Audio compatibility' })).toBeTruthy();
    expect(screen.queryByText('Converting HDR to an SDR preview')).toBeNull();
    expect(screen.getByRole('button', { name: 'Start 1 item' }).hasAttribute('disabled')).toBe(false);
  });

  it('switches SeedVR2 to SDR, preserves the custom HDR option, and submits the chosen resolution', async () => {
    const media = { ...video('hdr', true), width: 2160, height: 3840, hdr_format: 'HLG' as const };
    const snapshot = readySnapshot([media]);
    Object.assign(snapshot.settings, { task: 'video', selected_model_id: 'hat_s_x4',
      selected_video_model_id: 'frame_by_frame', video_hdr_mode: 'preserve' });
    const user = await mountWith(snapshot);
    const hdr = screen.getByLabelText('Colour output') as HTMLSelectElement;
    expect(hdr.value).toBe('preserve');
    await user.selectOptions(screen.getByLabelText('Video engine'), 'seedvr2_3b');
    expect(hdr.value).toBe('tone_map');
    expect(hdr.querySelector<HTMLOptionElement>('option[value="preserve"]')!.disabled).toBe(true);
    expect(screen.getByText('SDR output selected. This model cannot preserve HDR.')).toBeTruthy();
    expect((screen.getByLabelText('Output resolution') as HTMLSelectElement).selectedIndex).toBe(0);
    await user.selectOptions(screen.getByLabelText('Output resolution'), '512');
    expect(screen.getByText('512 × 910 · SDR')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Start selected video' }));
    expect(api.startJobs).toHaveBeenCalledWith(expect.objectContaining({
      video_model_id: 'seedvr2_3b', video_hdr_mode: 'tone_map', video_target_resolution: 512
    }));
    await user.selectOptions(screen.getByLabelText('Video engine'), 'frame_by_frame');
    await user.selectOptions(screen.getByLabelText('Frame model'), '__custom__');
    expect(hdr.querySelector<HTMLOptionElement>('option[value="preserve"]')!.disabled).toBe(false);
    await user.selectOptions(hdr, 'preserve');
    expect(hdr.value).toBe('preserve');
    expect(screen.getByText(/The current adapter requires HAT/)).toBeTruthy();
  });

  it('normalizes a saved incompatible HDR setting when reopening SeedVR2', async () => {
    const snapshot = readySnapshot([{ ...video('hdr', true), hdr_format: 'HLG' }]);
    Object.assign(snapshot.settings, { task: 'video', selected_video_model_id: 'seedvr2_3b', video_hdr_mode: 'preserve' });
    await mountWith(snapshot);
    expect((screen.getByLabelText('Colour output') as HTMLSelectElement).value).toBe('tone_map');
    await waitFor(() => expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ video_hdr_mode: 'tone_map' })));
  });

  it('saves the current setup as a named recipe beside the built-in recipes', async () => {
    const snapshot = readySnapshot([image('first', true)]);
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: '＋ Save current setup as recipe' }));
    const name = screen.getByRole('textbox', { name: 'Recipe name' });
    expect(document.activeElement).toBe(name);
    await user.type(name, 'Portrait cleanup');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(api.saveRecipe).toHaveBeenCalledTimes(1));
    expect(api.saveRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Portrait cleanup', task: 'upscale' })
    );
  }, 20_000);

  it('exposes image recipes, hardware controls, and honest face availability', async () => {
    const user = await mountWith(readySnapshot());
    await chooseTask(user, /Upscale\s*Photos and artwork/i);

    expect(screen.getByRole('button', { name: /Quick\s*Fast and efficient/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Best\s*Maximum quality/i })).toBeTruthy();
    expect(screen.getByLabelText('Checkpoint')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /Advanced.*Model, output, hardware/i }));
    const hardware = screen.getByLabelText('Hardware');
    expect(within(hardware).getByRole('option', { name: 'Apple GPU (Metal)' })).toBeTruthy();
    expect(within(hardware).getByRole('option', { name: 'CPU' })).toBeTruthy();

    await user.click(screen.getByRole('checkbox', { name: /Face-aware pass/i }));
    expect(await screen.findByRole('heading', { name: 'Face detector unavailable' })).toBeTruthy();
    expect(screen.getByText(/disabled unless a supported local detector/i)).toBeTruthy();
  });

  it.each([
    ['hat_s_x4', 'hat_s_x4_face'],
    ['hat_l_x4_imagenet', 'hat_l_x4_face']
  ])('imports the matching face companion for %s without downloading', async (primaryId, faceId) => {
    const snapshot = readySnapshot([image('portrait', true)]);
    snapshot.engine!.features.push('face_aware');
    const face = snapshot.catalog.models.find((model) => model.model_id === faceId)!;
    face.installed = false;
    api.chooseCustomModel.mockResolvedValueOnce(`/models/${face.filename}` as never);
    const user = await mountWith(snapshot);
    await chooseTask(user, /Upscale\s*Photos and artwork/i);
    await user.selectOptions(screen.getByLabelText('Checkpoint'), primaryId);
    await user.click(screen.getByRole('checkbox', { name: new RegExp(`Face-aware pass with ${face.name}`) }));
    expect(screen.getByRole('button', { name: 'Upscale selected' }).hasAttribute('disabled')).toBe(true);
    await user.click(screen.getByRole('checkbox', { name: /I understand that the checkpoint rights are unresolved/i }));
    await user.click(screen.getByRole('button', { name: 'Choose externally downloaded face checkpoint…' }));
    await waitFor(() => expect(api.importCatalogModel).toHaveBeenCalledWith(faceId, `/models/${face.filename}`, true));
    expect(api.downloadModel).not.toHaveBeenCalled();
  });

  it('labels experimental engines separately from standard video', async () => {
    const user = await mountWith(readySnapshot());
    await chooseTask(user, /Upscale Video\s*Local video · HLG \/ PQ \/ SDR/i);

    const engine = screen.getByLabelText('Video engine');
    expect(within(engine).getByRole('option', { name: /Frame-by-frame/i })).toBeTruthy();
    expect(within(engine).getByRole('option', { name: /SeedVR2-3B — Labs/i })).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /Advanced.*Model, output, hardware/i }));
    expect(screen.getByLabelText('Container')).toBeTruthy();
    expect(screen.getByLabelText(/Video quality · CRF/i)).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: /De-flicker · Labs/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start selected video' })).toBeTruthy();
    await user.selectOptions(engine, 'seedvr2_3b');
    expect(screen.queryByRole('checkbox', { name: /De-flicker · Labs/i })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /Safe memory mode/i })).toBeNull();
  });

  it('queues a video with its own settings while an image job is running', async () => {
    const activeImage = image('active-image');
    const nextVideo = video('next-video', true);
    const snapshot = readySnapshot([activeImage, nextVideo]);
    snapshot.runtime.active_job_id = 'image-job';
    snapshot.runtime.status_title = 'Enhancing';
    snapshot.jobs = [
      {
        id: 'image-job',
        media_id: activeImage.id,
        media_name: activeImage.name,
        media_kind: 'image',
        status: 'running',
        progress: 20,
        output_path: '',
        error: '',
        created_at: 1
      }
    ];

    const user = await mountWith(snapshot);
    const addMedia = screen.getByRole('button', { name: '＋ Add Media' });
    expect((addMedia as HTMLButtonElement).disabled).toBe(false);
    const queue = await screen.findByRole('button', { name: 'Add selected to queue' });
    expect((queue as HTMLButtonElement).disabled).toBe(false);
    await user.click(queue);

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({ media_ids: ['next-video'], task: 'video' })
    );
  });

  it('disables media-incompatible task cards', async () => {
    await mountWith(readySnapshot([image('photo', true)]));
    expect((screen.getByRole('button', { name: /Upscale Video/i }) as HTMLButtonElement).disabled).toBe(true);

    cleanup();
    await mountWith(readySnapshot([video('clip', true)]));
    expect((screen.getByRole('button', { name: /Upscale\s*Photos and artwork/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /Denoise\s*Noise and blur/i }) as HTMLButtonElement).disabled).toBe(true);
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
        recommended_tile_sizes: [64, 128]
      },
      {
        id: 'cuda:1',
        type: 'rocm',
        name: 'AMD GPU (ROCm)',
        total_memory: 12,
        free_memory: 8,
        supports_fp16: true,
        is_integrated: false,
        recommended_tile_sizes: [64, 128]
      },
      {
        id: 'xpu:0',
        type: 'xpu',
        name: 'Intel GPU (XPU)',
        total_memory: 8,
        free_memory: 6,
        supports_fp16: true,
        is_integrated: true,
        recommended_tile_sizes: [64, 128]
      },
      {
        id: 'directml:0',
        type: 'directml',
        name: 'Windows GPU (DirectML)',
        total_memory: 8,
        free_memory: 6,
        supports_fp16: true,
        is_integrated: true,
        recommended_tile_sizes: [64, 128]
      }
    ];
    const user = await mountWith(snapshot);
    await chooseTask(user, /Upscale\s*Photos and artwork/i);
    await user.click(screen.getByRole('button', { name: /Advanced.*Model, output, hardware/i }));
    const hardware = screen.getByLabelText('Hardware');

    for (const name of [
      'Apple GPU (Metal)',
      'CPU',
      'NVIDIA GPU (CUDA)',
      'AMD GPU (ROCm)',
      'Intel GPU (XPU)',
      'Windows GPU (DirectML)'
    ]) {
      expect(within(hardware).getByRole('option', { name })).toBeTruthy();
    }

    await user.selectOptions(hardware, 'directml:0');
    await waitFor(() =>
      expect(api.saveSettings).toHaveBeenLastCalledWith(
        expect.objectContaining({ device_id: 'directml:0' })
      )
    );
  });

  it('submits one image with the selected native backend', async () => {
    const snapshot = readySnapshot([image('first', true)]);
    const user = await mountWith(snapshot);
    await chooseTask(user, /Upscale\s*Photos and artwork/i);

    const start = screen.getByRole('button', { name: 'Upscale selected' }) as HTMLButtonElement;
    expect(start.disabled).toBe(false);
    await user.click(start);

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({
        media_ids: ['first'],
        batch_mode: false,
        task: 'upscale',
        device: 'mps'
      })
    );
  });

  it('submits every compatible item when batch mode is selected', async () => {
    const snapshot = readySnapshot([image('first', true), image('second')]);
    const user = await mountWith(snapshot);
    await user.click(screen.getByRole('button', { name: 'Batch' }));
    await chooseTask(user, /Upscale\s*Photos and artwork/i);

    const start = screen.getByRole('button', { name: 'Start 2 items' });
    await user.click(start);

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({ media_ids: ['first', 'second'], batch_mode: true })
    );
  });

  it('routes a mixed batch by the selected media type', async () => {
    const snapshot = readySnapshot([
      video('selected-video', true),
      image('first-image'),
      image('second-image')
    ]);
    snapshot.settings.batch_mode = true;
    snapshot.settings.task = 'upscale';
    const user = await mountWith(snapshot);

    const start = screen.getByRole('button', { name: 'Start 1 item' }) as HTMLButtonElement;
    expect(start.disabled).toBe(false);
    expect(
      (screen.getByRole('button', { name: /Upscale\s*Photos and artwork/i }) as HTMLButtonElement)
        .disabled
    ).toBe(true);
    expect(
      (screen.getByRole('button', { name: /Upscale Video\s*Local video · HLG \/ PQ \/ SDR/i }) as HTMLButtonElement)
        .disabled
    ).toBe(false);
    await user.click(start);

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({
        media_ids: ['selected-video'],
        batch_mode: true,
        task: 'video'
      })
    );
  });

  it('switches from batch to a non-destructive selected-item single scope', async () => {
    const snapshot = readySnapshot([image('first', true), image('second')]);
    snapshot.settings.batch_mode = true;
    snapshot.settings.task = 'upscale';
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Single' }));

    expect(screen.getAllByText('first.png').length).toBeGreaterThan(0);
    expect(screen.getByText('second.png')).toBeTruthy();
    expect(screen.getByText(/Single processes only “first.png”/i)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Upscale selected' }));

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({ media_ids: ['first'], batch_mode: false })
    );
  });

  it('automatically chooses the compatible task and disables mismatched task cards', async () => {
    const snapshot = readySnapshot([video('selected-video', true), image('available-image')]);
    snapshot.settings.batch_mode = true;
    snapshot.settings.task = 'upscale';
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: 'Single' }));

    expect(screen.getByText(/Single processes only “selected-video.mp4”/i)).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: /Upscale\s*Photos and artwork/i }) as HTMLButtonElement)
        .disabled
    ).toBe(true);
    expect(
      (screen.getByRole('button', { name: 'Start selected video' }) as HTMLButtonElement)
        .disabled
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
        { files: ['/private/opened-photo.png'], preset: null, recipe: null, auto_start: false }
      ])
      .mockResolvedValueOnce([]);

    render(App);

    await screen.findAllByText('opened-photo.png');
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /Upscale\s*Photos and artwork/i }).classList
      ).toContain('active')
    );
    expect(
      (screen.getByRole('button', {
        name: /Upscale Video\s*Local video · HLG \/ PQ \/ SDR/i
      }) as HTMLButtonElement).disabled
    ).toBe(true);
    expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ task: 'upscale' }));
  });

  it('locks the single/batch scope for an active queue', async () => {
    const snapshot = readySnapshot([image('first', true)]);
    snapshot.settings.task = 'upscale';
    snapshot.runtime.active_job_id = 'running-job';
    await mountWith(snapshot);

    expect((screen.getByRole('button', { name: 'Single' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Batch' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('appends a differently configured video behind a running image job', async () => {
    const current = readySnapshot([image('photo', true), video('clip')]);
    current.settings.task = 'upscale';
    current.runtime.active_job_id = 'running-image';
    current.runtime.status_title = 'Enhancing';
    current.jobs = [
      {
        id: 'running-image',
        media_id: 'photo',
        media_name: 'photo.png',
        media_kind: 'image',
        status: 'running',
        progress: 25,
        output_path: '',
        error: '',
        created_at: 1
      }
    ];
    api.bootstrap.mockResolvedValue(structuredClone(current));
    api.refreshSnapshot.mockImplementation(async () => structuredClone(current));
    api.selectMedia.mockImplementation(async (id: string) => {
      current.media.forEach((item) => (item.selected = item.id === id));
    });
    api.saveSettings.mockImplementation(async (settings: UiSettings) => {
      current.settings = structuredClone(settings);
    });

    render(App);
    await screen.findByText(/The isolated inference engine is ready/i);
    const user = userEvent.setup();

    expect(
      (screen.getByRole('button', { name: 'Add selected to queue' }) as HTMLButtonElement).disabled
    ).toBe(true);
    await user.click(screen.getByRole('button', { name: /^▶ clip\.mp4/i }));
    await waitFor(() =>
      expect(
        (screen.getByRole('button', {
          name: /Upscale Video\s*Local video · HLG \/ PQ \/ SDR/i
        }) as HTMLButtonElement).disabled
      ).toBe(false)
    );
    await user.selectOptions(screen.getByLabelText('Video engine'), 'seedvr2_3b');
    const append = screen.getByRole('button', { name: 'Add selected to queue' }) as HTMLButtonElement;
    await waitFor(() => expect(append.disabled).toBe(false));
    await user.click(append);

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1));
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({
        media_ids: ['clip'],
        task: 'video',
        video_model_id: 'seedvr2_3b'
      })
    );
  });

  it('appends from the main Add Media action even in single mode', async () => {
    const snapshot = readySnapshot([image('first', true)]);
    api.chooseMediaFiles.mockResolvedValueOnce(['/private/second.png']);
    const user = await mountWith(snapshot);

    await user.click(screen.getByRole('button', { name: '＋ Add Media' }));

    await waitFor(() => expect(api.addMedia).toHaveBeenCalledWith(['/private/second.png'], false));
  });

  it('collects separate file-manager launches into one explicit batch', async () => {
    const current = readySnapshot();
    current.settings.task = 'upscale';
    current.settings.selected_model_id = current.catalog.models.find(
      (model) => model.native_scale > 1 && !model.purposes.includes('face')
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
        { files: ['/private/second.png'], preset: null, recipe: null, auto_start: true }
      ])
      .mockResolvedValueOnce([]);

    render(App);

    await waitFor(() => expect(api.startJobs).toHaveBeenCalledTimes(1), { timeout: 2_000 });
    expect(api.startJobs).toHaveBeenCalledWith(
      expect.objectContaining({
        media_ids: ['first', 'second'],
        batch_mode: true,
        task: 'upscale'
      })
    );
    expect(api.saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({ batch_mode: true })
    );
  });

  it('keeps separately forwarded mixed media but cancels automatic processing', async () => {
    const current = readySnapshot();
    current.settings.task = 'upscale';
    current.settings.selected_model_id = current.catalog.models.find(
      (model) => model.native_scale > 1 && !model.purposes.includes('face')
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
            : image(id, current.media.length === 0)
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
        { files: ['/private/clip.mp4'], preset: null, recipe: null, auto_start: true }
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
      toJSON: () => ({})
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
        created_at: 1
      }
    ];

    await mountWith(snapshot);

    expect(screen.getByText('Enhanced')).toBeTruthy();
    const divider = screen.getByRole('slider', { name: 'Before and after comparison' });
    await fireEvent.load(screen.getByAltText('Preview of first.png'));
    await waitFor(() =>
      expect(Number.parseFloat(document.querySelector<HTMLElement>('.image-stage')?.style.width ?? '0')).toBeGreaterThan(700)
    );

    await fireEvent.pointerDown(divider, {
      button: 0,
      pointerId: 1,
      clientX: 400,
      clientY: 300
    });
    await fireEvent.pointerMove(divider, { pointerId: 1, clientX: 600, clientY: 300 });
    await fireEvent.pointerUp(divider, { pointerId: 1, clientX: 600, clientY: 300 });
    await waitFor(() => expect(Number(divider.getAttribute('aria-valuenow'))).toBe(78));

    await fireEvent.keyDown(divider, { key: 'ArrowLeft' });
    expect(Number(divider.getAttribute('aria-valuenow'))).toBe(77);
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
      toJSON: () => ({})
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
      | ((message: WorkerEnvelope) => void)
      | undefined;
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
        image_height: 7856
      }
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
        jpeg_base64: 'completed-tile'
      }
    });

    const canvas = screen.getByLabelText('Progressive tiled preview');
    await waitFor(() => expect(canvas.classList.contains('visible')).toBe(true));
    const context = (canvas as HTMLCanvasElement).getContext('2d')!;
    const drawImage = vi.mocked(context.drawImage);
    await waitFor(() => expect(drawImage).toHaveBeenCalledTimes(2));
    expect(drawImage).toHaveBeenLastCalledWith(
      expect.any(DecodedImage),
      0,
      0,
      34,
      34
    );
    expect(screen.queryByText('Enhanced')).toBeNull();
    expect(screen.queryByLabelText('Before and after comparison')).toBeNull();

    for (let completed = 1; completed <= 100; completed += 1) {
      listener?.({
        type: 'progress',
        data: {
          job_id: 'job-progressive',
          completed_tiles: completed,
          total_tiles: 100,
          percentage: completed
        }
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
          jpeg_base64: `completed-tile-${tileIndex}`
        }
      });
    }
    await waitFor(() => expect(drawImage).toHaveBeenCalledTimes(3));
    expect(drawImage.mock.calls.length).toBeLessThan(8);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '1:1' }));
    expect(screen.getByTitle(/Dynamic maximum/).textContent).toMatch(/^40[67]%$/);
    expect(screen.getByLabelText('Media comparison canvas').classList.contains('panning')).toBe(false);
    listener?.({
      type: 'job_completed',
      data: { job_id: 'job-progressive', output_path: '/output/complete.png' }
    });
    await waitFor(() => expect(screen.getByTitle(/Dynamic maximum/).textContent).toBe('100%'));
    expect(stage?.style.left).toBe('50%');
    expect(stage?.style.top).toBe('50%');
  });
});
