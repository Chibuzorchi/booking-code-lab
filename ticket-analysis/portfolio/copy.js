/* The one clipboard helper every generated page uses.
 *
 * Inlined verbatim (never linked) by reporting.html, the distinct page and the
 * 3-stage page: two of those are standalone files opened straight from disk,
 * where a <script src="..."> would not resolve. portfolio/pages.py does the
 * inlining, so this file stays the only copy of the logic.
 *
 * Three escalating paths, so a booking code is always copyable:
 *   1. navigator.clipboard.writeText - blocked outside a secure context
 *   2. document.execCommand('copy')  - the older path, still allowed on http://localhost
 *   3. select-the-text fallback      - put the text on the page, focused and
 *      selected, and name the platform shortcut; the user finishes the copy.
 * Path 3 is what makes a failed copy recoverable rather than a dead button.
 */
const Copy = (() => {
  'use strict';
  const FLASH_MS = 1400, PROMPT_MS = 6000;

  function shortcut() {
    const ua = `${navigator.platform || ''} ${navigator.userAgent || ''}`;
    return /Mac|iPhone|iPad|iPod/.test(ua) ? '⌘-C' : 'Ctrl-C';
  }

  /* Swap a button's label for a moment without ever losing the original. */
  function flash(button, message, ok, ms) {
    if (!button) return;
    if (!('copyLabel' in button.dataset)) button.dataset.copyLabel = button.textContent;
    clearTimeout(button.copyTimer);
    button.textContent = message;
    button.classList.remove('is-copied', 'is-copy-failed');
    button.classList.add(ok ? 'is-copied' : 'is-copy-failed');
    button.copyTimer = setTimeout(() => {
      button.textContent = button.dataset.copyLabel;
      button.classList.remove('is-copied', 'is-copy-failed');
    }, ms || FLASH_MS);
  }

  async function viaClipboard(text) {
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) return false;
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      return false;   // insecure context, denied permission, or no user gesture
    }
  }

  /* Off-screen host, so the page the user is reading never jumps or flickers. */
  function viaExecCommand(text) {
    if (!document.execCommand) return false;
    const host = document.createElement('textarea');
    host.value = text;
    host.readOnly = true;
    host.setAttribute('aria-hidden', 'true');
    host.style.cssText = 'position:fixed;top:0;left:-9999px;opacity:0';
    const previous = document.activeElement;
    document.body.appendChild(host);
    let ok = false;
    try {
      host.focus();
      host.select();
      if (host.setSelectionRange) host.setSelectionRange(0, text.length);
      ok = !!document.execCommand('copy');
    } catch (e) {
      ok = false;
    } finally {
      host.remove();
      if (previous && previous.focus) previous.focus();
    }
    return ok;
  }

  /* Path 3: one reusable field per button, holding the exact text, selected. */
  function offer(button, text, label) {
    let field = button && button.copyFallback;
    if (!field || !field.isConnected) {
      const lines = text.split('\n');
      field = document.createElement(lines.length > 1 ? 'textarea' : 'input');
      field.className = 'copy-fallback';
      field.readOnly = true;
      field.setAttribute('aria-label', label || 'Text to copy');
      if (lines.length > 1) field.rows = Math.min(8, lines.length);
      // The field can land inside a <summary>: keep its own clicks and keys from
      // reaching it, or selecting the text would toggle the card open.
      field.addEventListener('click', ev => ev.stopPropagation());
      field.addEventListener('keydown', ev => {
        if (ev.key === 'Enter' || ev.key === ' ') ev.stopPropagation();
      });
      if (button && button.after) button.after(field);
      else document.body.appendChild(field);
      if (button) button.copyFallback = field;
    }
    field.value = text;
    field.focus();
    field.select();
    return field;
  }

  /* Clipboard only: true when the text is on the clipboard, false otherwise. */
  async function text(value) {
    if (typeof value !== 'string' || !value) return false;
    return (await viaClipboard(value)) || viaExecCommand(value);
  }

  /* The button entry point: copies, or falls back to selectable text. */
  async function button(el, value, options) {
    const opts = options || {};
    if (typeof value !== 'string' || !value) {
      flash(el, opts.empty || 'Nothing to copy', false);
      return false;
    }
    if (await text(value)) {
      flash(el, opts.copied || 'Copied', true);
      return true;
    }
    offer(el, value, opts.label);
    flash(el, `Press ${shortcut()}`, false, PROMPT_MS);
    return false;
  }

  return { text, button, flash, offer, shortcut };
})();
