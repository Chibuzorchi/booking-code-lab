# F9 step 4 fix re-review — 24 October 2026

Reviewed commit `f2e68dc`. No implementation edits, external result requests or
real ticket outcome claims were made. All reproductions below use offline test
fixtures; population checks also use real audit-command output in temporary
workspaces, not just mocked row dictionaries.

Verdict: several original examples are fixed, but S4-01 through S4-05 cannot
yet be closed completely. S4-06, S4-07 and the reproduced UTC-offset issue in
S4-08 are fixed. A new reporting-path regression is also reproduced below.

## Verification

- Full suite: **436 tests passed** in 71.815 seconds. The existing ResourceWarning
  and expected CLI-negative-test messages remain; no suite failures occurred.
- Changed target teams/competition, contradictory aliases and conflicting source
  fixture observations now have conservative checks.
- Unknown/duplicate audited row IDs are rejected, and omitted audited rows become
  explicit unresolved rows. Raw evaluation evidence is preserved and manifested.
- Root-container and missing score-field examples from S4-05 are fixed.
- Cancelled/abandoned/awarded/unknown fixtures now receive named unresolved reasons.
- Literal Home/Away totals resolve directly; nonzero offsets in the field named
  `utc` are rejected. The ticket-type gate remains conservative.

## Remaining findings

### S4R-01 — P1: verified mappings still ignore kickoff consistency

Location: `portfolio/results_tracking/matching.py:261`.

Source teams/competition are now rechecked, but neither a verified kickoff nor
schedule-change evidence is recorded/checked in the mapping path. Keep a selection
on 24 October 2026 and its valid verified mapping; change only the target fixture
kickoff to 1 January 2020. The pipeline still returns a matched, won leg. The exact
tier's kickoff check does not protect tier 1.

Required: check current kickoff consistency and/or pin verified temporal identity
with explicit rescheduling evidence. Changed schedules must be deliberate mapping
revisions, not a reason to accept an unrelated date under an unchanged source ID.
Add a target-date-only mutation test, independently of team/competition mutations.

### S4R-02 — P1: conflicting verified mappings still select the first entry

Location: `portfolio/results_tracking/matching.py:235`.

Aliases and fixture observations are validated, but `verified_mappings` are not.
Supply two mappings for the same provider/event ID and provider context, targeting
two fixtures with different source IDs and opposing scores. Both target fixture
records are individually valid and match their recorded source team/competition
evidence. Reversing the mapping list changes the leg from `won` to `lost`.

Required: validate mapping identity/scope before use, reject contradictory active
targets or require an explicitly selected revision, and permit only exact duplicate
entries without changing the decision. Test permutations of mapping lists as well
as alias and fixture lists.

### S4R-03 — P1: row membership does not bind the original ticket or selections

Locations: `portfolio/results_tracking/__main__.py:162` and `:196`.

The loader verifies listed artifact hashes but uses only report-row metadata; it
does not import the preserved extract's ticket/selection facts. `evaluate_run()`
then checks only that a row ID exists and is not repeated. It does not compare the
supplied ticket/provider/type/legs with the original retained observation.

Reproduction using an actual temporary audit: a Bet9ja ticket named `ORIGINAL`
contains an Alpha–Beta away selection. Attach that audit's valid row ID to the
test helper's SportyBet Arsenal–Everton home selection and rename the ticket
`REPLACEMENT`. Evaluation accepts it and reports `proxy_all_legs_won = true`.
The audited row metadata still says `ORIGINAL`; neither provider nor contents agree.

Thus the new option anchors row count, not what would have happened to the saved
codes. Deleted/substituted selections within a supplied row remain undetected.
Unknown/missing rows are handled correctly, but that alone does not satisfy S4-03.

Required: derive original ticket facts from the hash-verified frozen extract,
attach fixture evidence to those legs, and validate any supplied cross-reference
against stable original selection identities. Preserve provider/observation/type
information and apply exact-duplicate/conflicting-code policy. Require manifest
coverage for every audit dependency read; do not treat unmanifested metadata or
an empty file map as a complete verified population.

### S4R-04 — P1: anchored reports cannot replay independently of the audit directory

Location: `portfolio/results_tracking/__main__.py:359`.

Copying `evidence.json` fixes synthetic-input preservation. An anchored report also
depends on `audit_rows`, but the output saves only an absolute audit-directory path,
not the population snapshot/audit dependencies or their manifest.

Reproduction: create a real two-row audit, supply evidence for one row, and evaluate
with `--audit-dir`. The report correctly has N=2 with the second row unresolved.
Move the original audit directory away. The evaluation output contains only
evidence/report/manifest JSON; replaying its preserved evidence alone has N=1,
while the stored audit path no longer exists. The omitted population row cannot
be reconstructed from the archived input evidence.

Required: preserve/hash the anchored population input and its dependency recipe
with the evaluation artifact, and provide a replay path that restores the same
population mode. Record matcher/input-schema/configuration versions as well as
rule versions. Test a missing evidence row and remove both original evidence and
audit directories before replay; equal N and all row decisions must survive.

### S4R-05 — P2: scalar field types remain unchecked

Location: `portfolio/results_tracking/__main__.py:110`.

Object/list checks fix the original container examples, but a supported leg with
`event_display: null` still reaches `.split()` and raises AttributeError. Required
key presence is not string validation. Comparable scalar fields and identifiers
also lack a uniform input contract.

Required: validate strings/IDs/provider consistency and kickoff payload values at
the parser boundary with contextual EvaluationInputError messages. Add null/wrong-
type scalar tests through the actual CLI, not only root/container validation.

### S4R-06 — P2: report generation bypasses the sport/market gate

Location: `portfolio/results_tracking/__main__.py:229`.

`evaluate_selection()` correctly returns unresolved before matching an unsupported
sport/market. The new per-leg reporting loop reruns matching unconditionally,
including for those rejected legs. A Tennis leg with a non-team event display,
`"not a team fixture"`, is first classified unsupported, then raises MatchingError
while its report is constructed. An otherwise accountably unresolved row can
therefore abort the entire evaluation instead of staying in the population.

Required: carry the original MatchDecision (or an explicit not-attempted value)
from the actual evaluated path into the report. Do not invoke an ungated second
matching pass just to obtain provenance. Test unsupported sports/markets with
unparseable event names and confirm they remain explicit unresolved report rows.

## Outcome and next checks

These are executable failures of remaining original guarantees or the new report
path, not demands for live adapters. Current unverified ticket types still prevent
confirmed ticket wins; incorrect leg/proxy decisions and population drift remain
material to the planned feature.

Keep step 4 partial until the four P1 guarantees are complete. Recheck with real
audit artifacts, mapping-order permutations, altered ticket legs under a valid row
ID, and replay after both source directories disappear. No implementation code
was changed by this review.
