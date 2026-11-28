<script lang="ts">
  import HelpTip from './HelpTip.svelte';
  import RunSettings from './RunSettings.svelte';
  import { customModels } from './lib/customModels';
  import { EDIT_FIT_LABELS, editFamilyLabel, editVariantName } from './lib/editing';
  import type { EnhancePanelActions } from './lib/enhancePanel';
  import { UNSTABLE_FIX_MODELS } from './lib/fixStages';
  import type { PlanState } from './lib/jobPlanClient';
  import { FIT_LABELS, displayName, fitFor, frameRateChange, type PlanStage } from './lib/plan';
  import { FIX_NAMES } from './lib/steps';
  import { scanChoice, scanOptions, scanRate } from './lib/stepRows';
  import type {
    AppSnapshot,
    CatalogModel,
    DeviceInfo,
    FixStageSelection,
    MediaItem,
    ModelDownloadStatus,
    UiSettings,
  } from './lib/types';
  import { FRAME_BY_FRAME_TIP, SEVERAL_FRAMES_TIP, videoModelShortName } from './lib/videoEngines';

  export let snapshot: AppSnapshot;
  /** Row names as the plan line writes them; falls back to the host label. */
  export let stepNames: string[] = [];
  export let settings: UiSettings;
  export let defaultSettings: UiSettings;
  export let settingsLocked: boolean;
  export let restoring: boolean;
  export let usingTemporalVideo: boolean;
  export let selectedModel: CatalogModel | undefined;
  export let selectedMedia: MediaItem | undefined;
  export let editFit: keyof typeof EDIT_FIT_LABELS;
  export let imageFixes: FixStageSelection[];
  export let planState: PlanState;
  export let plan: PlanStage[];
  export let activeDevice: DeviceInfo | undefined;
  export let downloadMap: Map<string, ModelDownloadStatus>;
  export let canOpenTrials: boolean;
  export let canTryOthers: boolean;
  export let updateSettings: EnhancePanelActions['updateSettings'];
  export let chooseInAdvanced: EnhancePanelActions['chooseInAdvanced'];
  export let useDefaultsForSession: EnhancePanelActions['useDefaultsForSession'];
  export let openLibrary: EnhancePanelActions['openLibrary'];
  export let moveFix: EnhancePanelActions['moveFix'];
  export let refreshCapabilities: EnhancePanelActions['refreshCapabilities'];

  $: scanSelected = scanChoice(settings);
  $: scanNote =
    scanOptions(selectedMedia?.field_order, selectedMedia?.fps ?? 0, scanSelected).find(
      (option) => option.value === scanSelected,
    )?.label ?? '';
  $: baseFps = scanRate(scanSelected, selectedMedia?.field_order, selectedMedia?.fps ?? 0);

  function openFromSheet(
    slot: Parameters<EnhancePanelActions['openLibrary']>[0],
    fix?: Parameters<EnhancePanelActions['openLibrary']>[1],
    select?: Parameters<EnhancePanelActions['openLibrary']>[2],
  ): void {
    // The sheet stays under the library, so Escape there returns to this Change….
    openLibrary(slot, fix, select);
  }
</script>

<span class="eyebrow">Plan</span>
<div
  class="plan"
  class:movable={imageFixes.length > 1}
  role="list"
  aria-label="Stages"
  aria-busy={planState.status === 'loading' && !planState.first ? 'true' : undefined}
>
  {#each plan as stage, index (`${stage.role}:${stage.fix ?? stage.kind}`)}
    {@const target =
      stage.model ??
      stage.videoModel ??
      stage.editModel ??
      stage.interpolationModel ??
      stage.audioModel}
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
          editFit !== 'unknown' && editFit !== 'runs' ? ` · ${EDIT_FIT_LABELS[editFit]}` : ''
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
                  ? frameRateChange(settings.video_interpolation, baseFps)
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
              : stage.kind === 'deinterlace'
                ? scanNote
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
            >{stepNames[index] ?? stage.label}<HelpTip
              label="Colour"
              text={settings.task === 'video'
                ? "Adds colour to black-and-white videos. Colours are the model's guess and are carried between frames so they stay steady."
                : "Adds colour to black-and-white photos. The colours are the model's guess. A photo that already has colour is recoloured too."}
            /></span
          >{:else}{stepNames[index] ?? stage.label}{/if}</span
      >
      <div class="name" title={name || undefined}>{name}</div>
      <div class="plan-end">
        <span class="state" class:warn={stage.needsFile}
          >{!stageDownload && stage.needsFile && !stage.installed ? 'Needs your file' : ''}</span
        >
        {#if movable}
          <!-- Only the moves this fix can make, directly left of Change…. -->
          <span class="plan-moves">
            {#if fixIndex > 0}<button
                class="move-button"
                type="button"
                disabled={settingsLocked}
                title="Move earlier"
                aria-label={`Move the ${FIX_NAMES[stage.fix!]} fix earlier`}
                on:click={() => void moveFix(stage.fix!, -1)}>↑</button
              >{/if}
            {#if fixIndex >= 0 && fixIndex < imageFixes.length - 1}<button
                class="move-button"
                type="button"
                disabled={settingsLocked}
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
                disabled={settingsLocked}
                title="Interpolate after upscaling (faster)"
                aria-label="Interpolate after upscaling"
                on:click={() => updateSettings({ video_interpolation_order: 'after_upscale' })}
                >↓</button
              >{:else}<button
                class="move-button"
                type="button"
                disabled={settingsLocked}
                title="Interpolate before upscaling (smoother, slower)"
                aria-label="Interpolate before upscaling"
                on:click={() => updateSettings({ video_interpolation_order: 'before_upscale' })}
                >↑</button
              >{/if}
          </span>
        {/if}
      </div>
      {#if tryOthers}<div class="plan-links">
          <span class="try-others"
            ><button
              class="link-button"
              type="button"
              aria-label={restoring
                ? 'Try other restoration models on this photo'
                : 'Try other upscalers on this photo'}
              disabled={settingsLocked || !canOpenTrials}
              on:click={() => openFromSheet('try', stage.fix)}>Try others…</button
            ><HelpTip
              label="Try others"
              text="Runs this photo once per model you pick, with the same settings. Each result is kept as its own file, and Showing in the preview lets you flip between them."
            /></span
          >
        </div>{/if}
      {#if meta || warning || stage.custom !== undefined}<p
          class="plan-note"
          title={(stage.custom !== undefined
            ? settings.custom_model_path
            : [meta, warning].filter(Boolean).join(' · ')) || undefined}
        >
          {#if stage.custom !== undefined}<span class="path-text"
              >{settings.custom_model_path || 'Safetensors is strongly recommended.'}</span
            >{:else}{#if stage.videoModel}<span class="label-with-tip"
                >{meta}<HelpTip
                  label="Video models"
                  text={`${SEVERAL_FRAMES_TIP} ${FRAME_BY_FRAME_TIP}`}
                /></span
              >{:else}{meta}{/if}{#if meta && warning}{' · '}{/if}{#if warning}<span>{warning}</span
              >{/if}{/if}
        </p>{/if}
    </div>
  {/each}
</div>
<hr class="sheet-sep" />
<span class="eyebrow">This run</span>
<RunSettings
  {settings}
  {selectedModel}
  capabilities={snapshot.capabilities}
  {usingTemporalVideo}
  updateSettings={chooseInAdvanced}
  defaults={defaultSettings}
  onUseDefaults={useDefaultsForSession}
  {refreshCapabilities}
  disabled={settingsLocked}
/>
