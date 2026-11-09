import { mount } from 'svelte';
import App from './App.svelte';
import './styles.css';

const target = document.getElementById('app');

function renderStartupFailure(reason: unknown): void {
  if (!target) return;
  const detail = reason instanceof Error ? `${reason.name}: ${reason.message}` : String(reason);
  target.replaceChildren();
  const card = document.createElement('main');
  card.className = 'startup-failure';
  const title = document.createElement('h1');
  title.textContent = 'LocalSR could not open';
  const message = document.createElement('p');
  message.textContent =
    'The interface failed before connecting to the local inference engine. No media was changed.';
  const diagnostic = document.createElement('pre');
  diagnostic.textContent = detail;
  card.append(title, message, diagnostic);
  target.append(card);
}

const onError = (event: ErrorEvent) => renderStartupFailure(event.error ?? event.message);
const onRejection = (event: PromiseRejectionEvent) => renderStartupFailure(event.reason);
window.addEventListener('error', onError);
window.addEventListener('unhandledrejection', onRejection);

if (!target) {
  throw new Error('LocalSR frontend mount point is missing');
}

try {
  target.replaceChildren();
  mount(App, { target });
  // From here the app reports its own failures in a dialog. A later stray error must not
  // replace a working interface, and possibly a running job's progress, with this screen.
  window.removeEventListener('error', onError);
  window.removeEventListener('unhandledrejection', onRejection);
} catch (error) {
  renderStartupFailure(error);
}
