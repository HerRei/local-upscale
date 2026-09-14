// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import LicenseDownload from './LicenseDownload.svelte';
import { demoSnapshot } from './lib/demo';

afterEach(cleanup);

function props() {
  const model = demoSnapshot().catalog.models.find(
    (m) => m.model_id === 'realplksr_nomoswebphoto_x4',
  )!;
  return {
    model: { ...model, installed: false },
    download: vi.fn(async () => {}),
    openLicense: vi.fn(async () => {}),
    openSource: vi.fn(async () => {}),
  };
}

it('shows the author and license with source and terms actions, without imposing non-commercial terms', async () => {
  const p = props();
  const user = userEvent.setup();
  render(LicenseDownload, p);
  const notice = screen.getByRole('note', { name: 'Model license' });
  expect(notice.textContent).toContain('CC BY 4.0');
  expect(notice.textContent).toContain('Philip Hofmann');
  expect(notice.textContent).toContain('unchanged');
  expect(notice.textContent).not.toMatch(/non-commercial|clarification|unverified/);
  expect(screen.queryByRole('checkbox')).toBeNull();
  await user.click(screen.getByRole('button', { name: /Model source/ }));
  await user.click(screen.getByRole('button', { name: /License terms/ }));
  expect(p.openSource).toHaveBeenCalledTimes(1);
  expect(p.openLicense).toHaveBeenCalledTimes(1);
  expect(p.download).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: /Download .* MB/ }));
  expect(p.download).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('keeps cancellation available during a download and exposes real progress', async () => {
  const p = props();
  render(LicenseDownload, { ...p, downloading: true, progress: 42 });
  expect(screen.getByRole('progressbar').getAttribute('value')).toBe('42');
  await userEvent.click(screen.getByRole('button', { name: 'Downloading 42% · Cancel' }));
  expect(p.download).toHaveBeenCalledTimes(1);
});

it('keeps attribution visible after installation without offering another download', () => {
  const p = props();
  render(LicenseDownload, { ...p, model: { ...p.model, installed: true } });
  expect(screen.getByRole('note', { name: 'Model license' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: /Download/ })).toBeNull();
});

it('blocks downloads while processing controls are locked', async () => {
  const p = props();
  render(LicenseDownload, { ...p, disabled: true });
  await userEvent.click(screen.getByRole('button', { name: /Download .* MB/ }));
  expect(p.download).not.toHaveBeenCalled();
});
