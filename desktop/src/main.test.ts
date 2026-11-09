// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.resetModules();
  vi.doUnmock('svelte');
  document.body.innerHTML = '';
});

async function start(mount: () => void): Promise<HTMLElement> {
  document.body.innerHTML = '<div id="app"></div>';
  vi.doMock('./App.svelte', () => ({ default: {} }));
  vi.doMock('svelte', async (original) => ({ ...(await original<object>()), mount }));
  await import('./main');
  return document.getElementById('app')!;
}

describe('startup failure screen', () => {
  it('appears when the interface cannot be mounted', async () => {
    const target = await start(() => {
      throw new Error('broken bundle');
    });
    expect(target.textContent).toContain('LocalSR could not open');
    expect(target.textContent).toContain('broken bundle');
  });

  it('leaves a running interface alone when a later error occurs', async () => {
    const target = await start(() => {
      document.getElementById('app')!.innerHTML = '<p>running</p>';
    });
    window.dispatchEvent(new ErrorEvent('error', { error: new Error('late failure') }));
    expect(target.textContent).toBe('running');
  });
});
