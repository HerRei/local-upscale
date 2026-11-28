import rawWorkflows from '../../src-tauri/resources/workflows.json';
import { formatBytes } from './format';
import {
  choosePresetModel,
  displayName,
  pendingDownloads,
  resolvePlan,
  type PlanStage,
} from './plan';
import { chooseFixModel, fixStageKind } from './fixStages';
import { planLine } from './planLine';
import { sameFixStages } from './recipes';
import type {
  AppSnapshot,
  FixKind,
  FixStageSelection,
  Recipe,
  TaskKind,
  UiSettings,
} from './types';

export type WorkflowFix = Exclude<FixKind, 'faces'>;

export interface Workflow {
  id: string;
  name: string;
  media: 'image' | 'video';
  for: string;
  note: string;
  /** One more grey line under the note, for what the workflow leaves out. */
  detail_extra?: string;
  content: 'photo' | 'illustration';
  quality: 'quick' | 'best';
  fixes: WorkflowFix[];
  output_scale: 1 | 2 | 3 | 4;
  example: string;
}

export const WORKFLOWS: readonly Workflow[] = rawWorkflows.workflows as Workflow[];

/**
 * The settings a user would reach by hand: Content, the ticks in order, Size and Quality,
 * with the models Quality picks and the user's remembered ones included. Output, hardware,
 * tile size and precision stay as they are; the overlap follows the upscaler, as it does
 * when Quality picks it.
 */
export function workflowSettings(
  workflow: Workflow,
  snapshot: AppSnapshot,
  settings: UiSettings,
): Partial<UiSettings> {
  const { quality, content } = workflow;
  const fix_stages: FixStageSelection[] = workflow.fixes.map((fix) => ({
    fix,
    model_id: chooseFixModel(snapshot, fix, quality, settings.preset_pins)?.model_id ?? '',
  }));
  const patch: Partial<UiSettings> = {
    quality,
    content,
    fix_stages,
    fixes: workflow.fixes,
    enable_face_model: false,
  };
  if (workflow.output_scale === 1) {
    // Same size is Restore, and its model is the last fix (`setSize` makes the same task).
    const final = fix_stages.at(-1);
    const model = snapshot.catalog.models.find((entry) => entry.model_id === final?.model_id);
    return {
      ...patch,
      task: 'denoise',
      selected_model_id: final?.model_id ?? '',
      output_scale: 1,
      selected_video_model_id: 'frame_by_frame',
      ...(model ? { halo: model.recommended_halo } : {}),
    };
  }
  const primary = choosePresetModel(snapshot, 'upscale', quality, content, settings.preset_pins);
  return {
    ...patch,
    task: 'upscale',
    selected_model_id: primary?.model_id ?? '',
    output_scale: Math.min(primary?.native_scale ?? workflow.output_scale, workflow.output_scale),
    selected_video_model_id: 'frame_by_frame',
    ...(primary ? { halo: primary.recommended_halo } : {}),
  };
}

/** True while the current settings equal what the workflow applies: it shows the check mark. */
export function workflowMatches(
  workflow: Workflow,
  snapshot: AppSnapshot,
  settings: UiSettings,
): boolean {
  const patch = workflowSettings(workflow, snapshot, settings);
  return (
    settings.task === patch.task &&
    settings.content === patch.content &&
    (settings.quality || 'custom') === patch.quality &&
    settings.selected_model_id === patch.selected_model_id &&
    settings.output_scale === patch.output_scale &&
    !settings.enable_face_model &&
    sameFixStages(settings.fix_stages, patch.fix_stages ?? [])
  );
}

function workflowStages(
  workflow: Workflow,
  snapshot: AppSnapshot,
  settings: UiSettings,
): { stages: PlanStage[]; ui: UiSettings } {
  const ui = { ...settings, ...workflowSettings(workflow, snapshot, settings) } as UiSettings;
  const stages = resolvePlan(snapshot, ui, { faceEnabled: false, usingTemporalVideo: false });
  return { stages, ui };
}

/** The plan as the card shows it: one entry per step, with the model it runs. */
export function workflowPlan(
  workflow: Workflow,
  snapshot: AppSnapshot,
  settings: UiSettings,
): { step: string; model: string }[] {
  const { stages, ui } = workflowStages(workflow, snapshot, settings);
  const steps = planLine(stages, ui);
  return stages.map((stage, index) => ({
    step: steps[index],
    model: stage.model ? displayName(stage.model) : '',
  }));
}

/** What is still to download for the workflow's models, as `formatBytes` prints it. */
export function workflowDownload(
  workflow: Workflow,
  snapshot: AppSnapshot,
  settings: UiSettings,
): { bytes: number; text: string } {
  const { stages } = workflowStages(workflow, snapshot, settings);
  const { bytes } = pendingDownloads(stages);
  return { bytes, text: bytes > 0 ? formatBytes(bytes) : '' };
}

/**
 * The workflow's settings merged over the current ones, as a saved recipe. It applies nothing:
 * the panel stays as it is. Output, hardware and the run's tile and precision come from the
 * current settings, as they do for `Save current as recipe…`.
 */
export function workflowRecipe(
  workflow: Workflow,
  snapshot: AppSnapshot,
  settings: UiSettings,
  id: string,
  name: string,
): Recipe {
  const merged = { ...settings, ...workflowSettings(workflow, snapshot, settings) } as UiSettings;
  const restoring = workflow.output_scale === 1;
  const fixes = merged.fix_stages;
  const prefix = (restoring ? fixes.slice(0, -1) : fixes).filter((selection) => selection.model_id);
  const modelOf = (modelId: string) =>
    snapshot.catalog.models.find((model) => model.model_id === modelId);
  return {
    id,
    name,
    task: merged.task as TaskKind,
    model_id: merged.selected_model_id,
    output_scale: restoring ? 1 : merged.output_scale,
    tile_size: merged.tile_size,
    halo: merged.halo,
    precision: merged.precision,
    safe_memory: merged.safe_memory,
    video_model_id: merged.selected_video_model_id,
    custom_model_path: '',
    output_format: merged.output_format,
    output_bit_depth: merged.output_bit_depth ?? 8,
    preserve_metadata: merged.preserve_metadata,
    jpeg_quality: merged.jpeg_quality,
    enable_face_model: false,
    face_fidelity: merged.face_fidelity,
    quality: workflow.quality,
    content: workflow.content,
    fixes: [...workflow.fixes],
    stages: [
      ...prefix.map((selection) => ({
        kind: fixStageKind(modelOf(selection.model_id), selection.fix),
        model_id: selection.model_id,
        fix: selection.fix,
      })),
      {
        kind: restoring ? ('restore' as const) : ('upscale' as const),
        model_id: merged.selected_model_id,
        ...(restoring && fixes.length ? { fix: fixes[fixes.length - 1].fix } : {}),
      },
    ],
  };
}
