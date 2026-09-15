<script lang="ts">
  import { chooseExternalFFmpeg, detectExternalFFmpeg } from './lib/api';
  import { detectedFfmpeg, systemCodecs } from './lib/ffmpeg';
  import type { CapabilityInfo, CatalogModel, UiSettings, VideoCodec } from './lib/types';

  export let settings: UiSettings;
  export let selectedModel: CatalogModel | undefined;
  export let capabilities: CapabilityInfo;
  export let usingTemporalVideo: boolean;
  export let updateSettings: (patch: Partial<UiSettings>) => void;
  export let chooseOutput: () => Promise<void>;
  export let refreshCapabilities: () => Promise<void>;
  let advanced = false;
  let ffmpegNote = '';

  $: codec = settings.video_codec ?? 'av1';
  $: externalCodec = codec === 'h264' || codec === 'hevc';
  $: system = systemCodecs(capabilities);
  $: foundFfmpeg = detectedFfmpeg(capabilities);
  $: codecSuffix = system
    ? `via ${system.label}`
    : foundFfmpeg.length
      ? 'via FFmpeg'
      : 'needs your FFmpeg';

  function chooseCodec(value: VideoCodec): void {
    // FFV1 is lossless and only fits MKV; the others keep the chosen container.
    updateSettings(
      value === 'ffv1' ? { video_codec: value, video_container: 'mkv' } : { video_codec: value },
    );
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
    ><span>Advanced</span><b>{advanced ? '⌃' : '⌄'}</b><small>Model, output, hardware</small
    ></button
  >
  {#if advanced}
    <div class="advanced-controls">
      {#if settings.task !== 'denoise' && !(usingTemporalVideo && settings.video_target_resolution)}
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
      {#if settings.task !== 'video'}
        <div class="field-row">
          <label for="format">Format</label><select
            id="format"
            value={settings.output_format}
            on:change={(event) =>
              updateSettings({
                output_format: event.currentTarget.value as UiSettings['output_format'],
              })}
            ><option value="png">PNG</option><option value="jpg">JPEG</option><option value="tif"
              >TIFF</option
            ><option value="webp">WebP</option></select
          >
        </div>
        {#if settings.output_format === 'jpg' || settings.output_format === 'webp'}<div
            class="range-row"
          >
            <label for="quality"
              >{settings.output_format === 'webp' ? 'WebP' : 'JPEG'} quality
              <b>{settings.jpeg_quality}</b></label
            ><input
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
      {:else}
        <div class="field-row">
          <label for="video-codec">Video format</label><select
            id="video-codec"
            value={codec}
            on:change={(event) => chooseCodec(event.currentTarget.value as VideoCodec)}
            ><option value="av1">AV1 · recommended</option><option value="vp9">VP9</option><option
              value="ffv1">FFV1 · lossless, MKV</option
            ><option value="h264">H.264 · {codecSuffix}</option><option value="hevc"
              >HEVC · {codecSuffix}</option
            ></select
          >
        </div>
        <div class="field-row">
          <label for="container">Container</label><select
            id="container"
            value={settings.video_container}
            on:change={(event) =>
              updateSettings({
                video_container: event.currentTarget.value as UiSettings['video_container'],
              })}
            ><option value="mp4" disabled={codec === 'ffv1'}>MP4</option><option value="mkv"
              >MKV</option
            ></select
          >
        </div>
        {#if externalCodec && system}
          <p class="model-description">
            LocalSR does not include patent-licensed H.264/HEVC encoders. This export is written by
            {system.label}'s own encoder into MP4, from a temporary lossless copy that needs extra
            disk space. MKV output needs an FFmpeg installed on your computer.
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
        {:else}
          <p class="model-description">
            SeedVR2 · Labs. Uses its own temporal processing and memory settings; longer clips and
            hardware limits are still being validated.
          </p>
        {/if}
        <p class="model-description">
          Original timing and orientation preserved. HLG/PQ output follows the HDR setting above;
          HDR uses FP32 and disables de-flicker.
        </p>
      {/if}
      <button class="directory-field" on:click={chooseOutput}
        ><span><small>Save to</small>{settings.output_directory || 'Choose an output folder'}</span
        ><b>Choose…</b></button
      >
      <div class="field-row">
        <label for="device">Hardware</label><select
          id="device"
          value={settings.device_id}
          on:change={(event) => updateSettings({ device_id: event.currentTarget.value })}
          >{#each capabilities.devices as device}<option value={device.id}>{device.name}</option
            >{/each}</select
        >
      </div>
      {#if !usingTemporalVideo && settings.device_id.startsWith('directml:')}
        <p class="model-description">
          DirectML uses the selected Windows GPU, including compatible Intel integrated graphics.
          Start with smaller tiles if shared memory is tight.
        </p>
      {/if}
      <div class="field-row">
        <label for="interface-scale">Interface text</label><select
          id="interface-scale"
          value={settings.interface_scale}
          on:change={(event) =>
            updateSettings({
              interface_scale: Number(event.currentTarget.value) as UiSettings['interface_scale'],
            })}
          ><option value="100">100%</option><option value="110">110%</option><option value="125"
            >125%</option
          ></select
        >
      </div>
      {#if !usingTemporalVideo}
        <div class="field-grid">
          <label
            >Tile<select
              value={settings.tile_size}
              on:change={(event) =>
                updateSettings({ tile_size: Number(event.currentTarget.value) })}
              >{#each [64, 128, 192, 256, 384, 512] as size}<option value={size}>{size}</option
                >{/each}</select
            ></label
          ><label
            >Halo<select
              value={settings.halo}
              on:change={(event) => updateSettings({ halo: Number(event.currentTarget.value) })}
              >{#each [8, 16, 32, 64] as halo}<option value={halo}>{halo}</option>{/each}</select
            ></label
          ><label
            >Precision<select
              disabled={settings.task === 'video' && settings.video_hdr_mode === 'preserve'}
              value={settings.precision}
              on:change={(event) => updateSettings({ precision: event.currentTarget.value })}
              ><option value="fp32">FP32</option><option value="fp16">FP16</option></select
            ></label
          >
        </div>
      {/if}
      {#if !usingTemporalVideo}<label class="check-row"
          ><input
            type="checkbox"
            checked={settings.safe_memory}
            on:change={(event) => updateSettings({ safe_memory: event.currentTarget.checked })}
          /> Safe memory mode</label
        >{/if}
      <label class="check-row"
        ><input
          type="checkbox"
          checked={settings.enable_live_preview}
          on:change={(event) =>
            updateSettings({ enable_live_preview: event.currentTarget.checked })}
        /> Show sampled enhanced previews (max 2 fps)</label
      >
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
