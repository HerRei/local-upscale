import { customModels } from './customModels';
import { formatBytes } from './format';
import { displayName, type PlanStage } from './plan';
import { PHASES, STEPS, type StepDefinition } from './steps';
import type { CatalogModel, ModelDownloadStatus, UiSettings } from './types';

export interface RowChoice {
  /** The parameter it writes and the radiogroup's accessible name. */
  param: 'strength' | 'smoothing';
  name: 'Strength' | 'Smoothing';
  options: readonly { value: number | string; label: string; title: string }[];
  /** The value of an entry that does not name the parameter. */
  fallback: number | string;
}

export interface FixRow {
  id: string;
  /** `step_stage`: an entry of `settings.step_stages`. */
  source: 'fix_stage' | 'setting' | 'step_stage';
  label: string;
  title: string;
  tag: '' | 'Labs';
  common: boolean;
  /** The row's value is this choice; its popover shows it as segments. */
  choice?: RowChoice;
  /** The row's value opens painting in the viewer, not a popover. */
  paints?: boolean;
}

export const STRENGTH_FEATURE = 'fix_strength';
export const STRENGTH_STOPS = [
  { value: 0.4, label: 'Light', title: '40 % of the fix' },
  { value: 0.7, label: 'Medium', title: '70 % of the fix' },
  { value: 1, label: 'Full', title: 'The whole fix' },
] as const;

/** The entry's strength: its `params.strength` when that is a number from 0 to 1, else 1. */
export function strengthOf(entry: { params?: Record<string, unknown> } | undefined): number {
  const value = entry?.params?.strength;
  return typeof value === 'number' && value >= 0 && value <= 1 ? value : 1;
}

/** The entry with that strength. At 1 the key goes, and `params` with it when it is empty. */
export function withStrength<T extends { params?: Record<string, unknown> }>(
  entry: T,
  value: number,
): T {
  const { params, ...rest } = entry;
  const next: Record<string, unknown> = { ...params };
  delete next.strength;
  if (value < 1) next.strength = value;
  return (Object.keys(next).length > 0 ? { ...rest, params: next } : rest) as T;
}

/** `Light`, `Medium`, `Full`; `55%` for an amount more than 0.005 from every stop. */
export function strengthLabel(value: number): string {
  const stop = STRENGTH_STOPS.find((entry) => Math.abs(entry.value - value) <= 0.005);
  return stop ? stop.label : `${Math.round(value * 100)}%`;
}

/** The step declares `strength` (steps.json). */
export function takesStrength(stepId: string, steps: readonly StepDefinition[] = STEPS): boolean {
  return Boolean(
    steps.find((step) => step.id === stepId)?.parameters.some((param) => param.name === 'strength'),
  );
}

/** The row's choice is on show: the engine lists it, or the entry already stores a strength. */
export function choiceShown(
  row: FixRow,
  features: readonly string[],
  entry: { params?: Record<string, unknown> } | undefined,
): boolean {
  return Boolean(row.choice) && (features.includes(STRENGTH_FEATURE) || strengthOf(entry) < 1);
}

/** Steps whose Fix row is ticked by an entry in `settings.step_stages`. */
export const STEP_ROWS = new Set<string>(['stabilise', 'repair']);

export const STABILISE_STEP = 'stabilise';
export const REPAIR_STEP = 'repair';
export type Smoothing = 'light' | 'medium' | 'strong';

/** The crop is the engine's (`SMOOTHING` levels in stabilise.py): a fraction of each edge. */
export const SMOOTHING_STOPS = [
  {
    value: 'light',
    label: 'Light',
    title: "Evens out the camera's path over half a second · cuts 3 % off each edge",
    crop: 0.03,
  },
  {
    value: 'medium',
    label: 'Medium',
    title: "Evens out the camera's path over one second · cuts 5 % off each edge",
    crop: 0.05,
  },
  {
    value: 'strong',
    label: 'Strong',
    title: "Evens out the camera's path over two seconds · cuts 8 % off each edge",
    crop: 0.08,
  },
] as const;

/** The entry's smoothing; an entry that names none (or an unknown one) reads as Medium. */
export function smoothingOf(entry: { params?: Record<string, unknown> } | undefined): Smoothing {
  const value = entry?.params?.smoothing;
  return value === 'light' || value === 'strong' ? value : 'medium';
}

export function smoothingStop(smoothing: Smoothing): (typeof SMOOTHING_STOPS)[number] {
  return SMOOTHING_STOPS.find((stop) => stop.value === smoothing) ?? SMOOTHING_STOPS[1];
}

/** `Medium · cuts 5 % off each edge`: the Plan sheet's note for the step. */
export function smoothingNote(smoothing: Smoothing): string {
  const stop = smoothingStop(smoothing);
  return `${stop.label} · ${stop.title.split(' · ')[1]}`;
}

/** What the Dust & scratches row knows of the queue: the open photo's marks and how many are painted. */
export interface MediaFacts {
  marks: number;
  marked: number;
  photos: number;
  /** A photo is open in the viewer. */
  open: boolean;
}

/** The value of a ticked Dust & scratches row: `Paint…`, `1 mark`, `3 marks`. */
export function marksValue(marks: number): string {
  return marks > 0 ? `${marks} ${marks === 1 ? 'mark' : 'marks'}` : 'Paint…';
}

/** The value's `title`; with several photos it adds how many have marks. */
export function marksTitle(facts: MediaFacts): string {
  const own =
    facts.marks > 0
      ? `${marksValue(facts.marks)} on this photo. Click to change them.`
      : 'Paint over the marks on this photo';
  if (facts.photos < 2) return own;
  const queue = `${facts.marked} of ${facts.photos} photos ${facts.marked === 1 ? 'has' : 'have'} marks.`;
  return facts.marks > 0 ? `${own} ${queue}` : `${own}. ${queue}`;
}

export const SETTING_ROWS: Record<string, keyof UiSettings> = {
  deflicker: 'deflicker',
};

export const LABS_ROWS = new Set<string>(['deflicker', 'stabilise']);

const COMMON_ROWS = new Set<string>(['blur', 'jpeg', 'noise', 'deflicker', 'stabilise', 'repair']);

interface RowCopy {
  label: string;
  title: string;
}

function copyForRow(step: StepDefinition, media: 'image' | 'video'): RowCopy {
  switch (step.id) {
    case 'blur':
      return {
        label: 'Blur',
        title: 'Reduces blur from motion or missed focus.',
      };
    case 'jpeg':
      return media === 'video'
        ? {
            label: 'Compression',
            title: 'Removes blocks and ringing left by video compression.',
          }
        : {
            label: 'JPEG artifacts',
            title: 'Removes blocks and ringing left by JPEG compression.',
          };
    case 'noise':
      return {
        label: 'Noise',
        title: 'Removes grain and sensor noise.',
      };
    case 'colour':
      return {
        label: 'Colour',
        title: `Adds colour to black-and-white ${media === 'video' ? 'videos' : 'photos'}`,
      };
    case 'stabilise':
      return {
        label: 'Stabilise',
        title:
          'Steadies shaky handheld video. The picture is enlarged a little so its moving edges stay out of view.',
      };
    case 'repair':
      return {
        label: 'Dust & scratches',
        title: 'Repairs small marks that you paint over: dust, scratches, spots.',
      };
    case 'deflicker':
      return {
        label: 'De-flicker',
        title:
          'Evens out brightness flicker in the original video and the shimmer that frame-by-frame upscaling adds in still areas. It decides from the original, so motion, cuts and real light changes pass through. Adds about 40 ms per frame. Off by default.',
      };
    default:
      return { label: step.label, title: '' };
  }
}

function isFixListStep(step: StepDefinition): boolean {
  return step.is_fix_stage || Object.hasOwn(SETTING_ROWS, step.id) || STEP_ROWS.has(step.id);
}

/** Order of Fix rows in the panel, by registry phase and order. */
export function fixRowOrder(
  steps: readonly StepDefinition[] = STEPS,
  phases: readonly string[] = PHASES,
): string[] {
  const phaseIndex = (phase: string) => {
    const index = phases.indexOf(phase);
    return index === -1 ? Number.MAX_SAFE_INTEGER : index;
  };
  return [...steps]
    .filter(isFixListStep)
    .sort(
      (left, right) =>
        phaseIndex(left.phase) - phaseIndex(right.phase) ||
        left.order - right.order ||
        left.id.localeCompare(right.id),
    )
    .map((step) => step.id);
}

/**
 * Fix rows offered for this media kind and engine feature set, in `fixRowOrder`. A step stored in
 * `stored` keeps its row whatever the engine lists, so it can still be unticked.
 */
export function fixRows(
  media: 'image' | 'video',
  features: readonly string[],
  steps: readonly StepDefinition[] = STEPS,
  phases: readonly string[] = PHASES,
  stored: readonly string[] = [],
): FixRow[] {
  const order = fixRowOrder(steps, phases);
  const rank = new Map(order.map((id, index) => [id, index]));
  return steps
    .filter(
      (step) =>
        isFixListStep(step) &&
        step.media.includes(media) &&
        (!step.required_feature ||
          features.includes(step.required_feature) ||
          (STEP_ROWS.has(step.id) && stored.includes(step.id))),
    )
    .sort(
      (left, right) =>
        (rank.get(left.id) ?? Number.MAX_SAFE_INTEGER) -
        (rank.get(right.id) ?? Number.MAX_SAFE_INTEGER),
    )
    .map((step) => {
      const copy = copyForRow(step, media);
      return {
        id: step.id,
        source: STEP_ROWS.has(step.id)
          ? 'step_stage'
          : Object.hasOwn(SETTING_ROWS, step.id)
            ? 'setting'
            : 'fix_stage',
        label: copy.label,
        title: copy.title,
        tag: LABS_ROWS.has(step.id) ? 'Labs' : '',
        common: COMMON_ROWS.has(step.id),
        ...(step.id === REPAIR_STEP ? { paints: true } : {}),
        ...(step.id === STABILISE_STEP
          ? {
              choice: {
                param: 'smoothing' as const,
                name: 'Smoothing' as const,
                options: SMOOTHING_STOPS,
                fallback: 'medium',
              },
            }
          : {}),
        ...(takesStrength(step.id, steps)
          ? {
              choice: {
                param: 'strength' as const,
                name: 'Strength' as const,
                options: STRENGTH_STOPS,
                fallback: 1,
              },
            }
          : {}),
      };
    });
}

/** Hide non-common unticked rows behind `More fixes (n)` only when 2 or more would be hidden. */
export function splitFixRows(
  rows: readonly FixRow[],
  isTicked: (row: FixRow) => boolean,
  expanded: boolean,
): { shown: FixRow[]; hiddenCount: number } {
  const hiddenCount = rows.filter((row) => !row.common && !isTicked(row)).length;
  if (hiddenCount < 2) {
    return { shown: [...rows], hiddenCount: 0 };
  }
  return {
    shown: expanded ? [...rows] : rows.filter((row) => row.common || isTicked(row)),
    hiddenCount,
  };
}

/** The value shown on the right of a ticked `fix_stage` row. */
export function fixRowValue(
  row: FixRow,
  plan: readonly PlanStage[],
  settings: UiSettings,
  models: readonly CatalogModel[],
  downloadMap: Map<string, ModelDownloadStatus>,
  showChoice = false,
): string {
  const rowId = row.id;
  const stage = plan.find((entry) => entry.fix === rowId);
  if (stage) {
    if (stage.missing) return 'No model for this fix';
    const name = stage.model
      ? displayName(stage.model)
      : stage.custom !== undefined
        ? (customModels(settings).find((model) => model.path === settings.custom_model_path)
            ?.name ?? 'Checkpoint')
        : '';
    const lead =
      showChoice && row.choice && name
        ? strengthLabel(strengthOf(settings.fix_stages.find((entry) => entry.fix === rowId)))
        : name;
    const stageDownload = stage.model ? downloadMap.get(stage.model.model_id) : undefined;
    if (stageDownload) return lead;
    if (stage.needsFile && !stage.installed) return 'Needs your file';
    if (!stage.installed && stage.canDownload && stage.sizeBytes > 0) {
      return `${lead} · ${formatBytes(stage.sizeBytes)}`;
    }
    return lead;
  }

  const selection = settings.fix_stages.find((entry) => entry.fix === rowId);
  if (!selection?.model_id) return '';
  const name =
    selection.model_id === '__custom__'
      ? (customModels(settings).find((model) => model.path === settings.custom_model_path)?.name ??
        'Checkpoint')
      : (() => {
          const model = models.find((entry) => entry.model_id === selection.model_id);
          return model ? displayName(model) : '';
        })();
  return showChoice && row.choice && name ? strengthLabel(strengthOf(selection)) : name;
}
