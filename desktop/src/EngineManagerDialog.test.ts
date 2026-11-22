// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import EngineManagerDialog from './EngineManagerDialog.svelte';
import { demoSnapshot } from './lib/demo';

afterEach(cleanup);

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
