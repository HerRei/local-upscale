import type { Scenario } from './fake-host';

const FEATURES = ['image_multi_fix', 'video_interpolation'];
const SPAN = 'span_photo_x4';

/** One photo, Quick upscale selected, no worker (the app's browser preview). */
export const upscale: Scenario = {
  photos: 1,
  task: 'upscale',
  installed: [SPAN],
  settings: { quality: 'quick', content: 'photo', selected_model_id: SPAN },
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
