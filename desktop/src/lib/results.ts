/**
 * Saved results of one image and the pair the preview compares.
 *
 * Nothing here knows the layout: a split, two panes or a flip view take the
 * same choice, labels and side states. The host owns the history and checks
 * every target before it decodes anything (`list_media_results`,
 * `request_image_comparison`).
 */
import { FIX_LABELS, fixesOf, formatDuration } from './state';
import type {
  CatalogModel,
  ComparisonPair,
  ComparisonSlot,
  FixKind,
  JobRecord,
  MediaResults,
  PairChoice,
  ResultSummary,
  ResultTarget,
  TrialEstimate,
  UiSettings,
} from './types';

export const ORIGINAL = 'original';
type Side = 'showing' | 'against';
type Size = { width?: number; height?: number };

/** Completed results whose file exists: what Showing and against can hold. */
export function selectableResults(results: ResultSummary[]): ResultSummary[] {
  return results.filter((result) => result.status === 'completed' && result.available);
}

/** Unpinned, Showing follows the newest saved result against the Original. */
export function defaultChoice(history: MediaResults | undefined): PairChoice {
  return { showing: history?.default_result_id ?? ORIGINAL, against: ORIGINAL, pinned: false };
}

/** A manual choice holds until one of its results is no longer available. */
export function effectiveChoice(
  history: MediaResults | undefined,
  saved: PairChoice | undefined,
): PairChoice {
  if (!saved?.pinned || !history) return defaultChoice(history);
  const gone = (target: ResultTarget) =>
    target !== ORIGINAL &&
    history.results.some((result) => result.job_id === target && !result.available);
  return gone(saved.showing) || gone(saved.against) ? defaultChoice(history) : saved;
}

/** Choose one side; choosing what the other side holds swaps the two. */
export function chooseTarget(choice: PairChoice, side: Side, target: ResultTarget): PairChoice {
  const other: Side = side === 'showing' ? 'against' : 'showing';
  const next = { ...choice, pinned: true };
  if (choice[other] === target) next[other] = choice[side];
  next[side] = target;
  return next;
}

/**
 * Previous Result (older) and Next Result (newer) move Showing only: they skip
 * results without a file and the one the reference side holds, and stop at
 * the ends (null). From the Original, Previous goes to the newest result.
 */
export function stepShowing(
  results: ResultSummary[],
  choice: PairChoice,
  direction: 'older' | 'newer',
): ResultTarget | null {
  const usable = selectableResults(results).filter(
    (result) => result.job_id === choice.showing || result.job_id !== choice.against,
  );
  const index = usable.findIndex((result) => result.job_id === choice.showing);
  if (index < 0) return direction === 'older' ? (usable[0]?.job_id ?? null) : null;
  return usable[index + (direction === 'older' ? 1 : -1)]?.job_id ?? null;
}

/** A pair slot belongs to the current choice only if media and target match. */
export function slotFor(
  pair: ComparisonPair | undefined,
  mediaId: string,
  side: Side,
  target: ResultTarget,
): ComparisonSlot | undefined {
  return pair?.media_id === mediaId && pair[side].target_id === target ? pair[side] : undefined;
}

/** Same framing as the source (within rounding of the saved size), so it can share a split. */
export function alignedFraming(output: Size, source: Size): boolean {
  if (!output.width || !output.height || !source.width || !source.height) return true;
  return (
    Math.abs(output.width * source.height - output.height * source.width) <=
    Math.max(source.width, source.height)
  );
}

function times(value: number): string {
  return `${Number(value.toFixed(value < 10 ? 2 : 1))}×`;
}

/**
 * How large the saved file is against the source ("4× output"), from the
 * decoded output dimensions when they are known, else from the recorded
 * scale. Both sides are bounded previews, so neither is called enlarged.
 */
export function scaleLabel(result: ResultSummary, output: Size = {}, source: Size = {}): string {
  if (result.kind === 'edit') return 'Edit';
  if (output.width && source.width) return `${times(output.width / source.width)} output`;
  return result.scale > 0 ? `${result.scale}× output` : '';
}

/** A divider or chip label: the model and its output scale, or Original. */
export function sideLabel(
  result: ResultSummary | undefined,
  output: Size = {},
  source: Size = {},
): string {
  if (!result) return 'Original';
  return [result.model_label, scaleLabel(result, output, source)].filter(Boolean).join(' · ');
}

/** The recorded quality or recipe; older jobs did not record one. */
export function settingsLabel(result: ResultSummary): string {
  const ui = result.ui;
  if (!ui) return 'Settings unavailable';
  const quality =
    ui.recipe_name ||
    (ui.quality === 'quick' ? 'Quick' : ui.quality === 'best' ? 'Best' : 'Custom');
  const content =
    ui.content === 'illustration' ? 'Illustration' : ui.content === 'photo' ? 'Photo' : '';
  return [quality, content].filter(Boolean).join(' · ');
}

export function runTimeLabel(result: ResultSummary): string {
  return result.elapsed_seconds > 0
    ? `Run time ${formatDuration(result.elapsed_seconds)}`
    : 'Run time unavailable';
}

/** The chooser button: model, settings and run time. */
export function resultTitle(result: ResultSummary | undefined): string {
  if (!result) return 'Original';
  return [
    result.kind === 'edit' ? `Edit · ${result.model_label}` : result.model_label,
    settingsLabel(result),
    result.elapsed_seconds > 0 ? formatDuration(result.elapsed_seconds) : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

/** Configured stages besides the primary model. A note says when one was skipped. */
export function configuredStages(result: ResultSummary): string[] {
  return result.stages
    .filter((stage) => stage.model_id !== result.model_id)
    .map((stage) =>
      stage.kind === 'face_restore'
        ? `Faces · ${stage.model_label}`
        : stage.kind === 'deblock'
          ? `JPEG fix · ${stage.model_label}`
          : `Fix · ${stage.model_label}`,
    );
}

export function statusLabel(result: ResultSummary): string {
  if (result.status === 'failed') return 'Failed';
  if (result.status === 'cancelled') return 'Cancelled';
  if (result.status === 'interrupted') return 'Interrupted';
  return result.available ? '' : 'File missing';
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "today 14:02", "Mon 11:40" within the week, else "3 Nov". */
export function whenLabel(milliseconds: number, now = Date.now()): string {
  const date = new Date(milliseconds);
  const today = new Date(now);
  const clock = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  if (date.toDateString() === today.toDateString()) return `today ${clock}`;
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  if (milliseconds < startOfToday && startOfToday - milliseconds < 6 * 86_400_000)
    return `${DAYS[date.getDay()]} ${clock}`;
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

/** "Running SwinIR · 2 queued": the active job first, then this image's queue. */
export function queueModelState(jobs: JobRecord[], mediaId: string, activeJobId: string): string {
  const inflight = jobs.filter(
    (job) =>
      job.media_id === mediaId &&
      ['queued', 'starting', 'running', 'cancelling'].includes(job.status),
  );
  const active =
    inflight.find((job) => job.id === activeJobId) ??
    inflight.find((job) => job.status !== 'queued');
  const queued = inflight.filter((job) => job.status === 'queued').length;
  if (active)
    return `${active.status === 'cancelling' ? 'Cancelling' : 'Running'} ${active.model_label || 'model'}${queued ? ` · ${queued} queued` : ''}`;
  return queued > 1 ? `${queued} queued` : 'queued';
}

/** Why a model cannot join "Try others…" with the shown settings; '' when it can. */
export function trialIncompatibility(
  model: CatalogModel,
  settings: UiSettings,
  models: CatalogModel[],
): string {
  if (!model.installed) return 'Not installed';
  if (settings.task === 'upscale' && model.native_scale < settings.output_scale)
    return `Cannot keep ${settings.output_scale}×`;
  const chip = settings.fixes.find((fix): fix is Exclude<FixKind, 'faces'> => fix !== 'faces');
  if (settings.task === 'denoise' && chip && !fixesOf(model).includes(chip))
    return `Does not fix ${FIX_LABELS[chip]}`;
  if (settings.task === 'upscale' && settings.enable_face_model) {
    const companion = models.find(
      (candidate) => candidate.model_id === model.pair_with && candidate.purposes.includes('face'),
    );
    if (!companion?.installed || companion.native_scale !== model.native_scale)
      return 'Cannot keep Faces';
  }
  return '';
}

/** A total only when every model was measured; otherwise the known part and the unknown names. */
export function trialTime(estimates: TrialEstimate[]): string {
  const unknown = estimates.filter((estimate) => estimate.seconds === null);
  const seconds = estimates.reduce((total, estimate) => total + (estimate.seconds ?? 0), 0);
  if (!unknown.length) return `≈ ${formatDuration(seconds)} in total`;
  const names = unknown.map((estimate) => estimate.model_label).join(', ');
  return seconds > 0
    ? `≈ ${formatDuration(seconds)} measured · not yet measured for ${names}`
    : `Time not yet measured for ${names}`;
}

/** The settings every model keeps, as the plan card shows them. */
export function keptTrialSettings(settings: UiSettings, faces: boolean): string {
  const scale = settings.task === 'denoise' ? '1×' : `${settings.output_scale}×`;
  const fixes = settings.fixes
    .filter((fix) => fix !== 'faces' || faces)
    .map((fix) => (fix === 'faces' ? 'Faces' : `${FIX_LABELS[fix]} fix`));
  return [scale, ...fixes, settings.output_format.toUpperCase()].join(', ');
}

/** What a layout draws for one side of the comparison. */
export interface ComparisonSide {
  /** The bounded preview; '' while it loads or after it failed. */
  src: string;
  label: string;
  state: 'ready' | 'loading' | 'error';
  error: string;
}

export interface SideView extends ComparisonSide {
  /** The saved file's dimensions once decoded (the source's for the Original). */
  width: number;
  height: number;
}

/**
 * One side of the chosen pair. The Original is the probe thumbnail, never a
 * decoded result. A result is ready once its pair slot holds its preview;
 * `automatic` is the completion preview of the job that just finished, used
 * while the slot is still decoding the same file.
 */
export function describeSide(
  target: ResultTarget,
  result: ResultSummary | undefined,
  slot: ComparisonSlot | undefined,
  source: { width: number; height: number; preview_data_url: string },
  automatic = '',
): SideView {
  if (target === ORIGINAL)
    return {
      src: source.preview_data_url,
      label: 'Original',
      state: 'ready',
      error: '',
      width: source.width,
      height: source.height,
    };
  const output = { width: slot?.width ?? 0, height: slot?.height ?? 0 };
  const label = result ? sideLabel(result, output, source) : 'Saved result';
  if (slot?.preview_data_url)
    return { src: slot.preview_data_url, label, state: 'ready', error: '', ...output };
  if (slot?.error) return { src: '', label, state: 'error', error: slot.error, ...output };
  if (automatic) return { src: automatic, label, state: 'ready', error: '', width: 0, height: 0 };
  return { src: '', label, state: 'loading', error: '', width: 0, height: 0 };
}
