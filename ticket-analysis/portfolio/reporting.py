"""Build offline, human-readable provider result indexes without changing evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

from .analysis import label, load_inputs, normalize_ticket, number, read_json
from .pages import inline_copy_js
from .selection import select_subset
from .contracts import odds_contract

PROVIDERS = {"bet9ja": "Bet9ja", "sportybet": "SportyBet"}

# Bankers bucket: realistic, low-odd accumulators with a higher hit rate than the
# 2000+ longshots. Harvested by scanning with a low --min-odds and a --max-odds-cap.
BANKER_MIN_ODDS = 5.0
BANKER_MAX_ODDS = 50.0
# Longshots are the numbers-game shots, but a 40-50 leg monster at astronomical
# odds is unbookable noise, not a shot. Keep the bucket to realistic longshots.
LONGSHOT_MIN_ODDS = 5000.0
LONGSHOT_MAX_ODDS = 350000.0


def clean(value):
    return " ".join(str(value).split())


def fmt(value):
    return f"{value:,.2f}" if value is not None else "-"


def table(headers, rows, numeric=()):
    rows = [[clean(cell) for cell in row] for row in rows]
    widths = [max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
              for i, header in enumerate(headers)]
    def line(row):
        return " | ".join(cell.rjust(widths[i]) if i in numeric else cell.ljust(widths[i])
                          for i, cell in enumerate(row))
    rule = "-+-".join("-" * width for width in widths)
    return [line(headers), rule, *(line(row) for row in rows)]


def scan_summary(run, provider):
    tickets = sorted(run["tickets"], key=lambda ticket: ticket["code"])
    title = f'{PROVIDERS[provider].upper()}  /  SCAN RESULTS'
    lines = [title, "=" * len(title), "",
             f'Captured : {clean(run["time"])}',
             f'Seed     : {clean(run["seed"])}',
             f'Tickets  : {len(tickets)} displayed / {run["saved_count"]} saved',
             f'Tried    : {run["tried"]:,.0f}' if run['tried'] is not None else 'Tried    : Not recorded',
             f'Valid    : {run["valid"]:,.0f}' if run['valid'] is not None else 'Valid    : Not recorded',
             "", "BOOKING CODES  (A-Z)", ""]
    legacy = any(ticket['legacy'] is not None for ticket in tickets)
    headers = ['#', 'Code', 'Legs', 'Site odds', 'Calculated odds']
    if legacy:
        headers.append('Legacy total')
    rows = []
    for i, ticket in enumerate(tickets, 1):
        row = [str(i), ticket['code'], str(len(ticket['legs'])), fmt(ticket['site']), fmt(ticket['product'])]
        if legacy:
            row.append(fmt(ticket['legacy']))
        rows.append(row)
    lines.extend(table(headers, rows, numeric=(0, 2, 3, 4, 5)))
    lines.extend(['', 'Site odds       = captured provider-displayed odds.',
                  'Calculated odds = product of the recorded leg prices, not verified payout odds.',
                  '-               = not recorded or unavailable.'])
    if legacy:
        lines.append('Legacy total    = older reported total; source provenance is not explicit.')
    lines.extend(['', 'Historical candidates only. Current availability and settlement are unchecked.',
                  'These are not selected or placed bets.', '', 'COLLECTION SETTINGS', ''])
    settings = []
    for key, title in [('min_total_odds', 'Minimum odds'), ('max_total_odds', 'Maximum odds'),
                       ('min_legs', 'Minimum legs'), ('max_legs', 'Maximum legs'), ('top_n_display', 'Original code-list limit')]:
        value = run['filters'].get(key)
        settings.append([title, 'Not recorded' if value is None else ('No limit' if value == 0 else str(value))])
    lines.extend(table(['Setting', 'Recorded value'], settings))
    notes = [f'{clean(t["code"])}: {"; ".join(t["issues"])}' for t in tickets if t['issues']]
    if run['rejected']:
        notes.append(f'{run["rejected"]} malformed ticket(s) could not be displayed.')
    if notes:
        lines.extend(['', 'SNAPSHOT NOTES', '', *notes])
    lines.extend(['', 'Open index.html in the results folder for full ticket details.',
                  f'Original snapshot: extracts/{run["id"]}', ''])
    return '\n'.join(lines)


def run_time(data, name):
    """Sort by recorded time, never filesystem modification time."""
    value = data.get("saved_at")
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                utc = dt.astimezone(timezone.utc)
                return utc.isoformat(), utc.strftime("%d %b %Y · %H:%M UTC"), "recorded"
        except ValueError:
            pass
    match = re.search(r"_(\d{8}-\d{6})(?:-|\.)", name)
    if match:
        try:
            dt = datetime.strptime(match[1], "%Y%m%d-%H%M%S")
            return dt.isoformat(), dt.strftime("%d %b %Y · %H:%M") + " (filename; timezone unknown)", "filename"
        except ValueError:
            pass
    return "", "Capture time unknown", "unknown"


def _kickoff_dt(value):
    """Parse an ISO kickoff into an aware UTC datetime, or None if unusable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ticket_status(legs, now):
    """A coupon is expired once its earliest known kickoff is in the past;
    unknown when no leg carries a kickoff (cannot prove it is stale)."""
    kicks = [dt for dt in (_kickoff_dt(l.get("kickoff")) for l in legs) if dt]
    if not kicks:
        return "unknown", ""
    earliest = min(kicks)
    return ("expired" if earliest <= now else "live"), earliest.isoformat()


def booking_time(stamp_ms, name):
    """Prefer a recorded epoch-ms timestamp; fall back to the filename stamp."""
    if isinstance(stamp_ms, (int, float)) and stamp_ms > 0:
        dt = datetime.fromtimestamp(stamp_ms / 1000, tz=timezone.utc)
        return dt.isoformat(), dt.strftime("%d %b %Y · %H:%M UTC")
    match = re.search(r"_(\d{8}-\d{6})", name)
    if match:
        try:
            dt = datetime.strptime(match[1], "%Y%m%d-%H%M%S")
            return dt.isoformat(), dt.strftime("%d %b %Y · %H:%M") + " (filename; timezone unknown)"
        except ValueError:
            pass
    return "", "Booking time unknown"


def load_bookings(results: Path, provider: str):
    """Live view of every code saved under booked/. Deleting a code from those
    files removes it here on the next refresh; nothing is cached elsewhere."""
    booked_dir = results / "booked"
    if not booked_dir.is_dir():
        return []
    records = {}

    def remember(code, stamp_ms, name):
        code = label(code)
        if not code:
            return
        sort_time, display = booking_time(stamp_ms, name)
        prev = records.get(code)
        if prev is None or sort_time > prev["sort_time"]:
            records[code] = {"code": code, "sort_time": sort_time,
                             "when": display, "batch": name, "provider": provider}

    # JSON batches are richer (carry a real timestamp), so read them first.
    for source in sorted(booked_dir.glob("*.json")):
        try:
            data = read_json(source)
        except (ValueError, OSError):
            continue
        if not isinstance(data, list):
            continue
        for raw in data:
            if isinstance(raw, dict):
                remember(raw.get("code"), raw.get("ts"), source.name)
    # Plain code lists: only fill in codes a JSON batch did not already describe.
    for source in sorted(booked_dir.glob("*.txt")):
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            code = label(line.strip())
            if code and code not in records:
                remember(code, None, source.name)
    rows = list(records.values())
    rows.sort(key=lambda r: (r["sort_time"], r["code"]), reverse=True)
    return rows


def archive_stale(results: Path, provider: str):
    """Move extracts whose every coupon has already kicked off into
    archive/extracts/, so dead scans stop appearing in the report. Files with
    even one live coupon are left in place (the report hides only the dead legs)."""
    results = results.resolve()
    extracts = results / "extracts"
    if not extracts.is_dir():
        return []
    now = datetime.now(timezone.utc)
    destination = results / "archive" / "extracts"
    moved = []
    for source in sorted(extracts.glob("*.json")):
        try:
            data = read_json(source)
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("qualifying"), list):
            continue
        if data.get("provider") not in (None, "", provider):
            continue
        rows = [row for row in data["qualifying"] if isinstance(row, dict)]
        if not rows:
            continue
        any_live = False
        for row in rows:
            legs = row.get("selections") if isinstance(row.get("selections"), list) else []
            status, _ = ticket_status([l for l in legs if isinstance(l, dict)], now)
            if status != "expired":
                any_live = True
                break
        if any_live:
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / source.name
        if target.exists():
            target = destination / f"{source.stem}-{int(now.timestamp())}{source.suffix}"
        source.replace(target)
        moved.append(source.name)
    return moved


def build_shortlist(results: Path, provider: str, min_odds: float = 2000.0, max_exposure: int = 1, max_odds: float = None):
    """The actual deliverable: from live scanned coupons, keep 2000+ odds and
    de-correlate at K=max_exposure so no game repeats across the picks. Rebuilt
    live from the current extracts, so it always reflects fresh, unexpired data."""
    extracts = results / "extracts"
    if not extracts.is_dir():
        return None
    paths = sorted(extracts.glob("*.json"))
    if not paths:
        return None
    # Dedup by code, latest observation wins. We deliberately do NOT use load_inputs
    # here: it treats the same code seen in two extracts (e.g. near-neighbour seeds
    # rediscovering it with a different observed_at) as a "conflicting observation"
    # and discards it — which would collapse a healthy multi-seed pool to a handful.
    # For harvesting we just want unique, unexpired, well-formed codes.
    by_id = {}
    for path in paths:   # sorted ascending, so a later file overwrites with fresher data
        try:
            data = read_json(path)
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("qualifying"), list):
            continue
        for raw in data["qualifying"]:
            ticket, issues = normalize_ticket(raw, data.get("provider") or provider)
            if ticket is None or issues:   # issues includes expired + malformed coupons
                continue
            if ticket.get("provider") != provider:
                continue
            by_id[ticket["id"]] = ticket
    tickets = list(by_id.values())
    def in_band(t):
        o = t["odds"].get("parsed_leg_product") or 0
        return o >= min_odds and (max_odds is None or o <= max_odds)
    pool = [t for t in tickets if t.get("provider") == provider and in_band(t)]
    if not pool:
        return {"min_odds": min_odds, "max_odds": max_odds, "max_exposure": max_exposure,
                "pool_size": 0, "selected_count": 0, "odds_range": None, "codes": []}
    result = select_subset(pool, max_exposure)
    legs_by_id = {t["id"]: t["legs"] for t in pool}
    codes = []
    for row in result["selected"]:
        legs = [{"event": leg.get("event"), "league": leg.get("league"),
                 "market": leg.get("market"), "pick": leg.get("pick"),
                 "odds": leg.get("odds")} for leg in legs_by_id.get(row["id"], [])]
        codes.append({"code": row["code"], "num_legs": row["num_legs"],
                      "odds": row["parsed_leg_product"],
                      "shared_leg_count": row["shared_leg_count"], "legs": legs})
    codes.sort(key=lambda c: (c["odds"] is None, c["odds"] or 0))
    return {"min_odds": min_odds, "max_odds": max_odds, "max_exposure": max_exposure,
            "pool_size": result["pool_size"], "selected_count": result["selected_count"],
            "realized_max_exposure": result["realized_max_exposure"],
            "odds_range": result["odds_range"], "codes": codes}


def load_catalog(results: Path, provider: str):
    runs, errors = [], []
    for source in sorted((results / "extracts").glob("*.json")):
        try:
            data = read_json(source)
            if not isinstance(data, dict) or not isinstance(data.get("qualifying"), list):
                raise ValueError("Expected an extract with a qualifying list")
            if data.get("provider") not in (None, "", provider):
                raise ValueError("Provider does not match this results folder")
            stamp, display, time_source = run_time(data, source.name)
            tickets, rejected = [], 0
            for raw in data["qualifying"]:
                if not isinstance(raw, dict) or raw.get("provider") not in (None, "", provider):
                    rejected += 1
                    continue
                if not label(raw.get("code")):
                    rejected += 1
                    continue
                _, issues = normalize_ticket(raw, provider)
                odds = odds_contract(raw, provider)
                legs = []
                for leg in raw.get("selections", []) if isinstance(raw.get("selections"), list) else []:
                    if isinstance(leg, dict):
                        legs.append({k: number(leg.get(k)) if k == "odds" else label(leg.get(k))
                                     for k in ("event", "league", "market", "pick", "odds", "kickoff")})
                tickets.append({
                    "code": label(raw["code"]), "legs": legs,
                    "site": odds["site_displayed_odds"] if odds["site_odds_source"] == "explicit_site_displayed_odds" else None,
                    "legacy": odds["site_displayed_odds"] if odds["site_odds_source"].startswith("legacy") else None,
                    "product": odds["parsed_leg_product"], "type": odds["ticket_structure"],
                    "observed": label(raw.get("observed_at")) or "Not recorded",
                    "issues": sorted(set(issues)),
                })
            now = datetime.now(timezone.utc)
            live, expired = [], 0
            for ticket in tickets:
                status, _ = ticket_status(ticket["legs"], now)
                if status == "expired":
                    expired += 1
                else:
                    live.append(ticket)
            if tickets and not live:
                # Every coupon here has already kicked off: hide the whole scan.
                continue
            tickets = live
            runs.append({"id": source.name, "seed": label(data.get("seed")) or "Unknown",
                         "time": display, "sort_time": stamp, "time_source": time_source,
                         "tried": number(data.get("tried")), "valid": number(data.get("found_ok")),
                         "source": "extracts/" + quote(source.name), "tickets": tickets,
                         "rejected": rejected, "saved_count": len(data["qualifying"]),
                         "expired": expired,
                         "filters": {key: data.get(key) for key in
                                     ("min_total_odds", "max_total_odds", "min_legs", "max_legs", "top_n_display")}})
        except (ValueError, OSError) as exc:
            errors.append({"file": source.name, "message": str(exc)})
    runs.sort(key=lambda run: (run["sort_time"], run["id"]), reverse=True)
    return {"provider": provider, "name": PROVIDERS[provider], "runs": runs, "errors": errors,
            "bookings": load_bookings(results, provider),
            "shortlist": build_shortlist(results, provider, min_odds=LONGSHOT_MIN_ODDS,
                                         max_odds=LONGSHOT_MAX_ODDS),
            "generated": datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")}


def atomic_write(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".report-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def render(results: Path, provider: str):
    results = results.resolve()
    if not results.is_dir():
        raise ValueError(f"Results folder does not exist: {results}")
    catalog = load_catalog(results, provider)
    # Escaping '<' prevents an API-supplied </script> from escaping the JSON element.
    payload = json.dumps(catalog, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")
    template = Path(__file__).with_name("reporting.html").read_text(encoding="utf-8")
    # The catalog payload is substituted last, so scanned data can never be
    # re-scanned for a placeholder of its own.
    page = inline_copy_js(template.replace("__TITLE__", PROVIDERS[provider])).replace("__CATALOG__", payload)
    atomic_write(results / "index.html", page)
    summaries = results / "summaries"
    summaries.mkdir(exist_ok=True)
    lines = [f"{PROVIDERS[provider].upper()} RESULTS", "=" * 32, "",
             "OPEN IN YOUR IDE : latest.txt", "OPEN IN A BROWSER: index.html", "",
             "SAVED SCANS (newest listed first)", ""]
    rows = []
    references = []
    for i, run in enumerate(catalog["runs"], 1):
        name = Path(run["id"]).stem + ".txt"
        atomic_write(summaries / name, scan_summary(run, provider))
        rows.append([str(i), run['time'], run['seed'], str(run['saved_count'])])
        references.append(f'{i}. summaries/{name}')
    lines.extend(table(['#', 'Captured', 'Seed', 'Tickets'], rows, numeric=(0, 3)))
    lines.extend(['', 'SCAN TABLE FILES', '', *references])
    latest = scan_summary(catalog['runs'][0], provider) if catalog['runs'] else 'No readable scans found in extracts/.\n'
    atomic_write(results / 'latest.txt', latest)
    if not catalog["runs"]:
        lines.extend(["", "No readable scans found in extracts/."])
    if catalog["errors"]:
        lines.extend(["", "FILES NEEDING ATTENTION", ""])
        lines.extend(clean(error['file']) + ': ' + clean(error['message']) for error in catalog['errors'])
    lines.extend(["", "SUPPORTING FILES", "",
                  "extracts/  Full ticket snapshots used to build these reports.",
                  "codes/     Original scan exports, retained unchanged.",
                  "raw/       Original API evidence for investigations."])
    for folder, description in [("booked", "Saved seed booking batches"), ("won", "Manually saved coupon evidence; not verified settlement reporting")]:
        if (results / folder).is_dir():
            lines.append(f"{folder}/  {description}.")
    lines.extend(["", "REFRESH", "", "New scanner saves refresh both report formats automatically.",
                  "To rebuild manually, run from Sport/ticket-analysis/:", "",
                  f"python3 -m portfolio.reporting --provider {provider} --results ../{provider}/results",
                  "", "Generated reports may be replaced on refresh.",
                  "Original scans, code exports, and raw responses are not modified.", ""])
    atomic_write(results / "START_HERE.txt", "\n".join(lines))
    return results / "index.html"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--archive-stale", action="store_true",
                        help="move fully-expired scans into archive/ before building")
    args = parser.parse_args()
    try:
        if args.archive_stale:
            moved = archive_stale(args.results, args.provider)
            if moved:
                print(f"Archived {len(moved)} fully-expired scan(s): {', '.join(moved)}")
        print(render(args.results, args.provider))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Cannot build report: {exc}\n")


if __name__ == "__main__":
    main()
