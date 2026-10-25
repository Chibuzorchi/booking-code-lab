# F9 step 4 review — 24 October 2026

Reviewed HEAD `1e153e3`: Fixture model, matching, aggregation/metrics and offline
evaluation CLI. Review only: no implementation edits, external result requests,
credentials or real-ticket win claims. Reproductions use constructed offline
evidence and the repository's own test helpers.

Verdict: step 4 is partial. The pure aggregation/percentage work and conservative
ticket-type gate are useful, but matching can produce wrong leg outcomes and the
CLI does not yet preserve replay evidence or bind the evaluated population to the
frozen harvest. Live retrieval can remain deferred; these local correctness and
preservation gaps do not require network integration.

## Verification and successful behavior

- Full suite: **407 tests passed** in 72.230 seconds. Expected CLI-negative tests
  print argparse/error messages; the existing unclosed-file ResourceWarning also
  appears. No regressions were reported by the suite.
- Unknown ticket types stay `unresolved`, including when every leg is marked won.
  The caller's evidence cannot directly set `structure_verified = true`.
- Fuzzy candidates are not automatically accepted. The exact tier rejects multiple
  plausible fixtures and preserves ordered team aliases in its normal path.
- Score-before-finality, missing regulation score after AET/PEN, missing HT facts,
  and zero percentage denominators have explicit conservative paths.
- The section 7 numerical metric example is covered and passes. Ratio values are
  distinct from a claim of observed real-ticket success.

The issues below are additional cases not covered by those passing tests. Current
ticket structure gating limits the impact to leg outcomes, descriptive proxies
and accounting/replay integrity; it does not repair the underlying matching error.

## S4-01 — P1: verified mapping bypasses target fixture consistency

Location: `portfolio/results_tracking/matching.py:185`.

Tier 1 checks only whether the selection's league/home/away display strings equal
the strings in the mapping. It then returns the first fixture with the mapped
source and ID, without rechecking that fixture's teams, competition, kickoff or
orientation against the verification evidence.

Reproduction through `evaluate_run()`: retain a verified mapping for an
Arsenal–Everton selection; supply its target source/ID with Chelsea–Fulham,
`competition="Wrong League"`, kickoff `2020-01-01`, and score 2–1. The original
Arsenal home-win leg is still `won`, with `proxy_all_legs_won = true`. The ticket
itself remains unresolved because its structure is unverified.

The comments also permit reversed source orientation through tier 1, but neither
the mapping nor MatchDecision carries an orientation transform. Home/away picks,
correct-score picks and literal-side markets would therefore be evaluated in the
source orientation without translating the provider's selection.

Required: recheck target identity/context against archived mapping evidence and
reject stale/inconsistent mappings. Explicitly model and apply an evidenced
orientation transformation, or reject reversed orientation until supported. Test
changed target teams/competition/date and reversal through the full pipeline.

## S4-02 — P1: conflicting evidence is resolved by list order

Locations: `matching.py:99` and `matching.py:189`.

`resolve_alias()` returns the first matching namespace/display alias. The verified
mapping branch similarly uses the first source/ID observation, and there is no
validation for conflicting aliases, mappings or fixture observations.

Reproductions:

- Supply two observations for one verified source fixture ID, one scored 2–1 and
  one 0–2. Reversing their order flips the same leg between `won` and `lost`.
- Supply conflicting `sportybet_team / Arsenal` aliases mapping to Arsenal and
  Chelsea, with matching source aliases/fixtures. Reversing the alias order flips
  the accepted fixture and the leg between `won` and `lost`.

Exact-tier ambiguity checks do not protect the tier-1 path or alias resolution.
Determinism for the same byte sequence is not conflict handling: equivalent
collections of contradictory evidence cannot be silently resolved by ordering.

Required: validate identity indexes before evaluation. Deduplicate exact repeated
evidence; reject or explicitly report contradictory aliases/mappings and source
observations. Selecting a newer revision must be an explicit pinned decision,
not an implicit array-order rule. Add order-permutation conflict tests.

## S4-03 — P1: historical population is not connected to the evaluator

Location: `portfolio/results_tracking/__main__.py:119`.

`evaluate` accepts an arbitrary `tickets` array without a run ID, frozen-extract
digest, audit manifest or row/leg correspondence check. The step-1 preserved
extract is not consumed. Repeating the exact same `ticket_id` and ticket contents
twice changes `counts.total` from one to two; dropping a ticket silently reduces
the denominator. Duplicate leg IDs and ticket-versus-leg provider inconsistencies
are also unchecked.

The standalone input is useful as a synthetic offline harness, but its percentages
cannot yet be labelled the retained-run win rates specified by F9. The plan's
step 4 explicitly includes historical import; that missing connection is local
input/accounting work, not a live source-adapter dependency.

Required: import/pin the frozen population, validate its hashes and enforce one
accounted row per retained ticket under the duplicate/conflict policy. Evidence
should attach to preserved original legs rather than replace them with a freely
edited leg list. Preserve observation times and original provider identities.
Keep synthetic-evidence mode explicitly separate if it remains useful.

## S4-04 — P1: output cannot independently replay its evidence

Location: `portfolio/results_tracking/__main__.py:233`.

An actual evaluation output directory contains only `report.json` and
`manifest.json`; the manifest hashes only the report. The supplied evidence JSON
is not copied or hashed. The report drops original selections, fixture facts,
aliases, verified mapping evidence, matching candidates/decisions, rule versions
and the selected source fixture IDs. Deleting or changing the input therefore
leaves no independently reproducible evaluation artifact.

`evaluate_run()` returning equal dictionaries twice proves repeatability while
its inputs remain available, not the plan's archived offline replay guarantee.
Full live response caches/jobs can wait for step 5; preserving the already-supplied
input and the decision recipe does not need a network adapter or job framework.

Required: preserve and hash the exact input snapshot, record population reference,
schema/rules/matcher/configuration/as-of versions and retain leg decision evidence.
Manifest those inputs with the report. Verify replay after removing the originals
and detect a changed/corrupted evidence file before returning an outcome.
Treat wall-clock generation metadata separately from deterministic decisions.

## S4-05 — P2: structural JSON failures escape the CLI error contract

Locations: `__main__.py:38`, `:54`, `:112`, and `:251`.

The parser checks key presence without validating root/container/field types.
A JSON root `[]` leaks AttributeError from `evidence.get()`. A fixture with
`regulation_score={"away": 1}` leaks KeyError for `home`. Both escape the CLI's
exception list instead of returning exit code 2 with an EvaluationInputError.

Required: validate the evidence schema and nested types with contextual paths;
use the same required-field helper for score facts. Invalid retained ticket rows
must follow the population-accounting policy rather than disappearing through
unchecked parsing. Add root-list/null, short score objects, wrong-type names,
duplicate IDs and inconsistent provider tests. Catching every exception without
validation would hide programming errors and is not the remedy.

## S4-06 — P2: all non-finished fixture states become pending

Location: `portfolio/results_tracking/evaluation.py:112`.

`CANCELLED`, `ABANDONED`, `AWARDED` and `UNKNOWN` each produce
`pending / result_not_yet_available`, exactly like a scheduled/in-play fixture.
There is no provider rule evidence making these normal pending outcomes, nor a
reason distinguishing an unknown state or an administrative result.

Required: an explicit state-to-evaluation policy. Scheduled/in-play and appropriate
rescheduling cases may remain pending; other cases require the verified
provider/market policy or explicit unresolved reasons. Do not automatically void
them either. Preserve the actual fixture state in decision evidence and test the
entire enum rather than only scheduled and one finished state.

## S4-07 — P2: literal Home/Away totals are registered but not resolvable

Location: `portfolio/results_tracking/evaluation.py:58`.

The registry supports `Home Over/Under` and `Away Over/Under`. Side resolution
extracts `Home`/`Away` as if they were club names and searches the team alias table,
rather than binding the literal side as it does for win-either-half markets.
On a correctly matched Arsenal–Everton fixture with a 2–1 score,
`Home Over/Under / Over 1.5` returns unresolved/invalid_input instead of won.

Required: explicit literal home/away handling, preserving any orientation policy
from matching. Do not require fake club aliases for the words Home and Away.
Test literal and named-team totals independently through the pipeline.

## S4-08 — P2: the UTC field accepts non-UTC values used as calendar dates

Locations: `portfolio/results_tracking/fixtures.py:82` and `matching.py:128`.

The CLI parses an offset datetime with `fromisoformat()` and KickoffTime checks
only awareness/evidence. A value named `utc` can remain `2026-10-25T00:30+02:00`,
whose UTC instant is October 24. Exact timestamp subtraction handles this correctly,
but date-only fallback calls `.date()` without normalization and uses October 25.
For a source calendar explicitly established as UTC, this rejects the correct
October 24 date and accepts October 25 instead.

Required: normalize interpreted instants to UTC or reject a non-UTC `utc` field.
Also retain/verify date-only sources' calendar basis rather than assuming every
date-only result is a UTC date. Validate actual raw date/time syntax and precision
consistency; a merely nonempty string is not time evidence. Add offset/DST/midnight
cases with an explicit source date basis.

## Scope boundary and next steps

Deferring live source retrieval, batching and HTTP jobs is consistent with the
plan. Unknown provider ticket types remaining unresolved is correct. The lack of
adapters alone is not a negative finding.

Before calling step 4 complete, fix target/conflict matching, connect the frozen
population and make provided-evidence reports independently replayable. Close the
schema/state/side/time cases before broader source integration. Add negative
end-to-end tests for these paths, then rerun the existing suite. No production
ticket win percentage has been established by the current offline tests.
