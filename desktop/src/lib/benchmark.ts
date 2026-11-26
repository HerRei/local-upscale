import type { BenchmarkDeviceResult } from './types';

export function benchmarkDeviceBarWidth(score: number, devices: BenchmarkDeviceResult[]): number {
  const fastest = Math.max(1, ...devices.map((device) => device.score));
  return Math.max(5, Math.min(100, (score / fastest) * 100));
}

export function benchmarkSpeedBarWidth(
  secondsPerInputMp: number | null | undefined,
  devices: BenchmarkDeviceResult[],
): number {
  if (secondsPerInputMp == null || secondsPerInputMp <= 0) return 0;
  const valid = devices
    .map((device) => device.seconds_per_input_mp)
    .filter((value): value is number => value != null && value > 0);
  if (!valid.length) return 0;
  const fastest = Math.min(...valid);
  return Math.max(5, Math.min(100, (fastest / secondsPerInputMp) * 100));
}

export function benchmarkOneMpExample(secondsPerInputMp: number, scale = 4): string {
  const outputMp = scale * scale;
  const formatted =
    secondsPerInputMp < 1
      ? secondsPerInputMp.toFixed(1)
      : String(Math.max(1, Math.round(secondsPerInputMp)));
  return `a 1 MP photo to ${outputMp} MP takes about ${formatted} s`;
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
