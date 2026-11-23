import type { UiSettings } from './types';

export function defaultCustomModelName(path: string): string {
  const file = path.split(/[\\/]/).pop() || '';
  return (
    file
      .replace(/\.[^.]+$/, '')
      .replaceAll('_', ' ')
      .trim() || 'Checkpoint'
  );
}

export function customModels(settings: UiSettings): { path: string; name: string }[] {
  const models = (settings.custom_models ?? []).map((model) => ({
    ...model,
    name: model.name?.trim() || defaultCustomModelName(model.path),
  }));
  if (
    settings.custom_model_path &&
    !models.some((model) => model.path === settings.custom_model_path)
  ) {
    models.push({
      path: settings.custom_model_path,
      name: defaultCustomModelName(settings.custom_model_path),
    });
  }
  return models;
}
