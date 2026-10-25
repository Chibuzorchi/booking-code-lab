# F9 implementation review — 24 October 2026

Reviewed HEAD: `c8a771e` on `feat/f9-population-audit`, including the four commits
in the supplied summary and the additional API-Football client commit at HEAD.
Review only: no implementation files changed, no external result requests made,
no API key read or used, and no outcome claims generated.

Verdict: useful foundation, but not ready to trust as a settlement pipeline.
The test suite passes while several important fail-closed and preservation
contracts remain unmet. Steps 1 and 3 should be called partial against the plan.

## Verified evidence

- Full existing suite: **300 tests passed** in 71.695 seconds. The supplied 289
  count applies before the additional 11 API-Football tests. An existing unclosed
  file ResourceWarning appears without failing the suite.
- Real audit CLI reproduced 102 Bet9ja rows / 826 provider fixtures and 62
  SportyBet rows / 605 provider fixtures, using expected providers/counts.
- Real SportyBet classification reproduced the entire published 966-selection
  table, including two rejected Handball selections.
- Thirty tickets have only supported classifications among their in-scope legs;
  only **nine** have every original ticket leg both in scope and classified
  supported. Neither number establishes source availability, evaluator success,
  ticket structure, or a ticket win.
- Local `archive/E0.csv`: 70 fixtures, 21 August–19 October 2026, SHA-256
  `705ef4373f6c4c9ec09b67eb3ca3398d6a770813095efc5d629edb591e4ee41c`.
  All selections in the two audited runs start on 23 October or later: no fixture
  from those runs can be resolved by the dated results in this particular file.
- Source feasibility's remote file listings/freshness were not re-fetched in this
  code review. Its permanent-coverage conclusions should not be treated as newly
  verified here. The manual importer was checked solely against local bytes.

## Findings, ordered by severity

### R01 — P1: unknown market names become supported goal rules

Location: `portfolio/results_tracking/rules/registry.py:91`.

The `market.endswith(" Over/Under")` branch contradicts the claimed exact registry.
Both `classify("Cards Over/Under", "Football")` and
`classify("Unknown Promotion Over/Under", "Football")` return supported
`team_total_over_under`. They are not verified team-goal markets. The preceding
block rejects Corners/Offsides/Statistics, but that denylist cannot make the
catch-all safe. A caller resolving a side can then evaluate a non-goal market
against goals and manufacture a wrong win/loss.

Required: a verified team-market mapping or a validated team-name match against
the resolved fixture, with explicit rejection of unknown modifiers/statistics.
Test unseen suffix-matching names, not only a random unrelated market name.

### R02 — P1: unsupported or malformed totals are assigned binary outcomes

Location: `portfolio/results_tracking/rules/football.py:33` and `:61`.

`over_under("Over 2.25", "total=2.25", RegulationScore(2, 0))` returns `lost`.
The plan explicitly leaves quarter/split-return lines unresolved until their
model exists; this parser accepts arbitrary decimals and has no such guard.
The same issue exists in `team_total_over_under`.

Additionally, `parse_over_under("Over 2.5", "total=3;period=HT")` accepts 2.5:
a nonempty unrecognized specifier is silently ignored when the pick has a line.
This bypasses the intended redundant-line integrity check. Float parsing also
violates the plan's exact arithmetic requirement and can round differing lines
into apparent agreement.

Required: exact decimal/rational parsing, explicitly supported line increments,
and rejection of nonempty malformed or unsupported specifiers. Retain separate
reasons for unsupported settlement semantics versus malformed data.

### R03 — P1: the population audit does not freeze its inputs

Location: `portfolio/results_tracking/__main__.py:48`.

An actual CLI execution produced only two run-summary JSON files, `summary.json`
and `manifest.json`. `audit_run()` holds the verified raw extract only in memory;
the CLI never writes it. Its row entries contain code/counts rather than original
selections. A digest and path identify an extract but do not preserve it if the
source disappears. The manifest protects only these summaries.

Required: write the exact registry-verified extract bytes alongside the audit and
include them in the manifest; never reopen the path to copy potentially changed
bytes. Preserve raw evidence bytes when they are used for later mappings.
Prove replay still works after original sources are removed. Publish the manifest
atomically as the completion marker, rather than exposing a partially written one.

### R04 — P2: actual Bet9ja totals fail despite advertised format support

Location: `portfolio/results_tracking/rules/football.py:16`.

The real extract uses `pick="Over", specifier="2.5"`, not `total=2.5`.
All **319 football selections** labelled exactly `Over/Under` in yesterday's
Bet9ja run fail `parse_over_under()`. The test named for Bet9ja uses SportyBet's
`total=...` representation and therefore does not exercise the provider's bytes.
Bet9ja's actual `Goal/No Goal` / `GG` vocabulary is also unregistered/unparsed.
Bet9ja is excluded from the current league-scoped classification pass, so this
does not change that table; it invalidates the broader both-provider claim.

Required: provider-specific raw fixtures and parsers, or explicitly mark Bet9ja
unsupported for these rules until implemented. Do not silently expand ambiguous
Bet9ja league matching to exercise them.

### R05 — P2: period-specific registry entries lose their period

Location: `portfolio/results_tracking/rules/registry.py:76`.

HT and second-half markets return the same FT-default evaluator with no bound
period metadata. For FT 2–0 / HT 0–0, the evaluator returned by
`classify("1st Half - Over/Under", "Football")`, called with
`("Over 0.5", "total=0.5", score)`, returns `won`. Explicit `period="HT"`
correctly returns `lost`. No settlement dispatcher exists yet to supply this,
so this is an integration trap rather than evidence of already mis-settled tickets.

Required: carry period in Classification or bind a period-specific wrapper, and
test the registry-to-evaluator path. Also document or normalize differing evaluator
signatures; the common `(pick, specifier, score)` description is not universal.

### R06 — P2: raw-file availability reports check only a nonempty path

Location: `portfolio/results_tracking/inputs.py:87`.

A ticket with `raw_response_path="/definitely-does-not-exist/f9.json"` produces
an empty `raw_response_missing` list. The summary's claim that deleted files are
detected is false. The existing simple-audit test creates no `/tmp/Q1.json` but
expects an empty missing list, encoding this gap.

Required: distinguish absent path, missing/unreadable file and available bytes;
use controlled real temporary files in tests. Availability at audit time is not
the same as preserving the file for replay.

### R07 — P2: audit conflicts and denominator validation are incomplete

Location: `portfolio/results_tracking/inputs.py:110`.

Conflicts are indexed by full leg identity, not event identity. Two selections
with the same native event but different markets and different kickoff dates
produce `unique_fixtures = 1` and `conflicting_observations = []`. Fixture metadata
can therefore conflict undetected unless precisely the same leg repeats.
Two different snapshots of the same booking code are also accepted as two rows;
the plan requires conflict validation and an exact-duplicate accounting policy.

Required: separate fixture-metadata and leg-metadata conflict indexes, report the
affected row/selection IDs, and detect exact duplicate versus conflicting code
observations before publishing a population denominator. Keep malformed/empty
selection containers explicit rather than silently turning them into an empty list.

### R08 — P2: CSV importer silently drops an away-only half-time score

Location: `portfolio/results_tracking/sources/football_data_import.py:117`.

With FTHG/FTAG/HTHG blank and HTAG=1, the importer returns `score=None` instead
of raising `FootballDataImportError`. The no-FT branch checks only `ht_home`.
This violates its documented rejection of HT facts without FT and paired halves.
Rows shorter than their header can also leak AttributeError from `.strip()` on
None instead of the documented import error.

Required: check both HT fields independently of FT availability, validate row
shape, and add symmetric malformed-row tests. Local E0's successful parse does
not exercise these cases.

### R09 — P2: coverage language needs additional distinctions

Location: `docs/F9_RULE_REGISTRY.md:91` and the supplied work summary.

The published 30-ticket measure explicitly ignores out-of-scope legs. Only nine
tickets have every leg in scope and classified supported; even those have no
verified ticket type or matched complete result evidence. Use separate counts for
classified, rule-input-valid, source-matchable and ticket-evaluable.

The manual E0 file proves import/freshness for one division, not usable result
coverage for these 164 tickets: none of their dates are in its result range.
This does not make the importer useless; it means pending/missing-results handling
must remain explicit as files are updated.

### R10 — P2: timezone verification is not scoped to its evidence

Location: `portfolio/results_tracking/rules/provider_types.py:22`.

The lookup is documented as verified for three fixtures “in this run,” but keys
contain only provider, event label and naive time. They do not include run/extract
digest, native event ID, competition or sport. The earlier review already showed
that display labels can collide. Another observation with the same strings can
inherit this verification without matching the evidence that established it.

Required: key the decision to verified event identity and archived evidence, with
run/input scope and provenance. Cross-provider timestamp agreement is limited
corroboration, not a documented universal provider timezone mapping.

### R11 — P2, extra HEAD commit: malformed API envelopes look like no fixtures

Location: `portfolio/results_tracking/sources/api_football.py:62`.

Using a fake session without external requests: `{}` returns `[]`,
`{"response": {}}` returns `{}`, `{"response": null}` returns None, and a top-level
list leaks AttributeError. The method promises `list[dict]` and a response-shape
error contract, but does not validate either. Missing/malformed results must not
become a successful empty fixture lookup.

Required: validate object/envelope/list/item shapes and pagination completeness,
then raise a structured ApiFootballError. Add missing/null/wrong-type tests.
The client also adds `requests` without a ticket-analysis dependency manifest;
document/install this requirement or retain a standard-library transport. Quota,
raw-response archiving and persistence remain future pipeline work, not features
established merely by manual triggering.

## Recommended order before continuing

1. Close R01/R02 fail-closed rule guards and bind periods before wiring settlement.
2. Make the step-1 artifact independently replayable and validate its population.
3. Add provider-real inputs and malformed CSV/API tests, then revise support claims.
4. Continue fixture matching and ticket aggregation only with verified source facts
   and the existing unverified-type gate intact. No current test or classification
   count establishes the requested historical ticket win percentage.
