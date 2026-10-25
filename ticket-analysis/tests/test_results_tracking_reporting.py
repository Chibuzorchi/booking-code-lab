"""F9 step 4: ticket aggregation precedence (section 6) and percentage
accounting (section 7). Pure-function tests; no fixtures/matching/network.
"""
import unittest

from portfolio.results_tracking.reporting import (
    LegOutcome, Outcome, TicketCounts, aggregate_ticket, compute_percentages,
    counts_from_ticket_outcomes,
)


def _leg(status, leg_id="leg", rule_id="over_under", reason=None):
    return LegOutcome(leg_id=leg_id, status=status, rule_id=rule_id, reason=reason)


class AggregateTicketTests(unittest.TestCase):
    def test_no_legs_is_unresolved(self):
        outcome = aggregate_ticket([], structure_verified=True)
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "no_legs")

    def test_unverified_structure_is_unresolved_even_if_all_legs_won(self):
        # Section 6: "Unknown types must not become assumed accumulators."
        # All-wins must not be promoted to a confirmed ticket win when the
        # ticket structure itself is unverified.
        legs = [_leg(Outcome.WON, "l1"), _leg(Outcome.WON, "l2")]
        outcome = aggregate_ticket(legs, structure_verified=False)
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "unverified_ticket_structure")
        self.assertTrue(outcome.proxy_all_legs_won)

    def test_any_lost_leg_gives_lost_even_with_pending_and_unresolved(self):
        legs = [_leg(Outcome.LOST, "l1"), _leg(Outcome.PENDING, "l2"),
                _leg(Outcome.UNRESOLVED, "l3")]
        outcome = aggregate_ticket(legs, structure_verified=True)
        self.assertEqual(outcome.status, Outcome.LOST)
        self.assertFalse(outcome.proxy_all_legs_won)

    def test_unresolved_leg_beats_pending_when_no_loss(self):
        legs = [_leg(Outcome.PENDING, "l1"), _leg(Outcome.UNRESOLVED, "l2")]
        outcome = aggregate_ticket(legs, structure_verified=True)
        self.assertEqual(outcome.status, Outcome.UNRESOLVED)
        self.assertEqual(outcome.reason, "unresolved_leg_present")

    def test_pending_leg_with_no_loss_or_unresolved(self):
        legs = [_leg(Outcome.WON, "l1"), _leg(Outcome.PENDING, "l2")]
        outcome = aggregate_ticket(legs, structure_verified=True)
        self.assertEqual(outcome.status, Outcome.PENDING)
        self.assertEqual(outcome.reason, "pending_leg_present")

    def test_all_void_gives_void(self):
        legs = [_leg(Outcome.VOID, "l1"), _leg(Outcome.VOID, "l2")]
        outcome = aggregate_ticket(legs, structure_verified=True)
        self.assertEqual(outcome.status, Outcome.VOID)

    def test_won_and_void_mix_gives_won(self):
        legs = [_leg(Outcome.WON, "l1"), _leg(Outcome.VOID, "l2")]
        outcome = aggregate_ticket(legs, structure_verified=True)
        self.assertEqual(outcome.status, Outcome.WON)
        self.assertTrue(outcome.proxy_all_legs_won is False)  # not every leg itself WON

    def test_all_won_gives_won_and_proxy_true(self):
        legs = [_leg(Outcome.WON, "l1"), _leg(Outcome.WON, "l2")]
        outcome = aggregate_ticket(legs, structure_verified=True)
        self.assertEqual(outcome.status, Outcome.WON)
        self.assertTrue(outcome.proxy_all_legs_won)

    def test_legs_retained_on_output_for_audit(self):
        legs = [_leg(Outcome.LOST, "l1")]
        outcome = aggregate_ticket(legs, structure_verified=True)
        self.assertEqual(outcome.legs, tuple(legs))


class CountsFromTicketOutcomesTests(unittest.TestCase):
    def test_totals_conserve_row_count(self):
        legs_won = [_leg(Outcome.WON, "l1")]
        legs_lost = [_leg(Outcome.LOST, "l1")]
        outcomes = ([aggregate_ticket(legs_won, structure_verified=True)] * 2
                    + [aggregate_ticket(legs_lost, structure_verified=True)] * 3
                    + [aggregate_ticket([], structure_verified=True)])
        counts = counts_from_ticket_outcomes(outcomes)
        self.assertEqual(counts.total, len(outcomes))
        self.assertEqual(counts.won, 2)
        self.assertEqual(counts.lost, 3)
        self.assertEqual(counts.unresolved, 1)


class PercentageReportTests(unittest.TestCase):
    def test_section_7_worked_example(self):
        # N=100, W=2, L=60, V=5, P=20, U=13 -> 2% confirmed wins, 3.23%
        # decided win/loss rate, 67% classified, 2%-35% eventual bounds.
        counts = TicketCounts(won=2, lost=60, void=5, pending=20, unresolved=13)
        self.assertEqual(counts.total, 100)
        report = compute_percentages(counts)
        self.assertAlmostEqual(report.confirmed_win_rate.value, 0.02)
        self.assertAlmostEqual(report.decided_win_loss_rate.value, 2 / 62, places=6)
        self.assertAlmostEqual(report.classification_coverage.value, 0.67)
        self.assertAlmostEqual(report.eventual_binary_win_bound_low.value, 0.02)
        self.assertAlmostEqual(report.eventual_binary_win_bound_high.value, 0.35)

    def test_zero_denominator_gives_null_value_with_reason(self):
        counts = TicketCounts(won=0, lost=0, void=0, pending=0, unresolved=0)
        report = compute_percentages(counts)
        self.assertIsNone(report.confirmed_win_rate.value)
        self.assertEqual(report.confirmed_win_rate.reason, "zero_denominator_no_retained_tickets")
        self.assertIsNone(report.decided_win_loss_rate.value)
        self.assertEqual(report.decided_win_loss_rate.reason, "zero_denominator_no_decided_tickets")

    def test_zero_decided_denominator_with_nonzero_population(self):
        # All tickets pending/unresolved/void -- no won/lost at all -- the
        # decided win/loss rate must be null, not a misleading 0/0 -> 0%.
        counts = TicketCounts(won=0, lost=0, void=1, pending=1, unresolved=1)
        report = compute_percentages(counts)
        self.assertIsNotNone(report.confirmed_win_rate.value)
        self.assertEqual(report.confirmed_win_rate.value, 0.0)
        self.assertIsNone(report.decided_win_loss_rate.value)


if __name__ == "__main__":
    unittest.main()
