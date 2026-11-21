// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EmptyStage from './EmptyStage.svelte';
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
describe('EmptyStage', () => {
  it('does not spin forever when the stage is hidden (clientWidth 0)', () => {
    let lineTos = 0;
    const context = {
      createLinearGradient: () => ({ addColorStop: vi.fn() }),
      fillRect: vi.fn(),
      beginPath: vi.fn(),
      arc: vi.fn(),
      fill: vi.fn(),
      moveTo: vi.fn(),
      lineTo: () => {
        lineTos += 1;
        if (lineTos > 100_000) throw new Error('paintScene loops forever');
      },
      drawImage: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      rect: vi.fn(),
      clip: vi.fn(),
      clearRect: vi.fn(),
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context);
    expect(() => render(EmptyStage, { addFiles: () => undefined })).not.toThrow();
  });
});
