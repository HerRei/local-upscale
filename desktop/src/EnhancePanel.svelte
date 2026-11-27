<script lang="ts">
  import AdvancedSettings from './AdvancedSettings.svelte';
  import HelpTip from './HelpTip.svelte';
  import ModelCredit from './ModelCredit.svelte';
  import Popover from './Popover.svelte';
  import RecipesMenu from './RecipesMenu.svelte';
  import VideoMemory from './VideoMemory.svelte';
  import { customModels } from './lib/customModels';
  import { EDIT_FIT_LABELS, editFamilyLabel, editVariantName } from './lib/editing';
  import type { EnhancePanelActions, EnhancePanelView } from './lib/enhancePanel';
  import { UNSTABLE_FIX_MODELS } from './lib/fixStages';
  import { formatBytes } from './lib/format';
  import {
    FIT_LABELS,
    FIX_LABELS,
    displayName,
    fitFor,
    frameRateChange,
    libraryModelsForTask,
    modelsAvailableBeyondSelection,
  } from './lib/plan';
  import { FIX_CHIPS, FIX_NAMES } from './lib/steps';
  import type { FixKind, TaskKind } from './lib/types';
  import {
    FRAME_BY_FRAME_TIP,
    SEVERAL_FRAMES_TIP,
    videoEngineName,
    videoModelShortName,
    videoModelsForJob,
  } from './lib/videoEngines';

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
  const fixChips = FIX_CHIPS as FixKind[];
  let recipesOpen = false;

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

  $: taskTip =
    selectedMedia?.kind === 'video' || settings.task === 'video'
      ? 'Upscale & restore enlarges videos, fixes them, or both. Edit is for photos.'
      : 'Upscale & restore enlarges photos and artwork, fixes them, or both. Edit changes a photo from a description in words.';

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

      {#if settings.task === 'video' && selectedMedia?.hdr_format}
        <section class="control-section" aria-label="HDR conversion">
          <span class="eyebrow"
            >{preservingHdr ? 'HDR input · HDR output · Labs' : 'HDR input · SDR output'}</span
          >
          <label class="field-label" for="hdr-output">Colour output</label>
          <select
            id="hdr-output"
            value={usingTemporalVideo ? 'tone_map' : (settings.video_hdr_mode ?? 'tone_map')}
            on:change={(event) =>
              updateSettings({
                video_hdr_mode: event.currentTarget.value as 'tone_map' | 'preserve',
                ...(event.currentTarget.value === 'preserve'
                  ? { precision: 'fp32', deflicker: false }
                  : {}),
              })}
          >
            <option value="preserve" disabled={!hdrPreservationAvailable}
              >Preserve {selectedMedia.hdr_format} · 10-bit HEVC · Labs{!hdrPreservationAvailable
                ? ' · unavailable for this model'
                : ''}</option
            >
            <option value="tone_map">Convert to SDR · 8-bit H.264</option>
          </select>
          {#if preservingHdr}
            <p class="model-description">
              Keeps {selectedMedia.hdr_format} in float precision through HAT and exports 10-bit BT.2020
              HEVC. Source light and colour constrain the model’s added detail. HAT models were trained
              on SDR; HDR detail quality is experimental.
            </p>
            <p class="model-description">
              Thumbnails and live tiles use SDR tone mapping. Final playback depends on system HDR
              support. Dolby Vision dynamic metadata is not retained; compatible HLG/PQ base video
              is preserved.
            </p>
            {#if !hdrModelCompatible}<p class="inline-warning">
                Choose a HAT model for HDR preservation.
              </p>{/if}
          {:else}
            <p class="model-description">
              This {selectedMedia.hdr_format} video is tone-mapped to SDR before enhancement and exported
              as 8-bit SDR. The preview uses the same conversion.
            </p>
          {/if}
          {#if settings.selected_model_id === '__custom__' && !usingTemporalVideo}<p
              class="model-description"
            >
              HDR preservation remains available for custom checkpoints. The current adapter
              requires HAT; compatibility is checked when the model loads.
            </p>{/if}
        </section>
      {/if}

      {#if settings.task === 'video'}
        <section class="control-section" aria-label="Audio">
          <div class="eyebrow eyebrow-row">
            <span class="label-with-tip"
              >Audio{#if selectedMedia?.audio_warning}<HelpTip
                  label="this video's audio"
                  text={selectedMedia.audio_warning}
                />{/if}</span
            >
          </div>
          <div class="check-line">
            <label class="check-row"
              ><input
                type="checkbox"
                disabled={audioDisabled}
                checked={!audioDisabled && Boolean(settings.video_clean_audio)}
                on:change={(event) =>
                  updateSettings({ video_clean_audio: event.currentTarget.checked })}
              /> Clean up audio</label
            ><HelpTip
              label="Clean up audio"
              text="Removes background noise such as hum, wind and hiss from speech. Music may sound worse."
            />
          </div>
          {#if audioDisabled}
            <p class="model-description">This video has no audio track.</p>
          {:else if settings.video_clean_audio && audioModels.length > 0}
            <label class="field-label" for="audio-model">Audio model</label>
            <select
              id="audio-model"
              value={selectedAudioModel?.model_id ?? ''}
              on:change={(event) =>
                updateSettings({ video_audio_model: event.currentTarget.value })}
            >
              {#each audioModels as model (model.model_id)}
                <option value={model.model_id}>{model.name}</option>
              {/each}
            </select>
            {#if selectedAudioModel?.summary}
              <p class="model-description">{selectedAudioModel.summary}</p>
            {/if}
          {/if}
        </section>
      {/if}

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
            aria-label={settings.task === 'video' ? 'Your video' : 'Your image'}
          >
            <span class="eyebrow">{settings.task === 'video' ? 'Your video' : 'Your image'}</span>
            {#if !restoring}
              <div class="field-heading">
                <span class="field-label" id="content-label">Content</span><HelpTip
                  label="Content"
                  text="Photo picks models trained on camera pictures: skin, foliage, fabric, buildings. Illustration picks models for drawings, anime and comics, which keep lines clean and colours flat. Choose what the picture is, not the look you want."
                />
              </div>
              <div class="seg-inline" role="radiogroup" aria-labelledby="content-label">
                <button
                  role="radio"
                  aria-checked={settings.content !== 'illustration'}
                  class:active={settings.content !== 'illustration'}
                  title="Uses a model made for photographs"
                  on:keydown={radioGroupKeydown}
                  on:click={() => setContent('photo')}>Photo</button
                >
                <button
                  role="radio"
                  aria-checked={settings.content === 'illustration'}
                  class:active={settings.content === 'illustration'}
                  title="Uses a model made for drawings and anime"
                  on:keydown={radioGroupKeydown}
                  on:click={() => setContent('illustration')}>Illustration</button
                >
              </div>
            {/if}
            <div class="field-heading">
              <span class="field-label" id="fix-label">Fix</span><HelpTip
                label="Fix"
                text={`Each problem you pick gets its own fix at the original size, in the order listed under Model. Quick picks a fast model for each problem, Best the most careful one. ↑ and ↓ change the order; a fix's Change… picks its model, which LocalSR remembers for that problem. ${
                  settings.task === 'video'
                    ? 'Colour adds colour to black-and-white videos and keeps it steady from frame to frame.'
                    : 'Colour adds colour to black-and-white photos.'
                }`}
              />
            </div>
            <div class="chips" role="group" aria-label="Fix">
              {#each fixChips as fix (fix)}
                <button
                  class="chip"
                  type="button"
                  class:on={settings.fixes.includes(fix)}
                  aria-pressed={settings.fixes.includes(fix)}
                  title={fix === 'colour'
                    ? `Adds colour to black-and-white ${settings.task === 'video' ? 'videos' : 'photos'}`
                    : ''}
                  on:click={() => toggleFix(fix)}
                  >{#if settings.fixes.includes(fix)}<svg
                      xmlns="http://www.w3.org/2000/svg"
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2.5"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      aria-hidden="true"><path d="m5 12 5 5L20 7" /></svg
                    >{/if}{FIX_LABELS[fix]}</button
                >
              {/each}
            </div>
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

        <section class="control-section model-section" aria-label="Model plan">
          <div class="eyebrow eyebrow-row">
            <span
              >{activeQuality === 'custom' && settings.task !== 'edit'
                ? 'Model · chosen by you'
                : 'Model'}</span
            >
            <span class="eyebrow-actions">
              {#if activeQuality === 'custom' && (bestModel || (settings.task === 'video' && restoring && imageFixes.length > 0)) && settings.task !== 'edit'}<button
                  class="link-button"
                  type="button"
                  on:click={() => applyPreset('best')}>Back to Best</button
                >{/if}
              {#if !noRestoreModel && !plan.some((stage) => stage.role === 'primary')}<button
                  class="link-button"
                  type="button"
                  on:click={() => openLibrary(settings.task === 'edit' ? 'edit' : 'primary')}
                  >Change…</button
                >{/if}
            </span>
          </div>
          <div
            class="plan"
            class:movable={imageFixes.length > 1}
            role="list"
            aria-label="Stages"
            aria-busy={planState.status === 'loading' && !planState.first ? 'true' : undefined}
          >
            {#each plan as stage (`${stage.role}:${stage.fix ?? stage.kind}`)}
              {@const target =
                stage.model ??
                stage.videoModel ??
                stage.editModel ??
                stage.interpolationModel ??
                stage.audioModel}
              {@const changeSlot =
                stage.role === 'fix'
                  ? stage.fix !== 'colour' && stage.fix && 'fix'
                  : stage.role === 'primary'
                    ? stage.editModel
                      ? 'edit'
                      : 'primary'
                    : undefined}
              {@const moreVideoCount =
                changeSlot === 'primary'
                  ? videoModelsForJob(snapshot.catalog, settings.task, videoTask).filter(
                      (model) =>
                        !videoAvailable[model.model_id] &&
                        model.model_id !== stage.videoModel?.model_id,
                    ).length
                  : 0}
              {@const runnableVideoModels =
                changeSlot === 'primary' && stage.model
                  ? videoModelsForJob(snapshot.catalog, settings.task, videoTask).filter(
                      (model) => !videoAvailable[model.model_id],
                    )
                  : []}
              {@const videoNames = [
                ...new Set(runnableVideoModels.map((model) => videoEngineName(model.engine_kind))),
              ].join(', ')}
              {@const moreModelCount =
                changeSlot && settings.task !== 'edit'
                  ? modelsAvailableBeyondSelection(
                      libraryModelsForTask(
                        snapshot.catalog.models,
                        settings.task,
                        settings.content,
                        stage.fix,
                        videoTask,
                      ),
                      stage.model?.model_id ?? '',
                    ) + moreVideoCount
                  : 0}
              {@const tryOthers = stage.role === 'primary' && canTryOthers}
              {@const name = stage.model
                ? displayName(stage.model)
                : stage.videoModel
                  ? videoModelShortName(stage.videoModel)
                  : stage.interpolationModel
                    ? stage.interpolationModel.name.split(' — ')[0]
                    : stage.audioModel
                      ? stage.audioModel.name.split(' — ')[0]
                      : stage.editModel
                        ? editFamilyLabel(stage.editModel.family)
                        : stage.missing
                          ? 'No model for this fix'
                          : stage.custom !== undefined
                            ? (customModels(settings).find(
                                (model) => model.path === settings.custom_model_path,
                              )?.name ?? 'Checkpoint')
                            : ''}
              <!-- Licenses live in the model library; only exceptions show here. -->
              {@const meta = stage.editModel
                ? `${editVariantName(stage.editModel)}${
                    editFit !== 'unknown' && editFit !== 'runs'
                      ? ` · ${EDIT_FIT_LABELS[editFit]}`
                      : ''
                  }`
                : stage.model
                  ? [
                      stage.remembered ? 'Your pick' : '',
                      stage.model.support_tier === 'labs' ? 'Labs' : '',
                      stage.model.commercial_use_allowed === false ? 'Non-commercial' : '',
                      ['heavy', 'too_heavy'].includes(fitFor(stage.model, activeDevice))
                        ? FIT_LABELS[fitFor(stage.model, activeDevice)]
                        : '',
                    ]
                      .filter(Boolean)
                      .join(' · ')
                  : stage.videoModel
                    ? 'Labs · several frames at once'
                    : stage.interpolationModel
                      ? [
                          settings.video_interpolation && settings.video_interpolation !== 'off'
                            ? frameRateChange(settings.video_interpolation, selectedMedia?.fps ?? 0)
                            : '',
                          settings.video_interpolation_order === 'before_upscale'
                            ? 'before upscaling'
                            : 'after upscaling',
                          'Labs',
                        ]
                          .filter(Boolean)
                          .join(' · ')
                      : stage.audioModel
                        ? stage.audioModel.summary || 'Audio cleanup'
                        : ''}
              {@const warning =
                stage.model && UNSTABLE_FIX_MODELS.has(stage.model.model_id)
                  ? stage.role === 'fix'
                    ? 'Can add artifacts to sharp pictures and line art. Skipped if the result is unstable.'
                    : 'Can add artifacts to sharp pictures and line art.'
                  : ''}
              {@const movable = stage.fix && imageFixes.length > 1}
              {@const fixIndex = stage.fix
                ? imageFixes.findIndex((selection) => selection.fix === stage.fix)
                : -1}
              {@const stageDownload = target ? downloadMap.get(target.model_id) : undefined}
              <!-- Line 1: role, name, arrows and Change…; line 2 the library links; line 3 the note. -->
              <div class="plan-row" role="listitem">
                <span class="stage"
                  >{#if stage.fix === 'colour' || stage.model?.purposes.includes('colorize')}<span
                      class="label-with-tip"
                      >{stage.label}<HelpTip
                        label="Colour"
                        text={settings.task === 'video'
                          ? "Adds colour to black-and-white videos. Colours are the model's guess and are carried between frames so they stay steady."
                          : "Adds colour to black-and-white photos. The colours are the model's guess. A photo that already has colour is recoloured too."}
                      /></span
                    >{:else}{stage.label}{/if}</span
                >
                <div class="name" title={name || undefined}>{name}</div>
                <div class="plan-end">
                  <span class="state" class:warn={stage.needsFile}
                    >{stageDownload
                      ? stageDownload.status === 'queued'
                        ? 'Waiting'
                        : `${Math.round(stageDownload.progress)}%`
                      : stage.needsFile && !stage.installed
                        ? 'Needs your file'
                        : ''}</span
                  >
                  {#if movable}
                    <!-- Only the moves this fix can make, directly left of Change…. -->
                    <span class="plan-moves">
                      {#if fixIndex > 0}<button
                          class="move-button"
                          type="button"
                          title="Move earlier"
                          aria-label={`Move the ${FIX_NAMES[stage.fix!]} fix earlier`}
                          on:click={() => void moveFix(stage.fix!, -1)}>↑</button
                        >{/if}
                      {#if fixIndex >= 0 && fixIndex < imageFixes.length - 1}<button
                          class="move-button"
                          type="button"
                          title="Move later"
                          aria-label={`Move the ${FIX_NAMES[stage.fix!]} fix later`}
                          on:click={() => void moveFix(stage.fix!, 1)}>↓</button
                        >{/if}
                    </span>
                  {:else if stage.kind === 'interpolate'}
                    <!-- Interpolation runs first (before upscaling) or last (after it). -->
                    <span class="plan-moves">
                      {#if settings.video_interpolation_order === 'before_upscale'}<button
                          class="move-button"
                          type="button"
                          title="Interpolate after upscaling (faster)"
                          aria-label="Interpolate after upscaling"
                          on:click={() =>
                            updateSettings({ video_interpolation_order: 'after_upscale' })}
                          >↓</button
                        >{:else}<button
                          class="move-button"
                          type="button"
                          title="Interpolate before upscaling (smoother, slower)"
                          aria-label="Interpolate before upscaling"
                          on:click={() =>
                            updateSettings({ video_interpolation_order: 'before_upscale' })}
                          >↑</button
                        >{/if}
                    </span>
                  {/if}
                  {#if changeSlot}<button
                      class="link-button"
                      type="button"
                      aria-label={changeSlot === 'fix' && stage.fix
                        ? `Change the ${FIX_NAMES[stage.fix]} fix model`
                        : changeSlot === 'edit'
                          ? 'Change the edit model'
                          : restoring
                            ? 'Change the restore model'
                            : 'Change the upscaler'}
                      on:click={() => openLibrary(changeSlot, stage.fix)}>Change…</button
                    >
                  {/if}
                </div>
                {#if moreModelCount || tryOthers || runnableVideoModels.length}<div
                    class="plan-links"
                  >
                    {#if moreModelCount}<button
                        class="link-button"
                        type="button"
                        aria-label={`${moreModelCount} more ${moreModelCount === 1 ? 'model' : 'models'} in the library`}
                        on:click={() => changeSlot && openLibrary(changeSlot, stage.fix)}
                        >{moreModelCount} more {moreModelCount === 1 ? 'model' : 'models'}</button
                      >{/if}
                    {#if tryOthers}<span class="try-others"
                        ><button
                          class="link-button"
                          type="button"
                          aria-label={restoring
                            ? 'Try other restoration models on this image'
                            : 'Try other upscalers on this image'}
                          disabled={!canOpenTrials}
                          on:click={() => openLibrary('try', stage.fix)}>Try others…</button
                        ><HelpTip
                          label="Try others"
                          text="Runs this image once per model you pick, with the same settings. Each result is kept as its own file, and Showing in the preview lets you flip between them."
                        /></span
                      >{/if}
                    {#if runnableVideoModels.length}<span class="video-models-link"
                        ><button
                          class="link-button"
                          type="button"
                          aria-label={`${videoNames} in the model library`}
                          on:click={() =>
                            openLibrary(
                              'primary',
                              stage.fix,
                              (
                                runnableVideoModels.find((model) => model.installed) ??
                                runnableVideoModels[0]
                              ).model_id,
                            )}>{videoNames}…</button
                        ><HelpTip
                          label="Video models"
                          text={`${SEVERAL_FRAMES_TIP} ${FRAME_BY_FRAME_TIP}`}
                        /></span
                      >{/if}
                  </div>{/if}
                {#if meta || warning || stage.custom !== undefined}<p
                    class="plan-note"
                    title={(stage.custom !== undefined
                      ? settings.custom_model_path
                      : [meta, warning].filter(Boolean).join(' · ')) || undefined}
                  >
                    {#if stage.custom !== undefined}<span class="path-text"
                        >{settings.custom_model_path ||
                          'Safetensors is strongly recommended.'}</span
                      >{:else}{#if stage.videoModel}<span class="label-with-tip"
                          >{meta}<HelpTip
                            label="Video models"
                            text={`${SEVERAL_FRAMES_TIP} ${FRAME_BY_FRAME_TIP}`}
                          /></span
                        >{:else}{meta}{/if}{#if meta && warning}{' · '}{/if}{#if warning}<span
                          >{warning}</span
                        >{/if}{/if}
                  </p>{/if}
                {#if stage.custom !== undefined || (target && !stage.installed)}<div
                    class="plan-extra"
                  >
                    {#if stage.custom !== undefined}
                      <button class="button full" on:click={chooseCustomModel}
                        >{settings.custom_model_path
                          ? 'Choose another checkpoint…'
                          : 'Choose checkpoint…'}</button
                      >
                      {#if customModelNeedsOptIn}
                        <label class="terms"
                          ><input
                            type="checkbox"
                            checked={settings.allow_unsafe_pickle_model}
                            on:change={(event) =>
                              void changeCheckpointConsent(event.currentTarget.checked)}
                          /> Allow this pickle-based checkpoint. It may execute code inside the inference
                          worker.</label
                        >
                      {/if}
                    {:else if stage.model && licenseReviewModel?.model_id === stage.model.model_id && !stage.installed}
                      <ModelCredit
                        model={stage.model}
                        openLicense={() => openAnyLicense(stage.model!.model_id)}
                        openSource={() => openAnySource(stage.model!.model_id)}
                      />
                    {:else if stage.editModel?.model_id === '__custom__' && !stage.installed}
                      <button class="button full" type="button" on:click={openCustomEdit}
                        >Choose edit model files…</button
                      >
                    {:else if target && !stage.installed}
                      <!-- A downloadable model is fetched by the Start button; this row only
                         asks for what Start cannot do on its own. -->
                      {#if (stage.model ?? stage.editModel)?.terms_acceptance_required}
                        <label class="terms"
                          ><input type="checkbox" bind:checked={termsAccepted[target!.model_id]} /> I
                          reviewed the model license and restrictions.</label
                        >
                      {/if}
                      {#if !stage.canDownload || target.terms_acceptance_required || Boolean(stageDownload)}
                        <button
                          class="button full"
                          disabled={(stage.videoModel && Boolean(temporalUnavailableReason)) ||
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
                    {/if}
                  </div>{/if}
              </div>
            {/each}
          </div>
          {#if planState.status === 'loading' && planState.first}
            <p class="model-note" role="status">Working out the plan…</p>
          {:else if planState.status === 'error'}
            <p class="inline-warning" role="status" title={planState.message}>
              Could not work out the plan.
              <button class="link-button" type="button" on:click={retryPlan}>Try again</button>
            </p>
          {/if}
          {#if hasNothingToRun}
            <p class="inline-warning" role="status">{nothingToRunReason}</p>
          {/if}
          {#if usingTemporalVideo && temporalUnavailableReason}
            <p class="inline-warning" role="status">{temporalUnavailableReason}</p>
          {/if}
          {#if settings.task === 'video' && !restoring && selectedMedia?.hdr_format && !hdrPreservationAvailable}
            <p class="model-output-note" role="status">
              SDR output selected. This model cannot preserve HDR.
            </p>
          {/if}
          {#if !fixPlanSupported && snapshot.engine}<p class="inline-warning" role="status">
              This engine runs one fix at a time. Update LocalSR to run several.
            </p>{/if}
          {#if settings.task === 'edit'}
            {#if editModel?.terms_acceptance_required && editModel.installed}
              <label class="terms"
                ><input type="checkbox" bind:checked={termsAccepted[editModel.model_id]} /> I reviewed
                the Qwen Research License and accept it for this edit.</label
              >
            {/if}
            {#if editBlockReason}
              <p class="inline-warning" role="status">
                {editBlockReason}
                {#if !editRuntime && editingDevice && editSupported}<button
                    class="link-button"
                    type="button"
                    on:click={chooseEditRuntime}>Choose runtime…</button
                  >{/if}
              </p>
            {/if}
          {/if}
          {#if activeQuality === 'custom' && settings.task !== 'edit'}
            <p class="model-note">
              {usingTemporalVideo
                ? 'Chosen by you. Fix, Same size, De-flicker and Frame rate need a frame-by-frame model: pick Quick or Best to go back.'
                : 'Chosen by you. Pick Quick or Best to go back.'}
            </p>
          {:else if downloads.stages.length}
            <p class="model-note">
              Models download once when you start and stay on this computer. Your {settings.task ===
              'video'
                ? 'videos'
                : 'photos'} never leave it.
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
                Smaller output takes less time. For a first test, try 256 or 512 px. FlashVSR works
                best up to 4× the source: above that, it runs at 4× and resizes the rest.
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
        <AdvancedSettings
          {settings}
          {selectedModel}
          {editModel}
          capabilities={snapshot.capabilities}
          {usingTemporalVideo}
          videoModel={usingTemporalVideo ? selectedVideoModel : undefined}
          {preservingHdr}
          {interpolationSupported}
          updateSettings={chooseInAdvanced}
          defaults={defaultSettings}
          onUseDefaults={useDefaultsForSession}
          {chooseOutput}
          onOpenFfmpegSettings={() => openSettings('storage', 'ffmpeg')}
          {refreshCapabilities}
        />
      {/if}
    </fieldset>
  </div>
</aside>
