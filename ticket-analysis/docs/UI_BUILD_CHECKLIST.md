# Implementation Checklist — backend contract fixes before the UI

Fixes the contract issues from the 2nd review (UI_BUILD_PLAN.md §3, L10, L11) plus the
supporting flags the job runner needs. Each item: WHAT / WHERE / CHANGE / DONE-WHEN.
Order matters: F0 unblocks the runner; F1-F4 are the contract fixes; F5-F7 are the UI seam.

Legend: [BLOCKER] build cannot work without it · [HIGH] wrong results without it.

---

## F0. scan_code flags the runner needs  (small, do first)  [DONE 2026-09-27]
WHAT   The job runner drives scans by shelling out; it needs to set the output cap and
       write into a per-run folder from the CLI (today both are .env-only / fixed).
WHERE  bet9ja/scripts/scan_code.py, sportybet/scripts/scan_code.py (mirror both).
CHANGE - add `--max-qualifying INT` -> sets settings.max_qualifying (0 = uncapped).
       - add `--run-dir PATH` -> scan writes its extract (and codes) under PATH instead
         of results/extracts/, so a run is self-contained (plan L4).
DONE   `scan_code <seed> --scan --max-qualifying 0 --run-dir results/runs/<id>` writes
       exactly one extract under that dir; --max-qualifying 5 stops at 5 qualifying.
STATUS Flags wired and offline integration verified (external review: real main(),
       mutation gen, parser, scanner, archival + report subprocess with fixture HTTP;
       63 tests / 17 subtests pass). `--max-qualifying` -> settings.max_qualifying
       (scanner treats 0 as uncapped, bet9ja scanner.py:117 / sportybet:121); now
       rejects < 0 at the CLI. `--run-dir` -> settings.results_dir, so save() writes
       extracts/, codes/, index.html under the run folder; CLI now refuses a dir that
       already holds an extract (guards accidental re-run contamination of Stage 2's
       glob). NOTE: the re-run guard is a TOCTOU check, not a reservation -- two
       processes can both pass it before either writes (concurrent-start race). It stops
       accidental sequential re-runs, not concurrent starts. Atomic run ownership +
       exact committed-extract-path consumption (not dir globbing) stay OPEN under
       F5/F2 and are the real L4 fix. Negative-count + re-run guards verified to exit
       non-zero on both providers (bet9ja scan_code.py:63-68 / sportybet:68-73).

---

## F1. Stage-3 max_exposure domain  [BLOCKER]  [PARTIAL 2026-09-28 — validator done; API route/UI knob deferred to F6/F7]
WHAT   select_subset rejects max_exposure < 1; the plan/UI must never send 0.
WHERE  portfolio/selection.py:66 (raises ValueError). New API handler /api/decorrelate.
CHANGE - UI knob: integer, min = 1, default = 1 (= fully independent: no selection shared
         by two selected codes). Copy: "max codes any one selection may appear in".
       - API validates `max_exposure` is int >= 1 (and `target` int >= 1 if present)
         and returns HTTP 400 with a clear message BEFORE calling select_subset — never
         let the ValueError become a 500.
DONE   /api/decorrelate with max_exposure=0 -> 400 "must be >= 1"; with 1 -> a set whose
       codes share no selection.
STATUS Validator landed (branch fix/f1-exposure-validation). selection.py now exports
       ParamError + parse_decorrelate_params(raw) -> {max_exposure:int>=1, target:int>=1|None},
       defaulting max_exposure=1, and REJECTING 0/negatives/bools/non-integral/garbage
       BEFORE select_subset runs (so the future handler answers 400, never 500). Domain
       matches select_subset exactly -- test_parsed_output_is_accepted_by_select_subset
       proves parse output is never rejected downstream. Outer body is guarded too:
       None -> defaults; any non-mapping (falsy [] False 0 '' OR truthy [1] True 1 'x'
       3.5) -> ParamError "request body must be a JSON object", never AttributeError
       (so the 400-not-500 promise holds for malformed bodies). 11 validator tests;
       full suite 70 OK; direct `python tests/test_selection.py` now runs all 21 (the
       __main__ block was mid-file and skipped the new class -- moved to bottom).
       REMAINING (moves to F6/F7, not F1): wire /api/decorrelate to call parse_ first and
       map ParamError->400; add the UI integer knob (min 1, default 1) with the copy
       "max codes any one selection may appear in".

## F2. Stage-3 input shape + full pool  [HIGH] [PARTIAL 2026-09-28 — adapter tested; API wiring deferred to F6]
WHAT   select_subset needs tickets shaped {id, provider, num_legs, odds, legs:[{key,
       event, market, pick}]}. Raw extract coupons are NOT that shape (they have
       `selections` + `selection_key`, no `legs`/`key`). And it must run on the FULL
       harvested pool, not Stage-2's distinct list (the 42 are already disjoint).
WHERE  Adapter EXISTS: analysis.normalize_ticket(raw, provider) (analysis.py:133) emits
       legs[{"key",...}]. Reference pipeline: reporting.py:250-273 (read extract ->
       normalize_ticket -> drop issues -> dedup by id -> band -> select_subset).
CHANGE - /api/decorrelate uses portfolio.decorrelation.decorrelate_extract with one
         explicit committed run extract path, never a directory glob or distinct list.
         The runner resolves the path and provider; HTTP clients must not supply paths.
       - Normalize, exclude invalid/expired and foreign-provider tickets, deduplicate
         identical code observations; fail on conflicting observations without choosing
         a winner by filename or input order. Return exclusion reasons and pool counts.
       - Require explicit odds_basis + min_odds, optional max_odds=None (no ceiling).
         Missing odds on that basis are excluded without fallback. This exposes the
         policy choice; aligning it with harvesting remains F4's responsibility.
       - Keep every band-passing coupon, including overlapping ones.
       - Return result as a GREEDY MAXIMAL feasible subset (not "maximum"/"largest");
         surface result["rejected"] (reason + blocking selections) for explainability.
DONE   Deterministic three-code fixture: A/B share a selection, C is disjoint. Full
       pool size stays 3; cap 1 selects C and one of A/B; cap 2 selects all three.
       Fixed-clock tests exclude expired/malformed tickets, enforce each provider,
       prove explicit odds-basis filtering and isolation from neighboring extracts.
STATUS Offline adapter and nine tests implemented in portfolio/decorrelation.py and
       tests/test_decorrelation.py. Selection rejection details are preserved. Historical
       468 -> >=42 is not an acceptance oracle: expiry changes eligibility over time.
       REMAINING F6: wire route to the adapter using runner-owned extract/provider/policy,
       map ParamError to 400 and surface ExtractError as a failed analysis, not empty
       success. F5 still owns committed-path provenance; F2b owns shared stage identity.

## F2b. One leg-identity AND one eligible pool across Stage 2/3  [HIGH]  [DONE 2026-09-28 v2]
WHAT   Stage 2 distinctness and Stage 3 exposure are currently computed on DIFFERENT
       keys, so their notions of "same selection" disagree.
WHERE  Stage 2: uniqueness.selection_key (uniqueness.py:26) — prefers raw selection_key,
       NOT provider-namespaced. Stage 3: contracts.identity via normalize_ticket
       (contracts.py:19-30) — provider-namespaced, native + display fallback.
CHANGE Unify on contracts.identity as the single leg key: have uniqueness derive its leg
       key from normalize_ticket's legs[].key (drop its private selection_key), so
       both stages count shared selections on the SAME basis and eligible pool.
DONE   For one run BOTH stages build the SAME eligible pool from ONE builder
       (decorrelation.build_pool: same extract, provider, odds basis+band, duplicate/
       conflict handling) on the SAME leg identity. Corrected criterion: the fully-
       distinct set (Stage 2) == eligible codes whose every leg is unique across the
       WHOLE eligible pool. On the same pool with no target limit, distinct ⊆ selected
       at max_exposure=1; equality is possible. With a target limit, inclusion is not
       guaranteed.
STATUS Done (v2 — first pass FAILED review: Stage 2 had its own ingestion that ignored
       the odds band, accepted foreign-provider coupons, and diverged on conflicting
       duplicates). FIX: both stages now consume ONE builder, decorrelation.build_pool.
       uniqueness.load_coupons is gone; uniqueness.analyze(tickets) takes build_pool's
       normalized tickets and analyze_extract(extract, provider, *, odds_basis, min_odds,
       max_odds) is the Stage-2 entry. build_pool stamps ticket['raw'] so distinct_page
       keeps every display field. Leg identity = canonical(contracts.identity) on both
       sides. Integration tests (real extract files, not a _norm_legs helper) cover the
       three reproduced discrepancies: odds band applied in Stage 2; foreign provider
       excluded; conflicting duplicate raises ExtractError in BOTH stages -- plus the
       corrected subset criterion (distinct {C} is a strict subset of selected {C, A|B}).
       distinct_page/uniqueness CLIs now take --extract + --provider + odds policy (run-
       scoped), matching F0 --run-dir. Both CLIs REQUIRE --odds-basis. The distinct
       page displays, labels, and sorts by that basis without fallback. Regression
       coverage includes equal sets, target truncation, and odds display/CLI policy.

## F3. Provider isolation via worker processes  [BLOCKER]
WHAT   Both engines mutate process-global state, so two providers cannot share one
       interpreter: env.py load_dotenv(override=False) means the 2nd provider inherits
       the 1st's env; each scan_code does sys.path.insert(0, provider_root).
WHERE  bet9ja/engine/env.py:14-22 + sportybet/engine/env.py:14-22; scan_code.py:13-18.
CHANGE - Job runner launches each scan/booking as a SUBPROCESS: cwd = Sport/<provider>,
         argv = [python, -m, scripts.scan_code, seed, --scan, ...flags]. Never import a
         provider's engine into the server process.
       - Single-flight: at most ONE active job per provider (a per-provider lock).
       - Separate run registries/dirs per provider; on provider switch in the UI,
         restore that provider's run state.
DONE   Run bet9ja then sportybet back-to-back through the runner; the sportybet extract
       shows base36 codes and sportybet's own band (proves no env/path bleed).

## F4. Single odds basis end to end  [HIGH]
WHAT   "odds" means different things per stage: sportybet scanner qualifies on total_odds
       (site odds when present); reporting/uniqueness band on parsed_leg_product. Stage-1
       "5,000-350,000" and Stage-2/3 numbers can then refer to different quantities.
WHERE  sportybet/engine/coupon.py:108-112 (total_odds -> site when present);
       reporting.py:267 (in_band on parsed_leg_product); scanner qualify path.
CHANGE Pick parsed_leg_product as the canonical basis everywhere (provider-neutral,
       always computable). Make the scanner's min/max qualify compare parsed_leg_product
       (add ODDS_BASIS=parsed_leg_product setting if we want it switchable). Label the
       number in every stage/UI as "recorded odds (leg product), not verified payout".
DONE   A coupon whose site odds differ from its leg product gets the SAME in/out band
       decision at Stage 1 (scan qualify) and Stage 2/3 (report) — one basis, one answer.

---

## F5. Run registry + job runner
WHAT   Track runs on disk so status polling and Stage 2/3 can find a run's pool.
CHANGE results/runs/<run_id>/ holds the extract + status.json {state, provider, params,
       tried, valid, qualifying, stop_reason, started_at, finished_at, error?}. Runner
       writes status transitions; supports cancel (terminate the subprocess).
DONE   GET /api/scan/<id> reflects live counts and a stop_reason; cancel ends the job.

## F6. API routes on serve.py
WHAT   Add POST handlers; serve.py is GET-only today (serve.py:67).
CHANGE POST /api/scan, GET /api/scan/<id>, POST /api/distinct, POST /api/decorrelate,
       GET /api/runs. Enforce loopback (or auth) since these trigger scans/bookings —
       do NOT rely on the 127.0.0.1 default alone (--host is configurable, serve.py:103).
DONE   Each route works; a non-loopback request to a mutating route is refused.

## F7. Shared copy() helper + 3-stage page
WHAT   Copy JS is duplicated (reporting.html, distinct_page.py) and distinct_page's
       per-code copy has no fallback.
CHANGE Extract one copy helper (clipboard + select-text fallback) used by both the
       distinct view and the 3-stage page. Build the page: Stage1 form -> poll ->
       Stage2 distinct (per-code Copy + Copy-all) -> Stage3 decorrelate.
DONE   Per-code copy works with the fallback path; one helper, no duplication.

---

## Acceptance gate for the whole slice
1. bet9ja + sportybet each: scan (band, uncapped+budget) -> distinct -> decorrelate,
   all from the browser, each provider in its own process.
2. Stage 2 distinct count == Stage 3 zero-shared count on the same run (F2b).
3. max_exposure=0 is impossible to send; the pool fed to Stage 3 is the full run pool.
4. Every odds figure is one basis, labelled "recorded, not verified payout".
