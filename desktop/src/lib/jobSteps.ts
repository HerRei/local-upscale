import { INTERPOLATION_BY_TARGET, PLAN_FIX_NAMES } from './planLine';
import { STEPS } from './steps';
import type { JobStep, UiSettings } from './types';

export type StepNameSettings = Pick<UiSettings, 'output_scale'> &
  Partial<Pick<UiSettings, 'task' | 'video_target_resolution' | 'video_interpolation'>>;

export interface PlanMark {
  name: string;
  state: JobStep['state'];
}

/** Plan-line name for a running or queued job step. */
export function stepName(
  step: Pick<JobStep, 'kind' | 'fix'> & { label?: string },
  settings: StepNameSettings,
  isVideo = settings.task === 'video',
): string {
  if (step.fix) {
    if (step.fix === 'jpeg') return isVideo ? 'Compression' : 'JPEG';
    if (step.fix in PLAN_FIX_NAMES) {
      return PLAN_FIX_NAMES[step.fix as keyof typeof PLAN_FIX_NAMES];
    }
    return step.label ?? step.fix;
  }
  if (step.kind === 'deblock') return isVideo ? 'Compression' : 'JPEG';
  if (step.kind === 'upscale' || step.kind === 'video') {
    if (step.kind === 'video' && settings.video_target_resolution) {
      return `Upscale to ${settings.video_target_resolution} px`;
    }
    return settings.output_scale > 0 ? `Upscale ${settings.output_scale}×` : 'Upscale';
  }
  if (step.kind === 'restore') return 'Fix';
  if (step.kind === 'edit') return 'Edit';
  if (step.kind === 'deflicker') return 'De-flicker';
  if (step.kind === 'interpolate') {
    const target = settings.video_interpolation;
    if (target && target !== 'off' && target in INTERPOLATION_BY_TARGET) {
      return INTERPOLATION_BY_TARGET[target as keyof typeof INTERPOLATION_BY_TARGET];
    }
    return step.label ?? 'Interpolate';
  }
  if (step.kind === 'audio_denoise') return 'Clean audio';
  if (step.kind === 'face_restore') return 'Faces';
  return (
    step.label ??
    STEPS.find((entry) => entry.id === step.kind)?.label ??
    (step.kind || 'Processing')
  );
}

/** Viewer and status-bar label for a job of two or more steps. */
export function stepLabel(
  steps: readonly JobStep[],
  settings: StepNameSettings = { output_scale: 0 },
  isVideo = settings.task === 'video',
): string {
  if (steps.length < 2) return '';
  const runningIndex = steps.findIndex((step) => step.state === 'running');
  if (runningIndex >= 0) {
    const name = stepName(steps[runningIndex], settings, isVideo);
    return `Step ${runningIndex + 1} of ${steps.length} · ${name}`;
  }
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const step = steps[index];
    if (step.state === 'skipped' || step.state === 'done') {
      const name = stepName(step, settings, isVideo);
      return step.state === 'skipped'
        ? `Step ${index + 1} of ${steps.length} · ${name} skipped`
        : `Step ${index + 1} of ${steps.length} · ${name}`;
    }
  }
  return '';
}

/** Pair plan-line names with their live step states when they match the running job. */
export function planMarks(steps: readonly JobStep[], names: readonly string[]): PlanMark[] {
  if (steps.length < 2 || steps.length !== names.length) return [];
  return names.map((name, index) => ({
    name,
    state: steps[index].state,
  }));
}
