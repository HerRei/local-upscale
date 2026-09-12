// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import LicenseDownload from './LicenseDownload.svelte';
import { demoSnapshot } from './lib/demo';
afterEach(cleanup);
it('requires two distinct acknowledgements and resets after cancelling', async () => {
  const model = demoSnapshot().catalog.models.find(m => m.model_id === 'realplksr_nomoswebphoto_x4')!;
  const download = vi.fn(async () => {});
  const user = userEvent.setup();
  render(LicenseDownload, { model: { ...model, installed: false }, download, openLicense: vi.fn(), chooseAlternative: vi.fn() });
  await user.click(screen.getByRole('button', { name: /Review terms & download/ }));
  expect(screen.getByRole('button', { name: 'Continue' }).matches(':disabled')).toBe(true);
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: 'Continue' }));
  expect(screen.getByRole('button', { name: 'Confirm & download' }).matches(':disabled')).toBe(true);
  expect(download).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  await user.click(screen.getByRole('button', { name: /Review terms & download/ }));
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: 'Continue' }));
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: 'Confirm & download' }));
  expect(download).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('note', { name: 'Model license reminder' }).textContent).toContain('CC-BY-0.4');
});
