// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import ModelCredit from './ModelCredit.svelte';
import { demoSnapshot } from './lib/demo';

afterEach(cleanup);

function props() {
  const model = demoSnapshot().catalog.models.find(
    (m) => m.model_id === 'realplksr_nomoswebphoto_x4',
  )!;
  return {
    model: { ...model, installed: false },
    openLicense: vi.fn(async () => {}),
    openSource: vi.fn(async () => {}),
  };
}

it('credits the author and license with source and terms, without imposing non-commercial terms', async () => {
  const p = props();
  const user = userEvent.setup();
  render(ModelCredit, p);
  const note = screen.getByRole('note', { name: 'Model license' });
  expect(note.textContent).toContain('CC BY 4.0');
  expect(note.textContent).toContain('Philip Hofmann');
  expect(note.textContent).toContain('unchanged');
  expect(note.textContent).not.toMatch(/non-commercial|clarification|unverified/);
  await user.click(screen.getByRole('button', { name: 'Model source' }));
  await user.click(screen.getByRole('button', { name: 'License terms' }));
  expect(p.openSource).toHaveBeenCalledTimes(1);
  expect(p.openLicense).toHaveBeenCalledTimes(1);
});

it('leaves the download to the Start button', () => {
  render(ModelCredit, props());
  expect(screen.queryByRole('button', { name: /Download/ })).toBeNull();
});
