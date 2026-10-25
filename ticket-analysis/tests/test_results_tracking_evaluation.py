"""F9 step 4: per-selection evaluation pipeline (classify -> match -> side
resolve -> evaluate). Pure/offline -- no network, no run directories.
"""
import unittest
from datetime import datetime, timedelta, timezone

from portfolio.results_tracking.evaluation import evaluate_selection, evaluate_selection_detailed
from portfolio.results_tracking.fixtures import Fixture, FixtureProvenance, FixtureState, KickoffTime
from portfolio.results_tracking.matching import Alias, MatchingError, VerifiedMapping
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

    def test_unsupported_sport_with_unparseable_event_display_never_attempts_matching(self):
        # F9_STEP4_REREVIEW.md S4R-06 reproduction: a Tennis leg (unsupported
        # sport) whose event_display isn't a "Team - Team" string must stay
        # an explicit unresolved row. evaluate_selection_detailed() must not
        # attempt matching for it (a real match attempt would raise
        # MatchingError trying to split "not a team fixture"), and must
        # report decision=None ("not attempted"), not a failed decision.
        outcome, decision = evaluate_selection_detailed(**_leg(
            sport="Tennis", event_display="not a team fixture",
            candidate_fixtures=(_fixture(),)))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "unsupported_sport")
        self.assertIsNone(decision)

    def test_supported_leg_returns_its_match_decision(self):
        fixture = _fixture()
        outcome, decision = evaluate_selection_detailed(**_leg(candidate_fixtures=(fixture,)))
        self.assertIsNotNone(decision)


class MatchingTests(unittest.TestCase):
    def test_no_candidate_fixtures_is_unresolved_fixture_not_matched(self):
        outcome = evaluate_selection(**_leg(candidate_fixtures=()))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "fixture_not_matched")
        self.assertEqual(outcome.rule_id, "regulation_1x2")

    def test_ambiguous_match_is_unresolved(self):
        fx1 = _fixture(competition="Premier League")
        fx2 = _fixture(competition="Premier League", season="2027",
                       provenance=_provenance(source_fixture_id="2"))
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
                                   source_fixture_id="1", source_competition="Premier League",
                                   source_home_team="Arsenal", source_away_team="Everton",
                                   source_kickoff_raw="2026-10-24",
                                   verified_at="2026-10-23T00:00:00Z", evidence="manual")
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

    def test_in_play_fixture_is_pending(self):
        fx = _fixture(state=FixtureState.IN_PLAY, score=None)
        outcome = evaluate_selection(**_leg(candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.PENDING)
        self.assertEqual(outcome.reason, "result_not_yet_available")

    def test_postponed_fixture_is_pending(self):
        fx = _fixture(state=FixtureState.POSTPONED, score=None)
        outcome = evaluate_selection(**_leg(candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.PENDING)
        self.assertEqual(outcome.reason, "result_not_yet_available")

    def test_cancelled_fixture_is_unresolved_not_pending(self):
        # F9_STEP4_REVIEW.md S4-06: a cancelled fixture is not an ordinary
        # "not yet available" result and must not be reported as pending.
        fx = _fixture(state=FixtureState.CANCELLED, score=None)
        outcome = evaluate_selection(**_leg(candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "fixture_cancelled")

    def test_abandoned_fixture_is_unresolved_not_pending(self):
        fx = _fixture(state=FixtureState.ABANDONED, score=None)
        outcome = evaluate_selection(**_leg(candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "fixture_abandoned")

    def test_awarded_fixture_is_unresolved_not_pending(self):
        fx = _fixture(state=FixtureState.AWARDED, score=None)
        outcome = evaluate_selection(**_leg(candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "fixture_awarded_unverified_policy")

    def test_unknown_fixture_state_is_unresolved_not_pending(self):
        fx = _fixture(state=FixtureState.UNKNOWN, score=None)
        outcome = evaluate_selection(**_leg(candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "fixture_state_unknown")

    def test_finished_penalties_without_score_is_unresolved_missing_period(self):
        fx = _fixture(state=FixtureState.FINISHED_PENALTIES, score=None)
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

    def test_literal_home_over_under_resolves_without_team_alias(self):
        # F9_STEP4_REVIEW.md S4-07: "Home"/"Away" here name a side, not a
        # club, so this must resolve without any team alias lookup.
        fx = _fixture(score=RegulationScore(2, 1))
        outcome = evaluate_selection(
            **_leg(market="Home Over/Under", pick="Over", specifier="1.5",
                   candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.WON)  # home scored 2, line 1.5
        self.assertEqual(outcome.rule_id, "team_total_over_under")

    def test_literal_away_over_under_resolves_without_team_alias(self):
        fx = _fixture(score=RegulationScore(2, 1))
        outcome = evaluate_selection(
            **_leg(market="Away Over/Under", pick="Over", specifier="1.5",
                   candidate_fixtures=(fx,), aliases=self._aliases()))
        self.assertEqual(outcome.status, Outcome.LOST)  # away scored 1, line 1.5
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


class ConflictingEvidencePropagationTests(unittest.TestCase):
    """F9_STEP4_REVIEW.md S4-02: contradictory evidence must abort the leg
    evaluation with MatchingError, not silently resolve by list order."""

    def test_conflicting_fixture_observations_raise_through_evaluate_selection(self):
        fx_a = _fixture()
        fx_b = _fixture(home="Chelsea", away="Fulham")  # same default source_fixture_id="1"
        with self.assertRaises(MatchingError):
            evaluate_selection(**_leg(candidate_fixtures=(fx_a, fx_b)))


class KickoffUtcOffsetTests(unittest.TestCase):
    def test_non_utc_offset_is_rejected(self):
        from portfolio.results_tracking.fixtures import InvalidFixtureError
        with self.assertRaises(InvalidFixtureError):
            KickoffTime(raw="2026-10-25T00:30:00+02:00", precision="exact",
                        utc=datetime(2026, 10, 25, 0, 30, tzinfo=timezone(timedelta(hours=2))),
                        timezone_evidence="provider-documented +02:00")


if __name__ == "__main__":
    unittest.main()
