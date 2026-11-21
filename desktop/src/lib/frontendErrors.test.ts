// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';

const recordFrontendError = vi.hoisted(() => vi.fn(async () => undefined));
vi.mock('./api', () => ({ recordFrontendError }));

import { captureFrontendErrors } from './frontendErrors';

describe('captureFrontendErrors', () => {
  let stop: (() => void) | undefined;

  afterEach(() => {
    stop?.();
    stop = undefined;
    recordFrontendError.mockClear();
  });

  it('captures the first unhandled error or rejection and ignores later ones', () => {
    stop = captureFrontendErrors(window);
    const first = new Error('Boom at /Users/alice/app.js');
    window.dispatchEvent(new ErrorEvent('error', { error: first, message: first.message }));
    window.dispatchEvent(new ErrorEvent('error', { error: new Error('Second') }));
    const rejection = new Event('unhandledrejection') as PromiseRejectionEvent;
    Object.defineProperty(rejection, 'reason', { value: new Error('Late rejection') });
    window.dispatchEvent(rejection);

    expect(recordFrontendError).toHaveBeenCalledTimes(1);
    expect(recordFrontendError).toHaveBeenCalledWith(
      'Boom at /Users/alice/app.js',
      expect.stringContaining('Boom at /Users/alice/app.js'),
    );
  });

  it('captures an unhandled promise rejection when it happens first and stops on dispose', () => {
    stop = captureFrontendErrors(window);
    const rejection = new Event('unhandledrejection') as PromiseRejectionEvent;
    Object.defineProperty(rejection, 'reason', { value: 'String rejection' });
    window.dispatchEvent(rejection);

    expect(recordFrontendError).toHaveBeenCalledTimes(1);
    expect(recordFrontendError).toHaveBeenCalledWith('String rejection', '');

    stop();
    stop = undefined;
    window.dispatchEvent(new ErrorEvent('error', { message: 'After dispose' }));
    expect(recordFrontendError).toHaveBeenCalledTimes(1);
  });
});
