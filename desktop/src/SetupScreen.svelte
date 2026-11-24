<script lang="ts">
  import { createEventDispatcher, tick } from 'svelte';
  import { modal } from './lib/modal';
  import type { UiSettings } from './lib/types';
  import ThemePicker from './ThemePicker.svelte';
  import HelpTip from './HelpTip.svelte';

  type Appearance = UiSettings['appearance'];
  type Contrast = NonNullable<UiSettings['contrast']>;
  export let appearance: Appearance = 'dark';
  export let colourBlind = false;
  export let contrast: Contrast = 'system';
  export let checkAtLaunch = true;
  export let anonymousCount = true;
  export let crashReports = true;
  export let menuBar = true;
  export let sound = false;
  export let storeBuild = false;
  export let saving = false;
  export let error = '';
  let step: 1 | 2 = 1;
  let heading: HTMLHeadingElement;
  const os = /Mac/.test(navigator.platform)
    ? 'macos'
    : /Win/.test(navigator.platform)
      ? 'windows'
      : 'linux';
  const trayName: 'menu bar' | 'system tray' | null = os === 'macos' ? 'menu bar' : 'system tray';
  const menu = os === 'macos' ? 'LocalSR' : 'Help';
  const trayTip =
    os === 'macos'
      ? 'An icon in the menu bar shows what is running and how many files wait, with Pause and Resume.'
      : os === 'windows'
        ? 'An icon in the system tray shows what is running and how many files wait, with Pause and Resume. Windows may keep it behind the ^ arrow next to the clock.'
        : 'An icon in the system tray shows what is running and how many files wait, with Pause and Resume. Some desktops, such as GNOME without an extension, do not show tray icons.';
  const dispatch = createEventDispatcher<{
    preview: { appearance: Appearance; colourBlind: boolean; contrast: Contrast };
    finish: {
      appearance: Appearance;
      colourBlind: boolean;
      contrast: Contrast;
      checkAtLaunch: boolean;
      anonymousCount: boolean;
      crashReports: boolean;
      menuBar: boolean | null;
      sound: boolean;
    };
  }>();
  function preview(): void {
    dispatch('preview', { appearance, colourBlind, contrast });
  }
  async function go(next: 1 | 2): Promise<void> {
    step = next;
    await tick();
    heading.focus();
  }
  function finish(): void {
    dispatch('finish', {
      appearance,
      colourBlind,
      contrast,
      checkAtLaunch,
      anonymousCount,
      crashReports,
      menuBar: trayName === null ? null : menuBar,
      sound,
    });
  }
</script>

<div class="modal-backdrop" role="presentation">
  <div
    class="modal setup-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="setup-title"
    tabindex="-1"
    use:modal
  >
    <div class="setup-head">
      <h2 id="setup-title">Welcome to LocalSR</h2>
      <span class="setup-step" aria-label="Step {step} of 2">{step} of 2</span>
    </div>
    {#if step === 1}
      <h3 bind:this={heading} tabindex="-1">Privacy</h3>
      <p class="setup-intro">
        Your photos and videos stay on this computer. Nothing is sent until you finish setup.
      </p>
      <section class="setup-section">
        {#if !storeBuild}
          <div class="setting-row">
            <div class="setting-copy">
              <span class="label-with-tip"
                ><label class="setting-label" for="setup-check-updates"
                  >Check for updates when LocalSR starts</label
                ><HelpTip
                  label="Check for updates when LocalSR starts"
                  text={`Reads the update feed on GitHub Pages once at launch and tells you when a newer version exists. Nothing about you or your files is sent; GitHub sees your IP address, as any website does. With this off, Check for Updates… in the ${menu} menu still works.`}
                /></span
              >
            </div>
            <input
              id="setup-check-updates"
              class="switch"
              type="checkbox"
              role="switch"
              bind:checked={checkAtLaunch}
            />
          </div>
          <div class="setting-row">
            <div class="setting-copy">
              <span class="label-with-tip"
                ><label class="setting-label" for="setup-anonymous-count"
                  >Send an anonymous count with each update check</label
                ><HelpTip
                  label="Send an anonymous count with each update check"
                  text="After an update check, sends the app version, platform and release channel to the developer's server. No ID, no cookies, nothing about your files, and your IP address is not stored. It shows how many copies of LocalSR are in use."
                /></span
              >
            </div>
            <input
              id="setup-anonymous-count"
              class="switch"
              type="checkbox"
              role="switch"
              bind:checked={anonymousCount}
            />
          </div>
        {/if}
        <div class="setting-row">
          <div class="setting-copy">
            <span class="label-with-tip"
              ><label class="setting-label" for="setup-crash-reports">Offer to report crashes</label
              ><HelpTip
                label="Offer to report crashes"
                text="After a crash, LocalSR asks at the next start whether you want to report it. Reports stay on this computer and are never sent automatically: you read the report first and decide whether to open a GitHub issue with it. File names and paths are removed."
              /></span
            >
          </div>
          <input
            id="setup-crash-reports"
            class="switch"
            type="checkbox"
            role="switch"
            bind:checked={crashReports}
          />
        </div>
      </section>
      <div class="setup-footer">
        <p class="setup-note">
          You can change all of this later in Settings → {storeBuild
            ? 'Privacy'
            : 'Privacy & updates'}.
        </p>
        <div class="setup-buttons">
          <button type="button" class="button primary" on:click={() => go(2)}>Continue</button>
        </div>
      </div>
    {:else}
      <h3 bind:this={heading} tabindex="-1">Look and feel</h3>
      <span id="setup-theme-label" class="visually-hidden">Theme</span>
      <ThemePicker
        {appearance}
        {colourBlind}
        labelledBy="setup-theme-label"
        on:choose={(event) => {
          appearance = event.detail;
          preview();
        }}
      />
      <div class="setting-row setup-appearance-toggle">
        <label class="setting-label" for="setup-colour-blind">Colour-blind friendly colours</label
        ><input
          id="setup-colour-blind"
          class="switch"
          type="checkbox"
          role="switch"
          bind:checked={colourBlind}
          on:change={preview}
        />
      </div>
      <div class="setting-row">
        <div class="setting-copy">
          <span class="label-with-tip"
            ><label class="setting-label" for="setup-contrast">High contrast</label><HelpTip
              label="High contrast"
              text="Stronger text, borders and focus outlines. Also turns on by itself when your computer asks for more contrast."
            /></span
          >
        </div>
        <input
          id="setup-contrast"
          class="switch"
          type="checkbox"
          role="switch"
          checked={contrast === 'high'}
          on:change={(event) => {
            contrast = event.currentTarget.checked ? 'high' : 'system';
            preview();
          }}
        />
      </div>
      <section class="setup-section">
        {#if trayName !== null}<div class="setting-row">
            <div class="setting-copy">
              <span class="label-with-tip"
                ><label class="setting-label" for="setup-menu-bar"
                  >Show LocalSR in the {trayName}</label
                ><HelpTip label={`Show LocalSR in the ${trayName}`} text={trayTip} /></span
              >
            </div>
            <input
              id="setup-menu-bar"
              class="switch"
              type="checkbox"
              role="switch"
              bind:checked={menuBar}
            />
          </div>{/if}
        <div class="setting-row">
          <div class="setting-copy">
            <span class="label-with-tip"
              ><label class="setting-label" for="setup-sound"
                >Play a sound when a file finishes</label
              ><HelpTip
                label="Play a sound when a file finishes"
                text="Plays your system's notification sound with the notification that a file is finished or has failed. Notifications stay on for this; if your computer mutes them, there is no sound."
              /></span
            >
          </div>
          <input
            id="setup-sound"
            class="switch"
            type="checkbox"
            role="switch"
            bind:checked={sound}
          />
        </div>
      </section>
      {#if error}<p class="setup-error" role="alert">{error}</p>{/if}
      <div class="setup-footer">
        <p class="setup-note">You can change all of this later in Settings.</p>
        <div class="setup-buttons">
          <button type="button" class="button" disabled={saving} on:click={() => go(1)}>Back</button
          ><button type="button" class="button primary" disabled={saving} on:click={finish}
            >{saving ? 'Saving…' : 'Done'}</button
          >
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .setup-modal {
    width: min(600px, calc(100vw - 40px));
    padding: 20px 22px;
  }
  .setup-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }
  .setup-step,
  .setup-note {
    color: var(--secondary);
    font-size: calc(11.5px * var(--ui-scale));
  }
  h3 {
    margin: 0 0 10px;
    font-size: calc(12px * var(--ui-scale));
    font-weight: 600;
    color: var(--text);
    outline: none;
  }
  .setup-intro {
    margin: 0 0 14px;
  }
  .setup-section {
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }
  .setup-appearance-toggle {
    align-items: center;
    margin-top: 10px;
  }
  .setup-footer {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 16px;
    border-top: 1px solid var(--border);
    padding-top: 12px;
  }
  .setup-note {
    margin: 0;
  }
  .setup-buttons {
    display: flex;
    margin-left: auto;
    gap: 8px;
  }
  .setup-error {
    margin: 10px 0 0;
    color: var(--danger-text);
  }
</style>
