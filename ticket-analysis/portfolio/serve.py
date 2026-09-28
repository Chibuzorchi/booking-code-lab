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
import hashlib
import ipaddress
import json
import socket
from urllib.parse import parse_qs, urlsplit
import http.server
import socketserver
import threading
import traceback
import webbrowser
from pathlib import Path

from .api import APIError, dispatch, validate_method, allowed_methods
from .decorrelation import ExtractError
from .registry import RunRegistry, RunNotFoundError, RunStateError
from .worker import JobBusyError
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


def is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_handler(results: Path, provider: str, registry=None):
    registry = registry if registry is not None else RunRegistry()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(results), **kwargs)

        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def log_message(self, *args):  # keep the console quiet
            pass

        def _send(self, body: bytes, content_type: str, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if status == 405:
                self.send_header('Allow', ', '.join(allowed_methods(urlsplit(self.path).path)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, payload):
            body = json.dumps(payload, allow_nan=False).encode('utf-8')
            self._send(body, 'application/json; charset=utf-8', status)

        def _local_access(self):
            # Peer alone is insufficient: reject DNS rebinding and cross-origin
            # browser requests even when they reach the loopback socket.
            if not is_loopback(self.client_address[0]):
                raise APIError(403, 'local_only', 'Only loopback clients are allowed')
            hosts = self.headers.get_all('Host', [])
            if len(hosts) != 1:
                raise APIError(403, 'invalid_host', 'One local Host header is required')
            try:
                host = urlsplit('http://' + hosts[0])
                port = host.port or 80
            except ValueError as exc:
                raise APIError(403, 'invalid_host', 'Invalid local Host') from exc
            if (host.hostname != 'localhost' and not is_loopback(host.hostname or '')) or host.username or host.password or host.path or host.query or host.fragment:
                raise APIError(403, 'invalid_host', 'Host must name the local server')
            if port != self.server.server_address[1]:
                raise APIError(403, 'invalid_host', 'Host port must match the server')
            origins = self.headers.get_all('Origin', [])
            if origins:
                try:
                    origin = urlsplit(origins[0])
                    valid = (len(origins) == 1 and origin.scheme == 'http'
                             and (origin.hostname, origin.port or 80) == (host.hostname, port)
                             and not (origin.username or origin.password or origin.path
                                      or origin.query or origin.fragment))
                except ValueError:
                    valid = False
                if not valid:
                    raise APIError(403, 'cross_origin', 'Cross-origin requests are refused')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise APIError(403, 'cross_origin', 'Cross-site requests are refused')

        def _body(self):
            if self.headers.get('Transfer-Encoding'):
                raise APIError(400, 'invalid_body', 'Transfer-Encoding is unsupported')
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                raise APIError(400, 'invalid_body', 'One Content-Length is required')
            size = int(lengths[0])
            if size > 65536:
                raise APIError(413, 'body_too_large', 'JSON body exceeds 64 KiB')
            if self.headers.get_content_type() != 'application/json':
                raise APIError(415, 'content_type', 'Use application/json')
            def constant(value):
                raise ValueError('Non-finite JSON numbers are not allowed')
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError('Duplicate JSON fields are not allowed')
                    result[key] = value
                return result
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError('Incomplete JSON body')
            body = json.loads(raw, parse_constant=constant, object_pairs_hook=pairs)
            if not isinstance(body, dict):
                raise ValueError('request body must be a JSON object')
            return body

        def _api(self):
            try:
                self._local_access()
                url = urlsplit(self.path)
                validate_method(self.command, url.path)
                query = parse_qs(url.query, keep_blank_values=True)
                if any(len(values) != 1 for values in query.values()):
                    raise ValueError('Duplicate query parameters are not allowed')
                body = self._body() if self.command == 'POST' else {}
                status, payload = dispatch(registry, self.command, url.path, body,
                                           {key: values[0] for key, values in query.items()})
                self._json(status, payload)
            except APIError as exc:
                self._json(exc.status, {'error': {'code': exc.code, 'message': exc.message}})
            except RunNotFoundError:
                self._json(404, {'error': {'code': 'run_not_found', 'message': 'Run not found'}})
            except (RunStateError, JobBusyError) as exc:
                self._json(409, {'error': {'code': 'run_conflict', 'message': str(exc)}})
            except ExtractError as exc:
                self._json(422, {'error': {'code': 'analysis_failed', 'message': str(exc)}})
            except (ValueError, OverflowError, RecursionError) as exc:
                self._json(400, {'error': {'code': 'invalid_request', 'message': str(exc)}})
            except Exception:
                # The client answer stays generic, but an unexpected failure is a
                # bug: print the cause here, since log_message is silenced and
                # nothing else would record it.
                traceback.print_exc()
                self._json(500, {'error': {'code': 'server_error', 'message': 'Run operation failed'}})

        def send_error(self, code, message=None, explain=None):
            path = getattr(self, 'path', '').split('?', 1)[0]
            if path == '/api' or path.startswith('/api/'):
                self._json(code, {'error': {'code': 'http_error',
                                          'message': self.responses.get(code, ('Request failed',))[0]}})
            else:
                super().send_error(code, message, explain)

        do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _api

        def do_POST(self):
            self._api()

        def do_GET(self):
            try:
                self._local_access()
            except APIError as exc:
                self._json(exc.status, {'error': {'code': exc.code, 'message': exc.message}})
                return
            path = self.path.split("?", 1)[0]
            if path == '/api' or path.startswith('/api/'):
                self._api()
                return
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

    # Resolve localhost to a fixed literal; never bind a wildcard or external IP.
    if args.host == 'localhost':
        args.host = '127.0.0.1'
    if not is_loopback(args.host):
        parser.error('--host must be a loopback IP address or localhost')

    results = (args.results or default_results(args.provider)).resolve()
    if not results.is_dir():
        parser.exit(1, f"Results folder does not exist: {results}\n")

    swept = archive_stale(results, args.provider)
    if swept:
        print(f"Archived {len(swept)} fully-expired scan(s) into archive/extracts/.")

    handler = make_handler(results, args.provider)
    try:
        class Server(socketserver.ThreadingTCPServer):
            address_family = socket.AF_INET6 if ':' in args.host else socket.AF_INET
            allow_reuse_address = True
        server = Server((args.host, args.port), handler)
    except OSError as exc:
        parser.exit(1, f"Cannot bind {args.host}:{args.port} ({exc}). Try --port <other>.\n")
    server.daemon_threads = True

    url_host = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{url_host}:{args.port}/"
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
