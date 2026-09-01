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

window.addEventListener('error', (event) => renderStartupFailure(event.error ?? event.message));
window.addEventListener('unhandledrejection', (event) => renderStartupFailure(event.reason));

if (!target) {
  throw new Error('LocalSR frontend mount point is missing');
}

try {
  target.replaceChildren();
  mount(App, { target });
} catch (error) {
  renderStartupFailure(error);
}
