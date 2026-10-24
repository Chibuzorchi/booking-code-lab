# F9 rule registry — step 3, narrowed to openfootball-scoped leagues

Plan step 3 (`F9_RESULTS_PLAN.md` section 10): verify type/time mappings and
register every observed market combination with support status and ticket
impact. Scope is narrowed, per explicit instruction, to the 8 leagues
openfootball can in principle supply a score for (see
`docs/F9_SOURCE_FEASIBILITY.md`): `England · Premier League`,
`England · Championship`, `Germany · Bundesliga`, `Spain · LaLiga`,
`France · Ligue 1`, `Italy · Serie A`, `Netherlands · Eredivisie`,
`Portugal · Liga Portugal`. Only SportyBet's unambiguous "Country · League"
display strings are used to select in-scope selections; Bet9ja's bare
league-name strings stay excluded (`ambiguous_fixture`, league identity
unverified) pending step 5's real fixture matching.

This is a registry of what's *evaluable*, not a settlement run: no fixture
is matched to an openfootball score here, no ticket is scored. It classifies
every real (market, sport) combination observed in the audited run as either
supported (bound to an evaluator in `rules/football.py`) or unsupported
(with one of the plan's named reasons), and reports selection/ticket counts
for the one real run audited in step 1
(`run-sportybet-20261023-070118-f6753695`, 62 qualifying tickets).

## What's implemented and why

`rules/scores.py` defines `RegulationScore`: the exact shape openfootball's
score object can supply (full-time goals, optionally half-time goals), with
validation that rejects data-integrity violations (negative goals, a bool
passed where an int is required, half-time present for only one side,
half-time goals exceeding full-time goals for the same side) rather than
silently repairing them.

`rules/football.py` implements pure evaluators, strictly against
`RegulationScore`, for every market family in the real data that regulation
full-time (and, where needed, half-time) goals alone can resolve:
`regulation_1x2`, `double_chance`, `goal_goal_no_goal`, `over_under`,
`odd_even`, `correct_score`, `win_either_half`, `team_total_over_under`.
Each handles both providers' real pick-string vocabularies (e.g. Bet9ja's
bare `"Over"`/`"Under"` pick with the line in `specifier`, versus
SportyBet's line embedded directly in the pick string, cross-validated
against `specifier` when both are present) and the real integer-line
push/void case found in the audited data (`total=2`, `total=3`).
`team_total_over_under` and `win_either_half` take an already-resolved
`side` ("home"/"away") as an explicit parameter — resolving which named team
("Chelsea", "Bayern Munich") maps to which side is a fixture-matching
concern the plan assigns to step 5, not to this package.

`rules/registry.py`'s `classify(market, sport)` gates on sport first
(`"Football"`/`"Soccer"` only), then dispatches by exact market string.
Every other observed combination — Handicap/Asian Handicap, 1UP/2UP/Never
Down, Early Goals and other minute-windowed markets, corners/offsides
statistics markets, combined AND/OR markets (`"Draw or Over 2.5"`,
`"Over/Under & GG/NG"`) — returns `unsupported_market` or
`incomplete_timeline` explicitly. Nothing is guessed into a status.

`rules/provider_types.py` records, as functions rather than claims to trust
blindly: `verify_bet_type` always returns `"unverified"` /
`"no_documented_provider_mapping"` for both providers' bet-structure fields,
because an Explore pass over `bet9ja/`, `sportybet/` and `ticket-analysis/`
found no code or documentation anywhere mapping Bet9ja's `bet_type` codes
or SportyBet's `bet_type`/`selectedSystems` fields to a named structure.
`is_verified_naive_utc` returns `True` only for the exact three
(provider, event, kickoff) triples hand-verified by cross-provider matching
in step 2 (Arsenal-Everton, Liverpool-Arsenal, Fleetwood Town-Arsenal) — it
does not generalize to any other fixture, even the same competition.

## Real classification pass — `run-sportybet-20261023-070118-f6753695`

Of 1,789 total selections across 62 qualifying tickets, 966 belong to the
8 in-scope leagues by SportyBet's display string. Of those 966, 2 are
Handball selections nominally inside the `Germany · Bundesliga`
league-string filter (`Asian Handicap` picks on handball fixtures) — the
sport gate in `classify()` correctly rejects both as `unsupported_sport`
rather than evaluating them as football, confirming the label-collision
risk recorded in step 2 is real and that league-string scoping alone would
have silently mis-evaluated them.

| status | rule_id / reason | selections | tickets |
| --- | --- | --- | --- |
| supported | `regulation_1x2` | 228 | 29 |
| supported | `over_under` | 242 | 21 |
| supported | `team_total_over_under` | 108 | 14 |
| supported | `double_chance` | 43 | 9 |
| supported | `goal_goal_no_goal` | 43 | 9 |
| supported | `win_either_half` | 18 | 3 |
| supported | `correct_score` | 6 | 1 |
| supported | `odd_even` | 1 | 1 |
| unsupported | `unsupported_market` | 243 | 21 |
| unsupported | `incomplete_timeline` | 32 | 7 |
| unsupported | `unsupported_sport` | 2 | 1 |

Ticket-level impact: 54 of the run's 62 tickets have at least one in-scope
leg; of those, 30 have every in-scope leg resolvable by a supported
evaluator, and 24 are blocked by at least one unsupported leg (mostly
`1X2 - 1UP/2UP/Never Down` timeline markets, statistics markets, and
combined AND/OR markets). This is a count of evaluable legs given a
hypothetically-known score, not a settlement result — no openfootball score
was actually fetched or matched for this pass, consistent with step 2's
finding that the one in-scope league's data is currently ~5 weeks stale.

## Still unverified, left disabled

- Bet9ja/SportyBet bet-type and selectedSystems semantics (`F9-04`):
  no documented mapping exists anywhere in the codebase; `verify_bet_type`
  reports this explicitly rather than guessing single/multiple/system.
- Naive-kickoff-equals-UTC (`F9-05`): verified for exactly three real
  fixtures in this run, not assumed for any other fixture, competition, or
  provider run.
- Bet9ja's bare league-name strings: excluded from this registry's in-scope
  selection entirely (not run through `classify()` at all), because league
  identity cannot be trusted without fixture-level verification (the
  Kazakhstan "Premier League" collision from step 2).
- Handicap/Asian Handicap, minute-windowed and 1UP/2UP/Never Down markets,
  statistics markets (corners/offsides), and combined AND/OR markets: no
  evaluator exists; each returns an explicit unsupported reason, not a
  silent skip.
