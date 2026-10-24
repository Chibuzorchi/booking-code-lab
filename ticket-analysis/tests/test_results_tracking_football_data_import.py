"""F9: manual football-data.co.uk CSV import. No network access is exercised
or permitted here -- every test writes its own local CSV file."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from portfolio.results_tracking.sources.football_data_import import (
    FootballDataImportError, import_football_data_csv,
)

HEADER = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR\n"


class FootballDataImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name, body):
        path = self.dir / name
        path.write_text(HEADER + body, encoding="utf-8")
        return path

    def test_played_and_unplayed_rows(self):
        path = self._write("e0.csv",
            "E0,15/08/2026,Arsenal,Everton,2,1,H,1,0,H\n"
            "E0,22/08/2026,Chelsea,Fulham,,,,,,\n")
        result = import_football_data_csv(path, division="E0", season="2026-27",
                                           downloaded_at="2026-10-24T09:00:00+00:00")
        self.assertEqual(len(result.fixtures), 2)
        played = result.fixtures[0]
        self.assertEqual(played.date, date(2026, 8, 15))
        self.assertEqual((played.score.home, played.score.away), (2, 1))
        self.assertEqual(played.score.second_half, (1, 1))
        unplayed = result.fixtures[1]
        self.assertIsNone(unplayed.score)

    def test_two_digit_year(self):
        path = self._write("e0_old.csv", "E0,15/08/09,Arsenal,Everton,2,1,H,,,\n")
        result = import_football_data_csv(path, division="E0", season="2009-10",
                                           downloaded_at="2026-10-24T09:00:00+00:00")
        self.assertEqual(result.fixtures[0].date, date(2009, 8, 15))

    def test_missing_required_column_raises(self):
        path = self.dir / "bad.csv"
        path.write_text("Div,Date,HomeTeam,AwayTeam\nE0,15/08/2026,Arsenal,Everton\n", encoding="utf-8")
        with self.assertRaises(FootballDataImportError):
            import_football_data_csv(path, division="E0", season="2026-27", downloaded_at="x")

    def test_malformed_date_raises(self):
        path = self._write("bad_date.csv", "E0,not-a-date,Arsenal,Everton,2,1,H,,,\n")
        with self.assertRaises(FootballDataImportError):
            import_football_data_csv(path, division="E0", season="2026-27", downloaded_at="x")

    def test_non_integer_score_raises(self):
        path = self._write("bad_score.csv", "E0,15/08/2026,Arsenal,Everton,two,1,H,,,\n")
        with self.assertRaises(FootballDataImportError):
            import_football_data_csv(path, division="E0", season="2026-27", downloaded_at="x")

    def test_half_time_without_full_time_raises(self):
        path = self._write("ht_only.csv", "E0,15/08/2026,Arsenal,Everton,,,,1,0,H\n")
        with self.assertRaises(FootballDataImportError):
            import_football_data_csv(path, division="E0", season="2026-27", downloaded_at="x")

    def test_half_time_exceeding_full_time_raises(self):
        path = self._write("bad_half.csv", "E0,15/08/2026,Arsenal,Everton,1,1,D,2,0,H\n")
        with self.assertRaises(FootballDataImportError):
            import_football_data_csv(path, division="E0", season="2026-27", downloaded_at="x")

    def test_provenance_recorded(self):
        path = self._write("prov.csv", "E0,15/08/2026,Arsenal,Everton,2,1,H,,,\n")
        result = import_football_data_csv(path, division="E0", season="2026-27",
                                           downloaded_at="2026-10-24T09:00:00+00:00")
        self.assertEqual(result.source_file, str(path))
        self.assertEqual(len(result.sha256), 64)
        self.assertEqual(result.downloaded_at, "2026-10-24T09:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
