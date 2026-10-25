"""Parse a user-downloaded football-data.co.uk CSV. No network access here.

football-data.co.uk's terms forbid bot/scraper/AI access, so this module
never fetches anything itself -- the user downloads the file by hand from
the site and hands its local path to import_football_data_csv(). Every
field this parser can't resolve without guessing (a malformed date, a
non-blank non-integer score, a half-time score with no full-time score)
raises FootballDataImportError rather than being silently repaired.
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..rules.scores import InvalidScoreError, RegulationScore

REQUIRED_COLUMNS = ("Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG")


class FootballDataImportError(ValueError):
    """The CSV is missing a required column, or a row can't be parsed
    without guessing a fact."""


@dataclass(frozen=True)
class FootballDataFixture:
    division: str
    date: date
    home_team: str
    away_team: str
    score: RegulationScore | None  # None: not yet played, per this file


@dataclass(frozen=True)
class FootballDataImport:
    division: str
    season: str
    downloaded_at: str
    source_file: str
    sha256: str
    fixtures: tuple[FootballDataFixture, ...]


def _required_field(row: dict, name: str, row_number: int) -> str:
    """csv.DictReader pads a row shorter than the header with None for the
    trailing columns; a bare row[name] would then crash with AttributeError
    on the first .strip() call instead of raising the documented import
    error."""
    value = row.get(name)
    if value is None:
        raise FootballDataImportError(
            f"row {row_number}: missing value for required column {name!r} "
            "(row is shorter than the header)")
    return value


def _parse_date(raw: str, row_number: int) -> date:
    parts = raw.strip().split("/")
    if len(parts) != 3:
        raise FootballDataImportError(f"row {row_number}: unparseable Date {raw!r}")
    day, month, year = parts
    if len(year) == 2:
        # football-data.co.uk's older season files use a 2-digit year; every
        # season this project targets falls in the 2000s, so "YY" -> "20YY".
        year = f"20{year}"
    try:
        return date(int(year), int(month), int(day))
    except ValueError as exc:
        raise FootballDataImportError(f"row {row_number}: unparseable Date {raw!r}") from exc


def _parse_goal_count(raw: str, field_name: str, row_number: int) -> int | None:
    raw = raw.strip()
    if raw == "":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise FootballDataImportError(
            f"row {row_number}: {field_name} must be an int or blank, got {raw!r}") from exc
    if value < 0:
        raise FootballDataImportError(f"row {row_number}: {field_name} must be non-negative, got {value}")
    return value


def import_football_data_csv(path: Path, *, division: str, season: str,
                              downloaded_at: str) -> FootballDataImport:
    """path must already exist -- the user downloaded it by hand.

    division/season/downloaded_at are supplied by the caller as provenance,
    not inferred from the filename or the file's own Div column (which may
    not agree with what the user actually intended to import).
    """
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        raise FootballDataImportError(f"{path}: no header row found")
    missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise FootballDataImportError(f"{path}: missing required column(s) {missing}")

    fixtures = []
    for row_number, row in enumerate(reader, start=2):  # header is row 1
        if not any((row.get(c) or "").strip() for c in REQUIRED_COLUMNS):
            continue  # blank trailing line
        match_date = _parse_date(_required_field(row, "Date", row_number), row_number)
        home_team = _required_field(row, "HomeTeam", row_number).strip()
        away_team = _required_field(row, "AwayTeam", row_number).strip()
        if not home_team or not away_team:
            raise FootballDataImportError(f"row {row_number}: missing HomeTeam/AwayTeam")
        ft_home = _parse_goal_count(_required_field(row, "FTHG", row_number), "FTHG", row_number)
        ft_away = _parse_goal_count(_required_field(row, "FTAG", row_number), "FTAG", row_number)
        if (ft_home is None) != (ft_away is None):
            raise FootballDataImportError(
                f"row {row_number}: FTHG and FTAG must both be known or both be blank")
        ht_home = _parse_goal_count(row.get("HTHG") or "", "HTHG", row_number)
        ht_away = _parse_goal_count(row.get("HTAG") or "", "HTAG", row_number)

        score = None
        if ft_home is not None:
            try:
                score = RegulationScore(ft_home, ft_away, ht_home, ht_away)
            except InvalidScoreError as exc:
                raise FootballDataImportError(f"row {row_number}: {exc}") from exc
        elif ht_home is not None or ht_away is not None:
            # R08: an away-only (or home-only) half-time score without a
            # full-time score is just as unresolvable as a paired one.
            raise FootballDataImportError(
                f"row {row_number}: half-time score present without a full-time score")

        fixtures.append(FootballDataFixture(
            division=_required_field(row, "Div", row_number).strip(), date=match_date,
            home_team=home_team, away_team=away_team, score=score))

    return FootballDataImport(
        division=division, season=season, downloaded_at=downloaded_at,
        source_file=str(path), sha256=sha256, fixtures=tuple(fixtures))
