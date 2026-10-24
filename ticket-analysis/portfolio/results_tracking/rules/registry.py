"""Versioned classify() dispatch over observed real market combinations.

classify() gates on sport first (the Handball-under-"Bundesliga" collision
found in the real SportyBet extract proves a league-string match alone is
not a safe scope filter), then matches the exact (market, specifier-shape)
combinations hand-inventoried from the real bet9ja/sportybet in-scope data.
Every combination not registered here as supported is returned unsupported
with one of F9_RESULTS_PLAN.md section 6's named reasons -- never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import football

_FOOTBALL_SPORTS = {"Football", "Soccer"}

# rule_id -> version, bumped whenever a function in football.py changes
# observable behavior for an already-registered combination.
_RULE_VERSIONS = {
    "regulation_1x2": 1,
    "double_chance": 1,
    "goal_goal_no_goal": 1,
    "over_under": 1,
    "odd_even": 1,
    "correct_score": 1,
    "win_either_half": 1,
    "team_total_over_under": 1,
}


@dataclass(frozen=True)
class Classification:
    status: str  # "supported" or "unsupported"
    rule_id: str | None = None
    version: int | None = None
    evaluator: Callable | None = None
    reason: str | None = None


def _unsupported(reason: str) -> Classification:
    return Classification(status="unsupported", reason=reason)


def _supported(rule_id: str, evaluator: Callable) -> Classification:
    return Classification(status="supported", rule_id=rule_id,
                           version=_RULE_VERSIONS[rule_id], evaluator=evaluator)


def classify(market: str, sport: str) -> Classification:
    """Classify a real observed (market, sport) pair.

    Returns a Classification whose evaluator, when present, is a callable
    taking (pick, specifier, score, **kwargs appropriate to the family) and
    returning (outcome, reason) per football.py's contract. Side-resolution
    (which team is home/away, or which named team a per-team market refers
    to) is always the caller's job, done before calling the evaluator --
    this function only decides whether and how a market is evaluable at all.
    """
    if sport not in _FOOTBALL_SPORTS:
        return _unsupported("unsupported_sport")

    if market == "1X2":
        return _supported("regulation_1x2", football.regulation_1x2)
    if market == "Double Chance":
        return _supported("double_chance", football.double_chance)
    if market in ("GG/NG", "Both Teams to Score"):
        return _supported("goal_goal_no_goal", football.goal_goal_no_goal)
    if market == "Over/Under":
        return _supported("over_under", football.over_under)
    if market == "Odd/Even":
        return _supported("odd_even", football.odd_even)
    if market == "Correct Score":
        return _supported("correct_score", football.correct_score)
    if market in ("1st Half - Over/Under", "2nd Half - Over/Under"):
        return _supported("over_under", football.over_under)
    if market in ("Home Team to Win Either Half", "Away Team to Win Either Half"):
        return _supported("win_either_half", football.win_either_half)
    if market in ("Handicap", "Asian Handicap", "European Handicap"):
        return _unsupported("unsupported_market")
    if market in ("1X2 - 1UP/2UP/Never Down",):
        return _unsupported("incomplete_timeline")
    if "Early Goals" in market or market.startswith("10 minutes") or " from " in market:
        return _unsupported("incomplete_timeline")
    if "Corners" in market or "Offsides" in market or "Statistics" in market:
        return _unsupported("unsupported_market")
    if " & " in market or (" or " in market.lower() and market not in ("Double Chance",)):
        return _unsupported("unsupported_market")  # combined AND/OR markets

    if market in ("Home Over/Under", "Away Over/Under") or market.endswith(" Over/Under"):
        # per-team markets carry the team's display name in the market string
        # (e.g. "Chelsea Over/Under"); resolving that name to home/away is a
        # fixture-matching concern this registry never performs itself.
        return _supported("team_total_over_under", football.team_total_over_under)

    return _unsupported("unsupported_market")
