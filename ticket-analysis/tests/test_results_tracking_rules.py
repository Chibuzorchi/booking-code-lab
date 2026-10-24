"""F9 step 3: rule evaluation, score fact validation, and registry dispatch.

Pure-function tests; no fixtures, no network, no run directories needed.
"""
import unittest

from portfolio.results_tracking.rules import football, provider_types, registry
from portfolio.results_tracking.rules.scores import InvalidScoreError, RegulationScore


class RegulationScoreTests(unittest.TestCase):
    def test_valid_score_with_half_time(self):
        score = RegulationScore(2, 1, ht_home=1, ht_away=0)
        self.assertTrue(score.has_half_time)
        self.assertEqual(score.second_half, (1, 1))

    def test_valid_score_without_half_time(self):
        score = RegulationScore(2, 1)
        self.assertFalse(score.has_half_time)

    def test_negative_goal_rejected(self):
        with self.assertRaises(InvalidScoreError):
            RegulationScore(-1, 0)

    def test_bool_rejected_as_goal_count(self):
        with self.assertRaises(InvalidScoreError):
            RegulationScore(True, 0)

    def test_half_only_one_side_rejected(self):
        with self.assertRaises(InvalidScoreError):
            RegulationScore(2, 1, ht_home=1)

    def test_half_time_exceeding_full_time_rejected(self):
        with self.assertRaises(InvalidScoreError):
            RegulationScore(1, 1, ht_home=2, ht_away=0)

    def test_second_half_without_half_time_raises(self):
        score = RegulationScore(2, 1)
        with self.assertRaises(InvalidScoreError):
            score.second_half


class RegulationOneByTwoTests(unittest.TestCase):
    def test_bet9ja_home_win(self):
        self.assertEqual(football.regulation_1x2("1", RegulationScore(2, 0)), ("won", None))

    def test_sportybet_draw_pick(self):
        self.assertEqual(football.regulation_1x2("Draw", RegulationScore(1, 1)), ("won", None))

    def test_away_win_loses_home_pick(self):
        self.assertEqual(football.regulation_1x2("1", RegulationScore(0, 2)), ("lost", None))

    def test_unknown_pick_unresolved(self):
        self.assertEqual(football.regulation_1x2("3", RegulationScore(1, 0)), ("unresolved", "invalid_input"))


class DoubleChanceTests(unittest.TestCase):
    def test_bet9ja_1x_covers_draw(self):
        self.assertEqual(football.double_chance("1X", RegulationScore(1, 1)), ("won", None))

    def test_sportybet_draw_or_away_covers_away_win(self):
        self.assertEqual(football.double_chance("Draw or Away", RegulationScore(0, 1)), ("won", None))

    def test_double_chance_miss(self):
        self.assertEqual(football.double_chance("12", RegulationScore(1, 1)), ("lost", None))


class GoalGoalNoGoalTests(unittest.TestCase):
    def test_yes_both_scored(self):
        self.assertEqual(football.goal_goal_no_goal("Yes", RegulationScore(1, 1)), ("won", None))

    def test_no_when_a_side_blanked(self):
        self.assertEqual(football.goal_goal_no_goal("No", RegulationScore(2, 0)), ("won", None))

    def test_yes_fails_on_clean_sheet(self):
        self.assertEqual(football.goal_goal_no_goal("Yes", RegulationScore(2, 0)), ("lost", None))


class OddEvenTests(unittest.TestCase):
    def test_even_total(self):
        self.assertEqual(football.odd_even("Even", RegulationScore(1, 1)), ("won", None))

    def test_odd_total(self):
        self.assertEqual(football.odd_even("Odd", RegulationScore(2, 1)), ("won", None))


class CorrectScoreTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(football.correct_score("2:1", RegulationScore(2, 1)), ("won", None))

    def test_near_miss_is_a_loss_not_void(self):
        self.assertEqual(football.correct_score("2:0", RegulationScore(2, 1)), ("lost", None))

    def test_malformed_pick_unresolved(self):
        self.assertEqual(football.correct_score("2-1", RegulationScore(2, 1)), ("unresolved", "invalid_input"))


class OverUnderTests(unittest.TestCase):
    def test_bet9ja_style_line_in_specifier(self):
        outcome = football.over_under("Over", "total=2.5", RegulationScore(2, 1))
        self.assertEqual(outcome, ("won", None))

    def test_sportybet_style_line_in_pick(self):
        outcome = football.over_under("Under 3", "total=3", RegulationScore(1, 1))
        self.assertEqual(outcome, ("won", None))

    def test_integer_line_push_is_void_not_won_or_lost(self):
        outcome = football.over_under("Over 2", "total=2", RegulationScore(1, 1))
        self.assertEqual(outcome, ("void", None))

    def test_mismatched_pick_and_specifier_lines_unresolved(self):
        outcome = football.over_under("Over 2.5", "total=3", RegulationScore(2, 1))
        self.assertEqual(outcome, ("unresolved", "invalid_input"))

    def test_half_time_period_without_half_facts(self):
        outcome = football.over_under("Over", "total=1.5", RegulationScore(2, 1), period="HT")
        self.assertEqual(outcome, ("unresolved", "missing_period"))

    def test_second_half_period_uses_second_half_total(self):
        score = RegulationScore(2, 1, ht_home=2, ht_away=0)
        outcome = football.over_under("Over 0.5", "total=0.5", score, period="2H")
        self.assertEqual(outcome, ("won", None))


class WinEitherHalfTests(unittest.TestCase):
    def test_home_wins_second_half_only(self):
        score = RegulationScore(2, 1, ht_home=0, ht_away=1)
        self.assertEqual(football.win_either_half("Yes", score, "home"), ("won", None))

    def test_home_draws_both_halves_is_no(self):
        score = RegulationScore(1, 1, ht_home=0, ht_away=0)
        self.assertEqual(football.win_either_half("No", score, "home"), ("won", None))

    def test_missing_half_time_unresolved(self):
        score = RegulationScore(1, 0)
        self.assertEqual(football.win_either_half("Yes", score, "home"), ("unresolved", "missing_period"))


class TeamTotalOverUnderTests(unittest.TestCase):
    def test_home_side_over(self):
        outcome = football.team_total_over_under("Over 1.5", "total=1.5", RegulationScore(2, 0), "home")
        self.assertEqual(outcome, ("won", None))

    def test_away_side_integer_line_push(self):
        outcome = football.team_total_over_under("Over 1", "total=1", RegulationScore(2, 1), "away")
        self.assertEqual(outcome, ("void", None))


class ProviderTypesTests(unittest.TestCase):
    def test_bet_type_always_unverified(self):
        result = provider_types.verify_bet_type("bet9ja", "0")
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["reason"], "no_documented_provider_mapping")

    def test_verified_naive_utc_fixture(self):
        self.assertTrue(provider_types.is_verified_naive_utc(
            "bet9ja", "Arsenal - Everton", "2026-10-24 14:00:00"))

    def test_unverified_fixture_not_generalized(self):
        self.assertFalse(provider_types.is_verified_naive_utc(
            "bet9ja", "Some Other Match", "2026-10-24 14:00:00"))


class RegistryTests(unittest.TestCase):
    def test_non_football_sport_rejected_even_with_football_market_string(self):
        result = registry.classify("1X2", "Handball")
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reason, "unsupported_sport")

    def test_1x2_supported(self):
        result = registry.classify("1X2", "Football")
        self.assertEqual(result.status, "supported")
        self.assertEqual(result.rule_id, "regulation_1x2")
        self.assertIs(result.evaluator, football.regulation_1x2)

    def test_soccer_sport_label_also_accepted(self):
        result = registry.classify("Over/Under", "Soccer")
        self.assertEqual(result.status, "supported")

    def test_handicap_unsupported_market(self):
        result = registry.classify("Handicap", "Football")
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reason, "unsupported_market")

    def test_early_goals_unsupported_incomplete_timeline(self):
        result = registry.classify("Over/Under - Early Goals", "Football")
        self.assertEqual(result.reason, "incomplete_timeline")

    def test_corners_statistic_market_unsupported(self):
        result = registry.classify("Corners Over/Under", "Football")
        self.assertEqual(result.status, "unsupported")

    def test_per_team_over_under_supported(self):
        result = registry.classify("Chelsea Over/Under", "Football")
        self.assertEqual(result.status, "supported")
        self.assertEqual(result.rule_id, "team_total_over_under")

    def test_unknown_market_unsupported(self):
        result = registry.classify("Some New Market Nobody Has Seen", "Football")
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reason, "unsupported_market")


if __name__ == "__main__":
    unittest.main()
