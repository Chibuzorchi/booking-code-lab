
'use strict';
const $ = id => document.getElementById(id);
const POLL_MS = 3000;
/* The states in which the registry still owns this provider's worker
   (registry.active_run). cancelled_unresolved is NOT an end state -- it settles to
   cancelled once the worker is gone -- so the page keeps polling it and keeps this
   provider's launch disabled, because a second scan would be refused with 409. */
const ACTIVE = new Set(['starting', 'running', 'cancelled_unresolved']);
const CANCELLABLE = new Set(['starting', 'running']);
const BASIS_LABEL = {parsed_leg_product: 'parsed leg product', site_displayed_odds: 'site displayed odds',
                     verified_payout: 'verified payout'};

const saved = Object.create(null);      // provider -> that provider's own view
let provider = $('provider').value;     // the provider whose view is on screen
let view = blankView();                 // the run and the two analyses now shown
let gen = 0;                            // bumped whenever the selection changes
let poller = null, pollGen = -1;
let historyRequest = 0;
const analysisRequest = {s1: null, s2: null, s3: null};

/* Navigation changes which existing panel is visible; it does not own analysis
   results or invalidate requests when the selected provider/run stays the same. */
const SECTIONS = {
  scan: {panel: 's1', title: 'Scan Codes'},
  runs: {panel: 'saved-runs', title: 'Saved Runs'},
  distinct: {panel: 's2', title: 'Distinct Codes'},
  decorrelate: {panel: 's3', title: 'De-correlate'},
  results: {panel: 'historical-results', title: 'Historical Results'},
};
const PROVIDER_NAMES = {bet9ja: 'Bet9ja', sportybet: 'SportyBet'};
const STATE_NAMES = {starting: 'Starting', running: 'Running', succeeded: 'Completed',
  failed: 'Failed', launch_failed: 'Could not start', cancelled: 'Cancelled',
  cancelled_unresolved: 'Cancellation pending', unknown: 'Completion unknown'};
let section = 'scan', routeToken = 0, pendingRun = null, handledHash = null;
const sectionScroll = Object.create(null);
let runHistory = [], historyLoading = true, historyProvider = null, selectedHistory = '', historyPage = 0;
let runReturn = 'scan', runLoading = null;

function routeHash(target = section) {
  const query = new URLSearchParams({provider});
  const run = pendingRun || (view.run && view.run.run_id);
  if (run) query.set('run', run);
  return '#' + target + '?' + query;
}

function syncRoute(push = false) {
  const hash = routeHash();
  if (window.location.hash !== hash) window.history[push ? 'pushState' : 'replaceState'](null, '', hash);
  handledHash = hash;
  renderContext();
}

function renderContext() {
  const run = view.run;
  let date = '';
  if (run && run.started_at && Number.isFinite(Date.parse(run.started_at))) {
    date = new Date(run.started_at).toLocaleString(undefined,
      {year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short'});
  }
  $('context-run').textContent = pendingRun ? 'Loading saved run…' : run
    ? [PROVIDER_NAMES[provider], date, 'Seed ' + seedText(run)].filter(Boolean).join(' · ')
    : 'No run selected';
  $('context-state').textContent = view.submission ? 'Submitting scan…' : run
    ? (STATE_NAMES[run.state] || run.state) : 'Choose a saved run or start a scan.';
  $('context-details').hidden = !run;
  $('context-id').textContent = run ? run.run_id : '';
  $('change-run').textContent = run ? 'Change run' : 'Choose a run';
  $('change-run').setAttribute('href', routeHash('runs'));
  for (const key of Object.keys(SECTIONS)) $('nav-' + key).setAttribute('href', routeHash(key));
  runControls();
}

function setNavigation(open) {
  $('app-sidebar').hidden = !open;
  $('app-layout').className = open ? 'app-layout' : 'app-layout nav-closed';
  $('nav-toggle').setAttribute('aria-expanded', String(open));
  $('nav-toggle').textContent = open ? 'Hide navigation' : 'Show navigation';
}

function navigateSection(next, {write = true, focus = true} = {}) {
  if (!SECTIONS[next]) next = 'scan';
  if (section !== next) sectionScroll[section] = window.scrollY;
  const changed = section !== next;
  section = next;
  for (const [key, config] of Object.entries(SECTIONS)) {
    $(config.panel).hidden = key !== section;
    if (key === section) $('nav-' + key).setAttribute('aria-current', 'page');
    else $('nav-' + key).removeAttribute('aria-current');
  }
  $('page-title').textContent = SECTIONS[section].title;
  document.title = SECTIONS[section].title + ' · Booking codes';
  if (write) syncRoute(true);
  if (focus) {
    if (window.matchMedia('(max-width:850px)').matches) setNavigation(false);
    // On a return to a scrolled view, focus its labelled workspace rather than
    // leaving keyboard focus on a heading above the viewport.
    $(sectionScroll[section] ? 'workspace' : 'page-title').focus({preventScroll: true});
  }
  if (changed) window.scrollTo(0, sectionScroll[section] || 0);
}

async function restoreRoute() {
  if (handledHash === window.location.hash) return;
  handledHash = window.location.hash;
  const [requestedSection, search = ''] = window.location.hash.slice(1).split('?');
  const query = new URLSearchParams(search);
  const nextSection = SECTIONS[requestedSection] ? requestedSection : 'scan';
  const requestedProvider = query.get('provider') || 'bet9ja';
  const nextProvider = PROVIDER_NAMES[requestedProvider] ? requestedProvider : 'bet9ja';
  let nextRun = query.get('run') || '';
  let problem = PROVIDER_NAMES[requestedProvider] ? '' : 'Unknown provider in the link. Choose a saved run.';
  if (nextRun && !new RegExp('^run-' + nextProvider + '-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$').test(nextRun)) {
    nextRun = '';
    problem = 'Invalid run link. Choose a saved run.';
  }
  fail('navigation-error', problem);
  // Browser history must obey the same submission guard as the run selector.
  const remembered = nextProvider === provider ? view : saved[nextProvider];
  if (remembered && remembered.submission) {
    if (nextProvider !== provider) {
      $('provider').value = nextProvider;
      switchProvider({syncURL: false});
    }
    navigateSection(nextSection, {write: false});
    syncRoute();
    if (nextRun) fail('navigation-error', 'Wait for scan submission before changing runs.');
    return;
  }
  if (nextProvider === provider && (nextRun === (pendingRun || (view.run && view.run.run_id) || ''))) {
    navigateSection(nextSection, {write: false});
    syncRoute();
    return;
  }
  const token = ++routeToken;
  saved[provider] = view;
  invalidate();
  pendingRun = null;
  provider = nextProvider;
  $('provider').value = provider;
  view = remembered && remembered.run && remembered.run.run_id === nextRun ? remembered : blankView();
  seedHint();
  restore();
  refreshRuns();
  navigateSection(nextSection, {write: false});
  if (!nextRun || view.run) { syncRoute(); return; }
  pendingRun = nextRun;
  $('s1-run').disabled = $('runs-load').disabled = true;
  renderContext();
  try {
    // Reload restores only the run and saved tickets. Analyses and result-check
    // jobs remain explicit user actions; never serialize reports into the URL.
    const status = await api('GET', '/api/scan/' + encodeURIComponent(nextRun));
    if (token !== routeToken) return;
    if (status.provider !== provider || status.run_id !== nextRun)
      throw new Error('The saved run does not match this link.');
    showRun(status);
    loadCodes();
    if (ACTIVE.has(status.state)) startPolling();
  } catch (error) {
    if (token === routeToken) fail('navigation-error', 'Could not restore this run: ' + error.message);
  } finally {
    if (token === routeToken) {
      pendingRun = null;
      $('s1-run').disabled = Boolean(view.submission || (view.run && ACTIVE.has(view.run.state)));
      runControls();
      syncRoute();
    }
  }
}

function initializeShell() {
  for (const key of Object.keys(SECTIONS)) $('nav-' + key).addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); if (key === 'runs') runReturn = 'scan'; navigateSection(key);
  });
  $('change-run').addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); runReturn = section === 'runs' ? 'scan' : section; navigateSection('runs');
  });
  $('skip-workspace').addEventListener('click', event => { event.preventDefault(); $('page-title').focus(); });
  $('nav-toggle').addEventListener('click', () => setNavigation($('app-sidebar').hidden));
  setNavigation(!window.matchMedia('(max-width:850px)').matches);
  window.addEventListener('popstate', restoreRoute);
  window.addEventListener('hashchange', restoreRoute);
  restoreRoute();
}

/* Every reply is checked against the generation it was issued under. Anything in
   flight when the user picks another run or provider belongs to the selection they
   left, so it drops itself instead of painting over the current one. */
function invalidate() {
  gen += 1;
  runLoading = null;
  stopPolling();
  analysisRequest.s1 = analysisRequest.s2 = analysisRequest.s3 = null;
  $('s2-run').disabled = $('s3-run').disabled = false;
}

function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
const num = v => v == null ? '—' : Number(v).toLocaleString(undefined, {maximumFractionDigits: 2});

function head(names) {
  const row = node('tr'), group = node('thead');
  for (const name of names) {
    const cell = node('th', name);
    cell.scope = 'col';
    row.append(cell);
  }
  group.append(row);
  return group;
}

function fail(id, message) {
  const box = $(id);
  box.textContent = message;
  box.hidden = !message;
}

function facts(id, pairs) {
  const box = $(id);
  box.replaceChildren();
  for (const [name, value] of pairs) {
    const li = node('li');
    li.append(node('b', name), node('span', String(value), 'n'));
    box.append(li);
  }
  box.hidden = false;
}

/* Every call goes through here, so the API's error envelope is always shown. */
async function api(method, path, body) {
  const init = {method, cache: 'no-store', headers: {Accept: 'application/json'}};
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (e) {
    throw new Error('Cannot reach the local server — is it still running?');
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (e) {
    throw new Error(`Unreadable reply from ${path} (HTTP ${response.status})`);
  }
  if (!response.ok) {
    const error = (data && data.error) || {};
    const failure = new Error(`${error.message || 'Request failed'} (HTTP ${response.status}${error.code ? ', ' + error.code : ''})`);
    failure.status = response.status; failure.code = error.code; throw failure;
  }
  return data;
}

function bareCode(ticket) {
  const raw = ticket.raw || {};
  if (raw.code) return String(raw.code).trim();
  const id = ticket.id || '';
  return id.includes(':') ? id.slice(id.indexOf(':') + 1) : id;
}

function oddsOf(ticket, basis) {
  const odds = ticket.odds || {};
  if (basis === 'verified_payout') {
    const payout = odds.payout_semantics || {};
    return payout.status === 'verified' ? payout.multiplier : null;
  }
  return odds[basis] == null ? null : odds[basis];
}

function bandText(report) {
  const basis = BASIS_LABEL[report.odds_basis] || report.odds_basis;
  const upper = report.max_odds == null ? 'no ceiling' : num(report.max_odds);
  return `Odds basis: ${basis} — ${report.odds_label}. Band ${num(report.min_odds)} to ${upper}, `
       + `as recorded by the scan; the page cannot override it.`;
}

function lock(stage, locked, note) {
  $(stage).setAttribute('aria-disabled', String(locked));
  $(stage + '-fields').disabled = locked;
  $(stage + '-lock').textContent = note;
}

/* ---------- stage 1: harvest ---------- */

/* The seed-source radios select exactly one source. Fresh booking takes
   the text input out of play (a
   disabled input is also skipped by form validation, which is why `required`
   can stay on it). */
function seedHint() {
  const fresh = $('fresh_seed').checked;
  $('seed').disabled = fresh;
  $('fresh-hint').hidden = !fresh;
  $('seed-hint').textContent = fresh
    ? 'booked for you when the run starts'
    : ($('provider').value === 'bet9ja' ? '7 characters, case kept' : '6 characters, upper-cased');
}

/* One view per provider. Switching providers never leaves the other provider's run,
   poller or analysis on screen, and never mixes the two: the outgoing view is stashed
   whole and the incoming one is repainted from its own remembered reports. */
/* How stage 2's list is being looked at. Kept with the rest of the provider's
   view, so switching away and back shows the same list, not a reset one. */
function blankPrefs() { return {order: 'asc', min: null, max: null, minLegs: null, maxLegs: null, search: ''}; }
function blankView() { return {run: null, codes: null, codesAsked: null, codesError: null, stage2: null, stage3: null, submission: null, showExpired: false,
                               error: null, statusError: null, cancelling: null, prefs: blankPrefs(), codesPrefs: blankPrefs(), codesPage: 0, page: 0}; }

/* Pagination is purely a slice of whatever `arrange()` already produced, so it
   never changes what counts as distinct or what a band/order selects -- only
   how many of those rows are drawn into the DOM at once. */
const PAGE_SIZE = 25;
function paginate(rows, page) {
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const clamped = Math.min(Math.max(0, page), totalPages - 1);
  const start = clamped * PAGE_SIZE;
  return {pageRows: rows.slice(start, start + PAGE_SIZE), totalPages, page: clamped};
}

function renderPager(stage, page, totalPages, total) {
  const bar = $(stage + '-pager');
  bar.hidden = total <= PAGE_SIZE;
  $(stage + '-page-info').textContent = `Page ${page + 1} of ${totalPages} (${PAGE_SIZE} per page)`;
  $(stage + '-prev').disabled = page <= 0;
  $(stage + '-next').disabled = page >= totalPages - 1;
}

function clearRun() {
  view.run = null;
  resetResults();
  $('s1-counters').hidden = true;
  $('s1-state').textContent = 'No run yet.';
  $('s1-cancel').disabled = true;
  $('s1-run').disabled = false;
  $('s1-refresh').hidden = true;
  $('s1-diagnostics').hidden = true;
  $('s1-diagnostic-text').textContent = '';
  $('s1-guidance').textContent = 'Choose a seed source and set your limits to start.';
  $('scan-form').setAttribute('aria-busy', 'false');
  fail('s1-error', '');
  lock('s2', true, 'Locked until a scan on this page has committed an extract.');
}

function restore() {
  const {run, codes, codesAsked, codesError, stage2, stage3, prefs, codesPrefs, showExpired} = view;
  clearRun();
  clearAnalysis();
  $('s2-run').disabled = false;
  $('s3-run').disabled = false;
  if (run) {
    showRun(run);                 // repaint stage 1 from the remembered status...
    if (ACTIVE.has(run.state)) startPolling();   // ...and poll again if it is still live
  }
  view.codes = codes;             // showRun's run-change reset cleared these
  view.codesAsked = codesAsked;
  view.codesError = codesError;
  view.stage2 = stage2;
  view.stage3 = stage3;
  view.prefs = prefs || blankPrefs();
  view.codesPrefs = codesPrefs || blankPrefs();
  view.showExpired = Boolean(showExpired);
  writePrefs('s2', view.prefs);
  writePrefs('s1', view.codesPrefs);
  if (codes) renderCodes(codes);
  showCodesError();               // a failed listing keeps its message and Retry while away
  loadCodes();                    // a no-op unless the run moved on while away
  if (stage2) {
    renderDistinct(stage2);
    lock('s3', false, stage3Note(stage2));
  }
  if (stage3) renderSelection(stage3);
  if (view.submission) {
    $('s1-state').textContent = 'Submitting scan…';
    $('s1-guidance').textContent = 'Waiting for confirmation. Do not submit again.';
    $('scan-form').setAttribute('aria-busy', 'true');
    $('s1-run').disabled = true;
    $('s1-cancel').disabled = true;
    lock('s2', true, 'Waiting for scan submission.');
  }
  if (view.error) {
    fail('s1-error', view.error);
    $('s1-state').textContent = 'Scan submission could not be confirmed.';
    $('s1-guidance').textContent = 'Check Saved Runs before starting again: the server may have received your request.';
  }
  runControls();
  renderContext();
}

function switchProvider({syncURL = true} = {}) {
  routeToken += 1;
  pendingRun = null;
  fail('navigation-error', '');
  saved[provider] = view;
  invalidate();                   // drop replies for the provider being left
  provider = $('provider').value;
  view = saved[provider] || blankView();
  seedHint();
  restore();
  refreshRuns();
  if (syncURL) syncRoute(true);
}

/* A fresh-seed run has no seed until its worker books one, so "booking…" is
   the honest reading of a null seed while the run is still live; a null seed
   on a finished run means it never got one. */
function seedText(status) {
  if (status.seed) return status.seed;
  if (!(status.request || {}).fresh_seed) return '—';
  return ACTIVE.has(status.state) ? 'booking…' : 'never booked';
}

const STOP_NAMES = {try_budget: 'Attempt limit reached', qualifying_limit: 'Code limit reached',
  candidates_exhausted: 'All candidate codes checked', cancelled: 'Cancelled by request',
  failure: 'Stopped after an error', unknown: 'Stop reason unavailable'};

function counterRows(status) {
  const c = status.counters || {};
  return [['Seed', seedText(status)], ['Codes tried', num(c.tried)],
          ['Valid codes', num(c.found_ok)], ['Qualifying found', num(c.qualifying)]];
}

function scanFeedback(status) {
  const booking = (status.request || {}).fresh_seed && !status.seed && ACTIVE.has(status.state);
  let title = STATE_NAMES[status.state] || 'Status unavailable';
  let guidance = 'Inspect saved codes below or choose another run.';
  if (view.cancelling || status.state === 'cancelled_unresolved') {
    title = 'Cancelling scan…';
    guidance = 'Waiting for the worker to stop. Another scan for this provider stays blocked until cancellation is confirmed.';
  } else if (booking) {
    title = 'Booking a fresh code';
    guidance = 'The provider browser is booking your seed before scanning. You can cancel while waiting.';
  } else if (ACTIVE.has(status.state)) {
    title = status.state === 'starting' ? 'Starting scan…' : 'Scanning codes';
    guidance = 'You can use other views while scanning continues. Found counts are not confirmed saved totals.';
  } else if (status.state === 'succeeded') {
    guidance = 'Saved codes are ready below. You can continue to Distinct Codes.';
  } else if (status.state === 'cancelled') {
    guidance = 'The scan stopped. Any checkpointed codes remain available below; this run cannot feed analysis.';
  } else if (status.state === 'failed' || status.state === 'launch_failed') {
    guidance = 'Review scan diagnostics for the cause. Any saved codes remain available below.';
  } else if (status.state === 'unknown') {
    guidance = 'Completion could not be confirmed. Refresh status or inspect Saved Runs before starting again.';
  }
  if (status.stop_reason && !ACTIVE.has(status.state)) title += ' — ' + (STOP_NAMES[status.stop_reason] || 'See scan diagnostics');
  if (status.seed) title += ' · seed ' + status.seed;
  $('s1-state').textContent = title;
  $('s1-guidance').textContent = view.statusError
    ? 'Status updates paused. Refresh status to reconnect; this does not start another scan. Last confirmed state is shown above.'
    : guidance;
  const c = status.counters || {};
  $('s1-diagnostic-text').textContent = [
    'Run: ' + status.run_id, 'Provider: ' + status.provider, 'State: ' + status.state,
    'Stop reason: ' + (status.stop_reason || '—'),
    'Simulations excluded: ' + num(c.excluded_simulations), 'Unpriced: ' + num(c.unpriced),
    status.error ? 'Error: ' + (typeof status.error === 'object' ? JSON.stringify(status.error, null, 2) : status.error) : '',
    status.recovery_note || '', view.statusError || '',
  ].filter(Boolean).join('\n');
  $('s1-diagnostics').hidden = false;
  $('s1-refresh').hidden = false;
  $('s1-refresh').disabled = pollGen === gen || Boolean(view.cancelling);
  $('s1-refresh').textContent = pollGen === gen ? 'Refreshing…' : 'Refresh status';
  $('s1-cancel').disabled = Boolean(view.cancelling) || !CANCELLABLE.has(status.state);
  $('s1-run').disabled = Boolean(view.cancelling) || ACTIVE.has(status.state);
}

function showRun(status) {
  // A different run means the analysis on screen describes something else. Drop both
  // stages on any change of run id, whatever state the incoming run is in -- a run
  // that arrives already succeeded must not inherit the previous run's results.
  if (!view.run || view.run.run_id !== status.run_id) { resetAnalysis(); resetResults(); view.statusError = null; }
  if (view.cancelling && view.cancelling.run_id !== status.run_id) view.cancelling = null;
  view.run = status;
  resultsControls();
  facts('s1-counters', counterRows(status));
  scanFeedback(status);

  const ready = status.state === 'succeeded';
  lock('s2', !ready, ready
    ? `Run ${status.run_id} committed its extract. Stage 2 reads that exact snapshot.`
    : `Locked until a run reaches "succeeded" with a committed extract; this one is ${status.state}.`);
  renderContext();
}

function stopPolling() {
  if (poller !== null) clearInterval(poller);
  poller = null;
}

function startPolling() {
  stopPolling();
  poller = setInterval(tick, POLL_MS);
  tick();                       // do not make the user wait one interval for the first update
}

/* One poll in flight at a time: a slow reply must not be overtaken by the next
   interval. pollGen records whose poll is out, so a stale one cannot clear the flag
   for the poll that replaced it. */
async function tick() {
  if (!view.run || view.cancelling || pollGen === gen) return;
  const mine = gen, run_id = view.run.run_id, cancelRevision = view.cancelRevision;
  pollGen = mine;
  scanFeedback(view.run);
  try {
    const status = await api('GET', '/api/scan/' + encodeURIComponent(run_id));
    if (mine !== gen || view.cancelling || cancelRevision !== view.cancelRevision) return;
    if (status.provider !== provider || status.run_id !== run_id) throw new Error('Status does not match the selected run.');
    view.statusError = null;
    fail('s1-error', '');
    showRun(status);
    loadCodes();
    if (!ACTIVE.has(status.state)) stopPolling();
  } catch (e) {
    if (mine !== gen || view.cancelling || cancelRevision !== view.cancelRevision) return;
    stopPolling();
    view.statusError = e.message;
    fail('s1-error', e.message);
  } finally {
    if (pollGen === mine) {
      pollGen = -1;
      if (mine === gen && view.run) scanFeedback(view.run);
    }
  }
}

function optionalNumber(id, name) {
  const raw = $(id).value.trim();
  if (raw === '') return undefined;              // let the API apply its own default
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error(`${name} must be a number`);
  return value;
}

function optionalInteger(id, name, minimum) {
  const value = optionalNumber(id, name);
  if (value === undefined) return undefined;
  if (!Number.isInteger(value) || value < minimum) throw new Error(`${name} must be a whole number >= ${minimum}`);
  return value;
}

async function startScan(event) {
  event.preventDefault();
  if (pendingRun || view.submission || view.cancelling || (view.run && ACTIVE.has(view.run.state))) return;
  fail('s1-error', '');
  let body;
  try {
    body = {provider: $('provider').value};
    if ($('fresh_seed').checked) body.fresh_seed = true;
    else body.seed = $('seed').value.trim();
    for (const [key, value] of Object.entries({
      min_odds: optionalNumber('min_odds', 'Min odds'),
      max_odds: optionalNumber('max_odds', 'Max odds'),
      min_legs: optionalInteger('min_legs', 'Min legs', 0),
      max_legs: optionalInteger('max_legs', 'Max legs', 0),
      want_count: optionalInteger('want_count', 'How many codes', 0),
      try_budget: optionalInteger('try_budget', 'Try budget', 1),
    })) if (value !== undefined) body[key] = value;
  } catch (e) {
    fail('s1-error', e.message);
    return;
  }
  invalidate();
  // Submission has a server-side effect: keep its outcome in the originating
  // provider view even when that provider is no longer on screen.
  const owner = view, token = {};
  owner.submission = token;
  owner.error = null;
  owner.run = owner.codes = owner.codesAsked = owner.codesError = owner.stage2 = owner.stage3 = null;
  restore();
  syncRoute();
  try {
    const status = await api('POST', '/api/scan', body);
    if (owner.submission !== token) return;
    if (status.provider !== body.provider) throw new Error('Scan provider does not match the request.');
    owner.run = status;
  } catch (e) {
    if (owner.submission !== token) return;
    owner.error = e.message;
  } finally {
    if (owner.submission === token) {
      owner.submission = null;
      if (view === owner) {
        invalidate();
        restore();
        syncRoute();
      }
    }
  }
}

async function cancelScan() {
  if (!view.run || view.cancelling || !CANCELLABLE.has(view.run.state)) return;
  fail('s1-error', '');
  const owner = view, run_id = view.run.run_id, token = {run_id};
  owner.cancelling = token;
  owner.cancelRevision = (owner.cancelRevision || 0) + 1;
  scanFeedback(view.run);
  try {
    const status = await api('POST', `/api/scan/${encodeURIComponent(run_id)}/cancel`, {});
    if (!owner.run || owner.run.run_id !== run_id) return;
    if (status.provider !== owner.run.provider || status.run_id !== run_id) throw new Error('Cancellation reply does not match the selected run.');
    owner.run = status;
    owner.statusError = null;
  } catch (e) {
    if (!owner.run || owner.run.run_id !== run_id) return;
    owner.statusError = e.message;
    if (view === owner) fail('s1-error', e.message);
  } finally {
    if (owner.cancelling === token) {
      owner.cancelling = null;
      owner.cancelRevision += 1;
    }
    if (view === owner && owner.run && owner.run.run_id === run_id) {
      showRun(owner.run);
      loadCodes();
      if (ACTIVE.has(owner.run.state)) startPolling();
    }
  }
}

async function refreshScanStatus() {
  if (!view.run || view.submission || view.cancelling || pollGen === gen) return;
  const owner = view, mine = gen;
  await tick();
  if (view !== owner || mine !== gen) return;
  // Resume automatic updates only after a successful read. Never resubmit a job.
  if (view.run && !view.statusError && ACTIVE.has(view.run.state) && poller === null)
    poller = setInterval(tick, POLL_MS);
}

function runDate(status) {
  return status.started_at && Number.isFinite(Date.parse(status.started_at))
    ? new Date(status.started_at).toLocaleString(undefined, {year:'numeric', month:'short', day:'numeric',
        hour:'2-digit', minute:'2-digit', timeZoneName:'short'}) : 'Date unavailable';
}
function historyMatches(status) {
  const query = $('runs-search').value.trim().toLocaleLowerCase(), filter = $('runs-status').value;
  const matchesState = !filter || (filter === 'active' ? ACTIVE.has(status.state)
    : filter === 'failed' ? ['failed', 'launch_failed'].includes(status.state) : status.state === filter);
  return matchesState && [status.run_id, status.seed, runDate(status), status.started_at,
    PROVIDER_NAMES[status.provider], STATE_NAMES[status.state]].join(' ').toLocaleLowerCase().includes(query);
}
function runControls() {
  const selected = runHistory.find(run => run.run_id === selectedHistory);
  const blocked = historyLoading || Boolean(view.submission || pendingRun || runLoading);
  $('runs').disabled = blocked;
  $('runs-load').disabled = blocked || !selected;
  $('runs-check-results').disabled = blocked || !selected || Boolean(view.results && view.results.job);
  $('runs-choice').textContent = runLoading ? 'Loading selected run…' : selected
    ? `Chosen: ${runDate(selected)} · ${PROVIDER_NAMES[selected.provider]} · Seed ${seedText(selected)}`
    : 'Select a run from the table.';
  for (const row of $('runs-rows').querySelectorAll('tr')) {
    const current = Boolean(view.run && view.run.run_id === row.dataset.run);
    row.className = current ? 'current-run' : '';
    for (const marker of row.querySelectorAll('span')) if (marker.dataset.current) marker.hidden = !current;
  }
}
function renderRuns() {
  const rows = runHistory.filter(historyMatches);
  const page = paginate(rows, historyPage); historyPage = page.page;
  const body = $('runs-rows'); body.replaceChildren();
  for (const status of page.pageRows) {
    const row = node('tr'); row.dataset.run = status.run_id;
    const choice = node('input'); choice.type = 'radio'; choice.name = 'saved-run'; choice.value = status.run_id;
    choice.checked = selectedHistory === status.run_id;
    choice.setAttribute('aria-label', `Choose ${PROVIDER_NAMES[status.provider]} run ${runDate(status)}, seed ${seedText(status)}, ${STATE_NAMES[status.state] || status.state}`);
    choice.addEventListener('change', () => { selectedHistory = status.run_id; runControls(); });
    const choose = node('td'); choose.append(choice);
    const started = node('td', runDate(status));
    const identity = node('td'); identity.append(node('b', seedText(status)), node('small', PROVIDER_NAMES[status.provider]));
    const state = node('td', STATE_NAMES[status.state] || status.state);
    const marker = node('span', 'Current run', 'current-label'); marker.dataset.current = 'true'; state.append(marker);
    const count = status.state === 'succeeded' && status.extract && Number.isInteger((status.counters || {}).qualifying)
      ? status.counters.qualifying : null;
    const total = node('td', count == null ? '—' : num(count));
    const detail = node('td'), disclosure = node('details');
    disclosure.append(node('summary', 'Details'), node('code', status.run_id));
    if (status.stop_reason) disclosure.append(node('p', 'Stop reason: ' + status.stop_reason));
    if (status.error) disclosure.append(node('p', status.error.message || String(status.error)));
    detail.append(disclosure); row.append(choose, started, identity, state, total, detail); body.append(row);
  }
  $('runs-note').textContent = historyLoading ? 'Loading saved runs…' : !runHistory.length
    ? `No saved runs for ${PROVIDER_NAMES[provider]}. Start a scan to create one.`
    : !rows.length ? 'No runs match these filters. Clear filters to see all runs.'
    : `${rows.length} of ${runHistory.length} runs · newest first`;
  $('runs-pager').hidden = rows.length <= PAGE_SIZE;
  $('runs-page').textContent = `Page ${page.page + 1} of ${page.totalPages}`;
  $('runs-prev').disabled = page.page === 0;
  $('runs-next').disabled = page.page + 1 >= page.totalPages;
  runControls();
}
async function refreshRuns() {
  fail('s1-error', view.error || '');
  const request = ++historyRequest, which = provider;
  if (historyProvider !== which) { $('runs-search').value = ''; $('runs-status').value = ''; }
  historyProvider = which; runHistory = []; selectedHistory = ''; historyPage = 0; historyLoading = true;
  renderRuns();
  try {
    const history = await api('GET', '/api/runs?provider=' + encodeURIComponent(which));
    if (request !== historyRequest || which !== provider) return;
    runHistory = history.runs.filter(status => status.provider === which)
      .sort((a, b) => String(b.started_at || '').localeCompare(String(a.started_at || '')));
    selectedHistory = view.run && runHistory.some(run => run.run_id === view.run.run_id) ? view.run.run_id : '';
    historyLoading = false; renderRuns();
  } catch (e) {
    if (request !== historyRequest || which !== provider) return;
    historyLoading = false; renderRuns();
    $('runs-note').textContent = 'Saved runs could not be loaded. Use Refresh list to retry.';
    fail('s1-error', e.message);
  }
}
async function loadRun() {
  if ($('runs').disabled || view.submission || pendingRun || runLoading) return;
  const run_id = selectedHistory;
  if (!runHistory.some(run => run.run_id === run_id)) return;
  fail('s1-error', '');
  invalidate();
  const mine = gen, token = {}; runLoading = token; runControls();
  try {
    const status = await api('GET', '/api/scan/' + encodeURIComponent(run_id));
    if (mine !== gen) return;
    if (status.provider !== provider) throw new Error('Run belongs to another provider. Refresh the history list.');
    if (status.run_id !== run_id) throw new Error('Run identity does not match the selection. Refresh the history list.');
    showRun(status);
    loadCodes();
    if (ACTIVE.has(status.state)) startPolling();
    return true;
  } catch (e) {
    if (mine !== gen) return;
    fail('s1-error', e.message);
    if (view.run && ACTIVE.has(view.run.state)) startPolling();
  } finally {
    if (runLoading === token) { runLoading = null; runControls(); }
  }
}

/* ---------- stage 1: every code the run found ---------- */

/* The whole eligible pool, listed before any analysis is asked for: from the
   committed extract once the run succeeds, and until then from what the worker
   has saved so far (`partial`), which is also all that survives a run that
   fails or is cancelled. Nothing here is judged distinct or overlapping --
   that is stage 2's job -- and its order/band controls are a view over this
   list only, exactly as stage 2's are over its own. */
function renderCodes(report) {
  const basis = report.odds_basis;
  const prefs = view.codesPrefs || blankPrefs();
  $('s1-codes').hidden = false;
  $('s1-band').textContent = bandText(report);
  $('s1-band').hidden = false;
  const expired = report.expired_codes || [];
  const rows = view.showExpired ? [...report.codes, ...expired] : report.codes;
  const expiredButton = $('s1-expired');
  expiredButton.hidden = !expired.length;
  expiredButton.textContent = `${view.showExpired ? 'Hide' : 'Show'} ${expired.length} expired code(s)`;
  expiredButton.setAttribute('aria-pressed', String(Boolean(view.showExpired)));
  $('s1-view').hidden = !rows.length;
  $('s1-codes-retry').hidden = true;

  const shown = arrange(rows, basis, prefs);
  const codes = shown.rows.map(([ticket]) => bareCode(ticket));
  const state = view.run ? view.run.state : report.state;
  const lead = !report.partial
    ? `${report.pool_size} upcoming code(s) available for analysis; ${report.saved_count ?? rows.length} saved code(s) in this run, overlapping ones included.`
    : ACTIVE.has(state)
      ? `${report.saved_count ?? report.pool_size} code(s) saved so far, overlapping ones included. The scan is still running; this list grows as more are saved.`
      : `The scan stopped early (${state}). ${report.saved_count ?? report.pool_size} code(s) were saved before it stopped, overlapping ones included.`;
  const parts = rows.length ? [lead, ...viewNote(rows.length, prefs, shown)]
    : [expired.length ? 'No upcoming codes in this snapshot.'
       : report.partial ? 'No codes saved yet.' : 'This run found no codes in its odds band.'];
  if (report.saved_count !== undefined) parts.push(`${report.saved_count} code(s) saved in this snapshot.`);
  if (expired.length) parts.push(`${expired.length} saved code(s) ${view.showExpired ? 'shown' : 'hidden'} after their scheduled kickoff. Use Check result to inspect outcomes.`);
  const otherExcluded = report.excluded.length - expired.length;
  if (otherExcluded > 0) parts.push(`${otherExcluded} ${expired.length ? 'other ' : ''}extract row(s) were excluded from the pool.`);
  $('s1-count').textContent = parts.join(' ');
  const unavailable = 'Unavailable';
  facts('s1-summary', [
    [report.partial ? 'Saved so far' : 'Saved in run', report.saved_count == null ? unavailable : num(report.saved_count)],
    [report.partial ? 'Eligible so far' : 'Eligible now', report.pool_size == null ? unavailable : num(report.pool_size)],
    ['Started', report.expired_codes == null ? unavailable : num(expired.length)],
    ['Other exclusions', num(Math.max(0, otherExcluded))],
    ['Showing', num(shown.rows.length)]]);
  $('s1-bar').hidden = false;
  $('s1-copy-all').onclick = () => Copy.button($('s1-copy-all'), codes.join('\n'),
    {copied: 'Copied', empty: 'No codes match', label: 'The matching booking codes, to copy'});
  $('s1-copy-all').disabled = !codes.length;
  // Copy.button keeps the label it flashes over, even after the flash ends: update
  // that memory always, and the visible text unless a flash is showing over it.
  const copyLabel = codes.length ? `Copy all ${num(codes.length)} matching code${codes.length === 1 ? '' : 's'}` : 'Copy all matching codes';
  const copyButton = $('s1-copy-all'), flashing = copyButton.classList
    && (copyButton.classList.contains('is-copied') || copyButton.classList.contains('is-copy-failed'));
  if (copyButton.dataset && 'copyLabel' in copyButton.dataset) copyButton.dataset.copyLabel = copyLabel;
  if (!flashing) copyButton.textContent = copyLabel;
  $('s1-check-results').textContent = `Check all ${report.saved_count ?? rows.length} saved codes`;
  $('s1-check-results').disabled = !report.saved_count || Boolean(view.results && view.results.job);

  const {pageRows, totalPages, page} = paginate(shown.rows, view.codesPage || 0);
  view.codesPage = page;
  const cards = $('s1-cards');
  cards.replaceChildren();
  for (const [ticket] of pageRows) {
    cards.append(ticketCard(bareCode(ticket),
      [['Legs', String(ticket.num_legs)], ['Odds', num(oddsOf(ticket, basis))],
       ...(ticket.history_note ? [['History', 'Scheduled kickoff has passed.']] : [])],
      () => legTable(ticket.legs), ticket.row_id ? () => checkSavedCode(ticket.row_id, report.run_id) : null));
  }
  renderPager('s1', page, totalPages, shown.rows.length);
}

function showCodesError() {
  if (!view.codesError) return;
  $('s1-codes').hidden = false;
  fail('s1-codes-error', view.codesError);
  $('s1-codes-retry').hidden = false;
}

/* Asked whenever the run has something new to list: once when it succeeds,
   and before that each time its qualifying count or state moves, so a scan
   still going -- or one that failed or was cancelled -- shows what it saved.
   codesAsked remembers the last question, answered or refused, so the same
   one is never put twice. */
function codesStamp(run) { return [run.run_id, run.state, (run.counters || {}).qualifying].join('|'); }

async function loadCodes() {
  const run = view.run;
  if (!run || analysisRequest.s1) return;
  const done = run.state === 'succeeded';
  if (!done && !((run.counters || {}).qualifying > 0)) return;
  const stamp = codesStamp(run);
  if (view.codesAsked === stamp) return;
  view.codesAsked = stamp;
  const mine = gen, run_id = run.run_id;
  const request = {};
  analysisRequest.s1 = request;
  view.codesError = null;
  fail('s1-codes-error', '');
  $('s1-codes-retry').hidden = true;
  try {
    const report = await api('POST', '/api/codes', {run_id});
    if (mine !== gen || !view.run || view.run.run_id !== run_id) return;
    view.codes = report;
    renderCodes(report);
  } catch (e) {
    if (mine !== gen || !view.run || view.run.run_id !== run_id) return;
    // 409 is the server saying this run has nothing saved yet, which is an answer,
    // not a failure. Anything else leaves the saved codes unavailable -- not zero --
    // for a finished, failed or cancelled run alike, and Retry asks again.
    if (e.status === 409 && !done) return;
    view.codesError = `Could not list this run's codes: ${e.message}` + (done ? ''
      : ' Codes saved so far may still exist; this is not a count of zero.');
    showCodesError();
  } finally {
    if (analysisRequest.s1 === request) {
      analysisRequest.s1 = null;
      if (mine === gen) loadCodes();     // the run may have moved on meanwhile
    }
  }
}

/* Historical results always use the saved population, independently of Stage 1. */
function resetResults() {
  view.results = null;
  $('results-body').hidden = true;
  $('results-cards').replaceChildren();
  $('results-job').textContent = 'Choose a saved run, then open its results.';
  $('results-partial').checked = false;
  $('results-filter').value = '';
  fail('results-error', '');
  resultsControls();
}
function resultsControls() {
  const run = view.run;
  const busy = Boolean(view.results && view.results.job);
  const usable = run && (run.state === 'succeeded' || $('results-partial').checked);
  $('results-open').disabled = !usable || busy;
  $('results-check-all').disabled = !usable || busy;
  $('results-refresh').disabled = !usable || busy || !view.results || !view.results.summary;
  $('results-cancel').disabled = !busy;
  $('results-partial').disabled = busy;
  $('s1-check-results').disabled = !run || busy || !view.codes || !view.codes.saved_count;
  runControls();
  for (const button of $('s1-cards').querySelectorAll('button[data-result-action]')) button.disabled = busy;
  for (const button of $('results-cards').querySelectorAll('button')) button.disabled = busy;
}
function resultCurrent(state, mine) {
  return gen === mine && view.results === state && view.run && view.run.run_id === state.run;
}
function resultReason(value) { return (value || '').replaceAll('_', ' '); }
function resultRate(label, rate) {
  return rate ? `${label}: ${rate.value == null ? 'unavailable' : (rate.value * 100).toFixed(2) + '%'} (${rate.numerator}/${rate.denominator})` : '';
}
async function openResults() {
  if (!view.run) return;
  const state = {run: view.run.run_id, partial: $('results-partial').checked,
    page: 0, revision: null, snapshot: null, summary: null, job: null, loading: 0};
  view.results = state;
  const mine = gen;
  fail('results-error', '');
  $('results-job').textContent = 'Loading saved results…';
  try {
    try { state.summary = await api('GET', '/api/results/' + encodeURIComponent(state.run)); }
    catch (e) { if (e.code !== 'report_not_found') throw e; }
    if (!resultCurrent(state, mine)) return;
    if (state.summary) state.revision = state.summary.revision_id;
    await loadResultPage(state, mine);
    if (resultCurrent(state, mine)) $('results-job').textContent = `Showing saved results for ${state.run}.`;
  } catch (e) { if (resultCurrent(state, mine)) fail('results-error', e.message); }
  if (resultCurrent(state, mine)) resultsControls();
}
async function loadResultPage(state = view.results, mine = gen) {
  if (!state) return;
  const request = ++state.loading;
  const query = new URLSearchParams({offset: String(state.page * PAGE_SIZE), limit: String(PAGE_SIZE)});
  if (state.revision) query.set('revision', state.revision);
  else {
    query.set('view', 'saved'); query.set('allow_partial', String(state.partial));
    if (state.snapshot) query.set('snapshot', state.snapshot);
  }
  const filter = $('results-filter').value;
  if (filter) query.set('status', filter);
  if (state.focusRowId) query.set('row_id', state.focusRowId);
  const page = await api('GET', '/api/results/' + encodeURIComponent(state.run) + '/codes?' + query);
  if (!resultCurrent(state, mine) || request !== state.loading) return;
  state.snapshot = page.input_digest || state.snapshot;
  state.total = page.total;
  const c = state.summary && state.summary.counts;
  $('results-body').hidden = false;
  $('results-summary').textContent = c
    ? `${c.total} saved codes · ${c.won} won · ${c.lost} lost · ${c.void} void · ${c.pending} pending · ${c.unresolved} unresolved (including unchecked). ${state.summary.checked_count || 0} checked.`
    : `${page.total} saved codes. Results have not been checked.`;
  const rates = state.summary && state.summary.percentages;
  $('results-rates').textContent = rates ? [resultRate('Confirmed wins / all saved codes', rates.confirmed_win_rate),
    resultRate('Wins among decided wins/losses', rates.decided_win_loss_rate),
    resultRate('Classification coverage', rates.classification_coverage)].filter(Boolean).join(' · ')
    + ' Unknown outcomes are not losses. These historical rates are not a forecast.' : '';
  $('results-evidence').textContent = (state.summary ? `Checked ${state.summary.created_at}. Revision ${state.revision}. ` : '')
    + ((state.summary ? state.summary.population_complete : page.population_complete) === false ? 'Incomplete saved history. ' : '')
    + (state.summary && Object.keys(state.summary.retrieval_errors || {}).length ? `${Object.keys(state.summary.retrieval_errors).length} date(s) have source limitations. ` : '')
    + (state.summary && state.summary.leg_counts ? 'Selection outcomes: ' + Object.entries(state.summary.leg_counts).map(([status, count]) => `${count} ${status}`).join(', ') + '. ' : '')
    + 'Ticket types, missing source access and unsupported markets can prevent a confirmed ticket result; inspect each code for its leg outcomes.';
  const cards = $('results-cards'); cards.replaceChildren();
  for (const row of page.rows) {
    const original = row.original_ticket || {};
    const card = node('article', undefined, 'panel');
    card.append(node('h3', `${row.ticket_id || original.code || 'Saved row'} · ${row.checked ? row.status : 'not checked'}`));
    if (row.reason) card.append(node('p', resultReason(row.reason)));
    if (row.proxy_all_legs_won) card.append(node('p', 'All selected legs won. This is not a confirmed ticket win while its structure is unverified.'));
    const button = node('button', row.checked ? 'Check again' : 'Check result'); button.type = 'button';
    button.disabled = Boolean(state.job);
    button.onclick = () => checkResults(row.row_id);
    card.append(button);
    const details = node('details'), summary = node('summary', 'Original picks and outcome evidence'); details.append(summary);
    if (row.structure_verification && row.structure_verification.evidence)
      details.append(node('p', 'Ticket structure evidence: ' + row.structure_verification.evidence));
    const rawLegs = Array.isArray(original.selections) ? original.selections.filter(s => s && typeof s === 'object') : [];
    const legs = row.legs && row.legs.length ? row.legs : rawLegs.map(selection => ({selection: {
      event_display: selection.event, league_display: selection.league, market: selection.market,
      pick: selection.pick, specifier: selection.specifier, kickoff: {raw: selection.kickoff}}, reason: 'not_checked'}));
    for (const leg of legs) {
      const selection = leg.selection || {};
      details.append(node('h4', selection.event_display || leg.leg_id || 'Selection'));
      details.append(node('p', [selection.league_display, selection.market, selection.pick, selection.specifier,
        selection.kickoff && selection.kickoff.raw].filter(Boolean).join(' · ')));
      details.append(node('p', `${leg.status || 'not checked'}${leg.reason ? ' · ' + resultReason(leg.reason) : ''}`));
      const f = leg.fixture;
      if (f) {
        const score = f.score;
        details.append(node('p', `${f.home_team} — ${f.away_team}: ${resultReason(f.state)}; regulation ${score ? score.home + '–' + score.away : 'unavailable'}`
          + (score && score.ht_home != null ? `; half-time ${score.ht_home}–${score.ht_away}` : '')));
        details.append(node('p', `Source ${f.provenance.source} / ${f.provenance.source_fixture_id}, observed ${f.provenance.fetched_at}. Match ${leg.match_tier}; rule ${leg.rule_id}.`));
      }
      if (leg.retrieval_error) details.append(node('p', leg.retrieval_error.message));
      for (const candidate of leg.candidates || []) details.append(node('p', `Review candidate: ${candidate.event}, ${candidate.kickoff} (${candidate.source} / ${candidate.source_fixture_id}).`));
    }
    if (!legs.length) details.append(node('p', 'Original selections are unavailable or malformed. This row remains in the saved population.'));
    card.append(details); cards.append(card);
  }
  if (!page.rows.length) cards.append(node('p', page.total ? 'No codes match this filter in this report. Unresolved codes may change as evidence becomes available.' : 'This snapshot contains no saved codes.'));
  $('results-show-all').hidden = !state.focusRowId;
  const filtered = page.filtered_total == null ? page.total : page.filtered_total;
  $('results-page').textContent = `Page ${state.page + 1} of ${Math.max(1, Math.ceil(filtered / PAGE_SIZE))} · ${filtered} matching codes · ${page.total} saved in run`;
  $('results-prev').disabled = state.page === 0;
  $('results-next').disabled = (state.page + 1) * PAGE_SIZE >= filtered;
}
async function checkResults(rowId = null, refresh = false) {
  const requestedRun = view.run && view.run.run_id, requestedGeneration = gen;
  if (!view.results) { await openResults(); if (!view.results || !view.results.total) return; }
  if (gen !== requestedGeneration || !view.run || view.run.run_id !== requestedRun) return;
  const state = view.results, mine = gen;
  if (state.job) return;
  state.focusRowId = rowId;
  state.page = 0;
  $('results-filter').value = '';
  const body = {refresh, allow_partial: state.partial};
  if (rowId) body.row_id = rowId;
  state.job = 'submitting'; resultsControls(); fail('results-error', '');
  $('results-job').textContent = rowId ? 'Checking this saved code…' : 'Checking all saved codes…';
  try {
    const job = await api('POST', '/api/results/' + encodeURIComponent(state.run), body);
    if (!resultCurrent(state, mine)) return;
    state.job = job.job_id; resultsControls();
    await pollResultJob(state, mine);
  } catch (e) {
    if (!resultCurrent(state, mine)) return;
    state.job = null; resultsControls(); fail('results-error', e.message);
  }
}
async function pollResultJob(state, mine) {
  if (!resultCurrent(state, mine) || !state.job) return;
  try {
    const job = await api('GET', '/api/results/jobs/' + encodeURIComponent(state.job));
    if (!resultCurrent(state, mine)) return;
    fail('results-error', '');
    $('results-job').textContent = `Result check ${job.state}${job.cancel_requested ? ' · cancellation requested' : ''}.`;
    if (['succeeded', 'failed', 'cancelled'].includes(job.state)) {
      state.job = null; resultsControls();
      if (job.state === 'succeeded') {
        state.summary = await api('GET', '/api/results/' + encodeURIComponent(state.run));
        if (!resultCurrent(state, mine)) return;
        state.revision = state.summary.revision_id;
        await loadResultPage(state, mine);
      } else if (job.error) fail('results-error', job.error.message || job.error.code);
      return;
    }
    if (job.progress && job.progress.fetch_job_id) {
      const fetchJob = await api('GET', '/api/results/jobs/' + encodeURIComponent(job.progress.fetch_job_id));
      if (!resultCurrent(state, mine)) return;
      const progress = fetchJob.progress || {};
      $('results-job').textContent += ` Dates ${(progress.tasks_completed || []).length}/${progress.tasks_total || '?'}; request attempts ${progress.requests_used || 0}.`;
    }
    setTimeout(() => pollResultJob(state, mine), 1500);
  } catch (e) {
    if (resultCurrent(state, mine)) {
      fail('results-error', e.message + ' Retrying job status…');
      setTimeout(() => pollResultJob(state, mine), 3000);
    }
  }
}
function reloadResultPage() {
  const state = view.results, mine = gen;
  return loadResultPage(state, mine).catch(e => { if (resultCurrent(state, mine)) fail('results-error', e.message); });
}
async function checkSavedCode(rowId = null, expectedRun = null) {
  const run = view.run;
  if (!run || (expectedRun && run.run_id !== expectedRun)) return;
  if (run.state !== 'succeeded' && !$('results-partial').checked) {
    fail('results-error', 'Enable “Use incomplete saved history” before checking this stopped run.');
    navigateSection('results');
    return;
  }
  if (view.results && view.results.job) {
    navigateSection('results');
    return;
  }
  navigateSection('results');
  await checkResults(rowId);
}
$('s1-check-results').addEventListener('click', () => checkSavedCode());
$('runs-check-results').addEventListener('click', async () => {
  const selected = selectedHistory, which = provider;
  if (!selected || $('runs').disabled || view.submission) return;
  const loaded = await loadRun();
  if (loaded && provider === which && view.run && view.run.run_id === selected) await checkSavedCode(null, selected);
});
$('results-show-all').addEventListener('click', () => {
  if (!view.results) return;
  view.results.focusRowId = null; view.results.page = 0; $('results-filter').value = ''; reloadResultPage();
});
$('results-open').addEventListener('click', openResults);
$('results-check-all').addEventListener('click', () => checkResults());
$('results-refresh').addEventListener('click', () => checkResults(null, true));
$('results-partial').addEventListener('change', () => { view.results = null; $('results-body').hidden = true; resultsControls(); });
$('results-filter').addEventListener('change', () => { if (view.results) { view.results.focusRowId = null; view.results.page = 0; reloadResultPage(); } });
for (const [id, change] of [['results-prev', -1], ['results-next', 1]]) $(id).addEventListener('click', () => {
  if (view.results) { view.results.page += change; reloadResultPage(); }
});
$('results-cancel').addEventListener('click', async () => {
  const state = view.results, mine = gen;
  if (!state || !state.job || state.job === 'submitting') return;
  try { await api('POST', '/api/results/jobs/' + encodeURIComponent(state.job) + '/cancel', {}); }
  catch (e) { if (resultCurrent(state, mine)) fail('results-error', e.message); }
});

/* ---------- stage 2: distinct ---------- */

const stage3Note = report => `Ready on the same eligible pool as stage 2 (${report.pool_size} code(s)).`;

function clearAnalysis() {               // the DOM only; the remembered reports survive
  $('s1-cards').replaceChildren();
  for (const id of ['s1-codes', 's1-band', 's1-summary', 's1-codes-retry', 's1-view', 's1-bar', 's1-pager']) $(id).hidden = true;
  fail('s1-codes-error', '');
  $('s2-cards').replaceChildren();
  for (const id of ['s2-facts', 's2-band', 's2-view', 's2-bar', 's2-more', 's2-pager']) $(id).hidden = true;
  fail('s2-error', '');
  lock('s3', true, 'Locked until stage 2 has a result for this run.');
  $('s3-cards').replaceChildren();
  for (const id of ['s3-facts', 's3-band', 's3-bar', 's3-more', 's3-subset']) $(id).hidden = true;
  fail('s3-error', '');
}

function resetAnalysis() {               // the analysis is gone, not just off screen
  view.codes = null;
  view.codesAsked = null;
  view.codesError = null;
  view.showExpired = false;
  view.stage2 = null;
  view.stage3 = null;
  view.prefs = blankPrefs();             // a new analysis starts unfiltered
  view.codesPrefs = blankPrefs();
  view.page = 0;
  view.codesPage = 0;
  writePrefs('s2', view.prefs);
  writePrefs('s1', view.codesPrefs);
  clearAnalysis();
}

function legTable(legs) {
  const scroll = node('div', undefined, 'scroll'), table = node('table');
  table.append(head(['Match / league', 'Market', 'Pick', 'Odds']));
  const body = node('tbody');
  for (const leg of legs) {
    const row = node('tr'), match = node('td', leg.event || 'Unknown match');
    match.append(node('small', [leg.league, leg.kickoff].filter(Boolean).join(' · ')));
    row.append(match, node('td', leg.market || '—'), node('td', leg.pick || '—'), node('td', num(leg.odds), 'n'));
    body.append(row);
  }
  table.append(body);
  scroll.append(table);
  return scroll;
}

function ticketCard(code, columns, detail, onCheck = null) {
  const card = node('details', undefined, 'ticket'), summary = node('summary');
  const first = node('span', undefined, 'ticket-code');
  first.append(node('small', 'Booking code'), node('strong', code));
  summary.append(first);
  for (const [name, value] of columns) {
    const cell = node('span', undefined, name === 'History' ? 'ticket-history' : 'ticket-metric');
    cell.append(node('small', name), node('span', value, 'n'));
    if (name !== 'History') summary.append(cell);
  }
  const actions = node('span', undefined, 'ticket-actions');
  if (onCheck) {
    const check = node('button', 'Check result'); check.type = 'button';
    check.setAttribute('data-result-action', 'true');
    check.disabled = Boolean(view.results && view.results.job);
    check.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); onCheck(); });
    actions.append(check);
  }
  const button = node('button', 'Copy');
  button.type = 'button';
  button.addEventListener('click', event => {
    event.preventDefault();          // the button sits inside <summary>
    event.stopPropagation();
    Copy.button(button, code, {label: 'Booking code to copy'});
  });
  actions.append(button, node('span', '›', 'chev'));
  summary.append(actions);
  for (const [name, value] of columns.filter(([name]) => name === 'History')) {
    const note = node('span', undefined, 'ticket-history');
    note.append(node('small', name), node('span', value)); summary.append(note);
  }
  card.append(summary);
  card.addEventListener('toggle', () => {
    if (!card.open || card.dataset.loaded) return;
    card.dataset.loaded = 'y';
    const box = node('div', undefined, 'detail');
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', `Selections for booking code ${code}`);
    box.append(detail());
    card.append(box);
  });
  return card;
}

/* The order/band controls are a VIEW over the analysis, never the analysis.
   Distinctness is a property of the whole eligible pool -- a code can be
   distinct only because the code it overlaps is in that pool -- so narrowing
   the pool first would quietly change what "distinct" means. These controls
   decide what is listed and copied here and nothing else: stage 3 and the
   stage-2 subset check keep reading the full report. */
function readPrefs(stage) {
  const bound = id => {
    const raw = $(id).value.trim();
    if (raw === '') return null;
    const value = Number(raw);
    return Number.isFinite(value) && value >= 0 ? value : null;
  };
  return {order: $(stage + '-order').value === 'desc' ? 'desc' : 'asc',
          min: bound(stage + '-min'), max: bound(stage + '-max'),
          minLegs: bound(stage + '-min-legs'), maxLegs: bound(stage + '-max-legs'),
          search: $(stage + '-search') ? $(stage + '-search').value.trim() : ''};
}

function writePrefs(stage, prefs) {
  $(stage + '-order').value = prefs.order === 'desc' ? 'desc' : 'asc';
  $(stage + '-min').value = prefs.min == null ? '' : String(prefs.min);
  $(stage + '-max').value = prefs.max == null ? '' : String(prefs.max);
  $(stage + '-min-legs').value = prefs.minLegs == null ? '' : String(prefs.minLegs);
  $(stage + '-max-legs').value = prefs.maxLegs == null ? '' : String(prefs.maxLegs);
  if ($(stage + '-search')) $(stage + '-search').value = prefs.search || '';
}

function arrange(tickets, basis, prefs) {
  const rows = tickets.map((ticket, index) => [ticket, index, oddsOf(ticket, basis)]);
  const oddsBanded = prefs.min != null || prefs.max != null;
  const legsBanded = prefs.minLegs != null || prefs.maxLegs != null;
  const needle = (prefs.search || '').toLocaleLowerCase();
  // A banded list can only contain codes whose odds are known, so unpriced
  // ones drop out here; the count line says how many did. Likewise a code
  // with no known leg count cannot match a legs band.
  const banded = !oddsBanded && !legsBanded && !needle ? rows.slice()
    : rows.filter(([ticket, , odds]) =>
        (!needle || bareCode(ticket).toLocaleLowerCase().includes(needle))
        && (!oddsBanded || (odds != null
          && (prefs.min == null || odds >= prefs.min)
          && (prefs.max == null || odds <= prefs.max)))
        && (!legsBanded || (Number.isFinite(ticket.num_legs)
          && (prefs.minLegs == null || ticket.num_legs >= prefs.minLegs)
          && (prefs.maxLegs == null || ticket.num_legs <= prefs.maxLegs))));
  const sign = prefs.order === 'desc' ? -1 : 1;
  banded.sort((a, b) => {
    if (a[2] === b[2]) return a[1] - b[1];       // stable within equal odds
    if (a[2] == null) return 1;                  // unpriced last in either order
    if (b[2] == null) return -1;
    return sign * (a[2] - b[2]);
  });
  return {rows: banded, banding: oddsBanded || legsBanded || Boolean(needle), oddsBanded, legsBanded, needle,
          droppedUnpriced: oddsBanded ? rows.filter(row => row[2] == null).length : 0};
}

/* What the order/band controls are doing to a list of `total` codes. */
function viewNote(total, prefs, shown) {
  const order = prefs.order === 'desc' ? 'highest odds first' : 'lowest odds first';
  if (!shown.banding) return [`Listed ${order}.`];
  if (prefs.min != null && prefs.max != null && prefs.min > prefs.max)
    return [`"From odds" is above "To odds", so nothing matches.`];
  if (prefs.minLegs != null && prefs.maxLegs != null && prefs.minLegs > prefs.maxLegs)
    return [`"Min legs" is above "Max legs", so nothing matches.`];
  const range = (lo, hi) => lo == null ? `up to ${num(hi)}`
    : hi == null ? `from ${num(lo)} up` : `${num(lo)} to ${num(hi)}`;
  const bands = [];
  if (shown.oddsBanded) bands.push(`odds ${range(prefs.min, prefs.max)}`);
  if (shown.legsBanded) bands.push(`${range(prefs.minLegs, prefs.maxLegs)} legs`);
  if (shown.needle) bands.push(`code containing "${prefs.search}"`);
  const parts = [`Showing ${shown.rows.length} of ${total} with ${bands.join(' and ')}, ${order}.`];
  if (shown.droppedUnpriced)
    parts.push(`${shown.droppedUnpriced} unpriced code(s) cannot match a band and are hidden.`);
  return parts;
}

function listNote(report, prefs, shown) {
  const parts = [`${report.distinct_codes} code(s) share zero game+option with any other code in the pool.`,
                 ...viewNote(report.distinct_codes, prefs, shown)];
  parts.push(`${report.excluded.length} extract row(s) were excluded from the pool.`);
  return parts.join(' ');
}

function renderDistinct(report) {
  const basis = report.odds_basis;
  const prefs = view.prefs || blankPrefs();
  facts('s2-facts', [['In extract', num(report.input_count)], ['Eligible pool', num(report.pool_size)],
                     ['Distinct', num(report.distinct_codes)], ['Overlapping', num(report.overlapping_codes)],
                     ['Shared legs', num(report.shared_legs)]]);
  $('s2-band').textContent = bandText(report);
  $('s2-band').hidden = false;
  $('s2-view').hidden = false;

  const shown = arrange(report.distinct, basis, prefs);
  const codes = shown.rows.map(([ticket]) => bareCode(ticket));
  $('s2-count').textContent = listNote(report, prefs, shown);
  $('s2-bar').hidden = false;
  // Copy what is on screen: a filtered list that copied all 94 would be a lie.
  $('s2-copy-all').onclick = () => Copy.button($('s2-copy-all'), codes.join('\n'),
    {copied: 'Copied', empty: 'No codes shown', label: 'The distinct booking codes shown, to copy'});
  $('s2-copy-all').disabled = !codes.length;

  const {pageRows, totalPages, page} = paginate(shown.rows, view.page || 0);
  view.page = page;
  const cards = $('s2-cards');
  cards.replaceChildren();
  for (const [ticket] of pageRows) {
    cards.append(ticketCard(bareCode(ticket),
      [['Legs', String(ticket.num_legs)], ['Odds', num(oddsOf(ticket, basis))]],
      () => legTable(ticket.legs)));
  }
  renderPager('s2', page, totalPages, shown.rows.length);

  const top = node('table');
  top.append(head(['Game + option', 'In codes']));
  const body = node('tbody');
  for (const row of report.top_legs) {
    const tr = node('tr');
    tr.append(node('td', row.leg), node('td', String(row.in_codes), 'n'));
    body.append(tr);
  }
  top.append(body);
  $('s2-top').replaceChildren(top);
  $('s2-more').hidden = !report.top_legs.length;
}

async function findDistinct() {
  if (!view.run || analysisRequest.s2) return;
  fail('s2-error', '');
  const mine = gen, run_id = view.run.run_id;
  const request = {};
  analysisRequest.s2 = request;
  $('s2-run').disabled = true;
  try {
    const report = await api('POST', '/api/distinct', {run_id});
    // A report for a run the user has moved off is not this page's state any more.
    if (mine !== gen || !view.run || view.run.run_id !== run_id) return;
    view.stage2 = report;
    renderDistinct(report);
    lock('s3', false, stage3Note(report));
  } catch (e) {
    if (mine === gen) fail('s2-error', e.message);
  } finally {
    if (analysisRequest.s2 === request) {
      analysisRequest.s2 = null;
      $('s2-run').disabled = false;
    }
  }
}

/* ---------- stage 3: de-correlate ---------- */

function sharedTable(rows) {
  const scroll = node('div', undefined, 'scroll'), table = node('table');
  table.append(head(['Shared game + option', 'Market', 'Pick', 'Also in']));
  const body = node('tbody');
  for (const row of rows) {
    const tr = node('tr');
    tr.append(node('td', row.event || '—'), node('td', row.market || '—'), node('td', row.pick || '—'),
              node('td', (row.shared_with_selected || row.at_capacity_with || []).join(', ') || '—'));
    body.append(tr);
  }
  table.append(body);
  scroll.append(table);
  return scroll;
}

/* The F2b invariant, checked in front of the user rather than asserted in prose. */
function subsetCheck(report) {
  const box = $('s3-subset');
  const selection = report.selection, stage2 = view.stage2;
  if (!stage2 || stage2.run_id !== report.run_id || selection.target !== null || selection.max_exposure !== 1) {
    box.hidden = true;
    return;
  }
  const selected = new Set(selection.selected_codes);
  const missing = stage2.distinct.map(bareCode).filter(code => !selected.has(code));
  box.replaceChildren(node('span', 'Stage-2 check: '),
    node('span', missing.length
      ? `${missing.length} of ${stage2.distinct_codes} fully-distinct code(s) are missing from this cap-1 selection: `
        + missing.slice(0, 10).join(', ')
      : `all ${stage2.distinct_codes} fully-distinct code(s) are inside this cap-1 selection `
        + `(${selection.selected_count} selected; counts need not be equal).`,
      missing.length ? 'bad' : 'ok'));
  box.hidden = false;
}

function renderSelection(report) {
  const selection = report.selection, codes = selection.selected_codes;
  const range = selection.odds_range || {};
  facts('s3-facts', [['Full pool', num(report.pool_size)], ['Selected', num(selection.selected_count)],
                     ['Left out', num(selection.rejected_count)],
                     ['Cap', num(selection.max_exposure)],
                     ['Realized max', num(selection.realized_max_exposure)],
                     ['Target', selection.target == null ? 'none' : num(selection.target)]]);
  $('s3-band').textContent = `${bandText(report)} Selected odds ${num(range.min)} to ${num(range.max)} `
    + `(median ${num(range.median)}; ${num(range.priced_codes)} priced, ${num(range.unpriced_codes)} unpriced).`;
  $('s3-band').hidden = false;
  $('s3-count').textContent = selection.uniqueness_buckets.map(b => `${b.code_count} ${b.tier}`).join(' · ')
    || 'No codes selected.';
  $('s3-bar').hidden = false;
  $('s3-copy-all').onclick = () => Copy.button($('s3-copy-all'), codes.join('\n'),
    {copied: 'All copied', label: 'All selected booking codes to copy'});
  $('s3-copy-all').disabled = !codes.length;

  const cards = $('s3-cards');
  cards.replaceChildren();
  for (const row of selection.selected) {
    cards.append(ticketCard(row.code,
      [['Legs', String(row.num_legs)], ['Odds', num(row.parsed_leg_product)],
       ['Shared', String(row.shared_leg_count)]],
      () => row.shared_legs.length ? sharedTable(row.shared_legs)
        : node('p', 'No game+option in this code is shared with another selected code.', 'notice')));
  }

  const table = node('table');
  table.append(head(['Code', 'Reason', 'Blocked by']));
  const body = node('tbody');
  for (const row of selection.rejected) {
    const tr = node('tr'), why = node('td');
    why.append(node('span', row.reason === 'target_reached' ? 'target reached' : 'exposure cap'));
    const blocking = (row.blocking || []).map(b =>
      `${b.event} :: ${b.market} = ${b.pick} (full with ${(b.at_capacity_with || []).join(', ')})`);
    tr.append(node('td', row.code), why, node('td', blocking.join(' · ') || '—'));
    body.append(tr);
  }
  table.append(body);
  $('s3-rejected').replaceChildren(table);
  $('s3-more').hidden = !selection.rejected.length;
  subsetCheck(report);
}

async function decorrelate() {
  if (!view.run || analysisRequest.s3) return;
  fail('s3-error', '');
  const run_id = view.run.run_id, body = {run_id};
  const exposure = Number($('max_exposure').value);
  if (!Number.isInteger(exposure) || exposure < 1) {      // the API refuses it too
    fail('s3-error', 'Max exposure must be a whole number of at least 1 (1 = fully independent).');
    return;
  }
  body.max_exposure = exposure;
  const target = $('target').value.trim();
  if (target !== '') {
    const wanted = Number(target);
    if (!Number.isInteger(wanted) || wanted < 1) {
      fail('s3-error', 'Target count must be a whole number of at least 1, or blank for no limit.');
      return;
    }
    body.target = wanted;
  }
  const mine = gen;
  const request = {};
  analysisRequest.s3 = request;
  $('s3-run').disabled = true;
  try {
    const report = await api('POST', '/api/decorrelate', body);
    if (mine !== gen || !view.run || view.run.run_id !== run_id) return;
    view.stage3 = report;
    renderSelection(report);
  } catch (e) {
    if (mine === gen) fail('s3-error', e.message);
  } finally {
    if (analysisRequest.s3 === request) {
      analysisRequest.s3 = null;
      $('s3-run').disabled = false;
    }
  }
}

/* ---------- wiring ---------- */

$('scan-form').addEventListener('submit', startScan);
$('s1-cancel').addEventListener('click', cancelScan);
$('s1-refresh').addEventListener('click', refreshScanStatus);
$('provider').addEventListener('change', switchProvider);
$('fresh_seed').addEventListener('change', seedHint);
$('seed-typed').addEventListener('change', seedHint);
$('runs-refresh').addEventListener('click', refreshRuns);
for (const [id, event] of [['runs-search', 'input'], ['runs-status', 'change']]) $(id).addEventListener(event, () => { historyPage = 0; renderRuns(); });
$('runs-reset').addEventListener('click', () => { $('runs-search').value = ''; $('runs-status').value = ''; historyPage = 0; renderRuns(); $('runs-search').focus(); });
for (const [id, change] of [['runs-prev', -1], ['runs-next', 1]]) $(id).addEventListener('click', () => { historyPage += change; renderRuns(); });
$('runs-load').addEventListener('click', async () => {
  if (await loadRun()) {
    if (section === 'runs') navigateSection(runReturn);
    else syncRoute(true);
  }
});
$('s2-run').addEventListener('click', findDistinct);

/* Re-listing is a repaint of the report already in hand: no request is made,
   so changing the order or the band can never race a reply or unlock a stage. */
function relist() {
  view.prefs = readPrefs('s2');
  view.page = 0;
  if (view.stage2) renderDistinct(view.stage2);
}
for (const id of ['s2-order', 's2-min', 's2-max', 's2-min-legs', 's2-max-legs']) $(id).addEventListener('input', relist);
$('s2-view-reset').addEventListener('click', () => {
  view.prefs = blankPrefs();
  writePrefs('s2', view.prefs);
  view.page = 0;
  if (view.stage2) renderDistinct(view.stage2);
});
function relistCodes() {
  view.codesPrefs = readPrefs('s1');
  view.codesPage = 0;
  if (view.codes) renderCodes(view.codes);
}
$('s1-expired').addEventListener('click', () => {
  view.showExpired = !view.showExpired;
  view.codesPage = 0;
  if (view.codes) renderCodes(view.codes);
});
for (const id of ['s1-search', 's1-order', 's1-min', 's1-max', 's1-min-legs', 's1-max-legs']) $(id).addEventListener('input', relistCodes);
$('s1-codes-retry').addEventListener('click', () => { view.codesAsked = null; loadCodes(); });
$('s1-view-reset').addEventListener('click', () => {
  view.codesPrefs = blankPrefs();
  writePrefs('s1', view.codesPrefs);
  view.codesPage = 0;
  if (view.codes) renderCodes(view.codes);
});
$('s1-prev').addEventListener('click', () => {
  view.codesPage = Math.max(0, (view.codesPage || 0) - 1);
  if (view.codes) renderCodes(view.codes);
});
$('s1-next').addEventListener('click', () => {
  view.codesPage = (view.codesPage || 0) + 1;
  if (view.codes) renderCodes(view.codes);
});
$('s2-prev').addEventListener('click', () => {
  view.page = Math.max(0, (view.page || 0) - 1);
  if (view.stage2) renderDistinct(view.stage2);
});
$('s2-next').addEventListener('click', () => {
  view.page = (view.page || 0) + 1;
  if (view.stage2) renderDistinct(view.stage2);
});
$('s3-run').addEventListener('click', decorrelate);
$('max_exposure').addEventListener('input', () => {
  if (Number($('max_exposure').value) < 1) $('max_exposure').value = '1';
});
window.addEventListener('beforeunload', stopPolling);
seedHint();
refreshRuns();
initializeShell();
