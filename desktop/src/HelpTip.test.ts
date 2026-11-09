// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';

import AdvancedSettings from './AdvancedSettings.svelte';
import HelpTip from './HelpTip.svelte';
import { demoSnapshot } from './lib/demo';

afterEach(cleanup);

describe('setting hints', () => {
  it('show their text on hover, focus and click and hide it on Escape', async () => {
    render(HelpTip, { label: 'Tile', text: 'How large a piece is processed at once.' });
    const button = screen.getByRole('button', { name: 'About Tile' });
    const hint = screen.getByText('How large a piece is processed at once.');
    expect(button.getAttribute('aria-describedby')).toBe(hint.id);
    expect(hint.hidden).toBe(true);
    await fireEvent.mouseEnter(button);
    expect(hint.hidden).toBe(false);
    await fireEvent.mouseLeave(button);
    expect(hint.hidden).toBe(true);
    await fireEvent.focus(button);
    expect(hint.hidden).toBe(false);
    await fireEvent.keyDown(button, { key: 'Escape' });
    expect(hint.hidden).toBe(true);
    await fireEvent.click(button);
    expect(hint.hidden).toBe(false);
  });

  it('sit next to Tile, Halo and Precision, whose labels still name their fields', async () => {
    const snapshot = demoSnapshot();
    snapshot.settings.task = 'upscale';
    render(AdvancedSettings, {
      settings: snapshot.settings,
      selectedModel: undefined,
      capabilities: snapshot.capabilities,
      usingTemporalVideo: false,
      updateSettings: () => undefined,
      chooseOutput: async () => undefined,
      refreshCapabilities: async () => undefined,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    for (const name of ['Tile', 'Halo', 'Precision']) {
      expect(screen.getByLabelText(name).tagName).toBe('SELECT');
      expect(screen.getByRole('button', { name: `About ${name}` })).toBeTruthy();
    }
  });
});
