"""Fixture matching: one provider's selection-level event info -> a known
Fixture, by F9_RESULTS_PLAN.md section 5's three-tier order:

1. A previously verified provider-event to source-fixture mapping, re-checked
   against the *current* inputs every time -- it is not a blank cheque for
   every future observation sharing the same provider_event_id.
2. Exact canonical competition + ordered home/away aliases + kickoff evidence,
   with exactly one plausible fixture. Multiple equally-plausible fixtures
   are `ambiguous_fixture`, never "first candidate wins".
3. Fuzzy candidate proposals, for human review only. Fuzzy name similarity
   alone never promotes a result to `matched` in this module.

Deliberately out of scope here (left to a future reporting.py): preserving
observed_at so a known post-kickoff observation doesn't enter a pre-match
cohort (section 5) -- that is a reporting-time cohort decision, not a
matching decision, and this module does not even receive observed_at.

Also deliberately not modeled: a `sport` field on Fixture or a sport check
in match_selection_event. Every adapter in this package (api_football.py,
football_data_import.py) is football-only, so a Fixture here is always a
football fixture by construction; sport gating for a ticket leg already
happens in rules/registry.classify(), which is where a genuinely
non-football leg is rejected.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Sequence

from .fixtures import Fixture, KickoffTime

DEFAULT_EXACT_TIME_TOLERANCE = timedelta(minutes=15)
_FUZZY_CANDIDATE_THRESHOLD = 0.6
_MAX_FUZZY_CANDIDATES = 5


class MatchStatus(Enum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous_fixture"
    UNMATCHED = "unmatched"


class MatchingError(ValueError):
    """A structural input problem (e.g. an unparseable event display
    string), as distinct from a normal unmatched/ambiguous match result."""


@dataclass(frozen=True)
class Alias:
    """One accepted equivalence between a source/provider's own display
    string and a canonical identity, for either a team or a competition.
    `namespace` scopes what's being aliased and whose vocabulary it's in
    (e.g. "bet9ja_team", "api_football_competition"); namespaces never mix,
    so a provider's "Arsenal Women" alias can never collide with, or be
    confused for, its "Arsenal" alias -- distinct club/women/youth/reserve/
    national identities are registered as distinct canonical names, never
    merged by this module."""
    namespace: str
    display_name: str
    canonical: str


@dataclass(frozen=True)
class VerifiedMapping:
    """A previously verified provider-event to source-fixture mapping
    (tier 1). Records the exact display strings seen at verification time,
    so a later call can re-check them against the *current* inputs rather
    than trusting the provider_event_id forever -- a changed league/team
    display for the same ID fails the recheck instead of silently matching."""
    provider: str
    provider_event_id: str
    league_display: str
    home_display: str
    away_display: str
    source: str
    source_fixture_id: str
    verified_at: str
    evidence: str


@dataclass(frozen=True)
class MatchCandidate:
    fixture: Fixture
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class MatchDecision:
    status: MatchStatus
    fixture: Fixture | None
    candidates: tuple[MatchCandidate, ...]
    tier: str | None
    reason: str | None


def resolve_alias(aliases: Sequence[Alias], namespace: str, display_name: str) -> str | None:
    """None when no alias is registered -- never guessed, never fuzzy here."""
    for alias in aliases:
        if alias.namespace == namespace and alias.display_name == display_name:
            return alias.canonical
    return None


def split_event_display(event_display: str) -> tuple[str, str]:
    """Bet9ja and SportyBet both show one event as "Home - Away" with that
    exact " - " separator (verified against real extracts). Zero or more
    than one occurrence is ambiguous -- raises rather than guessing which
    side is which team."""
    parts = event_display.split(" - ")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise MatchingError(f"cannot split event display into home/away: {event_display!r}")
    return parts[0], parts[1]


def _kickoff_consistent(selection_kickoff: KickoffTime, fixture_kickoff: KickoffTime,
                         tolerance: timedelta) -> bool:
    """Exact-vs-exact uses the configured tolerance (default 15 minutes).
    Anything else -- either side date-only, or either side's zone
    unverified -- falls back to comparing calendar dates only; this never
    treats a date-only source as midnight UTC and never searches outside
    the one date actually present in each kickoff's raw/utc value."""
    if (selection_kickoff.precision == "exact" and selection_kickoff.utc is not None
            and fixture_kickoff.precision == "exact" and fixture_kickoff.utc is not None):
        return abs(selection_kickoff.utc - fixture_kickoff.utc) <= tolerance
    selection_date = (selection_kickoff.utc.date().isoformat() if selection_kickoff.utc is not None
                      else selection_kickoff.raw[:10])
    fixture_date = (fixture_kickoff.utc.date().isoformat() if fixture_kickoff.utc is not None
                    else fixture_kickoff.raw[:10])
    return selection_date == fixture_date


def _consistent_with_fixture(fixture: Fixture, canonical_competition: str | None,
                              canonical_home: str | None, canonical_away: str | None,
                              aliases: Sequence[Alias]) -> bool:
    """Both the selection's and the fixture's own display strings must
    resolve, via registered aliases, to the identical canonical competition
    and the identical ordered (home, away) teams. Orientation is never
    swapped implicitly -- a source that disagrees on which side is home
    needs its own verified mapping (tier 1), not a silent reversal here."""
    if canonical_competition is None or canonical_home is None or canonical_away is None:
        return False
    fixture_competition = resolve_alias(
        aliases, f"{fixture.provenance.source}_competition", fixture.competition)
    fixture_home = resolve_alias(aliases, f"{fixture.provenance.source}_team", fixture.home_team)
    fixture_away = resolve_alias(aliases, f"{fixture.provenance.source}_team", fixture.away_team)
    return (fixture_competition == canonical_competition
            and fixture_home == canonical_home
            and fixture_away == canonical_away)


def _fuzzy_candidates(home_display: str, away_display: str,
                       candidate_fixtures: Sequence[Fixture]) -> tuple[MatchCandidate, ...]:
    scored = []
    for fixture in candidate_fixtures:
        home_ratio = difflib.SequenceMatcher(
            None, home_display.lower(), fixture.home_team.lower()).ratio()
        away_ratio = difflib.SequenceMatcher(
            None, away_display.lower(), fixture.away_team.lower()).ratio()
        score = (home_ratio + away_ratio) / 2
        if score >= _FUZZY_CANDIDATE_THRESHOLD:
            scored.append((score, fixture))
    scored.sort(key=lambda pair: -pair[0])
    return tuple(MatchCandidate(fixture, (f"fuzzy_name_similarity={score:.2f}",))
                 for score, fixture in scored[:_MAX_FUZZY_CANDIDATES])


def match_selection_event(
    *, provider: str, provider_event_id: str | None, event_display: str, league_display: str,
    kickoff: KickoffTime, candidate_fixtures: Sequence[Fixture], aliases: Sequence[Alias] = (),
    verified_mappings: Sequence[VerifiedMapping] = (),
    tolerance: timedelta = DEFAULT_EXACT_TIME_TOLERANCE,
) -> MatchDecision:
    """Resolve one provider selection's event to a Fixture, or explain why
    it can't be resolved yet. Never returns MATCHED except through tier 1's
    recheck or tier 2's unique-exact-match path."""
    home_display, away_display = split_event_display(event_display)

    if provider_event_id:
        for mapping in verified_mappings:
            if mapping.provider != provider or mapping.provider_event_id != provider_event_id:
                continue
            if (mapping.league_display != league_display or mapping.home_display != home_display
                    or mapping.away_display != away_display):
                return MatchDecision(MatchStatus.UNMATCHED, None, (), "verified_mapping",
                                      "verified_mapping_failed_consistency_recheck")
            fixture = next(
                (f for f in candidate_fixtures
                 if f.provenance.source == mapping.source
                 and f.provenance.source_fixture_id == mapping.source_fixture_id),
                None)
            if fixture is None:
                return MatchDecision(MatchStatus.UNMATCHED, None, (), "verified_mapping",
                                      "verified_mapping_fixture_not_supplied")
            return MatchDecision(
                MatchStatus.MATCHED, fixture,
                (MatchCandidate(fixture, ("verified_mapping",)),), "verified_mapping", None)

    canonical_competition = resolve_alias(aliases, f"{provider}_competition", league_display)
    canonical_home = resolve_alias(aliases, f"{provider}_team", home_display)
    canonical_away = resolve_alias(aliases, f"{provider}_team", away_display)

    exact_matches = [
        fixture for fixture in candidate_fixtures
        if _consistent_with_fixture(fixture, canonical_competition, canonical_home,
                                     canonical_away, aliases)
        and _kickoff_consistent(kickoff, fixture.kickoff, tolerance)
    ]
    if len(exact_matches) == 1:
        fixture = exact_matches[0]
        return MatchDecision(
            MatchStatus.MATCHED, fixture,
            (MatchCandidate(fixture, ("exact_competition", "exact_teams", "kickoff_consistent")),),
            "exact_unique", None)
    if len(exact_matches) > 1:
        candidates = tuple(
            MatchCandidate(f, ("exact_competition", "exact_teams", "kickoff_consistent"))
            for f in exact_matches)
        return MatchDecision(MatchStatus.AMBIGUOUS, None, candidates, "exact_unique",
                              "multiple_plausible_fixtures")

    fuzzy = _fuzzy_candidates(home_display, away_display, candidate_fixtures)
    if fuzzy:
        return MatchDecision(MatchStatus.UNMATCHED, None, fuzzy, "fuzzy_review",
                              "no_verified_or_exact_match_fuzzy_candidates_for_review")
    return MatchDecision(MatchStatus.UNMATCHED, None, (), None, "no_candidate_fixtures")
