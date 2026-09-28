"""Run with python3 -m portfolio --help from ticket-analysis/."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .analysis import analyze, changes, digest, load_inputs, read_json, semantic_hash, validate_policy
from .positions import summarize_positions


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def text_report(result):
    lines = ["BOOKING-CODE EXPOSURE — IDENTITY-AWARE COMPARISON", "",
             f"Population: {result['population']} (not automatically a placed portfolio)",
             "COVERAGE (before profile filtering)", json.dumps(result["coverage"], indent=2),
             "", *result["warnings"], ""]
    for name, view in [("COMBINED", result["combined"]), *result["profiles"].items()]:
        lines += [f"VIEW: {name}", f"Candidates: {view['candidate_count']} | identity modes: {', '.join(view['identity_modes'])}",
                  "Concentration: " + json.dumps(view["concentration_summary"]),
                  "TOP SHARED SELECTIONS (code counts, not monetary loss)"]
        for row in view["R2_top"]:
            lines.append(f"  {row['code_count']} codes / {row['distinct_ticket_contents']} distinct contents: {row['event']} | {row['market']} | {row['pick']}")
        lines.append(f"Navigation: A={len(view['category_A'])}, B={len(view['category_B'])}; neither is a risk grade")
        if name in result["policy"]["profiles"]:
            lines.append("Profile: " + json.dumps(result["policy"]["profiles"][name], sort_keys=True))
        for ticket in view["concentration_ranking"]:
            odds = ticket["odds"]
            lines.append(f"{ticket['id']} [{ticket['category']}] legs={ticket['num_legs']} shared={ticket['shared_leg_fraction']:.1%} structure={odds['ticket_structure']}")
            lines.append(f"  site_displayed={odds['site_displayed_odds']} parsed_leg_product={odds['parsed_leg_product']} payout={odds['payout_semantics']['status']}")
            for overlap in ticket["overlaps"]:
                lines.append(f"  {overlap['event']} | {overlap['market']} | {overlap['pick']} -> {', '.join(overlap['shared_with'])}")
        lines.append("Same-event warnings (including different selections):")
        for event in view["same_event_exposure"]:
            lines.append(f"  {':'.join(event['event_key'])} -> {', '.join(event['codes'])}")
        lines.append("")
    sel = result.get("selection")
    if sel is not None:
        lines += ["", "EXPOSURE-CONSTRAINED SELECTION (candidate codes, not placed stakes)",
                  f"max_exposure={sel['max_exposure']} target={sel['target']} | pool={sel['pool_size']} selected={sel['selected_count']} rejected={sel['rejected_count']} realized_max_exposure={sel['realized_max_exposure']}",
                  "order_rule: " + sel["order_rule"],
                  "by_provider: " + json.dumps(sel["by_provider"], sort_keys=True),
                  "odds range (parsed_leg_product): min=" + str(sel["odds_range"]["min"]) + " max=" + str(sel["odds_range"]["max"]) + " median=" + str(sel["odds_range"]["median"]),
                  "OUTPUT CLASSIFIED BY UNIQUENESS:"]
        for b in sel["uniqueness_buckets"]:
            orr = b["odds_range"]
            lines.append(f"  [{b['tier']}] {b['code_count']} codes | odds min={orr['min']} max={orr['max']}")
            lines.append("    " + (", ".join(b["codes"]) or "(none)"))
        lines.append("SELECTED CODES (all): " + (", ".join(sel["selected_codes"]) or "(none)"))
        for row in sel["selected"]:
            lines.append(f"  {row['id']} legs={row['num_legs']} parsed_leg_product={row['parsed_leg_product']} site={row['site_displayed_odds']} payout={row['payout_status']} shared_legs={row['shared_leg_count']}")
            for leg in row["shared_legs"]:
                lines.append(f"    shares {leg['event']} | {leg['market']} | {leg['pick']} -> {', '.join(leg['shared_with_selected'])}")
        capped = [r for r in sel["rejected"] if r["reason"] == "exposure_cap"]
        if capped:
            lines.append("REJECTED (exposure cap):")
            for row in capped:
                blk = "; ".join(f"{b['event']} | {b['market']} | {b['pick']} (full with {', '.join(b['at_capacity_with'])})" for b in row["blocking"])
                lines.append(f"  {row['id']} blocked_by: {blk}")
        for note in sel["caveats"]:
            lines.append("NOTE: " + note)
        lines.append("")
    lines.append("UNCLASSIFIED")
    for item in result["unclassified"]:
        lines.append(f"{item['id'] or item.get('source')}: {'; '.join(item['reasons'])}")
    lines.append("MISSING REQUESTED CODES: " + ", ".join(result["missing_requested_codes"]))
    if "changes" in result:
        lines += ["", "CHANGES FROM PREVIOUS REPORT", json.dumps(result["changes"], indent=2)]
    if "positions" in result:
        lines += ["", "USER-SUPPLIED POSITIONS (PROVISIONAL IDENTITY)", json.dumps(result["positions"], indent=2)]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline provisional overlap analysis; never places or books bets.")
    parser.add_argument("--from", dest="inputs", nargs="+", required=True, help="Extract files or folders (nonrecursive *.json)")
    parser.add_argument("--policy", type=Path, help="JSON file containing named profiles")
    parser.add_argument("--odds-basis", choices=["site_displayed_odds", "parsed_leg_product", "verified_payout"],
                        help="Required for odds bounds unless declared by each profile; overrides selected profiles")
    parser.add_argument("--profile", action="append", help="Use only these named profiles (repeatable)")
    parser.add_argument("--codes", nargs="*", help="Provider-qualified shortlist; overrides policy include_codes")
    parser.add_argument("--exclude", nargs="*", help="Provider-qualified exclusions; overrides policy exclude_codes")
    for flag in ("min-odds", "max-odds", "min-legs", "max-legs"):
        parser.add_argument(f"--{flag}", type=float, help="Override this bound in selected profiles; 0 disables")
    parser.add_argument("--top", type=int, default=10, help="R2 row limit only; never truncates candidates")
    parser.add_argument("--long-odds", type=float, help="R3 long-odds proxy threshold; not a probability")
    parser.add_argument("--previous", type=Path, help="Previous report.json for membership and A/B changes")
    parser.add_argument("--positions", type=Path, help="User-supplied selected/placed/settled positions JSON")
    parser.add_argument("--select", action="store_true", help="Emit an exposure-constrained shortlist of booking codes")
    parser.add_argument("--max-exposure", type=int, help="Exposure cap: max selected codes any one selection may span (requires --select)")
    parser.add_argument("--target", type=int, help="Optional cap on how many codes to select")
    parser.add_argument("--output", required=True, type=Path, help="New run directory; existing directories are refused")
    args = parser.parse_args(argv)
    try:
        paths = []
        for value in args.inputs:
            path = Path(value)
            if path.is_dir():
                paths.extend(sorted(path.glob("*.json")))
            elif path.is_file():
                paths.append(path)
            else:
                raise ValueError(f"Input does not exist: {path}")
        if not paths:
            raise ValueError("No JSON extracts found")
        policy_input = read_json(args.policy) if args.policy else {}
        if args.odds_basis and isinstance(policy_input, dict):
            for config in policy_input.get("profiles", {}).values():
                if isinstance(config, dict):
                    config["odds_basis"] = args.odds_basis
        policy = validate_policy(policy_input)
        if args.profile:
            missing = set(args.profile) - set(policy["profiles"])
            if missing:
                raise ValueError(f"Unknown profiles: {sorted(missing)}")
            policy["profiles"] = {k: v for k, v in policy["profiles"].items() if k in args.profile}
        for name, attr in (("min_total_odds", "min_odds"), ("max_total_odds", "max_odds"),
                           ("min_legs", "min_legs"), ("max_legs", "max_legs")):
            value = getattr(args, attr)
            if value is not None:
                for config in policy["profiles"].values():
                    config[name] = value
        if args.odds_basis:
            for config in policy["profiles"].values():
                config["odds_basis"] = args.odds_basis
        for key, value in (("include_codes", args.codes), ("exclude_codes", args.exclude)):
            if value is not None:
                policy[key] = value
        select = None
        if args.select:
            if args.max_exposure is None:
                raise ValueError("--select requires --max-exposure")
            if args.max_exposure < 1:
                raise ValueError("--max-exposure must be an integer >= 1")
            if args.target is not None and args.target < 1:
                raise ValueError("--target must be an integer >= 1")
            select = {"max_exposure": args.max_exposure, "target": args.target}
        elif args.max_exposure is not None or args.target is not None:
            raise ValueError("--max-exposure/--target require --select")
        result = analyze(paths, policy, args.top, args.long_odds, select)
        previous = read_json(args.previous) if args.previous else None
        if previous is not None:
            result["changes"] = changes(previous, result)
            result["previous_report_hash"] = digest(previous)
        positions = read_json(args.positions) if args.positions else None
        if positions is not None:
            all_tickets, _, _ = load_inputs(paths)
            selected_ids = {ticket["id"] for ticket in result["combined"]["tickets"]}
            result["positions"] = summarize_positions(positions, [t for t in all_tickets if t["id"] in selected_ids])
            result["positions_input_hash"] = digest(positions)
        result["software_version"] = __version__
        result["semantic_hash"] = semantic_hash(result)
        result["analysis_hash"] = result["semantic_hash"]
        result["provenance_hash"] = digest(result["sources"])
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        # mkdir is exclusive: no existing report is overwritten.
        args.output.mkdir(parents=True, exist_ok=False)
        archive = args.output / "inputs"
        archive.mkdir()
        for source in result["sources"]:
            contents = Path(source["path"]).read_bytes()
            if hashlib.sha256(contents).hexdigest() != source["sha256"]:
                raise ValueError("Input changed during analysis; discard incomplete run and retry")
            target = archive / (source["sha256"] + ".json")
            if not target.exists():
                with target.open("xb") as stream:
                    stream.write(contents)
        write_json(args.output / "report.json", result)
        write_json(args.output / "policy.json", result["policy"])
        if previous is not None:
            write_json(args.output / "previous_report.json", previous)
        if positions is not None:
            write_json(args.output / "positions_input.json", positions)
        with (args.output / "similarity_report.txt").open("x", encoding="utf-8") as stream:
            stream.write(text_report(result))
        for category in ("A", "B"):
            with (args.output / f"category_{category}_provisional.txt").open("x", encoding="utf-8") as stream:
                stream.write("# Provisional; combined candidate view; not a safety classification.\n")
                stream.write("".join(t + "\n" for t in result["combined"][f"category_{category}"]))
        if result.get("selection") is not None:
            sel = result["selection"]
            write_json(args.output / "selection.json", sel)
            by_code = {r["code"]: r for r in sel["selected"]}
            with (args.output / "selected_codes.txt").open("x", encoding="utf-8") as stream:
                orr = sel["odds_range"]
                stream.write(f"# Exposure-constrained shortlist. max_exposure={sel['max_exposure']} target={sel['target']}.\n")
                stream.write(f"# {sel['selected_count']} codes; odds(parsed_leg_product) min={orr['min']} max={orr['max']} median={orr['median']}.\n")
                stream.write("# Candidate booking codes, not placed stakes; payout unknown.\n")
                for b in sel["uniqueness_buckets"]:
                    bo = b["odds_range"]
                    stream.write(f"\n## {b['tier']} - {b['code_count']} codes (odds {bo['min']}..{bo['max']})\n")
                    for code in b["codes"]:
                        row = by_code[code]
                        stream.write(f"{row['provider']}\t{code}\tlegs={row['num_legs']}\todds={row['parsed_leg_product']}\tshared_games={row['shared_leg_count']}\n")
        write_json(args.output / "manifest.json", {
            "complete": True, "software_version": __version__, "analysis_hash": result["analysis_hash"],
            "semantic_hash": result["semantic_hash"], "provenance_hash": result["provenance_hash"],
            "generated_at": result["generated_at"], "policy": result["policy"],
            "top_n": args.top, "long_odds_threshold": args.long_odds,
            "sources": result["sources"],
            "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(args.output.iterdir()) if p.is_file()}})
        view = result["combined"]
        coverage = result["coverage"]
        print(f"{result['population']}: {coverage['classifiable_tickets']}/{coverage['ticket_records']} classifiable; quarantined={coverage['quarantined_tickets']}; selected={view['candidate_count']}")
        print(f"Maximum shared-selection exposure: {view['concentration_summary']['max_selection_code_count']} codes; A={len(view['category_A'])}, B={len(view['category_B'])} (not risk grades)")
        print(f"Report: {args.output.resolve() / 'similarity_report.txt'}")
        if result.get("selection") is not None:
            sel = result["selection"]
            print(f"Selection: {sel['selected_count']}/{sel['pool_size']} codes at max_exposure={sel['max_exposure']} (realized {sel['realized_max_exposure']}); by_provider={sel['by_provider']}")
            orr = sel["odds_range"]
            print(f"Odds (parsed_leg_product): min={orr['min']} max={orr['max']} median={orr['median']}")
            print("By uniqueness:")
            for b in sel["uniqueness_buckets"]:
                bo = b["odds_range"]
                print(f"  [{b['tier']}] {b['code_count']} codes (odds {bo['min']}..{bo['max']}): " + ", ".join(b["codes"]))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
