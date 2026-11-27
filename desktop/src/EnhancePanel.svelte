<script lang="ts">
  import { onDestroy } from 'svelte';
  import AdvancedSettings from './AdvancedSettings.svelte';
  import FixList from './FixList.svelte';
  import HelpTip from './HelpTip.svelte';
  import ModelCredit from './ModelCredit.svelte';
  import OutputSheet from './OutputSheet.svelte';
  import PlanSheet from './PlanSheet.svelte';
  import Popover from './Popover.svelte';
  import RecipesMenu from './RecipesMenu.svelte';
  import VideoMemory from './VideoMemory.svelte';
  import { customModels } from './lib/customModels';
  import { EDIT_FIT_LABELS, editFamilyLabel, editVariantName } from './lib/editing';
  import {
    mediaFacts,
    outputSummary,
    type EnhancePanelActions,
    type EnhancePanelView,
  } from './lib/enhancePanel';
  import { formatBytes } from './lib/format';
  import { displayName, type PlanStage } from './lib/plan';
  import { planLine } from './lib/planLine';
  import type { InterpolationOrder, InterpolationTarget, TaskKind } from './lib/types';
  import { videoModelShortName } from './lib/videoEngines';

  export let view: EnhancePanelView;
  export let actions: EnhancePanelActions;
  export let editPrompt: string;
  export let recipeName: string;
  export let recipeEditorOpen: boolean;
  export let deletingRecipeId: string | null;
  export let termsAccepted: Record<string, boolean>;
  export let inspectorScroll: HTMLDivElement | undefined = undefined;
  export let failureNote: HTMLElement | undefined = undefined;

  const lockedTitle = 'Finish or cancel the run to change settings.';
  let recipesOpen = false;
  let planOpen = false;
  let outputOpen = false;
  let moreFixesOpen = false;

  const {
    setTask,
    updateSettings,
    openRecipeEditor,
    applyPreset,
    deleteRecipe,
    applyRecipe,
    addRecipe,
    setContent,
    radioGroupKeydown,
    toggleFix,
    setSize,
    openLibrary,
    moveFix,
    chooseCustomModel,
    changeCheckpointConsent,
    openAnyLicense,
    openAnySource,
    openCustomEdit,
    runDownload,
    retryPlan,
    chooseEditRuntime,
    retryOnCpu,
    chooseModelFolder,
    resetModelFolder,
    chooseInAdvanced,
    useDefaultsForSession,
    chooseOutput,
    openSettings,
    refreshCapabilities,
  } = actions;

  $: ({
    page,
    snapshot,
    settings,
    defaultSettings,
    selectedMedia,
    settingsLocked,
    movingModels,
    lockReason,
    activeQuality,
    restoring,
    usingTemporalVideo,
    videoTask,
    selectedModel,
    selectedVideoModel,
    editModel,
    editFit,
    editRuntime,
    editingDevice,
    editSupported,
    editBlockReason,
    preservingHdr,
    hdrPreservationAvailable,
    hdrModelCompatible,
    audioDisabled,
    audioModels,
    selectedAudioModel,
    quickDownload,
    bestDownload,
    activeRecipeIds,
    sizeResult,
    sizeMaxScale,
    bestModel,
    imageFixes,
    noRestoreModel,
    planState,
    plan,
    videoAvailable,
    activeDevice,
    downloadMap,
    canOpenTrials,
    canTryOthers,
    customModelNeedsOptIn,
    licenseReviewModel,
    preparing,
    hasNothingToRun,
    nothingToRunReason,
    temporalUnavailableReason,
    fixPlanSupported,
    downloads,
    flashVideo,
    videoMemoryEngine,
    selectedMemory,
    outputDimensions,
    lastFailure,
    acceleratorFailure,
    interpolationSupported,
  } = view);

  $: if (!recipesOpen) {
    deletingRecipeId = null;
    recipeEditorOpen = false;
  }
  $: if (!settings.task || settings.task === 'edit' || planState.status === 'error') {
    planOpen = false;
  }
  $: if (!settings.task || settings.task === 'edit') {
    outputOpen = false;
  }
  $: outputLineText = outputSummary(settings, { preservingHdr, audioDisabled });
  $: facts = mediaFacts(selectedMedia);

  $: taskTip =
    selectedMedia?.kind === 'video' || settings.task === 'video'
      ? 'Upscale & restore enlarges videos, fixes them, or both. Edit is for photos.'
      : 'Upscale & restore enlarges photos and artwork, fixes them, or both. Edit changes a photo from a description in words.';

  let planDimmed = false;
  let planDimTimer: ReturnType<typeof setTimeout> | undefined;
  $: {
    clearTimeout(planDimTimer);
    planDimTimer = undefined;
    if (planState.status === 'loading' && !planState.first) {
      planDimTimer = setTimeout(() => {
        planDimmed = true;
      }, 400);
    } else {
      planDimmed = false;
    }
  }
  onDestroy(() => clearTimeout(planDimTimer));

  function stageTarget(stage: PlanStage) {
    return (
      stage.model ??
      stage.videoModel ??
      stage.interpolationModel ??
      stage.audioModel ??
      stage.editModel
    );
  }

  function stageName(stage: PlanStage): string {
    if (stage.model) return displayName(stage.model);
    if (stage.videoModel) return videoModelShortName(stage.videoModel);
    if (stage.interpolationModel) return stage.interpolationModel.name.split(' — ')[0];
    if (stage.audioModel) return stage.audioModel.name.split(' — ')[0];
    if (stage.editModel) return editFamilyLabel(stage.editModel.family);
    if (stage.missing) return 'No model for this fix';
    if (stage.custom !== undefined) {
      return (
        customModels(settings).find((model) => model.path === settings.custom_model_path)?.name ??
        'Checkpoint'
      );
    }
    return '';
  }

  function needsYouStep(stage: PlanStage): string {
    const isVideo = selectedMedia?.kind === 'video' || settings.task === 'video';
    if (stage.role === 'fix' || (stage.role === 'primary' && stage.fix)) {
      if (stage.fix === 'blur') return 'Blur';
      if (stage.fix === 'jpeg') return isVideo ? 'Compression' : 'JPEG';
      if (stage.fix === 'noise') return 'Noise';
      if (stage.fix === 'colour') return 'Colour';
      return stage.label;
    }
    if (stage.kind === 'upscale' || stage.kind === 'video') return 'Upscale';
    if (stage.kind === 'restore') return 'Fix';
    if (stage.kind === 'edit') return 'Edit';
    if (stage.kind === 'interpolate') return 'Frames';
    if (stage.kind === 'audio_denoise') return 'Clean audio';
    if (stage.kind === 'face_restore') return 'Faces';
    return stage.label;
  }

  function stageNeedsUser(stage: PlanStage): boolean {
    if (stage.custom !== undefined) return true;
    if (stage.model && licenseReviewModel?.model_id === stage.model.model_id && !stage.installed) {
      return true;
    }
    if (stage.editModel?.model_id === '__custom__' && !stage.installed) return true;
    const target = stageTarget(stage);
    const stageDownload = target ? downloadMap.get(target.model_id) : undefined;
    if (
      target &&
      !stage.installed &&
      (Boolean((stage.model ?? stage.editModel)?.terms_acceptance_required) ||
        !stage.canDownload ||
        Boolean(stageDownload))
    ) {
      return true;
    }
    return Boolean(
      settings.task === 'edit' &&
      stage.kind === 'edit' &&
      stage.installed &&
      editModel?.terms_acceptance_required,
    );
  }

  $: planSteps = planLine(plan, settings, selectedMedia);
  $: planLineText =
    planState.status === 'loading' && planState.first
      ? 'Working out the plan…'
      : planSteps.length > 0
        ? planSteps.join(' → ')
        : 'Nothing to run';
  $: planLineLabel =
    planState.status === 'loading' && planState.first
      ? 'Working out the plan…'
      : planSteps.length > 0
        ? `Plan: ${planSteps.join(', ')}`
        : 'Plan: Nothing to run';

  // Quick and Best describe the result; the model behind them is named once, under Model.
  function qualityBlurb(
    quality: 'quick' | 'best',
    isRestoring = restoring,
    task: TaskKind | '' = settings.task,
  ): string {
    if (isRestoring) return quality === 'quick' ? 'Fast clean-up' : 'Most careful, slower';
    if (task === 'video')
      return quality === 'quick' ? 'Fast, frame by frame' : 'More detail, slower';
    return quality === 'quick' ? 'Fast, clean result' : 'Finest detail, slower';
  }
</script>

<aside class="enhance-pane pane" class:compact-hidden={page !== 'enhance'}>
  <div class="pane-heading" data-tauri-drag-region>
    <h1 data-tauri-drag-region>Enhance</h1>
    {#if movingModels}
      <span data-tauri-drag-region>Moving models</span>
    {:else if settingsLocked}
      <span data-tauri-drag-region title={lockedTitle}>Locked while running</span>
    {:else}
      <Popover
        bind:open={recipesOpen}
        label="Recipes"
        width={260}
        triggerClass="link-button recipes-trigger"
        let:close
      >
        <svelte:fragment slot="trigger">
          Recipes<svg
            class="pop-chevron"
            aria-hidden="true"
            xmlns="http://www.w3.org/2000/svg"
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.25"
            stroke-linecap="round"
            stroke-linejoin="round"><path d="m6 9 6 6 6-6" /></svg
          >
        </svelte:fragment>
        <RecipesMenu
          recipes={snapshot.recipes}
          {activeRecipeIds}
          selectedMediaKind={selectedMedia?.kind}
          canSave={Boolean(settings.task)}
          bind:recipeName
          bind:recipeEditorOpen
          bind:deletingRecipeId
          {openRecipeEditor}
          {applyRecipe}
          {deleteRecipe}
          {addRecipe}
          onClose={() => close(false)}
        />
      </Popover>
    {/if}
  </div>
  <!-- Locked: the header says so once, and the dimmed controls show the rest. Screen readers
       hear the explanation when the lock starts, and it describes the settings region. -->
  {#if settingsLocked}<span id="settings-lock-reason" class="visually-hidden" role="status"
      >{lockReason}</span
    >{/if}
  <div
    bind:this={inspectorScroll}
    class="inspector-scroll"
    role="region"
    aria-label="Enhancement settings"
    aria-describedby={settingsLocked ? 'settings-lock-reason' : undefined}
  >
    <fieldset
      class="processing-settings"
      disabled={settingsLocked}
      aria-label="Processing settings"
    >
      <section class="control-section">
        <div class="panel-row">
          <div class="panel-key">
            <span class="panel-label">Task</span><HelpTip label="Task" text={taskTip} />
          </div>
          <div class="seg-inline task-switch" role="group" aria-label="Task">
            <button
              aria-pressed={Boolean(settings.task) && settings.task !== 'edit'}
              class:active={Boolean(settings.task) && settings.task !== 'edit'}
              on:click={() => {
                // A video has this one task; pressing it again must not reset the setup.
                if (settings.task !== 'video')
                  setTask(settings.output_scale === 1 ? 'denoise' : 'upscale');
              }}>Upscale & restore</button
            >
            <button
              aria-pressed={settings.task === 'edit'}
              disabled={selectedMedia?.kind === 'video'}
              class:active={settings.task === 'edit'}
              on:click={() => setTask('edit')}>Edit</button
            >
          </div>
        </div>
      </section>

      {#if settings.task}
        {#if settings.task === 'edit'}
          <section class="control-section" aria-label="Edit prompt">
            <span class="eyebrow">What to change</span>
            <textarea
              class="edit-prompt"
              aria-label="What to change"
              rows="4"
              maxlength="4000"
              placeholder="Replace the background with a sunset beach. Keep the person and their clothing unchanged."
              bind:value={editPrompt}></textarea>
          </section>
        {:else}
          <section class="control-section recipes">
            <span class="eyebrow">Quality</span>
            <div class="recipe-grid" role="group" aria-label="Quality">
              <button
                class="quick"
                class:active={activeQuality === 'quick'}
                aria-pressed={activeQuality === 'quick'}
                on:click={() => applyPreset('quick')}
                ><b>Quick</b><span>{qualityBlurb('quick', restoring, settings.task)}</span
                >{#if quickDownload}<small>{quickDownload}</small>{/if}</button
              >
              <button
                class="best"
                class:active={activeQuality === 'best'}
                aria-pressed={activeQuality === 'best'}
                on:click={() => applyPreset('best')}
                ><b>Best</b><span>{qualityBlurb('best', restoring, settings.task)}</span
                >{#if bestDownload}<small>{bestDownload}</small>{/if}</button
              >
            </div>
          </section>
        {/if}

        {#if !usingTemporalVideo && settings.task !== 'edit'}
          <section
            class="control-section"
            aria-label={settings.task === 'video' ? 'Your video' : 'Your photo'}
          >
            <div class="panel-head">
              <span class="panel-head-title"
                >{settings.task === 'video' ? 'Your video' : 'Your photo'}</span
              >
              {#if facts}<span class="panel-head-value">{facts}</span>{/if}
            </div>
            <div
              class="panel-row"
              title={restoring
                ? 'Content chooses the upscaler. Nothing is enlarged at Same size.'
                : undefined}
            >
              <div class="panel-key">
                <span class="panel-label" id="content-label">Content</span><HelpTip
                  label="Content"
                  text={settings.task === 'video'
                    ? 'Video picks models trained on camera footage: skin, foliage, fabric, buildings. Animation picks models for drawings, anime and cartoons, which keep lines clean and colours flat. Choose what the video is, not the look you want.'
                    : 'Photo picks models trained on camera pictures: skin, foliage, fabric, buildings. Illustration picks models for drawings, anime and comics, which keep lines clean and colours flat. Choose what the picture is, not the look you want.'}
                />
              </div>
              <div
                class="seg-inline"
                role="radiogroup"
                aria-labelledby="content-label"
                title={restoring
                  ? 'Content chooses the upscaler. Nothing is enlarged at Same size.'
                  : undefined}
              >
                <button
                  role="radio"
                  aria-checked={settings.content !== 'illustration'}
                  class:active={settings.content !== 'illustration'}
                  disabled={restoring}
                  title={restoring
                    ? 'Content chooses the upscaler. Nothing is enlarged at Same size.'
                    : settings.task === 'video'
                      ? 'Uses a model made for camera footage'
                      : 'Uses a model made for photographs'}
                  on:keydown={radioGroupKeydown}
                  on:click={() => setContent('photo')}
                  >{settings.task === 'video' ? 'Video' : 'Photo'}</button
                >
                <button
                  role="radio"
                  aria-checked={settings.content === 'illustration'}
                  class:active={settings.content === 'illustration'}
                  disabled={restoring}
                  title={restoring
                    ? 'Content chooses the upscaler. Nothing is enlarged at Same size.'
                    : 'Uses a model made for drawings and anime'}
                  on:keydown={radioGroupKeydown}
                  on:click={() => setContent('illustration')}
                  >{settings.task === 'video' ? 'Animation' : 'Illustration'}</button
                >
              </div>
            </div>
          </section>

          <section class="control-section">
            <FixList
              {settings}
              mediaKind={settings.task === 'video' ? 'video' : 'image'}
              features={snapshot.engine?.features ?? []}
              {plan}
              models={snapshot.catalog.models}
              {downloadMap}
              {settingsLocked}
              {toggleFix}
              {updateSettings}
              bind:moreOpen={moreFixesOpen}
            />
          </section>

          <section class="control-section">
            <div class="field-heading">
              <span class="field-label" id="size-label">Size</span><HelpTip
                label="Size"
                text={settings.task === 'video'
                  ? `Same size keeps the resolution and runs only the fixes you picked. With nothing picked, only De-flicker, Frame rate and Clean up audio run. 2× to 4× enlarges the result after the fixes. A 2× or 3× choice on a ${sizeMaxScale}× model resizes down afterwards.`
                  : `Same size keeps the pixel size and runs only the fixes you picked. 2× to 4× enlarges the result after the fixes. The model always works at its own ${sizeMaxScale}× scale; a smaller choice resizes that result down afterwards, so it saves no time.`}
              />{#if sizeResult}<span class="field-value">{sizeResult}</span>{/if}
            </div>
            <div class="seg-inline size-switch" role="radiogroup" aria-labelledby="size-label">
              {#each [1, 2, 3, 4] as const as scale (scale)}
                {@const active = (restoring ? 1 : settings.output_scale) === scale}
                <button
                  role="radio"
                  aria-checked={active}
                  class:active
                  disabled={scale > sizeMaxScale}
                  title={scale > sizeMaxScale
                    ? `This model enlarges up to ${sizeMaxScale}×.`
                    : undefined}
                  on:keydown={radioGroupKeydown}
                  on:click={() => setSize(scale)}>{scale === 1 ? 'Same size' : `${scale}×`}</button
                >
              {/each}
            </div>
          </section>
        {/if}

        {#if settings.task === 'edit'}
          <section class="control-section model-section" aria-label="Model plan">
            <div class="eyebrow eyebrow-row">
              <span>Model</span>
              <span class="eyebrow-actions">
                {#if !noRestoreModel && !plan.some((stage) => stage.role === 'primary')}<button
                    class="link-button"
                    type="button"
                    on:click={() => openLibrary('edit')}>Change…</button
                  >{/if}
              </span>
            </div>
            <div
              class="plan"
              role="list"
              aria-label="Stages"
              aria-busy={planState.status === 'loading' && !planState.first ? 'true' : undefined}
            >
              {#each plan as stage (`${stage.role}:${stage.fix ?? stage.kind}`)}
                {@const name = stage.editModel
                  ? editFamilyLabel(stage.editModel.family)
                  : stage.custom !== undefined
                    ? (customModels(settings).find(
                        (model) => model.path === settings.custom_model_path,
                      )?.name ?? 'Checkpoint')
                    : ''}
                {@const meta = stage.editModel
                  ? `${editVariantName(stage.editModel)}${
                      editFit !== 'unknown' && editFit !== 'runs'
                        ? ` · ${EDIT_FIT_LABELS[editFit]}`
                        : ''
                    }`
                  : ''}
                <div class="plan-row" role="listitem">
                  <span class="stage">{stage.label}</span>
                  <div class="name" title={name || undefined}>{name}</div>
                  <div class="plan-end">
                    <span class="state" class:warn={stage.needsFile}
                      >{stage.needsFile && !stage.installed ? 'Needs your file' : ''}</span
                    >
                    {#if stage.editModel}<button
                        class="link-button"
                        type="button"
                        aria-label="Change the edit model"
                        on:click={() => openLibrary('edit')}>Change…</button
                      >{/if}
                  </div>
                  {#if meta || stage.custom !== undefined}<p
                      class="plan-note"
                      title={(stage.custom !== undefined ? settings.custom_model_path : meta) ||
                        undefined}
                    >
                      {#if stage.custom !== undefined}<span class="path-text"
                          >{settings.custom_model_path ||
                            'Safetensors is strongly recommended.'}</span
                        >{:else}{meta}{/if}
                    </p>{/if}
                </div>
              {/each}
            </div>
          </section>
        {:else if activeQuality === 'custom' || usingTemporalVideo || (planState.status === 'ready' && !noRestoreModel && !plan.some((stage) => stage.role === 'primary'))}
          <section class="control-section model-section" aria-label="Model plan">
            <div class="eyebrow eyebrow-row">
              <span>{activeQuality === 'custom' ? 'Model · chosen by you' : 'Model'}</span>
              <span class="eyebrow-actions">
                {#if activeQuality === 'custom' && (bestModel || (settings.task === 'video' && restoring && imageFixes.length > 0))}<button
                    class="link-button"
                    type="button"
                    on:click={() => applyPreset('best')}>Back to Best</button
                  >{/if}
                {#if !noRestoreModel && !plan.some((stage) => stage.role === 'primary')}<button
                    class="link-button"
                    type="button"
                    on:click={() => openLibrary('primary')}>Change…</button
                  >{/if}
              </span>
            </div>
            {#if activeQuality === 'custom'}
              <p class="model-note">
                {usingTemporalVideo
                  ? 'Chosen by you. Fix, Same size, De-flicker and Frame rate need a frame-by-frame model: pick Quick or Best to go back.'
                  : 'Chosen by you. Pick Quick or Best to go back.'}
              </p>
            {/if}
            {#if usingTemporalVideo}
              <!-- The FlashVSR panel describes this Mac; where FlashVSR cannot run, the line
                 under the engine menu says why instead. -->
              {#if !(flashVideo && temporalUnavailableReason)}
                <VideoMemory
                  model={selectedVideoModel}
                  memoryEngine={videoMemoryEngine}
                  lowMemory={settings.video_low_memory ?? true}
                  device={snapshot.capabilities.devices.find(
                    (device) => device.id === settings.device_id,
                  )}
                  memory={selectedMemory}
                  running={Boolean(
                    selectedMemory && snapshot.runtime.active_job_id === selectedMemory.job_id,
                  )}
                  {outputDimensions}
                  disabled={Boolean(snapshot.runtime.active_job_id)}
                  on:change={(event) => updateSettings({ video_low_memory: event.detail })}
                />
              {/if}
              <div class="field-row">
                <label for="video-resolution">Output resolution</label><select
                  id="video-resolution"
                  value={settings.video_target_resolution
                    ? String(settings.video_target_resolution)
                    : `x${Math.min(4, Math.max(2, settings.output_scale))}`}
                  on:change={(event) => {
                    const chosen = event.currentTarget.value;
                    if (chosen.startsWith('x')) {
                      updateSettings({
                        output_scale: Number(chosen.slice(1)),
                        video_target_resolution: 0,
                      });
                    } else {
                      updateSettings({
                        video_target_resolution: Number(chosen),
                      });
                    }
                  }}
                  >{#each [2, 3, 4] as scale}<option value={`x${scale}`}
                      >{selectedMedia && selectedMedia.width > 0 && selectedMedia.height > 0
                        ? `${scale}× · ${selectedMedia.width * scale} × ${selectedMedia.height * scale}`
                        : `${scale}×`}</option
                    >{/each}{#each [256, 512, 720, 1080, 1440, 2160] as resolution}<option
                      value={String(resolution)}>{resolution} px · shorter edge</option
                    >{/each}</select
                >
              </div>
              <p class="model-description">
                {#if flashVideo}
                  Smaller output takes less time. For a first test, try 256 or 512 px. FlashVSR
                  works best up to 4× the source: above that, it runs at 4× and resizes the rest.
                {:else}
                  Smaller output uses less memory. For a first test, try 256 or 512 px. This can
                  reduce the size of a large source video.
                {/if}
              </p>
              {#if selectedMedia && settings.video_target_resolution && settings.video_target_resolution < Math.min(selectedMedia.width, selectedMedia.height)}
                <p class="notice">
                  This setting reduces {selectedMedia.width} × {selectedMedia.height} to {outputDimensions}.
                  Fine detail will be lost. Choose a larger output for a quality comparison.
                </p>
              {/if}
            {/if}
          </section>
        {/if}

        {#if settings.task === 'video' && !usingTemporalVideo}
          <section class="control-section">
            <div class="advanced-controls">
              <div class="field-row">
                <span class="label-with-tip"
                  ><label for="frame-rate">Frame rate</label><HelpTip
                    label="Frame rate"
                    text="Adds in-between frames for smoother motion. 2× and 4× multiply the clip's own rate; 60 and 120 fps set a fixed rate and are skipped when the clip is already about that fast. The first use downloads a model of about 25 MB."
                  /></span
                ><select
                  id="frame-rate"
                  disabled={preservingHdr || !interpolationSupported}
                  value={settings.video_interpolation ?? 'off'}
                  on:change={(event) =>
                    updateSettings({
                      video_interpolation: event.currentTarget.value as InterpolationTarget,
                    })}
                  ><option value="off">Original</option><option value="x2">2× · interpolated</option
                  ><option value="x4">4× · interpolated</option><option value="fps60"
                    >60 fps · interpolated</option
                  ><option value="fps120">120 fps · interpolated</option></select
                >
              </div>
              {#if (settings.video_interpolation ?? 'off') !== 'off'}
                <div class="field-row">
                  <span class="label-with-tip"
                    ><label for="interpolation-order">Interpolate</label><HelpTip
                      label="Interpolate"
                      text="When the extra frames are made. After upscaling is faster; before upscaling can look smoother but upscales more frames."
                    /></span
                  ><select
                    id="interpolation-order"
                    value={settings.video_interpolation_order ?? 'after_upscale'}
                    on:change={(event) =>
                      updateSettings({
                        video_interpolation_order: event.currentTarget.value as InterpolationOrder,
                      })}
                    ><option value="after_upscale">After upscaling · faster</option><option
                      value="before_upscale">Before upscaling</option
                    ></select
                  >
                </div>
                <p class="model-description">
                  RIFE · Labs adds the in-between frames. Scene cuts stay cuts; very fast motion can
                  smear. Before upscaling, the added frames are upscaled too: at 2× that takes about
                  twice as long.
                </p>
              {/if}
            </div>
          </section>
        {/if}

        {#if lastFailure}
          <section
            class="control-section failure-note"
            role="alert"
            aria-label="Last error"
            tabindex="-1"
            bind:this={failureNote}
          >
            <p>{lastFailure}</p>
            {#if acceleratorFailure}
              <button class="button" type="button" on:click={() => void retryOnCpu()}
                >Retry on CPU</button
              >
            {/if}
          </section>
        {/if}
        {#if snapshot.model_directory_missing}
          <section
            class="control-section failure-note"
            role="alert"
            aria-label="Model folder unavailable"
          >
            <p>
              Model folder not found: {settings.model_directory}. Reconnect the drive or choose
              another folder.
            </p>
            <div class="eyebrow-actions">
              <button type="button" class="button" on:click={() => void chooseModelFolder()}
                >Choose folder…</button
              >
              <button type="button" class="button" on:click={() => void resetModelFolder()}
                >Use default folder</button
              >
            </div>
          </section>
        {/if}
        {#if settings.task === 'edit'}
          <AdvancedSettings
            {settings}
            {editModel}
            capabilities={snapshot.capabilities}
            updateSettings={chooseInAdvanced}
            defaults={defaultSettings}
            onUseDefaults={useDefaultsForSession}
            {chooseOutput}
            {refreshCapabilities}
          />
        {/if}
      {/if}
    </fieldset>
  </div>
  {#if settings.task}
    <div class="panel-foot">
      {#each plan as stage (`${stage.role}:${stage.fix ?? stage.kind}`)}
        {#if stageNeedsUser(stage)}
          {@const target = stageTarget(stage)}
          {@const name = stageName(stage)}
          {@const head = name ? `${needsYouStep(stage)} · ${name}` : needsYouStep(stage)}
          {@const stageDownload = target ? downloadMap.get(target.model_id) : undefined}
          <div class="needs-you">
            <p class="needs-you-head">{head}</p>
            {#if stage.custom !== undefined}
              <button class="button full" disabled={settingsLocked} on:click={chooseCustomModel}
                >{settings.custom_model_path
                  ? 'Choose another checkpoint…'
                  : 'Choose checkpoint…'}</button
              >
              {#if customModelNeedsOptIn}
                <label class="terms"
                  ><input
                    type="checkbox"
                    disabled={settingsLocked}
                    checked={settings.allow_unsafe_pickle_model}
                    on:change={(event) => void changeCheckpointConsent(event.currentTarget.checked)}
                  /> Allow this pickle-based checkpoint. It may execute code inside the inference worker.</label
                >
              {/if}
            {:else if stage.model && licenseReviewModel?.model_id === stage.model.model_id && !stage.installed}
              <ModelCredit
                model={stage.model}
                openLicense={() => openAnyLicense(stage.model!.model_id)}
                openSource={() => openAnySource(stage.model!.model_id)}
              />
            {:else if stage.editModel?.model_id === '__custom__' && !stage.installed}
              <button
                class="button full"
                type="button"
                disabled={settingsLocked}
                on:click={openCustomEdit}>Choose edit model files…</button
              >
            {:else if target && !stage.installed}
              {#if (stage.model ?? stage.editModel)?.terms_acceptance_required}
                <label class="terms"
                  ><input
                    type="checkbox"
                    disabled={settingsLocked}
                    bind:checked={termsAccepted[target.model_id]}
                  /> I reviewed the model license and restrictions.</label
                >
              {/if}
              {#if !stage.canDownload || target.terms_acceptance_required || Boolean(stageDownload)}
                <button
                  class="button full"
                  disabled={settingsLocked ||
                    (stage.videoModel && Boolean(temporalUnavailableReason)) ||
                    preparing}
                  on:click={() => runDownload(target)}
                >
                  {stageDownload
                    ? stageDownload.status === 'queued'
                      ? 'Waiting in queue · Cancel'
                      : `Downloading ${Math.round(stageDownload.progress)}% · Cancel`
                    : stage.canDownload
                      ? `Download model · ${formatBytes(stage.sizeBytes)}`
                      : 'Choose externally downloaded checkpoint…'}
                </button>
              {/if}
              {#if stageDownload?.status === 'downloading'}<progress
                  class="download-progress"
                  value={stageDownload.progress}
                  max="100"
                  aria-label={`${name} download progress`}
                ></progress>{/if}
            {:else if settings.task === 'edit' && stage.kind === 'edit' && stage.installed && editModel?.terms_acceptance_required}
              <label class="terms"
                ><input
                  type="checkbox"
                  disabled={settingsLocked}
                  bind:checked={termsAccepted[editModel.model_id]}
                /> I reviewed the Qwen Research License and accept it for this edit.</label
              >
            {/if}
          </div>
        {/if}
      {/each}
      {#if hasNothingToRun}
        <p class="inline-warning" role="status">{nothingToRunReason}</p>
      {/if}
      {#if usingTemporalVideo && temporalUnavailableReason}
        <p class="inline-warning" role="status">{temporalUnavailableReason}</p>
      {/if}
      {#if settings.task === 'video' && !restoring && selectedMedia?.hdr_format && !hdrPreservationAvailable}
        <p class="inline-warning" role="status">
          SDR output selected. This model cannot preserve HDR.
        </p>
      {/if}
      {#if settings.task === 'video' && selectedMedia?.hdr_format && preservingHdr && !hdrModelCompatible}
        <p class="inline-warning" role="status">Choose a HAT model for HDR preservation.</p>
      {/if}
      {#if !fixPlanSupported && snapshot.engine}
        <p class="inline-warning" role="status">
          This engine runs one fix at a time. Update LocalSR to run several.
        </p>
      {/if}
      {#if settings.task === 'edit' && editBlockReason}
        <p class="inline-warning" role="status">
          {editBlockReason}
          {#if !editRuntime && editingDevice && editSupported}<button
              class="link-button"
              type="button"
              disabled={settingsLocked}
              on:click={chooseEditRuntime}>Choose runtime…</button
            >{/if}
        </p>
      {/if}
      {#if planState.status === 'error'}
        <p class="inline-warning" role="status" title={planState.message}>
          Could not work out the plan.
          <button class="link-button" type="button" on:click={retryPlan}>Try again</button>
        </p>
      {/if}
      {#if settings.task !== 'edit' && planState.status !== 'error'}
        <Popover
          bind:open={planOpen}
          placement="sheet"
          label="Plan"
          triggerClass={`foot-line foot-plan foot-trigger${
            planState.status === 'loading' && planState.first ? ' muted' : ''
          }${planDimmed ? ' dimmed' : ''}`}
          triggerLabel={planLineLabel}
          triggerTitle={planState.status === 'loading' && planState.first
            ? undefined
            : planLineText}
          triggerBusy={planState.status === 'loading' && !planState.first}
          let:close
        >
          <svelte:fragment slot="trigger">
            <span class="foot-plan-text">{planLineText}</span>
            <svg
              class="pop-chevron"
              aria-hidden="true"
              xmlns="http://www.w3.org/2000/svg"
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.25"
              stroke-linecap="round"
              stroke-linejoin="round"><path d={planOpen ? 'm6 9 6 6 6-6' : 'm9 6 6 6-6 6'} /></svg
            >
          </svelte:fragment>
          <PlanSheet
            {snapshot}
            {settings}
            {defaultSettings}
            {settingsLocked}
            {restoring}
            {usingTemporalVideo}
            {videoTask}
            {selectedModel}
            {selectedMedia}
            {editFit}
            {imageFixes}
            {planState}
            {plan}
            {videoAvailable}
            {activeDevice}
            {downloadMap}
            {canOpenTrials}
            {canTryOthers}
            {updateSettings}
            {chooseInAdvanced}
            {useDefaultsForSession}
            {openLibrary}
            {moveFix}
            {refreshCapabilities}
          />
        </Popover>
      {/if}
      {#if settings.task !== 'edit'}
        <Popover
          bind:open={outputOpen}
          placement="sheet"
          label="Output"
          triggerClass="foot-line foot-output foot-trigger"
          triggerTitle={outputLineText}
          let:close
        >
          <svelte:fragment slot="trigger">
            <span class="foot-output-key">Output</span>
            <span class="foot-output-value">{outputLineText}</span>
            <svg
              class="pop-chevron"
              aria-hidden="true"
              xmlns="http://www.w3.org/2000/svg"
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.25"
              stroke-linecap="round"
              stroke-linejoin="round"><path d={outputOpen ? 'm6 9 6 6 6-6' : 'm9 6 6 6-6 6'} /></svg
            >
          </svelte:fragment>
          <OutputSheet
            {settings}
            {selectedMedia}
            capabilities={snapshot.capabilities}
            {usingTemporalVideo}
            {preservingHdr}
            {hdrPreservationAvailable}
            {audioDisabled}
            {audioModels}
            {selectedAudioModel}
            disabled={settingsLocked}
            {updateSettings}
            {chooseOutput}
            onOpenFfmpegSettings={() => openSettings('storage', 'ffmpeg')}
          />
        </Popover>
      {/if}
      {#if downloads.stages.length}
        <div class="foot-line foot-cost">
          <span class="foot-cost-download"
            >{formatBytes(downloads.bytes)} to download<HelpTip
              label="Downloads"
              text={`Models download once when you start and stay on this computer. Your ${
                settings.task === 'video' ? 'videos' : 'photos'
              } never leave it.`}
            /></span
          >
        </div>
      {/if}
    </div>
  {/if}
</aside>
