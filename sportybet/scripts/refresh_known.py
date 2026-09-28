"""One bounded read-only GET per known audit code; never scans or books tickets.

Run from sportybet/: python3 -m scripts.refresh_known --source ... --output ...
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from infra.parser import parse_share
from engine.settings import DEFAULT_UA


CODES = ("N5PZDF", "N5PZ3Y", "N5PZHV", "N5PZ2P", "N5PZM9")


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source.read_text())
    known = {t["code"] for t in source["qualifying"] if t.get("provider") == "sportybet"}
    if not set(CODES) <= known:
        parser.error("All five fixed audit codes must already exist in the source extract")
    args.output.mkdir(parents=True, exist_ok=False)
    raw_dir = args.output / "raw"
    raw_dir.mkdir()
    attempts, tickets = [], []
    for code in CODES:
        observed = datetime.now(timezone.utc).isoformat()
        url = "https://www.sportybet.com/api/ng/orders/share/" + code
        record = {"code": code, "provider": "sportybet", "method": "GET", "url": url,
                  "request_started_at": observed, "http_status": None, "error": None}
        body = b""
        request = Request(url, headers={"User-Agent": DEFAULT_UA, "Accept": "application/json, text/plain, */*",
                                        "Referer": "https://www.sportybet.com/ng/"}, method="GET")
        try:
            with urlopen(request, timeout=15) as response:
                record["http_status"] = response.status
                body = response.read()
        except HTTPError as error:
            record["http_status"] = error.code
            body = error.read()
            record["error"] = str(error)
        except (URLError, OSError) as error:
            record["error"] = str(error)
        record["observed_at"] = datetime.now(timezone.utc).isoformat()
        record["body_sha256"] = hashlib.sha256(body).hexdigest()
        with (raw_dir / (code + ".body")).open("xb") as stream:
            stream.write(body)
        try:
            payload = json.loads(body) if body else None
        except (ValueError, UnicodeDecodeError):
            payload = None
        record["payload"] = payload
        raw_path = raw_dir / (code + ".json")
        # Capture transport evidence even if parsing fails.
        save(raw_path, record)
        try:
            coupon = parse_share(code, payload if isinstance(payload, dict) else None)
            coupon.observed_at = record["observed_at"]
            coupon.raw_response_path = str(raw_path.resolve())
            tickets.append(coupon.as_dict())
            record["parser_ok"] = coupon.ok
            record["parser_error"] = coupon.error
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            record["parser_ok"] = False
            record["parser_error"] = str(error)
            tickets.append({"provider": "sportybet", "code": code, "ok": False,
                            "selections": [], "num_legs": 0, "mapping_errors": [str(error)],
                            "observed_at": record["observed_at"], "retrieval_completeness": "incomplete"})
        attempts.append({k: v for k, v in record.items() if k != "payload"})
        print(f"{code}: HTTP={record['http_status']} parser_ok={record['parser_ok']} error={record['error'] or record['parser_error']}", flush=True)
    save(args.output / "extract.json", {"provider": "sportybet", "population": "five_known_audit_codes",
                                        "qualifying": tickets})
    save(args.output / "refresh_manifest.json", {"complete": True, "request_limit": len(CODES),
                                                "attempts": attempts, "no_booking_or_wagering": True})
    return 3 if all(a["http_status"] is None for a in attempts) else 0


if __name__ == "__main__":
    raise SystemExit(main())
