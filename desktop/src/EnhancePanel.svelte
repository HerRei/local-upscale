<script lang="ts">
  import { onDestroy } from 'svelte';
  import AdvancedSettings from './AdvancedSettings.svelte';
  import FixList from './FixList.svelte';
  import HelpTip from './HelpTip.svelte';
  import ModelCredit from './ModelCredit.svelte';
  import OutputSheet from './OutputSheet.svelte';
  import PlanSheet from './PlanSheet.svelte';
  import Popover from './Popover.svelte';
  import PromptExamples from './PromptExamples.svelte';
  import RecipesMenu from './RecipesMenu.svelte';
  import VideoMemory from './VideoMemory.svelte';
  import { customModels } from './lib/customModels';
  import {
    EDIT_FIT_LABELS,
    editExpert,
    editExpertChanges,
    editFamilyLabel,
    editVariantName,
  } from './lib/editing';
  import {
    mediaFacts,
    outputSummary,
    outputTitle,
    type EnhancePanelActions,
    type EnhancePanelView,
  } from './lib/enhancePanel';
  import { formatEta } from './lib/eta-display';
  import { formatBytes } from './lib/format';
  import { displayName, fitFor, FIT_LABELS, resultFps, type PlanStage } from './lib/plan';
  import { planMarks } from './lib/jobSteps';
  import { planLine } from './lib/planLine';
  import {
    BACKGROUND_STEP,
    backgroundModel,
    backgroundRemoved,
    backgroundRowShown,
    SCAN_STEP,
    scanChoice,
    scanOptions,
    scanRate,
    scanRowShown,
    scanStage,
    withStepStage,
    type ScanChoice,
  } from './lib/stepRows';
  import { examplesFor } from './lib/promptExamples';
  import type { InterpolationOrder, InterpolationTarget, TaskKind } from './lib/types';
  import { videoModelShortName } from './lib/videoEngines';
  import { WORKFLOWS as workflows, workflowMatches, workflowPlan } from './lib/workflows';

  export let view: EnhancePanelView;
  export let actions: EnhancePanelActions;
  export let editPrompt: string;
  let promptField: HTMLTextAreaElement | undefined;
  let examplesOpen = false;
  $: promptExamples = examplesFor(editModel?.family);
  export let recipeName: string;
  export let recipeEditorOpen: boolean;
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
    openRecipesLibrary,
    applyPreset,
    applyRecipe,
    addRecipe,
    updateRecipe,
    applyWorkflow,
    setContent,
    radioGroupKeydown,
    toggleFix,
    seeFixOnPhoto,
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
    updatableRecipe,
    sizeResult,
    sizeMaxScale,
    imageFixes,
    noRestoreModel,
    planState,
    plan,
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
    patchLink,
    startSeconds,
  } = view);

  $: if (!recipesOpen) {
    recipeEditorOpen = false;
  }
  $: if (!settings.task || settings.task === 'edit' || planState.status === 'error') {
    planOpen = false;
  }
  $: if (!settings.task || settings.task === 'edit') {
    outputOpen = false;
  }
  $: outputLineText = outputSummary(settings, { preservingHdr, audioDisabled });
  $: outputLineTitle = outputTitle(settings, { preservingHdr, audioDisabled });
  $: engineFeatures = snapshot.engine?.features ?? [];
  $: cutoutModel = backgroundModel(snapshot.catalog.models);
  $: cutoutRemoved = backgroundRemoved(settings);
  $: cutoutShown = backgroundRowShown({
    task: settings.task,
    features: engineFeatures,
    model: cutoutModel,
    removed: cutoutRemoved,
  });
  $: cutoutSupported = engineFeatures.includes('image_background_removal');
  $: cutoutDownload =
    cutoutModel && !cutoutModel.installed ? formatBytes(cutoutModel.size_bytes) : '';
  function setBackground(remove: boolean) {
    updateSettings({
      step_stages: withStepStage(
        settings.step_stages,
        BACKGROUND_STEP,
        remove && cutoutModel ? { step: BACKGROUND_STEP, model_id: cutoutModel.model_id } : null,
      ),
    });
  }
  $: fieldOrder = selectedMedia?.field_order;
  $: scanSelected = scanChoice(settings);
  $: scanShown = scanRowShown({
    task: settings.task,
    usingTemporalVideo,
    features: engineFeatures,
    fieldOrder,
    choice: scanSelected,
  });
  $: scanChoices = scanOptions(fieldOrder, selectedMedia?.fps ?? 0, scanSelected);
  $: scanSupported = engineFeatures.includes('video_deinterlace');
  $: scanTitle = !scanSupported
    ? 'This engine cannot deinterlace. Update LocalSR.'
    : scanSelected.endsWith(':auto') &&
        scanSelected !== 'frame:auto' &&
        fieldOrder === 'progressive'
      ? 'This file says it is not interlaced, so it is left as it is. To deinterlace it anyway, set the field order by hand.'
      : undefined;
  function setScan(choice: ScanChoice) {
    updateSettings({
      step_stages: withStepStage(settings.step_stages, SCAN_STEP, scanStage(choice)),
    });
  }
  $: expertChanges = editExpertChanges(
    editExpert(settings, editModel, editingDevice, snapshot.engine?.features ?? []),
  );
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
    const target = stageTarget(stage);
    if (target && downloadMap.has(target.model_id)) return false;
    if (stage.model && licenseReviewModel?.model_id === stage.model.model_id && !stage.installed) {
      return true;
    }
    if (stage.editModel?.model_id === '__custom__' && !stage.installed) return true;
    if (
      target &&
      !stage.installed &&
      (Boolean((stage.model ?? stage.editModel)?.terms_acceptance_required) || !stage.canDownload)
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

  $: needsYouStages =
    (void downloadMap,
    void licenseReviewModel,
    void editModel,
    void settings,
    plan.filter((stage) => stageNeedsUser(stage)));
  $: costBytes = downloads.stages.reduce((total, stage) => {
    const target = stageTarget(stage);
    if (target && downloadMap.has(target.model_id)) return total;
    return (
      total + (target ? (downloads.left[target.model_id] ?? stage.sizeBytes) : stage.sizeBytes)
    );
  }, 0);

  $: costTime = startSeconds === null ? null : formatEta(startSeconds);
  $: planSteps = planLine(plan, settings, selectedMedia);
  $: activeJobMediaId = snapshot.runtime.active_job_id
    ? (snapshot.jobs.find((job) => job.id === snapshot.runtime.active_job_id)?.media_id ?? '')
    : '';
  $: jobForSelected =
    Boolean(snapshot.runtime.active_job_id) &&
    (!activeJobMediaId || selectedMedia?.id === activeJobMediaId);
  $: activePlanMarks =
    settingsLocked && jobForSelected && !(planState.status === 'loading' && planState.first)
      ? planMarks(snapshot.runtime.job_steps ?? [], planSteps)
      : [];
  $: planLineText =
    planState.status === 'loading' && planState.first
      ? 'Working out the plan…'
      : planSteps.length > 0
        ? planSteps.join(' → ')
        : 'Nothing to run';
  $: planLineTitle =
    planState.status === 'loading' && planState.first
      ? undefined
      : activePlanMarks.length > 0
        ? activePlanMarks
            .map((mark) => (mark.state === 'done' ? `✓ ${mark.name}` : mark.name))
            .join(' → ')
        : planLineText;
  $: planLineLabel =
    planState.status === 'loading' && planState.first
      ? 'Working out the plan…'
      : activePlanMarks.length > 0
        ? `Plan: ${activePlanMarks
            .map((mark) =>
              mark.state === 'done'
                ? `${mark.name} done`
                : mark.state === 'running'
                  ? `${mark.name} running`
                  : mark.state === 'skipped'
                    ? `${mark.name} skipped`
                    : mark.name,
            )
            .join(', ')}`
        : planSteps.length > 0
          ? `Plan: ${planSteps.join(', ')}`
          : 'Plan: Nothing to run';

  $: primaryStage = plan.find((stage) => stage.role === 'primary');
  $: primaryName = primaryStage
    ? stageName(primaryStage)
    : usingTemporalVideo && selectedVideoModel
      ? videoModelShortName(selectedVideoModel)
      : selectedModel
        ? displayName(selectedModel)
        : '';
  $: primaryMeta = primaryStage?.model
    ? [
        primaryStage.remembered || activeQuality === 'custom' ? 'your pick' : '',
        primaryStage.model.support_tier === 'labs' ? 'Labs' : '',
        primaryStage.model.commercial_use_allowed === false ? 'Non-commercial' : '',
        ['heavy', 'too_heavy'].includes(fitFor(primaryStage.model, activeDevice))
          ? FIT_LABELS[fitFor(primaryStage.model, activeDevice)]
          : '',
      ]
        .filter(Boolean)
        .join(' · ')
    : primaryStage?.videoModel
      ? 'Labs'
      : '';
  $: fps = selectedMedia?.fps ?? 0;
  $: baseFps = scanRate(scanSelected, fieldOrder, fps);
  $: outputFps =
    settings.task === 'video' && !usingTemporalVideo
      ? settings.video_interpolation && settings.video_interpolation !== 'off'
        ? resultFps(settings.video_interpolation, baseFps)
        : baseFps
      : null;
  function frameLabel(target: Exclude<InterpolationTarget, 'off'>, baseRate: number): string {
    if (target === 'fps60') return '60 fps';
    if (target === 'fps120') return '120 fps';
    const rate = resultFps(target, baseRate);
    return `${target === 'x2' ? '2×' : '4×'}${rate === null ? '' : ` · ${Number(rate.toFixed(3))} fps`}`;
  }
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
          catalog={snapshot.catalog}
          pins={settings.recipe_pins ?? []}
          recent={settings.recipe_recent ?? []}
          {activeRecipeIds}
          selectedMediaKind={selectedMedia?.kind}
          canSave={Boolean(settings.task) && settings.task !== 'edit'}
          bind:recipeName
          bind:recipeEditorOpen
          {openRecipeEditor}
          {openRecipesLibrary}
          {applyRecipe}
          {workflows}
          {applyWorkflow}
          workflowLine={(workflow) =>
            workflowPlan(workflow, snapshot, settings)
              .map((entry) => entry.step)
              .join(' → ')}
          workflowActive={(workflow) => workflowMatches(workflow, snapshot, settings)}
          {addRecipe}
          {updatableRecipe}
          {updateRecipe}
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
            <div class="eyebrow-row">
              <span class="eyebrow">What to change</span>
              {#if promptExamples.length > 0}
                <Popover
                  bind:open={examplesOpen}
                  label="Prompt examples"
                  width={260}
                  triggerClass="link-button recipes-trigger"
                  let:close
                >
                  <svelte:fragment slot="trigger">
                    Examples<svg
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
                  <PromptExamples
                    examples={promptExamples}
                    textarea={promptField}
                    value={editPrompt}
                    onClose={() => close(false)}
                  />
                </Popover>
              {/if}
            </div>
            <textarea
              bind:this={promptField}
              class="edit-prompt"
              aria-label="What to change"
              rows="4"
              maxlength="4000"
              placeholder="Replace the background with a sunset beach. Keep the person and their clothing unchanged."
              bind:value={editPrompt}></textarea>
          </section>
        {/if}

        {#if settings.task !== 'edit'}
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
            {#if !usingTemporalVideo}<div
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
              </div>{/if}
            {#if scanShown}<div class="panel-row">
                <div class="panel-key">
                  <label class="panel-label" for="scan">Scan</label><HelpTip
                    label="Scan"
                    text="Old camcorder, TV and DVD recordings are interlaced: each frame holds two half-pictures taken a moment apart, which shows as fine stripes on anything that moves. Automatic removes the stripes when the file says it is interlaced, and keeps the frame rate. Double rate makes a frame from every half-picture, so motion is as smooth as it was on a TV: 25 fps becomes 50. That is twice the frames to process, so it takes about twice as long. A file that says it is not interlaced is left as it is. Set the field order by hand when stripes remain, or when motion jumps back and forth at the doubled rate: then pick the other order. A hand-set order is applied to every file you start, interlaced or not."
                  />
                </div>
                <select
                  id="scan"
                  title={scanTitle}
                  value={scanSelected}
                  on:change={(event) => setScan(event.currentTarget.value as ScanChoice)}
                  >{#each scanChoices.filter((option) => option.group === '') as option}<option
                      value={option.value}
                      disabled={!scanSupported && option.value !== 'auto'}>{option.label}</option
                    >{/each}<optgroup label="Field order by hand"
                    >{#each scanChoices.filter((option) => option.group === 'hand') as option}<option
                        value={option.value}
                        disabled={!scanSupported}>{option.label}</option
                      >{/each}</optgroup
                  ></select
                >
              </div>{/if}
          </section>

          {#if !usingTemporalVideo}<section class="control-section">
              <FixList
                {settings}
                mediaKind={settings.task === 'video' ? 'video' : 'image'}
                features={snapshot.engine?.features ?? []}
                {plan}
                models={snapshot.catalog.models}
                {downloadMap}
                {settingsLocked}
                {activeDevice}
                {openLibrary}
                {toggleFix}
                {seeFixOnPhoto}
                {radioGroupKeydown}
                {patchLink}
                {updateSettings}
                bind:moreOpen={moreFixesOpen}
              />
            </section>
          {/if}
          <section class="control-section" aria-label="Result">
            <div class="panel-head">
              <span class="panel-head-title">Result</span>{#if sizeResult}<span
                  class="panel-head-value field-value"
                  >{usingTemporalVideo
                    ? outputDimensions.split(' · ')[0]
                    : sizeResult}{outputFps !== null && outputFps !== fps
                    ? ` · ${Number(outputFps.toFixed(3))} fps`
                    : ''}</span
                >{/if}
            </div>
            <div class="panel-row">
              <div class="panel-key">
                <span class="panel-label" id="size-label">Size</span><HelpTip
                  label="Size"
                  text={usingTemporalVideo
                    ? `Smaller output takes less time and memory. For a first test, try 256 or 512 px.${flashVideo ? ' FlashVSR works best up to 4× the source: above that, it runs at 4× and resizes the rest.' : ''}`
                    : settings.task === 'video'
                      ? `Same size keeps the resolution and runs only the fixes you picked. With nothing picked, only De-flicker, Frame rate and Clean up audio run. 2× to 4× enlarges the result after the fixes. A 2× or 3× choice on a ${sizeMaxScale}× model resizes down afterwards.`
                      : `Same size keeps the pixel size and runs only the fixes you picked. 2× to 4× enlarges the result after the fixes. The model always works at its own ${sizeMaxScale}× scale; a smaller choice resizes that result down afterwards, so it saves no time.`}
                />
              </div>
              {#if usingTemporalVideo}
                <select
                  id="video-resolution"
                  aria-label="Output resolution"
                  value={settings.video_target_resolution
                    ? String(settings.video_target_resolution)
                    : `x${Math.min(4, Math.max(2, settings.output_scale))}`}
                  on:change={(event) => {
                    const chosen = event.currentTarget.value;
                    if (chosen.startsWith('x'))
                      updateSettings({
                        output_scale: Number(chosen.slice(1)),
                        video_target_resolution: 0,
                      });
                    else updateSettings({ video_target_resolution: Number(chosen) });
                  }}
                >
                  {#each [2, 3, 4] as scale}<option value={`x${scale}`}
                      >{selectedMedia && selectedMedia.width > 0 && selectedMedia.height > 0
                        ? `${scale}× · ${selectedMedia.width * scale} × ${selectedMedia.height * scale}`
                        : `${scale}×`}</option
                    >{/each}
                  {#each [256, 512, 720, 1080, 1440, 2160] as resolution}<option
                      value={String(resolution)}>{resolution} px · shorter edge</option
                    >{/each}
                </select>
              {:else}
                <div class="seg-inline size-switch" role="radiogroup" aria-labelledby="size-label">
                  {#each [1, 2, 3, 4] as const as scale (scale)}{@const active =
                      (restoring ? 1 : settings.output_scale) === scale}<button
                      role="radio"
                      aria-label={scale === 1 ? 'Same size' : undefined}
                      aria-checked={active}
                      class:active
                      disabled={scale > sizeMaxScale}
                      title={scale === 1
                        ? 'Same size'
                        : scale > sizeMaxScale
                          ? `This model enlarges up to ${sizeMaxScale}×.`
                          : undefined}
                      on:keydown={radioGroupKeydown}
                      on:click={() => setSize(scale)}>{scale === 1 ? 'Same' : `${scale}×`}</button
                    >{/each}
                </div>
              {/if}
            </div>
            <div class="panel-row">
              <div class="panel-key">
                <span class="panel-label">Quality</span><HelpTip
                  label="Quality"
                  text="Quick picks fast models that give a clean result. Best picks the most careful ones and takes longer. Both choose the upscaler and the model for each fix."
                />
              </div>
              <div class="seg-inline" role="group" aria-label="Quality">
                {#each ['quick', 'best'] as quality}<button
                    class:active={activeQuality === quality}
                    aria-pressed={activeQuality === quality}
                    title={`${qualityBlurb(quality as 'quick' | 'best', restoring, settings.task)}${(quality === 'quick' ? quickDownload : bestDownload) ? ` · ${quality === 'quick' ? quickDownload : bestDownload}` : ''}`}
                    on:click={() => applyPreset(quality as 'quick' | 'best')}
                    >{quality === 'quick' ? 'Quick' : 'Best'}</button
                  >{/each}
              </div>
            </div>
            {#if (!restoring || primaryStage?.custom !== undefined) && primaryName}<div
                class="panel-row"
              >
                <span></span>
                <div class="result-model">
                  <span
                    class="result-model-name"
                    title={activeQuality === 'custom'
                      ? usingTemporalVideo
                        ? 'Chosen by you. Fix, Same size, De-flicker and Frame rate need a frame-by-frame model: pick Quick or Best to go back.'
                        : 'Chosen by you. Pick Quick or Best to go back.'
                      : undefined}>{primaryName}{primaryMeta ? ` · ${primaryMeta}` : ''}</span
                  ><button
                    type="button"
                    class="link-button"
                    aria-label={restoring ? 'Change the restore model' : 'Change the upscaler'}
                    on:click={() => openLibrary('primary')}>Models…</button
                  >
                </div>
              </div>{/if}
            {#if settings.task === 'video' && !usingTemporalVideo}<div class="panel-row">
                <div class="panel-key">
                  <label class="panel-label" for="frame-rate">Frames</label><HelpTip
                    label="Frames"
                    text="Adds in-between frames for smoother motion. 2× and 4× multiply the clip's own rate; 60 and 120 fps set a fixed rate and are skipped when the clip is already about that fast. Adding them after upscaling is faster; before upscaling can look smoother, but the added frames are upscaled too, which takes about twice as long at 2×. RIFE · Labs makes the frames: scene cuts stay cuts, very fast motion can smear. The first use downloads about 25 MB."
                  />
                </div>
                <select
                  id="frame-rate"
                  disabled={preservingHdr || !interpolationSupported}
                  title={preservingHdr
                    ? 'Not available while HDR is preserved.'
                    : !interpolationSupported
                      ? 'This engine cannot add frames.'
                      : undefined}
                  value={(settings.video_interpolation ?? 'off') === 'off'
                    ? 'off'
                    : `${settings.video_interpolation}:${settings.video_interpolation_order ?? 'after_upscale'}`}
                  on:change={(event) => {
                    const [target, order] = event.currentTarget.value.split(':');
                    updateSettings({
                      video_interpolation: target as InterpolationTarget,
                      video_interpolation_order: (order ||
                        settings.video_interpolation_order ||
                        'after_upscale') as InterpolationOrder,
                    });
                  }}
                  ><option value="off">Original</option
                  >{#each ['after_upscale', 'before_upscale'] as order}<optgroup
                      label={order === 'after_upscale'
                        ? 'Add frames after upscaling'
                        : 'Add frames before upscaling (slower)'}
                      >{#each ['x2', 'x4', 'fps60', 'fps120'] as target}<option
                          value={`${target}:${order}`}
                          >{frameLabel(
                            target as Exclude<InterpolationTarget, 'off'>,
                            baseFps,
                          )}{order === 'before_upscale' ? ' · before upscaling' : ''}</option
                        >{/each}</optgroup
                    >{/each}</select
                >
              </div>{/if}
            {#if cutoutShown}<div class="panel-row">
                <div class="panel-key">
                  <span class="panel-label" title="Background">Backgr.</span><HelpTip
                    label="Background"
                    text={`Remove makes everything behind the main subject transparent. The subject is found on the original picture, before the fixes and the upscale, and its outline is enlarged with the picture. The outline is worked out at 1024 × 1024 pixels, so single hairs and other very fine edges are not followed exactly. The result needs transparency: PNG, TIFF and WebP keep it, and a JPEG or HEIC choice is saved as PNG. BiRefNet does the work.${cutoutDownload ? ` The first use downloads ${cutoutDownload}.` : ''}`}
                  />
                </div>
                <div
                  class="seg-inline"
                  role="radiogroup"
                  aria-label="Background"
                  title={cutoutSupported
                    ? undefined
                    : 'This engine cannot remove backgrounds. Update LocalSR.'}
                >
                  <button
                    role="radio"
                    aria-checked={!cutoutRemoved}
                    class:active={!cutoutRemoved}
                    title="Leaves the background as it is"
                    on:keydown={radioGroupKeydown}
                    on:click={() => setBackground(false)}>Keep</button
                  >
                  <button
                    role="radio"
                    aria-checked={cutoutRemoved}
                    class:active={cutoutRemoved}
                    disabled={!cutoutSupported}
                    title={`Makes the background transparent${cutoutDownload ? ` · ${cutoutDownload} download` : ''}`}
                    on:keydown={radioGroupKeydown}
                    on:click={() => setBackground(true)}>Remove</button
                  >
                </div>
              </div>{/if}
            {#if usingTemporalVideo}
              {#if !(flashVideo && temporalUnavailableReason)}<VideoMemory
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
                />{/if}
              {#if selectedMedia && settings.video_target_resolution && settings.video_target_resolution < Math.min(selectedMedia.width, selectedMedia.height)}<p
                  class="notice"
                >
                  This setting reduces {selectedMedia.width} × {selectedMedia.height} to {outputDimensions}.
                  Fine detail will be lost. Choose a larger output for a quality comparison.
                </p>{/if}
            {/if}
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
                {@const baseMeta = stage.editModel
                  ? `${editVariantName(stage.editModel)}${
                      editFit !== 'unknown' && editFit !== 'runs'
                        ? ` · ${EDIT_FIT_LABELS[editFit]}`
                        : ''
                    }`
                  : ''}
                {@const meta = baseMeta
                  ? `${baseMeta}${expertChanges.length ? ' · Expert settings' : ''}`
                  : ''}
                {@const metaTitle = baseMeta ? [baseMeta, ...expertChanges].join(' · ') : ''}
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
                      title={(stage.custom !== undefined
                        ? settings.custom_model_path
                        : metaTitle) || undefined}
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
            features={snapshot.engine?.features ?? []}
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
      {#each needsYouStages as stage (`${stage.role}:${stage.fix ?? stage.kind}`)}
        {@const target = stageTarget(stage)}
        {@const name = stageName(stage)}
        {@const head = name ? `${needsYouStep(stage)} · ${name}` : needsYouStep(stage)}
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
            {#if !stage.canDownload}
              <button
                class="button full"
                disabled={settingsLocked ||
                  (stage.videoModel && Boolean(temporalUnavailableReason)) ||
                  preparing}
                on:click={() => runDownload(target)}
                >Choose externally downloaded checkpoint…</button
              >
            {/if}
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
      {/each}{#if hasNothingToRun}
        <p class="inline-warning" role="status">{nothingToRunReason}</p>
      {/if}{#if usingTemporalVideo && temporalUnavailableReason}
        <p class="inline-warning" role="status">{temporalUnavailableReason}</p>
      {/if}{#if settings.task === 'video' && !restoring && selectedMedia?.hdr_format && !hdrPreservationAvailable}
        <p class="inline-warning" role="status">
          SDR output selected. This model cannot preserve HDR.
        </p>
      {/if}{#if settings.task === 'video' && selectedMedia?.hdr_format && preservingHdr && !hdrModelCompatible}
        <p class="inline-warning" role="status">Choose a HAT model for HDR preservation.</p>
      {/if}{#if !fixPlanSupported && snapshot.engine}
        <p class="inline-warning" role="status">
          This engine runs one fix at a time. Update LocalSR to run several.
        </p>
      {/if}{#if settings.task === 'edit' && editBlockReason}
        <p class="inline-warning" role="status">
          {editBlockReason}
          {#if !editRuntime && editingDevice && editSupported}<button
              class="link-button"
              type="button"
              disabled={settingsLocked}
              on:click={chooseEditRuntime}>Choose runtime…</button
            >{/if}
        </p>
      {/if}{#if planState.status === 'error'}
        <p class="inline-warning" role="status" title={planState.message}>
          Could not work out the plan.
          <button class="link-button" type="button" on:click={retryPlan}>Try again</button>
        </p>
      {/if}{#if settings.task !== 'edit' && planState.status !== 'error'}
        <Popover
          bind:open={planOpen}
          placement="sheet"
          label="Plan"
          triggerClass={`foot-line foot-plan foot-trigger${
            planState.status === 'loading' && planState.first ? ' muted' : ''
          }${planDimmed ? ' dimmed' : ''}`}
          triggerLabel={planLineLabel}
          triggerTitle={planLineTitle}
          triggerBusy={planState.status === 'loading' && !planState.first}
          let:close
        >
          <svelte:fragment slot="trigger">
            <span class="foot-plan-text" class:marked={activePlanMarks.length > 0}
              >{#if activePlanMarks.length > 0}{#each activePlanMarks as mark, index (index)}{#if index > 0}{' → '}{/if}<span
                    class={`foot-plan-step ${mark.state}`}
                    >{mark.state === 'done' ? `✓ ${mark.name}` : mark.name}</span
                  >{/each}{:else}{planLineText}{/if}</span
            >
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
            stepNames={planSteps}
            {settings}
            {defaultSettings}
            {settingsLocked}
            {restoring}
            {usingTemporalVideo}
            {selectedModel}
            {selectedMedia}
            {editFit}
            {imageFixes}
            {planState}
            {plan}
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
      {/if}{#if settings.task !== 'edit'}
        <Popover
          bind:open={outputOpen}
          placement="sheet"
          label="Output"
          triggerClass="foot-line foot-output foot-trigger"
          triggerTitle={outputLineTitle}
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
      {/if}{#if costBytes > 0 || costTime}
        <div class="foot-line foot-cost">
          {#if costTime}<span class="foot-cost-time">about {costTime}</span>{/if}
          {#if costBytes > 0}
            <span class="foot-cost-download"
              >{formatBytes(costBytes)} to download<HelpTip
                label="Downloads"
                text={`Models download once when you start and stay on this computer. Each file is checked before it is used. Your ${
                  settings.task === 'video' ? 'videos' : 'photos'
                } never leave it.`}
              /></span
            >
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</aside>

<style>
  .result-model {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    min-width: 0;
    min-height: calc(30px * var(--ui-scale));
    font-size: calc(12px * var(--ui-scale));
  }
  .result-model-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .result-model button {
    flex: none;
  }
</style>
