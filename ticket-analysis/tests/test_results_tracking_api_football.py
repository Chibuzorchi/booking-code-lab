"""F9: API-Football client and score extraction. No real network call is
made anywhere in this file -- requests.Session.get is mocked throughout, so
no test consumes any of the account's daily quota."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from portfolio.results_tracking.fixtures import FixtureState
from portfolio.results_tracking.sources.api_football import (
    ApiFootballAuthError, ApiFootballClient, ApiFootballError, ApiFootballQuotaError,
    ApiFootballTransportError, build_fixture, extract_regulation_score,
    fetch_in_scope_fixtures_for_date, filter_in_scope, map_fixture_state,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _real_ipswich_fixture() -> dict:
    """A real, unmodified API-Football response item (see
    tests/fixtures/README.md for provenance)."""
    return json.loads(
        (_FIXTURES_DIR / "api_football_ipswich_nottingham_forest_fulltime.json").read_text())


def _mock_response(json_body, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    if status_code < 400:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    return resp


class ApiFootballClientTests(unittest.TestCase):
    def test_missing_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ApiFootballAuthError):
                ApiFootballClient()

    def test_explicit_key_used_over_environment(self):
        with patch.dict("os.environ", {"API_FOOTBALL_KEY": "env-key"}):
            client = ApiFootballClient(api_key="explicit-key")
            self.assertEqual(client.session.headers["x-apisports-key"], "explicit-key")

    def test_get_fixtures_by_date_returns_raw_response_list(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response(
            {"response": [{"fixture": {"id": 1}}], "errors": []}))
        result = client.get_fixtures_by_date("2026-10-24")
        self.assertEqual(result, [{"fixture": {"id": 1}}])
        client.session.get.assert_called_once()
        self.assertEqual(client.session.get.call_args.kwargs["params"], {"date": "2026-10-24"})

    def test_league_and_season_params_passed_through(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response({"response": [], "errors": []}))
        client.get_fixtures_by_date("2026-10-24", league=39, season=2026)
        self.assertEqual(client.session.get.call_args.kwargs["params"],
                          {"date": "2026-10-24", "league": 39, "season": 2026})

    def test_api_errors_field_with_no_recognized_signal_is_fatal_not_retryable(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response(
            {"response": [], "errors": ["Invalid API key"]}))
        with self.assertRaises(ApiFootballError):
            client.get_fixtures_by_date("2026-10-24")
        # Not recognized as a specific auth/quota signal (a free-text
        # message, not a named field), so it must not be misclassified as
        # retryable.
        self.assertNotIsInstance(
            self._raised(client, "2026-10-24"), ApiFootballTransportError)

    def _raised(self, client, date):
        try:
            client.get_fixtures_by_date(date)
        except ApiFootballError as exc:
            return exc
        raise AssertionError("expected ApiFootballError")

    def test_api_errors_field_named_token_is_an_auth_error(self):
        # F9_STEP5_REVIEW.md S5-11: auth failures must be distinguished
        # from transport failures so they are never retried.
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response(
            {"response": [], "errors": {"token": "invalid key"}}))
        with self.assertRaises(ApiFootballAuthError):
            client.get_fixtures_by_date("2026-10-24")

    def test_api_errors_field_named_requests_is_a_quota_error(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response(
            {"response": [], "errors": {"requests": "rate limit exceeded"}}))
        with self.assertRaises(ApiFootballQuotaError):
            client.get_fixtures_by_date("2026-10-24")

    def test_connection_failure_is_a_retryable_transport_error(self):
        # F9_STEP5_REVIEW.md S5-11: the request never reached a usable
        # answer, so this is real evidence of nothing -- safe to retry.
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(side_effect=requests.ConnectionError("boom"))
        with self.assertRaises(ApiFootballTransportError):
            client.get_fixtures_by_date("2026-10-24")

    def test_server_error_status_is_a_retryable_transport_error(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response({}, status_code=503))
        with self.assertRaises(ApiFootballTransportError):
            client.get_fixtures_by_date("2026-10-24")

    def test_unauthorized_status_is_an_auth_error(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response({}, status_code=401))
        with self.assertRaises(ApiFootballAuthError):
            client.get_fixtures_by_date("2026-10-24")

    def test_too_many_requests_status_is_a_quota_error(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response({}, status_code=429))
        with self.assertRaises(ApiFootballQuotaError):
            client.get_fixtures_by_date("2026-10-24")

    def test_other_4xx_status_is_a_fatal_not_retryable_error(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response({}, status_code=400))
        with self.assertRaises(ApiFootballError):
            client.get_fixtures_by_date("2026-10-24")
        self.assertNotIsInstance(self._raised(client, "2026-10-24"), ApiFootballTransportError)

    def test_empty_envelope_raises_not_empty_list(self):
        # F9_IMPLEMENTATION_REVIEW.md R11: a missing 'response' field must
        # not look like a successful empty fixture lookup.
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response({}))
        with self.assertRaises(ApiFootballError):
            client.get_fixtures_by_date("2026-10-24")

    def test_response_wrong_type_dict_raises(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response({"response": {}, "errors": []}))
        with self.assertRaises(ApiFootballError):
            client.get_fixtures_by_date("2026-10-24")

    def test_response_null_raises(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response({"response": None, "errors": []}))
        with self.assertRaises(ApiFootballError):
            client.get_fixtures_by_date("2026-10-24")

    def test_top_level_list_envelope_raises(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response([]))
        with self.assertRaises(ApiFootballError):
            client.get_fixtures_by_date("2026-10-24")

    def test_response_item_wrong_type_raises(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response({"response": ["not-a-dict"], "errors": []}))
        with self.assertRaises(ApiFootballError):
            client.get_fixtures_by_date("2026-10-24")


class ExtractRegulationScoreTests(unittest.TestCase):
    def test_finished_match_extracted(self):
        fixture = {
            "fixture": {"status": {"short": "FT"}},
            "score": {"fulltime": {"home": 2, "away": 1}, "halftime": {"home": 1, "away": 0}},
        }
        score = extract_regulation_score(fixture)
        self.assertEqual((score.home, score.away, score.ht_home, score.ht_away), (2, 1, 1, 0))

    def test_not_yet_played_returns_none(self):
        fixture = {"fixture": {"status": {"short": "NS"}}, "score": {}}
        self.assertIsNone(extract_regulation_score(fixture))

    def test_extra_time_status_trusts_fulltime_as_regulation_score(self):
        # Real evidence (24 October 2026, see module docstring): a live AET
        # fixture had fulltime={2,2}, extratime={2,0}, goals={4,2} --
        # fulltime + extratime == goals, proving fulltime is the regulation
        # (pre-extra-time) score. AET is no longer declined.
        fixture = {
            "fixture": {"status": {"short": "AET"}},
            "score": {"fulltime": {"home": 1, "away": 1}, "halftime": {"home": 0, "away": 0}},
        }
        score = extract_regulation_score(fixture)
        self.assertEqual((score.home, score.away, score.ht_home, score.ht_away), (1, 1, 0, 0))

    def test_penalties_status_trusts_fulltime_as_regulation_score(self):
        # Real evidence: a live PEN fixture had fulltime={0,0},
        # extratime={null,null} (no extra time played in that format),
        # penalty={2,3}, goals={0,0} -- fulltime matches the pre-shootout
        # score. PEN is no longer declined.
        fixture = {
            "fixture": {"status": {"short": "PEN"}},
            "score": {"fulltime": {"home": 0, "away": 0}, "halftime": {"home": 0, "away": 0}},
        }
        score = extract_regulation_score(fixture)
        self.assertEqual((score.home, score.away, score.ht_home, score.ht_away), (0, 0, 0, 0))

    def test_finished_with_incomplete_score_raises(self):
        fixture = {"fixture": {"status": {"short": "FT"}}, "score": {"fulltime": {"home": 2, "away": None}}}
        with self.assertRaises(ApiFootballError):
            extract_regulation_score(fixture)

    def test_inconsistent_half_time_raises(self):
        fixture = {
            "fixture": {"status": {"short": "FT"}},
            "score": {"fulltime": {"home": 1, "away": 1}, "halftime": {"home": 2, "away": 0}},
        }
        with self.assertRaises(ApiFootballError):
            extract_regulation_score(fixture)

    def test_null_fixture_container_returns_none_not_attribute_error(self):
        # F9_IMPLEMENTATION_REREVIEW.md RR02: "fixture" present but JSON null
        # means .get() returns None, not {} -- status is unavailable, so this
        # must come back as None (not a trusted finished status), not crash.
        self.assertIsNone(extract_regulation_score({"fixture": None}))

    def test_null_score_container_on_finished_fixture_raises(self):
        # RR02: status is FT (trusted) but "score" is JSON null -- this is a
        # malformed finished-fixture payload and must raise ApiFootballError,
        # not AttributeError.
        fixture = {"fixture": {"status": {"short": "FT"}}, "score": None}
        with self.assertRaises(ApiFootballError):
            extract_regulation_score(fixture)

    def test_non_object_fixture_container_raises(self):
        fixture = {"fixture": "not-an-object", "score": {}}
        with self.assertRaises(ApiFootballError):
            extract_regulation_score(fixture)

    def test_non_object_status_container_raises(self):
        fixture = {"fixture": {"status": "FT"}, "score": {}}
        with self.assertRaises(ApiFootballError):
            extract_regulation_score(fixture)

    def test_non_string_status_short_raises(self):
        # Third re-review pass: status.short must be validated as a string
        # before the `in` membership check, since an unhashable value (a
        # list, here) raises TypeError against a set rather than the
        # documented ApiFootballError.
        fixture = {"fixture": {"status": {"short": []}}, "score": {}}
        with self.assertRaises(ApiFootballError):
            extract_regulation_score(fixture)


class MapFixtureStateTests(unittest.TestCase):
    def test_observed_codes_map_correctly(self):
        cases = {
            "NS": FixtureState.SCHEDULED, "PST": FixtureState.POSTPONED,
            "CANC": FixtureState.CANCELLED, "FT": FixtureState.FINISHED_REGULATION,
            "AET": FixtureState.FINISHED_EXTRA_TIME, "PEN": FixtureState.FINISHED_PENALTIES,
            "AWD": FixtureState.AWARDED, "WO": FixtureState.AWARDED,
        }
        for code, expected in cases.items():
            self.assertEqual(map_fixture_state(code), expected, code)

    def test_unobserved_code_is_unknown_not_guessed(self):
        # "1H" (in-play) is real for other providers but this module has not
        # yet captured a real API-Football response carrying it -- must not
        # be guessed into IN_PLAY.
        self.assertEqual(map_fixture_state("1H"), FixtureState.UNKNOWN)

    def test_none_and_non_string_are_unknown(self):
        self.assertEqual(map_fixture_state(None), FixtureState.UNKNOWN)
        self.assertEqual(map_fixture_state(123), FixtureState.UNKNOWN)


class FilterInScopeTests(unittest.TestCase):
    def test_keeps_only_in_scope_league_ids(self):
        in_scope = _real_ipswich_fixture()  # league.id 39, Premier League
        out_of_scope = copy.deepcopy(in_scope)
        out_of_scope["league"]["id"] = 71  # Serie A Brazil, not one of the 8
        result = filter_in_scope([in_scope, out_of_scope])
        self.assertEqual(result, [in_scope])

    def test_league_name_collision_across_countries_is_not_matched_by_id(self):
        # Real data: "Premier League" exists for England (id 39, in scope)
        # and also for e.g. Russia/Ukraine/Belarus (different ids, not in
        # scope) -- filtering must key on id, never on the name string.
        collider = copy.deepcopy(_real_ipswich_fixture())
        collider["league"]["id"] = 235  # Russia Premier League
        self.assertEqual(filter_in_scope([collider]), [])


class BuildFixtureTests(unittest.TestCase):
    def test_real_finished_fixture_builds_correctly(self):
        raw = _real_ipswich_fixture()
        fixture = build_fixture(raw, fetched_at="2026-10-24T16:00:00+00:00")
        self.assertEqual(fixture.provenance.source, "api_football")
        self.assertEqual(fixture.provenance.source_fixture_id, "1557443")
        self.assertEqual(fixture.provenance.fetched_at, "2026-10-24T16:00:00+00:00")
        self.assertEqual(fixture.competition, "Premier League")
        self.assertEqual(fixture.season, "2026")
        self.assertEqual(fixture.home_team, "Ipswich")
        self.assertEqual(fixture.away_team, "Nottingham Forest")
        self.assertEqual(fixture.kickoff.raw, "2026-10-23T19:00:00+00:00")
        self.assertEqual(fixture.kickoff.precision, "exact")
        self.assertIsNotNone(fixture.kickoff.utc)
        self.assertEqual(fixture.state, FixtureState.FINISHED_REGULATION)
        self.assertEqual((fixture.regulation_score.home, fixture.regulation_score.away), (1, 1))

    def test_content_sha256_is_deterministic_regardless_of_key_order(self):
        raw = _real_ipswich_fixture()
        reordered = json.loads(json.dumps(raw))  # same content
        reordered_rev = {k: reordered[k] for k in reversed(list(reordered))}
        f1 = build_fixture(raw, fetched_at="2026-10-24T16:00:00+00:00")
        f2 = build_fixture(reordered_rev, fetched_at="2026-10-24T16:00:00+00:00")
        self.assertEqual(f1.provenance.content_sha256, f2.provenance.content_sha256)

    def test_missing_fixture_id_raises(self):
        raw = copy.deepcopy(_real_ipswich_fixture())
        del raw["fixture"]["id"]
        with self.assertRaises(ApiFootballError):
            build_fixture(raw, fetched_at="2026-10-24T16:00:00+00:00")

    def test_missing_team_name_raises(self):
        raw = copy.deepcopy(_real_ipswich_fixture())
        raw["teams"]["home"]["name"] = None
        with self.assertRaises(ApiFootballError):
            build_fixture(raw, fetched_at="2026-10-24T16:00:00+00:00")

    def test_non_utc_offset_kickoff_raises(self):
        raw = copy.deepcopy(_real_ipswich_fixture())
        raw["fixture"]["date"] = "2026-10-23T19:00:00+02:00"
        with self.assertRaises(ApiFootballError):
            build_fixture(raw, fetched_at="2026-10-24T16:00:00+00:00")

    def test_scheduled_fixture_has_no_regulation_score(self):
        raw = copy.deepcopy(_real_ipswich_fixture())
        raw["fixture"]["status"] = {"long": "Not Started", "short": "NS", "elapsed": None, "extra": None}
        raw["goals"] = {"home": None, "away": None}
        raw["score"] = {"halftime": {"home": None, "away": None}, "fulltime": {"home": None, "away": None},
                         "extratime": {"home": None, "away": None}, "penalty": {"home": None, "away": None}}
        fixture = build_fixture(raw, fetched_at="2026-10-24T16:00:00+00:00")
        self.assertEqual(fixture.state, FixtureState.SCHEDULED)
        self.assertIsNone(fixture.regulation_score)


class FetchInScopeFixturesForDateTests(unittest.TestCase):
    def test_filters_and_builds_without_league_or_season_params(self):
        in_scope = _real_ipswich_fixture()
        out_of_scope = copy.deepcopy(in_scope)
        out_of_scope["league"]["id"] = 71
        out_of_scope["fixture"]["id"] = 999
        client = ApiFootballClient(api_key="k")
        client.get_fixtures_by_date = MagicMock(return_value=[in_scope, out_of_scope])
        fixtures = fetch_in_scope_fixtures_for_date(client, "2026-10-23")
        self.assertEqual(len(fixtures), 1)
        self.assertEqual(fixtures[0].provenance.source_fixture_id, "1557443")
        client.get_fixtures_by_date.assert_called_once_with("2026-10-23")


if __name__ == "__main__":
    unittest.main()
