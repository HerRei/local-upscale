/**
 * Keyboard behaviour of a modal dialog, as a Svelte action: `<div role="dialog" tabindex="-1"
 * use:modal={close}>`. Focus moves into the dialog when it opens (to the element marked
 * data-autofocus, else the dialog itself), Tab and Shift+Tab stay inside, Escape calls `close`,
 * and focus returns to where it was when the dialog goes away. Of several open dialogs only the
 * newest answers the keys.
 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const open: HTMLElement[] = [];

export function modal(node: HTMLElement, close?: () => void) {
  let onEscape = close;
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  open.push(node);
  (node.querySelector<HTMLElement>('[data-autofocus]') ?? node).focus();

  function onKeydown(event: KeyboardEvent): void {
    if (open[open.length - 1] !== node) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      onEscape?.();
      return;
    }
    if (event.key !== 'Tab') return;
    // WebKit's Tab skips buttons by default, which would let it leave the dialog: move by hand.
    const items = [...node.querySelectorAll<HTMLElement>(FOCUSABLE)];
    event.preventDefault();
    if (!items.length) return node.focus();
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = event.shiftKey ? (at <= 0 ? items.length - 1 : at - 1) : (at + 1) % items.length;
    items[next].focus();
  }
  document.addEventListener('keydown', onKeydown);

  return {
    update(next?: () => void) {
      onEscape = next;
    },
    destroy() {
      document.removeEventListener('keydown', onKeydown);
      open.splice(open.indexOf(node), 1);
      if (opener?.isConnected) opener.focus();
    },
  };
}
