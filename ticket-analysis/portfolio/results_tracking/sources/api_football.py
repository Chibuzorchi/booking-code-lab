"""Minimal api-football.com (api-sports.io) client: fixture lookup by date,
plus the real adapter that turns its raw responses into this project's
shared Fixture model (fixtures.py).

Raw JSON parsed directly -- no AI-summarization step between the response and
a recorded fact, per the methodology rule adopted in docs/F9_SOURCE_FEASIBILITY.md
after an AI-summarized fetch fabricated a result for a different source. The
API key is read only from the API_FOOTBALL_KEY environment variable, never
hardcoded or logged.

**Status/score evidence (24 October 2026):** a live date-only fetch for
2026-10-23 (446 real fixtures) and 2026-10-25 (1,191 real fixtures) was
hand-inspected to confirm what this module trusts, rather than relying on
provider documentation alone:

- `score.fulltime` is the regulation (90-minute) score even for AET/PEN
  matches, confirmed by direct arithmetic on two real responses: one AET
  fixture had `score.fulltime={2,2}`, `score.extratime={2,0}`,
  top-level `goals={4,2}` -- fulltime + extratime == goals, so fulltime is
  provably the pre-extra-time score, not the final one. One PEN fixture had
  `score.fulltime={0,0}`, `score.extratime={null,null}` (no extra time
  played in that competition's format) and `score.penalty={2,3}` -- fulltime
  again matches the pre-shootout score. This supersedes this module's
  earlier conservative FT-only stance; AET and PEN are now trusted too.
- Every other status actually observed across those two real fetches (`NS`,
  `PST`, `CANC`, `AWD`, `WO`, plus `FT`/`AET`/`PEN` above) is mapped below.
  Any status not in that observed set maps to `FixtureState.UNKNOWN` rather
  than a guessed category (e.g. the in-play codes like `1H`/`HT`/`2H` --
  real for other providers' documentation, but this module has not yet
  captured a real API-Football response carrying one) -- never claims a
  state from secondhand documentation this project's own methodology
  distrusts for exactly this reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

import requests

from ..fixtures import Fixture, FixtureProvenance, FixtureState, InvalidFixtureError, KickoffTime
from ..rules.scores import InvalidScoreError, RegulationScore

API_BASE = "https://v3.football.api-sports.io"

# Statuses for which score.fulltime is now known -- via the real evidence
# above, not provider documentation alone -- to be the regulation-time score.
_REGULATION_FINISHED_STATUSES = {"FT", "AET", "PEN"}

# status.short -> FixtureState, built only from codes this module has
# actually observed in a real API-Football response (see module docstring).
# Anything else falls through to FixtureState.UNKNOWN in map_fixture_state --
# add a code here only once a real response carrying it has been inspected.
_STATUS_TO_STATE = {
    "NS": FixtureState.SCHEDULED,
    "PST": FixtureState.POSTPONED,
    "CANC": FixtureState.CANCELLED,
    "FT": FixtureState.FINISHED_REGULATION,
    "AET": FixtureState.FINISHED_EXTRA_TIME,
    "PEN": FixtureState.FINISHED_PENALTIES,
    "AWD": FixtureState.AWARDED,
    "WO": FixtureState.AWARDED,
}

# The 8 leagues F9_RULE_REGISTRY.md scopes evaluation to, resolved to real
# API-Football league IDs by hand-inspecting real fixtures for each (24
# October 2026) -- never guessed from a league name string, which collides
# across countries (e.g. "Premier League" exists in >10 countries in the
# real data, "Bundesliga" in both Germany and Austria).
IN_SCOPE_LEAGUE_IDS = {
    39: "England · Premier League",
    40: "England · Championship",
    78: "Germany · Bundesliga",
    140: "Spain · LaLiga",
    61: "France · Ligue 1",
    135: "Italy · Serie A",
    88: "Netherlands · Eredivisie",
    94: "Portugal · Liga Portugal",
}


class ApiFootballError(RuntimeError):
    """A response-shape or application-level problem talking to
    API-Football that must never be retried (F9_STEP5_REVIEW.md S5-11):
    the request was answered, but the answer itself is unusable or
    wrong-shaped, or signals a condition a retry cannot fix."""


class ApiFootballTransportError(ApiFootballError):
    """A transport-level failure -- connection error, timeout, or a 5xx
    server error -- that is evidence of nothing about fixture data and is
    safe to retry (F9_STEP5_REVIEW.md S5-11)."""


class ApiFootballAuthError(ApiFootballError):
    """Authentication/authorization failed (missing or rejected API key).
    Retrying with the same key cannot succeed."""


class ApiFootballQuotaError(ApiFootballError):
    """The source itself signalled a rate/quota limit. Retrying
    immediately cannot succeed."""


def _classify_api_errors(errors) -> ApiFootballError:
    """API-Football's `errors` field is a dict (field name -> message) or,
    for some failure modes, a list of message strings. Classified by
    looking at its keys/entries for the auth- and quota-related names
    API-Football is documented to use, falling back to the generic
    (fatal, non-retryable) ApiFootballError for anything else -- never
    guessed into "safe to retry" without a recognized signal."""
    if isinstance(errors, dict):
        tokens = {str(key).lower() for key in errors}
    elif isinstance(errors, list):
        tokens = {str(item).lower() for item in errors}
    else:
        tokens = set()
    message = f"API-Football returned errors: {errors}"
    if tokens & {"token", "apikey", "auth"}:
        return ApiFootballAuthError(message)
    if tokens & {"requests", "ratelimit", "rate_limit"}:
        return ApiFootballQuotaError(message)
    return ApiFootballError(message)


class ApiFootballClient:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None,
                 timeout_s: float = 10.0):
        self.api_key = api_key or os.environ.get("API_FOOTBALL_KEY")
        if not self.api_key:
            raise ApiFootballAuthError("API_FOOTBALL_KEY is not set")
        self.session = session or requests.Session()
        self.session.headers.update({"x-apisports-key": self.api_key})
        self.timeout_s = timeout_s

    def get_fixtures_by_date(self, date: str, league: int | None = None,
                              season: int | None = None) -> list[dict]:
        """date: 'YYYY-MM-DD'. Returns the raw `response` list exactly as the
        API returns it -- no filtering, inference, or summarization here.

        Raises ApiFootballTransportError for connection/timeout/5xx
        failures (retryable -- the request never reached a usable answer),
        and the other ApiFootballError subclasses for everything else
        (fatal -- an answer was reached and it is unusable, F9_STEP5_REVIEW.md
        S5-11): callers must retry only the former."""
        params = {"date": date}
        if league is not None:
            params["league"] = league
        if season is not None:
            params["season"] = season
        try:
            resp = self.session.get(f"{API_BASE}/fixtures", params=params, timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise ApiFootballTransportError(f"request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise ApiFootballAuthError(f"API-Football authentication failed: HTTP {resp.status_code}")
        if resp.status_code == 429:
            raise ApiFootballQuotaError(f"API-Football rate limit: HTTP {resp.status_code}")
        if resp.status_code >= 500:
            raise ApiFootballTransportError(f"API-Football server error: HTTP {resp.status_code}")
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise ApiFootballError(f"unexpected HTTP error: {exc}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ApiFootballError(f"response is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiFootballError(f"API-Football envelope is not a JSON object: {type(payload)!r}")
        errors = payload.get("errors")
        if errors:
            raise _classify_api_errors(errors)
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


def map_fixture_state(status_short: str | None) -> FixtureState:
    """status.short -> FixtureState, from _STATUS_TO_STATE's real-evidence
    mapping only. An unrecognized or missing code is FixtureState.UNKNOWN,
    never guessed into SCHEDULED/IN_PLAY/a finished state."""
    if status_short is None or not isinstance(status_short, str):
        return FixtureState.UNKNOWN
    return _STATUS_TO_STATE.get(status_short, FixtureState.UNKNOWN)


def extract_regulation_score(fixture_json: dict) -> RegulationScore | None:
    """None when the fixture hasn't reached a status this module trusts for
    a regulation-time score (not yet played, in play, or a finish type this
    module hasn't seen real evidence for)."""
    fixture = _require_dict_or_empty(fixture_json.get("fixture"), "fixture")
    status_obj = _require_dict_or_empty(fixture.get("status"), "fixture.status")
    status = status_obj.get("short")
    if status is not None and not isinstance(status, str):
        raise ApiFootballError(f"fixture.status.short is not a string: {type(status)!r}")
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


def filter_in_scope(fixtures_json: list[dict]) -> list[dict]:
    """Keeps only fixtures in IN_SCOPE_LEAGUE_IDS. This is the "filter in our
    own code after the fetch" workaround docs/F9_SOURCE_FEASIBILITY.md
    records: the free tier rejects combining a `league` filter with the
    current season, so get_fixtures_by_date() is called with no league/
    season params at all, and narrowing to the 8 in-scope competitions
    happens here instead, by league ID -- never by league name, which
    collides across countries in the real data (e.g. "Premier League" and
    "Bundesliga" each exist under several countries)."""
    kept = []
    for item in fixtures_json:
        league = _require_dict_or_empty(item.get("league"), "league")
        if league.get("id") in IN_SCOPE_LEAGUE_IDS:
            kept.append(item)
    return kept


def _parse_kickoff(fixture_json: dict) -> KickoffTime:
    fixture = _require_dict_or_empty(fixture_json.get("fixture"), "fixture")
    date_raw = fixture.get("date")
    if not isinstance(date_raw, str) or not date_raw:
        raise ApiFootballError(f"fixture.date is missing or not a string: {date_raw!r}")
    try:
        parsed = datetime.fromisoformat(date_raw)
    except ValueError as exc:
        raise ApiFootballError(f"fixture.date is not a valid ISO8601 timestamp: {date_raw!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ApiFootballError(f"fixture.date has no zero UTC offset: {date_raw!r}")
    return KickoffTime(
        raw=date_raw, precision="exact", utc=parsed,
        timezone_evidence="API-Football fixture.date field, observed with a literal "
                           "+00:00 offset in every real response hand-inspected this "
                           "session (2026-10-23/24/25)")


def build_fixture(fixture_json: dict, *, fetched_at: str) -> Fixture:
    """One real API-Football fixture response item -> this project's shared
    Fixture model. fetched_at is supplied by the caller (the actual time of
    the request), never invented here. content_sha256 hashes the fixture's
    own raw JSON (canonicalized with sort_keys so the digest is deterministic
    regardless of key order), preserving it as this fixture's exact evidence."""
    fixture = _require_dict_or_empty(fixture_json.get("fixture"), "fixture")
    fixture_id = fixture.get("id")
    if not isinstance(fixture_id, int) or isinstance(fixture_id, bool):
        raise ApiFootballError(f"fixture.id is missing or not an int: {fixture_id!r}")
    league = _require_dict_or_empty(fixture_json.get("league"), "league")
    league_name = league.get("name")
    if not isinstance(league_name, str) or not league_name:
        raise ApiFootballError(f"league.name is missing or not a string: {league_name!r}")
    season = league.get("season")
    if season is not None:
        season = str(season)
    teams = _require_dict_or_empty(fixture_json.get("teams"), "teams")
    home = _require_dict_or_empty(teams.get("home"), "teams.home")
    away = _require_dict_or_empty(teams.get("away"), "teams.away")
    home_name, away_name = home.get("name"), away.get("name")
    if not isinstance(home_name, str) or not home_name:
        raise ApiFootballError(f"teams.home.name is missing or not a string: {home_name!r}")
    if not isinstance(away_name, str) or not away_name:
        raise ApiFootballError(f"teams.away.name is missing or not a string: {away_name!r}")

    status_obj = _require_dict_or_empty(fixture.get("status"), "fixture.status")
    state = map_fixture_state(status_obj.get("short"))
    regulation_score = extract_regulation_score(fixture_json)

    content_sha256 = hashlib.sha256(
        json.dumps(fixture_json, sort_keys=True).encode("utf-8")).hexdigest()

    try:
        return Fixture(
            provenance=FixtureProvenance(
                source="api_football", source_fixture_id=str(fixture_id),
                fetched_at=fetched_at, content_sha256=content_sha256),
            competition=league_name, season=season, home_team=home_name, away_team=away_name,
            kickoff=_parse_kickoff(fixture_json), state=state, regulation_score=regulation_score)
    except InvalidFixtureError as exc:
        raise ApiFootballError(f"fixture {fixture_id} produced an invalid Fixture: {exc}") from exc


def fetch_in_scope_fixtures_for_date(client: ApiFootballClient, date: str) -> list[Fixture]:
    """The real adapter entry point: one date-only fetch (no league/season
    params -- see filter_in_scope), narrowed to the 8 in-scope leagues, built
    into Fixture records with a shared fetched_at for this one call."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    raw_fixtures = client.get_fixtures_by_date(date)
    in_scope = filter_in_scope(raw_fixtures)
    return [build_fixture(item, fetched_at=fetched_at) for item in in_scope]
