# UI Build Plan — Independent Verification

Reviewed: UI_BUILD_PLAN.md, every factual/technical claim checked against the actual
code and live state (not against prior chat). Date of review: 2026-09-27.
Evidence is cited as file:line. Verdicts: CONFIRMED / CONFIRMED* (true with a
correction) / CORRECTION (claim is wrong or misleading) / UNVERIFIED (could not be
reproduced this pass).

## Summary
23 of 26 checkable claims CONFIRMED. 1 CONFIRMED with a timing correction (L1),
3 CORRECTIONS (all minor: bet9ja CHARSET source, the "reuses copy() helper" wording,
P4 wording), 1 UNVERIFIED (L2 monster-odds, not reproducible from in-band extracts).
Nothing in the plan is structurally broken. Corrections to fold in are listed at the end.

## Architecture / the seam
- CONFIRMED  serve.py is GET-only, no POST route.            serve.py:67 (only do_GET; do_HEAD=do_GET)
- CONFIRMED  uniqueness.analyze() exists.                    uniqueness.py:66
- CONFIRMED  selection.select_subset() exists.               selection.py:51
- CONFIRMED  scan entrypoint is scan_code.py --scan.         bet9ja/scripts/scan_code.py:25; sportybet:28

## Backend field mapping (§2)
- CONFIRMED  --min-odds flag (both).                         bet9ja:26 / sportybet:29
- CONFIRMED  --max-odds-cap flag (both).                     bet9ja:30 / sportybet:33
- CONFIRMED  --max-codes flag = MAX_CODES_TO_TRY (both).     bet9ja:28 / sportybet:31
- CONFIRMED  --depth flag; depth 3 = progressive 1->2->3.    code_mutator.py:17-32 (stage 1..depth)
- CONFIRMED  MAX_QUALIFYING output cap; 0 = uncapped.        scanner.py:117 (bet9ja) / :121 (sportybet)
             guarded `if self.settings.max_qualifying and len(...) >= ...`
- CONFIRMED  NO --max-qualifying CLI flag today (build new). absent from both scan_code.py
- CONFIRMED  NO --run-dir flag today (build new).            absent from both scan_code.py

## Reuse vs build (§4)
- CONFIRMED  reporting.html has a per-code copy button + fallback.
             reporting.html: "Copy code", navigator.clipboard.writeText, input-select fallback.
- CONFIRMED  distinct_page.py renders the Stage-2 page for any provider; ran clean for both.
             portfolio/distinct_page.py (render_html + --provider/--results CLI)
- CORRECTION "Reuses the copy() helper already in reporting.html" is inaccurate.
             distinct_page.py ships its OWN copy JS (cp / cpAll with a textarea+execCommand
             fallback). The pattern is duplicated, not a shared helper. Harmless, but the
             "reuse" accounting is wrong — there is no shared copy module.

## Provider differences (§4b)
- CONFIRMED  bet9ja: base62, 7 chars, case-sensitive.       code_mutator.py:4 docstring; sample code "5SVZB2D" (7)
- CONFIRMED  sportybet: base36, 6 chars.                     .env CHARSET=0-9A-Z; sample "UEQ099" (6)
- CONFIRMED  bet9ja uses Chrome channel due to Akamai.       bet9ja/.env:37 BROWSER_CHANNEL=chrome
             "# bet9ja Akamai blocks headless chromium"; book_random.py:55-56 applies it.
- CONFIRMED  sportybet uses plain chromium.                  sportybet/book_random.py:44 chromium.launch
- CONFIRMED  bet9ja decode API is CouponAjax.                settings.py:18 coupon_api_base .../feapi/CouponAjax
             (endpoint GetBookABetCouponV2, coupon_api.py:34)
- CONFIRMED  sportybet decode API is orders/share.          share_api.py:3 (GET /api/ng/orders/share/<code>)
- CONFIRMED  seed booking: bet9ja books ONE, sportybet books N.
             bet9ja/book_random.py:1 "Book a random ... game"; sportybet:1 "Book N ... --count 18"
- CONFIRMED  yields at the band: bet9ja 468 pool / 42 distinct; sportybet 180 / 23.
             re-ran uniqueness this pass; both numbers reproduced exactly.
- CORRECTION bet9ja CHARSET is NOT set in its .env — it is blank there and defaults to
             BASE62 in code (engine/settings.py:58 `_env.get("CHARSET", BASE62)`). The §4b
             table cell "charset (env) CHARSET (base62)" and implication P4 ("already in
             each .env (CHARSET)") are true for sportybet only. For bet9ja the charset is a
             code default. UI must read the effective charset from Settings, not assume .env.

## Loopholes (§5)
- CONFIRMED* L1 long scans need async. Timing figure needs a correction: the "~18 min"
             was a combined book+scan run (08:16:45->08:34:58). A pure 25k-try scan
             (sportybet, no booking) was ~9 min (09:38:20->09:47:33). Conclusion (must be
             async, non-blocking, cancellable) stands regardless.
- UNVERIFIED L2 "40-leg monster accumulators, odds ~1e40". Not reproducible this pass:
             the saved extracts only hold in-band (5,000-350,000) coupons, so the outliers
             are already filtered out and invisible here. The mechanism (product of ~50 leg
             prices) is mathematically plausible and was reported in earlier sessions, but
             it is not independently confirmed from current artifacts. The mitigation (a
             default max-odds ceiling) is still the right call.
- CONFIRMED  L4 uniqueness reads ALL extracts (glob).        uniqueness.py:101 (extracts/*.json)
             This is the real contamination risk the plan names; per-run folders fix it.
- CONFIRMED  L5 reporting has archive_stale and serve uses it. reporting.py:194; serve.py:24,111
- CONFIRMED  L9 serve.py binds 127.0.0.1 by default.         serve.py:103
- PARTIAL    L7 real-money guardrail is a design intent, not code-checkable. Supporting
             evidence: book_random books coupons with no stake step / no wallet call. There
             is no automated test that *proves* the UI can never stake; add one when built.

## Note on the CURRENTLY-live pages
The two live distinct pages are served by a plain `python -m http.server` on ports
8042/8043 (both --bind 127.0.0.1), NOT by serve.py. That matches L9 (localhost) but is
a throwaway server, not the "extended serve.py" the plan targets. Fine for viewing; not
the eventual path.

## Corrections to fold into UI_BUILD_PLAN.md
1. §4 line "Reuses the copy() helper already in reporting.html" -> distinct_page.py has
   its own copy JS; either extract a shared helper or drop the "reuse" wording.
2. §4b table + P4: bet9ja CHARSET is a code default (BASE62), not an .env value. UI should
   source the charset from Settings per provider, not assume .env.
3. §5 L1: correct the timing to "~9 min for a pure 25k scan; ~18 min when it also books a
   fresh seed."
4. §5 L2: mark the 1e40 figure as observed-earlier / not-reproducible-from-extracts, and
   keep the max-odds ceiling as the mitigation.

---

## Addendum — second-pass corrections (2026-09-27)
A follow-up code-level review found errors in THIS document. Recording them here so the
review is not itself a stale source.

- RETRACTED: "Nothing in the plan is structurally broken." False. Stage 3 as written set
  max_exposure=0, which select_subset REJECTS (raises ValueError for <1, selection.py:66).
  That is a build blocker. Fixed in the plan (min/default is now 1).
- CORRECTED (L2): I marked the extreme-odds claim UNVERIFIED. It IS verifiable — raw
  provider responses are archived BEFORE parsing/filtering under results/raw/
  (coupon_api.py:51-57; share_api.py:47-53). Example: sportybet UEQ14T, 43 legs,
  displayTotalOdds ~1.32e47 (results/raw/1267d85132dc45f69645435651f19068.json:667).
  I wrongly assumed in-band extracts were the only evidence source. Also: the mutation
  space is finite, so "never terminates" was wrong — it terminates; a budget bounds work.
- CORRECTED (L1): timing should be stated as observed intervals (sportybet ~9m13s,
  bet9ja ~17m49s, seed-observation -> extract-persistence), not benchmarks. bet9ja's
  booking finished <1s before seed observation, so the interval is NOT booking overhead.
  Launch-to-finish is unverifiable without job start records.
- MISSED (contracts): "identities already namespace providers" is only true for
  contracts.identity() ([provider,...]); Stage-2 uniqueness.selection_key() does NOT
  namespace. Isolation must come from separate results trees + worker processes.
- MISSED (BLOCKER): env.py load_dotenv(override=False) + per-provider sys.path.insert
  make same-process cross-provider reuse unsafe. Requires one worker process per provider.
- MISSED (HIGH): odds-basis mismatch — sportybet scanning uses site odds when present
  (coupon.py:110-112); reporting bands on parsed_leg_product (reporting.py:267).
- CORRECTED: the distinct_page.py PER-CODE copy has no fallback (only copy-all does).
- CORRECTED (L9): loopback is a default, not enforced (--host is configurable).

Net: the plan's structure is sound, but it had 2 BLOCKERs (Stage-3 max_exposure, provider
process isolation) and 2 HIGHs (full-pool input to Stage 3, odds basis) that this document
originally missed. All are now reflected in UI_BUILD_PLAN.md (§3, §5 L10/L11, §2, §6).
