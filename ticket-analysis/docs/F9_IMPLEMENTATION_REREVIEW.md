# F9 fix re-review — 24 October 2026

Reviewed HEAD `5b8fac0` on `feat/f9-population-audit`. Scope: fixes to the eleven
findings in `F9_IMPLEMENTATION_REVIEW.md`, including the API-Football client.
No implementation changes or live source/API requests were made in this review.

Verdict: the original P1 reproductions are fixed. Nine findings can be closed for
the current audit/rule-registry phase. R02 and R11 have residual P2 edge cases
described below; no ticket outcome should be inferred from this review.

## Verification

- Full test suite: **328 tests passed** in 71.968 seconds. The existing unclosed
  file ResourceWarning is still present but does not fail the suite.
- All **319** Bet9ja and **347** SportyBet football `Over/Under` occurrences in
  the real October 23 extracts now parse their recorded line representations.
- Unknown `Cards Over/Under` and `Unknown Promotion Over/Under` names are rejected.
- First-half over 0.5 with HT 0–0 / FT 2–0 now evaluates `lost` through the
  registry-returned evaluator, without a caller-supplied period override.
- A separate temporary-workspace test ran the audit CLI, removed its source run
  through test cleanup, then read the preserved extract and verified every
  manifest entry. Original selections and bytes remained available without the
  source tree. This establishes preserved population input, not a completed
  settlement replay engine. Provider raw-response files referenced by those
  selections are not themselves embedded by this audit.
- Re-derived the real SportyBet scope: 966 selection occurrences, with nine tickets
  having every original leg in scope and classified supported. Classification is
  still separate from source matching, required-fact availability and ticket rules.

## Original finding dispositions

| Finding | Disposition | Evidence / boundary |
| --- | --- | --- |
| R01 — arbitrary team-total suffix | Closed for current scope | Exact observed-name allow-list replaces the unrestricted catch-all; fixture-relative team/side verification remains required during matching |
| R02 — unsupported/malformed totals | Original examples fixed; residual P2 | 2.25 is unresolved; malformed redundant specifiers rejected; Decimal parsing is exact, but the supported-line arithmetic can still round |
| R03 — missing frozen extract | Closed for population preservation | CLI writes original verified bytes, checks digest, manifests nested extract files and atomically publishes completion marker; source-removal check passed |
| R04 — provider vocabulary | Closed for described vocabulary | Actual Bet9ja bare totals parse and GG/NG aliases are registered |
| R05 — dropped period | Closed for registered half markets | HT/2H evaluator binding added and tested through classification |
| R06 — missing raw files | Closed for file-existence detection | Real file checks and temporary-file tests replace trusting path strings; file readability/content preservation remain separate concerns |
| R07 — conflicts and duplicates | Closed for audit detection | Event-level conflicts and duplicate/conflicting code lists are separate; malformed selection containers are explicit. A future evaluator must enforce its conflict/denominator policy rather than treating flags as permission to settle |
| R08 — CSV away-only HT / required fields | Closed for reproduced cases | Symmetric HT validation and required-field checks reject the original malformed rows |
| R09 — coverage language | Closed | Documentation distinguishes nine whole-ticket classifications from thirty in-scope-only classifications and from actual evaluability |
| R10 — timezone scope | Closed for original cross-run leakage | Verification now requires the specific originating/corroborating run pair; native event identity and frozen mapping evidence remain necessary in future matching |
| R11 — API envelope/dependency | Original examples fixed; residual P2 | Empty/null/non-object envelopes and non-object list items are rejected; requests declared. Nested fixture/score containers remain unchecked |

## Residual findings

### RR01 — P2: Decimal supported-line check is context-rounded

Location: `portfolio/results_tracking/rules/football.py:29`.

`Decimal` construction and equality are exact, but division/modulo use the active
decimal context. At default precision, the following call returns `("lost", None)`:

```python
over_under(
    "Over 2.0000000000000000000000000001",
    "total=2.0000000000000000000000000001",
    RegulationScore(2, 0),
)
```

That line is neither an integer nor a half-integer; it should remain unresolved.
The division by 0.5 rounds the near-integer value before the modulo guard. A large
line such as `999999999999999999999999999999.5` raises `decimal.InvalidOperation`
instead of returning the evaluator's promised outcome/reason tuple. The team-total
evaluator uses the same guard. These are adversarial inputs, not values observed
in the retained sample, so this is a P2 validation hardening issue rather than
evidence that current real selections were mis-settled.

Required: a context-independent whole/half-line predicate, explicit supported
precision/range limits and conservative handling of decimal failures. Add boundary
tests beyond the default context precision and ensure unsupported values never
become binary outcomes or uncaught arithmetic exceptions.

### RR02 — P2: nested API fixture/score shape errors escape the error contract

Location: `portfolio/results_tracking/sources/api_football.py:82`.

Envelope validation fixes the original cases, but dictionary items are not yet
validated internally. These inputs raise AttributeError rather than ApiFootballError:

```python
extract_regulation_score({"fixture": None})
extract_regulation_score({
    "fixture": {"status": {"short": "FT"}},
    "score": None,
})
```

No external request is needed to reproduce either case. Malformed nested payloads
can therefore escape structured source-error handling in a future job runner.

Required: validate nested fixture/status/score/fulltime/halftime containers before
reading fields, distinguish unavailable period facts from invalid structure, and
raise ApiFootballError for malformed shapes. Test null and wrong-type containers.

## Next-step boundary

The audit now preserves its retained population and the normal rule reproductions
pass. Fix the two residual validation cases before treating the rule/source
boundary as fully checked. Fixture matching, actual source coverage, provider
ticket-type evidence, aggregation, durable result revisions and win-rate accounting
remain future work; passing these 328 tests does not establish that any code won.
