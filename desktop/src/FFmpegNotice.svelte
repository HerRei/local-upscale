<script lang="ts">
  import { chooseExternalFFmpeg, detectExternalFFmpeg, openFfmpegDownloadPage } from './lib/api';
  import { INSTALL_COMMANDS, detectPlatform, type FfmpegPlatform } from './lib/ffmpeg';

  /** Why the notice opened: a video LocalSR cannot decode, or an H.264/HEVC export. */
  export let context: 'open' | 'export' = 'open';
  export let mediaNames: string[] = [];
  export let platform: FfmpegPlatform = detectPlatform();
  /** The user picked or LocalSR found an installed FFmpeg. */
  export let onSelected: (path: string) => Promise<void> | void;
  export let onClose: () => void;

  let note = '';
  let busy = false;
  let copied = false;

  $: command = INSTALL_COMMANDS[platform];
  $: title =
    context === 'export'
      ? 'H.264 and HEVC export needs FFmpeg'
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
    <p>
      Phone and camera videos need FFmpeg. LocalSR only includes royalty-free video formats (AV1,
      VP9, FFV1 and more). To open H.264 or HEVC videos, or to export them, install FFmpeg yourself
      (for example <code>{command}</code>) and select it in the app under Advanced settings → Video.
      Photos work without it.
    </p>
    <div class="ffmpeg-command">
      <code>{command}</code>
      <button class="button compact" on:click={copyCommand}
        >{copied ? 'Copied' : 'Copy command'}</button
      >
    </div>
    <p class="ffmpeg-fineprint">
      “Install FFmpeg…” opens ffmpeg.org in your browser. LocalSR never downloads or bundles FFmpeg;
      it runs the program you select as a separate process.
    </p>
    {#if note}<p class="ffmpeg-note" role="status">{note}</p>{/if}
    <div class="modal-actions">
      <button class="button" on:click={onClose}>Not now</button>
      <button class="button" on:click={choose}>Choose FFmpeg…</button>
      <button class="button" disabled={busy} on:click={find}>I installed it, find FFmpeg</button>
      <button class="button primary" on:click={() => void openFfmpegDownloadPage()}
        >Install FFmpeg…</button
      >
    </div>
  </div>
</div>
