import type { Scenario } from './fake-host';

const FEATURES = ['image_multi_fix', 'video_interpolation'];
const QUICK = 'localsr_quick_x4_v1';

/** One photo, Quick upscale selected, no worker (the app's browser preview). */
export const upscale: Scenario = {
  photos: 1,
  task: 'upscale',
  installed: [QUICK],
  settings: { quality: 'quick', content: 'photo', selected_model_id: QUICK },
  features: FEATURES,
};

/** The upscale plan with a ready worker, so Try others… is available. */
export const ready: Scenario = { ...upscale, worker: 'ready' };

/** Two photos; the first has three finished outputs, the second one. */
export const withOutputs: Scenario = {
  ...upscale,
  photos: 2,
  outputs: {
    1: [
      { id: 'span', label: 'SPAN ×4', quality: 'Quick', seconds: 9 },
      { id: 'hat', label: 'HAT-S ×4', quality: 'Best', seconds: 64 },
      { id: 'esrgan', label: 'RealESRGAN ×4', quality: 'Quick', seconds: 31 },
    ],
    2: [{ id: 'span2', label: 'SPAN ×4', quality: 'Quick', seconds: 8 }],
  },
};

export const empty: Scenario = { welcome: false, features: FEATURES };
export const firstRun: Scenario = { welcome: true, features: FEATURES };

export const windows: Scenario = { ...upscale, devices: 'windows', platform: 'Win32' };

export const video: Scenario = {
  ...upscale,
  photos: 0,
  video: true,
  task: 'video',
  settings: { video_interpolation: 'x2' },
};

/** Two finished results of photo 1 on a host with pair slots: an upscale and an Edit at 512 px. */
export const withEdit: Scenario = {
  ...ready,
  pairs: true,
  finished: true,
  outputs: {
    1: [
      { id: 'edit', label: 'Qwen Edit', edit: { width: 512, height: 320 } },
      { id: 'span', label: 'SPAN ×4' },
    ],
  },
};

/** One photo with an upscale and two Edit results, the way the Outputs tab lists them. */
export const withEdits: Scenario = {
  ...ready,
  pairs: true,
  finished: true,
  outputs: {
    1: [
      { id: 'edit2', label: 'FLUX.2 klein 4B', seconds: 61, edit: { width: 512, height: 320 } },
      { id: 'edit1', label: 'FLUX.2 klein 4B', seconds: 48, edit: { width: 512, height: 320 } },
      { id: 'span', label: 'SPAN ×4', quality: 'Quick', seconds: 31 },
    ],
  },
};

// A 6400 x 4268 result in 1024 px tiles: seven columns and five rows, 35 tiles.
const tile = (column: number, row: number): [number, number, number, number] => [
  column * 1024,
  row * 1024,
  Math.min(1024, 6400 - column * 1024),
  Math.min(1024, 4268 - row * 1024),
];

/** Photo 1 was stopped after ten of its 35 tiles and kept; an earlier run finished. */
export const withUnfinished: Scenario = {
  ...ready,
  pairs: true,
  finished: true,
  outputs: {
    1: [
      {
        id: 'hat-part',
        label: 'HAT-S ×4',
        quality: 'Best',
        seconds: 48,
        partial: {
          tiles: Array.from({ length: 10 }, (_, index) => tile(index % 7, Math.floor(index / 7))),
          total: 35,
        },
      },
      { id: 'span', label: 'SPAN ×4', quality: 'Quick', seconds: 9 },
    ],
  },
};

const chain = [
  'SCUNet GAN',
  'FBCNN colour',
  'NAFNet GoPro Deblur',
  'DDColor ModelScope',
  'CodeFormer',
  'HAT-L ×4 SRx4 ImageNet-pretrain',
];

/** Four results of photo 1 that each ran every fix and a long chain of models. */
export const withLongDetails: Scenario = {
  ...ready,
  outputs: {
    1: ['one', 'two', 'three', 'four'].map((id, index) => ({
      id,
      label: `${chain[5 - index]} run ${index + 1}`,
      quality: 'Best',
      seconds: 100 + index,
      stages: chain,
      fixes: ['noise', 'jpeg', 'blur', 'colour', 'faces'],
    })),
  },
};

/** A video with two finished results and one stopped after two frames, on a host with pair slots. */
export const videoUnfinished: Scenario = {
  pairs: true,
  photos: 1,
  video: true,
  videoCompare: true,
  videoPartial: true,
  task: 'video',
};

/** Real PyAV test clips (30/24/60 fps, 1s unfinished, audio overhang) with burned-in frame counters. */
export const videoSync: Scenario = {
  pairs: true,
  photos: 0,
  video: true,
  videoSyncCompare: true,
  task: 'video',
};
