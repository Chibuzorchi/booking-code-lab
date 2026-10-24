# F9 — historical ticket outcomes and observed win rates

Status: reviewed draft, 24 October 2026. See [F9_PLAN_QA.md](F9_PLAN_QA.md).
This is an implementation contract, not a results backend or proof of source/rule
coverage. Complete section 10's evidence gates before claiming ticket wins.

## 1. Product question and boundaries

Answer: **“Of the codes we saved, which would have won if played, and what
percentage is that?”** One code means one historical ticket observation, not one
game. Include expired and overlapping tickets. Preserve original selections even
if a current provider lookup drops started games. Actual-play status is optional
context, never an evaluation filter.

The first deliverable reports every retained ticket in a selected run, including
explicit unresolved cases. Full market coverage remains the goal; available
sources must prove they can supply the required facts before we promise it.
Selection success, ticket success and profit are different. System tickets can
have winning sub-bets without every selection winning; voids can change returns.
Do not infer provider payout from a parsed odds product or promote the existing
`payout_semantics.status = unknown` merely because scores are known.
No paid bets, automatic staking, profit estimates or position settlement are added.

## 2. Historical population and completeness

Read a succeeded run through `RunRegistry.committed_extract_snapshot()` and verify
provider, schema, digest and size. Evaluate all retained `qualifying` rows. Do not
use `build_pool()`, distinct/selected lists, current expiry or current odds filters
to choose the population. Stage 1's visible rows are not the historical denominator.

Optional partial-run mode requires explicit `allow_partial`: freeze the registry's
checkpoint bytes into an immutable evaluation input, preserving digest, capture
time, run state and `population_complete = false`. A changing checkpoint creates
new input; never substitute it midway through a job. Missing checkpoints mean
unavailable history, not zero discovered codes. Use completed runs for the first
acceptance evaluation; partial reports must not inherit a full-run claim.

Input identity and accounting:

- Bet9ja code case is preserved; SportyBet codes are uppercased.
- Each row has a stable identity from extract digest and row index. Exactly
  repeated rows of one code/content are aliases, represented once in ticket
  metrics; show raw row and duplicate counts. Conflicting observations of one
  code within an extract fail validation rather than choosing the latest.
- Different booking codes with identical contents remain separate tickets for
  the primary per-run “play every saved code” measure. Content counts are secondary.
- Invalid retained tickets remain unresolved rows with reasons; normalization
  cannot silently shrink the population.
- Cross-run reporting shows both run-observation totals and unique
  provider+code+content totals. Repeated code/content does not create extra unique
  tickets. Different contents under one code remain dated, flagged observations;
  do not silently merge them or select the latest.

Current completeness limits: scanners discard content aliases before saving, and
cancelled workers can lose hits since their last checkpoint. F9 cannot reconstruct
missing original snapshots. Name the metric **retained-code history**, not every
code ever generated/decoded/booked. Durable per-hit capture and alias retention
are separate ingestion work. A code string in a log is not its full original ticket.

## 3. Real input inventory

Initial set: the 23 October Bet9ja run
`run-bet9ja-20261023-065902-28fff68b` (102 tickets) and SportyBet run
`run-sportybet-20261023-070118-f6753695` (62). Record their extract hashes in the audit.
They are not all finished merely because the runs happened yesterday.

| Observed input | Implication |
| --- | --- |
| 2,491 Bet9ja and 1,789 SportyBet selection occurrences | Deduplicate fixture requests, not ticket denominators |
| 51 and 206 displayed market labels respectively | Inventory combinations before writing market rules |
| Two Bet9ja and four SportyBet tickets contain non-football legs | Gate rules by sport; unsupported sports remain unresolved |
| All 2,491 Bet9ja kickoff strings have no timezone | Verify historical timezone; never silently assume UTC |
| Bet9ja types `0`/`1`/`2`, missing SportyBet types also present | Verify provider structure mapping before ticket aggregation |
| Native event/market/outcome IDs exist on all sample selections | Preserve provider IDs; external source mappings still need proof |

Inventory sport, league/country clues, market/pick, native IDs, specifier/line,
period, type/system fields, observation time, completeness, first/last kickoff,
missing fields, affected-ticket counts and unique fixtures. Unknown combinations
must not be assigned a rule by market-label substring alone.

All 164 sample snapshots have an observation time and an existing raw-response
path. If raw evidence is needed for a type/time/rule mapping, freeze and hash its
actual bytes in the evaluation evidence. A missing path must disable the affected
capability explicitly; re-decoding now is not historical recovery.

## 4. Source capabilities and access

Keep the proposed chain configurable: Football-Data, openfootball, then configured
API-Football when useful. The order is provisional until actual coverage and
permitted access are checked. GOAL/Highlightly remain unverified research candidates,
disabled and outside the first implementation.

| Source | Verified constraint | Gate before relying on it |
| --- | --- | --- |
| [Football-Data fields](https://www.football-data.co.uk/notes.txt) and [usage page](https://www.football-data.co.uk/data.php) | FT/HT CSV data and some statistics; published usage restrictions | Confirm intended automated use fits conditions before enabling downloads. Support user-supplied CSV snapshots with provenance; free availability is not assumed unrestricted automation permission |
| [openfootball](https://github.com/openfootball/football.json), [CC0 license](https://github.com/openfootball/football.json/blob/master/LICENSE.md) | Generated JSON depends on upstream updates | Verify target league/season and actual score freshness; daily generation does not guarantee recent results |
| [API-Football pricing](https://www.api-football.com/pricing) | Free offering currently lists 100 requests/day and season limits | Check configured account's access, quota and league/season/field coverage; no implicit paid upgrade |
| [API-Football official guide](https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide) | Scores, events and statistics are distinct capabilities | Verify completeness for sampled fixtures; result-source facts are not bookmaker settlement rules |

For each source+competition+season, record access mode, sports, date/time semantics,
terminal-state semantics, score periods, statistics/timeline availability, update
expectations, credentials, limits and observed gaps. Include split-year/calendar-year
seasons and cups. A league name on a coverage page is not field-level proof.

Fallback must be capability-aware: a matched full-time fixture cannot stop fallback
when the selection needs half-time facts or goal order. Missing facts remain null,
not zero/false. Join sources only after mapping them to the same fixture and period.
Contradictory final scores or identities yield `source_conflict`, with observations
preserved. A pinned resolution requires evidence, author/time and a new revision.

Handle 401/403, 429, timeout, HTML/error payloads, invalid CSV/JSON, schema changes,
pagination, missing fields and API errors inside HTTP-success responses. None is
proof of a draw/loss or that a fixture does not exist. Bound retries/backoff, respect
retry instructions, share fixture lookups and expose retryable quota failures.
Credentials stay server-side and out of logs/reports.

## 5. Fixture matching and time

Native IDs exist but are provider-scoped. A provider ID cannot automatically be
used as an API-Football/openfootball ID. Retain original identifiers and display
strings; record every verified cross-source mapping and its version.

Matching order:

1. Previously verified provider-event to source-fixture mapping, still consistent
   with sport, competition, teams and observation context.
2. Exact canonical competition, ordered home/away aliases, compatible season and
   date/time evidence, and exactly one plausible fixture.
3. Fuzzy candidate proposals for review. Fuzzy name similarity alone does not
   establish an accepted settlement mapping in the first release.

Preserve women, youth age, reserve/B-team, national/club distinctions and similar
club names. Retain orientation; reversing teams needs a verified mapping. Multiple
plausible fixtures mean `ambiguous_fixture`, never first candidate wins.
Postponements/rematches require identity and schedule history, not an unbounded
search around the date until a convenient score appears.

Verify Bet9ja's historical naive-time meaning against raw payload semantics and
known fixtures. Existing `_kickoff_dt()` assumes UTC; that is not evidence.
Store original time, interpreted UTC, precision and timezone evidence. Unknown
zones allow only independently verified date/identity matching; do not invent an
offset. Date-only sources remain date-only, not midnight UTC. Test DST, date
boundaries and rescheduling. For verified aware times, the first-release exact-time
match tolerance defaults to 15 minutes, configured/versioned. Date-only matches
require all other exact constraints and uniqueness; manual exceptions are recorded,
not silently incorporated by widening the tolerance.

Preserve `observed_at`: a known post-kickoff observation must not enter a pre-match
performance cohort. Unknown timing remains explicitly unknown, not silently dropped.

Reuse sports-skills selectively, with license attribution: normalization/map/HTTP
patterns are useful references. Its `_teams_match()` accepts Arsenal/Arsenal Women,
Chelsea/Chelsea U21 and Inter/Inter Miami; do not copy it unchanged. Its openfootball
normalizer zero-fills missing scores and fabricates timezone information; write an
evidence-preserving adapter. Its in-memory cache is not durable history or a shared
persistent quota counter.

## 6. Rules, fixture finality and ticket aggregation

Keep these separate: matching, fixture state, required-data completeness, rule
support and selection outcome. `matched = true` does not imply evaluable.
Leg outcomes: `won`, `lost`, `void`, `pending`, `unresolved`. Each retains source
facts, rule ID and reasons, including `unsupported_sport`, `unsupported_market`,
`unsupported_ticket_type`, `unknown_timezone`, `ambiguous_fixture`, `source_conflict`,
`missing_period`, `incomplete_timeline`, `quota_exhausted`, `invalid_input` and
`result_not_yet_available`. Reasons can overlap; primary outcome states cannot.

Dispatch rules on provider, sport, native market/outcome, specifier, period and
verified aliases. Version the mapping, line parser and rule. Do not assume
`Handicap` means Asian/European or assume which side a signed line applies to.
Quarter lines/split returns remain unresolved until a fractional-return model is
implemented and verified; never approximate them with a binary full win/loss.
Use exact decimal/rational line arithmetic and integer scores; avoid floating-point
rounding at push/quarter-line boundaries. Keep display rounding out of decisions.

| Family / observed variant | Evidence needed before enabling a rule |
| --- | --- |
| Football regulation 1X2, double chance, BTTS, correct score | Verified regulation final score and provider outcome-code meaning |
| Totals/team totals/multi-goal/score groups | Exact line/range/group membership and period; push handling where applicable |
| HT/second-half/either-half/highest-half/HT-FT | Required period scores, valid period arithmetic and tie rules |
| Combined AND/OR markets | Exact boolean expression plus every required component; punctuation is not a rule |
| European/Asian handicap and integer/quarter totals | Provider line/side semantics and push/split-return model |
| Corners/cards/offsides and period variants | Correct statistic, definitions, period and complete source coverage |
| 1UP/2UP/never-down/early-goals/consecutive goals/first-goal method | Provider terms and complete ordered timeline with required event detail; final score is insufficient |
| Basketball/American football/tennis/handball/other sports | Separate sport-specific sources, finality and rules; unresolved until implemented |

Register every inventoried combination as supported with evidence or explicitly
unsupported with affected-ticket counts. Full coverage has not been achieved while
required combinations remain unsupported. Add support through this registry;
do not hide difficult tickets or redefine the goal as only the easiest markets.

Kickoff passing or a score being present is not finality. Keep regulation, extra-time
and penalty-shootout scores distinct. AET/PEN cannot overwrite 90-minute facts.
Postponed/abandoned/cancelled/awarded fixtures are not automatically void bets;
apply documented provider/market rules, otherwise pending/unresolved with reasons.
Timeline completeness must handle own goals, stoppage time, event order, cancelled
goals and corrections before enabling sequence/promotion rules.

Verify Bet9ja numeric type codes and SportyBet types/system selections against
provider evidence. Unknown types must not become assumed accumulators. An optional
`all_recorded_selections_won` proxy is descriptive, separate from ticket outcome,
and does not count as a confirmed ticket win. Systems/builders need their own rules.

For a verified ordinary accumulator, with trustworthy selected legs and applicable
provider rules, aggregation precedence is:

1. Any final losing leg gives `lost`, even with other pending/unresolved legs,
   after applying promotion/void rules. This is not a shortcut around those rules.
2. Otherwise any unresolved leg gives `unresolved` (also retain pending blockers);
   otherwise a pending leg gives `pending`.
3. All legs void gives `void`; at least one won and all others won/void gives `won`
   under verified accumulator void rules.

Missing/conflicting input cannot be silently repaired to manufacture a win. Retain
and evaluate all legs for audit even when a loss determines the ticket result.
Payout and hypothetical money return remain unknown unless independently supported.

## 7. Percentages and accounting

For the explicitly named population, N is retained ticket observations after the
exact-duplicate policy. Require **N = W + L + V + P + U** (won/lost/void/pending/
unresolved). Always return numerator/denominator; zero denominators produce null
with a reason, not a misleading zero rate.

| Metric | Formula / interpretation |
| --- | --- |
| Confirmed wins across retained codes | W/N; lower bound while P/U remain, not final success probability |
| Decided win/loss rate | W/(W+L); explicitly excludes void/pending/unresolved |
| Outcome classification coverage | (W+L+V)/N; not complete per-leg evidence coverage |
| Eventual binary-win bounds | W/N to (W+P+U)/N conditional on retained population/evidence; not a statistical confidence interval |
| Leg coverage and selection success | Separate leg denominators; distinguish occurrences from unique selections |

Hypothetical example: N=100, W=2, L=60, V=5, P=20, U=13 gives 2% confirmed wins,
3.23% decided win/loss rate, 67% classified and 2%–35% eventual binary-win bounds.
These are examples, not measured rates from the current runs.

Report evaluation time and evidence completeness. Losses can be known before other
tickets finish, biasing the decided rate early. Also show a cohort with confirmed
fixture completion, its membership and unknown maturity. Last scheduled kickoff
alone cannot prove a ticket's fixtures have finished.

Provider/run/odds-band groups use recorded odds and fixed versioned band boundaries.
League/market ticket groups overlap; label that and do not sum their percentages.
Use separate leg breakdowns for league/market diagnostics. Selected/distinct-subset
comparisons need persisted decision-time membership: computing a subset after results
is not proof it was selected then. Shared games make tickets dependent; historical
rates are not calibrated future probabilities, independent trials or expected return.

## 8. Architecture, durable evidence and API

Use tracked code under `portfolio/results_tracking/`. The original proposed
`ticket-analysis/results/` is ignored by `.gitignore`'s `**/results/` rule.
Runtime reports can live under the existing ignored run tree.

```text
portfolio/results_tracking/
  inputs.py       # immutable population, validation and inventory
  sources/        # adapters and capability matrix
  fixtures.py     # shared fixture/period/state/provenance models
  matching.py     # mappings, aliases, candidate evidence and ambiguity
  rules/          # provider/sport/market and ticket-structure rules
  reporting.py    # reports, accounting, metrics and revision differences
  cache.py        # persistent responses and shared source quota control
  jobs.py         # bounded resumable result jobs, separate from harvests
  __main__.py     # audit, evaluate, inspect and offline replay CLI
```

Each report revision retains frozen extract bytes/digest, raw evidence actually
used, source URL/fixture ID/fetch time/content hash, normalized fixture observations,
matching candidates/decisions, alias overrides, rule/matcher/adapter versions,
configuration, as-of time and optional position-input digest. IDs are local and
validated; callers cannot choose arbitrary filesystem paths or network URLs.

Write into a new revision directory and publish its manifest atomically only after
hash/count checks. Interrupted directories are not reports. Keep the last good
report if refresh fails. New source facts/mappings/rules create revisions with
changed outcome reasons. Offline replay uses archived evidence only, reproduces
accounting and decisions, and performs neither wall-clock expiry nor network access.

Cache keys include source, competition/season/fixture, endpoint parameters, period
and parser version. Preserve raw bytes and nulls. Initial refresh policy: pending/
no-result data becomes eligible for recheck after 15 minutes, terminal data after
24 hours; source update schedules/quotas may lengthen these intervals. Show age and
next retry. Authentication/rate failures are not negative fixture evidence.
Explicit refresh differs from replay. Manual evidence/overrides need provenance,
author/time and a revision; they never silently replace originals.

Share/deduplicate fixture requests across codes/runs; serialize quota updates.
Result jobs persist progress and resume completed work. One active evaluation per
run/input/config prevents duplicate charges. Bound worker/source concurrency,
per-call timeout, retries and per-job request budget. Cancellation stops new calls,
preserves evidence and leaves the last complete report intact. Harvesting is separate.

Job states are starting/running/succeeded/failed/cancelled with durable timestamps
and structured errors. A succeeded evaluation means every population row received
an accounted outcome and a manifest was published, not that all fixtures finished
or all tickets were resolved. Source failures must not erase previously validated
facts on refresh; keep them with their age/refresh-error evidence until a verified
correction supersedes them. Fatal input/integrity/publication failures publish no
completed revision. Jobs need not wait for pending matches to finish.

Backend API contract:

- `POST /api/results/<run_id>` with validated optional `refresh`/`allow_partial`:
  202 + result job ID, or equivalent existing active job. No client-supplied keys,
  paths, URLs, rule overrides or unbounded budgets.
- `GET /api/results/<run_id>`: latest complete summary and revision ID;
  404 `report_not_found` when absent, not a zero-win report.
- `GET /api/results/jobs/<job_id>`: durable state, input digest, progress,
  quota/retry information, revision or structured error.
- `POST /api/results/jobs/<job_id>/cancel`: idempotent cancellation request.
- `GET /api/results/<run_id>/codes?revision=...&offset=...&limit=...`: bounded
  stable rows/leg evidence from one revision, with total count.

Preserve loopback/Origin/JSON/method/size protections. Use known 400/404/409/422
errors for bad options, unknown IDs, unavailable snapshots and invalid extracts.
Asynchronous source failures live in job/report reasons, not false outcomes.
No HTTP request waits for a whole run's external fetching.

Positions are read-only optional context. `positions.py` currently normalizes
expired tickets as invalid and holds user-supplied unverified records; do not reuse
that path to determine historical outcomes. Link exact provider/code and compatible
contents. Missing supplied positions mean “no matching supplied position,” not
“never played.” Do not rewrite actual payouts or settle the ledger; discrepancies
are review flags. This integration must not block the main counterfactual report.

## 9. First usable output and later UI

The backend includes CLI help, inspectable JSON and a human-readable report with
run/population, five counts, numerator/denominator metrics, evidence age, unresolved
causes, original legs and proof references. Show paths/revision IDs for replay.
Old runs can be evaluated without a new scan. All 164 sample tickets receive rows,
even if many cannot be resolved yet.

Stage 4 UI remains a separate follow-up reading the same reports. It is not gated
on Stage 2 analysis: a saved history run is enough, with explicit incomplete mode
for partial runs. Include started tickets without requiring the Stage 1 toggle.
Pagination/filters change visible rows, not headline percentages. Show check time,
refresh/job progress, reasons and source proof. Use “winning codes with no matching
supplied position,” not an asserted list of tickets the user never played.

## 10. Build sequence and acceptance gates

1. **Population audit:** freeze/inventory both real runs; verify 102+62 rows,
   digests, sports/types/times/markets and raw evidence availability. Establish
   denominator fixtures before source retrieval.
2. **Bounded feasibility spike:** verify permitted source access, actual season/
   league/field coverage and cost. Hand-check at least 20 distinct events across
   both providers, major/lower leagues, cups, team qualifiers, naive/date-only times
   and missing results. Add completed older events if the 23 October set is not
   final. Record requests/quota needs. Revise the source strategy when required
   facts are unavailable rather than stretching score-only data into false results.
3. **Versioned mappings/rules:** verify type/time mappings. Register every observed
   market combination with support status and ticket impact. Enable each rule only
   with required source facts and documented provider semantics, checked against
   positive/negative/boundary cases. Missing evidence leaves rules disabled.
4. **Local evaluator:** implement historical import, matching, five-state outcomes,
   metrics and immutable reports. Require deterministic offline replay and negative
   tests before network batching or API integration.
5. **Persistent retrieval/jobs:** add verified adapters, fallback, budgets/cache,
   refresh/revisions, interruption recovery and API. API-Football needs configured
   access; new vendors need their own evidence review.
6. **Acceptance evaluation:** report every one of the 164 retained tickets,
   unresolved reasons and coverage by sport/market/league/type. Require zero false
   accepted matches and zero wrong claimed outcomes in the hand-checked corpus;
   publish its size, not a claim of general perfection. Any claimed winner needs
   evidence for all required selected legs and ticket rules. Zero confirmed wins
   with uncertainty is not “we have never generated a winning code.”

Acceptance tests must include:

- Row conservation for expired/malformed/mixed-sport/unsupported tickets; exact
  duplicates, conflicting snapshots and cross-run aliases; changed digests rejected.
- Women/U21/reserve/B teams, similar competitions, reversed orientation, neutral
  venues, repeated/postponed fixtures, DST/midnight, ambiguity and provider ID scope.
- Missing versus 0–0 scores, absent HT, regulation versus AET/PEN, cancelled/abandoned/
  awarded fixtures, integer/quarter lines, combined markets, incomplete/corrected
  timelines, promotions, unsupported sports/types and unknown timezones.
- Known-loss plus unknown leg, all void, pending/unresolved precedence, unknown
  structure despite all selection wins, and systems when supported. Score presence
  or elapsed time must not establish finality.
- N=W+L+V+P+U, zero denominators, section 7's example, maturity/early-loss bias,
  overlapping strata, dependent codes and no denominator changes from display filters.
- Missing required source fields, conflicting scores, stale files, 401/429, HTTP-200
  errors, malformed/partial responses, unavailable credentials/seasons, quota limits
  and bounded retries; failures never fabricate outcomes.
- Concurrent identical jobs, failed refresh preserving the previous report,
  cancellation/restart, atomic manifests, evidence/rule revisions, offline replay,
  secret redaction and read-only positions.

These gates distinguish **software correctness**, **actual result-source coverage**
and **provider-rule evidence**. Passing one does not establish the others.
