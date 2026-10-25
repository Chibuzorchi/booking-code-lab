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


def verify_bet_type(provider: str, raw_value: str, *, row_id=None, selected_systems=None,
                    records=(), num_legs=None) -> dict:
    """Default to unverified. A server-reviewed attestation can enable only
    its exact frozen row and original provider fields, with an explicit ordinary
    settlement policy. Never infer single/accumulator/system from a label alone.
    """
    matches = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("ticket structure records must be objects")
        if record.get("row_id") != row_id or record.get("provider") != provider:
            continue
        if not row_id or record.get("raw_value") != raw_value or record.get("selected_systems") != selected_systems:
            continue
        if (record.get("structure") not in ("single", "ordinary_accumulator")
                or not isinstance(record.get("evidence"), str) or not record["evidence"].strip()
                or record.get("settlement_policy") != "ordinary_regulation_no_promotions"):
            continue
        if record["structure"] == "single" and num_legs != 1:
            continue
        matches.append(record)
    if matches:
        if len({r["structure"] for r in matches}) != 1:
            raise ValueError("conflicting ticket structure verification")
        return {"provider": provider, "raw_value": raw_value, "status": "verified",
                "structure": matches[0]["structure"], "evidence": matches[0]["evidence"],
                "reason": "reviewed_frozen_ticket_structure"}
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
