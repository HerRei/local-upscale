<script lang="ts">
  import { onMount } from 'svelte';
  import { customModels } from './lib/customModels';
  import { downloadFailureText, progressBytes, type DownloadFailure } from './lib/downloadProgress';
  import { keptTrialSettings, trialIncompatibility, trialTime } from './lib/modelTrials';
  import { modelDisplayName } from './lib/modelNames';
  import {
    FIX_LABELS,
    choosePresetModel,
    displayName,
    fitFor,
    fixesOf,
    libraryModelsForTask,
    modelsForTask,
    rightsAllowPreset,
  } from './lib/plan';
  import { chooseFixModel, fixQuality, rememberedFixModel } from './lib/fixStages';
  import { formatBytes } from './lib/format';
  import FileName from './FileName.svelte';
  import HelpTip from './HelpTip.svelte';
  import { modal } from './lib/modal';
  import { fitLine, runsOnAccelerator } from './lib/neuralEngine';
  import {
    FRAME_BY_FRAME_TIP,
    SEVERAL_FRAMES_TIP,
    videoAvailability,
    videoEngineName,
    videoModelSummary,
    videoModelsForJob,
  } from './lib/videoEngines';
  import { estimateModelTrials, openCustomModelTutorial } from './lib/api';
  import {
    EDIT_FAMILIES,
    EDIT_FIT_LABELS,
    editDevice,
    editExpert,
    editFamilyLabel,
    editFamilySummary,
    editFitFor,
    editHardwareLabel,
    editRecommendedSize,
    editRuntimeLabel,
    editVariantName,
  } from './lib/editing';
  import type {
    AppSnapshot,
    CatalogModel,
    CatalogVideoModel,
    ContentKind,
    DeviceInfo,
    EditModel,
    FixKind,
    ModelDownloadStatus,
    Quality,
    StartBatchInput,
    TaskKind,
    TrialEstimate,
    UiSettings,
  } from './lib/types';

  export let snapshot: AppSnapshot;
  export let settings: UiSettings;
  export let context: {
    task: TaskKind | '';
    videoTask?: 'upscale' | 'denoise';
    content: Exclude<ContentKind, 'face'>;
    quality: Quality | '';
    slot: 'primary' | 'fix' | 'face' | 'browse' | 'edit' | 'try';
    fix?: Exclude<FixKind, 'faces'>;
    /** A model to open on instead of the one the job uses now. */
    select?: string;
  };
  export let device: DeviceInfo | undefined;
  export let downloads: ModelDownloadStatus[] = [];
  export let downloadErrors: Record<string, string> = {};
  export let downloadFailures: Record<string, DownloadFailure> = {};
  export let downloadDetails: Record<string, string> = {};
  export let cancelDownload: (modelId: string) => Promise<void> = async () => {};
  export let retryDownload: (modelId: string) => Promise<void> = async () => {};
  export let dismissDownloadError: (modelId: string) => void = () => {};
  export let busy = false;
  export let close: () => void;
  export let useForJob: (model: CatalogModel) => void;
  export let useVideoForJob: (model: CatalogVideoModel) => void = () => {};
  /** Remember a model for one problem (a fix chip, or Same size with a chip on). */
  export let rememberFix: (model: CatalogModel, fix: Exclude<FixKind, 'faces'>) => void;
  export let forgetFix: (fix: Exclude<FixKind, 'faces'>) => void;
  export let useEditModel: (model: EditModel) => void;
  export let pin: (model: CatalogModel, quality: 'quick' | 'best') => void;
  // "Try others…": the image, the settings frozen when the sheet opened, and
  // the action that queues one run per ticked model.
  export let trialMedia: { id: string; name: string } | undefined = undefined;
  export let trialBase: StartBatchInput | undefined = undefined;
  export let runTrials: (modelIds: string[]) => Promise<void> = async () => {};
  /** Trials queue behind other work, so only starting one blocks the sheet. */
  export let trialBusy = false;
  export let download: (
    model: CatalogModel | CatalogVideoModel | EditModel,
    accepted?: boolean,
  ) => Promise<void>;
  export let importFile: (model: CatalogModel, accepted: boolean) => Promise<void>;
  export let remove: (model: CatalogModel | CatalogVideoModel | EditModel) => Promise<void>;
  export let chooseCustom: () => Promise<void>;
  export let selectCustomModel: (path: string) => void = () => {};
  export let renameCustomModel: (path: string, name: string) => void = () => {};
  let renamingPath = '';
  let customName = '';
  export let openCustomEdit: () => void = () => {};
  export let openLicense: (modelId: string) => Promise<void>;
  export let openSource: (modelId: string) => Promise<void>;
  export let modelDirectoryMissing = false;
  export let chooseModelFolder: () => Promise<void> = async () => undefined;
  export let resetModelFolder: () => Promise<void> = async () => undefined;

  type GroupId =
    'job' | 'photos' | 'illustration' | 'fix' | 'video' | 'audio' | 'edit' | 'installed' | 'own';

  let group: GroupId =
    context.slot === 'browse' ? 'photos' : context.slot === 'edit' ? 'edit' : 'job';
  let tutorialNote = '';

  async function openTutorial(): Promise<void> {
    tutorialNote = '';
    try {
      await openCustomModelTutorial();
    } catch (error) {
      tutorialNote = String(error);
    }
  }
  // Choosing the model for one problem: a fix chip, or Same size with a chip on.
  $: restoring =
    context.task === 'denoise' || (context.task === 'video' && context.videoTask === 'denoise');
  $: fixing =
    context.fix !== undefined &&
    (context.slot === 'fix' || (context.slot === 'primary' && restoring))
      ? context.fix
      : undefined;
  // A fix or temporal video picker opens on the model the job uses now.
  let selectedId = context.select
    ? context.select
    : context.slot === 'primary' &&
        context.task === 'video' &&
        context.videoTask !== 'denoise' &&
        settings.selected_video_model_id !== 'frame_by_frame'
      ? settings.selected_video_model_id
      : context.fix === undefined
        ? ''
        : context.slot === 'fix'
          ? (settings.fix_stages.find((selection) => selection.fix === context.fix)?.model_id ?? '')
          : context.slot === 'primary' &&
              (context.task === 'denoise' ||
                (context.task === 'video' && context.videoTask === 'denoise'))
            ? settings.selected_model_id
            : '';
  let listEl: HTMLElement | undefined;
  onMount(() => {
    listEl?.querySelector('.row.selected')?.scrollIntoView?.({ block: 'center' });
  });
  let termsAccepted = false;
  let showHeavyEdit = false;
  let selectedOwnPath = '';
  let trialIds: string[] = [];
  let trialEstimates: TrialEstimate[] = [];
  let estimateKey = '';
  let estimateError = '';
  let runningTrials = false;

  $: models = snapshot.catalog.models;
  $: videoModels = snapshot.catalog.video_models;
  $: videoUnavailable = videoAvailability(
    videoModels,
    snapshot.engine,
    snapshot.capabilities,
    settings.device_id,
  );
  $: jobVideoModels =
    context.slot === 'primary'
      ? videoModelsForJob(snapshot.catalog, context.task, context.videoTask)
      : [];
  $: audioModels = snapshot.catalog.audio_models ?? [];
  $: editModels = snapshot.edit.models;
  $: customEdit = snapshot.edit.custom_model ?? undefined;
  $: interpolationModels = snapshot.catalog.interpolation_models ?? [];
  $: downloadMap = new Map(downloads.map((item) => [item.model_id, item]));
  $: failedEntries = Object.entries(downloadErrors).filter(
    ([id, message]) => Boolean(message) && !downloadMap.has(id),
  );
  $: runningDownloadCount = downloads.filter((item) => item.status === 'downloading').length;
  $: queuedDownloadCount = downloads.filter((item) => item.status === 'queued').length;
  $: editGpu = editDevice(snapshot.capabilities, settings);
  $: editLimit = editRecommendedSize(
    selectedEdit,
    snapshot.capabilities,
    editGpu,
    editExpert(settings, selectedEdit, editGpu, snapshot.engine?.features ?? []),
  );
  $: editFit = (model: EditModel) => editFitFor(model, snapshot.capabilities, editGpu);
  $: installedVideoModels = videoModels.filter((model) => model.installed);
  $: installedInterpolationModels = interpolationModels.filter((model) => model.installed);
  $: installedAudioModels = audioModels.filter((model) => model.installed);
  $: installedEditModels = editModels.filter((model) => model.installed);
  $: ownModels = customModels(settings);
  // Variants that cannot run here stay out of the way until asked for.
  $: onMac = editGpu?.id === 'mps';
  $: editFamilies = EDIT_FAMILIES.map((family) => {
    const members = editModels.filter((model) => model.family === family.id);
    return {
      ...family,
      fitting: members.filter((model) => editFit(model) !== 'too_heavy'),
      heavy: members.filter((model) => editFit(model) === 'too_heavy'),
    };
  }).sort((left, right) => Number(right.fitting.length > 0) - Number(left.fitting.length > 0));
  $: heavyEditCount = editFamilies.reduce((total, family) => total + family.heavy.length, 0);
  $: isUpscaler = (model: CatalogModel): boolean =>
    model.native_scale > 1 && !model.purposes.includes('face');
  $: jobModels = ((): CatalogModel[] => {
    if (!context.task || context.slot === 'browse') return [];
    if (context.slot === 'face') return models.filter((model) => model.purposes.includes('face'));
    if (context.slot === 'fix') {
      const fix = context.fix;
      return libraryModelsForTask(models, 'denoise', context.content, fix);
    }
    return libraryModelsForTask(
      models,
      context.task,
      context.content,
      context.fix,
      context.videoTask,
    );
  })();
  // Installed models for this job first; the rest say why they cannot join.
  $: trialModels =
    context.slot === 'try'
      ? [
          ...jobModels,
          ...modelsForTask(snapshot, context.task, context.videoTask).filter(
            (model) =>
              model.installed &&
              !jobModels.includes(model) &&
              (!restoring || !context.fix || fixesOf(model).includes(context.fix)),
          ),
        ].sort((left, right) => Number(right.installed) - Number(left.installed))
      : [];
  $: trialReason = (model: CatalogModel): string => trialIncompatibility(model, settings, models);
  $: if (context.slot === 'try' && trialIds.join('|') !== estimateKey)
    void estimateTrials(trialIds.join('|'));

  async function estimateTrials(key: string): Promise<void> {
    estimateKey = key;
    estimateError = '';
    trialEstimates = [];
    if (!trialIds.length || !trialBase || !trialMedia) return;
    const ids = [...trialIds];
    try {
      const estimates = await estimateModelTrials(trialMedia.id, ids, trialBase);
      if (estimateKey === key) trialEstimates = estimates;
    } catch (error) {
      if (estimateKey === key) estimateError = String(error);
    }
  }

  function toggleTrial(id: string): void {
    trialIds = trialIds.includes(id) ? trialIds.filter((value) => value !== id) : [...trialIds, id];
    selectedId = id;
  }

  async function startTrials(): Promise<void> {
    runningTrials = true;
    try {
      await runTrials([...trialIds]);
    } finally {
      runningTrials = false;
    }
  }

  $: groups = [
    ...(context.slot !== 'browse' &&
    context.slot !== 'edit' &&
    context.task &&
    context.task !== 'edit'
      ? [
          {
            id: 'job' as GroupId,
            label: 'For this job',
            count: jobModels.length + jobVideoModels.length,
          },
        ]
      : []),
    {
      id: 'photos' as GroupId,
      label: 'Upscale photos',
      count: models.filter(
        (model) =>
          isUpscaler(model) &&
          (model.purposes.includes('photo') || model.purposes.includes('general')),
      ).length,
    },
    {
      id: 'illustration' as GroupId,
      label: 'Illustration',
      count: models.filter(
        (model) =>
          isUpscaler(model) &&
          (model.purposes.includes('illustration') || model.purposes.includes('anime')),
      ).length,
    },
    {
      id: 'fix' as GroupId,
      label: 'Fix noise, blur, JPEG, colour',
      count: models.filter((model) => model.native_scale === 1 && !model.purposes.includes('face'))
        .length,
    },
    {
      id: 'video' as GroupId,
      label: 'Video (Labs)',
      count: videoModels.length + interpolationModels.length,
    },
    ...(audioModels.length
      ? [{ id: 'audio' as GroupId, label: 'Audio', count: audioModels.length }]
      : []),
    { id: 'edit' as GroupId, label: 'Edit with a prompt', count: editModels.length },
    {
      id: 'installed' as GroupId,
      label: 'Installed',
      count:
        models.filter((model) => model.installed).length +
        installedVideoModels.length +
        installedInterpolationModels.length +
        installedAudioModels.length +
        installedEditModels.length,
    },
    // A fix runs a catalog model only; a file of your own replaces the upscaler.
    ...(context.slot === 'fix' ? [] : [{ id: 'own' as GroupId, label: 'Your own file', count: 0 }]),
  ];
  $: rows = ((): CatalogModel[] => {
    switch (group) {
      case 'job':
        return jobModels;
      case 'photos':
        return models
          .filter(
            (model) =>
              isUpscaler(model) &&
              (model.purposes.includes('photo') || model.purposes.includes('general')),
          )
          .sort((left, right) => right.quality_tier - left.quality_tier);
      case 'illustration':
        return models.filter(
          (model) =>
            isUpscaler(model) &&
            (model.purposes.includes('illustration') || model.purposes.includes('anime')),
        );
      case 'fix':
        return models.filter(
          (model) => model.native_scale === 1 && !model.purposes.includes('face'),
        );
      case 'installed':
        return models.filter((model) => model.installed);
      default:
        return [];
    }
  })();
  $: installedBytes =
    models
      .filter((model) => model.installed)
      .reduce((total, model) => total + model.size_bytes, 0) +
    installedVideoModels.reduce((total, model) => total + model.total_size_bytes, 0) +
    installedInterpolationModels.reduce((total, model) => total + model.total_size_bytes, 0) +
    installedAudioModels.reduce((total, model) => total + model.total_size_bytes, 0) +
    installedEditModels.reduce((total, model) => total + model.total_size_bytes, 0);
  $: selectedEdit =
    (selectedId === '__custom__' ? customEdit : undefined) ??
    editModels.find((model) => model.model_id === selectedId) ??
    (group === 'edit'
      ? ((settings.edit_model_id === '__custom__' ? customEdit : undefined) ??
        editModels.find((model) => model.model_id === settings.edit_model_id) ??
        editFamilies.flatMap((family) => family.fitting)[0] ??
        editModels[0])
      : undefined);
  $: selectedAudio =
    audioModels.find((model) => model.model_id === selectedId) ??
    (group === 'audio' ? audioModels[0] : undefined);
  $: selectedInterpolation = interpolationModels.find((model) => model.model_id === selectedId);
  $: selectedVideo =
    videoModels.find((model) => model.model_id === selectedId) ??
    (group === 'video' && !selectedInterpolation ? videoModels[0] : undefined);
  $: selectedOwn =
    group === 'own'
      ? (ownModels.find((model) => model.path === selectedOwnPath) ??
        ownModels.find((model) => model.path === settings.custom_model_path) ??
        ownModels[0])
      : undefined;
  $: selected =
    selectedEdit || selectedAudio || selectedVideo || selectedInterpolation || selectedOwn
      ? undefined
      : (models.find((model) => model.model_id === selectedId) ?? rows[0]);
  // The accelerator the selected hardware will use for a model: the Neural Engine on a Mac,
  // an Intel NPU elsewhere. Memoised per model id: rows and details read it on every render.
  $: accelerator = (model: CatalogModel) =>
    runsOnAccelerator(settings, snapshot.capabilities, model.architecture);
  $: fitLines = new Map(
    models.map((model) => [
      model.model_id,
      fitLine(model, settings, snapshot.capabilities, device),
    ]),
  );
  $: quickId = fixing
    ? chooseFixModel(snapshot, fixing, 'quick', settings.preset_pins)?.model_id
    : context.task
      ? choosePresetModel(
          snapshot,
          context.task,
          'quick',
          context.content,
          settings.preset_pins,
          context.videoTask,
        )?.model_id
      : '';
  $: bestId = fixing
    ? chooseFixModel(snapshot, fixing, 'best', settings.preset_pins)?.model_id
    : context.task
      ? choosePresetModel(
          snapshot,
          context.task,
          'best',
          context.content,
          settings.preset_pins,
          context.videoTask,
        )?.model_id
      : '';
  $: rememberedFix = fixing
    ? rememberedFixModel(snapshot, fixing, fixQuality(context.quality), settings.preset_pins)
    : undefined;
  $: canUseForJob = (model: CatalogModel): boolean =>
    context.slot !== 'browse' &&
    jobModels.some((candidate) => candidate.model_id === model.model_id);
  $: canPin = (model: CatalogModel): boolean =>
    (context.slot === 'primary' || context.slot === 'try') &&
    !restoring &&
    canUseForJob(model) &&
    rightsAllowPreset(model);

  function selectRow(model: { model_id: string }): void {
    selectedId = model.model_id;
    termsAccepted = false;
  }

  function pickGroup(next: GroupId): void {
    group = next;
    selectedId = '';
    termsAccepted = false;
  }

  function modelLabel(modelId: string): string {
    if (modelId === '__custom__' && customEdit) return customEdit.name;
    return modelDisplayName(snapshot, modelId, true);
  }

  function selectByModelId(modelId: string): void {
    if (
      videoModels.some((item) => item.model_id === modelId) ||
      interpolationModels.some((item) => item.model_id === modelId)
    ) {
      group = 'video';
      selectedId = modelId;
      termsAccepted = false;
      return;
    }
    if (modelId === '__custom__' && customEdit) {
      group = 'edit';
      selectedId = '__custom__';
      termsAccepted = false;
      return;
    }
    const edit = editModels.find((item) => item.model_id === modelId);
    if (edit) {
      group = 'edit';
      if (editFit(edit) === 'too_heavy') showHeavyEdit = true;
      selectedId = modelId;
      termsAccepted = false;
      return;
    }
    const image = models.find((item) => item.model_id === modelId);
    if (!image) return;
    if (jobModels.some((item) => item.model_id === modelId)) {
      group = 'job';
    } else if (image.purposes.includes('face')) {
      // Face models have no group of their own any more; installed ones stay removable.
      group = 'installed';
    } else if (image.native_scale === 1) {
      group = 'fix';
    } else if (image.purposes.includes('illustration') || image.purposes.includes('anime')) {
      group = 'illustration';
    } else {
      group = 'photos';
    }
    selectedId = modelId;
    termsAccepted = false;
  }

  function downloadButtonLabel(entry: ModelDownloadStatus | undefined, sizeBytes: number): string {
    if (!entry) return `Download ${formatBytes(sizeBytes)}`;
    return entry.status === 'queued' ? 'Waiting…' : 'Downloading…';
  }

  function rightsLabel(model: CatalogModel): string {
    switch (
      model.rights_status ??
      (model.commercial_use_allowed === true ? 'verified' : 'unresolved')
    ) {
      case 'non_commercial':
        return 'Non-commercial';
      case 'unresolved':
        return 'Rights unresolved · your file';
      case 'attribution':
        return `${model.license_name} · attribution`;
      default:
        return model.license_name;
    }
  }

  function speedLabel(model: CatalogModel): string {
    return model.speed_tier >= 3 ? 'Fast' : model.speed_tier === 2 ? 'Medium' : 'Slow';
  }

  function memoryLabel(model: CatalogModel): string {
    if (!model.vram_estimate_mb) return 'Not estimated';
    return model.vram_estimate_mb >= 1000
      ? `about ${(model.vram_estimate_mb / 1000).toFixed(model.vram_estimate_mb % 1000 ? 1 : 0)} GB`
      : `about ${model.vram_estimate_mb} MB`;
  }

  function commercialUseLabel(model: {
    commercial_use_allowed?: boolean | null;
    attribution_required?: boolean;
  }): string {
    return model.commercial_use_allowed === true
      ? model.attribution_required
        ? 'Allowed · attribution kept'
        : 'Allowed'
      : model.commercial_use_allowed === false
        ? 'Not allowed'
        : 'Rights unresolved';
  }

  function ownFileName(path: string): string {
    return path.split(/[\\/]/).pop() || path;
  }
</script>

<div class="modal-backdrop library-backdrop" role="presentation">
  <div
    class="sheet"
    role="dialog"
    aria-modal="true"
    aria-labelledby="library-title"
    tabindex="-1"
    use:modal={close}
  >
    <div class="sheet-head">
      <h2 id="library-title">Model library</h2>
      {#if context.slot === 'try'}
        <span class="sheet-context"
          >Try other {restoring ? 'restoration models' : 'upscalers'} on {trialMedia?.name ??
            'this image'}</span
        >
      {:else if context.slot === 'edit'}
        <span class="sheet-context">Choosing for Edit</span>
      {:else if context.slot !== 'browse' && context.task}
        <span class="sheet-context"
          >Choosing for {context.task === 'video'
            ? restoring
              ? 'Video · Same size'
              : 'Video'
            : restoring
              ? 'Same size'
              : 'Upscale'}{fixing
            ? ` · ${FIX_LABELS[fixing]}`
            : context.slot === 'fix'
              ? ' · fix'
              : context.slot === 'face'
                ? ' · faces'
                : ` · ${context.quality === 'quick' ? 'Quick' : context.quality === 'best' ? 'Best' : 'Custom'}${restoring ? '' : ` · ${context.content === 'illustration' ? 'Illustration' : 'Photo'}`}`}</span
        >
      {/if}
      <div class="sheet-right">
        <span title={`Catalog ${snapshot.catalog.catalog_revision.slice(0, 7)}`}
          >{installedBytes ? `${formatBytes(installedBytes)} on this computer` : ''}</span
        >
        <button class="close" type="button" aria-label="Close model library" on:click={close}
          ><svg
            xmlns="http://www.w3.org/2000/svg"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12" /></svg
          ></button
        >
      </div>
    </div>
    {#if modelDirectoryMissing}
      <section class="library-downloads" role="alert" aria-label="Model folder unavailable">
        <div class="library-downloads-head">
          <span class="library-downloads-title">
            Model folder unavailable: {settings.model_directory}. Reconnect the drive, choose the
            folder again, or reset to default.
          </span>
          <div class="pair">
            <button
              class="button compact"
              type="button"
              disabled={busy}
              on:click={chooseModelFolder}>Choose folder…</button
            >
            <button class="button compact" type="button" disabled={busy} on:click={resetModelFolder}
              >Reset to default</button
            >
          </div>
        </div>
      </section>
    {/if}
    {#if downloads.length > 0 || failedEntries.length > 0}
      <section class="library-downloads" aria-label="Model downloads">
        <div class="library-downloads-head">
          <span class="library-downloads-title">
            {#if downloads.length > 0}
              {queuedDownloadCount > 0
                ? `Downloads · ${runningDownloadCount} active · ${queuedDownloadCount} waiting`
                : `Downloads · ${runningDownloadCount} active`}
            {:else}
              Downloads
            {/if}
          </span>
        </div>
        <div class="library-downloads-list">
          {#each downloads as item (item.model_id)}
            {@const name = modelLabel(item.model_id)}
            <div class="library-download-item">
              <div class="library-download-top">
                <button
                  class="library-download-name"
                  type="button"
                  on:click={() => selectByModelId(item.model_id)}>{name}</button
                >
                <span class="library-download-meta">
                  {#if item.status === 'queued'}
                    {item.total_bytes ? `Waiting · ${formatBytes(item.total_bytes)}` : 'Waiting'}
                  {:else}
                    {downloadDetails[item.model_id] ||
                      progressBytes(item.downloaded_bytes ?? 0, item.total_bytes ?? 0)}
                  {/if}
                </span>
                <button
                  class="button compact ghost"
                  type="button"
                  aria-label={`Cancel ${name} download`}
                  on:click={() => void cancelDownload(item.model_id)}>Cancel</button
                >
              </div>
              {#if item.status === 'downloading'}
                <progress
                  class="download-progress"
                  value={item.progress}
                  max="100"
                  aria-label={`${name} download progress`}
                ></progress>
              {/if}
            </div>
          {/each}
          {#each failedEntries as [modelId, message] (modelId)}
            {@const name = modelLabel(modelId)}
            {@const failure = downloadFailures[modelId] ?? {
              modelId,
              name,
              reason: 'other',
              done: 0,
              total: 0,
              message,
            }}
            <div class="library-download-item is-error" role="alert">
              <div class="library-download-top">
                <button
                  class="library-download-name"
                  type="button"
                  on:click={() => selectByModelId(modelId)}>{name}</button
                >
                <div class="eyebrow-actions">
                  <button
                    class="button compact ghost"
                    type="button"
                    disabled={busy}
                    aria-label={`Try ${name} again`}
                    on:click={() => void retryDownload(modelId)}>Try again</button
                  >
                  <button
                    class="button compact ghost"
                    type="button"
                    aria-label={`Dismiss ${name} error`}
                    on:click={() => dismissDownloadError(modelId)}>Dismiss</button
                  >
                </div>
              </div>
              <p class="library-download-error" title={failure.message || undefined}>
                {downloadFailureText(failure, false).detail}
              </p>
            </div>
          {/each}
        </div>
      </section>
    {/if}
    <div class="sheet-body">
      <nav class="rail" aria-label="Model groups">
        {#each context.slot === 'try' ? groups.filter((item) => item.id === 'job') : groups as item (item.id)}
          <button
            class="rail-item"
            class:active={group === item.id}
            type="button"
            aria-pressed={group === item.id}
            on:click={() => pickGroup(item.id)}
            ><span>{item.label}</span><small
              >{item.id === 'installed'
                ? `${item.count}${installedBytes ? ` · ${formatBytes(installedBytes)}` : ''}`
                : item.id === 'own'
                  ? ''
                  : item.count}</small
            ></button
          >
        {/each}
      </nav>

      <section class="list" aria-label="Models" bind:this={listEl}>
        {#snippet meters(quality: number, speed: number)}
          <div class="meters">
            <div class="meter">
              Quality<span class="visually-hidden">: {quality} out of 4</span><span
                class="dots"
                aria-hidden="true"
                >{#each [1, 2, 3, 4] as tier}<i class:f={quality >= tier}></i>{/each}</span
              >
            </div>
            <div class="meter">
              Speed<span class="visually-hidden">: {Math.min(speed, 3)} out of 4</span><span
                class="dots"
                aria-hidden="true"
                >{#each [1, 2, 3, 4] as tier}<i class:f={speed >= tier && tier <= 3}
                  ></i>{/each}</span
              >
            </div>
          </div>
        {/snippet}
        {#snippet videoRow(model: CatalogVideoModel)}
          {@const dl = downloadMap.get(model.model_id)}
          {@const dlError = downloadErrors[model.model_id]}
          {@const chosen =
            context.task === 'video' &&
            !restoring &&
            settings.selected_video_model_id === model.model_id}
          <button
            class="row"
            type="button"
            class:selected={selectedId === model.model_id}
            aria-pressed={selectedId === model.model_id}
            on:click={() => selectRow(model)}
          >
            <div>
              <div class="name">{model.name}</div>
              <div class="role">{videoModelSummary(model)}</div>
              <div class="pills">
                <span class="pill labs">Labs</span><span class="pill">{model.license_name}</span
                >{#if chosen}<span class="pill quick">Chosen</span
                  >{/if}{#if videoUnavailable[model.model_id]}<span class="pill bad"
                    >{videoUnavailable[model.model_id]?.tag}</span
                  >{/if}
              </div>
            </div>
            <!-- On the frame-by-frame models' scales; a model without a rating keeps the gap. -->
            {#if model.quality_tier}{@render meters(
                model.quality_tier,
                model.speed_tier ?? 0,
              )}{:else}<div></div>{/if}
            <div class="right">
              <b>{formatBytes(model.total_size_bytes)}</b>{#if dl}<span
                  class:downloading={dl.status === 'downloading'}
                  >{dl.status === 'queued' ? 'Waiting' : 'Downloading'}</span
                >{:else if dlError}<span class="error">Failed</span>{:else if model.installed}<span
                  class="ok">Installed</span
                >{:else}<span>Not downloaded</span>{/if}
            </div>
          </button>
        {/snippet}
        {#snippet interpolationRow(model: CatalogVideoModel)}
          {@const dl = downloadMap.get(model.model_id)}
          {@const dlError = downloadErrors[model.model_id]}
          <button
            class="row"
            type="button"
            class:selected={selectedInterpolation?.model_id === model.model_id}
            aria-pressed={selectedInterpolation?.model_id === model.model_id}
            on:click={() => selectRow(model)}
          >
            <div>
              <div class="name">{model.name}</div>
              <div class="role">{videoModelSummary(model)}</div>
              <div class="pills">
                <span class="pill labs">Labs</span><span class="pill">{model.license_name}</span>
              </div>
            </div>
            <div></div>
            <div class="right">
              <b>{formatBytes(model.total_size_bytes)}</b>{#if dl}<span
                  class:downloading={dl.status === 'downloading'}
                  >{dl.status === 'queued' ? 'Waiting' : 'Downloading'}</span
                >{:else if dlError}<span class="error">Failed</span>{:else if model.installed}<span
                  class="ok">Installed</span
                >{:else}<span>Not downloaded</span>{/if}
            </div>
          </button>
        {/snippet}
        {#if context.slot === 'try'}
          <div class="list-head">For this job<span>Pick 2 to 4 installed models</span></div>
          {#if trialModels.filter((model) => model.installed && !trialReason(model)).length < 2}
            <p class="trial-hint" role="status">
              Comparing needs at least 2 installed models. Select a model, then use Download on the
              right.
            </p>
          {/if}
          {#each trialModels as model (model.model_id)}
            {@const reason = trialReason(model)}
            {@const picked = trialIds.includes(model.model_id)}
            <label
              class="row trial-row"
              class:dimmed={Boolean(reason)}
              class:selected={selected?.model_id === model.model_id}
            >
              <input
                type="checkbox"
                checked={picked}
                disabled={trialBusy || Boolean(reason) || (!picked && trialIds.length >= 4)}
                on:change={() => toggleTrial(model.model_id)}
              />
              <div>
                <div class="name">{displayName(model)}</div>
                <div class="role">{reason || model.role || model.description}</div>
                {#if model.model_id === quickId || model.model_id === bestId || ['heavy', 'too_heavy'].includes(fitFor(model, device))}
                  <div class="pills">
                    {#if model.model_id === quickId}<span class="pill quick">Quick</span>{/if}
                    {#if model.model_id === bestId}<span class="pill best">Best</span>{/if}
                    {#if fitFor(model, device) === 'heavy'}<span class="pill warn">Heavy here</span
                      >{:else if fitFor(model, device) === 'too_heavy'}<span class="pill bad"
                        >Too heavy</span
                      >{/if}
                  </div>
                {/if}
              </div>
              <div class="right">
                <b>{formatBytes(model.size_bytes)}</b>{#if model.installed}<span class="ok"
                    >Installed</span
                  >{:else}<span>Not installed</span>{/if}
              </div>
            </label>
          {/each}
        {:else if group === 'video'}
          <div class="list-head">
            Video · Labs<span
              >{videoModels.length === 1 ? '1 model' : `${videoModels.length} models`}</span
            >
          </div>
          {#each videoModels as model (model.model_id)}
            {@render videoRow(model)}
          {/each}
          {#if interpolationModels.length}
            <div class="list-head">
              Frame interpolation · Labs<span
                >{interpolationModels.length === 1
                  ? '1 model'
                  : `${interpolationModels.length} models`}</span
              >
            </div>
            {#each interpolationModels as model (model.model_id)}
              {@render interpolationRow(model)}
            {/each}
          {/if}
        {:else if group === 'audio'}
          <div class="list-head">
            Audio<span>{audioModels.length === 1 ? '1 model' : `${audioModels.length} models`}</span
            >
          </div>
          {#each audioModels as model (model.model_id)}
            {@const dl = downloadMap.get(model.model_id)}
            {@const dlError = downloadErrors[model.model_id]}
            <button
              class="row"
              type="button"
              class:selected={selectedAudio?.model_id === model.model_id}
              aria-pressed={selectedAudio?.model_id === model.model_id}
              on:click={() => selectRow(model)}
            >
              <div>
                <div class="name">{model.name}</div>
                <div class="role">{model.description}</div>
                <div class="pills">
                  <span class="pill">{model.license_name}</span>
                </div>
              </div>
              <div class="right">
                <b>{formatBytes(model.total_size_bytes)}</b>{#if dl}<span
                    class:downloading={dl.status === 'downloading'}
                    >{dl.status === 'queued' ? 'Waiting' : 'Downloading'}</span
                  >{:else if dlError}<span class="error">Failed</span
                  >{:else if model.installed}<span class="ok">Installed</span>{:else}<span
                    >Not downloaded</span
                  >{/if}
              </div>
            </button>
          {/each}
        {:else if group === 'edit'}
          {#each editFamilies as family (family.id)}
            {@const shown = showHeavyEdit ? [...family.fitting, ...family.heavy] : family.fitting}
            {#if shown.length}
              <div class="list-head">
                {family.label}<span>{family.summary.split(' · ')[0]}</span>
              </div>
            {/if}
            {#each shown as model (model.model_id)}
              {@const dl = downloadMap.get(model.model_id)}
              {@const dlError = downloadErrors[model.model_id]}
              <button
                class="row"
                type="button"
                class:selected={selectedEdit?.model_id === model.model_id}
                aria-pressed={selectedEdit?.model_id === model.model_id}
                on:click={() => selectRow(model)}
              >
                <div>
                  <div class="name">
                    {editVariantName(model)}<small class="tech">{model.quantization}</small>
                  </div>
                  <div class="role">
                    {editFit(model) === 'too_heavy'
                      ? `Needs ${onMac ? `a Mac with ${model.min_unified_memory_gb} GB` : `${model.min_vram_gb} GB of GPU memory`}`
                      : onMac
                        ? `Needs ${model.min_unified_memory_gb} GB of memory`
                        : `Needs ${model.min_vram_gb} GB of GPU memory`}
                  </div>
                  {#if settings.edit_model_id === model.model_id || model.terms_acceptance_required}
                    <div class="pills">
                      {#if settings.edit_model_id === model.model_id}<span class="pill quick"
                          >Chosen</span
                        >{/if}
                      {#if model.terms_acceptance_required}<span class="pill warn"
                          >{model.license_name}</span
                        >{/if}
                    </div>
                  {/if}
                </div>
                <div class="right">
                  <b>{formatBytes(model.total_size_bytes)}</b>{#if dl}<span
                      class:downloading={dl.status === 'downloading'}
                      >{dl.status === 'queued' ? 'Waiting' : 'Downloading'}</span
                    >{:else if dlError}<span class="error">Failed</span
                    >{:else if model.installed}<span class="ok">Installed</span>{/if}
                </div>
              </button>
            {/each}
          {/each}
          {#if heavyEditCount}
            <button
              class="list-more"
              type="button"
              aria-expanded={showHeavyEdit}
              on:click={() => (showHeavyEdit = !showHeavyEdit)}
              >{showHeavyEdit
                ? 'Hide models that need more memory'
                : `Show ${heavyEditCount} more that need more memory than this computer has`}</button
            >
          {/if}
          {#if customEdit}
            <div class="list-head">Your own edit model</div>
            <button
              class="row"
              type="button"
              class:selected={selectedEdit?.model_id === customEdit.model_id}
              aria-pressed={selectedEdit?.model_id === customEdit.model_id}
              on:click={() => selectRow(customEdit)}
            >
              <div>
                <div class="name">
                  {editFamilyLabel(customEdit.family)}<small class="tech">Custom</small>
                </div>
                <div class="role">
                  {onMac
                    ? `Needs ${customEdit.min_unified_memory_gb} GB of memory`
                    : `Needs ${customEdit.min_vram_gb} GB of GPU memory`}
                </div>
                {#if settings.edit_model_id === customEdit.model_id}
                  <div class="pills"><span class="pill quick">Chosen</span></div>
                {/if}
              </div>
              <div class="right">
                <b>{formatBytes(customEdit.total_size_bytes)}</b>
                <span class:ok={customEdit.installed} class:error={!customEdit.installed}
                  >{customEdit.installed ? 'Your files' : 'Missing file'}</span
                >
              </div>
            </button>
          {/if}
          <button class="list-more custom-edit-link" type="button" on:click={openCustomEdit}
            >Use your own edit model…</button
          >
        {:else if group === 'own'}
          <div class="list-head">Your own file</div>
          {#each ownModels as model (model.path)}
            <div class="own-file">
              <div class="name">{model.name}</div>
              <div class="role">{model.path}</div>
              {#if renamingPath === model.path}
                <label>Model name <input aria-label="Model name" bind:value={customName} /></label>
                <button
                  class="button"
                  type="button"
                  on:click={() => {
                    renameCustomModel(model.path, customName);
                    renamingPath = '';
                  }}>Save name</button
                >
                <button class="button" type="button" on:click={() => (renamingPath = '')}
                  >Cancel</button
                >
              {:else}
                {#if context.slot === 'primary'}<button
                    class="button"
                    type="button"
                    on:click={() => selectCustomModel(model.path)}>Use {model.name}</button
                  >{/if}
                <button
                  class="button"
                  type="button"
                  aria-label={`Rename ${model.name}`}
                  on:click={() => {
                    renamingPath = model.path;
                    customName = model.name;
                  }}>Rename</button
                >
                {#if ownModels.length > 1}<button
                    class="button"
                    type="button"
                    aria-pressed={selectedOwn?.path === model.path}
                    on:click={() => (selectedOwnPath = model.path)}>Details</button
                  >{/if}
              {/if}
            </div>
          {/each}
          <div class="own-file">
            <p>
              Use a checkpoint you already have. Safetensors is strongly recommended; pickle-based
              files run the publisher's code inside the inference worker and need an explicit
              confirmation.
            </p>
            <div class="own-actions">
              <button class="button" type="button" on:click={() => void chooseCustom()}
                >Choose checkpoint…</button
              ><button class="link-button" type="button" on:click={() => void openTutorial()}
                >Watch the tutorial</button
              >
            </div>
            {#if tutorialNote}<p class="own-note">{tutorialNote}</p>{/if}
          </div>
        {:else}
          <div class="list-head">
            {#if group === 'job' && jobVideoModels.length}<div class="label-with-tip">
                Frame by frame<HelpTip label="Frame by frame" text={FRAME_BY_FRAME_TIP} />
              </div>{:else}{group === 'job'
                ? 'For this job'
                : group === 'installed'
                  ? `INSTALLED · ${formatBytes(installedBytes)}`
                  : groups.find((item) => item.id === group)?.label}{/if}<span
              >{group === 'job' ? 'sorted by quality' : ''}</span
            >
          </div>
          {#each rows as model (model.model_id)}
            {#if group === 'installed'}
              <div class="row installed-row" class:selected={selected?.model_id === model.model_id}>
                <button class="row-select" type="button" on:click={() => selectRow(model)}>
                  <div class="name">{displayName(model)}</div>
                  <div class="role">
                    {model.model_id === bestId
                      ? 'Used by Best'
                      : model.model_id === quickId
                        ? 'Used by Quick'
                        : (model.role ?? '')}
                  </div>
                </button>
                <div class="right">
                  <b>{formatBytes(model.size_bytes)}</b><span class="ok">Verified</span>
                </div>
                <button
                  class="button compact ghost danger"
                  type="button"
                  disabled={busy}
                  aria-label={`Remove ${displayName(model)}`}
                  on:click={() => void remove(model)}>Remove</button
                >
              </div>
            {:else}
              <button
                class="row"
                type="button"
                class:selected={selected?.model_id === model.model_id}
                aria-pressed={selected?.model_id === model.model_id}
                on:click={() => selectRow(model)}
              >
                <div>
                  <div class="name">{displayName(model)}</div>
                  <div class="role">{model.role || model.description}</div>
                  {#if fitLines.get(model.model_id)}
                    {@const line = fitLines.get(model.model_id)}
                    <div class="fit row-fit" title={line?.text}>
                      <i aria-hidden="true" class:warn={line?.fit !== 'runs'}></i><span
                        >{line?.text}</span
                      >
                    </div>
                  {/if}
                  <!-- Badges mark exceptions only: a license that limits use, a model that
                       will struggle here. Everything else is in the details. -->
                  {#if model.model_id === quickId || model.model_id === bestId || model.support_tier === 'labs' || model.commercial_use_allowed !== true || ['heavy', 'too_heavy'].includes(fitFor(model, device))}
                    <div class="pills">
                      {#if model.model_id === quickId}<span class="pill quick">Quick</span>{/if}
                      {#if model.model_id === bestId}<span class="pill best">Best</span>{/if}
                      {#if model.support_tier === 'labs'}<span class="pill labs">Labs</span>{/if}
                      {#if model.commercial_use_allowed !== true}<span class="pill warn"
                          >{rightsLabel(model)}</span
                        >{/if}
                      {#if fitFor(model, device) === 'heavy'}<span class="pill warn"
                          >Heavy here</span
                        >{:else if fitFor(model, device) === 'too_heavy'}<span class="pill bad"
                          >Too heavy</span
                        >{/if}
                    </div>
                  {/if}
                </div>
                {@render meters(model.quality_tier, model.speed_tier)}
                <div class="right">
                  <b>{formatBytes(model.size_bytes)}</b>{#if downloadMap.get(model.model_id)}
                    {@const dl = downloadMap.get(model.model_id)}<span
                      class:downloading={dl?.status === 'downloading'}
                      >{dl?.status === 'queued' ? 'Waiting' : 'Downloading'}</span
                    >{:else if downloadErrors[model.model_id]}<span class="error">Failed</span
                    >{:else if model.installed}<span class="ok">Installed</span
                    >{:else if !model.automated_download_allowed}<span>Your file</span>{/if}
                </div>
              </button>
            {/if}
          {/each}
          {#if group === 'job' && jobVideoModels.length}
            <div class="list-head">
              <div class="label-with-tip">
                Several frames at once · Labs<HelpTip
                  label="Several frames at once"
                  text={SEVERAL_FRAMES_TIP}
                />
              </div>
              <span>use neighbouring frames · much slower</span>
            </div>
            {#each jobVideoModels as model (model.model_id)}
              {@render videoRow(model)}
            {/each}
          {/if}
          {#if group === 'installed'}
            {#each installedVideoModels as model (model.model_id)}
              <div
                class="row installed-row"
                class:selected={selectedVideo?.model_id === model.model_id}
              >
                <button class="row-select" type="button" on:click={() => selectRow(model)}>
                  <div class="name">{model.name}</div>
                  <div class="role">Video · {videoEngineName(model.engine_kind)}</div>
                </button>
                <div class="right">
                  <b>{formatBytes(model.total_size_bytes)}</b><span class="ok">Verified</span>
                </div>
                <button
                  class="button compact ghost danger"
                  type="button"
                  disabled={busy}
                  aria-label={`Remove ${model.name}`}
                  on:click={() => void remove(model)}>Remove</button
                >
              </div>
            {/each}
            {#each installedInterpolationModels as model (model.model_id)}
              <div
                class="row installed-row"
                class:selected={selectedInterpolation?.model_id === model.model_id}
              >
                <button class="row-select" type="button" on:click={() => selectRow(model)}>
                  <div class="name">{model.name}</div>
                  <div class="role">Frame interpolation</div>
                </button>
                <div class="right">
                  <b>{formatBytes(model.total_size_bytes)}</b><span class="ok">Verified</span>
                </div>
                <button
                  class="button compact ghost danger"
                  type="button"
                  disabled={busy}
                  aria-label={`Remove ${model.name}`}
                  on:click={() => void remove(model)}>Remove</button
                >
              </div>
            {/each}
            {#each installedAudioModels as model (model.model_id)}
              <div
                class="row installed-row"
                class:selected={selectedAudio?.model_id === model.model_id}
              >
                <button class="row-select" type="button" on:click={() => selectRow(model)}>
                  <div class="name">{model.name}</div>
                  <div class="role">{model.summary ? `Audio · ${model.summary}` : 'Audio'}</div>
                </button>
                <div class="right">
                  <b>{formatBytes(model.total_size_bytes)}</b><span class="ok">Verified</span>
                </div>
                <button
                  class="button compact ghost danger"
                  type="button"
                  disabled={busy}
                  aria-label={`Remove ${model.name}`}
                  on:click={() => void remove(model)}>Remove</button
                >
              </div>
            {/each}
            {#each installedEditModels as model (model.model_id)}
              <div
                class="row installed-row"
                class:selected={selectedEdit?.model_id === model.model_id}
              >
                <button class="row-select" type="button" on:click={() => selectRow(model)}>
                  <div class="name">{model.name}</div>
                  <div class="role">Edit with a prompt · {model.files.length} components</div>
                </button>
                <div class="right">
                  <b>{formatBytes(model.total_size_bytes)}</b><span class="ok">Verified</span>
                </div>
                <button
                  class="button compact ghost danger"
                  type="button"
                  disabled={busy}
                  aria-label={`Remove ${model.name}`}
                  on:click={() => void remove(model)}>Remove</button
                >
              </div>
            {/each}
          {/if}
          {#if !rows.length && !(group === 'job' && jobVideoModels.length) && !(group === 'installed' && (installedVideoModels.length || installedInterpolationModels.length || installedAudioModels.length || installedEditModels.length))}
            <p class="library-empty">
              Nothing verified is listed here yet. Import your own file, or suggest a model on the
              project page.
            </p>
          {/if}
        {/if}
      </section>

      <aside class="detail" aria-label="Model details">
        {#snippet licenseAndSource(
          modelId: string,
          licenseName: string,
          author: string,
          sourceUrl: string,
        )}
          <div>
            <dt>License</dt>
            <dd>
              <button class="link-button" type="button" on:click={() => void openLicense(modelId)}
                >{licenseName} ↗</button
              >
            </dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd class="source-value">
              {#if author}
                <button
                  class="link-button"
                  type="button"
                  title={sourceUrl}
                  aria-label={`${author} · model source`}
                  on:click={() => void openSource(modelId)}
                  >{author}<span class="source-link">{' · model source ↗'}</span></button
                >
              {:else}
                <button
                  class="link-button"
                  type="button"
                  title={sourceUrl}
                  on:click={() => void openSource(modelId)}>Model source ↗</button
                >
              {/if}
            </dd>
          </div>
        {/snippet}
        {#snippet integrityAndRights(commercialText: string)}
          <div>
            <dt>Integrity</dt>
            <dd>SHA-256 checked before use</dd>
          </div>
          <div>
            <dt>Commercial use</dt>
            <dd>{commercialText}</dd>
          </div>
        {/snippet}
        {#if context.slot === 'try'}
          <div class="trial-summary" aria-live="polite">
            <h3>{trialIds.length === 1 ? '1 model picked' : `${trialIds.length} models picked`}</h3>
            <p>
              Each runs the full image with your current settings ({keptTrialSettings(
                settings,
                Boolean(trialBase?.enable_face_model),
              )}).
            </p>
            {#if trialIds.length && trialEstimates.length === trialIds.length}
              <p>{trialTime(trialEstimates)}</p>
            {/if}
            <p>
              {trialIds.length || 'No'} new file{trialIds.length === 1 ? '' : 's'} in
              <span class="path-text"
                >{trialBase?.output_directory || 'the LocalSR output folder'}</span
              >, named by model.
            </p>
            <button
              class="button"
              type="button"
              disabled={trialBusy || runningTrials || trialIds.length < 2 || trialIds.length > 4}
              on:click={() => void startTrials()}
              >{runningTrials
                ? 'Queueing…'
                : `Run ${trialIds.length} model${trialIds.length === 1 ? '' : 's'}`}</button
            >
            <p>Pick 2 to 4. Results appear under Showing in the preview.</p>
            {#if estimateError}<p role="status">
                Run time estimate unavailable: {estimateError}
              </p>{/if}
          </div>
        {/if}
        {#if selectedVideo && group !== 'own'}
          <div>
            <h3>{selectedVideo.name}</h3>
            <div class="role">{selectedVideo.author} · {selectedVideo.license_name}</div>
          </div>
          <p class="model-description">{selectedVideo.description}</p>
          <dl class="facts">
            <div>
              <dt>Download</dt>
              <dd>{formatBytes(selectedVideo.total_size_bytes)}</dd>
            </div>
            <div>
              <dt>Memory</dt>
              <dd>{selectedVideo.min_unified_memory_gb} GB unified</dd>
            </div>
            <div>
              <dt>Engine</dt>
              <dd>{videoEngineName(selectedVideo.engine_kind)}</dd>
            </div>
            <div>
              <dt>Works on</dt>
              <dd>{selectedVideo.temporal_window} frames at once</dd>
            </div>
          </dl>
          {#if videoUnavailable[selectedVideo.model_id]}
            <div class="fit">
              <i aria-hidden="true" class="warn"></i>{videoUnavailable[selectedVideo.model_id]
                ?.reason}
            </div>
          {/if}
          <dl class="kv">
            {@render licenseAndSource(
              selectedVideo.model_id,
              selectedVideo.license_name,
              selectedVideo.author,
              selectedVideo.source_url,
            )}
            {#if selectedVideo.files.length === 1}
              <div>
                <dt>File</dt>
                <dd><FileName name={selectedVideo.files[0].filename} /></dd>
              </div>
            {:else if selectedVideo.files.length > 1}
              <div>
                <dt>Files</dt>
                <dd>{selectedVideo.files.length} files</dd>
              </div>
            {/if}
            {@render integrityAndRights(commercialUseLabel(selectedVideo))}
          </dl>
          <div class="actions">
            {#if !selectedVideo.installed}
              {@const dl = downloadMap.get(selectedVideo.model_id)}
              {@const dlError = downloadErrors[selectedVideo.model_id]}
              {#if dlError}<p class="download-error" role="alert">{dlError}</p>{/if}
              <button
                class="button primary"
                type="button"
                disabled={busy || Boolean(dl)}
                on:click={() => void download(selectedVideo)}
                >{downloadButtonLabel(dl, selectedVideo.total_size_bytes)}</button
              >
            {:else}
              <div class="installed-badge">✓ Installed · integrity checked before use</div>
            {/if}
            {#if context.slot === 'primary' && context.task === 'video' && !restoring}
              <button
                class="button"
                type="button"
                disabled={busy || Boolean(videoUnavailable[selectedVideo.model_id])}
                on:click={() => useVideoForJob(selectedVideo)}>Use for this job</button
              >
            {/if}
          </div>
        {:else if selectedInterpolation && group !== 'own'}
          <div>
            <h3>{selectedInterpolation.name}</h3>
            <div class="role">
              {selectedInterpolation.author} · {selectedInterpolation.license_name}
            </div>
          </div>
          <p class="model-description">{selectedInterpolation.description}</p>
          <dl class="facts">
            <div>
              <dt>Download</dt>
              <dd>{formatBytes(selectedInterpolation.total_size_bytes)}</dd>
            </div>
            <div>
              <dt>Memory</dt>
              <dd>{selectedInterpolation.min_unified_memory_gb} GB unified</dd>
            </div>
            <div>
              <dt>Works on</dt>
              <dd>{selectedInterpolation.temporal_window} frames at once</dd>
            </div>
          </dl>
          <dl class="kv">
            {@render licenseAndSource(
              selectedInterpolation.model_id,
              selectedInterpolation.license_name,
              selectedInterpolation.author,
              selectedInterpolation.source_url,
            )}
            {#if selectedInterpolation.files.length === 1}
              <div>
                <dt>File</dt>
                <dd><FileName name={selectedInterpolation.files[0].filename} /></dd>
              </div>
            {:else if selectedInterpolation.files.length > 1}
              <div>
                <dt>Files</dt>
                <dd>{selectedInterpolation.files.length} files</dd>
              </div>
            {/if}
            {@render integrityAndRights(commercialUseLabel(selectedInterpolation))}
          </dl>
          <div class="actions">
            {#if !selectedInterpolation.installed}
              {@const dl = downloadMap.get(selectedInterpolation.model_id)}
              {@const dlError = downloadErrors[selectedInterpolation.model_id]}
              {#if dlError}<p class="download-error" role="alert">{dlError}</p>{/if}
              <button
                class="button primary"
                type="button"
                disabled={busy || Boolean(dl)}
                on:click={() => void download(selectedInterpolation)}
                >{downloadButtonLabel(dl, selectedInterpolation.total_size_bytes)}</button
              >
            {:else}
              <div class="installed-badge">✓ Installed · integrity checked before use</div>
              <button
                class="button danger ghost"
                type="button"
                disabled={busy}
                on:click={() => void remove(selectedInterpolation)}
                >Remove from this computer</button
              >
            {/if}
          </div>
        {:else if selectedAudio && group !== 'own'}
          <div>
            <h3>{selectedAudio.name}</h3>
            <div class="role">
              {[selectedAudio.author, selectedAudio.summary].filter(Boolean).join(' · ')}
            </div>
          </div>
          <p class="model-description">{selectedAudio.description}</p>
          <dl class="facts">
            <div>
              <dt>Download</dt>
              <dd>{formatBytes(selectedAudio.total_size_bytes)}</dd>
            </div>
            <div>
              <dt>Memory while running</dt>
              <dd>about 100 MB</dd>
            </div>
            <div>
              <dt>Runs on</dt>
              <dd>CPU · ONNX Runtime</dd>
            </div>
          </dl>
          <dl class="kv">
            {@render licenseAndSource(
              selectedAudio.model_id,
              selectedAudio.license_name,
              selectedAudio.author,
              selectedAudio.source_url,
            )}
            {#if selectedAudio.files.length === 1}
              <div>
                <dt>File</dt>
                <dd><FileName name={selectedAudio.files[0].filename} /></dd>
              </div>
            {:else if selectedAudio.files.length > 1}
              <div>
                <dt>Files</dt>
                <dd>{selectedAudio.files.length} files</dd>
              </div>
            {/if}
            {@render integrityAndRights(commercialUseLabel(selectedAudio))}
          </dl>
          <div class="actions">
            {#if !selectedAudio.installed}
              {@const dl = downloadMap.get(selectedAudio.model_id)}
              {@const dlError = downloadErrors[selectedAudio.model_id]}
              {#if dlError}<p class="download-error" role="alert">{dlError}</p>{/if}
              <button
                class="button primary"
                type="button"
                disabled={busy || Boolean(dl)}
                on:click={() => void download(selectedAudio)}
                >{downloadButtonLabel(dl, selectedAudio.total_size_bytes)}</button
              >
            {:else}
              <div class="installed-badge">✓ Installed · integrity checked before use</div>
              <button
                class="button danger ghost"
                type="button"
                disabled={busy}
                on:click={() => void remove(selectedAudio)}>Remove from this computer</button
              >
            {/if}
          </div>
        {:else if selectedEdit && group !== 'own'}
          {@const fit = editFit(selectedEdit)}
          {@const isCustomEdit = selectedEdit.model_id === '__custom__'}
          <div>
            <h3>{isCustomEdit ? editFamilyLabel(selectedEdit.family) : selectedEdit.name}</h3>
            <div class="role">
              {editFamilyLabel(selectedEdit.family)} · {selectedEdit.files.length} components
            </div>
          </div>
          <p class="model-description">
            {#if isCustomEdit}
              {editFamilySummary(selectedEdit.family)}. Local files stay where they are, and the
              memory check uses their real sizes.
            {:else}
              {editFamilySummary(selectedEdit.family)}. {editVariantName(selectedEdit)} size: smaller
              sizes download faster and need less memory, but can lose some edit fidelity.
            {/if}
          </p>
          <dl class="facts">
            <div>
              <dt>{isCustomEdit ? 'Size' : 'Download'}</dt>
              <dd>{formatBytes(selectedEdit.total_size_bytes)}</dd>
            </div>
            <div>
              <dt>Needs</dt>
              <dd>
                {onMac
                  ? `${selectedEdit.min_unified_memory_gb} GB memory`
                  : `${selectedEdit.min_vram_gb} GB GPU memory`}
              </dd>
            </div>
            <div>
              <dt>Largest edit here</dt>
              <dd>{editLimit} px</dd>
            </div>
          </dl>
          {#if fit !== 'unknown'}
            <div class="fit">
              <i class:warn={fit !== 'runs'}></i>{EDIT_FIT_LABELS[fit]} · {editHardwareLabel(
                snapshot.capabilities,
                editGpu,
              )}
            </div>
          {/if}

          <dl class="kv">
            {#if isCustomEdit}
              <div>
                <dt>License</dt>
                <dd>Your own file</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd>Your own file</dd>
              </div>
              <div>
                <dt>Format</dt>
                <dd>Custom · {selectedEdit.files.length} files</dd>
              </div>
              <div>
                <dt>Runtime</dt>
                <dd>stable-diffusion.cpp · {editRuntimeLabel(editGpu)}</dd>
              </div>
              <div>
                <dt>Storage</dt>
                <dd>Kept in place</dd>
              </div>
            {:else}
              {@render licenseAndSource(
                selectedEdit.model_id,
                selectedEdit.license_name,
                selectedEdit.author ?? '',
                selectedEdit.source_url,
              )}
              <div>
                <dt>Format</dt>
                <dd>GGUF {selectedEdit.quantization} · {selectedEdit.files.length} files</dd>
              </div>
              <div>
                <dt>Runtime</dt>
                <dd>stable-diffusion.cpp · {editRuntimeLabel(editGpu)}</dd>
              </div>
              {@render integrityAndRights(
                selectedEdit.terms_acceptance_required
                  ? 'Research license · review the terms'
                  : commercialUseLabel({
                      ...selectedEdit,
                      commercial_use_allowed: selectedEdit.commercial_use_allowed ?? true,
                    }),
              )}
            {/if}
          </dl>
          <div class="actions">
            {#if isCustomEdit}
              {#if selectedEdit.installed}
                <div class="installed-badge">✓ Ready · files stay in place</div>
              {:else}
                <p class="download-error" role="alert">
                  One or more files are missing. Choose them again.
                </p>
              {/if}
              {#if context.slot === 'edit' || context.task === 'edit'}
                <button
                  class="button primary"
                  type="button"
                  disabled={busy || !selectedEdit.installed || fit === 'too_heavy'}
                  on:click={() => useEditModel(selectedEdit)}>Use for this job</button
                >
              {/if}
              <button class="button" type="button" disabled={busy} on:click={openCustomEdit}
                >Choose files…</button
              >
            {:else}
              {#if !selectedEdit.installed}
                {@const dl = downloadMap.get(selectedEdit.model_id)}
                {@const dlError = downloadErrors[selectedEdit.model_id]}
                {#if selectedEdit.terms_acceptance_required}
                  <label class="terms"
                    ><input type="checkbox" bind:checked={termsAccepted} /> I reviewed the Qwen Research
                    License and accept its terms.</label
                  >
                {/if}
                {#if dlError}<p class="download-error" role="alert">{dlError}</p>{/if}
                <button
                  class="button primary"
                  type="button"
                  disabled={busy ||
                    Boolean(dl) ||
                    (selectedEdit.terms_acceptance_required && !termsAccepted)}
                  on:click={() => void download(selectedEdit, termsAccepted)}
                  >{downloadButtonLabel(dl, selectedEdit.total_size_bytes)}</button
                >
              {:else}
                <div class="installed-badge">✓ Installed · integrity checked before use</div>
              {/if}
              {#if context.slot === 'edit' || context.task === 'edit'}
                <button
                  class="button"
                  type="button"
                  disabled={busy || fit === 'too_heavy'}
                  on:click={() => useEditModel(selectedEdit)}>Use for this job</button
                >
              {/if}
              {#if selectedEdit.installed}
                <button
                  class="button danger ghost"
                  type="button"
                  disabled={busy}
                  on:click={() => void remove(selectedEdit)}>Remove from this computer</button
                >
              {/if}
            {/if}
          </div>
        {:else if group !== 'own' && selected}
          <div>
            <h3>{displayName(selected)}</h3>
            <div class="role">{selected.role || selected.name}</div>
            {#if selected.support_tier === 'labs'}<div class="pills">
                <span class="pill labs">Labs</span>
              </div>{/if}
          </div>
          <p class="model-description">{selected.description}</p>
          <dl class="facts">
            <div>
              <dt>Scale</dt>
              <dd>×{selected.native_scale}</dd>
            </div>
            <div>
              <dt>Download</dt>
              <dd>{formatBytes(selected.size_bytes)}</dd>
            </div>
            <div>
              <dt>Memory while running</dt>
              <dd>{memoryLabel(selected)}</dd>
            </div>
            <div>
              <dt>Speed</dt>
              <dd>{speedLabel(selected)}</dd>
            </div>
            {#if accelerator(selected)}
              <div>
                <dt>Runs on</dt>
                <dd>{accelerator(selected)}</dd>
              </div>
            {/if}
          </dl>
          {#if fitLines.get(selected.model_id)}
            {@const line = fitLines.get(selected.model_id)}
            <div class="fit">
              <i aria-hidden="true" class:warn={line?.fit !== 'runs'}></i>{line?.text}
            </div>
          {/if}
          <dl class="kv">
            {@render licenseAndSource(
              selected.model_id,
              selected.license_name,
              selected.author,
              selected.source_url,
            )}
            <div>
              <dt>Architecture</dt>
              <dd>{selected.architecture} · Spandrel</dd>
            </div>
            <div>
              <dt>File</dt>
              <dd><FileName name={selected.filename} /></dd>
            </div>
            {@render integrityAndRights(commercialUseLabel(selected))}
          </dl>
          <div class="actions">
            {#if !selected.installed && selected.automated_download_allowed}
              {@const dl = downloadMap.get(selected.model_id)}
              {@const dlError = downloadErrors[selected.model_id]}
              {#if selected.terms_acceptance_required}
                <label class="terms"
                  ><input type="checkbox" bind:checked={termsAccepted} /> I reviewed the model license
                  and restrictions.</label
                >
              {/if}
              {#if dlError}<p class="download-error" role="alert">{dlError}</p>{/if}
              <button
                class="button primary"
                type="button"
                disabled={busy ||
                  Boolean(dl) ||
                  (selected.terms_acceptance_required && !termsAccepted)}
                on:click={() => void download(selected, termsAccepted)}
                >{downloadButtonLabel(dl, selected.size_bytes)}</button
              >
            {:else if !selected.installed}
              <label class="terms"
                ><input type="checkbox" bind:checked={termsAccepted} /> I understand that the checkpoint
                rights are unresolved and will provide a copy I am permitted to use.</label
              >
              <button
                class="button"
                type="button"
                disabled={busy || !termsAccepted}
                on:click={() => void importFile(selected, termsAccepted)}
                >Choose externally downloaded checkpoint…</button
              >
            {:else}
              <div class="installed-badge">✓ Installed · integrity checked before use</div>
            {/if}
            {#if fixing}
              {@const problem = fixing}
              <div class="pair">
                {#if canUseForJob(selected) && rightsAllowPreset(selected)}
                  <button
                    class="button"
                    type="button"
                    disabled={busy}
                    on:click={() => rememberFix(selected, problem)}
                    >Use for {FIX_LABELS[problem]}{context.quality === 'quick'
                      ? ' · Quick'
                      : context.quality === 'best'
                        ? ' · Best'
                        : ''}</button
                  >
                {/if}
                {#if rememberedFix}
                  <button
                    class="link-button"
                    type="button"
                    disabled={busy}
                    on:click={() => forgetFix(problem)}>Back to the default</button
                  >
                {/if}
              </div>
            {:else if canUseForJob(selected)}
              <div class="pair">
                <button
                  class="button"
                  type="button"
                  disabled={busy}
                  on:click={() => useForJob(selected)}>Use for this job</button
                >
                {#if canPin(selected)}
                  <button
                    class="button"
                    type="button"
                    disabled={busy}
                    on:click={() => pin(selected, context.quality === 'quick' ? 'quick' : 'best')}
                    >Make my {context.quality === 'quick' ? 'Quick' : 'Best'} · {context.content ===
                    'illustration'
                      ? 'Illustration'
                      : 'Photo'}</button
                  >
                {/if}
              </div>
            {/if}
            {#if selected.installed}
              <button
                class="button danger ghost"
                type="button"
                disabled={busy}
                on:click={() => void remove(selected)}>Remove from this computer</button
              >
              {#if selected.model_id === quickId || selected.model_id === bestId}
                <p class="inline-warning">
                  {selected.model_id === bestId ? 'Best' : 'Quick'} uses this model. Removing it means
                  the preset needs a {formatBytes(selected.size_bytes)} download again.
                </p>
              {/if}
            {/if}
          </div>
        {:else if selectedOwn}
          <div>
            <h3>{selectedOwn.name}</h3>
            <div class="role">Your own file</div>
          </div>
          <p class="model-description">
            You are responsible for the model's licence. LocalSR does not download or share it, and
            your file stays where it is.
          </p>
          <dl class="kv">
            <div>
              <dt>License</dt>
              <dd>Your own file</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>Your own file</dd>
            </div>
            <div>
              <dt>File</dt>
              <dd><FileName name={ownFileName(selectedOwn.path)} /></dd>
            </div>
            <div>
              <dt>Storage</dt>
              <dd>Kept in place</dd>
            </div>
          </dl>
        {:else if group !== 'own'}
          <p class="model-description">
            Select a model to see what it is for and where it comes from.
          </p>
        {/if}
      </aside>
    </div>
  </div>
</div>
