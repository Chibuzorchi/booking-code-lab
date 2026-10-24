"""Per-selection evaluation pipeline: classify -> match -> side-resolve ->
evaluate -> LegOutcome. This is the glue section 8's architecture sketch
doesn't name on its own, because it is the one place that necessarily touches
rules/registry.py, matching.py and fixtures.py together; it still keeps
section 6's five named concerns -- matching, fixture state, required-data
completeness, rule support and selection outcome -- as distinct, separately
reasoned steps inside one function, so a matched fixture never by itself
implies an evaluable leg.

Deterministic and offline: takes already-resolved candidate_fixtures,
aliases and verified_mappings as plain arguments (evidence a caller already
holds), fetches nothing, and is pure other than reading its inputs.
"""
from __future__ import annotations

from typing import Sequence

from .fixtures import Fixture
from .matching import Alias, MatchStatus, VerifiedMapping, match_selection_event, resolve_alias
from .reporting import LegOutcome, Outcome
from .rules import registry

# Side is literal from the market string for these two markets -- no lookup
# against the matched fixture is needed or possible (the market string names
# "Home"/"Away" directly, not a team name).
_LITERAL_SIDE_MARKETS = {
    "Home Team to Win Either Half": "home",
    "Away Team to Win Either Half": "away",
}

_TEAM_TOTAL_SUFFIX = " Over/Under"

# rule_id -> how to call its (already period-bound) evaluator, given
# (pick, specifier, score, side). Keeps the family-specific calling
# convention in one place rather than scattered through evaluate_selection.
_EVALUATOR_CALLS = {
    "regulation_1x2": lambda ev, pick, specifier, score, side: ev(pick, score),
    "double_chance": lambda ev, pick, specifier, score, side: ev(pick, score),
    "goal_goal_no_goal": lambda ev, pick, specifier, score, side: ev(pick, score),
    "odd_even": lambda ev, pick, specifier, score, side: ev(pick, score),
    "correct_score": lambda ev, pick, specifier, score, side: ev(pick, score),
    "over_under": lambda ev, pick, specifier, score, side: ev(pick, specifier, score),
    "win_either_half": lambda ev, pick, specifier, score, side: ev(pick, score, side),
    "team_total_over_under": lambda ev, pick, specifier, score, side: ev(pick, specifier, score, side),
}

_SIDE_REQUIRED_RULES = {"win_either_half", "team_total_over_under"}


def _resolve_team_total_side(market: str, provider: str, fixture: Fixture,
                              aliases: Sequence[Alias]) -> str | None:
    """"<Team> Over/Under" -> "home"/"away" by resolving both the market's
    team name and the matched fixture's own home/away display through the
    same alias table matching.py uses -- never by comparing raw strings, and
    never guessed when the team name doesn't resolve to either side."""
    if not market.endswith(_TEAM_TOTAL_SUFFIX):
        return None
    team_name = market[: -len(_TEAM_TOTAL_SUFFIX)]
    canonical_team = resolve_alias(aliases, f"{provider}_team", team_name)
    if canonical_team is None:
        return None
    canonical_home = resolve_alias(aliases, f"{fixture.provenance.source}_team", fixture.home_team)
    canonical_away = resolve_alias(aliases, f"{fixture.provenance.source}_team", fixture.away_team)
    if canonical_team == canonical_home:
        return "home"
    if canonical_team == canonical_away:
        return "away"
    return None


def _resolve_side(market: str, provider: str, rule_id: str, fixture: Fixture,
                   aliases: Sequence[Alias]) -> tuple[str | None, str | None]:
    """(side, reason). side is None with a reason when a side-dependent rule
    can't be resolved; (None, None) for a rule that needs no side at all."""
    if rule_id not in _SIDE_REQUIRED_RULES:
        return None, None
    if market in _LITERAL_SIDE_MARKETS:
        return _LITERAL_SIDE_MARKETS[market], None
    if rule_id == "team_total_over_under":
        side = _resolve_team_total_side(market, provider, fixture, aliases)
        if side is None:
            return None, "invalid_input"
        return side, None
    return None, "invalid_input"


def evaluate_selection(
    *, leg_id: str, provider: str, provider_event_id: str | None, event_display: str,
    league_display: str, sport: str, market: str, pick: str, specifier: str, kickoff,
    candidate_fixtures: Sequence[Fixture], aliases: Sequence[Alias] = (),
    verified_mappings: Sequence[VerifiedMapping] = (),
) -> LegOutcome:
    """Resolve one ticket leg to a LegOutcome, or explain why it can't be
    resolved yet. Order mirrors section 6's named concerns: market/sport
    support, then fixture matching, then fixture state/completeness, then
    side-resolution, then the rule itself."""
    classification = registry.classify(market, sport)
    if classification.status == "unsupported":
        return LegOutcome(leg_id, Outcome.UNRESOLVED, None, classification.reason)
    rule_id = classification.rule_id

    decision = match_selection_event(
        provider=provider, provider_event_id=provider_event_id, event_display=event_display,
        league_display=league_display, kickoff=kickoff, candidate_fixtures=candidate_fixtures,
        aliases=aliases, verified_mappings=verified_mappings)
    if decision.status == MatchStatus.AMBIGUOUS:
        return LegOutcome(leg_id, Outcome.UNRESOLVED, rule_id, "ambiguous_fixture")
    if decision.status == MatchStatus.UNMATCHED:
        return LegOutcome(leg_id, Outcome.UNRESOLVED, rule_id, "fixture_not_matched")
    fixture = decision.fixture

    if not fixture.is_finished:
        return LegOutcome(leg_id, Outcome.PENDING, rule_id, "result_not_yet_available")
    if fixture.regulation_score is None:
        return LegOutcome(leg_id, Outcome.UNRESOLVED, rule_id, "missing_period")

    side, side_reason = _resolve_side(market, provider, rule_id, fixture, aliases)
    if side_reason is not None:
        return LegOutcome(leg_id, Outcome.UNRESOLVED, rule_id, side_reason)

    outcome_str, reason = _EVALUATOR_CALLS[rule_id](
        classification.evaluator, pick, specifier, fixture.regulation_score, side)
    return LegOutcome(leg_id, Outcome(outcome_str), rule_id, reason)
