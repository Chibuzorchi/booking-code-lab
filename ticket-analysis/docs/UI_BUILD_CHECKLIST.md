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
       glob). NOTE: run-directory exclusivity properly belongs to the runner (F5); this
       flag + guard alone does NOT fully resolve L4 -- downstream stages must consume the
       exact committed extract path, not glob the dir. Negative-count + re-run guards
       verified to exit non-zero on both providers.

---

## F1. Stage-3 max_exposure domain  [BLOCKER]
WHAT   select_subset rejects max_exposure < 1; the plan/UI must never send 0.
WHERE  portfolio/selection.py:66 (raises ValueError). New API handler /api/decorrelate.
CHANGE - UI knob: integer, min = 1, default = 1 (= fully independent: no selection shared
         by two selected codes). Copy: "max codes any one selection may appear in".
       - API validates `max_exposure` is int >= 1 (and `target` int >= 1 if present)
         and returns HTTP 400 with a clear message BEFORE calling select_subset — never
         let the ValueError become a 500.
DONE   /api/decorrelate with max_exposure=0 -> 400 "must be >= 1"; with 1 -> a set whose
       codes share no selection.

## F2. Stage-3 input shape + full pool  [HIGH]
WHAT   select_subset needs tickets shaped {id, provider, num_legs, odds, legs:[{key,
       event, market, pick}]}. Raw extract coupons are NOT that shape (they have
       `selections` + `selection_key`, no `legs`/`key`). And it must run on the FULL
       harvested pool, not Stage-2's distinct list (the 42 are already disjoint).
WHERE  Adapter EXISTS: analysis.normalize_ticket(raw, provider) (analysis.py:133) emits
       legs[{"key",...}]. Reference pipeline: reporting.py:250-273 (read extract ->
       normalize_ticket -> drop issues -> dedup by id -> band -> select_subset).
CHANGE - /api/decorrelate builds its pool exactly like reporting.py:250-273, from the
         RUN's extract (results/runs/<id>/), NOT from the distinct_tickets.json.
       - Keep every band-passing coupon, including overlapping ones.
       - Return result as a GREEDY MAXIMAL feasible subset (not "maximum"/"largest");
         surface result["rejected"] (reason + blocking selections) for explainability.
DONE   Decorrelate the bet9ja 468-pool at max_exposure=1 returns >= 42 codes (it grows
       the fully-distinct 42 by adding one code per overlap cluster); no selected pair
       shares a selection.

## F2b. One leg-identity across Stage 2 and Stage 3  [HIGH]
WHAT   Stage 2 distinctness and Stage 3 exposure are currently computed on DIFFERENT
       keys, so their notions of "same selection" disagree.
WHERE  Stage 2: uniqueness.selection_key (uniqueness.py:26) — prefers raw selection_key,
       NOT provider-namespaced. Stage 3: contracts.identity via normalize_ticket
       (contracts.py:19-30) — provider-namespaced, native + display fallback.
CHANGE Unify on contracts.identity as the single leg key: have uniqueness derive its leg
       key from normalize_ticket's legs[].key (drop its private selection_key), so
       "distinct" (Stage 2) and "0 shared exposure" (Stage 3 @ max_exposure=1) are the
       SAME set on the SAME basis.
DONE   For one run: count of fully-distinct codes (Stage 2) == codes with zero shared
       selections under the Stage-3 identity; the two stages never disagree on a leg.

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
