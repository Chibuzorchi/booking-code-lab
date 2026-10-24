"""F9: API-Football client and score extraction. No real network call is
made anywhere in this file -- requests.Session.get is mocked throughout, so
no test consumes any of the account's daily quota."""
import unittest
from unittest.mock import MagicMock, patch

import requests

from portfolio.results_tracking.sources.api_football import (
    ApiFootballClient, ApiFootballError, extract_regulation_score,
)


def _mock_response(json_body, status_ok=True):
    resp = MagicMock()
    resp.json.return_value = json_body
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = requests.RequestException("http error")
    return resp


class ApiFootballClientTests(unittest.TestCase):
    def test_missing_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ApiFootballError):
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

    def test_api_errors_field_raises(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response(
            {"response": [], "errors": ["Invalid API key"]}))
        with self.assertRaises(ApiFootballError):
            client.get_fixtures_by_date("2026-10-24")

    def test_transport_failure_raises(self):
        client = ApiFootballClient(api_key="k")
        client.session.get = MagicMock(return_value=_mock_response({}, status_ok=False))
        with self.assertRaises(ApiFootballError):
            client.get_fixtures_by_date("2026-10-24")

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

    def test_extra_time_status_not_trusted(self):
        fixture = {
            "fixture": {"status": {"short": "AET"}},
            "score": {"fulltime": {"home": 1, "away": 1}, "halftime": {"home": 0, "away": 0}},
        }
        self.assertIsNone(extract_regulation_score(fixture))

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


if __name__ == "__main__":
    unittest.main()
