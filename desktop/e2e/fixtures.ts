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
