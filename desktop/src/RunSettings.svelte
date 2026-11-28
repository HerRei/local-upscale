<script lang="ts">
  import { onDestroy } from 'svelte';
  import HelpTip from './HelpTip.svelte';
  import { groupedHardware, optionLabel } from './lib/hardwareRoutes';
  import { hardwareChoices, hardwarePatch, selectedHardware } from './lib/neuralEngine';
  import { editingDevices, editDevice } from './lib/editing';
  import type { CapabilityInfo, CatalogModel, UiSettings } from './lib/types';

  export let settings: UiSettings;
  export let selectedModel: CatalogModel | undefined;
  export let capabilities: CapabilityInfo;
  export let usingTemporalVideo: boolean;
  export let updateSettings: (patch: Partial<UiSettings>) => void;
  /** The saved defaults from Settings; hardware and low priority here apply to this session. */
  export let defaults: UiSettings | undefined = undefined;
  export let onUseDefaults: () => void = () => undefined;
  /** Resolves true when the host took the request, false when it refused (and said so). */
  export let refreshCapabilities: () => Promise<boolean>;
  export let disabled = false;

  // ONNX Runtime GPU routes always run FP32, whatever the saved precision says.
  $: onnxGpu =
    settings.device_id.startsWith('directml:') || settings.device_id.startsWith('openvino:');
  $: npuSelected = capabilities.devices.some(
    (device) => device.id === settings.device_id && device.type === 'npu',
  );
  // An Intel NPU computes in FP16 whatever the saved precision says; a model it does not
  // run goes to the CPU, which runs FP32.
  $: npuRunning =
    npuSelected &&
    selectedHardware(settings, capabilities, selectedModel?.architecture) === settings.device_id;

  function hardwareGroupsFor(choice: UiSettings) {
    return groupedHardware(
      choice.task === 'edit'
        ? editingDevices(capabilities)
        : usingTemporalVideo
          ? capabilities.devices
          : hardwareChoices(capabilities, selectedModel?.architecture),
    );
  }

  function hardwareValueFor(choice: UiSettings): string {
    return choice.task === 'edit'
      ? (editDevice(capabilities, choice)?.id ?? '')
      : usingTemporalVideo
        ? choice.device_id
        : selectedHardware(choice, capabilities, selectedModel?.architecture);
  }

  $: hardwareGroups = hardwareGroupsFor(settings);
  $: hardwareValue = hardwareValueFor(settings);
  // A long entry (a GPU with its route) takes the row's full width, so the closed menu shows
  // the whole name and route instead of cutting them.
  $: hardwareStacked = hardwareGroups.some((group) =>
    group.options.some((option) => option.label.length > 22),
  );
  $: defaultHardware = defaults
    ? optionLabel(hardwareGroups, hardwareValueFor(defaults)) || hardwareValueFor(defaults)
    : '';
  $: hardwareDiffers = Boolean(defaults) && hardwareValueFor(defaults!) !== hardwareValue;
  $: priorityDiffers =
    Boolean(defaults) && Boolean(defaults!.low_priority) !== Boolean(settings.low_priority);

  let refreshing = false;
  let refreshed = false;
  let refreshedTimer: ReturnType<typeof setTimeout> | undefined;

  // The line says so for a moment once the host has taken the request; a refusal shows the
  // host's own message instead and no confirmation.
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

  onDestroy(() => clearTimeout(refreshedTimer));
</script>

<div class="run-settings" class:locked={disabled}>
  <div class="field-row" class:stacked={hardwareStacked}>
    <span class="label-with-tip"
      ><label for="device">Hardware</label><HelpTip
        label="Hardware"
        text="Which processor runs the model. A GPU is much faster than the CPU. On a Mac, Neural Engine runs the models it supports, such as Quick, on Apple's AI chip; for other models only the Apple GPU and the CPU are listed, and your choice is kept for the next model that supports it."
      /></span
    ><select
      id="device"
      {disabled}
      title={optionLabel(hardwareGroups, hardwareValue) || undefined}
      value={hardwareValue}
      on:change={(event) =>
        updateSettings(
          settings.task === 'edit' || usingTemporalVideo
            ? { device_id: event.currentTarget.value }
            : hardwarePatch(event.currentTarget.value, selectedModel?.architecture),
        )}
      >{#each hardwareGroups as group}{#if group.options.length > 1}<optgroup label={group.name}
            >{#each group.options as option}<option value={option.id}>{option.label}</option
              >{/each}</optgroup
          >{:else}<option value={group.options[0].id}>{group.name}</option>{/if}{/each}</select
    >
  </div>
  <!-- Not an <input> or <label>: this stays clickable and full-strength while a job locks the settings above. -->
  <div class="check-line">
    <span
      id="low-priority"
      role="checkbox"
      tabindex="0"
      aria-checked={Boolean(settings.low_priority)}
      class="check-row live-setting"
      on:click={() => updateSettings({ low_priority: !settings.low_priority })}
      on:keydown={(event) => {
        if (event.key === ' ' || event.key === 'Enter') {
          event.preventDefault();
          updateSettings({ low_priority: !settings.low_priority });
        }
      }}
      ><span class="live-checkbox" class:checked={Boolean(settings.low_priority)} aria-hidden="true"
      ></span><span class="live-setting-label">Run at low priority</span></span
    ><HelpTip
      label="Run at low priority"
      text="Keeps other apps responsive while LocalSR works. Jobs take longer, on a Mac about twice as long. It applies to the running job and the whole queue, and you can switch it during a job. Settings holds the default for the next start of LocalSR."
    />
  </div>
  {#if hardwareDiffers || priorityDiffers}
    <p class="model-description" id="advanced-session-choice">
      For this session only. Default in Settings: {[
        hardwareDiffers ? defaultHardware : '',
        priorityDiffers ? (defaults?.low_priority ? 'low priority' : 'normal priority') : '',
      ]
        .filter(Boolean)
        .join(', ')}.
      <button type="button" class="link-button" {disabled} on:click={onUseDefaults}
        >Use default</button
      >
    </p>
  {/if}
  {#if !usingTemporalVideo && settings.task !== 'edit'}
    <div class="field-grid">
      <div>
        <label for="tile-size">Tile</label><HelpTip
          label="Tile"
          text="How large a piece of the picture is processed at once, in pixels. Larger tiles are a little faster but need more memory; the result barely changes. Choose a smaller tile if you run out of memory."
        /><select
          id="tile-size"
          {disabled}
          value={settings.tile_size}
          on:change={(event) => updateSettings({ tile_size: Number(event.currentTarget.value) })}
          >{#each [64, 128, 192, 256, 384, 512] as size}<option value={size}>{size}</option
            >{/each}</select
        >
      </div>
      <div>
        <label for="halo-size">Halo</label><HelpTip
          label="Halo"
          text="How many pixels around each tile the model also sees, so the seams between tiles do not show. A larger halo is slower and rarely needed."
        /><select
          id="halo-size"
          {disabled}
          value={settings.halo}
          on:change={(event) => updateSettings({ halo: Number(event.currentTarget.value) })}
          >{#each [8, 16, 32, 64] as halo}<option value={halo}>{halo}</option>{/each}</select
        >
      </div>
      <div>
        <label for="precision">Precision</label><HelpTip
          label="Precision"
          text="Auto uses FP16 on Apple GPUs for verified models, and FP32 elsewhere. FP32 is the safe choice. FP16 is faster and needs less memory where supported. Keep FP32 on the CPU; DirectML and OpenVINO always use FP32, an Intel NPU always FP16."
        /><select
          id="precision"
          disabled={disabled ||
            onnxGpu ||
            (settings.task === 'video' && settings.video_hdr_mode === 'preserve')}
          value={npuRunning ? 'fp16' : onnxGpu ? 'fp32' : settings.precision}
          on:change={(event) => updateSettings({ precision: event.currentTarget.value })}
          ><option value="auto">Auto</option><option value="fp32">FP32</option><option value="fp16"
            >FP16</option
          ></select
        >
      </div>
    </div>
  {/if}
  {#if !usingTemporalVideo && settings.task !== 'edit'}<div class="check-line">
      <label class="check-row"
        ><input
          type="checkbox"
          {disabled}
          checked={settings.safe_memory}
          on:change={(event) => updateSettings({ safe_memory: event.currentTarget.checked })}
        /> Safe memory mode</label
      ><HelpTip
        label="Safe memory mode"
        text="Leaves more memory to the rest of the computer: LocalSR takes a smaller share of the GPU's memory and clears its cache after every tile. A little slower. Turn it on if the computer gets sluggish or a job runs out of memory."
      />
    </div>{/if}
  <div class="hardware-card">
    <span class="label-with-tip"
      >{capabilities.system_memory_pressure_level === 'unknown'
        ? 'Hardware ready'
        : `${capabilities.system_memory_pressure_level} memory pressure`}<HelpTip
        label="Memory pressure"
        text="How much of the computer's memory is in use: low below 75%, moderate from 75%, high from 90%. The bar shows that share. Refresh reads the hardware and the memory again."
      /></span
    ><!-- Not a <button>: this stays usable while the settings above are locked. The wider
         word is laid out hidden, so the line does not move when the label changes. -->
    <span
      role="button"
      tabindex="0"
      class="memory-refresh"
      aria-disabled={refreshing}
      on:click={refresh}
      on:keydown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          void refresh();
        }
      }}
      ><span class="refresh-ghost" aria-hidden="true">Refreshed</span><span class="refresh-now"
        >{refreshed ? 'Refreshed' : 'Refresh'}</span
      ></span
    ><span class="visually-hidden" role="status" aria-live="polite"
      >{refreshed ? 'Memory status refreshed' : ''}</span
    >
    <!-- Without a reading there is no share to show, and a minimum-width stub would read as one. -->
    {#if capabilities.system_memory_pressure_level !== 'unknown'}
      <div class="meter-clip">
        <i style={`width:${Math.max(3, capabilities.system_memory_pressure_percent)}%`}></i>
      </div>
    {/if}
  </div>
</div>
