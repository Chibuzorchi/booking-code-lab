/* Drive the /app console script through the state transitions that a syntax or
 * source check cannot see: a reply arriving for a run or provider the user has
 * already left, a provider switch, cancelled_unresolved settling, and a new run
 * that arrives already succeeded.
 *
 * The script is executed as written (extracted from portfolio/app.html) against a
 * DOM stub, a fetch whose replies this file resolves by hand, and fake timers, so
 * every observation below is the real code's behaviour. The clipboard paths are not
 * stubbed here: copy_probe.js covers those against the helper itself.
 *
 * Prints one JSON object: scenario name -> what the page ended up showing.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const PAGE = path.join(__dirname, '..', '..', 'portfolio', 'app.html');
const blocks = fs.readFileSync(PAGE, 'utf8').match(/<script>\n([\s\S]*?)<\/script>/g);
const SOURCE = blocks[blocks.length - 1].replace(/^<script>\n/, '').replace(/<\/script>$/, '');

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.textContent = '';
    this.className = '';
    this.value = '';
    this.hidden = false;
    this.disabled = false;
  }
  get firstChild() { return this.childNodes[0] || null; }
  append(...kids) { for (const kid of kids) this.childNodes.push(kid); }
  appendChild(kid) { this.childNodes.push(kid); return kid; }
  replaceChildren(...kids) { this.childNodes = kids; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  fire(type) {
    const event = {preventDefault() {}, stopPropagation() {}, type};
    for (const fn of this.listeners[type] || []) fn(event);
  }
}

/* A fresh world per scenario: its own elements, timers and pending requests. */
function boot(values) {
  const nodes = new Map();
  const world = {pending: [], timers: new Map()};
  const el = id => {
    if (!nodes.has(id)) {
      const made = new El('div');
      made.id = id;
      if (values && id in values) made.value = values[id];
      nodes.set(id, made);
    }
    return nodes.get(id);
  };
  world.el = el;
  const document = {
    getElementById: el,
    createElement: tag => new El(tag),
    body: new El('body'),
  };
  let nextTimer = 1;
  const fetch = (url, init) => new Promise((resolve, reject) => world.pending.push({
    url, method: init.method, body: init.body ? JSON.parse(init.body) : null, resolve, reject,
  }));
  const setIntervalStub = fn => { const id = nextTimer++; world.timers.set(id, fn); return id; };
  const clearIntervalStub = id => { world.timers.delete(id); };
  new Function('document', 'window', 'fetch', 'setInterval', 'clearInterval', 'setTimeout', 'Copy', SOURCE)(
    document, {addEventListener() {}}, fetch, setIntervalStub, clearIntervalStub, fn => fn(),
    {button() { return true; }, text() { return true; }, flash() {}, offer() {}, shortcut: () => 'Ctrl-C'});
  return world;
}

const settle = async () => { for (let i = 0; i < 4; i += 1) await new Promise(r => setImmediate(r)); };

function take(world, fragment) {
  const at = world.pending.findIndex(call => call.url.includes(fragment));
  if (at < 0) throw new Error(`no request for ${fragment}; pending: ${world.pending.map(c => c.url)}`);
  return world.pending.splice(at, 1)[0];
}
const answer = (call, data, status = 200) =>
  call.resolve({ok: status < 400, status, text: async () => JSON.stringify(data)});
const counting = () => ({tried: 9, found_ok: 4, qualifying: 2, excluded_simulations: 1, unpriced: 0});
const runStatus = (run_id, state, provider = 'bet9ja') =>
  ({run_id, provider, state, counters: counting(), request: {seed: 'SEED123'}});

const distinctReport = run_id => ({
  run_id, input_count: 3, pool_size: 2, distinct_codes: 1, overlapping_codes: 1, shared_legs: 1,
  excluded: [], odds_basis: 'parsed_leg_product', odds_label: 'recorded, not verified payout',
  min_odds: 1, max_odds: null, top_legs: [{leg: 'A v B :: 1X2 = Home', in_codes: 2}],
  distinct: [{id: 'bet9ja:AAA1111', raw: {code: 'AAA1111'}, num_legs: 2,
              odds: {parsed_leg_product: 12.5},
              legs: [{event: 'A v B', league: 'L', market: '1X2', pick: 'Home', odds: 2.5, kickoff: 'k'}]}],
});
const selectionReport = run_id => ({
  run_id, pool_size: 2, odds_basis: 'parsed_leg_product', odds_label: 'recorded, not verified payout',
  min_odds: 1, max_odds: null,
  selection: {selected_codes: ['AAA1111'], selected_count: 1, rejected_count: 0, max_exposure: 1,
              realized_max_exposure: 1, target: null,
              odds_range: {min: 12.5, max: 12.5, median: 12.5, priced_codes: 1, unpriced_codes: 0},
              uniqueness_buckets: [{tier: 'fully distinct', code_count: 1}],
              selected: [{code: 'AAA1111', num_legs: 2, parsed_leg_product: 12.5,
                          shared_leg_count: 0, shared_legs: []}],
              rejected: []},
});

/* The page asks for its run history as soon as it loads. */
async function start(values) {
  const world = boot(values || {provider: 'bet9ja', seed: 'SEED123', max_exposure: '1', target: ''});
  await settle();
  answer(take(world, '/api/runs'), {runs: []});
  await settle();
  return world;
}

async function pick(world, run_id, state, provider) {
  world.el('runs-refresh').fire('click');
  await settle();
  const latest = world.pending.map(c => c.url).lastIndexOf('/api/runs?provider=' + world.el('provider').value);
  answer(world.pending.splice(latest, 1)[0], {runs: [runStatus(run_id, state, provider)]});
  await settle();
  world.el('runs').value = run_id;
  press(world, 'runs-load');
  await settle();
  answer(take(world, `/api/scan/${run_id}`), runStatus(run_id, state, provider));
  await settle();
}

const OWNER = {'s1-run': 's1-fields', 's1-cancel': 's1-fields', 's2-run': 's2-fields',
               's3-run': 's3-fields', 'runs-load': 's1-fields'};
function press(world, id) {
  const owner = OWNER[id];
  if (owner && world.el(owner).disabled) throw new Error(`${id} sits in the disabled ${owner}`);
  if (world.el(id).disabled) throw new Error(`${id} is disabled`);
  world.el(id).fire('click');
}

const look = world => ({
  state_line: world.el('s1-state').textContent,
  run_disabled: world.el('s1-run').disabled,
  cancel_disabled: world.el('s1-cancel').disabled,
  s2_locked: world.el('s2-fields').disabled,
  s3_locked: world.el('s3-fields').disabled,
  s2_cards: world.el('s2-cards').childNodes.length,
  s3_cards: world.el('s3-cards').childNodes.length,
  s3_facts_hidden: world.el('s3-facts').hidden,
  polling: world.timers.size > 0,
});

const scenarios = {
  /* Finding 1: a stage-2 reply for run A must not become run B's state. */
  async stale_stage2() {
    const world = await start();
    await pick(world, 'RUN_A', 'succeeded');
    press(world, 's2-run');
    await settle();
    const distinct = take(world, '/api/distinct');
    await pick(world, 'RUN_B', 'succeeded');          // user moves on while it is in flight
    answer(distinct, distinctReport('RUN_A'));
    await settle();
    return look(world);
  },

  /* Finding 1: a delayed poll for A must not replace the run now on screen. */
  async stale_poll() {
    const world = await start();
    await pick(world, 'RUN_A', 'running');
    const poll = take(world, '/api/scan/RUN_A');
    await pick(world, 'RUN_B', 'succeeded');
    answer(poll, runStatus('RUN_A', 'running'));
    await settle();
    return look(world);
  },

  /* Finding 1: the same for stage 3. */
  async stale_stage3() {
    const world = await start();
    await pick(world, 'RUN_A', 'succeeded');
    press(world, 's2-run');
    await settle();
    answer(take(world, '/api/distinct'), distinctReport('RUN_A'));
    await settle();
    press(world, 's3-run');
    await settle();
    const selection = take(world, '/api/decorrelate');
    await pick(world, 'RUN_B', 'succeeded');
    answer(selection, selectionReport('RUN_A'));
    await settle();
    return look(world);
  },

  /* Finding 1: a cancellation reply for A must not resurrect A either. */
  async stale_cancel() {
    const world = await start();
    await pick(world, 'RUN_A', 'running');
    press(world, 's1-cancel');
    await settle();
    const cancelling = take(world, '/cancel');
    await pick(world, 'RUN_B', 'succeeded');
    answer(cancelling, runStatus('RUN_A', 'cancelled'));
    await settle();
    return look(world);
  },

  /* Finding 1: one poll at a time, however slow the reply. */
  async no_overlapping_polls() {
    const world = await start();
    await pick(world, 'RUN_A', 'running');
    for (const fire of [...world.timers.values()]) { fire(); fire(); }
    await settle();
    return {in_flight: world.pending.filter(c => c.url.includes('/api/scan/RUN_A')).length};
  },

  /* Finding 2: switching provider clears the other provider's run and analysis,
     and switching back restores them (including its poller). */
  async provider_switch() {
    const world = await start();
    await pick(world, 'RUN_A', 'succeeded');
    press(world, 's2-run');
    await settle();
    answer(take(world, '/api/distinct'), distinctReport('RUN_A'));
    await settle();
    press(world, 's3-run');
    await settle();
    answer(take(world, '/api/decorrelate'), selectionReport('RUN_A'));
    await settle();
    const before = look(world);
    world.el('provider').value = 'sportybet';
    world.el('provider').fire('change');
    await settle();
    const away = look(world);
    const history = take(world, '/api/runs');       // the switch asks for the new provider's runs
    answer(history, {runs: []});
    await settle();
    world.el('provider').value = 'bet9ja';
    world.el('provider').fire('change');
    await settle();
    return {before, away, history_provider: history.url, back: look(world)};
  },

  /* Finding 2: a run still owned for bet9ja must not block launching on sportybet. */
  async provider_switch_running() {
    const world = await start();
    await pick(world, 'RUN_A', 'running');
    const held = look(world);
    world.el('provider').value = 'sportybet';
    world.el('provider').fire('change');
    await settle();
    return {held, away: look(world)};
  },

  /* Finding 3: cancelled_unresolved is not an end state. */
  async unresolved_cancel() {
    const world = await start();
    await pick(world, 'RUN_A', 'running');
    answer(take(world, '/api/scan/RUN_A'), runStatus('RUN_A', 'cancelled_unresolved'));
    await settle();
    const unresolved = look(world);
    for (const fire of [...world.timers.values()]) fire();
    await settle();
    // Report a page that stopped polling here instead of throwing: the point of the
    // scenario is whether it kept asking, so that has to be an observation.
    const next = world.pending.find(call => call.url.includes('/api/scan/RUN_A'));
    if (next) {
      world.pending.splice(world.pending.indexOf(next), 1);
      answer(next, runStatus('RUN_A', 'cancelled'));
      await settle();
    }
    return {unresolved, kept_asking: Boolean(next), settled: look(world)};
  },

  /* Finding 4: a new run that arrives already succeeded inherits nothing. */
  async immediate_success() {
    const world = await start();
    await pick(world, 'RUN_A', 'succeeded');
    press(world, 's2-run');
    await settle();
    answer(take(world, '/api/distinct'), distinctReport('RUN_A'));
    await settle();
    const before = look(world);
    world.el('scan-form').fire('submit');
    await settle();
    answer(take(world, '/api/scan'), runStatus('RUN_B', 'succeeded'));
    await settle();
    return {before, after: look(world)};
  },
};

async function switchTo(w, provider) {
  w.el('provider').value = provider;
  w.el('provider').fire('change');
  await settle();
}

Object.assign(scenarios, {
  async history_switch_guard() {
    const w = await start();
    await pick(w, 'RUN_A', 'succeeded');
    await switchTo(w, 'sportybet');
    const locked = w.el('runs').disabled && w.el('runs-load').disabled;
    const cleared = w.el('runs').value === '' && !w.el('runs').childNodes.some(x => x.value === 'RUN_A');
    answer(take(w, '/api/runs'), {runs: [runStatus('RUN_B', 'succeeded', 'sportybet')]});
    await settle();
    w.el('runs').value = 'RUN_B';
    press(w, 'runs-load'); await settle();
    answer(take(w, '/api/scan/RUN_B'), runStatus('RUN_A', 'succeeded', 'bet9ja'));
    await settle();
    return {locked, cleared, rejected: w.el('s1-error').textContent.includes('another provider'), ...look(w)};
  },
  async analysis_request_ownership() {
    const result = {};
    for (const stage of ['s2', 's3']) {
      const w = await start();
      await pick(w, 'RUN_A', 'succeeded');
      if (stage === 's3') {
        press(w, 's2-run'); await settle();
        answer(take(w, '/api/distinct'), distinctReport('RUN_A')); await settle();
      }
      press(w, stage + '-run'); await settle();
      const route = stage === 's2' ? '/api/distinct' : '/api/decorrelate';
      const old = take(w, route);
      await switchTo(w, 'sportybet');
      answer(take(w, '/api/runs'), {runs: []}); await settle();
      await pick(w, 'RUN_B', 'succeeded', 'sportybet');
      if (stage === 's3') {
        press(w, 's2-run'); await settle();
        answer(take(w, '/api/distinct'), distinctReport('RUN_B')); await settle();
      }
      press(w, stage + '-run'); await settle();
      const current = take(w, route);
      answer(old, stage === 's2' ? distinctReport('RUN_A') : selectionReport('RUN_A'));
      await settle();
      const stillBusy = w.el(stage + '-run').disabled;
      // Even a synthetic event must not bypass the in-flight request lock.
      w.el(stage + '-run').fire('click'); await settle();
      const duplicates = w.pending.filter(c => c.url === route).length;
      answer(current, stage === 's2' ? distinctReport('RUN_B') : selectionReport('RUN_B'));
      await settle();
      result[stage] = {stillBusy, duplicates, released: !w.el(stage + '-run').disabled};
    }
    return result;
  },
  async submission_switches() {
    const result = {};
    for (const timing of ['away', 'returned', 'failed']) {
      const w = await start();
      w.el('scan-form').fire('submit'); await settle();
      const post = take(w, '/api/scan');
      await switchTo(w, 'sportybet');
      answer(take(w, '/api/runs'), {runs: []}); await settle();
      const otherEnabled = !w.el('s1-run').disabled;
      let pendingLocked = true;
      if (timing === 'returned') {
        await switchTo(w, 'bet9ja');
        answer(take(w, '/api/runs'), {runs: []}); await settle();
        pendingLocked = w.el('s1-run').disabled && w.el('runs-load').disabled;
        w.el('scan-form').fire('submit'); await settle();
        pendingLocked = pendingLocked && !w.pending.some(c => c.method === 'POST');
      }
      answer(post, timing === 'failed' ? {error: {message: 'Launch refused'}} : runStatus('RUN_NEW', 'running'), timing === 'failed' ? 500 : 202);
      await settle();
      const noOtherPaint = timing === 'returned' || w.el('s1-state').textContent === 'No run yet.';
      if (timing !== 'returned') {
        await switchTo(w, 'bet9ja');
        answer(take(w, '/api/runs'), {runs: []}); await settle();
      }
      result[timing] = {otherEnabled, pendingLocked, noOtherPaint, error: w.el('s1-error').textContent, ...look(w)};
    }
    return result;
  },
});

async function main() {
  const out = {};
  for (const [name, run] of Object.entries(scenarios)) out[name] = await run();
  console.log(JSON.stringify(out, null, 1));
}

main().catch(error => { console.error(error); process.exit(1); });
