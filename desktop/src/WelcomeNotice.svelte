<script lang="ts">
  import { openUserGuide } from './lib/api';
  import { updateState } from './lib/updates';

  /** Whether the anonymous update-check count is on, so the notice describes it truthfully. */
  export let anonymousUpdateCount = true;

  const SEEN_KEY = 'localsr.welcome.seen';

  function seen(): boolean {
    try {
      return window.localStorage.getItem(SEEN_KEY) === '1';
    } catch {
      return false;
    }
  }

  let visible = !seen();

  // Store editions update through the Store and never send the count.
  $: counts = $updateState.configured && !$updateState.managed_by_store;

  function close(): void {
    visible = false;
    try {
      window.localStorage.setItem(SEEN_KEY, '1');
    } catch {
      // Restricted storage: the notice simply returns next launch.
    }
  }
</script>

{#if visible}
  <section class="welcome-notice" aria-labelledby="welcome-title">
    <div class="text">
      <b id="welcome-title">Welcome to LocalSR</b>
      <span
        >Drop photos or videos in the middle, choose Upscale, Restore or Upscale Video under
        Enhance, then start with the blue button at the bottom right. The user guide explains
        models, batches, video and where results are saved.</span
      >
      {#if counts}
        <span class="count"
          >{anonymousUpdateCount
            ? 'At launch LocalSR checks for updates and sends an anonymous count: app version, platform and channel, with no files, names or identifiers. You can turn it off under Check for Updates in the … menu.'
            : 'The anonymous update-check count is off.'}</span
        >
      {/if}
    </div>
    <div class="buttons">
      <button class="notice-button" type="button" on:click={() => void openUserGuide()}
        >User Guide</button
      >
      <button class="notice-button primary" type="button" on:click={close}>Got It</button>
    </div>
  </section>
{/if}

<style>
  .welcome-notice {
    position: fixed;
    z-index: 79;
    bottom: 78px;
    left: 50%;
    display: grid;
    gap: 10px;
    width: min(520px, calc(100vw - 28px));
    padding: 14px;
    transform: translateX(-50%);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 14px;
    background: rgba(32, 35, 41, 0.9);
    backdrop-filter: blur(22px) saturate(150%);
    box-shadow:
      inset 0 1px rgba(255, 255, 255, 0.06),
      0 18px 46px rgba(0, 0, 0, 0.5);
  }
  .text {
    display: grid;
    gap: 5px;
  }
  .text b {
    font-size: calc(13px * var(--ui-scale));
    font-weight: 600;
  }
  .text span {
    color: var(--secondary);
    font-size: calc(12px * var(--ui-scale));
    line-height: 1.45;
  }
  .text .count {
    font-size: calc(11.5px * var(--ui-scale));
  }
  .buttons {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px;
  }
  .notice-button {
    padding: 6px 11px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 7px;
    background: rgba(255, 255, 255, 0.08);
    color: var(--text);
    font-size: calc(12px * var(--ui-scale));
    font-weight: 500;
    white-space: nowrap;
  }
  .notice-button:hover {
    background: rgba(255, 255, 255, 0.13);
  }
  .notice-button.primary {
    border-color: rgba(0, 0, 0, 0.25);
    background: linear-gradient(#5b95ff, #3f7cf2);
    box-shadow: inset 0 1px rgba(255, 255, 255, 0.22);
    color: #fff;
  }
</style>
