import type { PlanStage } from './plan';
import type { FixKind, InterpolationTarget, MediaItem, UiSettings } from './types';

export const PLAN_FIX_NAMES: Record<Exclude<FixKind, 'faces'>, string> = {
  blur: 'Blur',
  jpeg: 'JPEG',
  noise: 'Noise',
  colour: 'Colour',
};

export const INTERPOLATION_BY_TARGET: Record<Exclude<InterpolationTarget, 'off'>, string> = {
  x2: '2× frames',
  x4: '4× frames',
  fps60: '60 fps',
  fps120: '120 fps',
};

const INTERPOLATION_BY_LABEL: Record<string, string> = {
  'Interpolate 2×': '2× frames',
  'Interpolate 4×': '4× frames',
  'Interpolate to 60 fps': '60 fps',
  'Interpolate to 120 fps': '120 fps',
};

function stageStepName(
  stage: PlanStage,
  settings: Pick<
    UiSettings,
    'task' | 'output_scale' | 'video_target_resolution' | 'video_interpolation'
  >,
  isVideo: boolean,
): string {
  if (stage.role === 'fix' || (stage.role === 'primary' && stage.fix)) {
    if (stage.fix === 'jpeg') return isVideo ? 'Compression' : 'JPEG';
    if (stage.fix) return PLAN_FIX_NAMES[stage.fix] ?? stage.label;
    return stage.label;
  }
  if (stage.role === 'primary' && (stage.kind === 'upscale' || stage.kind === 'video')) {
    if (stage.kind === 'video' && settings.video_target_resolution) {
      return `Upscale to ${settings.video_target_resolution} px`;
    }
    return `Upscale ${settings.output_scale}×`;
  }
  if (stage.role === 'primary' && stage.kind === 'restore') {
    return 'Fix';
  }
  if (stage.kind === 'edit') return 'Edit';
  if (stage.kind === 'deflicker') return 'De-flicker';
  if (stage.kind === 'interpolate') {
    const target = settings.video_interpolation;
    if (target && target !== 'off' && INTERPOLATION_BY_TARGET[target]) {
      return INTERPOLATION_BY_TARGET[target];
    }
    return INTERPOLATION_BY_LABEL[stage.label] ?? stage.label;
  }
  if (stage.kind === 'audio_denoise') return 'Clean audio';
  if (stage.kind === 'face_restore') return 'Faces';
  return stage.label;
}

/** Short step names for the foot's plan line, keeping the host's stage order. */
export function planLine(
  stages: readonly PlanStage[],
  settings: Pick<
    UiSettings,
    'task' | 'output_scale' | 'video_target_resolution' | 'video_interpolation'
  >,
  media?: Pick<MediaItem, 'kind'>,
): string[] {
  const isVideo = media?.kind === 'video' || settings.task === 'video';
  return stages.map((stage) => stageStepName(stage, settings, isVideo));
}
