import manifest from '../assets/examples/examples.json';
import { displayName, fixesOf } from './plan';
import { FIX_LABELS } from './steps';
import type { CatalogModel, FixKind } from './types';

export interface ModelExampleEntry {
  id: string;
  kind: 'fix' | 'upscale';
  fix?: Exclude<FixKind, 'faces'>;
  model_id: string;
  before: string;
  after: string;
  alt: string;
  source: { title: string; author: string; url: string; licence: string };
  made: { app: string; settings: string };
}

export const EXAMPLES = manifest as ModelExampleEntry[];

const urls = import.meta.glob('../assets/examples/*.webp', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>;

/** The bundled URL of one example file; empty when the file is not there. */
export function exampleUrl(file: string): string {
  return urls[`../assets/examples/${file}`] ?? '';
}

export function exampleForModel(modelId: string): ModelExampleEntry | undefined {
  return EXAMPLES.find((example) => example.model_id === modelId);
}

export function exampleForFix(fix: FixKind): ModelExampleEntry | undefined {
  return EXAMPLES.find((example) => example.kind === 'fix' && example.fix === fix);
}

export interface ExampleChoice {
  example: ModelExampleEntry;
  /** True when the picture was made with another model of the same fix. */
  borrowed: boolean;
}

/**
 * A model's own example; a fix model without one shows its fix's example; an upscaler
 * without one shows none.
 */
export function exampleChoice(model: CatalogModel): ExampleChoice | undefined {
  const own = exampleForModel(model.model_id);
  if (own) return { example: own, borrowed: false };
  for (const fix of fixesOf(model)) {
    const shared = fix === 'faces' ? undefined : exampleForFix(fix);
    if (shared) return { example: shared, borrowed: true };
  }
  return undefined;
}

/** `Example · Noise`, `Example · 4×`, or the borrowed form naming the model that made it. */
export function exampleCaption(choice: ExampleChoice, models: CatalogModel[]): string {
  const { example, borrowed } = choice;
  if (example.kind === 'upscale') {
    const scale = models.find((model) => model.model_id === example.model_id)?.native_scale ?? 4;
    return `Example · ${scale}\u00d7`;
  }
  const label = FIX_LABELS[example.fix as FixKind];
  if (!borrowed) return `Example \u00b7 ${label}`;
  const maker = models.find((model) => model.model_id === example.model_id);
  return `Example of the ${label} fix, made with ${maker ? displayName(maker) : example.model_id}`;
}
