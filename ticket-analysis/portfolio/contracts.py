"""Explicit identity and odds semantics; never infer payout rules from products."""
import math


def text(value):
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def identity(selection, provider):
    native = [text(selection.get(k)) for k in ("native_event_id", "market_id", "outcome_id")]
    # Explicit empty specifier is valid for a market without a line. Missing is not.
    if all(native) and native[0] != "0" and isinstance(selection.get("specifier"), str):
        event, market, outcome = native
        specifier = selection["specifier"].strip()
        return ([provider, "native-v1", event, market, specifier, outcome],
                [provider, "native-v1", event], "structured_native", None)
    eid, market, pick = (text(selection.get(k)) for k in ("event_id", "market", "pick"))
    if eid and eid != "0" and market and pick:
        return ([provider, "display-v1", eid, market, pick],
                [provider, "display-v1", eid], "legacy_display", None)
    reason = "zero_event_id_without_complete_native_identity" if eid == "0" else "missing_selection_identity"
    return None, None, "unresolved", reason


def odds_contract(raw, provider):
    selections = raw.get("selections") if isinstance(raw.get("selections"), list) else []
    values = [numeric(s.get("odds")) if isinstance(s, dict) else None for s in selections]
    product = None
    if values and all(v is not None and v > 1 for v in values):
        product = math.prod(values)
        if not math.isfinite(product):
            product = None
    if "site_displayed_odds" in raw:
        site = numeric(raw["site_displayed_odds"])
        source = "explicit_site_displayed_odds"
    elif provider == "sportybet":
        site = numeric(raw.get("total_odds"))
        source = "legacy_sportybet_total_odds_unverified_provenance"
    else:
        site, source = None, "not_captured"
    if site is not None and site <= 1:
        site = None
    declared = text(raw.get("bet_type")).lower() or "unknown"
    structure = {"multiple": "declared_accumulator", "straight_accumulator": "declared_accumulator",
                 "single": "single", "system": "system"}.get(declared, "unknown")
    if any(isinstance(s, dict) and s.get("is_bet_builder") for s in selections):
        structure = "bet_builder"
    # No provider payoff-rule resolver has been validated in this release.
    # Even an input claiming verification must not promote itself to trusted payout odds.
    payout = {"status": "unknown", "multiplier": None,
              "reason": "No audited provider payout-rule resolver; type and odds agreement are insufficient."}
    return {"site_displayed_odds": site, "site_odds_source": source,
            "parsed_leg_product": product, "payout_semantics": payout,
            "site_product_discrepancy": (not math.isclose(site, product, rel_tol=1e-12, abs_tol=.011)
                                         if site is not None and product is not None else None),
            "declared_bet_type": declared, "ticket_structure": structure,
            "selected_systems": raw.get("selected_systems"),
            "odds_context": raw.get("odds_context", {})}


def odds_value(ticket, basis):
    odds = ticket["odds"]
    if basis == "verified_payout":
        return odds["payout_semantics"]["multiplier"] if odds["payout_semantics"]["status"] == "verified" else None
    return odds.get(basis)
