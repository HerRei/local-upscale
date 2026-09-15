<script lang="ts">
  import { onMount } from 'svelte';

  import {
    chooseExternalFFmpeg,
    detectExternalFFmpeg,
    ffmpegInstallHint,
    isTauri,
    openFfmpegDownloadPage,
    openTerminalWithInstallCommand,
  } from './lib/api';
  import { INSTALL_COMMANDS, detectPlatform, type FfmpegPlatform } from './lib/ffmpeg';

  /** Why the notice opened: a video LocalSR cannot decode, or an H.264/HEVC export. */
  export let context: 'open' | 'export' = 'open';
  export let mediaNames: string[] = [];
  export let platform: FfmpegPlatform = detectPlatform();
  /** The label of this platform's own codecs ("macOS"), or empty when it has none. */
  export let systemCodecs = '';
  /** The container of the export that needs FFmpeg. */
  export let container = 'mp4';
  /** The user picked or LocalSR found an installed FFmpeg. */
  export let onSelected: (path: string) => Promise<void> | void;
  export let onClose: () => void;

  let note = '';
  let busy = false;
  let copied = false;
  let command = INSTALL_COMMANDS[platform];
  let hintNote = '';

  onMount(async () => {
    const hint = await ffmpegInstallHint();
    if (hint) {
      command = hint.command;
      hintNote = hint.note;
    }
  });

  $: title =
    context === 'export'
      ? systemCodecs && container !== 'mp4'
        ? 'H.264 and HEVC in MKV need FFmpeg'
        : 'H.264 and HEVC export needs FFmpeg'
      : mediaNames.length > 1
        ? 'These videos need FFmpeg'
        : 'This video needs FFmpeg';

  async function find(): Promise<void> {
    busy = true;
    try {
      const found = await detectExternalFFmpeg();
      if (found.length) {
        note = '';
        await onSelected(found[0]);
      } else {
        note =
          'No FFmpeg was found yet. Install it, then try again, or choose the program yourself.';
      }
    } finally {
      busy = false;
    }
  }

  async function choose(): Promise<void> {
    const path = await chooseExternalFFmpeg('');
    if (path) {
      note = '';
      await onSelected(path);
    }
  }

  async function copyCommand(): Promise<void> {
    try {
      await navigator.clipboard.writeText(command);
      copied = true;
      setTimeout(() => (copied = false), 1600);
    } catch {
      note = `Copy is unavailable here. The command is: ${command}`;
    }
  }

  async function runInTerminal(): Promise<void> {
    try {
      await openTerminalWithInstallCommand(command);
      note =
        'A terminal window is running the install command. When it finishes, click “I installed it”.';
    } catch (error) {
      note = `Could not open a terminal: ${String(error)}. Run the command yourself.`;
    }
  }

  function closeFromBackdrop(event: MouseEvent): void {
    if (event.target === event.currentTarget) onClose();
  }

  function onKey(event: KeyboardEvent): void {
    if (event.key === 'Escape') onClose();
  }
</script>

<svelte:window on:keydown={onKey} />

<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
<div class="modal-backdrop" role="presentation" on:click={closeFromBackdrop}>
  <div
    class="modal ffmpeg-notice"
    role="dialog"
    aria-modal="true"
    aria-labelledby="ffmpeg-notice-title"
    tabindex="-1"
  >
    <h2 id="ffmpeg-notice-title">{title}</h2>
    {#if mediaNames.length}<p class="ffmpeg-files">{mediaNames.join(', ')}</p>{/if}
    {#if context === 'export' && systemCodecs}
      <p>
        {systemCodecs} writes H.264 and HEVC into MP4 without any extra software. Choose MP4 as the container,
        or install FFmpeg (for example <code>{command}</code>) for MKV and select it under Advanced
        settings → Video.
      </p>
    {:else if systemCodecs}
      <p>
        Neither LocalSR nor {systemCodecs} can decode this format (for example WMV, DivX or FLV). Photos,
        and H.264 or HEVC videos from phones and cameras, open without extra software. To open this file,
        install FFmpeg (for example <code>{command}</code>) and select it under Advanced settings →
        Video.
      </p>
    {:else}
      <p>
        Phone and camera videos need FFmpeg. LocalSR only includes royalty-free video formats (AV1,
        VP9, FFV1 and more). To open H.264 or HEVC videos, or to export them, install FFmpeg (for
        example <code>{command}</code>); LocalSR uses it as soon as it is installed. Photos work
        without it.
      </p>
    {/if}
    <div class="ffmpeg-command">
      <code>{command}</code>
      <button class="button compact" on:click={copyCommand}
        >{copied ? 'Copied' : 'Copy command'}</button
      >
    </div>
    {#if hintNote}<p class="ffmpeg-fineprint">{hintNote}</p>{/if}
    <p class="ffmpeg-fineprint">
      LocalSR never downloads or bundles FFmpeg; it runs the program installed on this computer as a
      separate process.
    </p>
    {#if note}<p class="ffmpeg-note" role="status">{note}</p>{/if}
    <div class="modal-actions">
      <button class="button" on:click={onClose}>Not now</button>
      <button class="button" on:click={choose}>Choose FFmpeg…</button>
      <button class="button" disabled={busy} on:click={find}>I installed it, find FFmpeg</button>
      {#if isTauri()}
        <button class="button primary" on:click={runInTerminal}>Install in Terminal…</button>
      {:else}
        <button class="button primary" on:click={() => void openFfmpegDownloadPage()}
          >Install FFmpeg…</button
        >
      {/if}
    </div>
  </div>
</div>
