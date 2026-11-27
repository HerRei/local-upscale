<script lang="ts">
  import HelpTip from './HelpTip.svelte';
  import SaveToField from './SaveToField.svelte';
  import { detectedFfmpeg, systemCodecs } from './lib/ffmpeg';
  import {
    codecPatch,
    codecsForContainer,
    containerChoices,
    containerPatch,
    imageFormatChoices,
    imageQualityName,
  } from './lib/outputFormats';
  import type {
    CapabilityInfo,
    CatalogVideoModel,
    MediaItem,
    UiSettings,
    VideoCodec,
  } from './lib/types';

  export let settings: UiSettings;
  export let selectedMedia: MediaItem | undefined = undefined;
  export let capabilities: CapabilityInfo;
  export let usingTemporalVideo = false;
  export let preservingHdr = false;
  export let hdrPreservationAvailable = false;
  export let audioDisabled = false;
  export let audioModels: CatalogVideoModel[] = [];
  export let selectedAudioModel: CatalogVideoModel | undefined = undefined;
  export let disabled = false;
  export let updateSettings: (patch: Partial<UiSettings>) => void;
  export let chooseOutput: () => Promise<void>;
  export let onOpenFfmpegSettings: () => void = () => undefined;

  $: codec = settings.video_codec ?? 'auto';
  // Mirrors automatic_video_codec in the worker: the Mac's hardware HEVC
  // encoder for SDR MP4, LocalSR's AV1 otherwise.
  $: automaticHardware =
    Boolean(system) &&
    (settings.video_container ?? 'mp4') === 'mp4' &&
    settings.video_hdr_mode !== 'preserve';
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

  $: colourOutputTip = selectedMedia?.hdr_format
    ? [
        preservingHdr
          ? `Keeps ${selectedMedia.hdr_format} in float precision through HAT and exports 10-bit BT.2020 HEVC. Source light and colour constrain the model’s added detail. HAT models were trained on SDR; HDR detail quality is experimental. Thumbnails and live tiles use SDR tone mapping. Final playback depends on system HDR support. Dolby Vision dynamic metadata is not retained; compatible HLG/PQ base video is preserved.`
          : `This ${selectedMedia.hdr_format} video is tone-mapped to SDR before enhancement and exported as 8-bit SDR. The preview uses the same conversion.`,
        settings.selected_model_id === '__custom__' && !usingTemporalVideo
          ? 'HDR preservation remains available for custom checkpoints. The current adapter requires HAT; compatibility is checked when the model loads.'
          : '',
      ]
        .filter(Boolean)
        .join(' ')
    : '';

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

<span class="eyebrow">Output</span>
<div class="output-settings" class:locked={disabled}>
  {#if settings.task !== 'video'}
    <div class="field-row">
      <span class="label-with-tip"
        ><label for="format">Format</label><HelpTip
          label="Format"
          text="PNG and TIFF are lossless. JPEG, WebP and HEIC make smaller files at some loss of detail, which the quality slider sets. HEIC is offered on Mac only."
        /></span
      ><select
        id="format"
        {disabled}
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
          {disabled}
          value={settings.jpeg_quality}
          on:change={(event) => updateSettings({ jpeg_quality: Number(event.currentTarget.value) })}
        />
      </div>{/if}
    <div class="check-line">
      <label class="check-row"
        ><input
          type="checkbox"
          {disabled}
          checked={settings.preserve_metadata}
          on:change={(event) => updateSettings({ preserve_metadata: event.currentTarget.checked })}
        /> Preserve safe metadata and color profile</label
      ><HelpTip
        label="Metadata and color profile"
        text="Keeps the date, artist and copyright tags and the color profile, converted to sRGB. GPS position, camera serial numbers and similar tags are never copied. With this off, the file carries none of it."
      />
    </div>
  {:else}
    <div class="field-row">
      <span class="label-with-tip"
        ><label for="container">Container</label><HelpTip
          label="Container"
          text="The file type of the result. MP4 is the default; MKV keeps any audio LocalSR can decode as it is; MOV, on a Mac only, holds HEVC or ProRes. The video format list follows this choice."
        /></span
      ><select
        id="container"
        {disabled}
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
        {disabled}
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
        {system.label}'s own encoder into {settings.video_container === 'mov' ? 'MOV' : 'MP4'}, from
        a temporary lossless copy that needs extra disk space. MKV output needs an FFmpeg installed
        on your computer.
      </p>
    {:else if externalCodec}
      <p class="model-description">
        LocalSR does not include patent-licensed H.264/HEVC encoders. This export is written by the
        FFmpeg installed on your computer, from a temporary lossless copy that needs extra disk
        space.
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
        <button type="button" class="link-button" {disabled} on:click={onOpenFfmpegSettings}
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
        {disabled}
        value={settings.video_crf}
        on:change={(event) => updateSettings({ video_crf: Number(event.currentTarget.value) })}
      />
    </div>
    {#if selectedMedia?.hdr_format}
      <div class="field-row">
        <span class="label-with-tip"
          ><label for="hdr-output">Colour output</label><HelpTip
            label="Colour output"
            text={colourOutputTip}
          /></span
        ><select
          id="hdr-output"
          {disabled}
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
      </div>
    {/if}
    <div class="check-line">
      <label class="check-row"
        ><input
          type="checkbox"
          disabled={disabled || audioDisabled}
          checked={!audioDisabled && Boolean(settings.video_clean_audio)}
          on:change={(event) => updateSettings({ video_clean_audio: event.currentTarget.checked })}
        /> Clean up audio</label
      ><HelpTip
        label="Clean up audio"
        text="Removes background noise such as hum, wind and hiss from speech. Music may sound worse."
      />{#if selectedMedia?.audio_warning}<HelpTip
          label="this video's audio"
          text={selectedMedia.audio_warning}
        />{/if}
    </div>
    {#if audioDisabled}
      <p class="model-description">This video has no audio track.</p>
    {:else if settings.video_clean_audio && audioModels.length > 0}
      <div class="field-row">
        <label for="audio-model">Audio model</label><select
          id="audio-model"
          {disabled}
          value={selectedAudioModel?.model_id ?? ''}
          on:change={(event) => updateSettings({ video_audio_model: event.currentTarget.value })}
        >
          {#each audioModels as model (model.model_id)}
            <option value={model.model_id}>{model.name}</option>
          {/each}
        </select>
      </div>
      {#if selectedAudioModel?.summary}
        <p class="model-description">{selectedAudioModel.summary}</p>
      {/if}
    {/if}
  {/if}
  <SaveToField outputDirectory={settings.output_directory} {chooseOutput} {disabled} />
</div>
