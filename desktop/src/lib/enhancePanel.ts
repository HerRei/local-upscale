import type { PlanState } from './jobPlanClient';
import { imageQualityName } from './outputFormats';
import type { Fit, PlanStage } from './plan';
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

/** One-line summary for the Output row in the Enhance panel foot. */
export function outputSummary(
  settings: UiSettings,
  options: { preservingHdr?: boolean; audioDisabled?: boolean } = {},
): string {
  const folder = outputFolderName(settings.output_directory);
  if (settings.task === 'video') {
    const container = (settings.video_container ?? 'mp4').toUpperCase();
    const codec = settings.video_codec ?? 'auto';
    const codecName = VIDEO_CODEC_NAMES[codec] ?? codec.toUpperCase();
    const crf = codec === 'ffv1' || codec === 'prores' ? '' : `CRF ${settings.video_crf}`;
    const hdr = options.preservingHdr ? 'HDR' : '';
    const audio = !options.audioDisabled && settings.video_clean_audio ? 'sound cleaned' : '';
    return [container, codecName, hdr, crf, audio, folder].filter(Boolean).join(' · ');
  }
  const format = PHOTO_FORMAT_NAMES[settings.output_format] ?? settings.output_format.toUpperCase();
  const formatPart = imageQualityName(settings.output_format)
    ? `${format} ${settings.jpeg_quality}`
    : format;
  return `${formatPart} · ${folder}`;
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
  downloads: { stages: PlanStage[]; bytes: number };
  flashVideo: boolean;
  videoMemoryEngine: string;
  selectedMemory: VideoMemoryStatus | undefined;
  outputDimensions: string;
  lastFailure: string;
  acceleratorFailure: boolean;
  interpolationSupported: boolean;
}

export interface EnhancePanelActions {
  setTask: (
    task: TaskKind,
    nextVideoTask?: 'upscale' | 'denoise',
    enlarge?: { scale: number; modelId: string },
  ) => void;
  updateSettings: (patch: Partial<UiSettings>) => void;
  openRecipeEditor: () => Promise<void>;
  applyPreset: (kind: 'quick' | 'best') => void;
  deleteRecipe: (recipeId: string) => void;
  applyRecipe: (recipe: Recipe) => void;
  addRecipe: () => Promise<void>;
  setContent: (content: UiSettings['content']) => void;
  radioGroupKeydown: (event: KeyboardEvent) => void;
  toggleFix: (fix: FixKind) => void;
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
