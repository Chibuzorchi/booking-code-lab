/* Executes portfolio/copy.js against a minimal DOM stub and reports what the
 * shared helper actually did, so the Python suite can pin the clipboard ->
 * execCommand -> select-the-text escalation instead of asserting on source text.
 *
 * Usage: node tests/fixtures/copy_probe.js   (prints one JSON object)
 */
'use strict';
const fs = require('fs');
const path = require('path');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', '..', 'portfolio', 'copy.js'), 'utf8');

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.attributes = {};
    this.style = {cssText: ''};
    this.dataset = {};
    this.listeners = {};
    this.textContent = '';
    this.className = '';
    this.value = '';
    this.readOnly = false;
    this.selected = false;
    this.selectionRange = null;
    this.focused = false;
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach(n => classes.add(n)),
      remove: (...names) => names.forEach(n => classes.delete(n)),
      contains: name => classes.has(name),
      values: () => [...classes],
    };
  }
  get isConnected() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node === document.body;
  }
  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  append(...nodes) { nodes.forEach(n => this.appendChild(n)); }
  after(node) {
    const siblings = this.parentNode.childNodes;
    node.parentNode = this.parentNode;
    siblings.splice(siblings.indexOf(this) + 1, 0, node);
  }
  remove() {
    if (!this.parentNode) return;
    const siblings = this.parentNode.childNodes;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentNode = null;
  }
  focus() { this.focused = true; document.activeElement = this; }
  select() { this.selected = true; }
  setSelectionRange(start, end) { this.selectionRange = [start, end]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); }
}

let document = null;

/* One isolated helper instance per scenario: copy.js gets its whole world as
 * arguments, so nothing leaks between cases. */
function load({clipboard, execCommand}) {
  const timers = [];
  document = {
    body: new El('body'),
    activeElement: null,
    createElement: tag => new El(tag),
  };
  if (execCommand !== null) document.execCommand = () => execCommand;
  const navigator = {platform: 'MacIntel', userAgent: 'probe', clipboard};
  const factory = new Function('navigator', 'document', 'setTimeout', 'clearTimeout',
    `${SOURCE}\nreturn Copy;`);
  const Copy = factory(navigator, document,
    (fn, ms) => { timers.push({fn, ms}); return timers.length; },
    id => { if (id) timers[id - 1] = null; });
  const button = new El('button');
  button.textContent = 'Copy';
  document.body.appendChild(button);
  return {Copy, button, timers, runTimers: () => timers.forEach(t => t && t.fn())};
}

function describe(world, returned) {
  const {button} = world;
  const field = button.parentNode.childNodes[button.parentNode.childNodes.indexOf(button) + 1] || null;
  return {
    returned,
    label: button.textContent,
    classes: button.classList.values(),
    body_children: document.body.childNodes.map(el => el.tagName),
    field: field && field.className === 'copy-fallback' ? {
      tag: field.tagName,
      value: field.value,
      read_only: field.readOnly,
      focused: field.focused,
      selected: field.selected,
      aria_label: field.getAttribute('aria-label'),
      stops: Object.keys(field.listeners).sort(),
      is_after_button: true,
    } : null,
  };
}

const CODE = '5SH7HBJ';

async function main() {
  const report = {};

  // 1. the modern path: navigator.clipboard accepts the text
  {
    const writes = [];
    const world = load({clipboard: {writeText: async text => { writes.push(text); }}, execCommand: null});
    report.clipboard = describe(world, await world.Copy.button(world.button, CODE));
    report.clipboard.wrote = writes;
    world.runTimers();
    report.clipboard.label_after_flash = world.button.textContent;
    report.clipboard.classes_after_flash = world.button.classList.values();
  }

  // 2. clipboard denied (no secure context), execCommand still allowed
  {
    const world = load({clipboard: {writeText: async () => { throw new Error('denied'); }}, execCommand: true});
    report.exec_command = describe(world, await world.Copy.button(world.button, CODE));
  }

  // 3. both refuse: the select-the-text fallback is the only way left
  {
    const world = load({clipboard: null, execCommand: false});
    report.fallback = describe(world, await world.Copy.button(world.button, CODE, {label: 'Booking code to copy'}));
    report.fallback.shortcut_named = world.button.textContent.includes(world.Copy.shortcut());
    // a second failure must reuse the one field, not stack copies of the code on the page
    await world.Copy.button(world.button, CODE);
    report.fallback.fields_after_second_click =
      document.body.childNodes.filter(el => el.className === 'copy-fallback').length;
  }

  // 4. nothing to copy: no field, and no false 'Copied'
  {
    const world = load({clipboard: null, execCommand: false});
    report.empty = describe(world, await world.Copy.button(world.button, ''));
  }

  // 5. Copy.text reports the clipboard truthfully, without touching the page
  {
    const world = load({clipboard: null, execCommand: false});
    report.text_only = {returned: await world.Copy.text(CODE),
                        body_children: document.body.childNodes.map(el => el.tagName)};
  }

  console.log(JSON.stringify(report, null, 2));
}

main().catch(error => { console.error(error); process.exit(1); });
