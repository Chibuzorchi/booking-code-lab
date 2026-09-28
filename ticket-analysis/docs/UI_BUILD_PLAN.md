# Dynamic UI Build Plan — Harvest → Distinct → De-correlate

Status: DRAFT FOR REVIEW (do not build yet) — updated 2026-09-27 after a second,
code-level review that found backend-contract issues (see BLOCKERs in §5).
Scope: one provider at a time (bet9ja first). Paper-only: the UI never stakes real money.

## 1. What the UI is
A local web app (served by an extended `portfolio/serve.py`) with three stages the
user drives in order. Each stage is a backend call against code we already have.

    Stage 1  HARVEST      form: min odd, max odd, how many codes  ->  scan_code.py --scan
    Stage 2  DISTINCT     one click                                ->  uniqueness.analyze()  (the "42")
    Stage 3  DE-CORRELATE knobs: max shared, target count          ->  selection.select_subset()

A stage unlocks only when the previous one has a result. Stage 1's output is the
input to Stage 2; Stage 2's pool is the input to Stage 3.

## 2. How it connects to the backend (the seam)
Today `serve.py` only does GET and rebuilds a static report. We add a small JSON API
to the same server and a background job runner (scans take minutes, so they cannot
run inside a request). No new frameworks — stdlib http.server + a run registry on
disk. IMPORTANT: each scan/booking runs as a SEPARATE WORKER PROCESS, not a thread
(see BLOCKER L10): both engines load their .env with load_dotenv(override=False) into
the process environment and insert their own root on sys.path, so two providers cannot
share one interpreter. The job runner shells out to the provider's scan_code.py, which
is exactly how scans are run today.

    POST /api/scan            body {provider,min_odds?,max_odds?,want_count?,try_budget?,seed?,fresh_seed?}
                              -> {run_id}                 (starts a background scan)
    GET  /api/scan/<run_id>   -> {state,tried,valid,qualifying,extract_path,error?}
    POST /api/distinct        body {run_id}               -> phase-1 report + the distinct codes
    POST /api/decorrelate     body {run_id,max_exposure,target?} -> phase-3 shortlist
    GET  /api/runs            -> list of past runs (for reload / history)
    GET  /                    -> the 3-stage UI page

Each run gets a `run_id` and a folder `results/runs/<run_id>/` holding exactly that
run's extract, so a run is self-contained (see Loophole L4).

Backend mapping, field by field:

    UI field            -> backend
    min odd  (optional) -> scan_code --min-odds        (blank = 0 = no floor)
    max odd  (optional) -> scan_code --max-odds-cap    (blank = default ceiling, NOT infinite; see L2)
    how many codes      -> MAX_QUALIFYING output cap   (blank/0 = uncapped, bounded by try_budget)
    (fixed)             -> --depth 3   progressive mutate 1->2->3, always on
    (advanced/optional) -> try_budget  = MAX_CODES_TO_TRY (how hard to search; governs uncapped runs)
    seed                -> reuse last seed, or "book fresh" -> book_random.py first

## 3. Stage detail

### Stage 1 — Harvest
- Form: provider (bet9ja/sportybet), min odd, max odd, how many codes, [advanced] try budget, seed source.
- "Run" -> POST /api/scan -> run_id. UI polls GET /api/scan/<run_id> every ~3s and shows
  a live counter: tried / valid / qualifying, like the CLI's "Scan done" line.
- On finish: shows the qualifying count (e.g. 468) and unlocks Stage 2.
- Fresh seed path: if the user picks "book fresh", the job first runs book_random.py
  (browser, slower, can fail behind Akamai) then scans that seed.

### Stage 2 — Distinct (phase 1, leg-level)
- One button: "Find distinct codes".
- POST /api/distinct {run_id} runs uniqueness.analyze() over THIS run's extract only.
- Shows the funnel (codes -> distinct / overlapping), the distinct count (the "42"),
  a list of the distinct codes each expandable to its games+options, and a
  "most-reused game+option" table so the overlap is visible.
- Each code row has a **Copy button** beside it (one-click copy the booking code to
  clipboard, with a select-the-text fallback) so no manual highlighting. NOTE: this
  copy JS currently lives twice (reporting.html and distinct_page.py); the build should
  extract a single shared helper rather than a third copy.
- A **Copy all** / **Download** action for the whole distinct list.
- This is the deliverable the user wanted first.

### Stage 3 — De-correlate (phase 2/3, later)
- Knob: max_exposure = the most selected codes any ONE exact selection (game+market+
  pick) may appear in. It is a PER-SELECTION cap, not "shared games per code".
  max_exposure=1 means fully independent (no selection shared by two selected codes).
  BLOCKER (fixed here): the backend REJECTS max_exposure=0 — select_subset raises
  ValueError for anything < 1 (selection.py:66). The UI's minimum/default is 1.
- Optional target count (must be >= 1 if given).
- Input is the FULL harvested pool (overlapping coupons included), normalised to the
  ticket shape select_subset expects (id, provider, num_legs, odds, legs[{key,event,
  market,pick}]). Passing only Stage-2's distinct list cannot grow anything — those are
  already mutually disjoint. So the run must retain the whole pool, not just the 42.
- POST /api/decorrelate {run_id,max_exposure,target} runs select_subset() and returns a
  GREEDY MAXIMAL feasible subset (explainable/reproducible ordering; NOT a provably
  maximum set — the docstring says so, selection.py:63).
- Deferred until Stage 2 is signed off, per the user's phased gate.

## 4. Reuse vs build
    Reuse as-is:   scan_code.py, book_random.py, uniqueness.py, selection.py,
                   reporting.py/html, analysis.py
                   (reporting.html has a per-code Copy button + fallback; distinct_page.py
                    has its OWN copy JS — the pattern is duplicated, not a shared helper.
                    Build item (f): extract one shared copy helper both pages use.)
                   distinct_page.py renders the Stage-2 page for ANY provider (built)
    Build new:     (a) job runner + run registry (thread + results/runs/<id>/)
                   (b) JSON API routes bolted onto serve.py
                   (c) the 3-stage HTML/JS page (extends reporting.html styling)
                   (d) a --max-qualifying CLI flag on scan_code.py (today only in .env)
                   (e) --run-dir flag so a scan writes into its own run folder
                   (f) a single shared copy() helper (today duplicated; note the
                       distinct_page.py PER-CODE copy has no fallback, only copy-all does)

## 4b. Provider differences (both backends now run the same pipeline)
Same band (5,000-350,000), same progressive mutate (1->2->3), same filter. Confirmed
runs: bet9ja 468 pool -> 42 distinct; sportybet 180 pool -> 23 distinct. Differences
the UI must respect:

    aspect          bet9ja                         sportybet
    code shape      base62, 7 chars, CASE-sensitive base36, 6 chars, case-insensitive
    charset          BASE62 (code default;         CHARSET=0-9A-Z in .env (base36)
                     NOT set in bet9ja/.env)
    browser         Chrome channel (Akamai)        plain chromium
    decode API      CouponAjax                     share API (orders/share)
    seed booking    book_random books ONE coupon   book_random books N games -> MANY
                    -> one seed                     seed codes; pick highest-leg one
    yield @ band    higher (468)                   lower (180) -> uncapped + try budget
                                                    matters more here

Implications for the UI:
- P1 Provider is a first-class selector; each provider routes to its own engine dir
     (Sport/<provider>/) and its own results/ tree. Isolation is enforced by SEPARATE
     results trees + separate worker processes (L10) — NOT by identity keys. Correction:
     contracts.identity() prefixes [provider,...] (contracts.py:19-30), but Stage-2
     uniqueness.selection_key() does NOT namespace (uniqueness.py:25-32). Never rely on
     leg identity alone to keep providers apart.
- P2 The seed step differs by provider: bet9ja yields a single seed per booking;
     sportybet yields a batch (the UI should let you pick, or auto-pick highest-leg).
- P3 sportybet's lower yield means "how many codes = uncapped" is the common case;
     the try-budget (L2) is what actually bounds the run.
- P4 Charset is per-provider but its SOURCE differs: sportybet sets CHARSET in .env,
     bet9ja leaves it blank and defaults to BASE62 in code (engine/settings.py). The UI
     must read the EFFECTIVE charset from Settings per provider, never assume it is in
     .env. Mutation depth stays 3 for both.

## 5. Loopholes / risks to resolve BEFORE building
- L1 Long-running scans (observed intervals, NOT guaranteed runtimes): 25k-attempt runs
     showed ~9m13s (sportybet) and ~17m49s (bet9ja) from seed OBSERVATION to extract
     PERSISTENCE. bet9ja's booking finished <1s before its seed observation, so almost
     the whole interval is post-booking — the gap is not "booking overhead". True
     launch-to-finish is unverifiable without job start records (add them). Run async,
     poll for progress, support cancel.
- L2 "Uncapped" needs bounds (but the space IS finite — it terminates): the mutation
     space is bounded (base62 depth-3 ~242k candidates; base36 ~48k), so a run cannot
     run forever; keep an explicit try_budget to bound WORK and LATENCY, not to prevent
     non-termination. Extreme odds are real and VERIFIABLE from results/raw/ (archived
     BEFORE filtering): e.g. sportybet UEQ14T, 43 legs, displayTotalOdds ~1.32e47
     (raw .../1267d85132dc45f69645435651f19068.json:667). These are RECORDED provider
     odds, not verified payout multipliers. Decision needed: default max-odds ceiling to
     drop these outliers.
- L3 Seeding: a scan needs a seed. Reusing the last seed re-harvests the same
     neighborhood (diminishing new codes); booking fresh needs a browser and can fail
     on Akamai. UI must make the seed source explicit and handle book failures.
- L4 Cross-run contamination: uniqueness.py currently globs ALL extracts in results/.
     If runs accumulate, Stage 2 mixes today's codes with stale/expired ones. Fix by
     scoping each run to results/runs/<run_id>/ and filtering Stage 2 to that run.
- L5 Expiry: codes die when their matches kick off. A "distinct" code found now may be
     partly started later. Show each code's earliest kickoff; reuse reporting's archive.
- L6 Concurrency: two simultaneous runs must not collide. Per-run folders (L4) plus a
     single-flight lock per provider (browser booking can't run twice at once).
- L7 Real-money guardrail: the UI books only UNSTAKED coupons and never stakes. No
     stake field, no wallet call. Must be impossible to trigger a paid bet from the UI.
- L8 Provider isolation: bet9ja and sportybet stay separate engines/paths; the UI
     switches provider but never mixes their pools — enforced by separate results trees
     and separate worker processes (L10), NOT by identity keys (see P1).
- L9 Local-only exposure: the server DEFAULTS to 127.0.0.1 but --host is configurable
     (serve.py:103) — loopback is not enforced. If the API can trigger scans/bookings,
     enforce loopback (or add auth) rather than relying on the default. Note: bet9ja's
     Chrome channel is configured (BROWSER_CHANNEL=chrome), but its effectiveness vs
     Akamai is unverifiable from config alone.
- L10 BLOCKER — provider processes: env.py load_dotenv(override=False) + per-provider
     sys.path.insert make same-process cross-provider reuse unsafe (a second provider
     inherits the first's env). The job runner MUST use one worker process per provider,
     one active job per provider, and restore that provider's run state on switch.
- L11 HIGH — one odds basis across stages: sportybet scanning qualifies on total_odds
     (site odds when present, coupon.py:110-112) while reporting/uniqueness band on
     parsed_leg_product (reporting.py:267). Pick ONE basis end to end (proposed:
     parsed_leg_product) and label it "recorded odds, not verified payout".

## 6. Build sequencing (once approved)
    1. scan_code.py: add --max-qualifying and --run-dir.           (tiny, unblocks API)
    2. job runner + run registry (results/runs/<id>/, status.json); ONE WORKER PROCESS
       PER PROVIDER (subprocess to scan_code.py), one active job per provider (L10).
    3. API routes on serve.py: /api/scan, /api/scan/<id>.          (Stage 1 works headless)
    4. 3-stage page; wire Stage 1 + live polling.
    5. /api/distinct + Stage 2 UI (the 42).                        <-- user's first milestone
    6. (gated) /api/decorrelate + Stage 3 UI.

## 7. Open questions for the user
    Q1 "How many codes" = size of the returned list (output cap), yes? And uncapped is
       allowed but always bounded by a try budget so it terminates?
    Q2 Default max-odds ceiling when the max field is left blank? (proposed: 350,000)
    Q3 Seed: default to reuse the last seed, with an explicit "book a fresh seed" toggle?
    Q4 Same UI switches between bet9ja and sportybet, or one instance per provider?
    Q5 sportybet booking returns many seeds at once: auto-pick the highest-leg seed,
       or let you choose which booked code to scan from?

## 8. Proposed answers (from the 2nd review — PENDING your confirmation)
    A1 "How many codes" = a MAX qualifying codes to collect (not a guaranteed distinct
       count). Allow uncapped, default try_budget 25,000 attempts; stop on count OR
       budget OR exhausted space, and show the stopping reason. (!= export-only TOP_N.)
    A2 Default max odds = 350,000 when blank, shown explicitly; use parsed_leg_product
       consistently across harvest + analysis (requires the L11 backend change); label
       the basis, never call it verified payout odds.
    A3 Seed defaults to the last selected seed for that provider (show source+timestamp)
       with an explicit "Book fresh seed" action; on reuse failure, surface it — never
       silently book. (Last-seed history is NEW work; CLI today falls back to SEED_CODE.)
    A4 One UI + provider selector; separate worker processes; separate run dirs; <=1
       active job per provider; switching restores that provider's run state.
    A5 Let the user choose from booked seeds; default to the NEWEST successfully-decoded
       eligible candidate. Do NOT auto-pick highest-leg — its superiority is unverified.
    NOTE: answering Q1-Q5 settles product choices; it does NOT clear L10/L11 and the
    Stage-3 contract fixes, which are implementation blockers regardless.
