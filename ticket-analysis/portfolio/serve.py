"""Serve a provider's results report live.

Unlike opening index.html over file://, this rebuilds the report from disk on
every page load and auto-reloads any open tab when the booked/ or extracts/
files change. The source of truth stays the folder: delete a code there and it
disappears from the page within a few seconds, no manual refresh.

Usage (run from Sport/ticket-analysis/):
  python3 -m portfolio.serve --provider sportybet          # serves + opens browser
  python3 -m portfolio.serve --provider bet9ja --port 8010
  python3 -m portfolio.serve --provider sportybet --no-open --results ../sportybet/results
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import socketserver
import threading
import webbrowser
from pathlib import Path

from .reporting import PROVIDERS, archive_stale, render

# Injected only into the served page, so file:// stays clean and offline.
POLL_SNIPPET = (
    "<script>(function(){let last=null;"
    "async function check(){try{const r=await fetch('__version',{cache:'no-store'});"
    "const v=await r.text();if(last!==null&&v!==last){location.reload();return;}last=v;}"
    "catch(e){}}setInterval(check,3000);check();})();</script>"
)


def version_token(results: Path) -> str:
    """A cheap fingerprint of the source files, so the tab reloads on any change."""
    parts = []
    for sub in ("booked", "extracts"):
        directory = results / sub
        if directory.is_dir():
            for item in sorted(directory.iterdir()):
                try:
                    stat = item.stat()
                    parts.append(f"{item.name}:{stat.st_mtime_ns}:{stat.st_size}")
                except OSError:
                    continue
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def make_handler(results: Path, provider: str):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(results), **kwargs)

        def log_message(self, *args):  # keep the console quiet
            pass

        def _send(self, body: bytes, content_type: str):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/__version":
                self._send(version_token(results).encode("utf-8"), "text/plain; charset=utf-8")
                return
            if path in ("/", "/index.html"):
                try:
                    render(results, provider)  # rebuild live from disk
                    html = (results / "index.html").read_text(encoding="utf-8")
                    if "</body>" in html:
                        html = html.replace("</body>", POLL_SNIPPET + "</body>", 1)
                    else:
                        html += POLL_SNIPPET
                    self._send(html.encode("utf-8"), "text/html; charset=utf-8")
                    return
                except (OSError, ValueError) as exc:
                    self.send_error(500, f"Report build failed: {exc}")
                    return
            super().do_GET()

        do_HEAD = do_GET

    return Handler


def default_results(provider: str) -> Path:
    return Path(__file__).resolve().parents[2] / provider / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    parser.add_argument("--results", type=Path, default=None,
                        help="results folder (default: ../<provider>/results)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args()

    results = (args.results or default_results(args.provider)).resolve()
    if not results.is_dir():
        parser.exit(1, f"Results folder does not exist: {results}\n")

    swept = archive_stale(results, args.provider)
    if swept:
        print(f"Archived {len(swept)} fully-expired scan(s) into archive/extracts/.")

    handler = make_handler(results, args.provider)
    try:
        server = socketserver.ThreadingTCPServer((args.host, args.port), handler)
    except OSError as exc:
        parser.exit(1, f"Cannot bind {args.host}:{args.port} ({exc}). Try --port <other>.\n")
    server.daemon_threads = True

    url = f"http://{args.host}:{args.port}/"
    print(f"Live report for {PROVIDERS[args.provider]}  ->  {url}")
    print(f"Serving {results}")
    print("The open tab reloads automatically when booked/ or extracts/ change.")
    print("Press Ctrl+C to stop.")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
