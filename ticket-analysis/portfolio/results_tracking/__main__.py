"""Run with python3 -m portfolio.results_tracking --help from ticket-analysis/.

Population audit only (F9_RESULTS_PLAN.md section 10 step 1): freezes and
inventories retained extracts for named runs. No source fetching, fixture
matching, or settlement happens here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..registry import RunRegistry, RunNotFoundError, RunStateError
from .inputs import PopulationAuditError, audit_run


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Freeze and inventory retained run extracts; no outcome or settlement claim.")
    parser.add_argument("--run-id", action="append", dest="run_ids", required=True,
                        help="A succeeded run id to audit (repeatable)")
    parser.add_argument("--provider", action="append", dest="providers", default=[],
                        help="Expected provider for the run at the same position (optional, repeatable)")
    parser.add_argument("--expect-rows", action="append", dest="expect_rows", default=[], type=int,
                        help="Expected retained row count for the run at the same position (optional, repeatable)")
    parser.add_argument("--workspace", type=Path, help="Repo root holding results/runs/ (default: autodetect)")
    parser.add_argument("--output", required=True, type=Path, help="New audit directory; existing directories are refused")
    args = parser.parse_args(argv)
    try:
        if args.providers and len(args.providers) != len(args.run_ids):
            raise ValueError("--provider must be supplied once per --run-id, or not at all")
        if args.expect_rows and len(args.expect_rows) != len(args.run_ids):
            raise ValueError("--expect-rows must be supplied once per --run-id, or not at all")
        registry = RunRegistry(args.workspace)
        reports = []
        for i, run_id in enumerate(args.run_ids):
            expected_provider = args.providers[i] if args.providers else None
            expected_rows = args.expect_rows[i] if args.expect_rows else None
            reports.append(audit_run(registry, run_id, expected_provider, expected_rows))
        generated_at = datetime.now(timezone.utc).isoformat()
        args.output.mkdir(parents=True, exist_ok=False)
        for report in reports:
            write_json(args.output / f"{report['run_id']}.json", report)
        summary = {
            "generated_at": generated_at,
            "runs": [{"run_id": r["run_id"], "provider": r["provider"], "row_count": r["row_count"],
                      "extract_sha256": r["extract_sha256"], "unique_fixtures": r["unique_fixtures"],
                      "identity_modes": r["identity_modes"],
                      "conflicting_observation_count": len(r["conflicting_observations"]),
                      "raw_response_missing_count": len(r["raw_response_missing"])}
                     for r in reports],
        }
        write_json(args.output / "summary.json", summary)
        write_json(args.output / "manifest.json", {
            "generated_at": generated_at,
            "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(args.output.iterdir()) if p.is_file()},
        })
        for r in reports:
            print(f"{r['run_id']} ({r['provider']}): {r['row_count']} retained rows, "
                  f"{r['unique_fixtures']} unique fixtures, "
                  f"{len(r['conflicting_observations'])} conflicting leg observations, "
                  f"{len(r['raw_response_missing'])} rows missing a raw response path")
        print(f"Audit written to {args.output.resolve()}")
        return 0
    except (OSError, ValueError, TypeError, RunNotFoundError, RunStateError, PopulationAuditError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
