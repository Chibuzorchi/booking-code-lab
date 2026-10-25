"""Server-owned historical imports. Freeze bytes before starting a result job."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .fixtures import Fixture, FixtureProvenance, FixtureState, KickoffTime
from .sources.football_data_import import import_football_data_csv


def freeze_local_sources(config_path: Path | None) -> dict:
    bundle = {"aliases": [], "verified_mappings": [], "ticket_structures": [],
              "fixtures": [], "raw_sources": [], "network_enabled": True}
    if config_path is None:
        return bundle
    raw = config_path.read_bytes()
    config = json.loads(raw)
    if not isinstance(config, dict):
        raise ValueError("results configuration must be an object")
    unknown = config.keys() - {"aliases", "verified_mappings", "ticket_structures", "network_enabled", "football_data_files"}
    if unknown:
        raise ValueError("unsupported results configuration fields: " + ", ".join(sorted(unknown)))
    for key in ("aliases", "verified_mappings", "ticket_structures"):
        value = config.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list")
        bundle[key] = value
    network = config.get("network_enabled", True)
    if type(network) is not bool:
        raise ValueError("network_enabled must be a boolean")
    bundle["network_enabled"] = network
    bundle["raw_sources"].append(_observation("configuration", raw))
    imports = config.get("football_data_files", [])
    if not isinstance(imports, list):
        raise ValueError("football_data_files must be a list")
    seen = set()
    for item in imports:
        if not isinstance(item, dict):
            raise ValueError("football_data_files entries must be objects")
        for key in ("path", "division", "season", "downloaded_at", "competition"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"football_data_files.{key} must be a nonempty string")
        path = Path(item["path"])
        if not path.is_absolute():
            path = config_path.parent / path
        csv_bytes = path.read_bytes()
        # Parse the very bytes being archived, even if the user's original file changes.
        with TemporaryDirectory() as tmp:
            frozen = Path(tmp) / "input.csv"
            frozen.write_bytes(csv_bytes)
            parsed = import_football_data_csv(frozen, division=item["division"],
                season=item["season"], downloaded_at=item["downloaded_at"])
        bundle["raw_sources"].append(_observation("football_data_csv", csv_bytes))
        for row in parsed.fixtures:
            identity = [row.division, parsed.season, row.date.isoformat(), row.home_team, row.away_team]
            fixture_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            if fixture_id in seen:
                raise ValueError("overlapping CSV imports contain the same fixture; select one observation")
            seen.add(fixture_id)
            bundle["fixtures"].append(Fixture(
                FixtureProvenance("football_data", fixture_id, parsed.downloaded_at, parsed.sha256),
                item["competition"], parsed.season, row.home_team, row.away_team,
                KickoffTime(row.date.isoformat(), "date_only"),
                FixtureState.FINISHED_REGULATION if row.score else FixtureState.SCHEDULED,
                row.score))
    return bundle


def _observation(kind: str, raw: bytes) -> dict:
    return {"kind": kind, "sha256": hashlib.sha256(raw).hexdigest(),
            "base64": base64.b64encode(raw).decode("ascii")}
