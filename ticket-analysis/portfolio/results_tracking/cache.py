"""Persistent responses and shared source quota control.

F9_RESULTS_PLAN.md section 8 scopes this module to two things, deliberately
kept separate from sources/ (which owns network calls) and jobs.py (which
will own fetch/refresh orchestration, not yet built):

- FixtureCache: a durable store of raw endpoint responses, keyed by source,
  endpoint, parameters, period and parser version, preserving raw bytes and
  nulls exactly as received. This module never calls a network client and
  never decides *whether* to refetch -- it only answers "what do we have"
  and "is what we have still fresh enough", per the plan's refresh policy:
  pending/no-result data is eligible for recheck after 15 minutes, terminal
  data after 24 hours. A cache miss means "not fetched, or stale" -- never
  "fetched, zero results" -- so a failed fetch (auth/rate limit) must never
  reach put(); callers only cache a response they actually received and
  parsed. "Explicit refresh differs from replay" (section 8) falls out of
  this shape for free: a future replay path calls get() and nothing else
  (no network, no staleness check gating a fetch); an explicit refresh calls
  get()/is_stale() to decide, then fetches and put()s a new entry.

- QuotaTracker: a shared, concurrency-safe daily request budget per source,
  so multiple runs/processes never jointly exceed a free-tier limit (e.g.
  API-Football's 100/day). Updates are serialized with the same flock
  convention registry.py uses for status.json, and the check-and-increment
  happens under that lock so concurrent callers cannot race past the limit.

Manual evidence/overrides (provenance, author/time, revision instead of
silent replacement) are not implemented here -- section 8 scopes those to
the report-revision workflow, not to the raw-response cache.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PENDING_REFRESH_INTERVAL = timedelta(minutes=15)
TERMINAL_REFRESH_INTERVAL = timedelta(hours=24)


class CacheError(RuntimeError):
    """The cache store is missing, corrupt, or was given inconsistent input."""


class QuotaExceededError(RuntimeError):
    """Recording this request would exceed the source's daily budget."""


@contextmanager
def _flock_path(path: Path):
    """Exclusive flock on path, serializing access across processes.

    Mirrors registry.py's _flock_path -- same convention, not reinvented.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def _write_atomic(path: Path, raw_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(raw_bytes)
    os.replace(tmp, path)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CacheKey:
    """Identifies one cached request, per section 8's key shape: source,
    competition/season/fixture, endpoint parameters, period and parser
    version. `params` carries whichever of those apply to a given endpoint
    (e.g. {"date": "2026-10-23"} for a fixtures-by-date call); this module
    does not assume any particular endpoint shape."""
    source: str
    endpoint: str
    params: tuple[tuple[str, str], ...]
    period: str | None
    parser_version: str

    def __post_init__(self):
        if not self.source:
            raise CacheError("CacheKey.source must be nonempty")
        if not self.endpoint:
            raise CacheError("CacheKey.endpoint must be nonempty")
        if not self.parser_version:
            raise CacheError("CacheKey.parser_version must be nonempty")
        if tuple(sorted(self.params)) != self.params:
            raise CacheError("CacheKey.params must be sorted for a stable digest")

    @classmethod
    def build(cls, source: str, endpoint: str, params: dict, *,
              period: str | None = None, parser_version: str) -> "CacheKey":
        return cls(
            source=source,
            endpoint=endpoint,
            params=tuple(sorted((str(k), str(v)) for k, v in params.items())),
            period=period,
            parser_version=parser_version,
        )

    def digest(self) -> str:
        payload = json.dumps(
            {
                "source": self.source,
                "endpoint": self.endpoint,
                "params": list(self.params),
                "period": self.period,
                "parser_version": self.parser_version,
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CacheEntry:
    """A stored response. `raw_bytes` is exactly what was received (preserved
    verbatim, including nulls); `all_terminal` records whether every fixture
    observed in this response was already in a finished/cancelled/abandoned/
    awarded state at fetch time, which governs the refresh-eligibility
    window (24h) vs. the shorter pending window (15min)."""
    key: CacheKey
    raw_bytes: bytes
    fetched_at: datetime
    all_terminal: bool
    content_sha256: str

    def age(self, *, now: datetime | None = None) -> timedelta:
        return (now or _utcnow()) - self.fetched_at

    def refresh_interval(self) -> timedelta:
        return TERMINAL_REFRESH_INTERVAL if self.all_terminal else PENDING_REFRESH_INTERVAL

    def next_eligible_recheck(self) -> datetime:
        return self.fetched_at + self.refresh_interval()

    def is_stale(self, *, now: datetime | None = None) -> bool:
        return (now or _utcnow()) >= self.next_eligible_recheck()


class FixtureCache:
    """Durable store of CacheEntry objects under `root`, one subdirectory per
    entry (raw bytes and metadata kept as separate files, mirroring the
    existing raw/-vs-extracts/ split elsewhere in this codebase)."""

    def __init__(self, root: Path):
        self._root = Path(root)

    def _entry_dir(self, key: CacheKey) -> Path:
        return self._root / key.source / key.digest()

    def get(self, key: CacheKey) -> CacheEntry | None:
        entry_dir = self._entry_dir(key)
        meta_path = entry_dir / "meta.json"
        raw_path = entry_dir / "raw.bin"
        try:
            meta_raw = meta_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            raw_bytes = raw_path.read_bytes()
        except FileNotFoundError:
            raise CacheError(f"cache entry {entry_dir} has metadata but no raw bytes")
        meta = json.loads(meta_raw)
        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha256 != meta["content_sha256"]:
            raise CacheError(f"cache entry {entry_dir} raw bytes do not match recorded hash")
        expected_key = json.loads(json.dumps({
            "source": key.source, "endpoint": key.endpoint,
            "params": list(key.params), "period": key.period,
            "parser_version": key.parser_version,
        }))
        if meta["key"] != expected_key:
            raise CacheError(f"cache entry {entry_dir} metadata key does not match lookup key")
        return CacheEntry(
            key=key,
            raw_bytes=raw_bytes,
            fetched_at=datetime.fromisoformat(meta["fetched_at"]),
            all_terminal=meta["all_terminal"],
            content_sha256=actual_sha256,
        )

    def put(self, key: CacheKey, raw_bytes: bytes, *, all_terminal: bool,
            fetched_at: datetime | None = None) -> CacheEntry:
        fetched_at = fetched_at or _utcnow()
        if fetched_at.utcoffset() != timedelta(0):
            raise CacheError("fetched_at must be UTC (offset zero)")
        entry_dir = self._entry_dir(key)
        content_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        meta = {
            "key": {
                "source": key.source, "endpoint": key.endpoint,
                "params": list(key.params), "period": key.period,
                "parser_version": key.parser_version,
            },
            "fetched_at": fetched_at.isoformat(),
            "all_terminal": all_terminal,
            "content_sha256": content_sha256,
        }
        with _flock_path(entry_dir / "write.lock"):
            _write_atomic(entry_dir / "raw.bin", raw_bytes)
            _write_atomic(
                entry_dir / "meta.json",
                json.dumps(meta, indent=2, sort_keys=True).encode("utf-8"),
            )
        return CacheEntry(
            key=key, raw_bytes=raw_bytes, fetched_at=fetched_at,
            all_terminal=all_terminal, content_sha256=content_sha256,
        )


class QuotaTracker:
    """Shared daily request budget for one source, safe under concurrent
    processes. Bucketed by UTC calendar date; check-and-increment happens
    under a single flock so concurrent callers cannot race past the limit."""

    def __init__(self, root: Path, source: str, daily_limit: int):
        if daily_limit <= 0:
            raise CacheError("daily_limit must be positive")
        self._root = Path(root)
        self._source = source
        self._daily_limit = daily_limit

    def _bucket_path(self, *, now: datetime) -> Path:
        date_str = now.date().isoformat()
        return self._root / self._source / f"{date_str}.json"

    def _lock_path(self) -> Path:
        return self._root / self._source / "quota.lock"

    def _read_count(self, bucket_path: Path) -> int:
        try:
            return json.loads(bucket_path.read_text(encoding="utf-8"))["count"]
        except FileNotFoundError:
            return 0

    def used(self, *, now: datetime | None = None) -> int:
        now = now or _utcnow()
        with _flock_path(self._lock_path()):
            return self._read_count(self._bucket_path(now=now))

    def remaining(self, *, now: datetime | None = None) -> int:
        return self._daily_limit - self.used(now=now)

    def record_request(self, *, now: datetime | None = None) -> int:
        """Atomically increments today's count and returns the new total.
        Raises QuotaExceededError (without incrementing) if the budget is
        already exhausted."""
        now = now or _utcnow()
        bucket_path = self._bucket_path(now=now)
        with _flock_path(self._lock_path()):
            count = self._read_count(bucket_path)
            if count >= self._daily_limit:
                raise QuotaExceededError(
                    f"{self._source} daily quota of {self._daily_limit} already used")
            count += 1
            _write_atomic(bucket_path, json.dumps({"count": count}).encode("utf-8"))
            return count
