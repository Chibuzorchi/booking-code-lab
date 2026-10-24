"""Ticket aggregation (F9_RESULTS_PLAN.md section 6) and percentage
accounting (section 7). Pure functions over already-produced leg outcomes --
no matching, no rule evaluation and no fixture state live here. Section 6:
"Keep these separate: matching, fixture state, required-data completeness,
rule support and selection outcome" applies to this module too, so it never
re-derives a leg's status; it only aggregates statuses it is handed.

Leg and ticket outcomes share one five-value Outcome enum (won/lost/void/
pending/unresolved) because section 7's accounting (N = W+L+V+P+U) is over
ticket-level outcomes drawn from the same five states section 6 defines for
legs.

Evidence retention (frozen extract bytes, raw fixture bytes, matching
candidates) is a report-revision concern assigned to jobs.py/cache.py
(section 8), not duplicated in LegOutcome here -- this module only carries
enough identity (leg_id, rule_id, reason) to point back at that evidence,
which a caller already has from matching.py/rules/ before building one.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class Outcome(Enum):
    WON = "won"
    LOST = "lost"
    VOID = "void"
    PENDING = "pending"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class LegOutcome:
    leg_id: str
    status: Outcome
    rule_id: str | None
    reason: str | None


@dataclass(frozen=True)
class TicketOutcome:
    status: Outcome
    reason: str | None
    proxy_all_legs_won: bool
    legs: tuple[LegOutcome, ...]


def aggregate_ticket(legs: Sequence[LegOutcome], *, structure_verified: bool) -> TicketOutcome:
    """structure_verified must come from provider_types.verify_bet_type (or
    an equivalent check) actually confirming this ticket is an ordinary
    accumulator -- never assumed true by default. Section 6: "Unknown types
    must not become assumed accumulators." An unverified structure gets
    UNRESOLVED regardless of leg statuses, even if every leg happens to show
    WON -- that all-wins fact is reported separately as proxy_all_legs_won,
    which is descriptive only and never promoted to a confirmed ticket win.

    Precedence for a verified ordinary accumulator (section 6):
    1. Any LOST leg makes the ticket LOST, even alongside PENDING/UNRESOLVED
       legs -- the LOST status handed in is trusted to already reflect
       applicable promotion/void rules; this is not a shortcut around them.
    2. Otherwise any UNRESOLVED leg makes the ticket UNRESOLVED; otherwise
       any PENDING leg makes the ticket PENDING.
    3. All legs VOID makes the ticket VOID; at least one WON with every
       other leg WON or VOID makes the ticket WON.
    """
    legs = tuple(legs)
    proxy_all_legs_won = bool(legs) and all(leg.status == Outcome.WON for leg in legs)
    if not legs:
        return TicketOutcome(Outcome.UNRESOLVED, "no_legs", proxy_all_legs_won, legs)
    if not structure_verified:
        return TicketOutcome(Outcome.UNRESOLVED, "unverified_ticket_structure", proxy_all_legs_won, legs)

    statuses = {leg.status for leg in legs}
    if Outcome.LOST in statuses:
        return TicketOutcome(Outcome.LOST, None, proxy_all_legs_won, legs)
    if Outcome.UNRESOLVED in statuses:
        return TicketOutcome(Outcome.UNRESOLVED, "unresolved_leg_present", proxy_all_legs_won, legs)
    if Outcome.PENDING in statuses:
        return TicketOutcome(Outcome.PENDING, "pending_leg_present", proxy_all_legs_won, legs)
    if statuses == {Outcome.VOID}:
        return TicketOutcome(Outcome.VOID, None, proxy_all_legs_won, legs)
    # Only WON and VOID remain, and not all VOID, so at least one WON.
    return TicketOutcome(Outcome.WON, None, proxy_all_legs_won, legs)


@dataclass(frozen=True)
class TicketCounts:
    """total is always won+lost+void+pending+unresolved by construction --
    section 7's N = W+L+V+P+U is structural here, not a separately-checked
    invariant that could silently drift out of sync."""
    won: int
    lost: int
    void: int
    pending: int
    unresolved: int

    @property
    def total(self) -> int:
        return self.won + self.lost + self.void + self.pending + self.unresolved


def counts_from_ticket_outcomes(outcomes: Sequence[TicketOutcome]) -> TicketCounts:
    tally = {Outcome.WON: 0, Outcome.LOST: 0, Outcome.VOID: 0,
             Outcome.PENDING: 0, Outcome.UNRESOLVED: 0}
    for outcome in outcomes:
        tally[outcome.status] += 1
    return TicketCounts(won=tally[Outcome.WON], lost=tally[Outcome.LOST], void=tally[Outcome.VOID],
                         pending=tally[Outcome.PENDING], unresolved=tally[Outcome.UNRESOLVED])


@dataclass(frozen=True)
class Rate:
    """A numerator/denominator percentage. value is None, with a reason,
    when the denominator is zero -- section 7: "zero denominators produce
    null with a reason, not a misleading zero rate.\""""
    numerator: int
    denominator: int
    value: float | None
    reason: str | None


def _rate(numerator: int, denominator: int, zero_denominator_reason: str) -> Rate:
    if denominator == 0:
        return Rate(numerator, denominator, None, zero_denominator_reason)
    return Rate(numerator, denominator, numerator / denominator, None)


@dataclass(frozen=True)
class PercentageReport:
    counts: TicketCounts
    confirmed_win_rate: Rate            # W/N
    decided_win_loss_rate: Rate         # W/(W+L)
    classification_coverage: Rate       # (W+L+V)/N
    eventual_binary_win_bound_low: Rate  # W/N
    eventual_binary_win_bound_high: Rate  # (W+P+U)/N


def compute_percentages(counts: TicketCounts) -> PercentageReport:
    """Section 7's named metrics. confirmed_win_rate and
    eventual_binary_win_bound_low are the same W/N ratio under two names
    because the plan gives them distinct meanings (a lower bound while
    pending/unresolved remain is still "confirmed wins across retained
    codes" today) -- this module always returns both rather than making a
    caller re-derive one from the other."""
    n = counts.total
    w, l, v, p, u = counts.won, counts.lost, counts.void, counts.pending, counts.unresolved
    no_tickets = "zero_denominator_no_retained_tickets"
    return PercentageReport(
        counts=counts,
        confirmed_win_rate=_rate(w, n, no_tickets),
        decided_win_loss_rate=_rate(w, w + l, "zero_denominator_no_decided_tickets"),
        classification_coverage=_rate(w + l + v, n, no_tickets),
        eventual_binary_win_bound_low=_rate(w, n, no_tickets),
        eventual_binary_win_bound_high=_rate(w + p + u, n, no_tickets),
    )
