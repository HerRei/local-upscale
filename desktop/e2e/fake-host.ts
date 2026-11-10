import type { Page } from '@playwright/test';

/**
 * Declarative description of what the fake host reports. Everything not named here
 * keeps the value of the app's own demo snapshot (src/lib/demo.ts, real model catalog).
 */
export interface Scenario {
  devices?: 'mac' | 'windows' | 'cpu';
  platform?: string;
  task?: string;
  /** Number of photos in the queue; the first one is selected. */
  photos?: number;
  video?: boolean;
  /** Catalog model ids marked as installed. */
  installed?: string[];
  settings?: Record<string, unknown>;
  worker?: 'ready' | 'unavailable';
  /** The host gave up restarting the engine and offers Try again. */
  engineGivenUp?: boolean;
  features?: string[];
  /** Finished outputs per photo number (1-based); each entry is one model result. */
  outputs?: Record<
    number,
    {
      id: string;
      label: string;
      quality?: string;
      seconds?: number;
      /** An Edit result, saved at its own pixel size (the engine rounds it down to 32s). */
      edit?: { width: number; height: number };
    }[]
  >;
  /** The host reports pair slots, as the real one does: results are compared by job id. */
  pairs?: boolean;
  welcome?: boolean;
  /** An update the feed offers; without it the feed is not configured. */
  update?: string;
  /** The outputs also appear as finished jobs, so Reveal and Open are offered for the selected photo. */
  finished?: boolean;
  /** A queue is running: the first photo is processing and the rest wait. */
  running?: boolean;
  /** With `running`: the state of each photo (1-based) instead of "first runs, rest wait". */
  rows?: Record<number, { status: string; progress?: number; elapsed_seconds?: number }>;
  /** With `running`: only the first n photos have jobs; the others are new input. */
  jobPhotos?: number;
  /** With `running`: the photo (1-based) that is selected; the first by default. */
  selected?: number;
  /** The first photo could not be read: its name and the reason the host gives. */
  unreadable?: { name: string; error: string };
}

export interface HostCall {
  command: string;
  args: unknown;
}

/** Runs in the page before the app starts. Self-contained: it is serialised by Playwright. */
function hostScript(scenario: Scenario): void {
  const GIB = 1024 ** 3;
  const win = window as unknown as Record<string, unknown>;
  try {
    if (scenario.welcome) localStorage.removeItem('localsr.welcome.seen');
    else localStorage.setItem('localsr.welcome.seen', '1');
  } catch {
    /* storage unavailable: the welcome note may show */
  }
  if (scenario.platform) {
    Object.defineProperty(navigator, 'platform', { get: () => scenario.platform });
  }

  // A fixed picture per hue; shapes only, so every browser draws the same thing.
  // Another pixel size shows the same picture filling its frame, as an edit of the
  // whole photo does, so it lines up with the original wherever the divider is.
  const picture = (hue: number, width = 1600, height = 1067) =>
    'data:image/svg+xml;utf8,' +
    encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 1600 1067"${width === 1600 ? '' : ' preserveAspectRatio="none"'}>` +
        `<defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(${hue},55%,62%)"/>` +
        `<stop offset="1" stop-color="hsl(${hue + 30},60%,86%)"/></linearGradient></defs>` +
        `<rect width="1600" height="1067" fill="url(#s)"/>` +
        `<path d="M0 760 L330 380 L560 620 L860 250 L1240 700 L1600 520 L1600 1067 L0 1067Z" fill="hsl(${hue + 200},25%,30%)"/>` +
        `<ellipse cx="800" cy="900" rx="900" ry="160" fill="hsl(${hue + 180},45%,38%)"/>` +
        `<circle cx="1220" cy="190" r="70" fill="hsl(45,90%,85%)"/></svg>`,
    );

  type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

  const photo = (n: number): Json => ({
    id: `photo-${n}`,
    path: `/fixture/alpine-lake-${n}.jpg`,
    name: `Alpine lake ${n}.jpg`,
    kind: 'image',
    width: 1600,
    height: 1067,
    frame_count: 1,
    fps: 0,
    duration_seconds: 0,
    preview_data_url: picture(190 + n * 25),
    probe_status: 'ready',
    error: '',
    selected: n === 1,
    ...(n === 1 && scenario.unreadable
      ? { ...scenario.unreadable, preview_data_url: '', probe_status: 'failed' }
      : {}),
  });
  let snapshot: Json | null = null;
  let compareGeneration = 0;
  const calls: { command: string; args: unknown }[] = [];
  // Tests make the host refuse a command (failures), answer one with a fixed value
  // (answers), or deliver an event the way Tauri does (emit).
  const failures: Record<string, string> = {};
  const answers: Record<string, unknown> = {};
  const listeners: Record<string, number[]> = {};
  const emit = (event: string, payload: unknown) => {
    for (const id of listeners[event] ?? []) {
      (callbacks.get(id) as (message: unknown) => void)({ event, id: 0, payload });
    }
  };
  win.__fakeHost = { calls, failures, answers, emit };

  async function build(): Promise<Json> {
    const demo = (await import(
      /* @vite-ignore */ new URL('/src/lib/demo.ts', location.href).href
    )) as {
      demoSnapshot: () => Json;
    };
    const s = demo.demoSnapshot();
    const cpu = { ...s.capabilities.devices[0], name: 'CPU' };
    const gpu = (id: string, type: string, name: string, extra: Json = {}) => ({
      ...cpu,
      id,
      type,
      name,
      is_integrated: true,
      ...extra,
    });
    const kind = scenario.devices ?? 'mac';
    if (kind === 'mac') {
      s.capabilities.devices = [
        gpu('mps', 'mps', 'Apple GPU', {
          neural_engine: true,
          total_memory: 16 * GIB,
          free_memory: 12 * GIB,
          supports_fp16: true,
        }),
        cpu,
      ];
      s.settings.device_id = 'mps';
    } else if (kind === 'windows') {
      s.capabilities.devices = [
        { ...cpu, name: 'Intel Core i7-8550U' },
        gpu('directml:0', 'directml', 'Intel(R) UHD Graphics 620 (DirectML)'),
        gpu('openvino:GPU', 'openvino', 'Intel(R) UHD Graphics 620 (OpenVINO)'),
        gpu('directml:1', 'directml', 'NVIDIA GeForce RTX 3060 (DirectML)', {
          is_integrated: false,
          total_memory: 12 * GIB,
        }),
      ];
      s.settings.device_id = 'directml:0';
    }
    s.capabilities.system_ram_total = 16 * GIB;
    for (const id of scenario.installed ?? []) {
      const model = [...s.catalog.models, ...s.edit.models].find((m: Json) => m.model_id === id);
      if (!model) throw new Error(`Fixture model ${id} is not in the catalog`);
      model.installed = true;
    }
    s.media = Array.from({ length: scenario.photos ?? 0 }, (_, i) => photo(i + 1));
    if (scenario.running && scenario.selected)
      for (const [i, m] of s.media.entries()) m.selected = i + 1 === scenario.selected;
    if (scenario.video) {
      s.media.push({
        ...photo(1),
        id: 'video-1',
        path: '/fixture/alpine-lake.mp4',
        name: 'Alpine lake.mp4',
        kind: 'video',
        width: 1920,
        height: 1080,
        fps: 24,
        frame_count: 288,
        duration_seconds: 12,
      });
    }
    Object.assign(s.settings, { task: scenario.task ?? '' }, scenario.settings ?? {});
    s.runtime.worker = scenario.worker ?? 'unavailable';
    if (scenario.running) {
      const rows = scenario.rows ?? {};
      const running = Object.entries(rows).find(([, row]) => row.status === 'running');
      s.runtime.active_job_id = `job-photo-${running ? running[0] : 1}`;
      s.runtime.status_title = 'Processing';
      s.runtime.progress = 40;
      s.jobs = Array.from({ length: scenario.jobPhotos ?? scenario.photos ?? 1 }, (_, index) => ({
        id: `job-photo-${index + 1}`,
        media_id: `photo-${index + 1}`,
        media_name: `Alpine lake ${index + 1}.jpg`,
        media_kind: 'image',
        status: rows[index + 1]?.status ?? (index === 0 ? 'running' : 'queued'),
        progress: rows[index + 1]?.progress ?? (index === 0 ? 40 : 0),
        elapsed_seconds: rows[index + 1]?.elapsed_seconds ?? 0,
        output_path: '',
        error: '',
        created_at: 1794160000, // one enqueue: a batch's jobs are created together
      }));
    }
    if (scenario.finished) {
      s.jobs = Object.entries(scenario.outputs ?? {}).flatMap(([n, list]) =>
        list.map((entry, index) => ({
          id: entry.id,
          media_id: `photo-${n}`,
          media_name: `Alpine lake ${n}.jpg`,
          media_kind: 'image',
          status: 'completed',
          progress: 100,
          output_path: `/fixture/results/alpine-lake-${n}-${entry.id}.png`,
          error: '',
          created_at: 1794160000 + index,
        })),
      );
    }
    if (scenario.pairs) {
      const empty = { target_id: '', preview_data_url: '' };
      s.runtime.comparison_pair = { media_id: '', generation: 0, showing: empty, against: empty };
    }
    if (scenario.engineGivenUp) {
      Object.assign(s.runtime, {
        worker: 'failed',
        status_title: 'Engine unavailable',
        status_detail: 'The inference engine repeatedly stopped. Try again, or restart LocalSR.',
        engine_retry: true,
      });
    }
    if (scenario.features) {
      s.engine = {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: 'fixture',
        engine_version: '0',
        features: scenario.features,
        model_formats: [],
        video_engines: [],
      };
    }
    return s;
  }

  const histories = (): Json[] =>
    Object.entries(scenario.outputs ?? {}).map(([n, list]) => ({
      media_id: `photo-${n}`,
      results: list.map((entry, index) => ({
        job_id: entry.id,
        completed_at: 1794160000 + index,
        status: 'completed',
        kind: entry.edit ? 'edit' : 'upscale',
        output_path: `/fixture/results/alpine-lake-${n}-${entry.id}.png`,
        output_name: `alpine-lake-${n}-${entry.id}.png`,
        available: true,
        model_id: entry.id,
        model_label: entry.label,
        scale: entry.edit ? 0 : 4,
        prompt: entry.edit ? 'Make it dusk' : '',
        max_dimension: entry.edit ? Math.max(entry.edit.width, entry.edit.height) : 0,
        stages: [],
        notes: '',
        elapsed_seconds: entry.seconds ?? 12,
        ui: entry.edit
          ? null
          : { quality: entry.quality ?? 'Quick', content: 'photo', recipe_name: '' },
      })),
      total_count: list.length,
      completed_count: list.length,
      available_count: list.length,
      default_result_id: list[0]?.id ?? null,
      next_before: null,
    }));

  function compareSlots(picks: Json[]): Json[] {
    return picks.map((pick, index) => ({
      pick,
      status: 'ready',
      preview_data_url: picture(30 + index * 70),
      error: '',
      width: 1600,
      height: 1067,
      preview_width: 1600,
      preview_height: 1067,
    }));
  }

  // One side of the pair the preview compares: the Original keeps the probe's size and
  // thumbnail, a result is decoded to its own pixel size.
  function pairSlot(mediaId: string, target: string): Json {
    if (target === 'original') {
      return { target_id: target, preview_data_url: '', width: 1600, height: 1067 };
    }
    const n = Number(mediaId.replace('photo-', ''));
    const entry = scenario.outputs?.[n]?.find((candidate) => candidate.id === target);
    const size = entry?.edit ?? { width: 1600 * 4, height: 1067 * 4 };
    return {
      target_id: target,
      output_path: `/fixture/results/alpine-lake-${n}-${target}.png`,
      preview_data_url: entry?.edit ? picture(190 + n * 25, size.width, size.height) : picture(30),
      error: '',
      ...size,
      preview_width: Math.min(size.width, 1600),
      preview_height: Math.min(size.height, 1067),
    };
  }

  async function handle(command: string, args: Json): Promise<unknown> {
    snapshot ??= await build();
    switch (command) {
      case 'bootstrap':
      case 'get_snapshot':
        return structuredClone(snapshot);
      case 'save_settings':
        snapshot.settings = structuredClone(args.settings);
        return null;
      case 'list_all_media_results':
        return histories();
      case 'list_media_results':
        return (
          histories().find((entry) => entry.media_id === args.mediaId) ?? {
            media_id: args.mediaId,
            results: [],
            total_count: 0,
            completed_count: 0,
            available_count: 0,
            default_result_id: null,
            next_before: null,
          }
        );
      case 'request_image_comparison': {
        if (!args.showing) return null;
        snapshot.runtime.comparison_pair = {
          media_id: args.mediaId,
          generation: ++compareGeneration,
          showing: pairSlot(args.mediaId, args.showing),
          against: pairSlot(args.mediaId, args.against),
        };
        return null;
      }
      case 'request_compare':
        snapshot.runtime.compare = {
          generation: ++compareGeneration,
          slots: compareSlots(args.picks),
        };
        return null;
      case 'add_media': {
        const known = new Set(snapshot.media.map((media: Json) => media.path));
        let next = snapshot.media.length;
        for (const path of args.paths as string[]) {
          if (known.has(path)) continue;
          const media = photo(++next);
          Object.assign(media, { path, name: path.split('/').pop(), selected: next === 1 });
          snapshot.media.push(media);
        }
        return null;
      }
      case 'remove_media':
        snapshot.media = snapshot.media.filter((media: Json) => media.id !== args.id);
        return null;
      case 'select_media':
        for (const media of snapshot.media) media.selected = media.id === args.id;
        return null;
      case 'clear_media':
        snapshot.media = [];
        return null;
      case 'choose_external_ffmpeg':
      case 'choose_edit_runtime':
        return null;
      case 'take_launch_intents':
        return [];
      case 'update_status':
      case 'check_update':
        return {
          configured: Boolean(scenario.update),
          managed_by_store: false,
          channel: 'beta',
          target: '',
          stage: scenario.update ? 'available' : 'idle',
          version: scenario.update ?? '',
          notes: '',
          size: scenario.update ? 58 * 1024 ** 2 : 0,
          downloaded: 0,
          message: '',
          settings_recovery: false,
        };
      case 'retry_engine':
        Object.assign(snapshot.runtime, {
          worker: 'starting',
          status_title: 'Starting engine',
          status_detail: 'The inference process is loading.',
          engine_retry: false,
        });
        emit('state-changed', null);
        return null;
      case 'diagnostic_summary':
        return 'Fixture host: no inference engine.';
      case 'plugin:event|listen':
        (listeners[args.event] ??= []).push(args.handler);
        return Math.floor(Math.random() * 1e9);
      case 'plugin:path|join':
        return (args.paths as string[]).join('/');
      default:
        return null;
    }
  }

  const callbacks = new Map<number, unknown>();
  let nextCallback = 1;
  win.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: () => {} };
  win.__TAURI_INTERNALS__ = {
    metadata: {
      currentWindow: { label: 'main' },
      currentWebview: { windowLabel: 'main', label: 'main' },
    },
    transformCallback: (callback: unknown) => {
      const id = nextCallback++;
      callbacks.set(id, callback);
      return id;
    },
    unregisterCallback: (id: number) => callbacks.delete(id),
    convertFileSrc: (path: string) => path,
    invoke: async (command: string, args: Json = {}) => {
      calls.push({ command, args: structuredClone(args) });
      if (command in failures) throw failures[command];
      if (command in answers) return answers[command];
      return handle(command, args);
    },
  };
}

/** Install the fake Tauri bridge before the page loads. */
export async function installFakeHost(page: Page, scenario: Scenario): Promise<void> {
  await page.addInitScript(hostScript, scenario);
}

export async function hostCalls(page: Page, command: string): Promise<HostCall[]> {
  const calls = await page.evaluate(
    () => (window as unknown as { __fakeHost: { calls: HostCall[] } }).__fakeHost.calls,
  );
  return calls.filter((call) => call.command === command);
}

type FakeHostControls = {
  failures: Record<string, string>;
  answers: Record<string, unknown>;
  emit: (event: string, payload: unknown) => void;
};
/** From now on the host refuses `command` with this message. */
export async function refuse(page: Page, command: string, message: string): Promise<void> {
  await page.evaluate(
    ([name, text]) =>
      ((window as unknown as { __fakeHost: FakeHostControls }).__fakeHost.failures[name] = text),
    [command, message],
  );
}

/** From now on the host answers `command` with this value, whatever the arguments. */
export async function answer(page: Page, command: string, value: unknown): Promise<void> {
  await page.evaluate(
    ([name, result]) =>
      ((window as unknown as { __fakeHost: FakeHostControls }).__fakeHost.answers[name as string] =
        result),
    [command, value] as const,
  );
}

/** Deliver a Tauri event to the window's listeners. */
export async function emit(page: Page, event: string, payload: unknown): Promise<void> {
  await page.evaluate(
    ([name, data]) =>
      (window as unknown as { __fakeHost: FakeHostControls }).__fakeHost.emit(name as string, data),
    [event, payload] as const,
  );
}

/** Files dropped on the window, as the operating system delivers them. */
export const dropFiles = (page: Page, paths: string[]): Promise<void> =>
  emit(page, 'tauri://drag-drop', { paths, position: { x: 5, y: 5 } });
