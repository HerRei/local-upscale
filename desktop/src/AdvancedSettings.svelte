<script lang="ts">
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
  import RunSettings from './RunSettings.svelte';
  import type {
    CapabilityInfo,
    CatalogModel,
    CatalogVideoModel,
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
  /** The selected video model of a temporal job; its engine decides the Labs note. */
  export let videoModel: Pick<CatalogVideoModel, 'engine_kind'> | undefined = undefined;
  export let preservingHdr = false;
  export let interpolationSupported = false;
  export let updateSettings: (patch: Partial<UiSettings>) => void;
  export let chooseOutput: () => Promise<void>;
  /** The saved defaults from Settings; hardware and low priority here apply to this session. */
  export let defaults: UiSettings | undefined = undefined;
  export let onUseDefaults: () => void = () => undefined;
  export let onOpenFfmpegSettings: () => void = () => undefined;
  /** Resolves true when the host took the request, false when it refused (and said so). */
  export let refreshCapabilities: () => Promise<boolean>;

  let advanced = false;

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
  $: ffmpegDisplay =
    settings.external_ffmpeg_path || (foundFfmpeg.length ? `Found (${foundFfmpeg[0]})` : 'None');
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
    ><small>{settings.task === 'edit' ? 'Size, steps, seed' : 'Format, tiles'}</small></button
  >
  {#if advanced}
    <div class="advanced-controls">
      {#if settings.task === 'edit'}
        <div class="field-row">
          <span class="label-with-tip"
            ><label for="edit-size">Edit size</label><HelpTip
              label="Edit size"
              text="The photo is scaled down to this longest edge before editing, and the result has that size. Larger sizes keep more detail but need more memory; sizes this computer cannot hold are disabled."
            /></span
          ><select
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
          <div>
            <label for="edit-steps">Steps</label><HelpTip
              label="Steps"
              text="How many refinement passes the model makes, from 1 to 60. More steps take longer. Choosing another model resets this to that model's own number."
            /><input
              id="edit-steps"
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
            />
          </div>
          <div>
            <label for="edit-seed">Seed</label><HelpTip
              label="Seed"
              text="The number the model's random choices start from. Try another one to get a different version of the same edit."
            /><input
              id="edit-seed"
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
            />
          </div>
        </div>
        <p class="model-description">
          {editModel
            ? `${editModel.name} is tuned for ${editModel.default_steps} steps.`
            : 'More steps take longer.'} The same seed with the same prompt and image repeats a result.
        </p>
      {/if}
      {#if settings.task !== 'video' && settings.task !== 'edit'}
        <div class="field-row">
          <span class="label-with-tip"
            ><label for="format">Format</label><HelpTip
              label="Format"
              text="PNG and TIFF are lossless. JPEG, WebP and HEIC make smaller files at some loss of detail, which the quality slider sets. HEIC is offered on Mac only."
            /></span
          ><select
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
            <div class="range-head">
              <span class="label-with-tip"
                ><label for="quality">{qualityName} quality</label><HelpTip
                  label={`${qualityName} quality`}
                  text="Higher values keep more detail and make larger files; lower values make smaller files with more visible compression. The default is 98."
                /></span
              ><b>{settings.jpeg_quality}</b>
            </div>
            <input
              id="quality"
              type="range"
              min="70"
              max="100"
              value={settings.jpeg_quality}
              on:change={(event) =>
                updateSettings({ jpeg_quality: Number(event.currentTarget.value) })}
            />
          </div>{/if}
        <div class="check-line">
          <label class="check-row"
            ><input
              type="checkbox"
              checked={settings.preserve_metadata}
              on:change={(event) =>
                updateSettings({ preserve_metadata: event.currentTarget.checked })}
            /> Preserve safe metadata and color profile</label
          ><HelpTip
            label="Metadata and color profile"
            text="Keeps the date, artist and copyright tags and the color profile, converted to sRGB. GPS position, camera serial numbers and similar tags are never copied. With this off, the file carries none of it."
          />
        </div>
      {:else if settings.task === 'video'}
        <div class="field-row">
          <span class="label-with-tip"
            ><label for="container">Container</label><HelpTip
              label="Container"
              text="The file type of the result. MP4 is the default; MKV keeps any audio LocalSR can decode as it is; MOV, on a Mac only, holds HEVC or ProRes. The video format list follows this choice."
            /></span
          ><select
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
          <span class="label-with-tip"
            ><label for="video-codec">Video format</label><HelpTip
              label="Video format"
              text="How the video is compressed. AV1, VP9 and lossless FFV1 are built into LocalSR; H.264 and HEVC are written by macOS or by an FFmpeg you installed. Automatic picks what this computer offers."
            /></span
          ><select
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
          <span class="label-with-tip"
            ><span class="field-label">External FFmpeg</span><HelpTip
              label="External FFmpeg"
              text="FFmpeg is a free video program that you install yourself. Leave this empty unless a video will not open or an export format asks for it; LocalSR uses the one it finds on this computer."
            /></span
          >
          <div class="engine-value">
            <span class="engine-active-label" title={ffmpegDisplay}>{ffmpegDisplay}</span>
            <button type="button" class="link-button" on:click={onOpenFfmpegSettings}
              >Change…</button
            >
          </div>
        </div>
        <div class="range-row">
          <div class="range-head">
            <span class="label-with-tip"
              ><label for="crf">Video quality · CRF</label><HelpTip
                label="Video quality"
                text="Lower numbers keep more detail and make larger files; higher numbers make smaller files. It has no effect on lossless FFV1 or on ProRes. The default is 18."
              /></span
            ><b>{settings.video_crf}</b>
          </div>
          <input
            id="crf"
            type="range"
            min="12"
            max="30"
            value={settings.video_crf}
            on:change={(event) => updateSettings({ video_crf: Number(event.currentTarget.value) })}
          />
        </div>
        {#if !usingTemporalVideo}
          <div class="check-line">
            <label class="check-row"
              ><input
                type="checkbox"
                disabled={settings.video_hdr_mode === 'preserve'}
                checked={settings.deflicker}
                on:change={(event) => updateSettings({ deflicker: event.currentTarget.checked })}
              /> De-flicker · Labs</label
            ><HelpTip
              label="De-flicker"
              text="Evens out brightness flicker in the original video and the shimmer that frame-by-frame upscaling adds in still areas. It decides from the original, so motion, cuts and real light changes pass through. Adds about 40 ms per frame. Off by default."
            />
          </div>
          <p class="model-description">
            Steadies flicker and still-area shimmer. Motion and scene cuts pass through.
          </p>
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
        {:else}
          <p class="model-description">
            {#if videoModel?.engine_kind === 'flashvsr'}
              FlashVSR · Labs. One-step diffusion upscaling that works best up to 4× the source.
              Memory is managed automatically; jobs on large frames can take hours.
            {:else}
              SeedVR2 · Labs. Uses its own temporal processing and memory settings; longer clips and
              hardware limits are still being validated.
            {/if}
          </p>
        {/if}
        <p class="model-description">
          Original timing and orientation preserved. HLG/PQ output follows the HDR setting above;
          HDR uses FP32 and disables de-flicker and frame interpolation.
        </p>
      {/if}
      <div class="directory-field">
        <button
          type="button"
          class="directory-hit"
          aria-label={`Save to ${settings.output_directory || 'Choose an output folder'}`}
          on:click={chooseOutput}
        ></button>
        <div class="directory-text">
          <span class="label-with-tip"
            ><small>Save to</small><HelpTip
              label="Save to"
              text="The folder where results are saved; it starts as your Desktop. Results get new names and never replace an existing file, and the original photo is not changed."
            /></span
          ><span class="directory-path"
            >{settings.output_directory || 'Choose an output folder'}</span
          >
        </div>
        <b>Choose…</b>
      </div>
      {#if settings.task === 'edit'}
        <RunSettings
          {settings}
          {selectedModel}
          {capabilities}
          {usingTemporalVideo}
          {updateSettings}
          {defaults}
          {onUseDefaults}
          {refreshCapabilities}
        />
      {/if}
    </div>
  {/if}
</section>
