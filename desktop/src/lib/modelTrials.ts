import { FIX_LABELS, fixesOf } from './plan';
import { formatDuration } from './format';
import type { CatalogModel, TrialEstimate, UiSettings } from './types';

/** Why a model cannot join "Try others…" with the shown settings; '' when it can. */
export function trialIncompatibility(
  model: CatalogModel,
  settings: UiSettings,
  models: CatalogModel[],
): string {
  if (!model.installed) return 'Not installed';
  if (settings.task === 'upscale' && model.native_scale < settings.output_scale)
    return `Cannot keep ${settings.output_scale}×`;
  // With several fixes Restore's model is the last one.
  const chip = settings.fix_stages.at(-1)?.fix;
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
