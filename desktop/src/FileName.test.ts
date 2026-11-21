// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, expect, it } from 'vitest';

import FileName from './FileName.svelte';

afterEach(cleanup);

it('shows the name in two parts, with the full name as its tooltip and for screen readers', () => {
  const name = 'IMG_490C1E18-B4D1-47E5-AC62-FD97F5A26811.jpeg';
  const { container } = render(FileName, { name });
  const root = container.querySelector('.file-name')!;
  expect(root.getAttribute('title')).toBe(name);
  expect(root.getAttribute('dir')).toBe('auto');
  const head = root.querySelector('.file-name-head')!;
  const tail = root.querySelector('.file-name-tail')!;
  expect(head.textContent).toBe('IMG_490C1E18-B4D1-47E5-AC62-FD97F5A');
  expect(tail.textContent).toBe('26811.jpeg');
  expect(head.getAttribute('aria-hidden')).toBe('true');
  expect(tail.getAttribute('aria-hidden')).toBe('true');
  expect(screen.getByText(name).classList.contains('file-name-text')).toBe(true);
});

it('renders a short name in one piece', () => {
  const { container } = render(FileName, { name: 'x.png' });
  expect(container.querySelector('.file-name')?.textContent).toBe('x.png');
  expect(container.querySelector('.file-name-tail')).toBeNull();
  expect(container.querySelector('[aria-hidden]')).toBeNull();
});
