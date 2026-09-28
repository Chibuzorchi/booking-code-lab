"""Offline Stage-3 adapter for one explicitly selected run extract."""
from pathlib import Path

from .analysis import canonical, normalize_ticket, number, read_json
from .contracts import odds_value
from .selection import ParamError, parse_decorrelate_params, select_subset


class ExtractError(ValueError):
    """The selected extract cannot be used as a trustworthy candidate source."""


def harvest_odds_band(extract):
    """Translate a scan extract's recorded odds policy into the Stage-2/3 band.

    The scanner's canonical basis is parsed_leg_product (scanner exports
    ``odds_basis`` since F4). The scanner's "no maximum" sentinel
    (max_total_odds <= 0, or not recorded) maps to max_odds=None here, so the
    harvest and the analysis stages share one band definition. Extracts
    without an explicit parsed_leg_product basis, or with a malformed /
    non-finite maximum, are rejected rather than assumed uncapped.
    """
    if not isinstance(extract, dict):
        raise ExtractError("extract must be an object")
    if extract.get("odds_basis") != "parsed_leg_product":
        raise ExtractError("extract carries no parsed_leg_product odds basis "
                           "(older or foreign scan)")
    lower = number(extract.get("min_total_odds"))
    if lower is None or lower < 1:
        raise ExtractError("extract min_total_odds is missing or invalid")
    raw_max = extract.get("max_total_odds")
    if raw_max in (None, ""):
        upper = None  # not recorded: scanner default (0 = no ceiling)
    else:
        upper = number(raw_max)
        if upper is None:
            raise ExtractError("extract max_total_odds is malformed or non-finite")
        if upper > 0 and upper < lower:
            raise ExtractError("extract max_total_odds is below min_total_odds")
    return {"odds_basis": "parsed_leg_product", "min_odds": lower,
            "max_odds": None if upper is None or upper <= 0 else upper}


def build_pool(extract_path, provider, *, odds_basis, min_odds, max_odds=None, data=None):
    """Return normalized candidates and exclusions; never discover other files.

    The caller supplies the run's provider and odds policy. Missing prices on the
    chosen basis are excluded, without falling back to another odds quantity.
    Conflicting observations of one code fail explicitly, regardless of order.
    Optional parsed data comes from the registry integrity-checked snapshot; when supplied,
    the path is provenance only and is never reopened.
    """
    if provider not in ("bet9ja", "sportybet"):
        raise ParamError("unsupported provider")
    if odds_basis not in ("parsed_leg_product", "site_displayed_odds", "verified_payout"):
        raise ParamError("unsupported odds_basis")
    lower = number(min_odds)
    upper = number(max_odds) if max_odds is not None else None
    if lower is None or lower < 1:
        raise ParamError("min_odds must be finite and >= 1")
    if max_odds is not None and (upper is None or upper < lower):
        raise ParamError("max_odds must be finite and >= min_odds, or None")
    path = Path(extract_path)
    try:
        data = read_json(path) if data is None else data
    except (OSError, ValueError) as exc:
        raise ExtractError("cannot read run extract") from exc
    if not isinstance(data, dict) or not isinstance(data.get("qualifying"), list):
        raise ExtractError("extract must contain a qualifying array")
    if data.get("provider") != provider:
        raise ExtractError("extract provider does not match run provider")

    tickets, excluded, seen = [], [], {}
    for index, raw in enumerate(data["qualifying"]):
        def exclude(reason, issues=None):
            excluded.append({"index": index, "reason": reason, "issues": issues or []})

        if isinstance(raw, dict) and raw.get("provider") not in (None, provider):
            exclude("provider_mismatch")
            continue
        ticket, issues = normalize_ticket(raw, provider)
        if ticket is not None:
            fingerprint = canonical(raw)
            previous = seen.get(ticket["id"])
            if previous is not None and previous != fingerprint:
                raise ExtractError("conflicting observations for " + ticket["id"])
            if previous is not None:
                exclude("duplicate_code")
                continue
            seen[ticket["id"]] = fingerprint
        if ticket is None or issues:
            exclude("invalid_ticket", issues)
            continue
        price = odds_value(ticket, odds_basis)
        if price is None:
            exclude("missing_odds")
        elif price < lower or (upper is not None and price > upper):
            exclude("outside_band")
        else:
            ticket["raw"] = raw          # preserve display fields alongside the normalized ticket
            tickets.append(ticket)
    return {"extract_path": str(path), "provider": provider,
            "odds_basis": odds_basis, "min_odds": lower, "max_odds": upper,
            "input_count": len(data["qualifying"]), "pool_size": len(tickets),
            "tickets": tickets, "excluded": excluded}


def decorrelate_extract(extract_path, provider, params=None, *, odds_basis,
                        min_odds, max_odds=None, data=None):
    """Validate parameters, normalize the full eligible pool, then select.

    HTTP routing and resolving a run ID to its committed extract belong to the
    runner/API. Selection's odds summaries retain their own labelled basis.
    """
    parsed = parse_decorrelate_params(params)
    pool = build_pool(extract_path, provider, odds_basis=odds_basis,
                      min_odds=min_odds, max_odds=max_odds, data=data)
    tickets = pool.pop("tickets")
    return {**pool, "selection": select_subset(tickets, **parsed)}
