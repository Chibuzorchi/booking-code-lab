"""Records of what provider-specific type/time facts have actually been
verified, as opposed to assumed, for this run.

Both findings here come from direct evidence gathered during step 2/3 of the
F9 plan, not from provider documentation (none was found):

- bet_type / selectedSystems: an Explore pass over bet9ja/, sportybet/ and
  ticket-analysis/ found no code or doc anywhere mapping bet9ja's numeric
  bet_type codes ("0"/"1"/"2") or sportybet's bet_type/selectedSystems
  fields to a named bet structure (single/multiple/system). verify_bet_type
  reflects that: every input is "unverified", never a guessed label.
- naive kickoff semantics: three real fixtures present in both providers'
  committed extracts had a bet9ja naive timestamp numerically identical to
  sportybet's +00:00-offset timestamp (Arsenal-Everton, Liverpool-Arsenal,
  Fleetwood Town-Arsenal; see docs/F9_SOURCE_FEASIBILITY.md). This is
  evidence for those three fixtures as observed in exactly the two runs that
  established it -- run-bet9ja-20261023-065902-28fff68b and
  run-sportybet-20261023-070118-f6753695 -- not a general claim about all
  bet9ja data or even about these same event strings in a different run.

F9_IMPLEMENTATION_REVIEW.md R10: a key of only (provider, event_label,
naive_time) lets an unrelated future observation inherit this verification
merely by coincidentally matching the same display strings, without ever
being checked against the evidence that established it. VERIFIED_NAIVE_UTC_FIXTURES
is therefore keyed on the originating run_id (the bet9ja run actually being
corroborated) and the corroborating_run_id (the sportybet run it was checked
against) as well, and is_verified_naive_utc() requires the caller to supply
both -- a caller who doesn't have them cannot claim this verification.
"""
from __future__ import annotations

_BET9JA_RUN_ID = "run-bet9ja-20261023-065902-28fff68b"
_SPORTYBET_RUN_ID = "run-sportybet-20261023-070118-f6753695"

VERIFIED_NAIVE_UTC_FIXTURES = frozenset({
    ("bet9ja", _BET9JA_RUN_ID, _SPORTYBET_RUN_ID, "Arsenal - Everton", "2026-10-24 14:00:00"),
    ("bet9ja", _BET9JA_RUN_ID, _SPORTYBET_RUN_ID, "Liverpool - Arsenal", "2026-11-01 16:30:00"),
    ("bet9ja", _BET9JA_RUN_ID, _SPORTYBET_RUN_ID, "Fleetwood Town - Arsenal", "2026-10-27 20:00:00"),
})


def verify_bet_type(provider: str, raw_value: str) -> dict:
    """No documented mapping exists for either provider's bet-structure
    field (checked by direct codebase search, not assumed). Always
    unverified; callers must not guess single/multiple/system from this."""
    return {
        "provider": provider,
        "raw_value": raw_value,
        "status": "unverified",
        "reason": "no_documented_provider_mapping",
    }


def is_verified_naive_utc(provider: str, run_id: str, corroborating_run_id: str,
                           event: str, kickoff: str) -> bool:
    """True only for the exact (provider, run_id, corroborating_run_id, event,
    kickoff) tuples hand-verified by cross-provider matching between those
    two specific runs. False for everything else, including the same
    provider/event/kickoff strings observed in a different run -- the
    verification does not travel with the display strings alone, only with
    the archived evidence (these two runs) that established it."""
    return (provider, run_id, corroborating_run_id, event, kickoff) in VERIFIED_NAIVE_UTC_FIXTURES
