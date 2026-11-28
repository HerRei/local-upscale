<script lang="ts">
  import HelpTip from './HelpTip.svelte';
  import Popover from './Popover.svelte';
  import { UNSTABLE_FIX_MODELS } from './lib/fixStages';
  import {
    displayName,
    fitFor,
    FIT_LABELS,
    libraryModelsForTask,
    modelsAvailableBeyondSelection,
  } from './lib/plan';
  import { FIX_NAMES, type FixStageKind } from './lib/steps';
  import type { EnhancePanelActions } from './lib/enhancePanel';
  import {
    choiceShown,
    fixRows,
    fixRowValue,
    SETTING_ROWS,
    splitFixRows,
    STRENGTH_FEATURE,
    strengthLabel,
    strengthOf,
    withStrength,
    type FixRow,
  } from './lib/fixRows';
  import type { PlanStage } from './lib/plan';
  import type {
    CatalogModel,
    DeviceInfo,
    FixKind,
    ModelDownloadStatus,
    UiSettings,
  } from './lib/types';

  export let settings: UiSettings;
  export let mediaKind: 'image' | 'video';
  export let features: readonly string[];
  export let plan: readonly PlanStage[];
  export let models: readonly CatalogModel[];
  export let downloadMap: Map<string, ModelDownloadStatus>;
  export let settingsLocked: boolean;
  export let activeDevice: DeviceInfo | undefined;
  export let openLibrary: EnhancePanelActions['openLibrary'];
  export let toggleFix: EnhancePanelActions['toggleFix'];
  export let seeFixOnPhoto: EnhancePanelActions['seeFixOnPhoto'];
  /** The preview patch is on offer: `photo` when a photo is open, `example` otherwise, empty when off. */
  export let patchLink: 'photo' | 'example' | '' = '';
  export let updateSettings: EnhancePanelActions['updateSettings'];
  export let radioGroupKeydown: EnhancePanelActions['radioGroupKeydown'];
  export let moreOpen = false;

  $: rows = fixRows(mediaKind, features);
  $: preservingHdr = settings.video_hdr_mode === 'preserve';

  function isRowTicked(row: FixRow, currentSettings: UiSettings, hdrPreserved: boolean): boolean {
    if (row.source === 'fix_stage') {
      return currentSettings.fix_stages.some((selection) => selection.fix === row.id);
    }
    if (row.id === 'deflicker' && hdrPreserved) return false;
    const field = SETTING_ROWS[row.id];
    return field ? Boolean(currentSettings[field]) : false;
  }

  function isRowDisabled(row: FixRow, hdrPreserved: boolean): boolean {
    return row.id === 'deflicker' && hdrPreserved;
  }

  function rowTitle(row: FixRow, hdrPreserved: boolean): string {
    if (row.id === 'deflicker' && hdrPreserved) {
      return 'Not available while HDR is preserved.';
    }
    return row.title;
  }

  function toggleRow(row: FixRow, checked: boolean): void {
    if (row.source === 'fix_stage') {
      toggleFix(row.id as FixKind);
      return;
    }
    const field = SETTING_ROWS[row.id];
    if (field) updateSettings({ [field]: checked } as Partial<UiSettings>);
  }

  $: ({ shown, hiddenCount } = splitFixRows(
    rows,
    (row) => isRowTicked(row, settings, preservingHdr),
    moreOpen,
  ));

  function setStrength(row: FixRow, value: number): void {
    updateSettings({
      fix_stages: settings.fix_stages.map((entry) =>
        entry.fix === row.id ? withStrength(entry, value) : entry,
      ),
    });
  }

  $: strengthListed = features.includes(STRENGTH_FEATURE);
  $: fixTip = fixTipFor(mediaKind, strengthListed);

  function fixTipFor(media: 'image' | 'video', strengthOffered: boolean): string {
    const thing = media === 'video' ? 'video' : 'picture';
    const unit = media === 'video' ? 'frame' : 'picture';
    const intro = `Tick what is wrong with the ${thing}. Each problem gets its own fix at the original size, before the ${thing} is enlarged. Quick picks a fast model for each problem, Best the most careful one.`;
    const middle = strengthOffered
      ? ` A ticked row shows how strongly its fix is applied: click it to choose Light, Medium or Full, or another model. Light and Medium mix the fixed ${unit} with the ${unit} before the fix, so more of its grain and texture stays. LocalSR remembers the model you choose for a problem.`
      : ' A ticked row names its model; click the name to change it, and LocalSR remembers your choice for that problem.';
    return `${intro}${middle} The plan line at the bottom shows the order and lets you change it.${media === 'video' ? ' De-flicker runs after the frames are processed.' : ''}`;
  }
</script>

<div class="panel-key fix-head">
  <span class="panel-label" id="fix-label">Fix</span><HelpTip label="Fix" text={fixTip} />
</div>
<div class="fix-list" role="group" aria-label="Fix">
  {#each shown as row (row.id)}
    {@const ticked = isRowTicked(row, settings, preservingHdr)}
    {@const rowDisabled = isRowDisabled(row, preservingHdr)}
    {@const title = rowTitle(row, preservingHdr)}
    {@const entry = settings.fix_stages.find((selection) => selection.fix === row.id)}
    {@const showStrength = ticked && choiceShown(row, features, entry)}
    {@const value =
      ticked && row.source === 'fix_stage'
        ? fixRowValue(row, plan, settings, models, downloadMap, showStrength)
        : ''}
    {@const stage = plan.find((candidate) => candidate.fix === row.id)}
    {@const model =
      stage?.model ??
      models.find(
        (entry) =>
          entry.model_id ===
          settings.fix_stages.find((selection) => selection.fix === row.id)?.model_id,
      )}
    {@const valueTitle =
      showStrength && value.startsWith(strengthLabel(strengthOf(entry))) && model
        ? `${strengthLabel(strengthOf(entry))} · ${displayName(model)}${value.slice(strengthLabel(strengthOf(entry)).length)}`
        : value}
    {@const meta = model
      ? [
          stage?.remembered ? 'Your pick' : '',
          model.support_tier === 'labs' ? 'Labs' : '',
          model.commercial_use_allowed === false ? 'Non-commercial' : '',
          ['heavy', 'too_heavy'].includes(fitFor(model, activeDevice))
            ? FIT_LABELS[fitFor(model, activeDevice)]
            : '',
        ]
          .filter(Boolean)
          .join(' · ')
      : ''}
    {@const moreCount =
      row.id !== 'colour'
        ? modelsAvailableBeyondSelection(
            libraryModelsForTask(
              [...models],
              settings.task,
              settings.content,
              row.id as FixStageKind,
              'upscale',
            ),
            model?.model_id ?? '',
          )
        : 0}
    <div class="fix-row" class:disabled={rowDisabled && !settingsLocked} title={title || undefined}>
      <label class="fix-check" class:disabled={rowDisabled && !settingsLocked}>
        <input
          type="checkbox"
          checked={ticked}
          disabled={rowDisabled}
          on:change={(event) => toggleRow(row, event.currentTarget.checked)}
        />
        <span class="fix-name">{row.label}</span>
      </label>
      {#if row.tag}
        <span class="fix-tag">{row.tag}</span>
      {/if}
      {#if ticked && row.source === 'fix_stage' && value}
        <Popover
          label={`${row.label} options`}
          triggerClass={`fix-value${value === 'Needs your file' || value === 'No model for this fix' ? ' warn' : ''}`}
          triggerTitle={valueTitle}
          disabled={settingsLocked}
        >
          <svelte:fragment slot="trigger">
            <span class="fix-value-text">{value}</span><svg
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
          <div class="fix-pop-head">{row.label}</div>
          {#if showStrength && row.choice}
            {@const current = strengthOf(entry)}
            <div
              class="seg-inline strength-switch"
              role="radiogroup"
              aria-label={row.choice.name}
              title={strengthListed
                ? undefined
                : "This engine cannot change a fix's strength. Update LocalSR."}
            >
              {#each row.choice.options as option (option.value)}
                <button
                  type="button"
                  role="radio"
                  aria-checked={Math.abs(option.value - current) <= 0.005}
                  class:active={Math.abs(option.value - current) <= 0.005}
                  disabled={!strengthListed &&
                    option.value < 1 &&
                    Math.abs(option.value - current) > 0.005}
                  title={option.title}
                  on:keydown={radioGroupKeydown}
                  on:click={() => setStrength(row, option.value)}>{option.label}</button
                >
              {/each}
            </div>
          {/if}
          <div class="fix-pop-model">
            <span>Model</span><strong>{model ? displayName(model) : value}</strong>
          </div>
          {#if meta}<p class="fix-pop-note">{meta}</p>{/if}
          {#if model && UNSTABLE_FIX_MODELS.has(model.model_id)}<p class="fix-pop-note">
              Can add artifacts to sharp pictures and line art. Skipped if the result is unstable.
            </p>{/if}
          {#if row.id === 'colour'}<p class="fix-pop-note">
              {mediaKind === 'video'
                ? "Adds colour to black-and-white videos. Colours are the model's guess and are carried between frames so they stay steady."
                : "Adds colour to black-and-white photos. The colours are the model's guess. A photo that already has colour is recoloured too."}
            </p>{/if}
          {#if patchLink}
            <div class="fix-pop-change">
              {#if model && stage?.installed && patchLink === 'photo'}
                <button
                  class="link-button"
                  type="button"
                  on:click={() => seeFixOnPhoto(row.id as FixKind)}
                  >See it on this {mediaKind === 'video' ? 'frame' : 'photo'}</button
                >
              {:else}
                <button
                  class="link-button"
                  type="button"
                  on:click={() =>
                    row.id === 'colour'
                      ? openLibrary('browse', undefined, model?.model_id)
                      : openLibrary(
                          stage?.role === 'primary' ? 'primary' : 'fix',
                          row.id as FixStageKind,
                          model?.model_id,
                        )}>See an example</button
                >
              {/if}
            </div>
          {/if}
          {#if row.id !== 'colour'}
            <div class="fix-pop-change">
              <button
                class="link-button"
                type="button"
                aria-label={`Change the ${FIX_NAMES[row.id as FixStageKind] ?? row.id} fix model`}
                on:click={() =>
                  openLibrary(
                    stage?.role === 'primary' ? 'primary' : 'fix',
                    row.id as FixStageKind,
                  )}>Change…</button
              >
            </div>
            {#if moreCount}<button
                class="link-button fix-pop-more"
                type="button"
                aria-label={`${moreCount} more ${moreCount === 1 ? 'model' : 'models'} in the library`}
                on:click={() =>
                  openLibrary(
                    stage?.role === 'primary' ? 'primary' : 'fix',
                    row.id as FixStageKind,
                  )}>{moreCount} more {moreCount === 1 ? 'model' : 'models'}</button
              >{/if}
          {/if}
        </Popover>
      {/if}
    </div>
  {/each}
  {#if hiddenCount > 0}
    <button
      type="button"
      class="link-button fix-more"
      aria-expanded={moreOpen}
      on:click={() => (moreOpen = !moreOpen)}
    >
      {moreOpen ? 'Fewer fixes' : `More fixes (${hiddenCount})`}
    </button>
  {/if}
</div>
