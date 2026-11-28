import { servesStep } from './plan';
import { STEPS } from './steps';
import type { CatalogModel, StepStageSelection, UiSettings } from './types';

/** The registry id of the step the Backgr. row stores. */
export const BACKGROUND_STEP = 'remove_background';

/** `stages` with the entry of `step` replaced by `entry`, appended, or removed (`null`). */
export function withStepStage(
  stages: readonly StepStageSelection[] | undefined,
  step: string,
  entry: StepStageSelection | null,
): StepStageSelection[] {
  const current = stages ?? [];
  const index = current.findIndex((selection) => selection.step === step);
  if (index < 0) return entry ? [...current, entry] : [...current];
  if (!entry) return current.filter((_, at) => at !== index);
  return current.map((selection, at) => (at === index ? entry : selection));
}

/** The model the Backgr. row chooses: the first catalog model that serves the step. */
export function backgroundModel(models: readonly CatalogModel[]): CatalogModel | undefined {
  const step = STEPS.find((candidate) => candidate.id === BACKGROUND_STEP);
  return step ? models.find((model) => servesStep(model, step)) : undefined;
}

/** Whether the background is set to be removed. */
export function backgroundRemoved(settings: Pick<UiSettings, 'step_stages'>): boolean {
  return (settings.step_stages ?? []).some((selection) => selection.step === BACKGROUND_STEP);
}

/** Whether the Backgr. row is drawn: a photo task, the engine feature and a model, or a stored choice. */
export function backgroundRowShown(o: {
  task: UiSettings['task'];
  features: readonly string[];
  model: CatalogModel | undefined;
  removed: boolean;
}): boolean {
  if (o.task !== 'upscale' && o.task !== 'denoise') return false;
  if (o.removed) return true;
  return o.features.includes('image_background_removal') && o.model !== undefined;
}

/** The registry id of the step the Scan row stores. */
export const SCAN_STEP = 'deinterlace';

/** `mode:parity` of the stored step; `auto` is no step at all (the decoder's own behaviour). */
export type ScanChoice = 'auto' | `${'frame' | 'field'}:${'auto' | 'tff' | 'bff'}`;

const SCAN_CHOICES: ReadonlySet<string> = new Set([
  'field:auto',
  'frame:auto',
  'frame:tff',
  'field:tff',
  'frame:bff',
  'field:bff',
]);

export function scanChoice(settings: Pick<UiSettings, 'step_stages'>): ScanChoice {
  const stored = (settings.step_stages ?? []).find((selection) => selection.step === SCAN_STEP);
  if (!stored) return 'auto';
  const mode = stored.params?.mode === 'field' ? 'field' : 'frame';
  const parity = stored.params?.parity;
  const choice = `${mode}:${parity === 'tff' || parity === 'bff' ? parity : 'auto'}`;
  return SCAN_CHOICES.has(choice) ? (choice as ScanChoice) : 'auto';
}

/** The entry to store: both parameters are written, as the registry lists them. */
export function scanStage(choice: ScanChoice): StepStageSelection | null {
  if (choice === 'auto') return null;
  const [mode, parity] = choice.split(':');
  return { step: SCAN_STEP, params: { mode, parity } };
}

/** False only for parity auto on a file that says progressive (video_pipeline.py:360-362). */
export function scanApplies(choice: ScanChoice, fieldOrder: string | undefined): boolean {
  if (choice === 'auto') return false;
  return !(choice.endsWith(':auto') && fieldOrder === 'progressive');
}

/** The rate after Scan: twice `fps` for a `field` choice that applies, else `fps`. */
export function scanRate(choice: ScanChoice, fieldOrder: string | undefined, fps: number): number {
  return choice.startsWith('field:') && scanApplies(choice, fieldOrder) ? fps * 2 : fps;
}

/** Whether the Scan row is drawn: the file says interlaced or says nothing, or a choice is stored. */
export function scanRowShown(o: {
  task: UiSettings['task'];
  usingTemporalVideo: boolean;
  features: readonly string[];
  fieldOrder: string | undefined;
  choice: ScanChoice;
}): boolean {
  if (o.task !== 'video' || o.usingTemporalVideo) return false;
  if (o.choice !== 'auto') return true;
  return (
    o.features.includes('video_deinterlace') &&
    (o.fieldOrder === 'tff' || o.fieldOrder === 'bff' || o.fieldOrder === 'unknown')
  );
}

/** At most three decimals, no trailing zeros: `25`, `59.94`. */
export function formatRate(fps: number): string {
  return String(Number(fps.toFixed(3)));
}

/** The plan line's name of the step; without a known rate a doubled rate reads `2×`. */
export function deinterlaceName(choice: ScanChoice, fps: number | undefined): string {
  if (!choice.startsWith('field:')) return 'Deinterlace';
  return fps && fps > 0 ? `Deinterlace to ${formatRate(fps * 2)} fps` : 'Deinterlace 2×';
}

export interface ScanOption {
  value: ScanChoice;
  label: string;
  group: '' | 'hand';
}

export function scanOptions(
  fieldOrder: string | undefined,
  fps: number,
  choice: ScanChoice,
): ScanOption[] {
  const known = fps > 0;
  const at = (rate: number) => (known ? ` · ${formatRate(rate)} fps` : '');
  const options: ScanOption[] = [
    {
      value: 'auto',
      label: fieldOrder === 'tff' || fieldOrder === 'bff' ? 'Automatic · interlaced' : 'Automatic',
      group: '',
    },
    {
      value: 'field:auto',
      label:
        fieldOrder === 'progressive' || !known
          ? 'Double rate'
          : `Double rate · ${formatRate(fps * 2)} fps`,
      group: '',
    },
  ];
  for (const [parity, name] of [
    ['tff', 'Top field first'],
    ['bff', 'Bottom field first'],
  ] as const) {
    options.push(
      { value: `frame:${parity}`, label: `${name}${at(fps)}`, group: 'hand' },
      {
        value: `field:${parity}`,
        label: known ? `${name}${at(fps * 2)}` : `${name} · double rate`,
        group: 'hand',
      },
    );
  }
  if (choice === 'frame:auto') {
    options.push({ value: choice, label: `Deinterlace${at(fps)}`, group: '' });
  }
  return options;
}
