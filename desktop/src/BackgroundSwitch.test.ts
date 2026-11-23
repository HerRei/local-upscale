// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import BackgroundSwitch from './BackgroundSwitch.svelte';

afterEach(cleanup);

describe('BackgroundSwitch', () => {
  it('labels the current background and steps to the next on click', async () => {
    const set = vi.fn();
    const { container } = render(BackgroundSwitch, { value: 'grey', set });

    const button = screen.getByRole('button', { name: 'Viewer background: Grey' });
    expect(button.getAttribute('title')).toBe('Background: Grey · click for White');
    expect(container.querySelector('.bg-swatch')?.getAttribute('data-bg')).toBe('grey');

    await fireEvent.click(button);
    expect(set).toHaveBeenCalledTimes(1);
    expect(set).toHaveBeenCalledWith('white');
  });
});
