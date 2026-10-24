"""F9 step 4: shared Fixture/KickoffTime/FixtureProvenance model validation.

Pure-construction tests; no sources, no network, no run directories needed.
"""
import unittest
from datetime import datetime, timedelta, timezone

from portfolio.results_tracking.fixtures import (
    Fixture, FixtureProvenance, FixtureState, InvalidFixtureError, KickoffTime,
)
from portfolio.results_tracking.rules.scores import RegulationScore


def _provenance(**overrides):
    defaults = dict(source="api_football", source_fixture_id="12345",
                     fetched_at="2026-10-24T12:00:00Z", content_sha256="a" * 64)
    defaults.update(overrides)
    return FixtureProvenance(**defaults)


class KickoffTimeTests(unittest.TestCase):
    def test_date_only_without_utc_is_valid(self):
        kt = KickoffTime(raw="2026-10-24", precision="date_only")
        self.assertIsNone(kt.utc)

    def test_exact_with_verified_utc_is_valid(self):
        kt = KickoffTime(raw="2026-10-24 14:00:00", precision="exact",
                          utc=datetime(2026, 10, 24, 14, 0, tzinfo=timezone.utc),
                          timezone_evidence="cross-provider match to sportybet run X")
        self.assertEqual(kt.utc.tzinfo, timezone.utc)

    def test_empty_raw_rejected(self):
        with self.assertRaises(InvalidFixtureError):
            KickoffTime(raw="", precision="date_only")

    def test_unknown_precision_rejected(self):
        with self.assertRaises(InvalidFixtureError):
            KickoffTime(raw="2026-10-24", precision="approximate")

    def test_utc_without_timezone_evidence_rejected(self):
        # Section 5: an interpreted UTC time is never invented -- it must
        # carry the evidence that established it.
        with self.assertRaises(InvalidFixtureError):
            KickoffTime(raw="2026-10-24 14:00:00", precision="exact",
                        utc=datetime(2026, 10, 24, 14, 0, tzinfo=timezone.utc))

    def test_naive_utc_datetime_rejected(self):
        with self.assertRaises(InvalidFixtureError):
            KickoffTime(raw="2026-10-24 14:00:00", precision="exact",
                        utc=datetime(2026, 10, 24, 14, 0), timezone_evidence="assumed")

    def test_non_utc_offset_rejected(self):
        # F9_STEP4_REVIEW.md S4-08: a field named `utc` must actually be
        # UTC (offset zero), not merely any aware timezone -- otherwise a
        # later date-only comparison could read the wrong calendar date.
        with self.assertRaises(InvalidFixtureError):
            KickoffTime(raw="2026-10-25T00:30:00+02:00", precision="exact",
                        utc=datetime(2026, 10, 25, 0, 30, tzinfo=timezone(timedelta(hours=2))),
                        timezone_evidence="provider-documented +02:00")


class FixtureProvenanceTests(unittest.TestCase):
    def test_valid_provenance(self):
        prov = _provenance()
        self.assertEqual(prov.source, "api_football")

    def test_missing_field_rejected(self):
        with self.assertRaises(InvalidFixtureError):
            _provenance(source_fixture_id="")


class FixtureTests(unittest.TestCase):
    def _kickoff(self):
        return KickoffTime(raw="2026-10-24", precision="date_only")

    def test_scheduled_fixture_without_score_is_valid(self):
        fx = Fixture(provenance=_provenance(), competition="Premier League", season="2026",
                     home_team="Arsenal", away_team="Everton", kickoff=self._kickoff(),
                     state=FixtureState.SCHEDULED)
        self.assertFalse(fx.is_finished)

    def test_finished_regulation_with_score_is_valid(self):
        fx = Fixture(provenance=_provenance(), competition="Premier League", season="2026",
                     home_team="Arsenal", away_team="Everton", kickoff=self._kickoff(),
                     state=FixtureState.FINISHED_REGULATION,
                     regulation_score=RegulationScore(2, 1))
        self.assertTrue(fx.is_finished)

    def test_finished_extra_time_without_known_regulation_score_is_valid(self):
        # Section 6: a FINISHED_EXTRA_TIME state does not require (or imply)
        # a known 90-minute score -- api_football.py declines to report it.
        fx = Fixture(provenance=_provenance(), competition="Premier League", season="2026",
                     home_team="Arsenal", away_team="Everton", kickoff=self._kickoff(),
                     state=FixtureState.FINISHED_EXTRA_TIME)
        self.assertIsNone(fx.regulation_score)
        self.assertTrue(fx.is_finished)

    def test_score_before_finished_state_rejected(self):
        # A fixture cannot claim a final score before the source reports
        # regulation as concluded -- kickoff passing is not finality.
        with self.assertRaises(InvalidFixtureError):
            Fixture(provenance=_provenance(), competition="Premier League", season="2026",
                    home_team="Arsenal", away_team="Everton", kickoff=self._kickoff(),
                    state=FixtureState.IN_PLAY, regulation_score=RegulationScore(1, 0))

    def test_missing_team_name_rejected(self):
        with self.assertRaises(InvalidFixtureError):
            Fixture(provenance=_provenance(), competition="Premier League", season="2026",
                    home_team="", away_team="Everton", kickoff=self._kickoff(),
                    state=FixtureState.SCHEDULED)

    def test_missing_competition_rejected(self):
        with self.assertRaises(InvalidFixtureError):
            Fixture(provenance=_provenance(), competition="", season="2026",
                    home_team="Arsenal", away_team="Everton", kickoff=self._kickoff(),
                    state=FixtureState.SCHEDULED)


if __name__ == "__main__":
    unittest.main()
