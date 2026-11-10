import type { BenchmarkDeviceResult } from './types';

export function benchmarkDeviceBarWidth(score: number, devices: BenchmarkDeviceResult[]): number {
  const fastest = Math.max(1, ...devices.map((device) => device.score));
  return Math.max(5, Math.min(100, (score / fastest) * 100));
}

export function benchmarkSceneLabel(sceneId: string): string {
  switch (sceneId) {
    case 's1-classroom':
      return 'Classroom';
    case 's2-gallery':
      return 'Gallery · tiled';
    case 's3-gallery-encode':
      return 'Gallery · encode';
    default:
      return sceneId;
  }
}
