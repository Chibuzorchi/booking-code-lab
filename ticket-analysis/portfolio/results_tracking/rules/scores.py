"""The regulation-time score fact a rule is allowed to use.

Deliberately narrower than a future fixtures.py fixture model: this is only
the shape openfootball's score object actually supplies (ft, optionally ht),
named explicitly so a rule can never reach past what evidence provides.
"""
from __future__ import annotations

from dataclasses import dataclass


class InvalidScoreError(ValueError):
    """The supplied score facts are internally inconsistent, not just missing."""


@dataclass(frozen=True)
class RegulationScore:
    """Final (ft) and, optionally, half-time (ht) goals for both sides.

    ht is None when unknown; a rule that requires a half period must check for
    that explicitly rather than treating None as 0. home/away are the fixture's
    resolved sides -- resolving which named team is "home" is a matching
    concern, not this module's.
    """
    home: int
    away: int
    ht_home: int | None = None
    ht_away: int | None = None

    def __post_init__(self):
        for name, value in (("home", self.home), ("away", self.away)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InvalidScoreError(f"{name} must be a non-negative int, got {value!r}")
        if (self.ht_home is None) != (self.ht_away is None):
            raise InvalidScoreError("ht_home and ht_away must both be known or both be unknown")
        if self.ht_home is not None:
            for name, value in (("ht_home", self.ht_home), ("ht_away", self.ht_away)):
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise InvalidScoreError(f"{name} must be a non-negative int, got {value!r}")
            if self.ht_home > self.home or self.ht_away > self.away:
                raise InvalidScoreError(
                    "half-time goals cannot exceed full-time goals for the same side")

    @property
    def has_half_time(self) -> bool:
        return self.ht_home is not None

    @property
    def second_half(self) -> tuple[int, int]:
        if not self.has_half_time:
            raise InvalidScoreError("second_half requested without half-time facts")
        return self.home - self.ht_home, self.away - self.ht_away
