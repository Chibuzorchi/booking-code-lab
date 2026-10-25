/* Drive the /app console script through the state transitions that a syntax or
 * source check cannot see: a reply arriving for a run or provider the user has
 * already left, a provider switch, cancelled_unresolved settling, and a new run
 * that arrives already succeeded.
 *
 * The script is executed as written (loaded from portfolio/app.js) against a
 * DOM stub, a fetch whose replies this file resolves by hand, and fake timers, so
 * every observation below is the real code's behaviour. The clipboard paths are not
 * stubbed here: copy_probe.js covers those against the helper itself.
 *
 * Prints one JSON object: scenario name -> what the page ended up showing.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', '..', 'portfolio', 'app.js'), 'utf8');

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
    this.checked = false;
  }
  scrollIntoView() {}
  querySelectorAll(tag) {
    return this.childNodes.flatMap(child => [
      ...(child.tagName === tag.toUpperCase() ? [child] : []), ...child.querySelectorAll(tag)]);
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
    const inline = this['on' + type];        // a real click runs onclick as well
    if (typeof inline === 'function') inline(event);
  }
  get text() { return this.textContent || this.childNodes.map(kid => kid.text).join(''); }
}

/* A fresh world per scenario: its own elements, timers and pending requests. */
function boot(values) {
  const nodes = new Map();
  const world = {pending: [], timers: new Map(), copied: []};
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
    {button(el, value) { world.copied.push(value); return true; },
     text() { return true; }, flash() {}, offer() {}, shortcut: () => 'Ctrl-C'});
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

/* Four distinct codes: three priced apart, one the scanner could not price. */
const ticket = (code, odds, legs) => ({
  id: `bet9ja:${code}`, raw: {code}, num_legs: legs || 2,
  odds: odds == null ? {} : {parsed_leg_product: odds},
  legs: [{event: 'A v B', league: 'L', market: '1X2', pick: 'Home', odds: 2.5, kickoff: 'k'}],
});
const bandedReport = run_id => ({
  run_id, input_count: 9, pool_size: 6, distinct_codes: 4, overlapping_codes: 2, shared_legs: 1,
  excluded: [], odds_basis: 'parsed_leg_product', odds_label: 'recorded, not verified payout',
  min_odds: 1, max_odds: null, top_legs: [],
  distinct: [ticket('LOW111', 9), ticket('MID222', 400), ticket('HIGH33', 9000), ticket('NOPRICE', null)],
});

/* The whole pool for a run: the four above plus two that overlap each other. */
const codesReport = run_id => ({
  run_id, input_count: 9, pool_size: 6, excluded: [], odds_basis: 'parsed_leg_product',
  odds_label: 'recorded, not verified payout', min_odds: 1, max_odds: null,
  codes: [ticket('MID222', 400), ticket('LOW111', 9), ticket('NOPRICE', null),
          ticket('HIGH33', 9000), ticket('TWIN44', 50), ticket('TWIN55', 60)],
});

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

const shownCodes = world => world.el('s2-cards').childNodes
  .map(card => card.childNodes[0].childNodes[0].childNodes[1].textContent);

const look = world => ({
  state_line: world.el('s1-state').textContent,
  seed_row: (world.el('s1-counters').childNodes
    .map(li => li.childNodes.map(cell => cell.textContent))
    .filter(pair => pair[0] === 'Seed').map(pair => pair[1])[0] || ''),
  run_disabled: world.el('s1-run').disabled,
  cancel_disabled: world.el('s1-cancel').disabled,
  s2_locked: world.el('s2-fields').disabled,
  s3_locked: world.el('s3-fields').disabled,
  s2_cards: world.el('s2-cards').childNodes.length,
  s3_cards: world.el('s3-cards').childNodes.length,
  s3_facts_hidden: world.el('s3-facts').hidden,
  s2_note: world.el('s2-count').textContent,
  s2_shown: shownCodes(world),
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
  /* F8: the fresh-seed box replaces the seed field and books during the run. */
  async fresh_seed() {
    const world = await start();
    const before = {seed_disabled: world.el('seed').disabled,
                    hint: world.el('seed-hint').textContent};
    world.el('fresh_seed').checked = true;
    world.el('fresh_seed').fire('change');
    const ticked = {seed_disabled: world.el('seed').disabled,
                    hint: world.el('seed-hint').textContent};
    world.el('scan-form').fire('submit');
    await settle();
    const post = take(world, '/api/scan');
    const booking = {provider: 'bet9ja', run_id: 'RUN_F', state: 'running', seed: null,
                     counters: counting(), request: {seed: null, fresh_seed: true}};
    answer(post, booking, 202);
    await settle();
    const while_booking = look(world);
    for (const fire of [...world.timers.values()]) fire();
    await settle();
    const poll = take(world, '/api/scan/RUN_F');
    answer(poll, {...booking, seed: 'BK9JA07'});
    await settle();
    const booked = look(world);
    const finished = await (async () => {
      for (const fire of [...world.timers.values()]) fire();
      await settle();
      const call = take(world, '/api/scan/RUN_F');
      answer(call, {...booking, state: 'failed', seed: null,
                    error: {code: 'booking_failed', message: 'fresh-seed booking failed: no code'}});
      await settle();
      return look(world);
    })();
    // Unticking hands the seed field back.
    world.el('fresh_seed').checked = false;
    world.el('fresh_seed').fire('change');
    const unticked = {seed_disabled: world.el('seed').disabled,
                      hint: world.el('seed-hint').textContent};
    return {body: post.body, before, ticked, unticked, while_booking, booked, finished};
  },

  /* F9: stage 2's order/band controls are a view, not another analysis. */
  async distinct_view() {
    const world = await start();
    await pick(world, 'RUN_A', 'succeeded');
    press(world, 's2-run');
    await settle();
    answer(take(world, '/api/distinct'), bandedReport('RUN_A'));
    await settle();
    const set = (id, value) => { world.el(id).value = value; world.el(id).fire('input'); };
    const copy = () => { world.copied.length = 0; world.el('s2-copy-all').fire('click');
                         return world.copied[0]; };

    const initial = {...look(world), copied: copy()};
    set('s2-order', 'desc');
    const highest = {...look(world), copied: copy()};
    set('s2-order', 'asc');
    set('s2-min', '10');
    set('s2-max', '1000');
    const banded = {...look(world), copied: copy(), copy_disabled: world.el('s2-copy-all').disabled};
    set('s2-max', '');
    set('s2-min', '900000');                 // a real band that nothing reaches
    const empty = {...look(world), copied: copy(), copy_disabled: world.el('s2-copy-all').disabled};
    set('s2-min', '1000');
    set('s2-max', '10');
    const inverted = look(world);

    // Stage 3 must still see every distinct code, not the three on screen.
    set('s2-min', '');
    set('s2-max', '1000');
    press(world, 's3-run');
    await settle();
    const request = take(world, '/api/decorrelate');
    answer(request, {...selectionReport('RUN_A'),
                     selection: {...selectionReport('RUN_A').selection,
                                 selected_codes: ['LOW111', 'MID222', 'HIGH33', 'NOPRICE']}});
    await settle();
    const with_stage3 = {subset: world.el('s3-subset').text,
                         s2_shown: shownCodes(world), request_body: request.body};

    world.el('s2-view-reset').fire('click');
    const reset = look(world);

    // The band belongs to this provider's view, and a new run starts clean.
    const kept = await (async () => {
      set('s2-max', '1000');
      await switchTo(world, 'sportybet');
      answer(take(world, '/api/runs'), {runs: []});
      await settle();
      const away = {s2_cards: world.el('s2-cards').childNodes.length,
                    max: world.el('s2-max').value};
      await switchTo(world, 'bet9ja');
      answer(take(world, '/api/runs'), {runs: []});
      await settle();
      return {away, back: {...look(world), max: world.el('s2-max').value}};
    })();
    await pick(world, 'RUN_B', 'succeeded');
    press(world, 's2-run');
    await settle();
    answer(take(world, '/api/distinct'), bandedReport('RUN_B'));
    await settle();
    const new_run = {...look(world), max: world.el('s2-max').value};

    return {initial, highest, banded, empty, inverted, with_stage3, reset, kept, new_run};
  },

  /* F10: a succeeded run lists every code it found, before stage 2 is asked. */
  async all_codes() {
    const world = await start();
    const s1 = () => ({
      hidden: world.el('s1-codes').hidden,
      note: world.el('s1-count').textContent,
      shown: world.el('s1-cards').childNodes
        .map(card => card.childNodes[0].childNodes[0].childNodes[1].textContent),
      error: world.el('s1-codes-error').textContent,
      controls_hidden: world.el('s1-view').hidden,
      copy_disabled: world.el('s1-copy-all').disabled,
      asks: world.pending.filter(c => c.url.includes('/api/codes')).length,
    });
    const set = (id, value) => { world.el(id).value = value; world.el(id).fire('input'); };
    const copy = () => { world.copied.length = 0; world.el('s1-copy-all').fire('click');
                         return world.copied[0]; };

    // A run still scanning lists what its worker has saved so far...
    const partial = (run_id, state) => ({...codesReport(run_id), partial: true, state, pool_size: 2,
                                         codes: [ticket('MID222', 400), ticket('LOW111', 9)]});
    await pick(world, 'RUN_A', 'running');
    answer(take(world, '/api/codes'), partial('RUN_A', 'running'));
    await settle();
    const scanning = s1();
    // ...and does not ask again while its qualifying count stands still.
    for (const fire of [...world.timers.values()]) fire();
    await settle();
    answer(take(world, '/api/scan/RUN_A'), runStatus('RUN_A', 'running'));
    await settle();
    const unchanged = s1();
    for (const fire of [...world.timers.values()]) fire();
    await settle();
    answer(take(world, '/api/scan/RUN_A'), runStatus('RUN_A', 'succeeded'));
    await settle();
    const asked = {...s1(), body: world.pending.find(c => c.url.includes('/api/codes')).body};
    answer(take(world, '/api/codes'), codesReport('RUN_A'));
    await settle();
    const listed = {...s1(), copied: copy(), s2_cards: world.el('s2-cards').childNodes.length,
                    s2_locked: world.el('s2-fields').disabled};

    set('s1-order', 'desc');
    const highest = s1();
    set('s1-order', 'asc');
    set('s1-max', '1000');
    const banded = {...s1(), copied: copy()};

    // Its band is its own: stage 2 lists every distinct code regardless.
    press(world, 's2-run');
    await settle();
    answer(take(world, '/api/distinct'), bandedReport('RUN_A'));
    await settle();
    const beside_stage2 = {s1: s1().shown, s2: shownCodes(world), s2_max: world.el('s2-max').value};

    // Remembered per provider: coming back repaints it without asking again.
    await switchTo(world, 'sportybet');
    answer(take(world, '/api/runs'), {runs: []});
    await settle();
    const away = s1();
    await switchTo(world, 'bet9ja');
    answer(take(world, '/api/runs'), {runs: []});
    await settle();
    const back = {...s1(), max: world.el('s1-max').value};
    world.el('s1-view-reset').fire('click');
    const reset = s1();

    // Another run replaces the list; a run with nothing in band says so.
    await pick(world, 'RUN_B', 'succeeded');
    const replaced = s1();
    answer(take(world, '/api/codes'), {...codesReport('RUN_B'), pool_size: 0, codes: [],
                                       excluded: [{reason: 'below odds band'}]});
    await settle();
    const none = s1();

    // A run that stopped early keeps what it saved; one with nothing saved stays quiet.
    await pick(world, 'RUN_C', 'failed');
    answer(take(world, '/api/codes'), partial('RUN_C', 'failed'));
    await settle();
    const failed_run = s1();
    await pick(world, 'RUN_E', 'cancelled');
    answer(take(world, '/api/codes'), {error: {message: 'run RUN_E has no saved codes'}}, 409);
    await settle();
    const nothing_saved = s1();
    // A succeeded run owes a list, so a refusal there is reported.
    await pick(world, 'RUN_D', 'succeeded');
    answer(take(world, '/api/codes'), {error: {message: 'Cannot parse committed extract'}}, 422);
    await settle();
    const refused = s1();
    return {scanning, unchanged, nothing_saved, asked, listed, highest, banded, beside_stage2, away, back, reset,
            replaced, none, failed_run, refused};
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

scenarios.expired_history = async () => {
  const world = await start();
  await pick(world, 'RUN_A', 'succeeded');
  const expired = Array.from({length: 26}, (_, i) => ({
    ...ticket('PAST' + String(i).padStart(3, '0'), i + 10),
    history_note: 'A match has started; outcome not checked.',
  }));
  answer(take(world, '/api/codes'), {...codesReport('RUN_A'), pool_size: 0,
    codes: [], saved_count: 26, expired_count: 26, expired_codes: expired,
    excluded: expired.map(() => ({reason: 'invalid_ticket'}))});
  await settle();
  const hidden = {cards: world.el('s1-cards').childNodes.length,
    button: world.el('s1-expired').textContent, available: !world.el('s1-expired').hidden,
    note: world.el('s1-count').textContent};
  world.el('s1-expired').fire('click');
  const revealed = {cards: world.el('s1-cards').childNodes.length,
    page: world.el('s1-page-info').textContent,
    label: world.el('s1-cards').childNodes[0].text,
    pressed: world.el('s1-expired').getAttribute('aria-pressed')};
  world.el('s1-copy-all').fire('click');
  const copied = world.copied[0].split('\n').length;
  world.el('s1-next').fire('click');
  const finalPage = world.el('s1-cards').childNodes.length;
  world.el('s1-expired').fire('click');
  const rehidden = world.el('s1-cards').childNodes.length;
  world.el('s1-expired').fire('click');
  await switchTo(world, 'sportybet');
  await switchTo(world, 'bet9ja');
  const restored = world.el('s1-expired').getAttribute('aria-pressed');
  await pick(world, 'RUN_B', 'succeeded');
  answer(take(world, '/api/codes'), codesReport('RUN_B'));
  await settle();
  return {hidden, revealed, copied, finalPage, rehidden, restored,
    reset: world.el('s1-expired').hidden, requests: world.pending.map(c => c.url)};
};


scenarios.historical_results = async () => {
  const w = await start();
  await pick(w, 'RUN_A', 'succeeded');
  answer(take(w, '/api/codes'), codesReport('RUN_A')); await settle();
  w.el('results-open').fire('click'); await settle();
  answer(take(w, '/api/results/RUN_A'), {error:{code:'report_not_found', message:'No report'}}, 404); await settle();
  const rows = Array.from({length:26},(_,i)=>({row_id:'digest:'+i, ticket_id:'OLD'+i,
    status:'unresolved',reason:'not_checked',checked:false,legs:[],original_ticket:{code:'OLD'+i,selections:[]}}));
  answer(take(w, '/codes?'), {total:26,filtered_total:26,input_digest:'digest',rows:rows.slice(0,25)}); await settle();
  const before = {cards:w.el('results-cards').childNodes.length,summary:w.el('results-summary').textContent};
  const button = w.el('results-cards').childNodes[0].childNodes.find(el=>el.tagName==='BUTTON');
  button.fire('click'); await settle();
  const submitted = take(w, '/api/results/RUN_A');
  const rowSelector = submitted.body.row_id;
  answer(submitted, {job_id:'job-one'},202); await settle();
  answer(take(w,'/api/results/jobs/job-one'),{state:'succeeded',result:{revision_id:'rev-one'}}); await settle();
  answer(take(w,'/api/results/RUN_A'),{revision_id:'rev-one',created_at:'2026-10-24',checked_count:1,
    counts:{total:26,won:1,lost:0,void:0,pending:0,unresolved:25},percentages:{
      confirmed_win_rate:{numerator:1,denominator:26,value:1/26},
      decided_win_loss_rate:{numerator:1,denominator:1,value:1},
      classification_coverage:{numerator:1,denominator:26,value:1/26}}}); await settle();
  rows[0]={...rows[0],checked:true,status:'won',reason:null};
  const focused = take(w,'/codes?');
  const focusedRow = focused.url.includes('row_id=digest%3A0');
  answer(focused,{total:26,filtered_total:1,rows:[rows[0]]}); await settle();
  w.el('results-show-all').fire('click'); await settle();
  answer(take(w,'/codes?'),{total:26,filtered_total:26,rows:rows.slice(0,25)}); await settle();
  const checked = {summary:w.el('results-summary').textContent,rates:w.el('results-rates').textContent};
  w.el('results-next').fire('click'); await settle();
  const next = take(w,'/codes?');
  const pinned = next.url.includes('revision=rev-one') && next.url.includes('offset=25');
  answer(next,{total:26,filtered_total:26,rows:rows.slice(25)}); await settle();
  const finalPage = w.el('results-cards').childNodes.length;
  w.el('results-filter').value='won'; w.el('results-filter').fire('change'); await settle();
  const filtered=take(w,'/codes?');
  const filtering=filtered.url.includes('status=won') && filtered.url.includes('offset=0');
  answer(filtered,{total:26,filtered_total:1,rows:[rows[0]]}); await settle();
  const unchanged=w.el('results-summary').textContent===checked.summary;
  w.el('results-filter').value=''; w.el('results-filter').fire('change'); await settle();
  const stale=take(w,'/codes?');
  await pick(w,'RUN_B','succeeded');
  answer(take(w,'/api/codes'),codesReport('RUN_B')); await settle();
  answer(stale,{error:{message:'Old run unavailable'}},422); await settle();
  return {before,rowSelector,focusedRow,checked,pinned,finalPage,filtering,unchanged,
    staleError:w.el('results-error').textContent,hidden:w.el('results-body').hidden,
    run:w.el('s1-state').textContent};
};


scenarios.direct_saved_code_actions = async () => {
  const w = await start();
  await pick(w,'RUN_A','succeeded');
  const report=codesReport('RUN_A'); report.saved_count=report.codes.length;
  report.codes.forEach((ticket,i)=>ticket.row_id='saved:'+i);
  answer(take(w,'/api/codes'),report); await settle();
  const summary=w.el('s1-cards').childNodes[0].childNodes[0];
  const actions=summary.childNodes.find(el=>el.className==='ticket-actions');
  actions.childNodes.find(el=>el.textContent==='Check result').fire('click'); await settle();
  answer(take(w,'/api/results/RUN_A'),{error:{code:'report_not_found',message:'No report'}},404); await settle();
  answer(take(w,'/codes?'),{total:report.saved_count,rows:[],input_digest:'saved'}); await settle();
  const single=take(w,'/api/results/RUN_A'); const selected=single.body.row_id;
  answer(single,{job_id:'direct-one'},202); await settle();
  answer(take(w,'/api/results/jobs/direct-one'),{state:'failed',error:{message:'No source'}}); await settle();
  w.el('s1-check-results').fire('click'); await settle();
  const all=take(w,'/api/results/RUN_A'); const allBody=all.body;
  answer(all,{job_id:'direct-all'},202); await settle();
  answer(take(w,'/api/results/jobs/direct-all'),{state:'failed',error:{message:'No source'}}); await settle();
  const displayedCode=summary.childNodes[0].childNodes[1].textContent;
  const expected=report.codes.find(ticket=>ticket.raw.code===displayedCode).row_id;
  return {selected,expected,allBody,copyPresent:actions.childNodes.some(el=>el.textContent==='Copy'),
    label:w.el('s1-check-results').textContent};
};

async function main() {
  const out = {};
  for (const [name, run] of Object.entries(scenarios)) out[name] = await run();
  console.log(JSON.stringify(out, null, 1));
}

main().catch(error => { console.error(error); process.exit(1); });
