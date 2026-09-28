"""Append-only response observations, before filtering or parsing."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def archive_response(results_dir, provider, code, payload):
    observed_at = datetime.now(timezone.utc).isoformat()
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    record = {
        "schema_version": 1, "provider": provider, "code": code,
        "observed_at": observed_at,
        "payload_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
        "payload": payload,
    }
    folder = Path(results_dir) / "raw"
    folder.mkdir(parents=True, exist_ok=True)
    # Filenames never contain untrusted provider code/path components.
    path = folder / (uuid4().hex + ".json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return observed_at, str(path.resolve())
