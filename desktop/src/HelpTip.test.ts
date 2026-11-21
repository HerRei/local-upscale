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

  it('stay usable inside a disabled fieldset, as while a job runs, and open with Enter or Space', async () => {
    const fieldset = document.body.appendChild(document.createElement('fieldset'));
    fieldset.disabled = true;
    render(HelpTip, {
      props: { label: 'Tile', text: 'How large a piece is processed at once.' },
      target: fieldset,
    });
    const tip = screen.getByRole('button', { name: 'About Tile' });
    const hint = screen.getByText('How large a piece is processed at once.');
    expect(tip.matches(':disabled')).toBe(false);
    expect(tip.tabIndex).toBe(0);
    await fireEvent.keyDown(tip, { key: 'Enter' });
    expect(hint.hidden).toBe(false);
    await fireEvent.keyDown(tip, { key: 'Escape' });
    expect(hint.hidden).toBe(true);
    await fireEvent.keyDown(tip, { key: ' ' });
    expect(hint.hidden).toBe(false);
    fieldset.remove();
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
      refreshCapabilities: async () => true,
    });
    await fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    expect(screen.getByRole('button', { name: 'About Hardware' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'About Safe memory mode' })).toBeTruthy();
    for (const name of ['Tile', 'Halo', 'Precision']) {
      expect(screen.getByLabelText(name).tagName).toBe('SELECT');
      expect(screen.getByRole('button', { name: `About ${name}` })).toBeTruthy();
    }
  });
});
