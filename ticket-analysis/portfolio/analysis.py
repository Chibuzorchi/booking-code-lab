"""Conservative, provisional comparison of provider extract files."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from .contracts import identity, odds_contract, odds_value


def _kickoff_dt(value):
    """Parse an ISO kickoff into aware UTC, or None if unusable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


WARNINGS = [
    "Native identities match within provider only; legacy display identities remain provisional and separate.",
    "Structured IDs do not establish retrieval completeness, payout rules, or cross-provider equivalence.",
    "Category A means no detected overlap in this view; it does not mean safe or independent.",
    "Cross-provider event matching is not implemented; overlaps across providers may be missed.",
    "Historical extracts do not establish current bookability or decision-time prices.",
    "Counts describe candidate codes, not placed stakes, probabilities, or expected returns.",
    "Collection filters and missing selections may hide candidates; analysis cannot recover them.",
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_json(path):
    def invalid(value):
        raise ValueError(f"Invalid JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid)


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def label(value):
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def code_id(provider, code):
    code = label(code)
    return f"{provider}:{code.upper() if provider == 'sportybet' else code}"


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) - {"profiles", "include_codes", "exclude_codes"}:
        raise ValueError("Policy accepts profiles, include_codes and exclude_codes only")
    profiles = policy.get("profiles", {"all": {}})
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("profiles must be a nonempty object")
    allowed = {"providers", "min_total_odds", "max_total_odds", "min_legs", "max_legs", "odds_basis", "ticket_types"}
    normalized = {}
    for name, config in profiles.items():
        if not name or not isinstance(config, dict) or set(config) - allowed:
            raise ValueError(f"Invalid profile: {name}")
        config = dict(config)
        providers = config.get("providers", ["bet9ja", "sportybet"])
        if not isinstance(providers, list) or not providers or any(
                p not in ("bet9ja", "sportybet") for p in providers):
            raise ValueError(f"Invalid providers in {name}")
        config["providers"] = sorted(set(providers))
        basis = config.get("odds_basis")
        if basis not in (None, "site_displayed_odds", "parsed_leg_product", "verified_payout"):
            raise ValueError(f"Invalid odds_basis in {name}")
        config["odds_basis"] = basis
        types = config.get("ticket_types")
        if types is not None and (not isinstance(types, list) or not types or any(
                t not in ("unknown", "declared_accumulator", "system", "single", "bet_builder") for t in types)):
            raise ValueError(f"Invalid ticket_types in {name}")
        config["ticket_types"] = sorted(set(types)) if types else None
        for field in allowed - {"providers", "odds_basis", "ticket_types"}:
            value = config.get(field)
            if value is None:
                config[field] = None
                continue
            n = number(value)
            if n is None or n < 0 or ("legs" in field and not n.is_integer()):
                raise ValueError(f"Invalid {field} in {name}")
            # Preserve legacy zero-as-disabled bounds.
            config[field] = n if n else None
        for suffix in ("total_odds", "legs"):
            lo, hi = config[f"min_{suffix}"], config[f"max_{suffix}"]
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"Minimum exceeds maximum {suffix} in {name}")
        if basis is None and any(config[k] is not None for k in ("min_total_odds", "max_total_odds")):
            raise ValueError(f"Profile {name} has odds bounds but no explicit odds_basis")
        normalized[name] = config
    result = {"profiles": normalized}
    for field in ("include_codes", "exclude_codes"):
        values = policy.get(field, [])
        if not isinstance(values, list):
            raise ValueError(f"{field} must be a list of provider:code strings")
        ids = []
        for value in values:
            if not isinstance(value, str) or ":" not in value:
                raise ValueError(f"Invalid {field}: {value}")
            provider, code = value.split(":", 1)
            if provider not in ("bet9ja", "sportybet") or not code.strip():
                raise ValueError(f"Invalid {field}: {value}")
            ids.append(code_id(provider, code))
        result[field] = sorted(set(ids))
    return result


def normalize_ticket(raw, provider):
    if not isinstance(raw, dict):
        return None, ["ticket is not an object"]
    provider = raw.get("provider") or provider
    if provider not in ("bet9ja", "sportybet") or not label(raw.get("code")):
        return None, ["missing/unsupported provider or missing code"]
    issues = []
    if raw.get("ok") is False:
        issues.append("provider marked ticket unsuccessful")
    if raw.get("num_unavailable", 0):
        issues.append("ticket contains unavailable selections")
    if raw.get("mapping_errors"):
        issues.append("provider selection mapping errors")
    selections = raw.get("selections")
    if not isinstance(selections, list) or not selections:
        selections = []
        issues.append("missing selections")
    declared = number(raw.get("num_legs"))
    if declared is not None and declared != len(selections):
        issues.append("declared leg count differs from selections")
    legs, identity_errors = [], []
    for i, selection in enumerate(selections):
        if not isinstance(selection, dict):
            issues.append(f"selection {i + 1} is not an object")
            continue
        key, event_key, mode, reason = identity(selection, provider)
        market, pick = (label(selection.get(k)) for k in ("market", "pick"))
        if reason:
            issues.append(f"selection {i + 1} lacks event/market/pick identity")
            identity_errors.append(reason)
            continue
        legs.append({"key": key, "event_key": event_key, "identity_mode": mode,
                     "event": label(selection.get("event")),
                     "league": label(selection.get("league")), "market": market, "pick": pick,
                     "odds": number(selection.get("odds")),
                     "kickoff": label(selection.get("kickoff"))})
    keys = [canonical(leg["key"]) for leg in legs]
    if len(set(keys)) != len(keys):
        issues.append("duplicate or indistinguishable selections within ticket")
    kicks = [dt for dt in (_kickoff_dt(leg.get("kickoff")) for leg in legs) if dt]
    if kicks and min(kicks) <= datetime.now(timezone.utc):
        issues.append("coupon expired: a leg has already kicked off")
    return {"id": code_id(provider, raw["code"]), "provider": provider,
            "odds": odds_contract(raw, provider), "num_legs": len(selections),
            "bet_type": raw.get("bet_type", "unknown"), "legs": legs,
            "identity_errors": identity_errors,
            "retrieval_completeness": raw.get("retrieval_completeness", "unknown"),
            "observed_at": raw.get("observed_at"),
            "content_id": digest(sorted(set(keys)))}, issues


def load_inputs(paths):
    """Retain all inputs; conflicting observations require explicit source selection."""
    observations = defaultdict(dict)
    sources, unclassified = [], []
    for path in sorted(set(Path(p).resolve() for p in paths)):
        raw_bytes = path.read_bytes()
        data = read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("qualifying"), list):
            raise ValueError(f"{path}: expected an extract object with qualifying array")
        source_id = hashlib.sha256(raw_bytes).hexdigest()
        sources.append({"path": str(path), "sha256": source_id,
                        "ticket_observations": len(data["qualifying"]),
                        "leg_occurrences": sum(len(t.get("selections", [])) for t in data["qualifying"]
                                               if isinstance(t, dict) and isinstance(t.get("selections"), list)),
                        "collection": {k: v for k, v in data.items() if k != "qualifying"}})
        for index, raw in enumerate(data["qualifying"]):
            ticket, issues = normalize_ticket(raw, data.get("provider"))
            location = {"source": str(path), "source_hash": source_id, "index": index}
            if ticket is None:
                unclassified.append({**location, "id": None, "reasons": issues})
                continue
            observation = observations[ticket["id"]].setdefault(digest(raw), {
                "ticket": ticket, "issues": issues, "locations": []})
            observation["locations"].append(location)
    tickets = []
    for tid, versions in sorted(observations.items()):
        if len(versions) > 1:
            unclassified.append({"id": tid, "reasons": ["conflicting observations; choose an explicit input snapshot"],
                                 "snapshots": [v["ticket"] for v in versions.values()],
                                 "locations": [loc for v in versions.values() for loc in v["locations"]]})
            continue
        observation = next(iter(versions.values()))
        if observation["issues"]:
            unclassified.append({"id": tid, "reasons": observation["issues"],
                                 "snapshot": observation["ticket"],
                                 "locations": observation["locations"]})
        else:
            tickets.append({**observation["ticket"], "locations": observation["locations"]})
    return tickets, unclassified, sources


def rejection_reasons(ticket, config):
    reasons = []
    if ticket["provider"] not in config["providers"]:
        reasons.append("provider outside profile")
    if config["ticket_types"] and ticket["odds"]["ticket_structure"] not in config["ticket_types"]:
        reasons.append("ticket structure outside profile")
    for suffix, actual in (("total_odds", odds_value(ticket, config["odds_basis"])), ("legs", ticket["num_legs"])):
        lo, hi = config[f"min_{suffix}"], config[f"max_{suffix}"]
        if (lo is not None or hi is not None) and actual is None:
            reasons.append(f"missing {suffix} for declared basis {config['odds_basis']}")
        elif actual is not None:
            if lo is not None and actual < lo:
                reasons.append(f"below min_{suffix}")
            if hi is not None and actual > hi:
                reasons.append(f"above max_{suffix}")
    return reasons


def compare(tickets, top_n=10, long_odds=None):
    selections, events = defaultdict(set), defaultdict(set)
    metadata, aliases = {}, defaultdict(list)
    by_id = {t["id"]: t for t in tickets}
    for ticket in tickets:
        aliases[ticket["content_id"]].append(ticket["id"])
        for leg in ticket["legs"]:
            key = canonical(leg["key"])
            selections[key].add(ticket["id"])
            events[canonical(leg["event_key"])].add(ticket["id"])
            metadata.setdefault(key, leg)
    rows, per_code = [], defaultdict(list)
    for key, ids in selections.items():
        if len(ids) < 2:
            continue
        odds = {tid: next(l["odds"] for l in by_id[tid]["legs"] if canonical(l["key"]) == key)
                for tid in sorted(ids)}
        row = {"selection": metadata[key]["key"], "event": metadata[key]["event"],
               "market": metadata[key]["market"], "pick": metadata[key]["pick"],
               "codes": sorted(ids), "code_count": len(ids),
               "distinct_ticket_contents": len({by_id[tid]["content_id"] for tid in ids}),
               "observed_odds": odds}
        rows.append(row)
        for tid in ids:
            per_code[tid].append({"selection": row["selection"], "event": row["event"],
                                  "market": row["market"], "pick": row["pick"],
                                  "shared_with": sorted(ids - {tid})})
    rows.sort(key=lambda r: (-r["code_count"], canonical(r["selection"])))
    details = []
    for tid, ticket in sorted(by_id.items()):
        shared = sorted(per_code[tid], key=lambda r: canonical(r["selection"]))
        details.append({"id": tid, "category": "B" if shared else "A",
                        "num_legs": ticket["num_legs"], "odds": ticket["odds"],
                        "ticket_type": ticket["bet_type"], "shared_leg_fraction": len(shared) / ticket["num_legs"],
                        "overlaps": shared})
    event_rows = [{"event_key": json.loads(key), "codes": sorted(ids), "code_count": len(ids)}
                  for key, ids in events.items() if len(ids) > 1]
    event_rows.sort(key=lambda r: (-r["code_count"], canonical(r["event_key"])))
    return {"candidate_count": len(tickets), "distinct_ticket_contents": len(aliases),
            "identity_modes": sorted({l["identity_mode"] for t in tickets for l in t["legs"]}),
            "concentration_summary": {
                "median_ticket_legs": median([t["num_legs"] for t in details]) if details else None,
                "median_shared_leg_fraction": median([t["shared_leg_fraction"] for t in details]) if details else None,
                "max_selection_code_count": max((r["code_count"] for r in rows), default=0)},
            "concentration_ranking": sorted(details, key=lambda t: (-t["shared_leg_fraction"], t["id"])),
            "category_A": [t["id"] for t in details if t["category"] == "A"],
            "category_B": [t["id"] for t in details if t["category"] == "B"],
            "tickets": details, "R1_shared": rows, "R2_top": rows[:top_n],
            "R3_long_odds_proxy": [r for r in rows if long_odds is not None and any(
                value is not None and value >= long_odds for value in r["observed_odds"].values())],
            "same_event_exposure": event_rows,
            "identical_content_codes": [sorted(v) for _, v in sorted(aliases.items()) if len(v) > 1]}


def analyze(paths, policy, top_n=10, long_odds=None, select=None):
    if top_n < 0 or (long_odds is not None and (number(long_odds) is None or long_odds <= 1)):
        raise ValueError("top_n must be nonnegative; long_odds must exceed 1")
    policy = validate_policy(policy)
    tickets, unclassified, sources = load_inputs(paths)
    include, exclude = set(policy["include_codes"]), set(policy["exclude_codes"])
    profiles, exclusions, union = {}, {}, {}
    for name, config in sorted(policy["profiles"].items()):
        chosen, rejected = [], []
        for ticket in tickets:
            reasons = rejection_reasons(ticket, config)
            if ticket["id"] in exclude:
                reasons.append("explicit exclusion")
            if include and ticket["id"] not in include:
                reasons.append("outside explicit shortlist")
            if reasons:
                rejected.append({"id": ticket["id"], "reasons": reasons})
            else:
                chosen.append(ticket)
                union[ticket["id"]] = ticket
        profiles[name] = compare(chosen, top_n, long_odds)
        exclusions[name] = rejected
    known = {t["id"] for t in tickets} | {u["id"] for u in unclassified if u["id"]}
    snapshots = [{k: v for k, v in t.items() if k != "locations"} for t in tickets]
    for item in unclassified:
        if "snapshot" in item:
            snapshots.append({**item["snapshot"], "quarantine_reasons": item["reasons"]})
    snapshots.sort(key=lambda t: t["id"])
    identity_reasons = defaultdict(int)
    for t in snapshots:
        for reason in t["identity_errors"]:
            identity_reasons[reason] += 1
    reason_tickets = defaultdict(int)
    for item in unclassified:
        for reason in set(item.get("snapshot", {}).get("identity_errors", [])):
            reason_tickets[reason] += 1
    total = len(tickets) + len(unclassified)
    coverage = {"scope": "input pool before profile filtering; duplicate observations collapsed",
                "ticket_records": total, "classifiable_tickets": len(tickets),
                "quarantined_tickets": len(unclassified),
                "classifiable_ticket_fraction": len(tickets) / total if total else None,
                "raw_ticket_observations": sum(s["ticket_observations"] for s in sources),
                "raw_leg_occurrences": sum(s["leg_occurrences"] for s in sources),
                "identity_failure_legs_in_nonconflicting_snapshots": dict(sorted(identity_reasons.items())),
                "identity_failure_tickets": dict(sorted(reason_tickets.items())),
                "identity_modes_classifiable_legs": {mode: sum(l["identity_mode"] == mode for t in tickets for l in t["legs"])
                                                     for mode in ("structured_native", "legacy_display")},
                "conflicting_observations": sum("snapshots" in item for item in unclassified),
                "selected_candidate_count": len(union),
                "payout_verified_tickets": 0,
                "site_product_discrepancy_tickets": sum(t["odds"]["site_product_discrepancy"] is True for t in snapshots)}
    populations = {s["collection"].get("population", "harvested_candidate_pool") for s in sources}
    population = next(iter(populations)) if len(populations) == 1 else "mixed_input_candidate_pool"
    selection = None
    if select is not None:
        from .selection import select_subset
        selection = select_subset(list(union.values()), select["max_exposure"], select.get("target"))
    return {"schema_version": 2, "mode": "identity_aware", "warnings": WARNINGS,
            "population": "explicit_shortlist" if include else population,
            "coverage": coverage, "snapshots": snapshots,
            "policy": policy, "policy_hash": digest(policy), "sources": sources,
            "top_n": top_n, "long_odds_threshold": long_odds,
            "profiles": profiles, "combined": compare(list(union.values()), top_n, long_odds),
            "excluded": exclusions, "unclassified": unclassified,
            "selection": selection,
            "missing_requested_codes": sorted(include - known)}


def changes(previous, current):
    """Membership/classification changes, not a claim about historical settlement."""
    if (not isinstance(previous, dict) or previous.get("schema_version") not in (1, 2)
            or not isinstance(previous.get("combined"), dict)
            or not isinstance(previous.get("profiles"), dict)):
        raise ValueError("Previous report must be a schema-version-1 or 2 portfolio report")
    for view in [previous["combined"], *previous["profiles"].values()]:
        if (not isinstance(view, dict) or not isinstance(view.get("tickets"), list)
                or any(not isinstance(t, dict) or not isinstance(t.get("id"), str)
                       or t.get("category") not in ("A", "B") for t in view["tickets"])):
            raise ValueError("Previous report contains an invalid ticket view")
    result = {}
    old_views = {"combined": previous["combined"], **{"profile:" + k: v for k, v in previous["profiles"].items()}}
    new_views = {"combined": current["combined"], **{"profile:" + k: v for k, v in current["profiles"].items()}}
    for name in sorted(set(old_views) | set(new_views)):
        old = {t["id"]: t for t in old_views.get(name, {}).get("tickets", [])}
        new = {t["id"]: t for t in new_views.get(name, {}).get("tickets", [])}
        result[name] = {
            "added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
            "category_changes": [{"id": tid, "before": old[tid]["category"], "after": new[tid]["category"]}
                                 for tid in sorted(old.keys() & new.keys())
                                 if old[tid]["category"] != new[tid]["category"]],
        }
    result["selection_changes"] = selection_changes(previous, current)
    return result


def selection_changes(previous, current):
    """Snapshot-level evidence only; removal never means a settled loss."""
    if "snapshots" not in previous:
        return {"status": "unavailable", "reason": "Previous report predates selection snapshots; replay its archived inputs first."}
    old = {t["id"]: t for t in previous["snapshots"]}
    new = {t["id"]: t for t in current["snapshots"]}
    rows = []
    for tid in sorted(old.keys() & new.keys()):
        a, b = old[tid], new[tid]
        am = {l["identity_mode"] for l in a["legs"]}
        bm = {l["identity_mode"] for l in b["legs"]}
        if am != bm or len(am) != 1:
            rows.append({"id": tid, "status": "identity_format_change_or_unresolved",
                         "before_modes": sorted(am), "after_modes": sorted(bm),
                         "removed": None, "added": None})
            continue
        akeys = {canonical(l["key"]): l for l in a["legs"]}
        bkeys = {canonical(l["key"]): l for l in b["legs"]}
        removed = [akeys[k] for k in sorted(akeys.keys() - bkeys.keys())]
        added = [bkeys[k] for k in sorted(bkeys.keys() - akeys.keys())]
        changed = [{"key": akeys[k]["key"], "before": akeys[k], "after": bkeys[k]}
                   for k in sorted(akeys.keys() & bkeys.keys()) if akeys[k] != bkeys[k]]
        if not (removed or added or changed):
            continue
        complete = (a["retrieval_completeness"] == b["retrieval_completeness"] == "complete"
                    and not a.get("quarantine_reasons") and not b.get("quarantine_reasons"))
        chronological = False
        try:
            from datetime import datetime
            at, bt = (datetime.fromisoformat(t["observed_at"].replace("Z", "+00:00")) for t in (a, b))
            chronological = at.tzinfo is not None and bt.tzinfo is not None and at < bt
        except (ValueError, TypeError, AttributeError):
            pass
        status = "observed_selection_change"
        if removed:
            status = ("confirmed_disappearance_from_complete_response" if complete and chronological and am == {"structured_native"}
                      else "possible_disappearance_incomplete_or_unverified_retrieval")
        rows.append({"id": tid, "status": status, "chronological": chronological,
                     "before_completeness": a["retrieval_completeness"], "after_completeness": b["retrieval_completeness"],
                     "removed": removed, "added": added, "changed": changed})
    return {"status": "compared", "tickets": rows,
            "missing_current_snapshots": sorted(old.keys() - new.keys()),
            "new_snapshots": sorted(new.keys() - old.keys()),
            "note": "Missing tickets or format changes do not prove vanished legs; no settlement is inferred."}


def semantic_hash(report):
    """Hash analysis meaning, separately from filesystem and source-byte provenance."""
    ignored = {"sources", "path", "source", "source_hash", "locations", "raw_response_path",
               "generated_at", "analysis_hash", "semantic_hash", "provenance_hash",
               "previous_report_hash", "positions_input_hash"}

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in ignored}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    data = clean(report)
    data["unclassified"] = sorted(data.get("unclassified", []), key=canonical)
    for item in data["unclassified"]:
        if "snapshots" in item:
            item["snapshots"].sort(key=canonical)
    return digest(data)
