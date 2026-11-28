// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EmptyStage from './EmptyStage.svelte';

function mockCanvasContext(): CanvasRenderingContext2D {
  return {
    createLinearGradient: () => ({ addColorStop: vi.fn() }),
    fillRect: vi.fn(),
    beginPath: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    drawImage: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    rect: vi.fn(),
    clip: vi.fn(),
    clearRect: vi.fn(),
  } as unknown as CanvasRenderingContext2D;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
});
describe('EmptyStage', () => {
  it('does not spin forever when the stage is hidden (clientWidth 0)', () => {
    let lineTos = 0;
    const context = {
      ...mockCanvasContext(),
      lineTo: () => {
        lineTos += 1;
        if (lineTos > 100_000) throw new Error('paintScene loops forever');
      },
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context);
    expect(() => render(EmptyStage, { addFiles: () => undefined })).not.toThrow();
  });

  it('pauses the animation loop when hidden and resumes when visible', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(mockCanvasContext());
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(240);
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(160);
    vi.stubGlobal('matchMedia', () => ({ matches: false }) as MediaQueryList);
    let nextId = 1;
    const callbacks = new Map<number, FrameRequestCallback>();
    const raf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      const id = nextId++;
      callbacks.set(id, cb);
      return id;
    });
    const caf = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
      callbacks.delete(id);
    });

    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    const view = render(EmptyStage, { addFiles: () => undefined });
    expect(raf).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(caf).toHaveBeenCalledWith(1);
    expect(callbacks.size).toBe(0);

    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(raf).toHaveBeenCalledTimes(2);
    const cb = callbacks.get(2)!;
    cb(16);
    expect(raf).toHaveBeenCalledTimes(3);

    view.unmount();
    expect(caf).toHaveBeenCalledWith(3);
  });

  it('does not animate under prefers-reduced-motion even across visibility changes', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(mockCanvasContext());
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(240);
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(160);
    vi.stubGlobal('matchMedia', () => ({ matches: true }) as MediaQueryList);
    const raf = vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(1);

    render(EmptyStage, { addFiles: () => undefined });
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    document.dispatchEvent(new Event('visibilitychange'));
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(raf).not.toHaveBeenCalled();
  });

  it('offers the workflows as a link beside the well, not inside it', async () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    const openWorkflows = vi.fn();
    const view = render(EmptyStage, { addFiles: () => undefined, openWorkflows });
    const link = view.getByRole('button', { name: 'or start from a workflow…' });
    expect(link.closest('button.well')).toBeNull();
    link.click();
    expect(openWorkflows).toHaveBeenCalledTimes(1);
  });
});
