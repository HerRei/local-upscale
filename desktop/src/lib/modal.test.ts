// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';
import { modal } from './modal';

afterEach(() => {
  document.body.innerHTML = '';
});

function press(key: string, shiftKey = false): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true });
  (document.activeElement ?? document.body).dispatchEvent(event);
  return event;
}

function dialog(html: string, close?: () => void) {
  const node = document.createElement('div');
  node.tabIndex = -1;
  node.innerHTML = html;
  document.body.append(node);
  return { node, action: modal(node, close) };
}

describe('modal dialog action', () => {
  it('moves focus in, to the marked element or else the dialog, and back to the opener', () => {
    const opener = document.createElement('button');
    document.body.append(opener);
    opener.focus();
    const { node, action } = dialog('<button>One</button><button data-autofocus>Two</button>');
    expect(document.activeElement?.textContent).toBe('Two');
    action.destroy();
    expect(document.activeElement).toBe(opener);
    const plain = dialog('<button>One</button>');
    expect(document.activeElement).toBe(plain.node);
    plain.action.destroy();
    node.remove();
  });

  it('keeps Tab and Shift+Tab inside, wrapping at both ends and pulling stray focus in', () => {
    const outside = document.createElement('button');
    document.body.append(outside);
    const { node } = dialog(
      '<button>A</button><input /><button disabled>X</button><button>B</button>',
    );
    const [a, , b] = [...node.querySelectorAll<HTMLElement>('button:not([disabled]), input')];
    expect(press('Tab').defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(a);
    press('Tab');
    press('Tab');
    expect(document.activeElement).toBe(b);
    press('Tab');
    expect(document.activeElement).toBe(a);
    press('Tab', true);
    expect(document.activeElement).toBe(b);
    outside.focus();
    press('Tab');
    expect(document.activeElement).toBe(a);
  });

  it('calls close on Escape, for the newest of several dialogs only', () => {
    const lower = vi.fn();
    const upper = vi.fn();
    const first = dialog('<button>A</button>', lower);
    const second = dialog('<button>B</button>', upper);
    expect(press('Escape').defaultPrevented).toBe(true);
    expect(upper).toHaveBeenCalledTimes(1);
    expect(lower).not.toHaveBeenCalled();
    second.action.destroy();
    press('Escape');
    expect(lower).toHaveBeenCalledTimes(1);
    first.action.destroy();
    press('Escape');
    expect(lower).toHaveBeenCalledTimes(1);
  });

  it('follows a new close handler', () => {
    const before = vi.fn();
    const after = vi.fn();
    const { action } = dialog('<button>A</button>', before);
    action.update(after);
    press('Escape');
    expect(before).not.toHaveBeenCalled();
    expect(after).toHaveBeenCalledTimes(1);
  });
});
