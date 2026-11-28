import { strengthLabel, strengthOf } from './fixRows';
import type { PlanStage } from './plan';
import { deinterlaceName, scanChoice } from './stepRows';
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

/** `Slow motion 2×` for added frames played at the clip's own rate; null for every other setting. */
export function slowMotionName(
  settings: Pick<UiSettings, 'video_interpolation' | 'video_slow_motion'>,
): string | null {
  if (!settings.video_slow_motion) return null;
  if (settings.video_interpolation === 'x2') return 'Slow motion 2×';
  if (settings.video_interpolation === 'x4') return 'Slow motion 4×';
  return null;
}

type PlanLineSettings = Pick<
  UiSettings,
  | 'task'
  | 'output_scale'
  | 'video_target_resolution'
  | 'video_interpolation'
  | 'video_slow_motion'
  | 'step_stages'
> &
  Partial<Pick<UiSettings, 'fix_stages'>>;

function stageStepName(
  stage: PlanStage,
  settings: PlanLineSettings,
  isVideo: boolean,
  amounts: boolean,
  fps?: number,
): string {
  if (stage.role === 'fix' || (stage.role === 'primary' && stage.fix)) {
    if (stage.fix) {
      const name =
        stage.fix === 'jpeg'
          ? isVideo
            ? 'Compression'
            : 'JPEG'
          : (PLAN_FIX_NAMES[stage.fix] ?? stage.label);
      const strength = amounts
        ? strengthOf(settings.fix_stages?.find((entry) => entry.fix === stage.fix))
        : 1;
      return strength < 1 ? `${name} (${strengthLabel(strength).toLowerCase()})` : name;
    }
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
    const slow = slowMotionName(settings);
    if (slow) return slow;
    const target = settings.video_interpolation;
    if (target && target !== 'off' && INTERPOLATION_BY_TARGET[target]) {
      return INTERPOLATION_BY_TARGET[target];
    }
    return INTERPOLATION_BY_LABEL[stage.label] ?? stage.label;
  }
  if (stage.kind === 'audio_denoise') return 'Clean audio';
  if (stage.kind === 'face_restore') return 'Faces';
  if (stage.kind === 'remove_background') return 'Background';
  if (stage.kind === 'deinterlace') return deinterlaceName(scanChoice(settings), fps);
  if (stage.kind === 'stabilise') return 'Stabilise';
  return stage.label;
}

/** Short step names for the foot's plan line, keeping the host's stage order. */
export function planLine(
  stages: readonly PlanStage[],
  settings: PlanLineSettings,
  media?: Pick<MediaItem, 'kind'> & Partial<Pick<MediaItem, 'fps'>>,
  options: { amounts?: boolean } = {},
): string[] {
  const isVideo = media?.kind === 'video' || settings.task === 'video';
  const amounts = options.amounts ?? true;
  return stages.map((stage) => stageStepName(stage, settings, isVideo, amounts, media?.fps));
}
