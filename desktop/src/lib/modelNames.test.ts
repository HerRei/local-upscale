import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import { modelDisplayName, modelDownloadSize } from './modelNames';

describe('modelDisplayName and modelDownloadSize', () => {
  const snapshot = demoSnapshot();

  it('names image, video, interpolation, audio, edit, and unknown models', () => {
    expect(modelDisplayName(snapshot, 'span_photo_x4')).toBe('SPAN ×4 NomosUni');
    expect(modelDisplayName(snapshot, 'seedvr2_3b_fp8')).toBe('SeedVR2-3B FP8');
    expect(modelDisplayName(snapshot, 'rife_4_26')).toBe('RIFE 4.26');
    expect(modelDisplayName(snapshot, 'deepfilternet3')).toBe('DeepFilterNet3');
    expect(modelDisplayName(snapshot, 'flux2_klein_4b_q4_0')).toBe('FLUX.2 klein 4B');
    expect(modelDisplayName(snapshot, 'flux2_klein_4b_q4_0', true)).toBe(
      'FLUX.2 klein 4B · Balanced',
    );
    expect(modelDisplayName(snapshot, 'unknown_model_id')).toBe('a model');
  });

  it('returns the catalog download size across every model group', () => {
    expect(modelDownloadSize(snapshot, 'denoise_realplksr_1x')).toBe(29_559_554);
    expect(modelDownloadSize(snapshot, 'rife_4_26')).toBe(24_636_301);
    expect(modelDownloadSize(snapshot, 'flux2_klein_4b_q4_0')).toBeGreaterThan(0);
    expect(modelDownloadSize(snapshot, 'unknown_model_id')).toBe(0);
  });
});
