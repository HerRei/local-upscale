import { displayName, type PlanStage } from './plan';
import { planLine } from './planLine';
import type { Size } from './viewport';
import type { PatchRegion, PatchState, PatchStep, StartBatchInput, UiSettings } from './types';

/** The patch is this many source pixels on a side, or the picture's shorter side when smaller. */
export const PATCH_SIZE = 384;
const SNAP = 16;
const STEP = 32;
const STEP_SHIFT = 128;

export interface PatchPixels {
  width: number;
  height: number;
  /** RGBA, as `ImageData.data`. */
  data: ArrayLike<number>;
}

function sideFor(image: Size): number {
  return Math.max(1, Math.min(PATCH_SIZE, image.width, image.height));
}

function snapAxis(value: number, side: number, extent: number): number {
  const room = Math.max(0, extent - side);
  const snapped = Math.round(value / SNAP) * SNAP;
  return Math.max(0, Math.min(snapped, Math.floor(room / SNAP) * SNAP));
}

/** Positions snap to 16 px and stay inside the picture. */
export function snapRegion(
  region: Pick<PatchRegion, 'x' | 'y'> & Partial<PatchRegion>,
  image: Size,
): PatchRegion {
  const side = sideFor(image);
  return {
    x: snapAxis(region.x, side, image.width),
    y: snapAxis(region.y, side, image.height),
    width: side,
    height: side,
  };
}

export function centreRegion(image: Size): PatchRegion {
  const side = sideFor(image);
  return snapRegion({ x: (image.width - side) / 2, y: (image.height - side) / 2 }, image);
}

function luminance(pixels: PatchPixels, x: number, y: number): number {
  const i = 4 * (y * pixels.width + x);
  return 0.2126 * pixels.data[i] + 0.7152 * pixels.data[i + 1] + 0.0722 * pixels.data[i + 2];
}

/**
 * The first position for a file: the cell with the most luminance variance on the source
 * preview, weighted down towards the edges so a busy border does not beat the middle.
 * The centre when the preview cannot be read or has no detail at all.
 */
export function pickPatchRegion(pixels: PatchPixels | null, image: Size): PatchRegion {
  const centre = centreRegion(image);
  if (!pixels || pixels.width < 2 || pixels.height < 2 || !image.width || !image.height) {
    return centre;
  }
  const side = sideFor(image);
  const cellW = Math.max(1, Math.round((side * pixels.width) / image.width));
  const cellH = Math.max(1, Math.round((side * pixels.height) / image.height));
  const maxX = Math.max(0, pixels.width - cellW);
  const maxY = Math.max(0, pixels.height - cellH);
  const strideX = Math.max(1, Math.floor(cellW / 2));
  const strideY = Math.max(1, Math.floor(cellH / 2));
  const sample = Math.max(1, Math.floor(Math.min(cellW, cellH) / 16));
  const xs = new Set<number>([maxX]);
  const ys = new Set<number>([maxY]);
  for (let x = 0; x < maxX; x += strideX) xs.add(x);
  for (let y = 0; y < maxY; y += strideY) ys.add(y);

  let best = { score: 0, x: -1, y: -1 };
  for (const y of ys) {
    for (const x of xs) {
      let sum = 0;
      let sumSquares = 0;
      let count = 0;
      for (let yy = y; yy < y + cellH; yy += sample) {
        for (let xx = x; xx < x + cellW; xx += sample) {
          const l = luminance(pixels, xx, yy);
          sum += l;
          sumSquares += l * l;
          count++;
        }
      }
      const mean = sum / count;
      const variance = sumSquares / count - mean * mean;
      const dx = (x + cellW / 2) / pixels.width - 0.5;
      const dy = (y + cellH / 2) / pixels.height - 0.5;
      const distance = Math.min(1, Math.hypot(dx, dy) / Math.SQRT1_2);
      const score = variance * (1 - 0.5 * distance);
      if (score > best.score) best = { score, x, y };
    }
  }
  if (best.x < 0 || best.score < 1) return centre;
  return snapRegion(
    { x: (best.x * image.width) / pixels.width, y: (best.y * image.height) / pixels.height },
    image,
  );
}

export type PatchArrow = 'ArrowLeft' | 'ArrowRight' | 'ArrowUp' | 'ArrowDown';

/** An arrow key moves the patch by 32 px, with Shift by 128 px, and keeps it in the picture. */
export function movePatch(
  region: PatchRegion,
  key: PatchArrow,
  shift: boolean,
  image: Size,
): PatchRegion {
  const step = shift ? STEP_SHIFT : STEP;
  const dx = key === 'ArrowLeft' ? -step : key === 'ArrowRight' ? step : 0;
  const dy = key === 'ArrowUp' ? -step : key === 'ArrowDown' ? step : 0;
  return snapRegion({ x: region.x + dx, y: region.y + dy }, image);
}

/** The id a step is known by while the plan keeps it. */
export function patchStepKey(step: Pick<PatchStep, 'kind' | 'fix'>): string {
  return step.fix || step.kind;
}

/** One entry of the plan line, as the tab names it. */
export interface PatchPlanEntry {
  name: string;
  /** Shown in the `title` of a chip whose model is missing. */
  modelName?: string;
  /** The model needs a licence tick or an own file, which only the panel can give. */
  needsPanel?: boolean;
}

const VIDEO_ONLY_KINDS: ReadonlySet<PlanStage['kind']> = new Set([
  'deflicker',
  'interpolate',
  'audio_denoise',
  'video',
  'edit',
  'deinterlace',
  'stabilise',
]);

/**
 * The plan line as the tab names it: the steps a photo goes through, the upscale named by its size.
 * Missing models and models only a panel step can fix are marked for the chips and the button.
 */
export function patchPlanEntries(
  stages: readonly PlanStage[],
  settings: Pick<
    UiSettings,
    'task' | 'output_scale' | 'video_target_resolution' | 'video_interpolation'
  >,
): PatchPlanEntry[] {
  const kept = stages.filter((stage) => !VIDEO_ONLY_KINDS.has(stage.kind));
  const names = planLine(kept, settings, { kind: 'image' }, { amounts: false });
  return kept.map((stage, index) => ({
    name: names[index].replace(/^Upscale (?=\d)/, ''),
    modelName: stage.model ? displayName(stage.model) : undefined,
    needsPanel: !stage.installed && stage.needsFile ? true : undefined,
  }));
}

export interface PatchChip {
  /** `original`, or the step's key. */
  id: string;
  /** Index into the state's steps; -1 for Original. */
  index: number;
  label: string;
  state: 'original' | PatchStep['state'];
  disabled: boolean;
  title: string;
}

function seconds(value: number): string {
  return value < 10 ? value.toFixed(1) : String(Math.round(value));
}

/**
 * `Original` and one chip per plan step. The state fills in what the host reports; a step the
 * host has not reported yet is waiting.
 */
export function patchChips(
  state: PatchState | null | undefined,
  plan: PatchPlanEntry[],
): PatchChip[] {
  const chips: PatchChip[] = [
    { id: 'original', index: -1, label: 'Original', state: 'original', disabled: false, title: '' },
  ];
  plan.forEach((entry, index) => {
    const step = state?.steps[index];
    const id = step ? patchStepKey(step) : `step-${index}`;
    const stepState = step?.state ?? 'waiting';
    let label = entry.name;
    let title = '';
    if (stepState === 'missing') {
      label = `↓ ${entry.name}`;
      title = `${entry.modelName ?? entry.name} is not downloaded.`;
    } else if (stepState === 'skipped') {
      title = step?.note ?? '';
    } else if (stepState === 'done' && step?.seconds) {
      title = `After ${entry.name} · ${seconds(step.seconds)} s`;
    }
    chips.push({
      id,
      index,
      label,
      state: stepState,
      disabled: stepState === 'skipped',
      title,
    });
  });
  return chips;
}

/**
 * The step whose picture the patch shows: the newest finished one, until the user picked a
 * chip; the pick holds for as long as the plan keeps it and has a picture. -1 is Original.
 */
export function shownStep(steps: PatchStep[], picked: string | null): number {
  if (picked !== null) {
    const index = steps.findIndex((step) => patchStepKey(step) === picked);
    if (index >= 0 && steps[index].state === 'done') return index;
  }
  for (let index = steps.length - 1; index >= 0; index--) {
    if (steps[index].state === 'done') return index;
  }
  return -1;
}

/** What a patch request depends on; the same key means the same picture. */
export function patchRequestKey(request: {
  mediaId: string;
  slot: 'a' | 'b';
  region: PatchRegion;
  input: Partial<StartBatchInput>;
  replace?: { slot: 'a' | 'b'; fix: string; model_id: string } | null;
  stageLimit?: number | null;
  /** The photo's `repair_rev`: new paint is a new mask, so the same plan asks again. */
  repairRev?: number;
}): string {
  const { input } = request;
  return JSON.stringify([
    request.mediaId,
    request.slot,
    request.region.x,
    request.region.y,
    request.region.width,
    request.region.height,
    input.task,
    input.model_id,
    input.fix_stages,
    input.step_stages,
    input.output_scale,
    input.ui?.quality,
    input.ui?.content,
    input.device,
    input.precision,
    input.tile_size,
    input.halo,
    input.neural_engine,
    input.safe_memory,
    request.replace ?? null,
    request.stageLimit ?? null,
    request.repairRev ?? 0,
  ]);
}
