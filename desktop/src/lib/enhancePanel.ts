import type { PlanState } from './jobPlanClient';
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
  VideoMemoryStatus,
} from './types';
import type { VideoUnavailable } from './videoEngines';

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
