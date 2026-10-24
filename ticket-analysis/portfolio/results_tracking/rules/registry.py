"""Versioned classify() dispatch over observed real market combinations.

classify() gates on sport first (the Handball-under-"Bundesliga" collision
found in the real SportyBet extract proves a league-string match alone is
not a safe scope filter), then matches the exact (market, specifier-shape)
combinations hand-inventoried from the real bet9ja/sportybet in-scope data.
Every combination not registered here as supported is returned unsupported
with one of F9_RESULTS_PLAN.md section 6's named reasons -- never guessed.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Callable

from . import football

_FOOTBALL_SPORTS = {"Football", "Soccer"}

# Real team display names observed in the real SportyBet
# run-sportybet-20261023-070118-f6753695 extract, for the 8 in-scope leagues,
# in the "<Team> Over/Under" market family. Hand-extracted, not guessed --
# see F9_IMPLEMENTATION_REVIEW.md R01. This is NOT a fixture-verified team
# match (it doesn't confirm the named team is actually playing in a given
# ticket's fixture); it only rejects fabricated/unverified modifiers
# ("Cards Over/Under", "Unknown Promotion Over/Under") that are not real
# team names at all. Full fixture-context verification is step 5's job.
_VERIFIED_TEAM_NAMES = frozenset({
    "Alaves", "Arsenal", "Aston Villa", "Athletic Bilbao", "Atletico Madrid",
    "Augsburg", "Barcelona", "Bayer Leverkusen", "Bayern Munich", "Benfica",
    "Bologna", "Borussia Dortmund", "Bournemouth", "Brentford", "Brest",
    "Brighton", "Celta", "Chelsea", "Como 1907", "Crystal Palace",
    "ESTAC Troyes", "Eintracht Frankfurt", "Elche CF", "Fortuna Sittard",
    "Frosinone", "Fulham", "Hull City", "Inter", "Ipswich Town", "Juventus",
    "Lazio", "Le Mans FC", "Lens", "Lille", "Liverpool", "Lorient",
    "Man City", "Man Utd", "Marseille", "Milan", "Monza", "Napoli",
    "Nottingham", "PSG", "Paris FC", "RB Leipzig", "Racing Santander",
    "Real Madrid", "Real Sociedad", "Rennes", "SV 07 Elversberg", "Sassuolo",
    "Schalke", "Sevilla", "Sparta Rotterdam", "Sporting", "Strasbourg",
    "Stuttgart", "Sunderland AFC", "Torino", "Villarreal", "Werder Bremen",
})

# rule_id -> version, bumped whenever a function in football.py changes
# observable behavior for an already-registered combination.
_RULE_VERSIONS = {
    "regulation_1x2": 1,
    "double_chance": 1,
    "goal_goal_no_goal": 2,  # v2: added Bet9ja's real GG/NG pick vocabulary and period
    "over_under": 2,  # v2: decimal parsing, bare-numeric specifier, unsupported_line reason
    "odd_even": 1,
    "correct_score": 1,
    "win_either_half": 1,
    "team_total_over_under": 2,  # v2: decimal parsing, unsupported_line reason
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


def _supported(rule_id: str, evaluator: Callable, **bound_kwargs) -> Classification:
    """bound_kwargs (e.g. period="HT") are pre-bound into the evaluator via
    functools.partial, so every classify() caller can invoke the returned
    evaluator with the same (pick, specifier, score, ...) shape without
    needing to know which market string selected which period."""
    if bound_kwargs:
        evaluator = functools.partial(evaluator, **bound_kwargs)
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
    if market in ("GG/NG", "Both Teams to Score", "Goal/No Goal"):
        return _supported("goal_goal_no_goal", football.goal_goal_no_goal)
    if market == "1st Half - Goal/No Goal":
        return _supported("goal_goal_no_goal", football.goal_goal_no_goal, period="HT")
    if market == "2nd Half - Goal/No Goal":
        return _supported("goal_goal_no_goal", football.goal_goal_no_goal, period="2H")
    if market == "Over/Under":
        return _supported("over_under", football.over_under)
    if market == "Odd/Even":
        return _supported("odd_even", football.odd_even)
    if market == "Correct Score":
        return _supported("correct_score", football.correct_score)
    if market == "1st Half - Over/Under":
        return _supported("over_under", football.over_under, period="HT")
    if market == "2nd Half - Over/Under":
        return _supported("over_under", football.over_under, period="2H")
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

    if market in ("Home Over/Under", "Away Over/Under"):
        return _supported("team_total_over_under", football.team_total_over_under)
    if market.endswith(" Over/Under"):
        team_name = market[: -len(" Over/Under")]
        if team_name in _VERIFIED_TEAM_NAMES:
            return _supported("team_total_over_under", football.team_total_over_under)
        return _unsupported("unsupported_market")  # unverified team/modifier name -- R01

    return _unsupported("unsupported_market")
