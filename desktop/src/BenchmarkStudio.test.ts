// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, expect, it } from 'vitest';
import BenchmarkStudio from './BenchmarkStudio.svelte';
import type { BenchmarkRender } from './lib/types';

afterEach(cleanup);
const capture = (scene_id: string, device: string): BenchmarkRender => ({
  scene_id, device, input_width: 512, input_height: 512,
  output_width: 2048, output_height: 2048,
  input_data_url: `data:image/jpeg;base64,source-${scene_id}`,
  output_data_url: `data:image/jpeg;base64,render-${scene_id}`
});

it('keeps the empty state distinct from a captured model output', () => {
  render(BenchmarkStudio, { running: true });
  expect(screen.getByText('Preparing the first render…')).toBeTruthy();
  expect(screen.queryByAltText('Actual enhanced benchmark render')).toBeNull();
});

it('inspects the actual selected capture with keyboard reveal and detail controls', async () => {
  const first = capture('s1-classroom', 'mps'), second = capture('s2-gallery', 'cpu');
  const { container } = render(BenchmarkStudio, { renders: [first, second] });
  expect(screen.getByAltText('Actual enhanced benchmark render').getAttribute('src')).toBe(second.output_data_url);
  await fireEvent.click(screen.getByRole('button', { name: 'Compute study mps' }));
  expect(screen.getByAltText('Actual enhanced benchmark render').getAttribute('src')).toBe(first.output_data_url);
  await fireEvent.input(screen.getByRole('slider', { name: 'Reveal enhanced benchmark render' }), { target: { value: '75' } });
  expect((container.querySelector('.render-stage') as HTMLElement).style.getPropertyValue('--split')).toBe('75%');
  await fireEvent.click(screen.getByRole('button', { name: 'Inspect detail' }));
  expect(screen.getByRole('button', { name: 'Fit render' }).getAttribute('aria-pressed')).toBe('true');
  expect(container.querySelector('.render-stage.detail')).toBeTruthy();
});
