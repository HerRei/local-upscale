<script lang="ts">
  import { chooseExternalFFmpeg, detectExternalFFmpeg } from './lib/api';
  import { editDevice, editSizeLimit, editingDevices } from './lib/editing';
  import { detectedFfmpeg, systemCodecs } from './lib/ffmpeg';
  import {
    codecPatch,
    codecsForContainer,
    containerChoices,
    containerPatch,
    imageFormatChoices,
    imageQualityName,
  } from './lib/outputFormats';
  import HelpTip from './HelpTip.svelte';
  import { hardwareChoices, hardwarePatch, selectedHardware } from './lib/neuralEngine';
  import { groupedHardware, optionLabel } from './lib/hardwareRoutes';
  import type {
    CapabilityInfo,
    CatalogModel,
    EditModel,
    InterpolationOrder,
    InterpolationTarget,
    UiSettings,
    VideoCodec,
  } from './lib/types';

  export let settings: UiSettings;
  export let selectedModel: CatalogModel | undefined;
  export let editModel: EditModel | undefined = undefined;
  export let capabilities: CapabilityInfo;
  export let usingTemporalVideo: boolean;
  export let preservingHdr = false;
  export let interpolationSupported = false;
  export let updateSettings: (patch: Partial<UiSettings>) => void;
  export let chooseOutput: () => Promise<void>;
  export let refreshCapabilities: () => Promise<void>;

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
  $: hardwareGroups = groupedHardware(
    settings.task === 'edit'
      ? editingDevices(capabilities)
      : usingTemporalVideo
        ? capabilities.devices
        : hardwareChoices(capabilities, selectedModel?.architecture),
  );
  $: hardwareValue =
    settings.task === 'edit'
      ? (editDevice(capabilities, settings)?.id ?? '')
      : usingTemporalVideo
        ? settings.device_id
        : selectedHardware(settings, capabilities, selectedModel?.architecture);
  // A long entry (a GPU with its route) takes the row's full width, so the closed menu shows
  // the whole name and route instead of cutting them.
  $: hardwareStacked = hardwareGroups.some((group) =>
    group.options.some((option) => option.label.length > 22),
  );
  let advanced = false;
  let ffmpegNote = '';

  $: codec = settings.video_codec ?? 'auto';
  // Mirrors automatic_video_codec in the worker: the Mac's hardware HEVC
  // encoder for SDR MP4, LocalSR's AV1 otherwise.
  $: automaticHardware =
    Boolean(system) &&
    (settings.video_container ?? 'mp4') === 'mp4' &&
    settings.video_hdr_mode !== 'preserve';
  $: editSizeAllowed = editSizeLimit(capabilities, editDevice(capabilities, settings));
  $: externalCodec = codec === 'h264' || codec === 'hevc';
  $: system = systemCodecs(capabilities);
  $: foundFfmpeg = detectedFfmpeg(capabilities);
  $: codecSuffix = system
    ? `via ${system.label}`
    : foundFfmpeg.length
      ? 'via FFmpeg'
      : 'needs your FFmpeg';

  $: imageFormats = imageFormatChoices(capabilities, settings.output_format);
  $: qualityName = imageQualityName(settings.output_format);
  $: containers = containerChoices(capabilities, settings.video_container);
  $: codecs = codecsForContainer(settings.video_container ?? 'mp4');

  function codecLabel(value: VideoCodec): string {
    switch (value) {
      case 'auto':
        return `Automatic · ${automaticHardware ? 'HEVC, hardware encoder' : 'AV1'}`;
      case 'ffv1':
        return 'FFV1 · lossless, MKV';
      case 'h264':
        return `H.264 · ${codecSuffix}`;
      case 'hevc':
        return `HEVC · ${codecSuffix}`;
      case 'prores':
        return `Apple ProRes 422 · ${codecSuffix}`;
      default:
        return value.toUpperCase();
    }
  }

  function chooseCodec(value: VideoCodec): void {
    updateSettings(codecPatch(value));
  }

  function chooseContainer(value: UiSettings['video_container']): void {
    updateSettings(containerPatch(value, codec));
  }

  async function findFFmpeg(): Promise<void> {
    const found = await detectExternalFFmpeg();
    if (found.length) {
      updateSettings({ external_ffmpeg_path: found[0] });
      ffmpegNote = found.length > 1 ? `Also found: ${found.slice(1).join(', ')}` : '';
    } else {
      ffmpegNote =
        'No FFmpeg was found. Install it (for example with Homebrew, winget or your package manager), then choose it here.';
    }
  }

  async function pickFFmpeg(): Promise<void> {
    const path = await chooseExternalFFmpeg(settings.external_ffmpeg_path ?? '');
    if (path) {
      updateSettings({ external_ffmpeg_path: path });
      ffmpegNote = '';
    }
  }
</script>

<section class="control-section">
  <button class="disclosure" on:click={() => (advanced = !advanced)}
    ><span>Advanced</span><b aria-hidden="true"
      ><svg
        viewBox="0 0 12 12"
        width="10"
        height="10"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        ><path d={advanced ? 'M3 7.5 6 4.5 9 7.5' : 'M3 4.5 6 7.5 9 4.5'} /></svg
      ></b
    ><small
      >{settings.task === 'edit' ? 'Size, steps, seed, hardware' : 'Scale, format, hardware'}</small
    ></button
  >
  {#if advanced}
    <div class="advanced-controls">
      {#if settings.task === 'edit'}
        <div class="field-row">
          <label for="edit-size">Edit size</label><select
            id="edit-size"
            value={Math.min(settings.edit_max_dimension, editSizeAllowed)}
            on:change={(event) =>
              updateSettings({ edit_max_dimension: Number(event.currentTarget.value) })}
            >{#each [512, 768, 1024] as size}<option value={size} disabled={size > editSizeAllowed}
                >{size} px · longest edge{size > editSizeAllowed
                  ? ' · needs more memory'
                  : ''}</option
              >{/each}</select
          >
        </div>
        <div class="field-grid">
          <label
            >Steps<input
              type="number"
              min="1"
              max="60"
              value={settings.edit_steps || editModel?.default_steps || 40}
              on:change={(event) =>
                updateSettings({
                  edit_steps: Math.min(
                    60,
                    Math.max(1, Math.round(Number(event.currentTarget.value)) || 40),
                  ),
                })}
            /></label
          ><label
            >Seed<input
              type="number"
              min="0"
              max="2147483647"
              value={settings.edit_seed}
              on:change={(event) =>
                updateSettings({
                  edit_seed: Math.min(
                    2147483647,
                    Math.max(0, Math.round(Number(event.currentTarget.value)) || 0),
                  ),
                })}
            /></label
          >
        </div>
        <p class="model-description">
          {editModel
            ? `${editModel.name} is tuned for ${editModel.default_steps} steps.`
            : 'More steps take longer.'} The same seed with the same prompt and image repeats a result.
        </p>
      {:else if settings.task !== 'denoise' && !(usingTemporalVideo && settings.video_target_resolution)}
        <div class="field-row">
          <label for="scale">Output scale</label><select
            id="scale"
            value={settings.output_scale}
            on:change={(event) =>
              updateSettings({ output_scale: Number(event.currentTarget.value) })}
            >{#each Array.from({ length: Math.max(1, (selectedModel?.native_scale ?? 4) - 1) }, (_, index) => index + 2) as scale}<option
                value={scale}>{scale}×</option
              >{/each}</select
          >
        </div>
      {/if}
      {#if settings.task !== 'video' && settings.task !== 'edit'}
        <div class="field-row">
          <label for="format">Format</label><select
            id="format"
            value={settings.output_format}
            on:change={(event) =>
              updateSettings({
                output_format: event.currentTarget.value as UiSettings['output_format'],
              })}
            >{#each imageFormats as choice}<option value={choice.value}>{choice.label}</option
              >{/each}</select
          >
        </div>
        {#if qualityName}<div class="range-row">
            <label for="quality">{qualityName} quality <b>{settings.jpeg_quality}</b></label><input
              id="quality"
              type="range"
              min="70"
              max="100"
              value={settings.jpeg_quality}
              on:change={(event) =>
                updateSettings({ jpeg_quality: Number(event.currentTarget.value) })}
            />
          </div>{/if}
        <label class="check-row"
          ><input
            type="checkbox"
            checked={settings.preserve_metadata}
            on:change={(event) =>
              updateSettings({ preserve_metadata: event.currentTarget.checked })}
          /> Preserve safe metadata and color profile</label
        >
      {:else if settings.task === 'video'}
        <div class="field-row">
          <label for="container">Container</label><select
            id="container"
            value={settings.video_container}
            on:change={(event) =>
              chooseContainer(event.currentTarget.value as UiSettings['video_container'])}
            >{#each containers as choice}<option
                value={choice.value}
                disabled={choice.value === 'mp4' && codec === 'ffv1'}>{choice.label}</option
              >{/each}</select
          >
        </div>
        <div class="field-row">
          <label for="video-codec">Video format</label><select
            id="video-codec"
            value={codec}
            on:change={(event) => chooseCodec(event.currentTarget.value as VideoCodec)}
            >{#each codecs as value}<option {value}>{codecLabel(value)}</option>{/each}</select
          >
        </div>
        {#if codec === 'prores'}
          <p class="model-description">
            Apple ProRes 422 is written by {system?.label}'s own encoder into MOV. It is made for
            editing: files are many times larger than HEVC, and the quality setting does not apply.
          </p>
        {:else if externalCodec && system}
          <p class="model-description">
            LocalSR does not include patent-licensed H.264/HEVC encoders. This export is written by
            {system.label}'s own encoder into {settings.video_container === 'mov' ? 'MOV' : 'MP4'},
            from a temporary lossless copy that needs extra disk space. MKV output needs an FFmpeg
            installed on your computer.
          </p>
        {:else if externalCodec}
          <p class="model-description">
            LocalSR does not include patent-licensed H.264/HEVC encoders. This export is written by
            the FFmpeg installed on your computer, from a temporary lossless copy that needs extra
            disk space.
          </p>
        {/if}
        <div class="field-row">
          <label for="external-ffmpeg">External FFmpeg</label><input
            id="external-ffmpeg"
            type="text"
            placeholder={foundFfmpeg.length ? `Found automatically: ${foundFfmpeg[0]}` : 'Not used'}
            value={settings.external_ffmpeg_path ?? ''}
            on:change={(event) =>
              updateSettings({ external_ffmpeg_path: event.currentTarget.value.trim() })}
          />
        </div>
        <div class="field-row">
          <button on:click={findFFmpeg}>Find installed FFmpeg</button>
          <button on:click={pickFFmpeg}>Choose…</button>
          {#if settings.external_ffmpeg_path}<button
              on:click={() => updateSettings({ external_ffmpeg_path: '' })}>Don’t use</button
            >{/if}
        </div>
        <p class="model-description">
          {#if system}
            Optional. On this computer, H.264 and HEVC videos open and export through {system.label}'s
            own codecs. FFmpeg is only needed for other formats (WMV, DivX, FLV) and for H.264/HEVC
            in MKV; one found on this computer is used automatically.
          {:else}
            Optional. An FFmpeg found on this computer is used automatically; select one here to use
            a different build. It is needed for H.264/HEVC export and for opening most phone and
            camera videos.
          {/if}
          LocalSR never downloads or bundles FFmpeg.{#if ffmpegNote}<br />{ffmpegNote}{/if}
        </p>
        <div class="range-row">
          <label for="crf">Video quality · CRF <b>{settings.video_crf}</b></label><input
            id="crf"
            type="range"
            min="12"
            max="30"
            value={settings.video_crf}
            on:change={(event) => updateSettings({ video_crf: Number(event.currentTarget.value) })}
          />
        </div>
        {#if !usingTemporalVideo}
          <label class="check-row"
            ><input
              type="checkbox"
              disabled={settings.video_hdr_mode === 'preserve'}
              checked={settings.deflicker}
              on:change={(event) => updateSettings({ deflicker: event.currentTarget.checked })}
            /> De-flicker · Labs</label
          >
          <p class="model-description">
            Gentle smoothing in static regions. Moving details and scene cuts bypass the filter.
          </p>
          <div class="field-row">
            <label for="frame-rate">Frame rate</label><select
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
              <label for="interpolation-order">Interpolate</label><select
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
        {:else}
          <p class="model-description">
            SeedVR2 · Labs. Uses its own temporal processing and memory settings; longer clips and
            hardware limits are still being validated.
          </p>
        {/if}
        <p class="model-description">
          Original timing and orientation preserved. HLG/PQ output follows the HDR setting above;
          HDR uses FP32 and disables de-flicker and frame interpolation.
        </p>
      {/if}
      <button class="directory-field" on:click={chooseOutput}
        ><span><small>Save to</small>{settings.output_directory || 'Choose an output folder'}</span
        ><b>Choose…</b></button
      >
      <div class="field-row" class:stacked={hardwareStacked}>
        <span class="label-with-tip"
          ><label for="device">Hardware</label><HelpTip
            label="Hardware"
            text="Which processor runs the model. A GPU is much faster than the CPU. On a Mac, Neural Engine runs the models it supports, such as Quick, on Apple's AI chip; for other models only the Apple GPU and the CPU are listed, and your choice is kept for the next model that supports it."
          /></span
        ><select
          id="device"
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
      {#if !usingTemporalVideo && settings.task !== 'edit' && onnxGpu && !npuSelected}
        <p class="model-description">
          {#if settings.device_id.startsWith('directml:')}DirectML has not been tested on real
            hardware yet.{/if}
          The first time a model runs on this GPU, LocalSR checks once that it gives the same result as
          the CPU.
          {#if settings.device_id.startsWith('directml:')}Choose a smaller tile if shared memory is
            tight.{/if}
        </p>
      {:else if !usingTemporalVideo && npuSelected}
        <p class="model-description">
          The NPU runs SPAN, Real-ESRGAN, FBCNN and NAFNet models; others run on the CPU. LocalSR
          compiles a model once for the NPU, which can take a minute, and checks once that its
          result matches the CPU's.
        </p>
      {/if}
      <div class="field-row">
        <label for="interface-scale">Interface text</label><select
          id="interface-scale"
          value={String(settings.interface_scale)}
          on:change={(event) =>
            updateSettings({
              interface_scale: Number(event.currentTarget.value) as UiSettings['interface_scale'],
            })}
          ><option value="100">100%</option><option value="110">110%</option><option value="125"
            >125%</option
          ></select
        >
      </div>
      {#if !usingTemporalVideo && settings.task !== 'edit'}
        <div class="field-grid">
          <div>
            <label for="tile-size">Tile</label><HelpTip
              label="Tile"
              text="How large a piece of the image is processed at once, in pixels. Larger tiles are a little faster but need more memory; the result barely changes. Choose a smaller tile if you run out of memory."
            /><select
              id="tile-size"
              value={settings.tile_size}
              on:change={(event) =>
                updateSettings({ tile_size: Number(event.currentTarget.value) })}
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
              value={settings.halo}
              on:change={(event) => updateSettings({ halo: Number(event.currentTarget.value) })}
              >{#each [8, 16, 32, 64] as halo}<option value={halo}>{halo}</option>{/each}</select
            >
          </div>
          <div>
            <label for="precision">Precision</label><HelpTip
              label="Precision"
              text="FP32 is the safe choice. FP16 is faster and needs less memory on GPUs that support it, for models that allow it. Keep FP32 on the CPU; DirectML and OpenVINO always use FP32, an Intel NPU always FP16."
            /><select
              id="precision"
              disabled={onnxGpu ||
                (settings.task === 'video' && settings.video_hdr_mode === 'preserve')}
              value={npuRunning ? 'fp16' : onnxGpu ? 'fp32' : settings.precision}
              on:change={(event) => updateSettings({ precision: event.currentTarget.value })}
              ><option value="fp32">FP32</option><option value="fp16">FP16</option></select
            >
          </div>
        </div>
      {/if}
      {#if !usingTemporalVideo && settings.task !== 'edit'}<div class="check-line">
          <label class="check-row"
            ><input
              type="checkbox"
              checked={settings.safe_memory}
              on:change={(event) => updateSettings({ safe_memory: event.currentTarget.checked })}
            /> Safe memory mode</label
          ><HelpTip
            label="Safe memory mode"
            text="Leaves more memory to the rest of the computer: LocalSR takes a smaller share of the GPU's memory and clears its cache after every tile. A little slower. Turn it on if the computer gets sluggish or a job runs out of memory."
          />
        </div>{/if}
      {#if settings.task !== 'edit'}<label class="check-row"
          ><input
            type="checkbox"
            checked={settings.enable_live_preview}
            on:change={(event) =>
              updateSettings({ enable_live_preview: event.currentTarget.checked })}
          /> Show sampled enhanced previews (max 2 fps)</label
        >{/if}
      <div class="hardware-card">
        <span
          >{capabilities.system_memory_pressure_level === 'unknown'
            ? 'Hardware ready'
            : `${capabilities.system_memory_pressure_level} memory pressure`}</span
        ><button on:click={refreshCapabilities}>Refresh</button><i
          style={`width:${Math.max(3, capabilities.system_memory_pressure_percent)}%`}
        ></i>
      </div>
    </div>
  {/if}
</section>
