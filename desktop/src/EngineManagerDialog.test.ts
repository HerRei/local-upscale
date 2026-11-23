// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import EngineManagerDialog from './EngineManagerDialog.svelte';
import { demoSnapshot } from './lib/demo';

afterEach(cleanup);

it('reinstalls an unverified engine when Use is clicked', async () => {
  const engines = demoSnapshot().engines!;
  engines.installed.push({
    id: 'windows-x86_64-directml-old',
    backend: 'directml',
    bundled: false,
    active: false,
    path: '',
    needs_reinstall: true,
  });
  const onDownload = vi.fn();
  const onUse = vi.fn();
  render(EngineManagerDialog, {
    engines,
    onDownload,
    onUse,
    onClose: vi.fn(),
    onRefreshCatalog: vi.fn(),
    onCancelDownload: vi.fn(),
    onRemove: vi.fn(),
    onImport: vi.fn(),
  });
  const row = screen.getByText('DirectML').closest('li')!;
  expect(within(row).getByText(/Needs reinstall/)).toBeTruthy();
  await userEvent.click(within(row).getByRole('button', { name: 'Use' }));
  expect(onDownload).toHaveBeenCalledWith('directml');
  expect(onUse).not.toHaveBeenCalled();
});

it('retries the current removal error after an earlier download error', async () => {
  const engines = demoSnapshot().engines!;
  engines.download = {
    stage: 'error',
    backend: 'cuda',
    engine_id: 'cuda',
    downloaded: 0,
    total: 0,
    error: 'Download failed',
  };
  engines.installed.push({
    id: 'directml',
    backend: 'directml',
    bundled: false,
    active: false,
    path: '',
  });
  const onRemove = vi.fn();
  const onDownload = vi.fn();
  render(EngineManagerDialog, {
    engines,
    errorMessage: 'Removal failed',
    errorBackend: 'directml',
    errorAction: 'remove',
    onRemove,
    onDownload,
    onClose: vi.fn(),
    onRefreshCatalog: vi.fn(),
    onCancelDownload: vi.fn(),
    onUse: vi.fn(),
    onImport: vi.fn(),
  });
  const row = screen.getByText('DirectML').closest('li')!;
  expect(within(row).getByText('Removal failed')).toBeTruthy();
  await userEvent.click(within(row).getByRole('button', { name: 'Try again' }));
  expect(onRemove).toHaveBeenCalledWith('directml');
  expect(onDownload).not.toHaveBeenCalled();
});

it('shows a Verify button next to Remove and triggers onVerify with the engine id', async () => {
  const engines = demoSnapshot().engines!;
  engines.installed.push({
    id: 'windows-x86_64-directml-ok',
    backend: 'directml',
    bundled: false,
    active: false,
    path: '',
  });
  const onVerify = vi.fn();
  const { rerender } = render(EngineManagerDialog, {
    engines,
    onVerify,
    onClose: vi.fn(),
    onRefreshCatalog: vi.fn(),
    onDownload: vi.fn(),
    onCancelDownload: vi.fn(),
    onUse: vi.fn(),
    onRemove: vi.fn(),
    onImport: vi.fn(),
  });
  const row = screen.getByText('DirectML').closest('li')!;
  const verifyButton = within(row).getByRole('button', { name: 'Verify DirectML' });
  expect(verifyButton.className).toContain('button compact ghost');
  await userEvent.click(verifyButton);
  expect(onVerify).toHaveBeenCalledWith('windows-x86_64-directml-ok');

  await rerender({
    engines,
    verifiedBackend: 'directml',
    onVerify,
    onClose: vi.fn(),
    onRefreshCatalog: vi.fn(),
    onDownload: vi.fn(),
    onCancelDownload: vi.fn(),
    onUse: vi.fn(),
    onRemove: vi.fn(),
    onImport: vi.fn(),
  });
  expect(within(row).getByText(/Verified/)).toBeTruthy();
});
