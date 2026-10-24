"""Pure regulation-time football rule functions.

Each function takes a parsed pick/specifier and a RegulationScore and returns
(outcome, reason): outcome is one of "won", "lost", "void", "unresolved";
reason is None on a clean decision or a short string (from
F9_RESULTS_PLAN.md section 6's named reasons) otherwise. No function fetches
anything, resolves a fixture, or guesses a missing fact.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .scores import InvalidScoreError, RegulationScore

_OU_PICK_RE = re.compile(r"^(Over|Under)(?:\s+([0-9]+(?:\.[0-9]+)?))?$")
# Accepts both providers' line representations: SportyBet's "total=2.5" and
# Bet9ja's bare "2.5". A nonempty specifier that matches neither shape is
# rejected outright (returns None), never silently ignored.
_LINE_RE = re.compile(r"^(?:total=)?([0-9]+(?:\.[0-9]+)?)$")
_SCORE_PICK_RE = re.compile(r"^([0-9]+):([0-9]+)$")
_HALF_STEP = Decimal("0.5")


def _is_supported_line(line: Decimal) -> bool:
    """Only whole/half-goal lines (0, 0.5, 1, 1.5, ...) have a settlement
    model in this project. Quarter/split-return lines (e.g. 2.25) are left
    unresolved per F9_RESULTS_PLAN.md until that model exists."""
    return (line / _HALF_STEP) % 1 == 0


def parse_over_under(pick: str, specifier: str) -> tuple[str, Decimal] | None:
    """("Over"/"Under", line) from either provider's representation.

    Bet9ja: pick is a bare "Over"/"Under"; the line is only in the specifier
    (bare "2.5"). SportyBet: the line is embedded in the pick ("Over 2.5")
    and also, redundantly, in the specifier ("total=2.5"). When both carry a
    line they must agree exactly (decimal, not float, comparison) -- a
    mismatch is a data-integrity problem, not a 50/50 guess, so returns None
    (caller reports "invalid_input"). A nonempty specifier that cannot be
    parsed as either known shape is also rejected outright, rather than
    silently falling back to the pick's own line.
    """
    pick_match = _OU_PICK_RE.match(pick or "")
    if not pick_match:
        return None
    side, pick_line_raw = pick_match.group(1), pick_match.group(2)
    pick_line = Decimal(pick_line_raw) if pick_line_raw else None

    spec_line = None
    if specifier:
        spec_match = _LINE_RE.match(specifier)
        if not spec_match:
            return None
        spec_line = Decimal(spec_match.group(1))

    if pick_line is not None and spec_line is not None and pick_line != spec_line:
        return None
    line = pick_line if pick_line is not None else spec_line
    if line is None:
        return None
    return side, line


def over_under(pick: str, specifier: str, score: RegulationScore, period: str = "FT") -> tuple[str, str | None]:
    parsed = parse_over_under(pick, specifier)
    if parsed is None:
        return "unresolved", "invalid_input"
    side, line = parsed
    if not _is_supported_line(line):
        return "unresolved", "unsupported_line"
    if period == "FT":
        total = score.home + score.away
    elif period == "HT":
        if not score.has_half_time:
            return "unresolved", "missing_period"
        total = score.ht_home + score.ht_away
    elif period == "2H":
        if not score.has_half_time:
            return "unresolved", "missing_period"
        sh_home, sh_away = score.second_half
        total = sh_home + sh_away
    else:
        return "unresolved", "invalid_input"
    if total == line:
        return "void", None  # push: only reachable for a whole-number line
    over = total > line
    return ("won", None) if (side == "Over") == over else ("lost", None)


def regulation_1x2(pick: str, score: RegulationScore) -> tuple[str, str | None]:
    outcome = "1" if score.home > score.away else ("2" if score.away > score.home else "X")
    bet9ja = {"1", "X", "2"}
    sportybet = {"Home": "1", "Draw": "X", "Away": "2"}
    if pick in bet9ja:
        picked = pick
    elif pick in sportybet:
        picked = sportybet[pick]
    else:
        return "unresolved", "invalid_input"
    return ("won", None) if picked == outcome else ("lost", None)


def double_chance(pick: str, score: RegulationScore) -> tuple[str, str | None]:
    outcome = "1" if score.home > score.away else ("2" if score.away > score.home else "X")
    bet9ja = {"1X": {"1", "X"}, "X2": {"X", "2"}, "12": {"1", "2"}}
    sportybet = {"Home or Draw": {"1", "X"}, "Draw or Away": {"X", "2"}, "Home or Away": {"1", "2"}}
    covered = bet9ja.get(pick) or sportybet.get(pick)
    if covered is None:
        return "unresolved", "invalid_input"
    return ("won", None) if outcome in covered else ("lost", None)


def goal_goal_no_goal(pick: str, score: RegulationScore, period: str = "FT") -> tuple[str, str | None]:
    """pick vocabulary covers both providers observed in real data:
    SportyBet's "Yes"/"No" and Bet9ja's "GG"/"NG"."""
    if period == "FT":
        home_goals, away_goals = score.home, score.away
    elif period == "HT":
        if not score.has_half_time:
            return "unresolved", "missing_period"
        home_goals, away_goals = score.ht_home, score.ht_away
    elif period == "2H":
        if not score.has_half_time:
            return "unresolved", "missing_period"
        home_goals, away_goals = score.second_half
    else:
        return "unresolved", "invalid_input"
    both_scored = home_goals > 0 and away_goals > 0
    if pick in ("Yes", "GG"):
        return ("won", None) if both_scored else ("lost", None)
    if pick in ("No", "NG"):
        return ("lost", None) if both_scored else ("won", None)
    return "unresolved", "invalid_input"


def odd_even(pick: str, score: RegulationScore) -> tuple[str, str | None]:
    total = score.home + score.away
    is_even = total % 2 == 0
    if pick == "Even":
        return ("won", None) if is_even else ("lost", None)
    if pick == "Odd":
        return ("lost", None) if is_even else ("won", None)
    return "unresolved", "invalid_input"


def correct_score(pick: str, score: RegulationScore) -> tuple[str, str | None]:
    match = _SCORE_PICK_RE.match(pick or "")
    if not match:
        return "unresolved", "invalid_input"
    picked = (int(match.group(1)), int(match.group(2)))
    return ("won", None) if picked == (score.home, score.away) else ("lost", None)


def win_either_half(pick: str, score: RegulationScore, side: str) -> tuple[str, str | None]:
    """side is "home" or "away", already resolved by the caller (a matching
    concern, not this rule's); pick is "Yes"/"No"."""
    if not score.has_half_time:
        return "unresolved", "missing_period"
    if side not in ("home", "away"):
        return "unresolved", "invalid_input"
    sh_home, sh_away = score.second_half
    if side == "home":
        won_a_half = score.ht_home > score.ht_away or sh_home > sh_away
    else:
        won_a_half = score.ht_away > score.ht_home or sh_away > sh_home
    if pick == "Yes":
        return ("won", None) if won_a_half else ("lost", None)
    if pick == "No":
        return ("lost", None) if won_a_half else ("won", None)
    return "unresolved", "invalid_input"


def team_total_over_under(pick: str, specifier: str, score: RegulationScore, side: str,
                           period: str = "FT") -> tuple[str, str | None]:
    """side is "home"/"away", already resolved by the caller."""
    if side not in ("home", "away"):
        return "unresolved", "invalid_input"
    parsed = parse_over_under(pick, specifier)
    if parsed is None:
        return "unresolved", "invalid_input"
    pick_side, line = parsed
    if not _is_supported_line(line):
        return "unresolved", "unsupported_line"
    if period == "FT":
        goals = score.home if side == "home" else score.away
    elif period == "HT":
        if not score.has_half_time:
            return "unresolved", "missing_period"
        goals = score.ht_home if side == "home" else score.ht_away
    else:
        return "unresolved", "invalid_input"
    if goals == line:
        return "void", None
    over = goals > line
    return ("won", None) if (pick_side == "Over") == over else ("lost", None)
