import { formatDuration } from './format';
import type { PlanState } from './jobPlanClient';
import { imageQualityName } from './outputFormats';
import {
  ALPHA_FORMAT_NOTE,
  activeStepStages,
  outputFormatKeepingAlpha,
  type Fit,
  type PendingDownloads,
  type PlanStage,
} from './plan';
import type { StepDefinition } from './steps';
import type { Workflow } from './workflows';
import type {
  AppSnapshot,
  CatalogModel,
  CatalogVideoModel,
  DeviceInfo,
  EditModel,
  FixKind,
  FixStageSelection,
  MediaItem,
  ModelDownloadStatus,
  Quality,
  Recipe,
  SettingsSection,
  TaskKind,
  UiSettings,
  VideoCodec,
  VideoMemoryStatus,
} from './types';
import type { VideoUnavailable } from './videoEngines';

const PHOTO_FORMAT_NAMES: Record<UiSettings['output_format'], string> = {
  png: 'PNG',
  jpg: 'JPEG',
  tif: 'TIFF',
  webp: 'WebP',
  heic: 'HEIC',
};

const VIDEO_CODEC_NAMES: Record<VideoCodec, string> = {
  auto: 'Automatic',
  av1: 'AV1',
  vp9: 'VP9',
  ffv1: 'FFV1',
  h264: 'H.264',
  hevc: 'HEVC',
  prores: 'ProRes',
};

function outputFolderName(directory: string): string {
  const trimmed = directory.trim().replace(/[\\/]+$/, '');
  if (!trimmed) return directory.trim() ? '/' : 'choose a folder';
  const parts = trimmed.split(/[\\/]/);
  return parts[parts.length - 1] || trimmed;
}

const PHOTO_EXTENSION_NAMES: Record<string, string> = {
  jpg: 'JPEG',
  jpeg: 'JPEG',
  png: 'PNG',
  tif: 'TIFF',
  tiff: 'TIFF',
  webp: 'WebP',
  heic: 'HEIC',
};

function photoTypeFromName(name: string): string {
  const match = /\.([^.]+)$/.exec(name.trim());
  if (!match) return '';
  const ext = match[1].toLowerCase();
  return PHOTO_EXTENSION_NAMES[ext] ?? ext.toUpperCase();
}

/** One-line facts for the Your photo / Your video section head. */
export function mediaFacts(media: MediaItem | undefined): string {
  if (!media || media.probe_status !== 'ready' || media.width <= 0 || media.height <= 0) {
    return '';
  }
  const size = `${media.width} × ${media.height}`;
  if (media.kind === 'video') {
    const fps = media.fps > 0 ? `${Number(media.fps.toFixed(3))} fps` : '';
    const duration = media.duration_seconds > 0 ? formatDuration(media.duration_seconds) : '';
    const hdr = media.hdr_format || '';
    return [size, fps, duration, hdr].filter(Boolean).join(' · ');
  }
  return [size, photoTypeFromName(media.name)].filter(Boolean).join(' · ');
}

/** One-line summary for the Output row in the Enhance panel foot. */
export function outputSummary(
  settings: UiSettings,
  options: {
    preservingHdr?: boolean;
    audioDisabled?: boolean;
    /** Slow motion applies: the file has no sound, whatever Clean up audio says. */
    slowMotion?: boolean;
    includeFolder?: boolean;
    /** The step registry; tests add steps that exist only in them. */
    steps?: readonly StepDefinition[];
  } = {},
): string {
  const folder = options.includeFolder === false ? '' : outputFolderName(settings.output_directory);
  if (settings.task === 'video') {
    const container = (settings.video_container ?? 'mp4').toUpperCase();
    const codec = settings.video_codec ?? 'auto';
    const codecName = VIDEO_CODEC_NAMES[codec] ?? codec.toUpperCase();
    const crf = codec === 'ffv1' || codec === 'prores' ? '' : `CRF ${settings.video_crf}`;
    const hdr = options.preservingHdr ? 'HDR' : '';
    const audio = options.audioDisabled
      ? ''
      : options.slowMotion
        ? 'no sound'
        : settings.video_clean_audio
          ? 'sound cleaned'
          : '';
    return [container, codecName, hdr, crf, audio, folder].filter(Boolean).join(' · ');
  }
  // A step that sets the alpha saves in a format that keeps it, as the start input does.
  const saved = savedPhotoFormat(settings, options.steps);
  const format = PHOTO_FORMAT_NAMES[saved] ?? saved.toUpperCase();
  const formatPart = imageQualityName(saved) ? `${format} ${settings.jpeg_quality}` : format;
  const transparent = setsAlpha(settings, options.steps) ? 'transparent' : '';
  return [formatPart, transparent, folder].filter(Boolean).join(' · ');
}

/** Whether a stored step sets the alpha channel of the photo it runs on. */
export function setsAlpha(
  settings: Pick<UiSettings, 'task' | 'step_stages'>,
  steps?: readonly StepDefinition[],
): boolean {
  return activeStepStages(settings, false, steps).some(({ step }) => step.output_alpha === true);
}

/** The format a photo job saves in: the chosen one, or PNG when a step needs transparency. */
export function savedPhotoFormat(
  settings: Pick<UiSettings, 'task' | 'step_stages' | 'output_format'>,
  steps?: readonly StepDefinition[],
): UiSettings['output_format'] {
  return outputFormatKeepingAlpha(
    activeStepStages(settings, false, steps).map(({ selection }) => selection),
    settings.output_format,
    steps,
  );
}

/** The Output line, plus why the format differs when a transparent result replaced it. */
export function outputTitle(
  settings: UiSettings,
  options: Parameters<typeof outputSummary>[1] = {},
): string {
  const summary = outputSummary(settings, options);
  if (settings.task === 'video') return summary;
  return savedPhotoFormat(settings, options.steps) === settings.output_format
    ? summary
    : `${summary} · ${ALPHA_FORMAT_NOTE}`;
}

export interface EnhancePanelView {
  page: 'media' | 'preview' | 'enhance';
  snapshot: AppSnapshot;
  settings: UiSettings;
  defaultSettings: UiSettings;
  selectedMedia: MediaItem | undefined;
  settingsLocked: boolean;
  movingModels: boolean;
  lockReason: string;
  activeQuality: Quality;
  restoring: boolean;
  usingTemporalVideo: boolean;
  videoTask: 'upscale' | 'denoise';
  selectedModel: CatalogModel | undefined;
  selectedVideoModel: CatalogVideoModel | undefined;
  editModel: EditModel | undefined;
  editFit: Fit;
  editRuntime: string;
  editingDevice: DeviceInfo | undefined;
  editSupported: boolean;
  editBlockReason: string;
  preservingHdr: boolean;
  hdrPreservationAvailable: boolean;
  hdrModelCompatible: boolean;
  audioDisabled: boolean;
  audioModels: CatalogVideoModel[];
  selectedAudioModel: CatalogVideoModel | undefined;
  quickDownload: string;
  bestDownload: string;
  activeRecipeIds: Set<string>;
  updatableRecipe: Recipe | undefined;
  sizeResult: string;
  sizeMaxScale: number;
  bestModel: CatalogModel | undefined;
  imageFixes: FixStageSelection[];
  noRestoreModel: boolean;
  planState: PlanState;
  plan: PlanStage[];
  videoAvailable: Record<string, VideoUnavailable | undefined>;
  activeDevice: DeviceInfo | undefined;
  downloadMap: Map<string, ModelDownloadStatus>;
  canOpenTrials: boolean;
  canTryOthers: boolean;
  customModelNeedsOptIn: boolean;
  licenseReviewModel: CatalogModel | undefined;
  preparing: boolean;
  hasNothingToRun: boolean;
  nothingToRunReason: string;
  temporalUnavailableReason: string;
  fixPlanSupported: boolean;
  downloads: PendingDownloads;
  flashVideo: boolean;
  videoMemoryEngine: string;
  selectedMemory: VideoMemoryStatus | undefined;
  outputDimensions: string;
  lastFailure: string;
  acceleratorFailure: boolean;
  interpolationSupported: boolean;
  /** What the fix popover offers for the preview patch; empty when there is none. */
  patchLink: 'photo' | 'example' | '';
  /** Measured run time of what Start would process; null until there is an honest number. */
  startSeconds: number | null;
}

export interface EnhancePanelActions {
  setTask: (
    task: TaskKind,
    nextVideoTask?: 'upscale' | 'denoise',
    enlarge?: { scale: number; modelId: string },
  ) => void;
  updateSettings: (patch: Partial<UiSettings>) => void;
  openRecipeEditor: () => Promise<void>;
  openRecipesLibrary: () => void;
  applyPreset: (kind: 'quick' | 'best') => void;
  applyRecipe: (recipe: Recipe) => void;
  addRecipe: () => Promise<void>;
  updateRecipe: () => Promise<void>;
  applyWorkflow: (workflow: Workflow) => void;
  setContent: (content: UiSettings['content']) => void;
  radioGroupKeydown: (event: KeyboardEvent) => void;
  toggleFix: (fix: FixKind) => void;
  /** Turns the preview patch on with this fix's chip pressed. */
  seeFixOnPhoto: (fix: FixKind) => void;
  /** The viewer dims this fraction of each edge while Stabilise's popover is open; null clears it. */
  showCropGuide: (fraction: number | null) => void;
  setSize: (scale: 1 | 2 | 3 | 4) => void;
  openLibrary: (
    slot: 'primary' | 'fix' | 'face' | 'browse' | 'edit' | 'try',
    fix?: Exclude<FixKind, 'faces'>,
    select?: string,
  ) => void;
  moveFix: (fix: Exclude<FixKind, 'faces'>, offset: -1 | 1) => Promise<void>;
  chooseCustomModel: () => Promise<void>;
  changeCheckpointConsent: (allow: boolean) => Promise<void>;
  openAnyLicense: (modelId: string) => Promise<void>;
  openAnySource: (modelId: string) => Promise<void>;
  openCustomEdit: () => void;
  runDownload: (
    target?: CatalogModel | CatalogVideoModel | EditModel,
    accepted?: boolean,
  ) => Promise<void>;
  retryPlan: () => void;
  chooseEditRuntime: () => Promise<void>;
  retryOnCpu: () => Promise<void>;
  chooseModelFolder: () => Promise<void>;
  resetModelFolder: () => Promise<void>;
  chooseInAdvanced: (patch: Partial<UiSettings>) => void;
  useDefaultsForSession: () => void;
  chooseOutput: () => Promise<void>;
  openSettings: (section?: SettingsSection, focusTarget?: 'hardware' | 'ffmpeg' | null) => void;
  refreshCapabilities: () => Promise<boolean>;
}
