import { editFamilyLabel, editVariantName } from './editing';
import { displayName } from './plan';
import type { AppSnapshot } from './types';
import { videoModelShortName } from './videoEngines';

export function modelDisplayName(
  snapshot: Pick<AppSnapshot, 'catalog' | 'edit'>,
  modelId: string,
  withVariant = false,
): string {
  const image = snapshot.catalog.models.find((item) => item.model_id === modelId);
  if (image) return displayName(image);

  const video = snapshot.catalog.video_models.find((item) => item.model_id === modelId);
  if (video) return videoModelShortName(video);

  const extra = [
    ...(snapshot.catalog.interpolation_models ?? []),
    ...(snapshot.catalog.audio_models ?? []),
  ].find((item) => item.model_id === modelId);
  if (extra) return extra.name.split(' — ')[0];

  const edit = snapshot.edit.models.find((item) => item.model_id === modelId);
  if (edit) {
    const family = editFamilyLabel(edit.family);
    return withVariant ? `${family} · ${editVariantName(edit)}` : family;
  }

  return 'a model';
}

export function modelDownloadSize(
  snapshot: Pick<AppSnapshot, 'catalog' | 'edit'>,
  modelId: string,
): number {
  const image = snapshot.catalog.models.find((item) => item.model_id === modelId);
  if (image) return image.size_bytes;

  const multi = [
    ...snapshot.catalog.video_models,
    ...(snapshot.catalog.interpolation_models ?? []),
    ...(snapshot.catalog.audio_models ?? []),
    ...snapshot.edit.models,
  ].find((item) => item.model_id === modelId);
  return multi?.total_size_bytes ?? 0;
}
