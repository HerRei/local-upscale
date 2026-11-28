// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  chooseExternalFFmpeg: vi.fn<() => Promise<string | null>>(async () => null),
  detectExternalFFmpeg: vi.fn<() => Promise<string[]>>(async () => []),
  updateStatus: vi.fn(async () => ({
    configured: true,
    managed_by_store: false,
    managed_by_package_manager: false,
    channel: 'beta',
    target: 'darwin-aarch64-mps-native',
    stage: 'idle',
    version: '',
    notes: '',
    size: 0,
    downloaded: 0,
    message: '',
    settings_recovery: false,
  })),
  listenForUpdates: vi.fn(async () => () => {}),
}));
vi.mock('./lib/api', () => api);

import SettingsSheet from './SettingsSheet.svelte';
import { demoSnapshot } from './lib/demo';
import type { CapabilityInfo, SettingsSection, UiSettings } from './lib/types';

afterEach(cleanup);

function withPlatform(platform: string, userAgent?: string): () => void {
  const platformDesc = Object.getOwnPropertyDescriptor(navigator, 'platform');
  const uaDesc = Object.getOwnPropertyDescriptor(navigator, 'userAgent');
  Object.defineProperty(navigator, 'platform', { value: platform, configurable: true });
  if (userAgent !== undefined) {
    Object.defineProperty(navigator, 'userAgent', { value: userAgent, configurable: true });
  }
  return () => {
    if (platformDesc) Object.defineProperty(navigator, 'platform', platformDesc);
    else delete (navigator as unknown as Record<string, unknown>).platform;
    if (uaDesc) Object.defineProperty(navigator, 'userAgent', uaDesc);
    else delete (navigator as unknown as Record<string, unknown>).userAgent;
  };
}

const macCapabilities: CapabilityInfo = {
  ...demoSnapshot().capabilities,
  devices: [
    {
      id: 'mps',
      type: 'mps',
      name: 'Apple GPU (Metal)',
      total_memory: 16 * 1024 ** 3,
      free_memory: 12 * 1024 ** 3,
      supports_fp16: true,
      is_integrated: true,
      recommended_tile_sizes: [128, 256],
      neural_engine: true,
    },
    ...demoSnapshot().capabilities.devices,
  ],
  media: {
    system_codecs: {
      label: 'macOS',
      decode: ['h264', 'hevc'],
      encode: ['h264', 'hevc', 'prores'],
      containers: ['mp4', 'mov'],
    },
    image_output: ['heic'],
    external_ffmpeg: { detected: [], candidates: [] },
  },
};

function renderSheet(overrides: Record<string, unknown> = {}) {
  const snapshot = demoSnapshot();
  const props = {
    settings: { ...snapshot.settings },
    capabilities: macCapabilities,
    engines: snapshot.engines,
    updateStatus: {
      configured: true,
      managed_by_store: false,
      managed_by_package_manager: false,
      channel: 'beta',
      target: 'darwin-aarch64-mps-native',
      stage: 'idle',
      version: '',
      notes: '',
      size: 0,
      downloaded: 0,
      message: '',
      settings_recovery: false,
    },
    section: 'general' as SettingsSection,
    focusTarget: null as 'hardware' | 'ffmpeg' | null,
    settingsLocked: false,
    defaultModelDirectory: '/Users/demo/Library/Application Support/LocalSR/models',
    modelDirectoryMissing: false,
    movingModels: false,
    modelMoveProgress: null,
    cacheSize: 2048,
    cacheBusy: false,
    linuxMenuAvailable: false,
    linuxMenuOffer: false,
    linuxMenuInstalled: false,
    linuxMenuBusy: false,
    setPreference: vi.fn(),
    updateSettings: vi.fn(),
    chooseModelFolder: vi.fn(async () => undefined),
    resetModelFolder: vi.fn(async () => undefined),
    clearCache: vi.fn(async () => undefined),
    loadCacheSize: vi.fn(),
    refreshCapabilities: vi.fn(async () => true),
    onOpenEngineManager: vi.fn(),
    onOpenIntegrations: vi.fn(),
    onOpenUpdates: vi.fn(),
    onInstallLinuxMenu: vi.fn(async () => undefined),
    onUninstallLinuxMenu: vi.fn(async () => undefined),
    onClose: vi.fn(),
    ...overrides,
  };
  return { ...render(SettingsSheet, props), props };
}

describe('SettingsSheet', () => {
  it('calls the right setters across every section including auto_cache_cleanup', async () => {
    const restore = withPlatform('MacIntel', 'Mozilla/5.0 (Macintosh; Intel Mac OS X)');
    try {
      const user = userEvent.setup();
      const { props } = renderSheet({
        settings: {
          ...demoSnapshot().settings,
          model_directory: '/Volumes/SSD/models',
          external_ffmpeg_path: '/opt/bin/ffmpeg',
        },
      });

      // General
      await user.click(screen.getByRole('switch', { name: 'Show LocalSR in the menu bar' }));
      expect(props.setPreference).toHaveBeenCalledWith('show_in_menu_bar', false);

      await user.click(screen.getByRole('switch', { name: 'Monochrome menu bar icon' }));
      expect(props.setPreference).toHaveBeenCalledWith('menu_bar_icon_monochrome', false);

      await user.click(screen.getByRole('switch', { name: 'Notify when a file finishes' }));
      expect(props.setPreference).toHaveBeenCalledWith('notify_on_finish', false);

      await user.click(screen.getByRole('switch', { name: 'Play a sound with the notification' }));
      expect(props.setPreference).toHaveBeenCalledWith('completion_sound', true);

      await user.click(screen.getByRole('switch', { name: 'Show previews while a job runs' }));
      expect(props.updateSettings).toHaveBeenCalledWith({ enable_live_preview: false });

      const patch = screen.getByLabelText('Preview fixes on your photo') as HTMLSelectElement;
      expect([...patch.options].map((option) => option.textContent)).toEqual([
        'Automatic',
        'Always update by itself',
        'Update when I click',
        'Off',
      ]);
      expect(patch.value).toBe('auto');
      await user.selectOptions(patch, 'manual');
      expect(props.setPreference).toHaveBeenCalledWith('preview_patch', 'manual');
      await user.selectOptions(patch, 'off');
      expect(props.setPreference).toHaveBeenCalledWith('preview_patch', 'off');

      await user.click(screen.getByRole('button', { name: 'Manage…' }));
      expect(props.onOpenIntegrations).toHaveBeenCalledTimes(1);

      // Appearance
      await user.click(screen.getByRole('tab', { name: 'Appearance' }));
      await user.click(screen.getByRole('radio', { name: 'Light' }));
      expect(props.setPreference).toHaveBeenCalledWith('appearance', 'light');

      await user.click(screen.getByRole('switch', { name: 'Colour-blind friendly colours' }));
      expect(props.setPreference).toHaveBeenCalledWith('colour_blind_palette', true);

      await user.click(screen.getByRole('switch', { name: 'High contrast' }));
      expect(props.setPreference).toHaveBeenCalledWith('contrast', 'high');

      await user.selectOptions(screen.getByLabelText('Interface text'), '125');
      expect(props.setPreference).toHaveBeenCalledWith('interface_scale', 125);

      await user.selectOptions(screen.getByLabelText('Viewer background'), 'grey');
      expect(props.setPreference).toHaveBeenCalledWith('viewer_background', 'grey');

      // Performance
      await user.click(screen.getByRole('tab', { name: 'Performance' }));
      await user.selectOptions(screen.getByLabelText('Hardware'), 'neural-engine');
      expect(props.updateSettings).toHaveBeenCalledWith({
        device_id: 'mps',
        neural_engine: true,
      });

      await user.click(screen.getByRole('button', { name: 'Refresh' }));
      expect(props.refreshCapabilities).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole('button', { name: 'Change…' }));
      expect(props.onOpenEngineManager).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole('switch', { name: 'Run at low priority' }));
      expect(props.setPreference).toHaveBeenCalledWith('low_priority', true);

      await user.click(
        screen.getByRole('switch', { name: 'Keep the computer awake while working' }),
      );
      expect(props.setPreference).toHaveBeenCalledWith('keep_awake', false);

      // Storage
      await user.click(screen.getByRole('tab', { name: 'Storage' }));
      expect(props.loadCacheSize).toHaveBeenCalled();

      await user.click(screen.getByRole('button', { name: /^Model folder / }));
      expect(props.chooseModelFolder).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole('button', { name: 'Reset to default' }));
      expect(props.resetModelFolder).toHaveBeenCalledTimes(1);

      api.detectExternalFFmpeg.mockResolvedValueOnce(['/usr/local/bin/ffmpeg']);
      await user.click(screen.getByRole('button', { name: 'Find installed' }));
      expect(props.updateSettings).toHaveBeenCalledWith({
        external_ffmpeg_path: '/usr/local/bin/ffmpeg',
      });

      await user.click(screen.getByRole('button', { name: 'Don’t use' }));
      expect(props.updateSettings).toHaveBeenCalledWith({ external_ffmpeg_path: '' });

      await user.click(screen.getByRole('button', { name: 'Clear cache' }));
      expect(props.clearCache).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole('switch', { name: 'Remove unused caches automatically' }));
      expect(props.setPreference).toHaveBeenCalledWith('auto_cache_cleanup', false);

      // Privacy & updates
      await user.click(screen.getByRole('tab', { name: 'Privacy & updates' }));
      await user.click(
        screen.getByRole('switch', { name: 'Check for updates when LocalSR starts' }),
      );
      expect(props.setPreference).toHaveBeenCalledWith('check_updates_at_launch', false);

      await user.click(
        screen.getByRole('switch', { name: 'Send an anonymous count with each update check' }),
      );
      expect(props.setPreference).toHaveBeenCalledWith('anonymous_update_count', true);

      await user.click(screen.getByRole('button', { name: 'Check for Updates…' }));
      expect(props.onOpenUpdates).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole('switch', { name: 'Offer to report crashes' }));
      expect(props.setPreference).toHaveBeenCalledWith('crash_reports', true);
    } finally {
      restore();
    }
  });

  it('disables idle-only rows when locked with aria-describedby, while low-priority and auto-cleanup stay enabled', async () => {
    const user = userEvent.setup();
    renderSheet({
      settingsLocked: true,
      settings: { ...demoSnapshot().settings, model_directory: '/Volumes/SSD/models' },
    });

    const lockNote = screen.getByRole('status');
    expect(lockNote.textContent).toContain('Some settings are locked while the queue runs.');

    const livePreview = screen.getByRole('switch', {
      name: 'Show previews while a job runs',
    }) as HTMLInputElement;
    expect(livePreview.disabled).toBe(true);
    expect(livePreview.getAttribute('aria-describedby')).toContain(lockNote.id);

    await user.click(screen.getByRole('tab', { name: 'Performance' }));
    const hardware = screen.getByLabelText('Hardware') as HTMLSelectElement;
    expect(hardware.disabled).toBe(true);
    expect(hardware.getAttribute('aria-describedby')).toContain(lockNote.id);
    expect(
      (screen.getByRole('switch', { name: 'Run at low priority' }) as HTMLInputElement).disabled,
    ).toBe(false);
    const keepAwake = screen.getByRole('switch', {
      name: 'Keep the computer awake while working',
    }) as HTMLInputElement;
    expect(keepAwake.disabled).toBe(false);
    expect(keepAwake.checked).toBe(true);

    await user.click(screen.getByRole('tab', { name: 'Storage' }));
    const modelFolder = screen.getByRole('button', { name: /^Model folder / }) as HTMLButtonElement;
    expect(modelFolder.disabled).toBe(true);
    expect(modelFolder.getAttribute('aria-describedby')).toContain(lockNote.id);
    expect(
      (screen.getByRole('button', { name: 'Choose FFmpeg' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole('button', { name: 'Clear cache' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (
        screen.getByRole('switch', {
          name: 'Remove unused caches automatically',
        }) as HTMLInputElement
      ).disabled,
    ).toBe(false);

    await user.click(screen.getByRole('tab', { name: 'Appearance' }));
    expect(
      (screen.getByRole('switch', { name: 'High contrast' }) as HTMLInputElement).disabled,
    ).toBe(false);
    expect((screen.getByLabelText('Viewer background') as HTMLSelectElement).disabled).toBe(false);

    cleanup();
    const missingSettings = { ...demoSnapshot().settings };
    delete missingSettings.keep_awake;
    renderSheet({ settings: missingSettings, section: 'performance' });
    expect(
      (
        screen.getByRole('switch', {
          name: 'Keep the computer awake while working',
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
  });

  it('defaults High contrast and Viewer background when fields are missing, turns High contrast off to system, and shows the background tip', async () => {
    const user = userEvent.setup();
    const settings = { ...demoSnapshot().settings };
    delete settings.contrast;
    delete settings.viewer_background;
    const { props } = renderSheet({ settings, section: 'appearance' });

    const contrastSwitch = screen.getByRole('switch', {
      name: 'High contrast',
    }) as HTMLInputElement;
    const backgroundSelect = screen.getByLabelText('Viewer background') as HTMLSelectElement;
    expect(contrastSwitch.checked).toBe(false);
    expect(backgroundSelect.value).toBe('checker');

    const tipButton = screen.getByRole('button', { name: 'About Viewer background' });
    await user.click(tipButton);
    expect(screen.getByRole('tooltip').textContent).toContain(
      'What is shown behind the picture in the viewer and in Compare. Grey is the neutral 18% grey used to judge brightness and colour. Black and White show how edges and transparent areas hold up. Checkerboard shows where a picture is transparent. Saved files are not changed.',
    );

    cleanup();
    const highSettings = { ...demoSnapshot().settings, contrast: 'high' as const };
    const { props: highProps } = renderSheet({ settings: highSettings, section: 'appearance' });
    const onSwitch = screen.getByRole('switch', { name: 'High contrast' }) as HTMLInputElement;
    expect(onSwitch.checked).toBe(true);
    await user.click(onSwitch);
    expect(highProps.setPreference).toHaveBeenCalledWith('contrast', 'system');
  });

  it('shows monochrome only on macOS, disables it when menu bar is off, and disables sound when notify is off', () => {
    const restoreWin = withPlatform('Win32', 'Mozilla/5.0 (Windows NT 10.0)');
    try {
      renderSheet();
      expect(screen.getByRole('switch', { name: 'Show LocalSR in the system tray' })).toBeTruthy();
      expect(screen.queryByRole('switch', { name: 'Monochrome menu bar icon' })).toBeNull();
    } finally {
      restoreWin();
    }
    cleanup();

    const restoreMac = withPlatform('MacIntel', 'Mozilla/5.0 (Macintosh; Intel Mac OS X)');
    try {
      renderSheet({
        settings: {
          ...demoSnapshot().settings,
          show_in_menu_bar: false,
          notify_on_finish: false,
        },
      });
      const mono = screen.getByRole('switch', {
        name: 'Monochrome menu bar icon',
      }) as HTMLInputElement;
      expect(mono.disabled).toBe(true);
      const sound = screen.getByRole('switch', {
        name: 'Play a sound with the notification',
      }) as HTMLInputElement;
      expect(sound.disabled).toBe(true);
      expect(
        screen.getByText(/Turn on "Notify when a file finishes" to play a sound/),
      ).toBeTruthy();
    } finally {
      restoreMac();
    }
  });

  it('hides updater rows and Software update on store builds and renames the tab to Privacy', async () => {
    const user = userEvent.setup();
    renderSheet({
      updateStatus: {
        configured: false,
        managed_by_store: true,
        managed_by_package_manager: false,
        channel: '',
        target: '',
        stage: 'store',
        version: '',
        notes: '',
        size: 0,
        downloaded: 0,
        message: '',
        settings_recovery: false,
      },
    });

    const privacyTab = screen.getByRole('tab', { name: 'Privacy' });
    expect(screen.queryByRole('tab', { name: 'Privacy & updates' })).toBeNull();
    await user.click(privacyTab);
    expect(
      screen.queryByRole('switch', { name: 'Check for updates when LocalSR starts' }),
    ).toBeNull();
    expect(
      screen.queryByRole('switch', { name: 'Send an anonymous count with each update check' }),
    ).toBeNull();
    expect(screen.queryByRole('button', { name: 'Check for Updates…' })).toBeNull();
    expect(screen.getByRole('switch', { name: 'Offer to report crashes' })).toBeTruthy();
  });

  it('shows the update switches disabled with a note in a build without an update feed', async () => {
    const user = userEvent.setup();
    renderSheet({
      updateStatus: {
        configured: false,
        managed_by_store: false,
        managed_by_package_manager: false,
        channel: '',
        target: '',
        stage: 'idle',
        version: '',
        notes: '',
        size: 0,
        downloaded: 0,
        message: '',
        settings_recovery: false,
      },
    });
    await user.click(screen.getByRole('tab', { name: 'Privacy & updates' }));
    const check = screen.getByRole('switch', { name: 'Check for updates when LocalSR starts' });
    const count = screen.getByRole('switch', {
      name: 'Send an anonymous count with each update check',
    });
    expect((check as HTMLInputElement).disabled).toBe(true);
    expect((count as HTMLInputElement).disabled).toBe(true);
    expect((check as HTMLInputElement).checked).toBe(false);
    expect((count as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText(/made without an update feed, so these two stay off/)).toBeTruthy();
  });

  it('supports Esc, HelpTip Esc priority, focus return, rail arrow keys, scroll reset, and focusTarget', async () => {
    const opener = document.createElement('button');
    opener.textContent = 'Open';
    document.body.appendChild(opener);
    opener.focus();

    const user = userEvent.setup();
    const { props, unmount } = renderSheet();

    const tablist = screen.getByRole('tablist', { name: 'Settings sections' });
    expect(tablist.getAttribute('aria-orientation')).toBe('vertical');
    const generalTab = within(tablist).getByRole('tab', { name: 'General' });
    expect(generalTab.getAttribute('aria-selected')).toBe('true');
    expect(generalTab.tabIndex).toBe(0);
    expect(document.activeElement).toBe(generalTab);

    const panel = screen.getByRole('tabpanel') as HTMLElement;
    panel.scrollTop = 120;

    await user.keyboard('{ArrowDown}');
    const appearanceTab = within(tablist).getByRole('tab', { name: 'Appearance' });
    expect(appearanceTab.getAttribute('aria-selected')).toBe('true');
    expect(appearanceTab.tabIndex).toBe(0);
    expect(generalTab.tabIndex).toBe(-1);
    expect(document.activeElement).toBe(appearanceTab);
    expect(screen.getByRole('tabpanel').scrollTop).toBe(0);

    await user.keyboard('{End}');
    expect(within(tablist).getByRole('tab', { name: 'Privacy & updates' }).tabIndex).toBe(0);
    await user.keyboard('{Home}');
    expect(generalTab.tabIndex).toBe(0);

    // Performance HelpTip takes Esc before Settings closes.
    await user.click(within(tablist).getByRole('tab', { name: 'Performance' }));
    const tipButton = screen.getByRole('button', { name: 'About Hardware' });
    await user.click(tipButton);
    const tooltip = screen.getByRole('tooltip');
    expect(tooltip.hidden).toBe(false);
    await fireEvent.keyDown(tipButton, { key: 'Escape' });
    expect(tooltip.hidden).toBe(true);
    expect(props.onClose).not.toHaveBeenCalled();

    // Esc now closes the sheet; unmount returns focus to opener.
    await user.keyboard('{Escape}');
    expect(props.onClose).toHaveBeenCalledTimes(1);
    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();

    // Opening via a Change… shortcut focuses the target control instead of the tab.
    renderSheet({ section: 'performance', focusTarget: 'hardware' });
    expect(document.activeElement).toBe(screen.getByLabelText('Hardware'));
  });

  it('shows a help line on every row in every section', async () => {
    const restoreMac = withPlatform('MacIntel', 'Mozilla/5.0 (Macintosh; Intel Mac OS X)');
    try {
      const user = userEvent.setup();
      renderSheet({ linuxMenuAvailable: true });
      const tabs = screen.getAllByRole('tab');
      for (const tab of tabs) {
        await user.click(tab);
        const panel = screen.getByRole('tabpanel');
        const rows = panel.querySelectorAll('.setting-row, .setting-block');
        expect(rows.length).toBeGreaterThan(0);
        for (const row of rows) {
          const desc = row.querySelector('.setting-desc');
          expect(desc?.textContent?.trim()).not.toBe('');
        }
      }
    } finally {
      restoreMac();
    }
  });

  it('shows host errors for External FFmpeg detection/pick and moving progress for Model folder', async () => {
    const user = userEvent.setup();
    const view = renderSheet({
      section: 'storage',
      settings: {
        ...demoSnapshot().settings,
        model_directory: '/Volumes/SSD/models',
        external_ffmpeg_path: '/usr/bin/ffmpeg',
      },
      movingModels: true,
      modelMoveProgress: {
        moved_bytes: 50,
        total_bytes: 100,
        moved_files: 1,
        total_files: 2,
        progress: 50,
        current_file: 'a.pth',
      },
      cacheSize: 1024,
      cacheBusy: false,
    });

    expect(screen.getByText('Moving 1 of 2 files…')).toBeTruthy();
    expect(screen.getByText('50%')).toBeTruthy();
    const progress = document.querySelector('progress.download-progress') as HTMLProgressElement;
    expect(progress.value).toBe(50);
    expect(
      (screen.getByRole('button', { name: /^Model folder / }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect((screen.getByRole('button', { name: 'Done' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Wait for the model move to finish.')).toBeTruthy();

    api.detectExternalFFmpeg.mockRejectedValueOnce('The search was refused.');
    await user.click(screen.getByRole('button', { name: 'Find installed' }));
    expect(await screen.findByText(/The search was refused\./)).toBeTruthy();

    api.chooseExternalFFmpeg.mockRejectedValueOnce('The file picker failed.');
    await user.click(screen.getByRole('button', { name: 'Choose FFmpeg' }));
    expect(await screen.findByText(/The file picker failed\./)).toBeTruthy();

    expect(screen.getByText(/1 KB/)).toBeTruthy();
    await view.rerender({
      section: 'storage',
      settings: { ...demoSnapshot().settings },
      capabilities: macCapabilities,
      setPreference: vi.fn(),
      updateSettings: vi.fn(),
      onClose: vi.fn(),
      cacheSize: 1024,
      cacheBusy: true,
    });
    expect(
      (screen.getByRole('button', { name: 'Clear cache' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('shows Engine row on supported builds and hides on unsupported or Store builds', async () => {
    const engines = structuredClone(demoSnapshot().engines!);
    const view = renderSheet({ section: 'performance', engines });
    expect(screen.getByText('Engine')).toBeTruthy();
    expect(screen.getByText('CPU')).toBeTruthy();

    await view.rerender({
      section: 'performance',
      settings: { ...demoSnapshot().settings },
      capabilities: macCapabilities,
      engines: { ...engines, active_backend: 'rocm' },
      setPreference: vi.fn(),
      updateSettings: vi.fn(),
      onClose: vi.fn(),
    });
    expect(screen.getByText('AMD ROCm')).toBeTruthy();

    await view.rerender({
      section: 'performance',
      settings: { ...demoSnapshot().settings },
      capabilities: macCapabilities,
      engines: { ...engines, supported: false },
      setPreference: vi.fn(),
      updateSettings: vi.fn(),
      onClose: vi.fn(),
    });
    expect(screen.queryByText('Engine')).toBeNull();

    await view.rerender({
      section: 'performance',
      settings: { ...demoSnapshot().settings },
      capabilities: macCapabilities,
      engines: { ...engines, supported: true, managed_by_store: true },
      setPreference: vi.fn(),
      updateSettings: vi.fn(),
      onClose: vi.fn(),
    });
    expect(screen.queryByText('Engine')).toBeNull();
  });

  it('cannot be closed by Esc, backdrop click, or Done while moving models, and resets scroll on section change', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderSheet({
      section: 'storage',
      movingModels: true,
      cacheSize: 1024,
      onClose,
    });

    await user.keyboard('{Escape}');
    const backdrop = document.querySelector('.settings-backdrop') as HTMLElement;
    await fireEvent.click(backdrop);
    const done = screen.getByRole('button', { name: 'Done' }) as HTMLButtonElement;
    expect(done.disabled).toBe(true);
    await user.click(done);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText('Wait for the model move to finish.')).toBeTruthy();

    // When cacheBusy is true but settingsLocked is false, Clear cache must not point at an absent lock note.
    const cacheBtn = screen.getByRole('button', { name: 'Clear cache' });
    expect(cacheBtn.getAttribute('aria-describedby')).toBe('settings-cache-desc');

    const panel = document.querySelector('.settings-panel') as HTMLElement;
    panel.scrollTop = 180;
    await user.click(screen.getByRole('tab', { name: 'General' }));
    expect((document.querySelector('.settings-panel') as HTMLElement).scrollTop).toBe(0);
  });

  it('shows When you stop a job in General and saves stopped_job_result via setPreference even while locked', async () => {
    const user = userEvent.setup();
    const { props } = renderSheet({ settingsLocked: true });
    const select = screen.getByLabelText('When you stop a job') as HTMLSelectElement;
    expect(select.disabled).toBe(false);
    expect(select.value).toBe('ask');
    expect([...select.options].map((option) => option.textContent)).toEqual([
      'Ask',
      'Keep the finished part',
      'Discard',
    ]);
    expect(
      screen.getByText(
        /A video keeps its finished frames as a playable video; a photo keeps its finished tiles, with the rest left as in the original\. Both are marked Unfinished\. A stopped edit is always discarded; finished files in a queue always stay\./,
      ),
    ).toBeTruthy();

    await user.selectOptions(select, 'keep');
    expect(props.setPreference).toHaveBeenCalledWith('stopped_job_result', 'keep');
    await user.selectOptions(select, 'discard');
    expect(props.setPreference).toHaveBeenCalledWith('stopped_job_result', 'discard');
  });
});
