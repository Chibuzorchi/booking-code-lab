"""Minimal api-football.com (api-sports.io) client: fixture lookup by date.

Raw JSON parsed directly -- no AI-summarization step between the response and
a recorded fact, per the methodology rule adopted in docs/F9_SOURCE_FEASIBILITY.md
after an AI-summarized fetch fabricated a result for a different source. The
API key is read only from the API_FOOTBALL_KEY environment variable, never
hardcoded or logged.

The fixture response shape (fixture.status.short, score.fulltime,
score.halftime) follows api-football's published documentation; this project
has not yet independently verified it against a real captured finished-match
response, so extract_regulation_score() stays conservative: it only accepts
a fulltime score when status.short == "FT" (ordinary full time), and declines
-- rather than guesses -- for "AET"/"PEN" or any status this module hasn't
seen evidence for.
"""
from __future__ import annotations

import os

import requests

from ..rules.scores import InvalidScoreError, RegulationScore

API_BASE = "https://v3.football.api-sports.io"

# Statuses for which score.fulltime is known, from api-football's docs, to be
# the regulation-time score. Extra-time/penalty statuses are deliberately
# left out until a real response confirms what fulltime means for them.
_REGULATION_FINISHED_STATUSES = {"FT"}


class ApiFootballError(RuntimeError):
    """A transport, auth, or response-shape problem talking to API-Football."""


class ApiFootballClient:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None,
                 timeout_s: float = 10.0):
        self.api_key = api_key or os.environ.get("API_FOOTBALL_KEY")
        if not self.api_key:
            raise ApiFootballError("API_FOOTBALL_KEY is not set")
        self.session = session or requests.Session()
        self.session.headers.update({"x-apisports-key": self.api_key})
        self.timeout_s = timeout_s

    def get_fixtures_by_date(self, date: str, league: int | None = None,
                              season: int | None = None) -> list[dict]:
        """date: 'YYYY-MM-DD'. Returns the raw `response` list exactly as the
        API returns it -- no filtering, inference, or summarization here."""
        params = {"date": date}
        if league is not None:
            params["league"] = league
        if season is not None:
            params["season"] = season
        try:
            resp = self.session.get(f"{API_BASE}/fixtures", params=params, timeout=self.timeout_s)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise ApiFootballError(f"request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiFootballError(f"API-Football envelope is not a JSON object: {type(payload)!r}")
        errors = payload.get("errors")
        if errors:
            raise ApiFootballError(f"API-Football returned errors: {errors}")
        response = payload.get("response")
        if response is None:
            raise ApiFootballError("API-Football envelope has no 'response' field")
        if not isinstance(response, list):
            raise ApiFootballError(f"API-Football 'response' is not a list: {type(response)!r}")
        for item in response:
            if not isinstance(item, dict):
                raise ApiFootballError(f"API-Football response item is not an object: {type(item)!r}")
        return response


def _require_dict_or_empty(container: object, field_name: str) -> dict:
    """container is the result of a .get() that may be legitimately absent
    (-> {}) or explicitly present-but-null in the JSON (-> None, which .get()
    passes through unchanged rather than substituting its default) or
    present with the wrong type entirely. Only the first two are tolerated;
    the third is a malformed shape and must raise, not crash the caller with
    an AttributeError on the next field access."""
    if container is None:
        return {}
    if not isinstance(container, dict):
        raise ApiFootballError(f"{field_name} is not an object: {type(container)!r}")
    return container


def extract_regulation_score(fixture_json: dict) -> RegulationScore | None:
    """None when the fixture hasn't reached a status this module trusts for
    a regulation-time score (not yet played, in play, or a finish type -
    AET/PEN - whose fulltime semantics are unverified here)."""
    fixture = _require_dict_or_empty(fixture_json.get("fixture"), "fixture")
    status_obj = _require_dict_or_empty(fixture.get("status"), "fixture.status")
    status = status_obj.get("short")
    if status not in _REGULATION_FINISHED_STATUSES:
        return None
    score = _require_dict_or_empty(fixture_json.get("score"), "score")
    fulltime = _require_dict_or_empty(score.get("fulltime"), "score.fulltime")
    halftime = _require_dict_or_empty(score.get("halftime"), "score.halftime")
    ft_home, ft_away = fulltime.get("home"), fulltime.get("away")
    if ft_home is None or ft_away is None:
        raise ApiFootballError(
            f"status is {status!r} but score.fulltime is incomplete: {fulltime!r}")
    ht_home, ht_away = halftime.get("home"), halftime.get("away")
    try:
        return RegulationScore(ft_home, ft_away, ht_home, ht_away)
    except InvalidScoreError as exc:
        raise ApiFootballError(f"inconsistent score in fixture response: {exc}") from exc
