"""F9 step 4: per-selection evaluation pipeline (classify -> match -> side
resolve -> evaluate). Pure/offline -- no network, no run directories.
"""
import unittest
from datetime import datetime, timezone

from portfolio.results_tracking.evaluation import evaluate_selection
from portfolio.results_tracking.fixtures import Fixture, FixtureProvenance, FixtureState, KickoffTime
from portfolio.results_tracking.matching import Alias, VerifiedMapping
from portfolio.results_tracking.reporting import Outcome
from portfolio.results_tracking.rules.scores import RegulationScore


def _provenance(source_fixture_id="1"):
    return FixtureProvenance(source="api_football", source_fixture_id=source_fixture_id,
                              fetched_at="2026-10-24T12:00:00Z", content_sha256="a" * 64)


def _kickoff(raw="2026-10-24"):
    return KickoffTime(raw=raw, precision="date_only")


def _fixture(home="Arsenal", away="Everton", state=FixtureState.FINISHED_REGULATION,
             score=RegulationScore(2, 1), competition="Premier League", **overrides):
    defaults = dict(provenance=_provenance(), competition=competition, season="2026",
                     home_team=home, away_team=away, kickoff=_kickoff(), state=state,
                     regulation_score=score)
    defaults.update(overrides)
    return Fixture(**defaults)


def _leg(**overrides):
    defaults = dict(leg_id="leg1", provider="sportybet", provider_event_id=None,
                     event_display="Arsenal - Everton", league_display="Premier League",
                     sport="Football", market="1X2", pick="Home", specifier="",
                     kickoff=_kickoff(), candidate_fixtures=(), aliases=(), verified_mappings=())
    defaults.update(overrides)
    return defaults


class UnsupportedMarketTests(unittest.TestCase):
    def test_unsupported_sport_is_unresolved_before_any_matching(self):
        outcome = evaluate_selection(**_leg(sport="Handball", candidate_fixtures=(_fixture(),)))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "unsupported_sport")
        self.assertIsNone(outcome.rule_id)

    def test_unsupported_market_is_unresolved(self):
        outcome = evaluate_selection(**_leg(market="Asian Handicap", candidate_fixtures=(_fixture(),)))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "unsupported_market")


class MatchingTests(unittest.TestCase):
    def test_no_candidate_fixtures_is_unresolved_fixture_not_matched(self):
        outcome = evaluate_selection(**_leg(candidate_fixtures=()))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "fixture_not_matched")
        self.assertEqual(outcome.rule_id, "regulation_1x2")

    def test_ambiguous_match_is_unresolved(self):
        fx1 = _fixture(competition="Premier League")
        fx2 = _fixture(competition="Premier League", season="2027")
        aliases = (Alias("sportybet_competition", "Premier League", "EPL"),
                   Alias("api_football_competition", "Premier League", "EPL"),
                   Alias("sportybet_team", "Arsenal", "Arsenal"),
                   Alias("api_football_team", "Arsenal", "Arsenal"),
                   Alias("sportybet_team", "Everton", "Everton"),
                   Alias("api_football_team", "Everton", "Everton"))
        outcome = evaluate_selection(**_leg(candidate_fixtures=(fx1, fx2), aliases=aliases))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "ambiguous_fixture")

    def test_verified_mapping_matches_without_aliases(self):
        fx = _fixture()
        mapping = VerifiedMapping(provider="sportybet", provider_event_id="evt-1",
                                   league_display="Premier League", home_display="Arsenal",
                                   away_display="Everton", source="api_football",
                                   source_fixture_id="1", verified_at="2026-10-23T00:00:00Z",
                                   evidence="manual")
        outcome = evaluate_selection(**_leg(provider_event_id="evt-1", candidate_fixtures=(fx,),
                                             verified_mappings=(mapping,)))
        self.assertEqual(outcome.status, Outcome.WON)  # Arsenal 2-1 Everton, "Home"


class FixtureStateTests(unittest.TestCase):
    def _aliases(self):
        return (Alias("sportybet_competition", "Premier League", "EPL"),
                Alias("api_football_competition", "Premier League", "EPL"),
                Alias("sportybet_team", "Arsenal", "Arsenal"),
                Alias("api_football_team", "Arsenal", "Arsenal"),
                Alias("sportybet_team", "Everton", "Everton"),
                Alias("api_football_team", "Everton", "Everton"))

    def test_scheduled_fixture_is_pending(self):
        fx = _fixture(state=FixtureState.SCHEDULED, score=None)
        outcome = evaluate_selection(**_leg(candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.PENDING)
        self.assertEqual(outcome.reason, "result_not_yet_available")

    def test_finished_extra_time_without_score_is_unresolved_missing_period(self):
        fx = _fixture(state=FixtureState.FINISHED_EXTRA_TIME, score=None)
        outcome = evaluate_selection(**_leg(candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "missing_period")


class RuleDispatchTests(unittest.TestCase):
    def _aliases(self):
        return (Alias("sportybet_competition", "Premier League", "EPL"),
                Alias("api_football_competition", "Premier League", "EPL"),
                Alias("sportybet_team", "Arsenal", "Arsenal"),
                Alias("api_football_team", "Arsenal", "Arsenal"),
                Alias("sportybet_team", "Everton", "Everton"),
                Alias("api_football_team", "Everton", "Everton"))

    def test_1x2_won(self):
        fx = _fixture(score=RegulationScore(2, 1))
        outcome = evaluate_selection(**_leg(market="1X2", pick="Home",
                                             candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.WON)
        self.assertEqual(outcome.rule_id, "regulation_1x2")

    def test_over_under_specifier(self):
        fx = _fixture(score=RegulationScore(2, 1))
        outcome = evaluate_selection(**_leg(market="Over/Under", pick="Over", specifier="2.5",
                                             candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.WON)

    def test_win_either_half_literal_side(self):
        fx = _fixture(score=RegulationScore(2, 1, ht_home=1, ht_away=0))
        outcome = evaluate_selection(
            **_leg(market="Home Team to Win Either Half", pick="Yes",
                   candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.WON)
        self.assertEqual(outcome.rule_id, "win_either_half")

    def test_team_total_over_under_resolves_side_via_alias(self):
        fx = _fixture(score=RegulationScore(2, 1))
        outcome = evaluate_selection(
            **_leg(provider="sportybet", market="Arsenal Over/Under", pick="Over", specifier="1.5",
                   candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.WON)  # Arsenal (home) scored 2, line 1.5
        self.assertEqual(outcome.rule_id, "team_total_over_under")

    def test_team_total_over_under_unresolvable_team_name_is_invalid_input(self):
        # "Chelsea" is a real, verified team name (so classify() supports the
        # market) but isn't either side of the matched Arsenal-Everton
        # fixture -- a data-integrity mismatch, not a guessable side.
        fx = _fixture(score=RegulationScore(2, 1))
        outcome = evaluate_selection(
            **_leg(provider="sportybet", market="Chelsea Over/Under", pick="Over", specifier="1.5",
                   candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "invalid_input")
        self.assertEqual(outcome.rule_id, "team_total_over_under")


if __name__ == "__main__":
    unittest.main()
