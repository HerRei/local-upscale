import { get, writable } from 'svelte/store';
import * as api from './api';

export const emptyUpdateStatus: api.UpdateStatus = {
  configured: false,
  managed_by_store: false,
  channel: 'beta',
  target: '',
  stage: 'idle',
  version: '',
  notes: '',
  size: 0,
  downloaded: 0,
  message: '',
  settings_recovery: false,
};

/** The one update state shared by the launch notice, the app menu and the update dialog. */
export const updateState = writable<api.UpdateStatus>(emptyUpdateStatus);
export const updateDialogOpen = writable(false);
export const updateError = writable('');

const DISMISSED_KEY = 'localsr.update.dismissed';

export function dismissedVersion(): string {
  try {
    return window.localStorage.getItem(DISMISSED_KEY) ?? '';
  } catch {
    return '';
  }
}

export function dismissVersion(version: string): void {
  try {
    window.localStorage.setItem(DISMISSED_KEY, version);
  } catch {
    // Private or restricted storage: the notice simply returns next launch.
  }
}

export async function refreshUpdateStatus(): Promise<api.UpdateStatus> {
  const status = await api.updateStatus();
  updateState.set(status);
  return status;
}

/**
 * Follow update progress and check the feed once at launch. A launch check is
 * silent: being offline or unconfigured must never interrupt the user.
 */
export async function startUpdateWatcher(): Promise<() => void> {
  if (!api.isTauri()) return () => {};
  const stop = await api.listenForUpdates((status) => updateState.set(status));
  try {
    const status = await refreshUpdateStatus();
    if (status.configured && !status.managed_by_store && status.stage === 'idle') {
      updateState.set(await api.checkUpdate(status.channel));
    }
  } catch {
    // Offline or feed unavailable; "Check for Updates…" reports errors explicitly.
  }
  return stop;
}

/** Download the available update if needed, then install it and restart. */
export async function updateNow(processing: boolean): Promise<void> {
  updateError.set('');
  try {
    let status = get(updateState);
    if (status.stage === 'available') {
      await api.downloadUpdate();
      status = await refreshUpdateStatus();
    }
    if (status.stage === 'downloaded' && !processing && !status.settings_recovery) {
      await api.installUpdate();
    }
  } catch (error) {
    updateError.set(String(error));
    await refreshUpdateStatus().catch(() => undefined);
  }
}
