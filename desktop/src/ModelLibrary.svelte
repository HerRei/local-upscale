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
  import type {
    AppSnapshot,
    CatalogModel,
    CatalogVideoModel,
    ContentKind,
    DeviceInfo,
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
    slot: 'primary' | 'fix' | 'face' | 'browse';
    fix?: Exclude<FixKind, 'faces'>;
  };
  export let device: DeviceInfo | undefined;
  export let activeDownload = '';
  export let downloadProgress = 0;
  export let busy = false;
  export let close: () => void;
  export let useForJob: (model: CatalogModel) => void;
  export let pin: (model: CatalogModel, quality: 'quick' | 'best') => void;
  export let download: (model: CatalogModel | CatalogVideoModel) => Promise<void>;
  export let importFile: (model: CatalogModel, accepted: boolean) => Promise<void>;
  export let remove: (model: CatalogModel) => Promise<void>;
  export let chooseCustom: () => Promise<void>;
  export let openLicense: (modelId: string) => Promise<void>;
  export let openSource: (modelId: string) => Promise<void>;

  type GroupId =
    'job' | 'photos' | 'illustration' | 'faces' | 'fix' | 'video' | 'installed' | 'own';

  let group: GroupId = context.slot === 'browse' ? 'photos' : 'job';
  let selectedId = '';
  let termsAccepted = false;

  $: models = snapshot.catalog.models;
  $: videoModels = snapshot.catalog.video_models;
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
    ...(context.slot !== 'browse' && context.task
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
    {
      id: 'installed' as GroupId,
      label: 'Installed',
      count: models.filter((model) => model.installed).length,
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
  $: installedBytes = models
    .filter((model) => model.installed)
    .reduce((total, model) => total + model.size_bytes, 0);
  $: selected = models.find((model) => model.model_id === selectedId) ?? rows[0];
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

  function selectRow(model: CatalogModel): void {
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
      {#if context.slot !== 'browse' && context.task}
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
        <span
          >Catalog {snapshot.catalog.catalog_revision.slice(0, 7)} · {models.length +
            videoModels.length} models</span
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
          <div class="list-head">VIDEO · LABS<span>SeedVR2 engines</span></div>
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
        {:else if group === 'own'}
          <div class="list-head">YOUR OWN FILE</div>
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
                : groups.find((item) => item.id === group)?.label.toUpperCase()}<span
              >{group === 'job' ? 'sorted by quality' : ''}</span
            >
          </div>
          {#each rows as model (model.model_id)}
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
                <div class="pills">
                  {#if model.model_id === quickId}<span class="pill quick">Quick</span>{/if}
                  {#if model.model_id === bestId}<span class="pill best">Best</span>{/if}
                  {#if model.support_tier === 'labs'}<span class="pill labs">Labs</span>{/if}
                  <span class="pill" class:warn={model.commercial_use_allowed !== true}
                    >{rightsLabel(model)}</span
                  >
                  {#if fitFor(model, device) === 'heavy'}<span class="pill warn">Heavy here</span
                    >{:else if fitFor(model, device) === 'too_heavy'}<span class="pill bad"
                      >Too heavy</span
                    >{:else if fitFor(model, device) === 'runs'}<span class="pill ok"
                      >Runs well</span
                    >{/if}
                </div>
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
                  >{:else if !model.automated_download_allowed}<span>Your file</span>{:else}<span
                    >Not downloaded</span
                  >{/if}
              </div>
            </button>
          {/each}
          {#if !rows.length}
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
        {:else if group !== 'own' && selected}
          <div>
            <h3>{displayName(selected)}</h3>
            <div class="role">{selected.role || selected.name}</div>
            <div class="pills">
              {#if selected.support_tier === 'labs'}<span class="pill labs">Labs</span>{/if}
              <span class="pill">{selected.license_name}</span>
              {#if fitFor(selected, device) !== 'unknown'}<span
                  class="pill"
                  class:ok={fitFor(selected, device) === 'runs'}
                  class:warn={fitFor(selected, device) === 'heavy'}
                  class:bad={fitFor(selected, device) === 'too_heavy'}
                  >{FIT_LABELS[fitFor(selected, device)].split(' · ')[0]}</span
                >{/if}
            </div>
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
            <div>
              <dt>Architecture</dt>
              <dd>{selected.architecture} · Spandrel</dd>
            </div>
            <div>
              <dt>Author</dt>
              <dd>{selected.author}</dd>
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
