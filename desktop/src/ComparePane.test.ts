// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import ComparePane from './ComparePane.svelte';
import type { CompareItem } from './lib/compareOutputs';

afterEach(cleanup);
const item = (id: string, media: string, status: CompareItem['status'] = 'ready'): CompareItem => ({
  key: id,
  pick: { kind: 'output', media_id: media, job_id: id },
  media_name: media,
  label: id,
  meta: '',
  preview_data_url: status === 'ready' ? `data:image/png;base64,${id}` : '',
  width: 100,
  height: 100,
  status,
  error: status === 'error' ? 'Decode failed' : '',
});
const props = {
  layout: 'slider' as const,
  setLayout: vi.fn(),
  swapItems: vi.fn(),
  removeItem: vi.fn(),
  close: vi.fn(),
  asTab: true,
};
it('shows the slider in slot order and provides swap and move controls', async () => {
  render(ComparePane, {
    ...props,
    items: [item('one', 'a'), item('two', 'a'), item('three', 'a')],
  });
  expect(screen.getByRole('slider', { name: /Divider between slot 1 and slot 2/ })).toBeTruthy();
  await fireEvent.click(screen.getByRole('button', { name: 'Swap slots 1 and 2' }));
  await fireEvent.click(screen.getByRole('button', { name: 'Move three to slider slot 2' }));
  expect(props.swapItems).toHaveBeenCalledWith(0, 1);
  expect(props.swapItems).toHaveBeenCalledWith(1, 2);
});
it('uses boxes across inputs and displays each slot state', () => {
  render(ComparePane, {
    ...props,
    items: [item('one', 'a', 'loading'), item('two', 'b', 'missing'), item('three', 'a', 'error')],
  });
  expect(screen.queryByRole('slider')).toBeNull();
  expect(screen.getByText('Loading…')).toBeTruthy();
  expect(screen.getByText('File missing')).toBeTruthy();
  expect(screen.getByText('Decode failed')).toBeTruthy();
});
