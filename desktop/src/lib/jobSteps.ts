import { INTERPOLATION_BY_TARGET, PLAN_FIX_NAMES, slowMotionName } from './planLine';
import { STEPS } from './steps';
import type { JobStep, LastSteps, UiSettings } from './types';

export type StepNameSettings = Pick<UiSettings, 'output_scale'> &
  Partial<
    Pick<
      UiSettings,
      'task' | 'video_target_resolution' | 'video_interpolation' | 'video_slow_motion'
    >
  >;

export interface PlanMark {
  name: string;
  state: JobStep['state'];
}

export interface StepChip {
  id: string;
  label: string;
  pressed?: boolean;
  disabled?: boolean;
  title?: string;
  skipped?: boolean;
  running?: boolean;
  missing?: boolean;
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
    const slow = slowMotionName(settings);
    if (slow) return slow;
    const target = settings.video_interpolation;
    if (target && target !== 'off' && target in INTERPOLATION_BY_TARGET) {
      return INTERPOLATION_BY_TARGET[target as keyof typeof INTERPOLATION_BY_TARGET];
    }
    return step.label ?? 'Interpolate';
  }
  if (step.kind === 'audio_denoise') return 'Clean audio';
  if (step.kind === 'face_restore') return 'Faces';
  if (step.kind === 'remove_background') return 'Background';
  if (step.kind === 'deinterlace') return 'Deinterlace';
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

/** Split `<job_id>#<index>` into its job id and step index, or null for other targets. */
export function parseStepTarget(target: string): { jobId: string; index: number } | null {
  const hash = target.indexOf('#');
  if (hash <= 0) return null;
  const raw = target.slice(hash + 1);
  if (!/^\d+$/.test(raw)) return null;
  return { jobId: target.slice(0, hash), index: Number(raw) };
}

/** False only when a `<job>#<index>` target no longer points to a kept step of `lastSteps`. */
export function validStepTarget(
  target: string,
  lastSteps: LastSteps | null | undefined,
  mediaId: string,
): boolean {
  if (!target.includes('#')) return true;
  if (!lastSteps || lastSteps.media_id !== mediaId || lastSteps.steps.length < 2) return false;
  const parsed = parseStepTarget(target);
  if (!parsed || parsed.jobId !== lastSteps.job_id) return false;
  if (parsed.index < 0 || parsed.index >= lastSteps.steps.length - 1) return false;
  const step = lastSteps.steps[parsed.index];
  return Boolean(step && step.state !== 'skipped' && step.kept);
}

/** Comparison side label (`After Noise`) for a step target of `lastSteps`. */
export function stepShowingLabel(
  target: string,
  lastSteps: LastSteps | null | undefined,
  settings: StepNameSettings,
): string {
  const parsed = parseStepTarget(target);
  if (!parsed || !lastSteps || parsed.jobId !== lastSteps.job_id) return '';
  if (parsed.index < 0 || parsed.index >= lastSteps.steps.length - 1) return '';
  const step = lastSteps.steps[parsed.index];
  if (!step) return '';
  return `After ${stepName(step, settings, false)}`;
}

/** Step result chips for the viewer head when the last photo job had two or more steps. */
export function resultStepChips(
  lastSteps: LastSteps,
  showingTarget: string,
  settings: StepNameSettings,
  jobScale?: number,
): StepChip[] {
  const { steps, job_id: jobId } = lastSteps;
  if (steps.length < 2) return [];
  if (steps.some((step) => step.kind === 'edit' || step.kind === 'video')) return [];
  const lastIndex = steps.length - 1;
  return steps.map((step, index) => {
    const isLast = index === lastIndex;
    const id = isLast ? jobId : `${jobId}#${index}`;
    const name = stepName(step, settings, false);
    const scale = jobScale && jobScale > 0 ? jobScale : settings.output_scale;
    const label = isLast && step.kind === 'upscale' ? (scale > 0 ? `${scale}×` : 'Upscale') : name;
    const skipped = step.state === 'skipped';
    const disabled = skipped || (!isLast && !step.kept);
    let title: string | undefined = step.note;
    if (!skipped) {
      if (isLast) title = 'The finished result';
      else if (!step.kept) title = "This step's picture could not be kept.";
      else if (step.kind === 'remove_background') {
        title = 'The picture with the background removed';
      } else title = `The picture after the ${name} fix`;
    }
    return {
      id,
      label,
      pressed: !disabled && showingTarget === id,
      disabled,
      skipped,
      title,
    };
  });
}
