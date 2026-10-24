"""F9 step 5 piece 2: persistent fixture-response cache + shared quota control.

Pure filesystem tests against a throwaway TemporaryDirectory; no network, no
run directories needed.
"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from portfolio.results_tracking.cache import (
    CacheEntry, CacheError, CacheKey, FixtureCache, PENDING_REFRESH_INTERVAL,
    QuotaExceededError, QuotaTracker, TERMINAL_REFRESH_INTERVAL,
)


def _key(**overrides):
    defaults = dict(source="api_football", endpoint="fixtures_by_date",
                     params={"date": "2026-10-23"}, parser_version="v1")
    defaults.update(overrides)
    return CacheKey.build(**defaults)


class CacheKeyTests(unittest.TestCase):
    def test_build_sorts_params(self):
        key = CacheKey.build("api_football", "fixtures_by_date",
                              {"b": "2", "a": "1"}, parser_version="v1")
        self.assertEqual(key.params, (("a", "1"), ("b", "2")))

    def test_digest_stable_regardless_of_dict_insertion_order(self):
        key_a = CacheKey.build("api_football", "fixtures_by_date",
                                {"a": "1", "b": "2"}, parser_version="v1")
        key_b = CacheKey.build("api_football", "fixtures_by_date",
                                {"b": "2", "a": "1"}, parser_version="v1")
        self.assertEqual(key_a.digest(), key_b.digest())

    def test_digest_changes_with_parser_version(self):
        key_a = _key(parser_version="v1")
        key_b = _key(parser_version="v2")
        self.assertNotEqual(key_a.digest(), key_b.digest())

    def test_digest_changes_with_period(self):
        key_a = _key()
        key_b = CacheKey.build("api_football", "fixtures_by_date",
                                {"date": "2026-10-23"}, period="first_half",
                                parser_version="v1")
        self.assertNotEqual(key_a.digest(), key_b.digest())

    def test_empty_source_rejected(self):
        with self.assertRaises(CacheError):
            CacheKey(source="", endpoint="e", params=(), period=None, parser_version="v1")

    def test_unsorted_params_rejected_on_direct_construction(self):
        with self.assertRaises(CacheError):
            CacheKey(source="s", endpoint="e", params=(("b", "2"), ("a", "1")),
                      period=None, parser_version="v1")


class CacheEntryStalenessTests(unittest.TestCase):
    def test_terminal_entry_fresh_within_24_hours(self):
        fetched_at = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        entry = CacheEntry(key=_key(), raw_bytes=b"[]", fetched_at=fetched_at,
                            all_terminal=True, content_sha256="x")
        now = fetched_at + timedelta(hours=23)
        self.assertFalse(entry.is_stale(now=now))

    def test_terminal_entry_stale_after_24_hours(self):
        fetched_at = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        entry = CacheEntry(key=_key(), raw_bytes=b"[]", fetched_at=fetched_at,
                            all_terminal=True, content_sha256="x")
        now = fetched_at + TERMINAL_REFRESH_INTERVAL
        self.assertTrue(entry.is_stale(now=now))

    def test_pending_entry_stale_after_15_minutes(self):
        fetched_at = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        entry = CacheEntry(key=_key(), raw_bytes=b"[]", fetched_at=fetched_at,
                            all_terminal=False, content_sha256="x")
        now = fetched_at + PENDING_REFRESH_INTERVAL
        self.assertTrue(entry.is_stale(now=now))

    def test_pending_entry_fresh_within_15_minutes(self):
        fetched_at = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        entry = CacheEntry(key=_key(), raw_bytes=b"[]", fetched_at=fetched_at,
                            all_terminal=False, content_sha256="x")
        now = fetched_at + timedelta(minutes=10)
        self.assertFalse(entry.is_stale(now=now))

    def test_next_eligible_recheck_matches_interval(self):
        fetched_at = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        entry = CacheEntry(key=_key(), raw_bytes=b"[]", fetched_at=fetched_at,
                            all_terminal=True, content_sha256="x")
        self.assertEqual(entry.next_eligible_recheck(), fetched_at + TERMINAL_REFRESH_INTERVAL)


class FixtureCacheTests(unittest.TestCase):
    def test_miss_returns_none(self):
        with TemporaryDirectory() as tmp:
            cache = FixtureCache(Path(tmp))
            self.assertIsNone(cache.get(_key()))

    def test_put_then_get_round_trips_raw_bytes_and_nulls(self):
        with TemporaryDirectory() as tmp:
            cache = FixtureCache(Path(tmp))
            raw = json.dumps([{"a": 1, "b": None}]).encode("utf-8")
            fetched_at = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)
            cache.put(_key(), raw, all_terminal=True, fetched_at=fetched_at)
            entry = cache.get(_key())
            self.assertEqual(entry.raw_bytes, raw)
            self.assertEqual(json.loads(entry.raw_bytes), [{"a": 1, "b": None}])
            self.assertEqual(entry.fetched_at, fetched_at)
            self.assertTrue(entry.all_terminal)

    def test_different_keys_do_not_collide(self):
        with TemporaryDirectory() as tmp:
            cache = FixtureCache(Path(tmp))
            cache.put(_key(params={"date": "2026-10-23"}), b"[1]", all_terminal=True)
            cache.put(_key(params={"date": "2026-10-24"}), b"[2]", all_terminal=True)
            self.assertEqual(cache.get(_key(params={"date": "2026-10-23"})).raw_bytes, b"[1]")
            self.assertEqual(cache.get(_key(params={"date": "2026-10-24"})).raw_bytes, b"[2]")

    def test_put_overwrites_previous_entry_for_same_key(self):
        with TemporaryDirectory() as tmp:
            cache = FixtureCache(Path(tmp))
            cache.put(_key(), b"[1]", all_terminal=False)
            cache.put(_key(), b"[2]", all_terminal=True)
            entry = cache.get(_key())
            self.assertEqual(entry.raw_bytes, b"[2]")
            self.assertTrue(entry.all_terminal)

    def test_non_utc_fetched_at_rejected(self):
        with TemporaryDirectory() as tmp:
            cache = FixtureCache(Path(tmp))
            bad = datetime(2026, 10, 24, 10, 0, tzinfo=timezone(timedelta(hours=2)))
            with self.assertRaises(CacheError):
                cache.put(_key(), b"[]", all_terminal=True, fetched_at=bad)

    def test_corrupted_raw_bytes_detected_via_hash_mismatch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = FixtureCache(root)
            cache.put(_key(), b"[1]", all_terminal=True)
            key = _key()
            entry_dir = root / key.source / key.digest()
            (entry_dir / "raw.bin").write_bytes(b"[tampered]")
            with self.assertRaises(CacheError):
                cache.get(key)


class QuotaTrackerTests(unittest.TestCase):
    def test_fresh_tracker_has_full_budget(self):
        with TemporaryDirectory() as tmp:
            tracker = QuotaTracker(Path(tmp), "api_football", daily_limit=100)
            self.assertEqual(tracker.remaining(), 100)
            self.assertEqual(tracker.used(), 0)

    def test_record_request_increments_and_returns_new_count(self):
        with TemporaryDirectory() as tmp:
            tracker = QuotaTracker(Path(tmp), "api_football", daily_limit=100)
            self.assertEqual(tracker.record_request(), 1)
            self.assertEqual(tracker.record_request(), 2)
            self.assertEqual(tracker.remaining(), 98)

    def test_exceeding_limit_raises_without_incrementing(self):
        with TemporaryDirectory() as tmp:
            tracker = QuotaTracker(Path(tmp), "api_football", daily_limit=1)
            tracker.record_request()
            with self.assertRaises(QuotaExceededError):
                tracker.record_request()
            self.assertEqual(tracker.used(), 1)

    def test_separate_sources_have_independent_budgets(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            QuotaTracker(root, "api_football", daily_limit=1).record_request()
            other = QuotaTracker(root, "football_data", daily_limit=1)
            self.assertEqual(other.used(), 0)
            other.record_request()  # must not raise

    def test_separate_utc_days_have_independent_buckets(self):
        with TemporaryDirectory() as tmp:
            tracker = QuotaTracker(Path(tmp), "api_football", daily_limit=1)
            day1 = datetime(2026, 10, 23, 23, 0, tzinfo=timezone.utc)
            day2 = datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)
            tracker.record_request(now=day1)
            tracker.record_request(now=day2)  # different day, must not raise
            self.assertEqual(tracker.used(now=day1), 1)
            self.assertEqual(tracker.used(now=day2), 1)

    def test_non_positive_daily_limit_rejected(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(CacheError):
                QuotaTracker(Path(tmp), "api_football", daily_limit=0)


if __name__ == "__main__":
    unittest.main()
