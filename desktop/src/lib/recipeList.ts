import { fixStageKind, FIX_ROW_LABELS } from './fixStages';
import { displayName, fixesOf, type PlanStage } from './plan';
import { planLine } from './planLine';
import { isRestoreRecipe, recipeFixStages } from './recipes';
import type { AppSnapshot, FixKind, MediaKind, Recipe } from './types';
import { videoModelShortName } from './videoEngines';

export const MAX_RECIPE_PINS = 20;
export const MAX_RECIPE_RECENT = 12;

const MAX_MENU_RECIPES = 6;
const MAX_RECIPE_NAME_LENGTH = 60;

/** Minimal shape for built-in workflows until workflows.ts adds the full definition. */
export interface WorkflowEntry {
  id: string;
  name: string;
  media: 'image' | 'video';
  planLine?: string;
  modelIds?: string[];
}

export type MenuRow =
  | {
      kind: 'recipe';
      id: string;
      recipe: Recipe;
      pinned: boolean;
      disabled: boolean;
    }
  | {
      kind: 'workflow';
      id: string;
      workflow: WorkflowEntry;
      pinned: boolean;
      disabled: boolean;
    }
  | {
      kind: 'separator';
      id: string;
    };

export interface MenuRowsOptions {
  pins?: readonly string[];
  recent?: readonly string[];
  mediaKind?: MediaKind;
  workflows?: readonly WorkflowEntry[];
}

type CatalogSubset = Pick<AppSnapshot['catalog'], 'models'> &
  Partial<Pick<AppSnapshot['catalog'], 'video_models' | 'interpolation_models' | 'audio_models'>>;

function fitsMediaKind(isVideo: boolean, mediaKind?: MediaKind): boolean {
  if (!mediaKind || mediaKind === 'unknown') return true;
  return isVideo === (mediaKind === 'video');
}

function makePlanStage(
  kind: PlanStage['kind'],
  role: PlanStage['role'],
  label: string,
  fix?: Exclude<FixKind, 'faces'>,
): PlanStage {
  return {
    kind,
    role,
    label,
    installed: true,
    canDownload: false,
    needsFile: false,
    sizeBytes: 0,
    ...(fix ? { fix } : {}),
  };
}

/** Plan-line summary shown beneath a recipe name in the menu and library. */
export function recipePlanLine(
  recipe: Recipe,
  catalog: Pick<AppSnapshot['catalog'], 'models'>,
): string {
  const restoring = isRestoreRecipe(recipe);
  const isVideo = recipe.task === 'video';
  const temporalVideo =
    isVideo && Boolean(recipe.video_model_id) && recipe.video_model_id !== 'frame_by_frame';
  const fullCatalog: AppSnapshot['catalog'] = {
    schema_version: 2,
    catalog_revision: '',
    models: catalog.models,
    video_models: [],
    interpolation_models: [],
    audio_models: [],
  };

  const stages: PlanStage[] = [];
  if (!temporalVideo) {
    let fixStages = recipeFixStages(recipe, fullCatalog);
    if (fixStages.length === 0 && !recipe.stages?.length && recipe.fixes?.length) {
      fixStages = recipe.fixes
        .filter((fix): fix is Exclude<FixKind, 'faces'> => fix !== 'faces')
        .map((fix) => ({ fix, model_id: '' }));
    }

    if (restoring && fixStages.length === 0) {
      if (recipe.model_id === '__custom__') {
        stages.push(makePlanStage('restore', 'primary', 'Fix'));
      } else if (!recipe.stages && recipe.model_id) {
        const model = catalog.models.find((entry) => entry.model_id === recipe.model_id);
        if (model && model.native_scale === 1) {
          const fix = fixesOf(model).find(
            (candidate): candidate is Exclude<FixKind, 'faces'> => candidate !== 'faces',
          );
          if (fix) {
            stages.push(
              makePlanStage(fixStageKind(model, fix), 'primary', FIX_ROW_LABELS[fix], fix),
            );
          } else {
            stages.push(makePlanStage('restore', 'primary', 'Fix'));
          }
        }
      }
    } else {
      for (const selection of fixStages) {
        const model = catalog.models.find((entry) => entry.model_id === selection.model_id);
        stages.push(
          makePlanStage(
            fixStageKind(model, selection.fix),
            'fix',
            FIX_ROW_LABELS[selection.fix],
            selection.fix,
          ),
        );
      }
    }
  }

  if (!restoring) {
    const kind = isVideo ? 'video' : 'upscale';
    const label =
      isVideo && recipe.video_target_resolution ? 'Upscale' : `Upscale ×${recipe.output_scale}`;
    stages.push(makePlanStage(kind, 'primary', label));
  }

  if (
    !restoring &&
    (recipe.enable_face_model || recipe.stages?.some((stage) => stage.kind === 'face_restore'))
  ) {
    stages.push(makePlanStage('face_restore', 'face', 'Faces'));
  }

  if (isVideo) {
    if (!temporalVideo && recipe.deflicker) {
      stages.push(makePlanStage('deflicker', 'step', 'De-flicker'));
    }
    if (!temporalVideo && recipe.video_interpolation && recipe.video_interpolation !== 'off') {
      const interp = makePlanStage('interpolate', 'interpolate', 'Interpolate');
      if (recipe.video_interpolation_order === 'before_upscale') stages.unshift(interp);
      else stages.push(interp);
    }
    if (recipe.video_clean_audio) {
      stages.unshift(makePlanStage('audio_denoise', 'audio_denoise', 'Clean up audio'));
    }
  }

  const names = planLine(
    stages,
    {
      task: recipe.task,
      output_scale: recipe.output_scale,
      video_target_resolution: recipe.video_target_resolution,
      video_interpolation: recipe.video_interpolation || 'off',
    },
    isVideo ? { kind: 'video' } : { kind: 'image' },
  );
  return names.length > 0 ? names.join(' → ') : 'Same size';
}

/** Build the Recipes popover rows according to pins, recents, and media kind. */
export function menuRows(recipes: readonly Recipe[], options: MenuRowsOptions = {}): MenuRow[] {
  const { pins = [], recent = [], mediaKind, workflows = [] } = options;

  type Entry =
    | { kind: 'recipe'; id: string; recipe: Recipe; isVideo: boolean }
    | { kind: 'workflow'; id: string; workflow: WorkflowEntry; isVideo: boolean };

  const entries: Entry[] = recipes.map((recipe) => ({
    kind: 'recipe',
    id: recipe.id,
    recipe,
    isVideo: recipe.task === 'video',
  }));
  const seenEntryIds = new Set(entries.map((entry) => entry.id));
  const pinSetRaw = new Set(pins);

  for (const workflow of workflows) {
    if (pinSetRaw.has(workflow.id) && !seenEntryIds.has(workflow.id)) {
      seenEntryIds.add(workflow.id);
      entries.push({
        kind: 'workflow',
        id: workflow.id,
        workflow,
        isVideo: workflow.media === 'video',
      });
    }
  }

  if (entries.length === 0) {
    return workflows
      .filter((workflow) => fitsMediaKind(workflow.media === 'video', mediaKind))
      .map((workflow) => ({
        kind: 'workflow',
        id: workflow.id,
        workflow,
        pinned: false,
        disabled: false,
      }));
  }

  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  const pinnedIds: string[] = [];
  const pinnedSet = new Set<string>();
  for (const id of pins) {
    if (!pinnedSet.has(id) && byId.has(id)) {
      pinnedSet.add(id);
      pinnedIds.push(id);
    }
  }

  const toRow = (entry: Entry, pinned: boolean): MenuRow =>
    entry.kind === 'recipe'
      ? {
          kind: 'recipe',
          id: entry.id,
          recipe: entry.recipe,
          pinned,
          disabled: !fitsMediaKind(entry.isVideo, mediaKind),
        }
      : {
          kind: 'workflow',
          id: entry.id,
          workflow: entry.workflow,
          pinned,
          disabled: !fitsMediaKind(entry.isVideo, mediaKind),
        };

  if (entries.length <= MAX_MENU_RECIPES) {
    const pinnedRows = pinnedIds.map((id) => toRow(byId.get(id)!, true));
    const restRows = entries
      .filter((entry) => !pinnedSet.has(entry.id))
      .map((entry) => toRow(entry, false));
    return [...pinnedRows, ...restRows];
  }

  const pinnedRows = pinnedIds.slice(0, MAX_MENU_RECIPES).map((id) => toRow(byId.get(id)!, true));
  const remaining = MAX_MENU_RECIPES - pinnedRows.length;
  const recentRows: MenuRow[] = [];
  const seenRecent = new Set<string>();

  for (const id of recent) {
    if (recentRows.length >= remaining) break;
    if (seenRecent.has(id) || pinnedSet.has(id)) continue;
    seenRecent.add(id);
    const entry = byId.get(id);
    if (!entry || !fitsMediaKind(entry.isVideo, mediaKind)) continue;
    recentRows.push(toRow(entry, false));
  }

  if (pinnedRows.length > 0 && recentRows.length > 0) {
    return [...pinnedRows, { kind: 'separator', id: 'menu-separator' }, ...recentRows];
  }
  return [...pinnedRows, ...recentRows];
}

function catalogModelNames(catalog: CatalogSubset, modelIds: readonly string[]): string[] {
  const names: string[] = [];
  for (const id of modelIds) {
    if (!id || id === 'frame_by_frame' || id === '__custom__') continue;
    const image = catalog.models.find((entry) => entry.model_id === id);
    if (image) {
      names.push(displayName(image));
      continue;
    }
    const video = catalog.video_models?.find((entry) => entry.model_id === id);
    if (video) {
      names.push(videoModelShortName(video));
      continue;
    }
    const extra = [...(catalog.interpolation_models ?? []), ...(catalog.audio_models ?? [])].find(
      (entry) => entry.model_id === id,
    );
    if (extra) names.push(extra.name.split(' — ')[0]);
  }
  return names;
}

function isRecipeItem(item: Recipe | WorkflowEntry): item is Recipe {
  return 'task' in item;
}

/** Match recipes or workflows where every typed word appears in name, plan line, or models. */
export function searchRecipes<T extends Recipe | WorkflowEntry>(
  items: readonly T[],
  query: string,
  catalog: CatalogSubset,
): T[] {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...items];

  return items.filter((item) => {
    let text: string;
    if (isRecipeItem(item)) {
      const ids = new Set<string>();
      for (const stage of item.stages ?? []) {
        if (stage.model_id) ids.add(stage.model_id);
      }
      if (item.model_id) ids.add(item.model_id);
      if (item.video_model_id) ids.add(item.video_model_id);
      if (item.video_clean_audio && item.video_audio_model) ids.add(item.video_audio_model);
      text = [item.name, recipePlanLine(item, catalog), ...catalogModelNames(catalog, [...ids])]
        .join(' ')
        .toLowerCase();
    } else {
      text = [item.name, item.planLine ?? '', ...catalogModelNames(catalog, item.modelIds ?? [])]
        .join(' ')
        .toLowerCase();
    }
    return words.every((word) => text.includes(word));
  });
}

/** Order entries with pins in pin order, then recent in use order, then by name. */
export function sortRecipes<T extends { id: string; name: string }>(
  items: readonly T[],
  pins: readonly string[] = [],
  recent: readonly string[] = [],
): T[] {
  const pinIndex = new Map<string, number>();
  pins.forEach((id, index) => {
    if (!pinIndex.has(id)) pinIndex.set(id, index);
  });
  const recentIndex = new Map<string, number>();
  recent.forEach((id, index) => {
    if (!recentIndex.has(id)) recentIndex.set(id, index);
  });

  return [...items].sort((left, right) => {
    const leftPin = pinIndex.get(left.id);
    const rightPin = pinIndex.get(right.id);
    if (leftPin !== undefined || rightPin !== undefined) {
      if (leftPin === undefined) return 1;
      if (rightPin === undefined) return -1;
      return leftPin - rightPin;
    }

    const leftRecent = recentIndex.get(left.id);
    const rightRecent = recentIndex.get(right.id);
    if (leftRecent !== undefined || rightRecent !== undefined) {
      if (leftRecent === undefined) return 1;
      if (rightRecent === undefined) return -1;
      return leftRecent - rightRecent;
    }

    return (
      left.name.localeCompare(right.name, undefined, { sensitivity: 'base' }) ||
      left.name.localeCompare(right.name) ||
      left.id.localeCompare(right.id)
    );
  });
}

/** Generate "<name> copy", "<name> copy 2", ... capped to the host's 60-character limit. */
export function duplicateName(
  name: string,
  existing: readonly (string | { name: string })[],
): string {
  const used = new Set(
    existing.map((item) => (typeof item === 'string' ? item : item.name).trim().toLowerCase()),
  );
  const trimmed = name.trim();
  const base = trimmed.replace(/ copy(?: \d+)?$/i, '').trim() || trimmed || 'Recipe';
  for (let count = 1; ; count += 1) {
    const suffix = count === 1 ? ' copy' : ` copy ${count}`;
    const head = base.slice(0, Math.max(1, MAX_RECIPE_NAME_LENGTH - suffix.length)).trimEnd();
    const candidate = `${head}${suffix}`;
    if (!used.has(candidate.toLowerCase())) return candidate;
  }
}

/** Prepend an applied recipe or workflow id to the recent list, keeping at most 12 unique ids. */
export function recordUse(
  recent: readonly string[] | undefined,
  id: string,
  limit = MAX_RECIPE_RECENT,
): string[] {
  const unique = [...new Set(recent ?? [])];
  if (!id) return unique.slice(0, limit);
  return [id, ...unique.filter((entry) => entry !== id)].slice(0, limit);
}

/** Add or remove an id in pin order, keeping at most 20 unique pins. */
export function togglePin(
  pins: readonly string[] | undefined,
  id: string,
  limit = MAX_RECIPE_PINS,
): string[] {
  const unique = [...new Set(pins ?? [])];
  if (unique.includes(id)) return unique.filter((entry) => entry !== id);
  if (!id || unique.length >= limit) return unique.slice(0, limit);
  return [...unique, id];
}
