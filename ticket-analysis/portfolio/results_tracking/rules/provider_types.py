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
  evidence for those three fixtures in this run, not a general claim about
  all bet9ja data -- VERIFIED_NAIVE_UTC_FIXTURES below is exactly that list,
  nothing is inferred beyond it.
"""
from __future__ import annotations

VERIFIED_NAIVE_UTC_FIXTURES = frozenset({
    ("bet9ja", "Arsenal - Everton", "2026-10-24 14:00:00"),
    ("bet9ja", "Liverpool - Arsenal", "2026-11-01 16:30:00"),
    ("bet9ja", "Fleetwood Town - Arsenal", "2026-10-27 20:00:00"),
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


def is_verified_naive_utc(provider: str, event: str, kickoff: str) -> bool:
    """True only for the exact (provider, event, kickoff) triples hand-
    verified by cross-provider matching in this run. False for everything
    else, including other fixtures of the same provider/competition --
    naive-equals-UTC is not assumed to generalize."""
    return (provider, event, kickoff) in VERIFIED_NAIVE_UTC_FIXTURES
