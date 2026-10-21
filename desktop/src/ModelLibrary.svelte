<script lang="ts">
  import {
    FIT_LABELS,
    choosePresetModel,
    displayName,
    fitFor,
    fixesOf,
    formatBytes,
    modelsForTask,
    rightsAllowPreset,
  } from './lib/state';
  import {
    EDIT_FAMILIES,
    EDIT_FIT_LABELS,
    editDevice,
    editFamilyLabel,
    editFitFor,
    editHardwareLabel,
    editRuntimeLabel,
    editSizeLimit,
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
    Quality,
    TaskKind,
    UiSettings,
  } from './lib/types';

  export let snapshot: AppSnapshot;
  export let settings: UiSettings;
  export let context: {
    task: TaskKind | '';
    content: Exclude<ContentKind, 'face'>;
    quality: Quality | '';
    slot: 'primary' | 'fix' | 'face' | 'browse' | 'edit';
    fix?: Exclude<FixKind, 'faces'>;
  };
  export let device: DeviceInfo | undefined;
  export let activeDownload = '';
  export let downloadProgress = 0;
  export let busy = false;
  export let close: () => void;
  export let useForJob: (model: CatalogModel) => void;
  export let useEditModel: (model: EditModel) => void;
  export let pin: (model: CatalogModel, quality: 'quick' | 'best') => void;
  export let download: (
    model: CatalogModel | CatalogVideoModel | EditModel,
    accepted?: boolean,
  ) => Promise<void>;
  export let importFile: (model: CatalogModel, accepted: boolean) => Promise<void>;
  export let remove: (model: CatalogModel | EditModel) => Promise<void>;
  export let chooseCustom: () => Promise<void>;
  export let openLicense: (modelId: string) => Promise<void>;
  export let openSource: (modelId: string) => Promise<void>;

  type GroupId =
    'job' | 'photos' | 'illustration' | 'faces' | 'fix' | 'video' | 'edit' | 'installed' | 'own';

  let group: GroupId =
    context.slot === 'browse' ? 'photos' : context.slot === 'edit' ? 'edit' : 'job';
  let selectedId = '';
  let termsAccepted = false;
  let showHeavyEdit = false;

  $: models = snapshot.catalog.models;
  $: videoModels = snapshot.catalog.video_models;
  $: editModels = snapshot.edit.models;
  $: editGpu = editDevice(snapshot.capabilities, settings);
  $: editLimit = editSizeLimit(snapshot.capabilities, editGpu);
  $: editFit = (model: EditModel) => editFitFor(model, snapshot.capabilities, editGpu);
  $: installedEditModels = editModels.filter((model) => model.installed);
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
      return models.filter(
        (model) =>
          model.native_scale === 1 &&
          !model.purposes.includes('face') &&
          (!fix || fixesOf(model).includes(fix)),
      );
    }
    const candidates = modelsForTask(snapshot, context.task);
    if (context.task === 'denoise') return candidates;
    const matches = (model: CatalogModel): number =>
      context.content === 'illustration'
        ? model.purposes.includes('illustration') || model.purposes.includes('anime')
          ? 2
          : model.purposes.includes('general')
            ? 1
            : 0
        : model.purposes.includes('photo')
          ? 2
          : model.purposes.includes('general')
            ? 1
            : 0;
    return candidates
      .filter((model) => matches(model) > 0)
      .sort(
        (left, right) =>
          matches(right) - matches(left) ||
          right.quality_tier - left.quality_tier ||
          right.speed_tier - left.speed_tier,
      );
  })();
  $: groups = [
    ...(context.slot !== 'browse' &&
    context.slot !== 'edit' &&
    context.task &&
    context.task !== 'edit'
      ? [{ id: 'job' as GroupId, label: 'For this job', count: jobModels.length }]
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
      id: 'faces' as GroupId,
      label: 'Faces',
      count: models.filter((model) => model.purposes.includes('face')).length,
    },
    {
      id: 'fix' as GroupId,
      label: 'Fix noise, blur, JPEG',
      count: models.filter((model) => model.native_scale === 1 && !model.purposes.includes('face'))
        .length,
    },
    { id: 'video' as GroupId, label: 'Video (Labs)', count: videoModels.length },
    { id: 'edit' as GroupId, label: 'Edit with a prompt', count: editModels.length },
    {
      id: 'installed' as GroupId,
      label: 'Installed',
      count: models.filter((model) => model.installed).length + installedEditModels.length,
    },
    { id: 'own' as GroupId, label: 'Your own file', count: 0 },
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
      case 'faces':
        return models.filter((model) => model.purposes.includes('face'));
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
    installedEditModels.reduce((total, model) => total + model.total_size_bytes, 0);
  $: selectedEdit =
    editModels.find((model) => model.model_id === selectedId) ??
    (group === 'edit'
      ? (editModels.find((model) => model.model_id === settings.edit_model_id) ??
        editFamilies.flatMap((family) => family.fitting)[0] ??
        editModels[0])
      : undefined);
  $: selected = selectedEdit
    ? undefined
    : (models.find((model) => model.model_id === selectedId) ?? rows[0]);
  $: selectedVideo = videoModels.find((model) => model.model_id === selectedId);
  $: quickId = context.task
    ? choosePresetModel(snapshot, context.task, 'quick', context.content, settings.preset_pins)
        ?.model_id
    : '';
  $: bestId = context.task
    ? choosePresetModel(snapshot, context.task, 'best', context.content, settings.preset_pins)
        ?.model_id
    : '';
  $: canUseForJob = (model: CatalogModel): boolean =>
    context.slot !== 'browse' &&
    jobModels.some((candidate) => candidate.model_id === model.model_id);
  $: canPin = (model: CatalogModel): boolean =>
    context.slot === 'primary' &&
    context.task !== 'denoise' &&
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

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') close();
  }
</script>

<div class="modal-backdrop library-backdrop" role="presentation" on:keydown={onKeydown}>
  <div class="sheet" role="dialog" aria-modal="true" aria-labelledby="library-title" tabindex="-1">
    <div class="sheet-head">
      <h2 id="library-title">Model library</h2>
      {#if context.slot === 'edit'}
        <span class="pill quick">Choosing for Edit</span>
      {:else if context.slot !== 'browse' && context.task}
        <span class="pill quick"
          >Choosing for {context.task === 'video'
            ? 'Video'
            : context.task === 'denoise'
              ? 'Restore'
              : 'Upscale'}{context.slot === 'fix'
            ? ' · fix'
            : context.slot === 'face'
              ? ' · faces'
              : ` · ${context.quality === 'quick' ? 'Quick' : context.quality === 'best' ? 'Best' : 'Custom'} · ${context.content === 'illustration' ? 'Illustration' : 'Photo'}`}</span
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
    <div class="sheet-body">
      <nav class="rail" aria-label="Model groups">
        {#each groups as item (item.id)}
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

      <section class="list" aria-label="Models">
        {#if group === 'video'}
          <div class="list-head">Video · Labs<span>SeedVR2 engines</span></div>
          {#each videoModels as model (model.model_id)}
            <button
              class="row"
              type="button"
              class:selected={selectedId === model.model_id}
              aria-pressed={selectedId === model.model_id}
              on:click={() => {
                selectedId = model.model_id;
              }}
            >
              <div>
                <div class="name">{model.name}</div>
                <div class="role">{model.description}</div>
                <div class="pills">
                  <span class="pill labs">Labs</span><span class="pill">{model.license_name}</span>
                </div>
              </div>
              <div class="right">
                <b>{formatBytes(model.total_size_bytes)}</b>{#if model.installed}<span class="ok"
                    >Installed</span
                  >{:else}<span>Not downloaded</span>{/if}
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
              <button
                class="row"
                class:dimmed={editFit(model) === 'too_heavy'}
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
                  <b>{formatBytes(model.total_size_bytes)}</b>{#if model.installed}<span class="ok"
                      >Installed</span
                    >{/if}
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
        {:else if group === 'own'}
          <div class="list-head">Your own file</div>
          <div class="own-file">
            <p>
              Use a checkpoint you already have. Safetensors is strongly recommended; pickle-based
              files run the publisher's code inside the inference worker and need an explicit
              confirmation.
            </p>
            <button class="button" type="button" on:click={() => void chooseCustom()}
              >Choose checkpoint…</button
            >
          </div>
        {:else}
          <div class="list-head">
            {group === 'job'
              ? 'FOR THIS JOB'
              : group === 'installed'
                ? `INSTALLED · ${formatBytes(installedBytes)}`
                : groups.find((item) => item.id === group)?.label}<span
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
                <div class="meters" aria-hidden="true">
                  <div class="meter">
                    Quality<span class="dots"
                      >{#each [1, 2, 3, 4] as tier}<i class:f={model.quality_tier >= tier}
                        ></i>{/each}</span
                    >
                  </div>
                  <div class="meter">
                    Speed<span class="dots"
                      >{#each [1, 2, 3, 4] as tier}<i
                          class:f={model.speed_tier >= (tier === 4 ? 3 : tier) && tier <= 3}
                        ></i>{/each}</span
                    >
                  </div>
                </div>
                <div class="right">
                  <b>{formatBytes(model.size_bytes)}</b>{#if model.installed}<span class="ok"
                      >Installed</span
                    >{:else if !model.automated_download_allowed}<span>Your file</span>{/if}
                </div>
              </button>
            {/if}
          {/each}
          {#if group === 'installed'}
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
          {#if !rows.length && !(group === 'installed' && installedEditModels.length)}
            <p class="library-empty">
              Nothing verified is listed here yet. Import your own file, or suggest a model on the
              project page.
            </p>
          {/if}
        {/if}
      </section>

      <aside class="detail" aria-label="Model details">
        {#if group === 'video' && selectedVideo}
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
              <dd>{selectedVideo.engine_kind}</dd>
            </div>
            <div>
              <dt>Window</dt>
              <dd>{selectedVideo.temporal_window} frames</dd>
            </div>
          </dl>
          <div class="actions">
            {#if !selectedVideo.installed}
              <button
                class="button primary"
                type="button"
                disabled={busy ||
                  (Boolean(activeDownload) && activeDownload !== selectedVideo.model_id)}
                on:click={() => void download(selectedVideo)}
                >{activeDownload === selectedVideo.model_id
                  ? `Downloading ${Math.round(downloadProgress)}% · Cancel`
                  : `Download ${formatBytes(selectedVideo.total_size_bytes)}`}</button
              >
            {:else}
              <div class="installed-badge">✓ Installed · integrity checked before use</div>
            {/if}
          </div>
        {:else if selectedEdit && group !== 'own'}
          {@const fit = editFit(selectedEdit)}
          <div>
            <h3>{selectedEdit.name}</h3>
            <div class="role">
              {editFamilyLabel(selectedEdit.family)} · {selectedEdit.files.length} components
            </div>
          </div>
          <p class="model-description">
            {EDIT_FAMILIES.find((family) => family.id === selectedEdit.family)?.summary.split(
              ' · ',
            )[0]}. {editVariantName(selectedEdit)} size: smaller sizes download faster and need less memory,
            but can lose some edit fidelity.
          </p>
          <dl class="facts">
            <div>
              <dt>Download</dt>
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
            <div>
              <dt>License</dt>
              <dd>
                <button
                  class="link-button"
                  type="button"
                  on:click={() => void openLicense(selectedEdit.model_id)}
                  >{selectedEdit.license_name} ↗</button
                >
              </dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>
                <button
                  class="link-button"
                  type="button"
                  on:click={() => void openSource(selectedEdit.model_id)}>Model source ↗</button
                >
              </dd>
            </div>
            <div>
              <dt>Format</dt>
              <dd>GGUF {selectedEdit.quantization} · {selectedEdit.files.length} files</dd>
            </div>
            <div>
              <dt>Runtime</dt>
              <dd>stable-diffusion.cpp · {editRuntimeLabel(editGpu)}</dd>
            </div>
            <div>
              <dt>Integrity</dt>
              <dd>SHA-256 checked before use</dd>
            </div>
            <div>
              <dt>Commercial use</dt>
              <dd>
                {selectedEdit.terms_acceptance_required
                  ? 'Research license · review the terms'
                  : 'Allowed'}
              </dd>
            </div>
          </dl>
          <div class="actions">
            {#if !selectedEdit.installed}
              {#if selectedEdit.terms_acceptance_required}
                <label class="terms"
                  ><input type="checkbox" bind:checked={termsAccepted} /> I reviewed the Qwen Research
                  License and accept its terms.</label
                >
              {/if}
              <button
                class="button primary"
                type="button"
                disabled={busy ||
                  (Boolean(activeDownload) && activeDownload !== selectedEdit.model_id) ||
                  (selectedEdit.terms_acceptance_required &&
                    !termsAccepted &&
                    activeDownload !== selectedEdit.model_id)}
                on:click={() => void download(selectedEdit, termsAccepted)}
                >{activeDownload === selectedEdit.model_id
                  ? `Downloading ${Math.round(downloadProgress)}% · Cancel`
                  : `Download ${formatBytes(selectedEdit.total_size_bytes)}`}</button
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
          </dl>
          {#if device && fitFor(selected, device) !== 'unknown'}
            <div class="fit">
              <i class:warn={fitFor(selected, device) !== 'runs'}></i>{FIT_LABELS[
                fitFor(selected, device)
              ]} · {device.name}
            </div>
          {/if}
          <dl class="kv">
            <div>
              <dt>License</dt>
              <dd>
                <button
                  class="link-button"
                  type="button"
                  on:click={() => void openLicense(selected.model_id)}
                  >{selected.license_name} ↗</button
                >
              </dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>
                <button
                  class="link-button"
                  type="button"
                  on:click={() => void openSource(selected.model_id)}>Model source ↗</button
                >
              </dd>
            </div>
            <div>
              <dt>Author</dt>
              <dd>{selected.author}</dd>
            </div>
            <div>
              <dt>Architecture</dt>
              <dd>{selected.architecture} · Spandrel</dd>
            </div>
            <div>
              <dt>File</dt>
              <dd title={selected.filename}>{selected.filename}</dd>
            </div>
            <div>
              <dt>Integrity</dt>
              <dd>SHA-256 checked before use</dd>
            </div>
            <div>
              <dt>Commercial use</dt>
              <dd>
                {selected.commercial_use_allowed === true
                  ? selected.attribution_required
                    ? 'Allowed · attribution kept'
                    : 'Allowed'
                  : selected.commercial_use_allowed === false
                    ? 'Not allowed'
                    : 'Rights unresolved'}
              </dd>
            </div>
          </dl>
          <div class="actions">
            {#if !selected.installed && selected.automated_download_allowed}
              <button
                class="button primary"
                type="button"
                disabled={busy || (Boolean(activeDownload) && activeDownload !== selected.model_id)}
                on:click={() => void download(selected)}
                >{activeDownload === selected.model_id
                  ? `Downloading ${Math.round(downloadProgress)}% · Cancel`
                  : `Download ${formatBytes(selected.size_bytes)}`}</button
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
            {#if canUseForJob(selected)}
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
        {:else}
          <p class="model-description">
            Select a model to see what it is for and where it comes from.
          </p>
        {/if}
      </aside>
    </div>
  </div>
</div>
