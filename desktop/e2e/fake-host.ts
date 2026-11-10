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
  /** Completed 15/60 fps outputs with tiny real playback sources. */
  videoCompare?: boolean;
  /** Populated by installFakeHost only for video Compare. */
  playbackFixtures?: string[];
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
  /** The host takes this long to prepare the preview of a saved result for a pair. */
  pairDelayMs?: number;
  welcome?: boolean;
  /** An update the feed offers; without it the feed is not configured. */
  update?: string;
  /** The outputs also appear as finished jobs, so Reveal and Open are offered for the selected photo. */
  finished?: boolean;
  /** A queue is running: the first photo is processing and the rest wait. */
  running?: boolean;
  /** A video is running while photos and their saved outputs remain browsable. */
  runningVideo?: boolean;
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
        duration_seconds: scenario.videoCompare ? 4 : 12,
        ...(scenario.videoCompare ? { selected: false, width: 32, height: 18, fps: 15 } : {}),
      });
    }
    Object.assign(s.settings, { task: scenario.task ?? '' }, scenario.settings ?? {});
    s.runtime.worker = scenario.worker ?? 'unavailable';
    if (scenario.running) {
      const rows = scenario.rows ?? {};
      const running = Object.entries(rows).find(
        ([, row]) => row.status === 'running' || row.status === 'cancelling',
      );
      s.runtime.active_job_id = `job-photo-${running ? running[0] : 1}`;
      s.runtime.status_title = 'Processing';
      s.runtime.progress = running ? (rows[Number(running[0])].progress ?? 40) : 40;
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
    if (scenario.runningVideo) {
      for (const media of s.media) media.selected = media.id === 'video-1';
      s.runtime.active_job_id = 'job-video-1';
      s.runtime.status_title = 'Enhancing video';
      s.runtime.progress = 40;
      s.jobs.push({
        id: 'job-video-1',
        media_id: 'video-1',
        media_name: 'Alpine lake.mp4',
        media_kind: 'video',
        status: 'running',
        progress: 40,
        output_path: '',
        error: '',
        created_at: 1794160100,
      });
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

  const histories = (): Json[] => [
    ...Object.entries(scenario.outputs ?? {}).map(([n, list]) => ({
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
    })),
    ...(scenario.videoCompare
      ? [
          {
            media_id: 'video-1',
            results: [15, 60].map((fps) => ({
              job_id: `video-${fps}`,
              completed_at: 1,
              status: 'completed',
              kind: 'video',
              output_path: `/fixture/result-${fps}.mp4`,
              output_name: `result-${fps}.mp4`,
              available: true,
              model_id: `video-${fps}`,
              model_label: `${fps} fps result`,
              scale: 1,
              prompt: '',
              max_dimension: 0,
              stages: [],
              notes: '',
              elapsed_seconds: 1,
              output_fps: fps,
              output_duration_seconds: 4,
              ui: null,
            })),
            total_count: 2,
            completed_count: 2,
            available_count: 2,
            default_result_id: 'video-60',
            next_before: null,
          },
        ]
      : []),
  ];

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
        if (scenario.pairDelayMs && args.showing !== 'original')
          await new Promise((resolve) => setTimeout(resolve, scenario.pairDelayMs));
        snapshot.runtime.comparison_pair = {
          media_id: args.mediaId,
          generation: ++compareGeneration,
          showing: pairSlot(args.mediaId, args.showing),
          against: pairSlot(args.mediaId, args.against),
        };
        return null;
      }
      case 'prepare_video_comparison': {
        if (!scenario.videoCompare || args.compareSlot === undefined)
          throw new Error('No video playback fixture for this request');
        const slot = snapshot.runtime.compare.slots[args.compareSlot];
        if (args.compareGeneration !== snapshot.runtime.compare.generation || !slot)
          throw new Error('Stale Compare request');
        const fixture = slot.pick.job_id === 'video-60' ? 1 : 0;
        return {
          original_path: '',
          enhanced_path: '',
          original_url: scenario.playbackFixtures![fixture],
          enhanced_url: '',
          playback_note: '',
        };
      }
      case 'cancel_video_comparison':
        return null;
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
      case 'use_result_as_input': {
        const path = String(args.path);
        let media = snapshot.media.find((item: Json) => item.path === path);
        if (!media) {
          media = photo(snapshot.media.length + 1);
          Object.assign(media, { path, name: path.split('/').pop() });
          snapshot.media.push(media);
        }
        for (const item of snapshot.media) item.selected = item.id === media.id;
        return media.id;
      }
      case 'select_media':
        for (const media of snapshot.media) media.selected = media.id === args.id;
        return null;
      case 'clear_results':
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

// Generated from ffmpeg testsrc2, H.264/yuv420p at 15 and 60 fps, four seconds.
// Embedded data URLs keep the test self-contained; neither fixture exceeds 50 kB.
const videoFixtures = [
  'data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAXybW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAD6AAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAABR10cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAD6AAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAACAAAAASAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAA+gAAAIAAABAAAAAASVbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAA8AAAA8ABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAAEQG1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAABABzdGJsAAAAwHN0c2QAAAAAAAAAAQAAALBhdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAACAAEgBIAAAASAAAAAAAAAABFExhdmM2My4xLjEwMiBsaWJ4MjY0AAAAAAAAAAAAAAAAGP//AAAANmF2Y0MBZAAK/+EAGWdkAAqs2Ul+IwEQAAADABAAAAMB4PEiWWABAAZo6+PLIsD9+PgAAAAAEHBhc3AAAAABAAAAAQAAABRidHJ0AAAAAAAAFLAAAAAAAAAAGHN0dHMAAAAAAAAAAQAAADwAAAQAAAAAFHN0c3MAAAAAAAAAAQAAAAEAAAHYY3R0cwAAAAAAAAA5AAAAAQAACAAAAAABAAAUAAAAAAEAAAgAAAAAAQAAAAAAAAABAAAEAAAAAAEAABQAAAAAAQAACAAAAAABAAAAAAAAAAEAAAQAAAAAAQAAFAAAAAABAAAIAAAAAAEAAAAAAAAAAQAABAAAAAABAAAMAAAAAAEAAAQAAAAAAQAAFAAAAAABAAAIAAAAAAEAAAAAAAAAAQAABAAAAAABAAAQAAAAAAIAAAQAAAAAAQAAFAAAAAABAAAIAAAAAAEAAAAAAAAAAQAABAAAAAABAAAUAAAAAAEAAAgAAAAAAQAAAAAAAAABAAAEAAAAAAEAABAAAAAAAgAABAAAAAABAAAUAAAAAAEAAAgAAAAAAQAAAAAAAAABAAAEAAAAAAEAABQAAAAAAQAACAAAAAABAAAAAAAAAAEAAAQAAAAAAQAAFAAAAAABAAAIAAAAAAEAAAAAAAAAAQAABAAAAAABAAAUAAAAAAEAAAgAAAAAAQAAAAAAAAABAAAEAAAAAAEAABQAAAAAAQAACAAAAAABAAAAAAAAAAEAAAQAAAAAAQAAFAAAAAABAAAIAAAAAAEAAAAAAAAAAQAABAAAAAABAAAQAAAAAAIAAAQAAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAA8AAAAAQAAAQRzdHN6AAAAAAAAAAAAAAA8AAAEgwAAAEwAAAANAAAADAAAAA0AAABzAAAADwAAAA0AAAAMAAAAQQAAAA8AAAANAAAADAAAAE4AAAANAAAAMAAAAA4AAAANAAAADAAAAFMAAAAPAAAADQAAAEIAAAAOAAAADQAAAAwAAAA0AAAADgAAAA0AAAAMAAAAOgAAAA8AAAAMAAAAJgAAAA4AAAAMAAAADQAAAF4AAAAPAAAADQAAAAwAAAA1AAAADwAAAA0AAAAMAAAAHQAAAA4AAAAMAAAADQAAAB8AAAANAAAADAAAAA0AAAARAAAADQAAAAwAAAAMAAAAGwAAAA8AAAAMAAAAFHN0Y28AAAAAAAAAAQAABiIAAABhdWR0YQAAAFltZXRhAAAAAAAAACFoZGxyAAAAAAAAAABtZGlyYXBwbAAAAAAAAAAAAAAAACxpbHN0AAAAJKl0b28AAAAcZGF0YQAAAAEAAAAATGF2ZjYzLjEuMTAyAAAACGZyZWUAAApgbWRhdAAAAq4GBf//qtxF6b3m2Ui3lizYINkj7u94MjY0IC0gY29yZSAxNjUgcjMyMjIgYjM1NjA1YSAtIEguMjY0L01QRUctNCBBVkMgY29kZWMgLSBDb3B5bGVmdCAyMDAzLTIwMjUgLSBodHRwOi8vd3d3LnZpZGVvbGFuLm9yZy94MjY0Lmh0bWwgLSBvcHRpb25zOiBjYWJhYz0xIHJlZj0zIGRlYmxvY2s9MTowOjAgYW5hbHlzZT0weDM6MHgxMTMgbWU9aGV4IHN1Ym1lPTcgcHN5PTEgcHN5X3JkPTEuMDA6MC4wMCBtaXhlZF9yZWY9MSBtZV9yYW5nZT0xNiBjaHJvbWFfbWU9MSB0cmVsbGlzPTEgOHg4ZGN0PTEgY3FtPTAgZGVhZHpvbmU9MjEsMTEgZmFzdF9wc2tpcD0xIGNocm9tYV9xcF9vZmZzZXQ9LTIgdGhyZWFkcz0xIGxvb2thaGVhZF90aHJlYWRzPTEgc2xpY2VkX3RocmVhZHM9MCBucj0wIGRlY2ltYXRlPTEgaW50ZXJsYWNlZD0wIGJsdXJheV9jb21wYXQ9MCBjb25zdHJhaW5lZF9pbnRyYT0wIGJmcmFtZXM9MyBiX3B5cmFtaWQ9MiBiX2FkYXB0PTEgYl9iaWFzPTAgZGlyZWN0PTEgd2VpZ2h0Yj0xIG9wZW5fZ29wPTAgd2VpZ2h0cD0yIGtleWludD0yNTAga2V5aW50X21pbj0xNSBzY2VuZWN1dD00MCBpbnRyYV9yZWZyZXNoPTAgcmNfbG9va2FoZWFkPTQwIHJjPWNyZiBtYnRyZWU9MSBjcmY9MjMuMCBxY29tcD0wLjYwIHFwbWluPTAgcXBtYXg9NjkgcXBzdGVwPTQgaXBfcmF0aW89MS40MCBhcT0xOjEuMDAAgAAAAc1liIQBf9RXbEfXd6NKk0vP473I5OWavVQJvghQBgZ5ITMTTu9pQoJG3XcdRUjrh/m6jr9gXibfVUuHGcH62mhsKuNOM816HB69p1kJsSECg6wYVGsMf8mzh0jvFrbv3D9BE3R0rchryEXO6dJdaOcsxpSeNqiuSBvr9YZtcX1PT9kzO7FKWBJVhyMXRHf2ngQDcdHArPLJCSW5MXfPPTQXcU/m0+1JJmKFb8qXXFFB17khUU3zjVGVAQWvuRLemeO7PcZn4BDaVXpXdie6RRTGQdhsHZzn2NWMUHvhTH/7Bp5ScKoo49Bg1InAVKE+lK2FTAyZOxpLaIZGaJqeqbnJqTj2e3O8ZXy1l+WA92cpcFON2+dyWqojDZn4dE+n+rUILBhQ92yTt8EA59HM/Blc7jAaI4Dzd9MJvgqQdezoYBhh0uV0GSb0VvcN15vEuJNggWlIHEVkrYTEDXW6zrsIz0dgj7o9SytyoshFrqWJWlLf56UzVADAt+MoA17BPSCw8k5MTIbFebhefxBUKoWIWq6MqV9w9FejQjP9IGiJO+nNWlM5mKLI5T0rxqsAeDG/llu5mhdM9jm7aBGMjSzUykraKKwi/CVCZ66IBQAAAEhBmiRsV7XjOXoyfNZ7yqpozMhBWoYP4P5Z9ojuAb2QP/7/AY/OIPsPJQhGJ3C8Y+FU6Hlk1lNwW7HOJs+WDF/JNJ10aKt/T4AAAAAJQZ5CeI7/9WaJAAAACAGeYXRCX7uAAAAACQGeY2pCX/e26QAAAG9BmmhJqEFomUwP//Ztu+N0jAPcipHVg9XrAW4rqdtuSeulX+cN35hahh+a3G49P2URULtbqq4YiEzTkzHqLe6gRl2xcL36YjcnabkKrvwXXWG8iFv6S9641sZM8xVOzxKVxvSupl717WXZAvxVzxsAAAALQZ6GRREsIf/s/jEAAAAJAZ6ldEJf+CM5AAAACAGep2pCX7uAAAAAPUGarEmoQWyZTAr/rhvs5P+By/Fo/5YQDZ4/+11OJGMsAjv7W4Opa+bxQlyUztC4t3S/O3hWGu0ufhsso1gAAAALQZ7KRRUsIf/s/jEAAAAJAZ7pdEJf+CM4AAAACAGe62pCX7uAAAAASkGa7kmoQWyZTBRP/9CnfpgsbwLYqrQjKaTQiFgJgMISWrERG4os3LRcghf8EX0tWDkTSeqbTxAS//l4L0xoAsUanlRvB4TDXLaxAAAACQGfDWpCX/e26QAAACxBmxJJ4QpSZTAr/64c2vDUqhJbnm3/4w9Ta3UPRG8z7TZ4bRtV4ChWJm/jWQAAAApBnzBFNEx37AtIAAAACQGfT3RCX+5XkAAAAAgBn1FqQl+7gQAAAE9Bm1VJqEFomUwP/9CtmZjH68rfpHMH9sfDmTpv0unGQ+3U+/v854Wxt3X6tzb5ULg8x/PKIYO6zxjmoR20tlrtMxQDWn372xYthK9IZ9RAAAAAC0Gfc0URLCX/97boAAAACQGflGpCX+710QAAAD5Bm5lJqEFsmUwP/9LHfo0k2E0HoTTGhAhYj5GqXyXmR//kUW9HFJ/JIVOhkJz3yXxtTXyVLgzj8Qzvpy6ogAAAAApBn7dFFSx37AtJAAAACQGf1nRCX+5XkQAAAAgBn9hqQl+7gAAAADBBm91JqEFsmUwP/9KHpeclHofD5Dx3rpBkU8ru5SjQ9Yxo/09C57DL/S2MsIodW8EAAAAKQZ/7RRUsd+wLSAAAAAkBnhp0Ql/uV5EAAAAIAZ4cakJfu4EAAAA2QZoASahBbJlMD//Sx4DYvZwhT5g2gObHT8HB3+BseWZFTDr+WRU/JzriGe209cZZFMrllZ1gAAAAC0GePkUVLCX/7vXQAAAACAGeX2pCX7uBAAAAIkGaREmoQWyZTA//6fITNkg+FuWIfxIrnU1/PEmyT7wUW8AAAAAKQZ5iRRUsd/TlpQAAAAgBnoF0Ql+7gAAAAAkBnoNqQl/3tukAAABaQZqISahBbJlMCv+td33YAIC52Z73D/fydC0xc0U8dhp8chti24+YPakCNcs64mmj/fYfpcTf2xNm/0qDdt5XIjHhIFqV1rfmosOyELsDEenae0ZCZ1Yguo/BAAAAC0GepkUVLCH/7P4xAAAACQGexXRCX/gjOQAAAAgBnsdqQl+7gAAAADFBmsxJqEFsmUwK/613feOcxR1aSQjPeuQlXP4+1BQrP9zyoc/C9FtelPLoSg3TiqPiAAAAC0Ge6kUVLCH/7P4xAAAACQGfCXRCX/gjOAAAAAgBnwtqQl+7gAAAABlBmxBJqEFsmUwJP3bKdzUEve6AkTqOJxOBAAAACkGfLkUVLHfedEEAAAAIAZ9NdEJfxGkAAAAJAZ9PakJf2rH+AAAAG0GbVEmoQWyZTAm/d4Byf+CP7dgvdVDslPM8TgAAAAlBn3JFFSx3sIEAAAAIAZ+RdEJfu4AAAAAJAZ+TakJf48nAAAAADUGbmEmoQWyZTAivREEAAAAJQZ+2RRUsd7CAAAAACAGf1XRCX7uBAAAACAGf12pCX7uBAAAAF0Gb20moQWyZTAhL/58NjtRQur3rYcN/AAAAC0Gf+UUVLCH/15KBAAAACAGeGmpCX7uA',
  'data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAA5hbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAD6AAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAADYx0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAD6AAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAADAAAAAcAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAA+gAAACAAABAAAAAA0EbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAA8AAAA8ABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAAMr21pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAADG9zdGJsAAAAv3N0c2QAAAAAAAAAAQAAAK9hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAADAAHABIAAAASAAAAAAAAAABFExhdmM2My4xLjEwMiBsaWJ4MjY0AAAAAAAAAAAAAAAAGP//AAAANWF2Y0MBZAAK/+EAGGdkAAqs2U1+8BEAAAMAAQAAAwB4DxIllgEABmjr48siwP34+AAAAAAQcGFzcAAAAAEAAAABAAAAFGJ0cnQAAAAAAAArCgAAAAAAAAAYc3R0cwAAAAAAAAABAAAA8AAAAQAAAAAUc3RzcwAAAAAAAAABAAAAAQAAB3hjdHRzAAAAAAAAAO0AAAABAAACAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAQAAAAAAgAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAEAAAAAAIAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAAAwAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAUAAAAAAQAAAgAAAAABAAAAAAAAAAEAAAEAAAAAAQAABQAAAAABAAACAAAAAAEAAAAAAAAAAQAAAQAAAAABAAAFAAAAAAEAAAIAAAAAAQAAAAAAAAABAAABAAAAAAEAAAQAAAAAAgAAAQAAAAAcc3RzYwAAAAAAAAABAAAAAQAAAPAAAAABAAAD1HN0c3oAAAAAAAAAAAAAAPAAAATDAAAADAAAAAwAAAAMAAAADAAAABEAAAAOAAAADAAAAAwAAABXAAAALwAAAA0AAAANAAAAEQAAAA4AAAAMAAAADAAAABEAAAAOAAAADAAAAAwAAAAxAAAADwAAAAwAAAANAAAAMQAAAA8AAAAMAAAADQAAABEAAAAOAAAADAAAAAwAAAAmAAAADwAAAAwAAAANAAAAEgAAAA4AAAAMAAAADAAAADwAAAAPAAAADAAAAA0AAAAzAAAADwAAAAwAAAANAAAAEQAAAA4AAAAMAAAADAAAABEAAAAOAAAADAAAAAwAAAAvAAAADgAAAAwAAAAMAAAAOAAAAA8AAAANAAAADAAAABEAAAAOAAAADAAAAAwAAAARAAAADgAAAAwAAAAMAAAAWAAAAA8AAAANAAAADQAAABEAAAAOAAAADAAAAAwAAAARAAAADgAAAAwAAAAMAAAARQAAAA8AAAAOAAAADAAAAFEAAAARAAAADAAAAA0AAAARAAAADgAAAAwAAAAMAAAARgAAABAAAAANAAAADQAAABEAAAAOAAAADAAAAAwAAAA4AAAADwAAAA0AAAANAAAAJQAAAA8AAAAOAAAADgAAABEAAAAOAAAADAAAAAwAAAARAAAADgAAAAwAAAAMAAAAOQAAAA8AAAANAAAAIgAAAA8AAAAOAAAADgAAABEAAAAOAAAADAAAAAwAAAARAAAADgAAAAwAAAAMAAAAPgAAABAAAAAOAAAADgAAABEAAAAQAAAADQAAAA0AAAARAAAADgAAAAwAAAAMAAAAJQAAAA8AAAANAAAADQAAABwAAAAQAAAADAAAAA0AAAAWAAAADgAAAAwAAAAMAAAAIAAAAA4AAAANAAAAIgAAAA8AAAAOAAAADAAAABEAAAAOAAAADAAAAAwAAAARAAAADgAAAAwAAAAMAAAAKQAAAA0AAAAWAAAADgAAAA0AAAAMAAAAEwAAAA4AAAAMAAAADAAAABkAAAAOAAAADAAAAA0AAAARAAAADgAAAAwAAAAMAAAAFQAAAA4AAAAMAAAADAAAABEAAAAOAAAADAAAAAwAAAAkAAAADwAAAA0AAAANAAAAFAAAAA4AAAAMAAAADAAAABMAAAAOAAAADQAAAAwAAAArAAAADgAAAA0AAAAMAAAAEQAAAA4AAAAMAAAADAAAABIAAAAPAAAADAAAAA0AAAApAAAADgAAAA0AAAAMAAAAEgAAAA4AAAAMAAAADAAAABIAAAAOAAAADAAAAAwAAAAVAAAADwAAAA0AAAAUc3RjbwAAAAAAAAABAAAOkQAAAGF1ZHRhAAAAWW1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJhcHBsAAAAAAAAAAAAAAAALGlsc3QAAAAkqXRvbwAAABxkYXRhAAAAAQAAAABMYXZmNjMuMS4xMDIAAAAIZnJlZQAAFY1tZGF0AAACrgYF//+q3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE2NSByMzIyMiBiMzU2MDVhIC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAyNSAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbCAtIG9wdGlvbnM6IGNhYmFjPTEgcmVmPTMgZGVibG9jaz0xOjA6MCBhbmFseXNlPTB4MzoweDExMyBtZT1oZXggc3VibWU9NyBwc3k9MSBwc3lfcmQ9MS4wMDowLjAwIG1peGVkX3JlZj0xIG1lX3JhbmdlPTE2IGNocm9tYV9tZT0xIHRyZWxsaXM9MSA4eDhkY3Q9MSBjcW09MCBkZWFkem9uZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hyb21hX3FwX29mZnNldD0tMiB0aHJlYWRzPTEgbG9va2FoZWFkX3RocmVhZHM9MSBzbGljZWRfdGhyZWFkcz0wIG5yPTAgZGVjaW1hdGU9MSBpbnRlcmxhY2VkPTAgYmx1cmF5X2NvbXBhdD0wIGNvbnN0cmFpbmVkX2ludHJhPTAgYmZyYW1lcz0zIGJfcHlyYW1pZD0yIGJfYWRhcHQ9MSBiX2JpYXM9MCBkaXJlY3Q9MSB3ZWlnaHRiPTEgb3Blbl9nb3A9MCB3ZWlnaHRwPTIga2V5aW50PTI1MCBrZXlpbnRfbWluPTI1IHNjZW5lY3V0PTQwIGludHJhX3JlZnJlc2g9MCByY19sb29rYWhlYWQ9NDAgcmM9Y3JmIG1idHJlZT0xIGNyZj0yMy4wIHFjb21wPTAuNjAgcXBtaW49MCBxcG1heD02OSBxcHN0ZXA9NCBpcF9yYXRpbz0xLjQwIGFxPTE6MS4wMACAAAACDWWIhABH2uOYNvXrqtY+g4mWy8UbQiKtgrohm5Ss5oMHtr9fnqOArvIzw/B9zYEnV+TaSe5/e111kl6xBqOYmI7OCirizRh6toIM1WcQ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',
];

/** Install the fake Tauri bridge before the page loads. */
export async function installFakeHost(page: Page, scenario: Scenario): Promise<void> {
  await page.addInitScript(
    hostScript,
    scenario.videoCompare ? { ...scenario, playbackFixtures: videoFixtures } : scenario,
  );
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
