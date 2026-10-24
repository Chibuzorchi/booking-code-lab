"""Shared fixture/period/state/provenance models.

F9_RESULTS_PLAN.md section 8 lists this as the one shape every source
adapter and the matcher (matching.py, not yet built) normalize into, instead
of each adapter inventing its own. Three things the plan is explicit about
are enforced here rather than left to callers:

- Section 5: a provider's own native event ID is provider-scoped and must
  never be assumed to be a source's fixture ID; FixtureProvenance always
  names which source and which of that source's own IDs this came from, and
  KickoffTime never invents a UTC interpretation without recorded evidence
  for it (`timezone_evidence`) -- "Existing `_kickoff_dt()` assumes UTC;
  that is not evidence" applies to every future caller, not just that one.
- Section 6: "Kickoff passing or a score being present is not finality...
  AET/PEN cannot overwrite 90-minute facts." `Fixture.regulation_score` is
  only ever the regulation-time score; a FixtureState of FINISHED_EXTRA_TIME
  or FINISHED_PENALTIES does not require (or imply) that it is known. There
  is deliberately no extra-time/penalty score field yet -- no rule in
  rules/football.py consumes one, and section 6 lists AET/PEN rules as still
  needing their own evidence review, so adding unused fields now would be
  structure nothing reads.
- A fixture cannot claim a final score before a source has actually reported
  regulation as concluded: `regulation_score` is only accepted when `state`
  is one of the FINISHED_* values. For those states, `regulation_score` can
  still legitimately be None -- api_football.py's extract_regulation_score()
  already declines to report AET/PEN fulltime as unverified; this module
  must not relax that by inferring a score from the state instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .rules.scores import RegulationScore


class FixtureState(Enum):
    SCHEDULED = "scheduled"
    IN_PLAY = "in_play"
    FINISHED_REGULATION = "finished_regulation"
    FINISHED_EXTRA_TIME = "finished_extra_time"
    FINISHED_PENALTIES = "finished_penalties"
    POSTPONED = "postponed"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"
    AWARDED = "awarded"
    UNKNOWN = "unknown"


FINISHED_STATES = frozenset({
    FixtureState.FINISHED_REGULATION,
    FixtureState.FINISHED_EXTRA_TIME,
    FixtureState.FINISHED_PENALTIES,
})

_KICKOFF_PRECISIONS = ("exact", "date_only")


class InvalidFixtureError(ValueError):
    """Supplied fixture facts are internally inconsistent, not just missing."""


@dataclass(frozen=True)
class KickoffTime:
    """The original provider/source time string, and -- only when actually
    verified -- its UTC interpretation and the evidence for that
    interpretation. `raw` is preserved even when `utc` is None, since an
    unknown timezone still allows independently verified date/identity
    matching (section 5); it is never silently defaulted to midnight UTC."""
    raw: str
    precision: str  # "exact" | "date_only"
    utc: datetime | None = None
    timezone_evidence: str | None = None

    def __post_init__(self):
        if not self.raw:
            raise InvalidFixtureError("raw kickoff string must be nonempty")
        if self.precision not in _KICKOFF_PRECISIONS:
            raise InvalidFixtureError(
                f"precision must be one of {_KICKOFF_PRECISIONS!r}, got {self.precision!r}")
        if self.utc is not None:
            if self.utc.tzinfo is None:
                raise InvalidFixtureError("utc must be a timezone-aware datetime")
            if not self.timezone_evidence:
                raise InvalidFixtureError(
                    "a utc interpretation requires nonempty timezone_evidence; "
                    "never invent an offset")


@dataclass(frozen=True)
class FixtureProvenance:
    """Where this fixture observation came from, scoped to that one source.
    source_fixture_id is that source's own identifier -- never a different
    provider's native_event_id reused as if it were the same ID space."""
    source: str
    source_fixture_id: str
    fetched_at: str
    content_sha256: str

    def __post_init__(self):
        for name in ("source", "source_fixture_id", "fetched_at", "content_sha256"):
            if not getattr(self, name):
                raise InvalidFixtureError(f"FixtureProvenance.{name} must be nonempty")


@dataclass(frozen=True)
class Fixture:
    """One source's normalized observation of one match. home_team/away_team
    retain the source's own orientation; reversing them is a matching
    concern requiring a verified mapping, never guessed here."""
    provenance: FixtureProvenance
    competition: str
    season: str | None
    home_team: str
    away_team: str
    kickoff: KickoffTime
    state: FixtureState
    regulation_score: RegulationScore | None = None

    def __post_init__(self):
        if not self.competition:
            raise InvalidFixtureError("competition must be nonempty")
        if not self.home_team or not self.away_team:
            raise InvalidFixtureError("home_team and away_team must be nonempty")
        if self.regulation_score is not None and self.state not in FINISHED_STATES:
            raise InvalidFixtureError(
                f"state is {self.state.value!r} but a regulation_score was supplied; "
                "a score cannot be final before the source reports regulation concluded")

    @property
    def is_finished(self) -> bool:
        return self.state in FINISHED_STATES
