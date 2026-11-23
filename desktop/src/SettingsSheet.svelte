<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import FileName from './FileName.svelte';
  import HelpTip from './HelpTip.svelte';
  import ThemePicker from './ThemePicker.svelte';
  import * as api from './lib/api';
  import { engineDisplayName } from './lib/engines';
  import { detectedFfmpeg, systemCodecs } from './lib/ffmpeg';
  import { formatBytes } from './lib/format';
  import { groupedHardware, optionLabel } from './lib/hardwareRoutes';
  import { tabFromKey } from './lib/keyboard';
  import { modal } from './lib/modal';
  import { hardwareChoices, hardwarePatch, selectedHardware } from './lib/neuralEngine';
  import { detectPlatform } from './lib/platform';
  import type {
    CapabilityInfo,
    EngineManagerSnapshot,
    ModelMoveProgress,
    SettingsSection,
    UiSettings,
  } from './lib/types';
  import { refreshUpdateStatus, updateState } from './lib/updates';

  export let settings: UiSettings;
  export let capabilities: CapabilityInfo;
  export let engines: EngineManagerSnapshot | undefined = undefined;
  export let updateStatus: api.UpdateStatus | undefined = undefined;
  export let section: SettingsSection = 'general';
  export let focusTarget: 'hardware' | 'ffmpeg' | null = null;
  export let settingsLocked = false;
  export let defaultModelDirectory = '';
  export let modelDirectoryMissing = false;
  export let movingModels = false;
  export let modelMoveProgress: ModelMoveProgress | null = null;
  export let cacheSize: number | null = null;
  export let cacheBusy = false;
  export let linuxMenuAvailable = false;
  export let linuxMenuOffer = false;
  export let linuxMenuInstalled = false;
  export let linuxMenuBusy = false;
  export let setPreference: (
    name: api.PreferenceName,
    value:
      | boolean
      | UiSettings['appearance']
      | UiSettings['interface_scale']
      | NonNullable<UiSettings['stopped_job_result']>,
  ) => void;
  export let updateSettings: (patch: Partial<UiSettings>) => void;
  export let chooseModelFolder: () => Promise<void> = async () => undefined;
  export let resetModelFolder: () => Promise<void> = async () => undefined;
  export let clearCache: () => Promise<void> = async () => undefined;
  export let loadCacheSize: () => void = () => undefined;
  export let refreshCapabilities: () => Promise<boolean> = async () => true;
  export let onOpenEngineManager: () => void = () => undefined;
  export let onOpenIntegrations: () => void = () => undefined;
  export let onOpenUpdates: () => void = () => undefined;
  export let onInstallLinuxMenu: () => Promise<void> = async () => undefined;
  export let onUninstallLinuxMenu: () => Promise<void> = async () => undefined;
  export let onClose: () => void;

  const isMac = typeof navigator !== 'undefined' && /Mac/.test(navigator.platform);
  const platform = detectPlatform();

  let panelEl: HTMLElement | undefined;
  let tabButtons: HTMLButtonElement[] = [];
  let refreshing = false;
  let refreshed = false;
  let refreshedTimer: ReturnType<typeof setTimeout> | undefined;
  let ffmpegNote = '';
  let wasCacheBusy = false;
  let cacheLoadedForVisit = false;

  $: activeUpdateStatus = updateStatus ?? $updateState;
  $: storeBuild = Boolean(activeUpdateStatus.managed_by_store);
  $: sections = [
    { id: 'general' as const, label: 'General' },
    { id: 'appearance' as const, label: 'Appearance' },
    { id: 'performance' as const, label: 'Performance' },
    { id: 'storage' as const, label: 'Storage' },
    { id: 'privacy' as const, label: storeBuild ? 'Privacy' : 'Privacy & updates' },
  ];

  $: hardwareGroups = groupedHardware(hardwareChoices(capabilities));
  $: hardwareValue = selectedHardware(settings, capabilities);
  $: onnxGpu =
    settings.device_id.startsWith('directml:') || settings.device_id.startsWith('openvino:');
  $: npuSelected = capabilities.devices.some(
    (device) => device.id === settings.device_id && device.type === 'npu',
  );
  $: system = systemCodecs(capabilities);
  $: foundFfmpeg = detectedFfmpeg(capabilities);
  $: notifyOn = settings.notify_on_finish ?? true;
  $: menuBarOn = settings.show_in_menu_bar ?? true;
  $: showLinuxMenuRow = linuxMenuAvailable || linuxMenuOffer || linuxMenuInstalled;

  $: if (section === 'storage' && !cacheLoadedForVisit) {
    cacheLoadedForVisit = true;
    loadCacheSize();
  } else if (section !== 'storage') {
    cacheLoadedForVisit = false;
  }

  $: if (cacheBusy) {
    wasCacheBusy = true;
  } else if (wasCacheBusy && section === 'storage') {
    wasCacheBusy = false;
    loadCacheSize();
  }

  onMount(() => {
    if (!updateStatus) {
      void refreshUpdateStatus().catch(() => undefined);
    }
  });

  onDestroy(() => clearTimeout(refreshedTimer));

  function lockDescribed(descId: string): string {
    return settingsLocked ? `${descId} settings-lock-note` : descId;
  }

  function requestClose(): void {
    if (!movingModels) onClose();
  }

  function closeFromBackdrop(event: MouseEvent): void {
    if (!movingModels && event.target === event.currentTarget) onClose();
  }

  async function selectSection(next: SettingsSection): Promise<void> {
    if (section === next) return;
    section = next;
    await tick();
    if (panelEl) panelEl.scrollTop = 0;
  }

  async function onTabKeydown(event: KeyboardEvent, index: number): Promise<void> {
    const nextIndex = tabFromKey(event.key, index, sections.length, 'vertical');
    if (nextIndex === null) return;
    event.preventDefault();
    await selectSection(sections[nextIndex].id);
    tabButtons[nextIndex]?.focus();
  }

  async function refresh(): Promise<void> {
    if (refreshing) return;
    refreshing = true;
    try {
      if (!(await refreshCapabilities())) return;
      refreshed = true;
      clearTimeout(refreshedTimer);
      refreshedTimer = setTimeout(() => (refreshed = false), 1500);
    } finally {
      refreshing = false;
    }
  }

  async function findFFmpeg(): Promise<void> {
    try {
      const found = await api.detectExternalFFmpeg();
      if (found.length) {
        updateSettings({ external_ffmpeg_path: found[0] });
        ffmpegNote = found.length > 1 ? `Also found: ${found.slice(1).join(', ')}` : '';
      } else {
        ffmpegNote =
          'No FFmpeg was found. Install it (for example with Homebrew, winget or your package manager), then choose it here.';
      }
    } catch (error) {
      ffmpegNote = String(error);
    }
  }

  async function pickFFmpeg(): Promise<void> {
    try {
      const path = await api.chooseExternalFFmpeg(settings.external_ffmpeg_path ?? '');
      if (path) {
        updateSettings({ external_ffmpeg_path: path });
        ffmpegNote = '';
      }
    } catch (error) {
      ffmpegNote = String(error);
    }
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
<div class="modal-backdrop settings-backdrop" role="presentation" on:click={closeFromBackdrop}>
  <div
    class="sheet settings-sheet"
    role="dialog"
    aria-modal="true"
    aria-labelledby="settings-title"
    tabindex="-1"
    use:modal={requestClose}
  >
    <header class="sheet-head">
      <h2 id="settings-title">Settings</h2>
    </header>

    <div class="settings-body">
      <div
        class="rail settings-rail"
        role="tablist"
        aria-orientation="vertical"
        aria-label="Settings sections"
      >
        {#each sections as item, index}
          <button
            type="button"
            class="rail-item"
            class:active={section === item.id}
            id={`settings-tab-${item.id}`}
            role="tab"
            aria-selected={section === item.id}
            aria-controls={`settings-panel-${item.id}`}
            tabindex={section === item.id ? 0 : -1}
            data-autofocus={section === item.id && !focusTarget ? '' : undefined}
            bind:this={tabButtons[index]}
            on:click={() => void selectSection(item.id)}
            on:keydown={(event) => void onTabKeydown(event, index)}>{item.label}</button
          >
        {/each}
      </div>

      <div
        class="settings-panel"
        bind:this={panelEl}
        id={`settings-panel-${section}`}
        role="tabpanel"
        aria-labelledby={`settings-tab-${section}`}
      >
        {#if settingsLocked}
          <p class="settings-lock-note" id="settings-lock-note" role="status">
            Some settings are locked while the queue runs.
          </p>
        {/if}

        {#if section === 'general'}
          <h3 class="settings-section-heading">General</h3>
          <div class="setting-row">
            <div class="setting-copy">
              <label class="setting-label" for="settings-menu-bar"
                >Show LocalSR in the {isMac ? 'menu bar' : 'system tray'}</label
              >
              <p class="setting-desc" id="settings-menu-bar-desc">
                Shows queue progress and pause or resume controls.
              </p>
            </div>
            <input
              id="settings-menu-bar"
              class="switch"
              type="checkbox"
              role="switch"
              aria-describedby="settings-menu-bar-desc"
              checked={menuBarOn}
              on:change={(event) => setPreference('show_in_menu_bar', event.currentTarget.checked)}
            />
          </div>

          {#if isMac}
            <div class="setting-row">
              <div class="setting-copy">
                <label class="setting-label" for="settings-menu-bar-mono"
                  >Monochrome menu bar icon</label
                >
                <p class="setting-desc" id="settings-menu-bar-mono-desc">
                  Black or white like the system's own icons. Off shows the coloured app icon.
                </p>
              </div>
              <input
                id="settings-menu-bar-mono"
                class="switch"
                type="checkbox"
                role="switch"
                aria-describedby="settings-menu-bar-mono-desc"
                disabled={!menuBarOn}
                checked={settings.menu_bar_icon_monochrome ?? true}
                on:change={(event) =>
                  setPreference('menu_bar_icon_monochrome', event.currentTarget.checked)}
              />
            </div>
          {/if}

          <div class="setting-row">
            <div class="setting-copy">
              <label class="setting-label" for="settings-notify-finish"
                >Notify when a file finishes</label
              >
              <p class="setting-desc" id="settings-notify-finish-desc">
                Shows a desktop notification with the file name.
              </p>
            </div>
            <input
              id="settings-notify-finish"
              class="switch"
              type="checkbox"
              role="switch"
              aria-describedby="settings-notify-finish-desc"
              checked={notifyOn}
              on:change={(event) => setPreference('notify_on_finish', event.currentTarget.checked)}
            />
          </div>

          <div class="setting-row">
            <div class="setting-copy">
              <label class="setting-label" for="settings-completion-sound"
                >Play a sound with the notification</label
              >
              <p class="setting-desc" id="settings-completion-sound-desc">
                {notifyOn
                  ? 'Adds the system notification sound to finish and failure notices.'
                  : 'Turn on "Notify when a file finishes" to play a sound with finish notifications.'}
              </p>
            </div>
            <input
              id="settings-completion-sound"
              class="switch"
              type="checkbox"
              role="switch"
              aria-describedby="settings-completion-sound-desc"
              disabled={!notifyOn}
              checked={settings.completion_sound ?? false}
              on:change={(event) => setPreference('completion_sound', event.currentTarget.checked)}
            />
          </div>

          <div class="setting-row">
            <div class="setting-copy">
              <label class="setting-label" for="settings-live-preview"
                >Show previews while a job runs</label
              >
              <p class="setting-desc" id="settings-live-preview-desc">
                Shows reduced pictures of the result, at most two a second. The job does not wait
                for them.
              </p>
            </div>
            <input
              id="settings-live-preview"
              class="switch"
              type="checkbox"
              role="switch"
              aria-describedby={lockDescribed('settings-live-preview-desc')}
              disabled={settingsLocked}
              checked={settings.enable_live_preview}
              on:change={(event) =>
                updateSettings({ enable_live_preview: event.currentTarget.checked })}
            />
          </div>

          <div class="setting-row">
            <div class="setting-copy">
              <label class="setting-label" for="settings-stopped-job-result"
                >When you stop a job</label
              >
              <p class="setting-desc" id="settings-stopped-job-result-desc">
                Applies to videos: the frames already finished can be kept as a playable video.
                Photos and edits that are stopped are always discarded; finished files in a queue
                always stay.
              </p>
            </div>
            <div class="setting-control">
              <select
                id="settings-stopped-job-result"
                aria-describedby="settings-stopped-job-result-desc"
                value={settings.stopped_job_result ?? 'ask'}
                on:change={(event) =>
                  setPreference(
                    'stopped_job_result',
                    event.currentTarget.value as NonNullable<UiSettings['stopped_job_result']>,
                  )}
              >
                <option value="ask">Ask</option>
                <option value="keep">Keep the finished part</option>
                <option value="discard">Discard</option>
              </select>
            </div>
          </div>

          <div class="setting-row">
            <div class="setting-copy">
              <span class="setting-label" id="settings-integrations-label"
                >File manager actions</span
              >
              <p class="setting-desc" id="settings-integrations-desc">
                Optional right-click actions for your file manager.
              </p>
            </div>
            <button
              type="button"
              class="button"
              aria-describedby="settings-integrations-desc"
              on:click={onOpenIntegrations}>Manage…</button
            >
          </div>

          {#if showLinuxMenuRow}
            <div class="setting-row">
              <div class="setting-copy">
                <span class="setting-label" id="settings-linux-menu-label">Applications menu</span>
                <p class="setting-desc" id="settings-linux-menu-desc">
                  Lists LocalSR in your desktop's applications menu.
                </p>
              </div>
              <button
                type="button"
                class="button"
                aria-describedby="settings-linux-menu-desc"
                disabled={linuxMenuBusy}
                on:click={() =>
                  void (linuxMenuInstalled ? onUninstallLinuxMenu() : onInstallLinuxMenu())}
                >{linuxMenuInstalled ? 'Remove' : 'Add'}</button
              >
            </div>
          {/if}
        {:else if section === 'appearance'}
          <h3 class="settings-section-heading">Appearance</h3>
          <div class="setting-block settings-block">
            <span class="setting-label" id="settings-theme-heading">Theme</span>
            <p class="setting-desc" id="settings-theme-desc">
              Match System follows your computer's light or dark mode.
            </p>
            <ThemePicker
              appearance={settings.appearance}
              colourBlind={settings.colour_blind_palette}
              labelledBy="settings-theme-heading"
              on:choose={(event) => setPreference('appearance', event.detail)}
            />
          </div>

          <div class="setting-row">
            <div class="setting-copy">
              <label class="setting-label" for="settings-colour-blind"
                >Colour-blind friendly colours</label
              >
              <p class="setting-desc" id="settings-colour-blind-desc">
                Uses colours that stay distinct with colour vision deficiencies.
              </p>
            </div>
            <input
              id="settings-colour-blind"
              class="switch"
              type="checkbox"
              role="switch"
              aria-describedby="settings-colour-blind-desc"
              checked={settings.colour_blind_palette}
              on:change={(event) =>
                setPreference('colour_blind_palette', event.currentTarget.checked)}
            />
          </div>

          <div class="setting-row">
            <div class="setting-copy">
              <label class="setting-label" for="settings-interface-scale">Interface text</label>
              <p class="setting-desc" id="settings-interface-scale-desc">
                Enlarges the app's text and controls. Your pictures are not affected.
              </p>
            </div>
            <div class="setting-control">
              <select
                id="settings-interface-scale"
                aria-describedby="settings-interface-scale-desc"
                value={String(settings.interface_scale)}
                on:change={(event) =>
                  setPreference(
                    'interface_scale',
                    Number(event.currentTarget.value) as UiSettings['interface_scale'],
                  )}
              >
                <option value="100">100%</option>
                <option value="110">110%</option>
                <option value="125">125%</option>
              </select>
            </div>
          </div>
        {:else if section === 'performance'}
          <h3 class="settings-section-heading">Performance</h3>
          <div class="setting-block settings-block">
            <div class="setting-row">
              <div class="setting-copy">
                <span class="label-with-tip">
                  <label class="setting-label" for="settings-device">Hardware</label>
                  <HelpTip
                    label="Hardware"
                    text="Which processor runs the model. A GPU is much faster than the CPU. On a Mac, Neural Engine runs the models it supports, such as Quick, on Apple's AI chip; for other models only the Apple GPU and the CPU are listed, and your choice is kept for the next model that supports it."
                  />
                </span>
                <p class="setting-desc" id="settings-hardware-desc">
                  Which processor runs the models. A model that cannot use your choice runs on the
                  next best one.
                </p>
              </div>
              <div class="setting-control">
                <select
                  id="settings-device"
                  title={optionLabel(hardwareGroups, hardwareValue) || undefined}
                  aria-describedby={lockDescribed('settings-hardware-desc')}
                  disabled={settingsLocked}
                  data-autofocus={focusTarget === 'hardware' ? '' : undefined}
                  value={hardwareValue}
                  on:change={(event) => updateSettings(hardwarePatch(event.currentTarget.value))}
                >
                  {#each hardwareGroups as group}
                    {#if group.options.length > 1}
                      <optgroup label={group.name}>
                        {#each group.options as option}
                          <option value={option.id}>{option.label}</option>
                        {/each}
                      </optgroup>
                    {:else}
                      <option value={group.options[0].id}>{group.name}</option>
                    {/if}
                  {/each}
                </select>
                <button
                  type="button"
                  class="button"
                  disabled={refreshing}
                  on:click={() => void refresh()}
                  >{refreshing ? 'Refreshing…' : refreshed ? 'Refreshed' : 'Refresh'}</button
                >
              </div>
            </div>
            {#if onnxGpu && !npuSelected}
              <p class="settings-note">
                {#if settings.device_id.startsWith('directml:')}DirectML has not been tested on real
                  hardware yet.{/if}
                The first time a model runs on this GPU, LocalSR checks once that it gives the same result
                as the CPU.
                {#if settings.device_id.startsWith('directml:')}Choose a smaller tile if shared
                  memory is tight.{/if}
              </p>
            {:else if npuSelected}
              <p class="settings-note">
                The NPU runs SPAN, Real-ESRGAN, FBCNN and NAFNet models; others run on the CPU.
                LocalSR compiles a model once for the NPU, which can take a minute, and checks once
                that its result matches the CPU's.
              </p>
            {/if}
          </div>

          {#if engines?.supported && !engines.managed_by_store}
            <div class="setting-row">
              <div class="setting-copy">
                <span class="setting-label">Engine</span>
                <p class="setting-desc" id="settings-engine-desc">
                  The inference runtime LocalSR uses on this computer.
                </p>
              </div>
              <div class="engine-value">
                <span class="engine-active-label">{engineDisplayName(engines.active_backend)}</span>
                <button type="button" class="link-button" on:click={onOpenEngineManager}
                  >Change…</button
                >
              </div>
            </div>
          {/if}

          <div class="setting-row">
            <div class="setting-copy">
              <label class="setting-label" for="settings-low-priority">Run at low priority</label>
              <p class="setting-desc" id="settings-low-priority-desc">
                {#if isMac}
                  Keeps other apps responsive. Jobs take about twice as long.
                {:else if platform === 'linux'}
                  Keeps other apps responsive. Jobs take longer. Switching it off takes full effect
                  the next time the engine starts.
                {:else}
                  Keeps other apps responsive. Jobs take longer.
                {/if}
              </p>
            </div>
            <input
              id="settings-low-priority"
              class="switch"
              type="checkbox"
              role="switch"
              aria-describedby="settings-low-priority-desc"
              checked={Boolean(settings.low_priority)}
              on:change={(event) => setPreference('low_priority', event.currentTarget.checked)}
            />
          </div>
        {:else if section === 'storage'}
          <h3 class="settings-section-heading">Storage</h3>
          <div class="setting-block settings-block">
            <span class="setting-label">Model folder</span>
            <p class="setting-desc" id="settings-model-folder-desc">
              Where downloaded models are stored. Changing it moves installed models there.
            </p>
            <div class="directory-field">
              <button
                type="button"
                class="directory-hit"
                disabled={settingsLocked || movingModels}
                aria-label={`Model folder ${settings.model_directory || defaultModelDirectory || 'Default folder'}`}
                aria-describedby={lockDescribed('settings-model-folder-desc')}
                on:click={() => void chooseModelFolder()}
              ></button>
              <div class="directory-text">
                <span
                  class="directory-path"
                  title={movingModels
                    ? modelMoveProgress
                      ? `Moving ${modelMoveProgress.moved_files} of ${modelMoveProgress.total_files} files…`
                      : 'Moving files…'
                    : settings.model_directory || defaultModelDirectory || 'Default folder'}
                  >{movingModels
                    ? modelMoveProgress
                      ? `Moving ${modelMoveProgress.moved_files} of ${modelMoveProgress.total_files} files…`
                      : 'Moving files…'
                    : settings.model_directory || defaultModelDirectory || 'Default folder'}</span
                >
              </div>
              <b
                >{movingModels
                  ? modelMoveProgress
                    ? `${Math.round(modelMoveProgress.progress)}%`
                    : '0%'
                  : 'Choose…'}</b
              >
            </div>
            {#if movingModels}
              <progress
                class="download-progress"
                max="100"
                value={modelMoveProgress?.progress ?? 0}
                aria-label="Moving models"
              ></progress>
            {:else if !modelDirectoryMissing && settings.model_directory}
              <div class="eyebrow-actions">
                <button
                  type="button"
                  class="link-button"
                  disabled={settingsLocked}
                  on:click={() => void resetModelFolder()}>Reset to default</button
                >
              </div>
            {/if}
          </div>

          <div class="setting-block settings-block">
            <span class="setting-label">External FFmpeg</span>
            <p class="setting-desc" id="settings-ffmpeg-desc">
              {#if system}
                Optional. On this computer, H.264 and HEVC videos open and export through {system.label}'s
                own codecs. FFmpeg is only needed for other formats (WMV, DivX, FLV) and for
                H.264/HEVC in MKV; one found on this computer is used automatically.
              {:else}
                Optional. An FFmpeg found on this computer is used automatically; select one here to
                use a different build. It is needed for H.264/HEVC export and for opening most phone
                and camera videos.
              {/if}
              LocalSR never downloads or bundles FFmpeg.
            </p>
            <div class="directory-field">
              <button
                id="settings-ffmpeg"
                type="button"
                class="directory-hit"
                disabled={settingsLocked}
                data-autofocus={focusTarget === 'ffmpeg' ? '' : undefined}
                aria-label="Choose FFmpeg"
                aria-describedby={lockDescribed('settings-ffmpeg-desc')}
                on:click={() => void pickFFmpeg()}
              ></button>
              <div class="directory-text">
                <span class="directory-path"
                  ><FileName
                    name={settings.external_ffmpeg_path ||
                      (foundFfmpeg.length ? `Found automatically: ${foundFfmpeg[0]}` : 'Not used')}
                  /></span
                >
              </div>
              <b>Choose…</b>
            </div>
            <div class="eyebrow-actions">
              <button
                type="button"
                class="button"
                disabled={settingsLocked}
                on:click={() => void findFFmpeg()}>Find installed</button
              >
              {#if settings.external_ffmpeg_path}
                <button
                  type="button"
                  class="link-button"
                  disabled={settingsLocked}
                  on:click={() => updateSettings({ external_ffmpeg_path: '' })}>Don’t use</button
                >
              {/if}
            </div>
            {#if ffmpegNote}
              <p class="settings-note" role="status">{ffmpegNote}</p>
            {/if}
          </div>

          <div class="setting-row">
            <div class="setting-copy">
              <span class="setting-label"
                >Model cache · {cacheSize === null
                  ? 'unavailable'
                  : cacheSize === 0
                    ? '0 B'
                    : formatBytes(cacheSize)}</span
              >
              <p class="setting-desc" id="settings-cache-desc">
                Compiled copies of models that make later runs start faster. Clearing is safe; they
                are rebuilt on the next run.
              </p>
            </div>
            <button
              type="button"
              class="button"
              aria-describedby={lockDescribed('settings-cache-desc')}
              disabled={cacheBusy || cacheSize === null || settingsLocked}
              on:click={() => void clearCache()}>Clear cache</button
            >
          </div>

          <div class="setting-row">
            <div class="setting-copy">
              <label class="setting-label" for="settings-auto-cache"
                >Remove unused caches automatically</label
              >
              <p class="setting-desc" id="settings-auto-cache-desc">
                Removes compiled model copies not used for 30 days, and the oldest ones above 5 GB,
                when LocalSR starts.
              </p>
            </div>
            <input
              id="settings-auto-cache"
              class="switch"
              type="checkbox"
              role="switch"
              aria-describedby="settings-auto-cache-desc"
              checked={settings.auto_cache_cleanup ?? true}
              on:change={(event) =>
                setPreference('auto_cache_cleanup', event.currentTarget.checked)}
            />
          </div>
        {:else if section === 'privacy'}
          <h3 class="settings-section-heading">{storeBuild ? 'Privacy' : 'Privacy & updates'}</h3>
          {#if activeUpdateStatus.configured && !storeBuild}
            <div class="setting-row">
              <div class="setting-copy">
                <label class="setting-label" for="settings-check-updates"
                  >Check for updates when LocalSR starts</label
                >
                <p class="setting-desc" id="settings-check-updates-desc">
                  Reads the update feed on GitHub Pages once at launch.
                </p>
              </div>
              <input
                id="settings-check-updates"
                class="switch"
                type="checkbox"
                role="switch"
                aria-describedby="settings-check-updates-desc"
                checked={settings.check_updates_at_launch ?? true}
                on:change={(event) =>
                  setPreference('check_updates_at_launch', event.currentTarget.checked)}
              />
            </div>

            <div class="setting-row">
              <div class="setting-copy">
                <label class="setting-label" for="settings-anonymous-count"
                  >Send an anonymous count with each update check</label
                >
                <p class="setting-desc" id="settings-anonymous-count-desc">
                  Sends app version, platform and channel. No ID, files or settings.
                </p>
              </div>
              <input
                id="settings-anonymous-count"
                class="switch"
                type="checkbox"
                role="switch"
                aria-describedby="settings-anonymous-count-desc"
                checked={settings.anonymous_update_count ?? false}
                on:change={(event) =>
                  setPreference('anonymous_update_count', event.currentTarget.checked)}
              />
            </div>
          {/if}

          {#if !storeBuild}
            <div class="setting-row">
              <div class="setting-copy">
                <span class="setting-label" id="settings-software-update-label"
                  >Software update</span
                >
                <p class="setting-desc" id="settings-software-update-desc">
                  Opens the updater, where you also choose the release channel.
                </p>
              </div>
              <button
                type="button"
                class="button"
                aria-describedby="settings-software-update-desc"
                on:click={onOpenUpdates}>Check for Updates…</button
              >
            </div>
          {/if}

          <div class="setting-row">
            <div class="setting-copy">
              <label class="setting-label" for="settings-crash-reports"
                >Offer to report crashes</label
              >
              <p class="setting-desc" id="settings-crash-reports-desc">
                After a crash, LocalSR asks whether to send a report.
              </p>
            </div>
            <input
              id="settings-crash-reports"
              class="switch"
              type="checkbox"
              role="switch"
              aria-describedby="settings-crash-reports-desc"
              checked={settings.crash_reports ?? false}
              on:change={(event) => setPreference('crash_reports', event.currentTarget.checked)}
            />
          </div>
        {/if}
      </div>
    </div>

    <footer class="settings-footer">
      {#if movingModels}
        <span class="settings-footer-note" role="status">Wait for the model move to finish.</span>
      {/if}
      <button type="button" class="button primary" disabled={movingModels} on:click={requestClose}
        >Done</button
      >
    </footer>
  </div>
</div>
