# Historical results: backend and frontend gap review

Reviewed 24 October 2026 against HEAD `aa68887`. Review only; no product code
changes, source requests or acceptance outcome claims were made.

## Intended experience

Open **Earlier runs on this machine**, select a saved run from weeks ago, see
its retained codes, and check whether an individual code would have won. Also
allow checking the whole run and viewing the resulting historical percentages.
Started fixtures, overlapping tickets and the current page must not restrict
this population. A new scan, distinct analysis and decorrelation are unnecessary.

The feature reports counterfactual outcomes for the original saved selections.
It does not re-decode a booking code to replace its original contents, place a
bet, infer an actual payout or claim knowledge of every code ever generated.

## What this commit actually delivers

`aa68887` changes worker ownership, cancellation and report publication, with
regression tests and readiness documentation. It prevents an active worker being
replaced merely because progress is old, permits recovery after process death,
and serializes cancellation against publication. These are useful backend
foundations. It adds no results controls or result rendering to `app.html`.

Existing foundations also include frozen input populations, deterministic leg
evaluation, conservative matching, five-state accounting, persistent retrieval
jobs, cache/quota controls, report revisions and the five results API endpoints.
These do not yet constitute the intended user feature.

I reran the commit's eight lifecycle tests: all passed. This review did not rerun
the full suite; the commit's readiness document records the previous 579-test run.

## Verified historical inputs

Both examples below were read through `RunRegistry.committed_extract_snapshot`,
which verifies the committed bytes. Dates are the saved kickoff date strings;
they are not evidence that a match actually completed.

| Provider / run | Retained codes | Saved fixture date range |
| --- | ---: | --- |
| Bet9ja `run-bet9ja-20261008-205016-d0bb05e9` | 25 | 8–18 October 2026 |
| SportyBet `run-sportybet-20261010-071900-651fee94` | 25 | 10–19 October 2026 |

There are also substantially larger older runs, including thousands of retained
Bet9ja codes. Some older runs contain later fixtures, so booking date alone must
never determine whether all their games have finished.

Use older runs as the primary product acceptance population. Keep the 23 October
164-ticket population as a regression/mixed-future case. The current plan section
10 step 6 and `F9_STEP6_READINESS.md` still prescribe those 164 tickets as the
acceptance population; that contract needs updating before execution.

## Blocking backend gaps

### H01 — Historical source access and fallback are not connected

`results_tracking/results_api.py:368–420,634–692` always orchestrates the
API-Football source. `sources/football_data_import.py` parses local CSVs into
`FootballDataFixture`, but no production caller connects that importer to the
shared `Fixture` model, cache, results job or published report.

The reported live access error allowed only 23–25 October. That test does not
establish access to weeks-old matches. Do not spend more requests on an entire
old run until the required historical dates and competitions have a demonstrated
source path. The local E0 CSV's existence alone does not prove a ticket can be
settled: exact fixture matches, required periods and every required leg matter.

Required: inventory the selected historical run's source requirements; establish
a permitted historical source/import path; preserve source bytes and provenance;
convert supported facts into shared fixtures; and select/reuse those observations
before network fetching. Unsupported competitions/markets remain represented.
The current API adapter's eight-league filter is a coverage boundary, not a
reason to omit other codes or quietly classify their fixtures as unplayed.

### H02 — Date restrictions discard the whole report

`_make_fetch_one` converts every non-transport `ApiFootballError` into
`FatalTaskError`. `_run_evaluation_job` returns on that error before evaluating
or publishing. Consequently one inaccessible date can hide usable observations
for other dates. `tolerate_incomplete_fetch` does not fix this fatal path.

Required: distinguish a date/season access restriction from invalid credentials,
transport failure, exhausted quota and malformed source data. Retain retrieval
status per requested date/fixture and attach it to affected legs. Publish a
complete population report with explicit unresolved reasons when some evidence
cannot be retrieved. Global failures may stop further requests while preserving
cached evidence and explaining affected rows. Cancellation must retain the
existing rule that a cancelled draft does not publish.

Do not translate inaccessible historical data into pending, loss, or a generic
fixture-not-found result. Missing source evidence is different from an observed
fixture whose status is scheduled/in progress.

### H03 — Normal server startup cannot supply matching evidence

`portfolio/serve.py:38–46` constructs `ResultsApiConfig(root=root)` with no
identity configuration. `load_identity_evidence` consequently returns empty
aliases and verified mappings. `matching.py:129–137,301–308` requires explicit
aliases for exact canonical matching; tier-one matching requires verified
mappings. Thus the normal served application has no accepted matching path,
even when the API successfully supplies the right football results.

Required: a server-owned configuration/loading path, reviewed mappings for the
initial old-run corpus, and a reproducible way to extend that coverage. Freeze
the exact identity evidence used by a job. Retain ambiguity and review reasons;
never automatically accept fuzzy candidates or weaken competition/team checks.
Human mapping review need not be a full UI editor for the first release.

### H04 — Confirmed ticket wins/losses are deliberately disabled

`rules/provider_types.py:42–51` returns unverified for every ticket type.
`reporting.aggregate_ticket` correctly makes every such ticket unresolved,
regardless of leg outcomes. Adding buttons or fixing source access alone cannot
produce the confirmed ticket answer the user wants.

Required: evidence-backed provider structure mappings and preservation of all
fields needed to distinguish ordinary accumulators, systems and other structures.
`_ticket_to_evidence` currently forwards `bet_type` but not a complete provider
structure payload. Audit actual old-ticket/raw-response fields before designing
that representation. Verify settlement assumptions for each enabled structure;
leave unsupported structures unresolved and show leg outcomes separately.
`proxy_all_legs_won` must remain visibly distinct from a confirmed ticket win.

### H05 — Public result rows cannot explain the result by themselves

`__main__.py:371–392` emits leg IDs, statuses, rules, reasons, match tier and source
IDs. `results_api.list_codes` returns those report rows directly. It does not
return original event/market/pick/specifier, fixture score/state, required period,
or observation time. The revision's evidence contains more information, but the
five-endpoint contract does not expose it as a usable detail response. The
existing Stage 1 pool is not a complete replacement for the frozen population.

Required: a stable detail representation joining a revision row to its original
ticket and fixture evidence. Expose the selected pick, relevant scores/periods,
outcome reason, source identity, observation time and rule version. Preserve
ambiguous-match review candidates where relevant. Choose either richer bounded
rows or a revision-pinned ticket detail endpoint; do not expose arbitrary paths.
Archive raw source observations with digests if original source-fact inspection
is promised: normalized `evidence.json` alone supports decision replay but does
not contain the original API payload preserved in the mutable cache.

### H06 — Checking one code has no defined backend contract

`POST /api/results/<run_id>` evaluates the whole population. There is no ticket
check endpoint or row selector. A single-code button must not silently trigger
an unexplained run-wide fetch or publish a one-ticket report as the full-run rate.

Required: identify tickets by run + frozen row identity, not booking code alone.
Define single-code retrieval/evaluation, reuse of shared fixture observations,
and how unchecked rows remain counted when showing a run summary. Keep the
existing full-run job path for Check all. Single-code work must not shrink N,
overwrite the full-run report with subset metrics, or duplicate source requests.

### H07 — Fetch dates disagree with normalized matching times at midnight

`results_api._dates_needed` takes the first ten characters of raw kickoff strings,
whereas `_selection_kickoff_evidence` converts aware timestamps to UTC. Offline
reproduction: `2026-10-08T00:30:00+02:00` requests **8 October**, but matching uses
**7 October 22:30 UTC**. A date-bounded source query can miss the correct fixture.

Required: derive query dates using the source's documented date/time semantics
and the same verified instant used for matching. Naive/date-only values need an
explicit bounded policy; do not silently assign UTC. Test both midnight directions
and postpone/reschedule cases.

## Frontend gaps

`portfolio/app.html` has history loading and Stage 1 code cards, but no calls to
`/api/results`, no result actions, no outcome display and no results job polling.
The following are required for the first usable release:

| Area | Required behavior |
| --- | --- |
| Entry point | Load a historical run, then open its results without a new scan or Stage 2/3 work. |
| Complete list | Include all retained rows, including started/overlapping/excluded rows; preserve malformed rows with an explanation. Stage 1's expired toggle and odds filters must not define results membership. |
| Actions | Check result on an individual retained row; Check all results for the full frozen run. Read existing results before fetching again. |
| Outcome detail | Won/lost/void/pending/unresolved, original legs, scores, source/check time and understandable reasons; separate ticket-structure uncertainty from known leg results. |
| Job lifecycle | Running/progress, quota/access failures, cancel, retry and explicit refresh; retain the previous published revision while a refresh is running or fails. |
| Summary | W/L/V/P/U, numerator/denominator, confirmed win rate, decided win/loss rate and coverage; unknowns cannot look like losses or evidence of zero historical winners. |
| Paging/filtering | Revision-pinned pagination; filtering winners or changing pages never changes full-run headline metrics. Scale to runs containing thousands of tickets. |
| Run/provider switching | Reset result state and reject stale responses/polls after navigation, following the existing generation-guard pattern in `loadRun`/`loadCodes`. |
| Partial history | Explicit opt-in and incomplete-population label for saved checkpoints; never present partial-run results as the complete history. |

Avoid making the user unhide expired codes to access results. A dedicated results
view can list the complete population while existing upcoming-code analysis keeps
its own purpose. Label new, unchecked rows separately from evaluated unresolved
rows in the UI without inventing a sixth settlement outcome.

## Proposed implementation order and acceptance

1. Revise the plan's acceptance population and UI delivery scope around opening
   historical runs. Freeze the two verified old-run candidates, inventory source,
   rule, identity, timezone and ticket-type gaps, and select a hand-checked corpus.
2. Prove a historical source/import path and a small number of real matches and
   leg decisions. Verify provider ticket semantics before claiming ticket wins.
   Include both supported and deliberately unsupported examples.
3. Implement per-date retrieval reasons, source selection, identity configuration,
   UTC-correct query planning, and evidence retention. Preserve existing worker,
   quota, cancellation and immutable-revision protections.
4. Define single-code/full-run evaluation and detail API contracts, including
   unchecked-row accounting and reuse of observations across both operations.
5. Build the history-to-results UI, actions, detail, summary, paging and job states
   against those contracts. Do not wait for all markets to be supported before
   showing retained tickets and honest explanations.
6. Accept the entire experience on old saved runs: one-code then all-code checks;
   exact row conservation; all five outcomes where supported by known evidence;
   access restrictions affecting only relevant rows; cached/offline operation;
   cancelled/failed refresh preserving prior results; navigation during jobs;
   complete replay after original run/cache removal; and independent checks of
   every accepted match and claimed outcome in the published corpus.

For each run require `N = W + L + V + P + U`, including unchecked rows in U when
calculating the complete-population metric. Show `W/N` alongside `W/(W+L)` and
coverage, with undefined denominators displayed as unavailable. Any later
cross-run summary needs an explicit repeat-code/content policy rather than adding
run totals and calling them distinct historical codes. Historical observed rates
are not a forecast of the next ticket's chance of winning.

The lifecycle commit is a useful foundation. The blockers above must be addressed
to deliver historical ticket checking; running the unchanged 164-ticket harness
again would not establish that the intended backend-and-frontend feature works.
