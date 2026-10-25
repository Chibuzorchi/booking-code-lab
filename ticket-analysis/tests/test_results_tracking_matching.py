"""F9 step 4: fixture matching tier order, aliasing, orientation and
ambiguity. Pure-function tests against constructed Fixture objects; no
network, no real run directories needed.
"""
import unittest
from datetime import datetime, timedelta, timezone

from portfolio.results_tracking.fixtures import (
    Fixture, FixtureProvenance, FixtureState, KickoffTime,
)
from portfolio.results_tracking.matching import (
    Alias, MatchingError, MatchStatus, VerifiedMapping, match_selection_event,
    split_event_display, validate_candidate_fixtures, validate_verified_mappings,
)


def _fixture(home, away, competition="Premier League", source="api_football",
             source_fixture_id="1", kickoff=None):
    kickoff = kickoff or KickoffTime(raw="2026-10-24", precision="date_only")
    return Fixture(
        provenance=FixtureProvenance(source=source, source_fixture_id=source_fixture_id,
                                      fetched_at="2026-10-24T12:00:00Z", content_sha256="a" * 64),
        competition=competition, season="2026", home_team=home, away_team=away,
        kickoff=kickoff, state=FixtureState.SCHEDULED)


def _aliases(*, provider="sportybet", source="api_football", competition_display="England · Premier League",
             competition_canonical="Premier League", team_pairs=()):
    aliases = [
        Alias(f"{provider}_competition", competition_display, competition_canonical),
        Alias(f"{source}_competition", "Premier League", competition_canonical),
    ]
    for provider_display, source_display, canonical in team_pairs:
        aliases.append(Alias(f"{provider}_team", provider_display, canonical))
        aliases.append(Alias(f"{source}_team", source_display, canonical))
    return aliases


class SplitEventDisplayTests(unittest.TestCase):
    def test_simple_split(self):
        self.assertEqual(split_event_display("Arsenal - Everton"), ("Arsenal", "Everton"))

    def test_no_separator_rejected(self):
        with self.assertRaises(MatchingError):
            split_event_display("Arsenal vs Everton")

    def test_multiple_separators_rejected(self):
        with self.assertRaises(MatchingError):
            split_event_display("Real Madrid - Deportivo - Spain")


class ExactTierTests(unittest.TestCase):
    def test_unique_exact_match(self):
        fixture = _fixture("Arsenal FC", "Everton FC")
        aliases = _aliases(team_pairs=[("Arsenal", "Arsenal FC", "Arsenal FC (men)"),
                                        ("Everton", "Everton FC", "Everton FC (men)")])
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[fixture], aliases=aliases)
        self.assertEqual(decision.status, MatchStatus.MATCHED)
        self.assertIs(decision.fixture, fixture)
        self.assertEqual(decision.tier, "exact_unique")

    def test_reversed_orientation_does_not_match(self):
        # Section 5: retaining orientation; reversing needs a verified
        # mapping, never a silent swap in the exact tier.
        fixture = _fixture("Everton FC", "Arsenal FC")  # swapped vs. selection
        aliases = _aliases(team_pairs=[("Arsenal", "Arsenal FC", "Arsenal FC (men)"),
                                        ("Everton", "Everton FC", "Everton FC (men)")])
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[fixture], aliases=aliases)
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)

    def test_women_variant_does_not_cross_match_mens_alias(self):
        # Section 5: women/youth/reserve/national distinctions are preserved
        # -- "Arsenal" and "Arsenal Women" must resolve to different
        # canonical identities, never merged.
        fixture = _fixture("Arsenal WFC", "Everton FC")
        aliases = _aliases(team_pairs=[
            ("Arsenal", "Arsenal FC", "Arsenal FC (men)"),
            ("Arsenal Women", "Arsenal WFC", "Arsenal WFC (women)"),
            ("Everton", "Everton FC", "Everton FC (men)"),
        ])
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[fixture], aliases=aliases)
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)

    def test_missing_alias_falls_through_to_fuzzy_not_accepted(self):
        # No alias means tier 2 can't accept anything; "Arsenal"/"Arsenal FC"
        # are similar enough to surface as a fuzzy candidate, but that is
        # explicitly not an accepted match.
        fixture = _fixture("Arsenal FC", "Everton FC")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[fixture], aliases=[])
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)
        self.assertIsNone(decision.fixture)

    def test_multiple_plausible_fixtures_are_ambiguous(self):
        aliases = _aliases(team_pairs=[("Arsenal", "Arsenal FC", "Arsenal FC (men)"),
                                        ("Everton", "Everton FC", "Everton FC (men)")])
        f1 = _fixture("Arsenal FC", "Everton FC", source_fixture_id="1")
        f2 = _fixture("Arsenal FC", "Everton FC", source_fixture_id="2")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[f1, f2], aliases=aliases)
        self.assertEqual(decision.status, MatchStatus.AMBIGUOUS)
        self.assertEqual(len(decision.candidates), 2)
        self.assertIsNone(decision.fixture)

    def test_different_competition_alias_does_not_match(self):
        # "Similar competitions" guard: the fixture's own competition must
        # also resolve to the same canonical name, not just share a string.
        fixture = _fixture("Arsenal FC", "Everton FC", competition="Championship")
        aliases = [
            Alias("sportybet_competition", "England · Premier League", "Premier League"),
            Alias("api_football_competition", "Championship", "Championship"),
            Alias("sportybet_team", "Arsenal", "Arsenal FC (men)"),
            Alias("api_football_team", "Arsenal FC", "Arsenal FC (men)"),
            Alias("sportybet_team", "Everton", "Everton FC (men)"),
            Alias("api_football_team", "Everton FC", "Everton FC (men)"),
        ]
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[fixture], aliases=aliases)
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)


class KickoffConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.aliases = _aliases(team_pairs=[("Arsenal", "Arsenal FC", "Arsenal FC (men)"),
                                             ("Everton", "Everton FC", "Everton FC (men)")])

    def test_exact_kickoff_within_tolerance_matches(self):
        fixture_kickoff = KickoffTime(raw="2026-10-24T14:00:00+00:00", precision="exact",
                                       utc=datetime(2026, 10, 24, 14, 0, tzinfo=timezone.utc),
                                       timezone_evidence="source reports UTC offset directly")
        fixture = _fixture("Arsenal FC", "Everton FC", kickoff=fixture_kickoff)
        selection_kickoff = KickoffTime(
            raw="2026-10-24T14:10:00+00:00", precision="exact",
            utc=datetime(2026, 10, 24, 14, 10, tzinfo=timezone.utc),
            timezone_evidence="provider reports UTC offset directly")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League", kickoff=selection_kickoff,
            candidate_fixtures=[fixture], aliases=self.aliases)
        self.assertEqual(decision.status, MatchStatus.MATCHED)

    def test_exact_kickoff_outside_tolerance_does_not_match(self):
        fixture_kickoff = KickoffTime(raw="2026-10-24T14:00:00+00:00", precision="exact",
                                       utc=datetime(2026, 10, 24, 14, 0, tzinfo=timezone.utc),
                                       timezone_evidence="source reports UTC offset directly")
        fixture = _fixture("Arsenal FC", "Everton FC", kickoff=fixture_kickoff)
        selection_kickoff = KickoffTime(
            raw="2026-10-24T14:30:00+00:00", precision="exact",
            utc=datetime(2026, 10, 24, 14, 30, tzinfo=timezone.utc),
            timezone_evidence="provider reports UTC offset directly")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League", kickoff=selection_kickoff,
            candidate_fixtures=[fixture], aliases=self.aliases,
            tolerance=timedelta(minutes=15))
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)

    def test_date_only_same_day_matches(self):
        fixture = _fixture("Arsenal FC", "Everton FC",
                            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"))
        selection_kickoff = KickoffTime(raw="2026-10-24", precision="date_only")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League", kickoff=selection_kickoff,
            candidate_fixtures=[fixture], aliases=self.aliases)
        self.assertEqual(decision.status, MatchStatus.MATCHED)

    def test_postponed_fixture_on_different_date_does_not_match(self):
        # Section 5: postponements/rematches require identity and schedule
        # history (tier 1), not an unbounded search around the date.
        fixture = _fixture("Arsenal FC", "Everton FC",
                            kickoff=KickoffTime(raw="2026-11-02", precision="date_only"))
        selection_kickoff = KickoffTime(raw="2026-10-24", precision="date_only")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League", kickoff=selection_kickoff,
            candidate_fixtures=[fixture], aliases=self.aliases)
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)


class VerifiedMappingTierTests(unittest.TestCase):
    def test_verified_mapping_matches_when_consistent(self):
        fixture = _fixture("Arsenal FC", "Everton FC")
        mapping = VerifiedMapping(
            provider="sportybet", provider_event_id="sr:match:1",
            league_display="England · Premier League", home_display="Arsenal",
            away_display="Everton", source="api_football", source_fixture_id="1",
            source_competition="Premier League", source_home_team="Arsenal FC",
            source_away_team="Everton FC",
            source_kickoff_raw="2026-10-24",
            verified_at="2026-10-23T00:00:00Z", evidence="hand-verified in step 2")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="sr:match:1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[fixture], verified_mappings=[mapping])
        self.assertEqual(decision.status, MatchStatus.MATCHED)
        self.assertEqual(decision.tier, "verified_mapping")
        self.assertIs(decision.fixture, fixture)

    def test_verified_mapping_fails_recheck_on_changed_league_display(self):
        # Section 5: a mapping is not a blank cheque -- the current
        # inputs are rechecked against what was actually verified.
        fixture = _fixture("Arsenal FC", "Everton FC")
        mapping = VerifiedMapping(
            provider="sportybet", provider_event_id="sr:match:1",
            league_display="England · Premier League", home_display="Arsenal",
            away_display="Everton", source="api_football", source_fixture_id="1",
            source_competition="Premier League", source_home_team="Arsenal FC",
            source_away_team="Everton FC",
            source_kickoff_raw="2026-10-24",
            verified_at="2026-10-23T00:00:00Z", evidence="hand-verified in step 2")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="sr:match:1", event_display="Arsenal - Everton",
            league_display="Kazakhstan · Premier League",  # changed since verification
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[fixture], verified_mappings=[mapping])
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)
        self.assertEqual(decision.reason, "verified_mapping_failed_consistency_recheck")

    def test_verified_mapping_fixture_not_supplied(self):
        mapping = VerifiedMapping(
            provider="sportybet", provider_event_id="sr:match:1",
            league_display="England · Premier League", home_display="Arsenal",
            away_display="Everton", source="api_football", source_fixture_id="1",
            source_competition="Premier League", source_home_team="Arsenal FC",
            source_away_team="Everton FC",
            source_kickoff_raw="2026-10-24",
            verified_at="2026-10-23T00:00:00Z", evidence="hand-verified in step 2")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="sr:match:1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[], verified_mappings=[mapping])
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)
        self.assertEqual(decision.reason, "verified_mapping_fixture_not_supplied")

    def test_mapping_for_different_provider_event_id_is_ignored(self):
        fixture = _fixture("Arsenal FC", "Everton FC")
        mapping = VerifiedMapping(
            provider="sportybet", provider_event_id="sr:match:999",
            league_display="England · Premier League", home_display="Arsenal",
            away_display="Everton", source="api_football", source_fixture_id="1",
            source_competition="Premier League", source_home_team="Arsenal FC",
            source_away_team="Everton FC",
            source_kickoff_raw="2026-10-24",
            verified_at="2026-10-23T00:00:00Z", evidence="hand-verified in step 2")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="sr:match:1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[fixture], verified_mappings=[mapping])
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)

    def test_verified_mapping_rejects_target_fixture_that_changed_since_verification(self):
        # F9_STEP4_REVIEW.md S4-01 reproduction: the mapping was verified
        # against an Arsenal-Everton fixture, but the (source,
        # source_fixture_id) key now resolves to a different match entirely.
        changed_fixture = _fixture("Chelsea FC", "Fulham FC", competition="Wrong League")
        mapping = VerifiedMapping(
            provider="sportybet", provider_event_id="sr:match:1",
            league_display="England · Premier League", home_display="Arsenal",
            away_display="Everton", source="api_football", source_fixture_id="1",
            source_competition="Premier League", source_home_team="Arsenal FC",
            source_away_team="Everton FC",
            source_kickoff_raw="2026-10-24",
            verified_at="2026-10-23T00:00:00Z", evidence="hand-verified in step 2")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="sr:match:1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[changed_fixture], verified_mappings=[mapping])
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)
        self.assertEqual(decision.reason, "verified_mapping_target_fixture_changed")

    def test_verified_mapping_rejects_reversed_orientation(self):
        # The comments previously implied reversed orientation could pass
        # tier 1; it must not -- a fixture with home/away swapped relative
        # to the mapping's verified evidence fails the exact-equality
        # recheck, never silently evaluated in the wrong orientation.
        reversed_fixture = _fixture("Everton FC", "Arsenal FC")
        mapping = VerifiedMapping(
            provider="sportybet", provider_event_id="sr:match:1",
            league_display="England · Premier League", home_display="Arsenal",
            away_display="Everton", source="api_football", source_fixture_id="1",
            source_competition="Premier League", source_home_team="Arsenal FC",
            source_away_team="Everton FC",
            source_kickoff_raw="2026-10-24",
            verified_at="2026-10-23T00:00:00Z", evidence="hand-verified in step 2")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="sr:match:1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[reversed_fixture], verified_mappings=[mapping])
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)
        self.assertEqual(decision.reason, "verified_mapping_target_fixture_changed")

    def test_verified_mapping_rejects_target_fixture_with_changed_kickoff_only(self):
        # F9_STEP4_REREVIEW.md S4R-01 reproduction: team/competition display
        # strings are left untouched, only the target fixture's kickoff has
        # drifted since verification (e.g. a reschedule to 2020). The tier-1
        # recheck must still reject it, never silently reuse the stale date.
        rescheduled_fixture = _fixture(
            "Arsenal FC", "Everton FC",
            kickoff=KickoffTime(raw="2020-01-01", precision="date_only"))
        mapping = VerifiedMapping(
            provider="sportybet", provider_event_id="sr:match:1",
            league_display="England · Premier League", home_display="Arsenal",
            away_display="Everton", source="api_football", source_fixture_id="1",
            source_competition="Premier League", source_home_team="Arsenal FC",
            source_away_team="Everton FC",
            source_kickoff_raw="2026-10-24",
            verified_at="2026-10-23T00:00:00Z", evidence="hand-verified in step 2")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="sr:match:1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[rescheduled_fixture], verified_mappings=[mapping])
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)
        self.assertEqual(decision.reason, "verified_mapping_target_fixture_changed")


class ConflictingEvidenceTests(unittest.TestCase):
    """F9_STEP4_REVIEW.md S4-02: contradictory evidence must raise, and must
    raise the same way regardless of list order (never 'first one wins')."""

    def test_conflicting_aliases_for_same_display_name_raise_regardless_of_order(self):
        forward = [
            Alias("sportybet_team", "Arsenal", "Arsenal"),
            Alias("sportybet_team", "Arsenal", "Chelsea"),
        ]
        backward = list(reversed(forward))
        for aliases in (forward, backward):
            with self.assertRaises(MatchingError):
                match_selection_event(
                    provider="sportybet", provider_event_id="1",
                    event_display="Arsenal - Everton",
                    league_display="England · Premier League",
                    kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
                    candidate_fixtures=[], aliases=aliases)

    def test_conflicting_fixture_observations_for_same_source_id_raise_regardless_of_order(self):
        fx_a = _fixture("Arsenal FC", "Everton FC")
        fx_b = _fixture("Chelsea FC", "Fulham FC")  # same default source_fixture_id="1"
        for candidate_fixtures in ([fx_a, fx_b], [fx_b, fx_a]):
            with self.assertRaises(MatchingError):
                match_selection_event(
                    provider="sportybet", provider_event_id="1",
                    event_display="Arsenal - Everton",
                    league_display="England · Premier League",
                    kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
                    candidate_fixtures=candidate_fixtures, aliases=[])

    def test_exact_duplicate_fixture_observations_are_not_a_conflict(self):
        fx = _fixture("Arsenal FC", "Everton FC")
        validate_candidate_fixtures([fx, fx])  # no raise

    def test_conflicting_verified_mappings_for_same_provider_event_raise_regardless_of_order(self):
        # F9_STEP4_REREVIEW.md S4R-02 reproduction: two individually
        # plausible verified mappings for the same (provider,
        # provider_event_id) target different source fixtures with opposing
        # results. This must raise deterministically, never "first wins".
        mapping_a = VerifiedMapping(
            provider="sportybet", provider_event_id="sr:match:1",
            league_display="England · Premier League", home_display="Arsenal",
            away_display="Everton", source="api_football", source_fixture_id="1",
            source_competition="Premier League", source_home_team="Arsenal FC",
            source_away_team="Everton FC",
            source_kickoff_raw="2026-10-24",
            verified_at="2026-10-23T00:00:00Z", evidence="hand-verified in step 2")
        mapping_b = VerifiedMapping(
            provider="sportybet", provider_event_id="sr:match:1",
            league_display="England · Premier League", home_display="Arsenal",
            away_display="Everton", source="api_football", source_fixture_id="2",
            source_competition="Premier League", source_home_team="Arsenal FC",
            source_away_team="Everton FC",
            source_kickoff_raw="2026-10-24",
            verified_at="2026-10-23T00:00:00Z", evidence="hand-verified in step 2 (rival claim)")
        fx_a = _fixture("Arsenal FC", "Everton FC", source_fixture_id="1")
        fx_b = _fixture("Arsenal FC", "Everton FC", source_fixture_id="2")
        for verified_mappings in ([mapping_a, mapping_b], [mapping_b, mapping_a]):
            with self.assertRaises(MatchingError):
                match_selection_event(
                    provider="sportybet", provider_event_id="sr:match:1",
                    event_display="Arsenal - Everton",
                    league_display="England · Premier League",
                    kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
                    candidate_fixtures=[fx_a, fx_b], verified_mappings=verified_mappings)

    def test_exact_duplicate_verified_mappings_are_not_a_conflict(self):
        mapping = VerifiedMapping(
            provider="sportybet", provider_event_id="sr:match:1",
            league_display="England · Premier League", home_display="Arsenal",
            away_display="Everton", source="api_football", source_fixture_id="1",
            source_competition="Premier League", source_home_team="Arsenal FC",
            source_away_team="Everton FC",
            source_kickoff_raw="2026-10-24",
            verified_at="2026-10-23T00:00:00Z", evidence="hand-verified in step 2")
        validate_verified_mappings([mapping, mapping])  # no raise


class FuzzyTierTests(unittest.TestCase):
    def test_fuzzy_candidates_proposed_but_not_accepted(self):
        fixture = _fixture("Arsenal FC", "Everton FC")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[fixture], aliases=[])
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)
        self.assertEqual(decision.tier, "fuzzy_review")
        self.assertEqual(len(decision.candidates), 1)
        self.assertIsNone(decision.fixture)

    def test_dissimilar_names_produce_no_fuzzy_candidates(self):
        fixture = _fixture("Bayern Munich", "Borussia Dortmund")
        decision = match_selection_event(
            provider="sportybet", provider_event_id="1", event_display="Arsenal - Everton",
            league_display="England · Premier League",
            kickoff=KickoffTime(raw="2026-10-24", precision="date_only"),
            candidate_fixtures=[fixture], aliases=[])
        self.assertEqual(decision.status, MatchStatus.UNMATCHED)
        self.assertEqual(decision.candidates, ())
        self.assertEqual(decision.reason, "no_candidate_fixtures")


if __name__ == "__main__":
    unittest.main()
