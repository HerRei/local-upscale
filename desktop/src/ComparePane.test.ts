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

it('debounces region requests while zooming and clears them on leaving Compare', async () => {
  vi.useFakeTimers();
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width: 300,
    height: 300,
    left: 0,
    top: 0,
    right: 300,
    bottom: 300,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  const requestRegions = vi.fn().mockResolvedValue(undefined);
  const clearRegions = vi.fn().mockResolvedValue(undefined);
  const entries = [item('one', 'a'), item('two', 'a')].map((entry) => ({
    ...entry,
    width: 2000,
    height: 2000,
    preview_width: 300,
    preview_height: 300,
  }));
  try {
    const view = render(ComparePane, {
      ...props,
      items: entries,
      generation: 1,
      requestRegions,
      clearRegions,
    });
    await Promise.resolve();
    const canvas = screen.getByRole('region', { name: 'Compare canvas' });
    await fireEvent.wheel(canvas, { deltaY: -1, clientX: 150, clientY: 150 });
    await fireEvent.wheel(canvas, { deltaY: -1, clientX: 150, clientY: 150 });
    await fireEvent.wheel(canvas, { deltaY: -1, clientX: 150, clientY: 150 });
    expect(requestRegions).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(160);
    expect(requestRegions).toHaveBeenCalledTimes(1);
    const bounds = requestRegions.mock.calls[0][1][0];
    expect(bounds).toMatchObject({ target_width: expect.any(Number) });
    await view.rerender({
      items: [
        { ...entries[0], region: { bounds, data_url: 'data:image/png;base64,crop' } },
        entries[1],
      ],
    });
    expect(canvas.querySelector('.region-image')?.getAttribute('src')).toBe(
      'data:image/png;base64,crop',
    );
    await fireEvent.wheel(canvas, { deltaY: -1, clientX: 150, clientY: 150 });
    expect(canvas.querySelector('.region-image')).toBeNull();
    view.unmount();
    await Promise.resolve();
    await Promise.resolve();
    expect(clearRegions).toHaveBeenCalledTimes(1);
  } finally {
    rect.mockRestore();
    vi.useRealTimers();
  }
});
