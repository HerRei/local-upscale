import { recordFrontendError } from './api';

/** Capture only the first unhandled frontend error in this launch. */
export function captureFrontendErrors(target: Window = window): () => void {
  let captured = false;
  const capture = (reason: unknown, fallback = '') => {
    if (captured) return;
    captured = true;
    const message = reason instanceof Error ? reason.message : String(reason ?? fallback);
    const stack = reason instanceof Error ? (reason.stack ?? '') : '';
    void recordFrontendError(message, stack).catch(() => {});
  };
  const onError = (event: ErrorEvent) => capture(event.error, event.message);
  const onRejection = (event: PromiseRejectionEvent) => capture(event.reason);
  target.addEventListener('error', onError);
  target.addEventListener('unhandledrejection', onRejection);
  return () => {
    target.removeEventListener('error', onError);
    target.removeEventListener('unhandledrejection', onRejection);
  };
}
