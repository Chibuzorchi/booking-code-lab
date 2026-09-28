"""Phase 1 leg-level uniqueness: a code is distinct only when none of its legs
(game+option) appears in any other code."""
from portfolio.uniqueness import analyze, leg_usage, selection_key


def leg(event, market="1X2", pick="1"):
    return {"selection_key": f"{event}${market}_{pick}", "native_event_id": event,
            "market_id": market, "outcome_id": pick, "event": f"Game {event}",
            "market": market, "pick": pick}


def coupon(code, legs):
    return {"code": code, "selections": legs}


def test_leg_usage_counts_codes_per_leg():
    pool = [coupon("A", [leg("1"), leg("2")]), coupon("B", [leg("2"), leg("3")])]
    use = leg_usage(pool)
    assert use[selection_key(leg("2"))] == 2   # game 2 shared by both codes
    assert use[selection_key(leg("1"))] == 1


def test_same_game_different_option_is_not_a_shared_leg():
    # England home vs England draw -> different option -> distinct legs
    pool = [coupon("A", [leg("ENG", pick="1"), leg("X")]),
            coupon("B", [leg("ENG", pick="2"), leg("Y")])]
    r = analyze(pool)
    assert r["distinct_codes"] == 2
    assert r["overlapping_codes"] == 0


def test_shared_game_and_option_makes_both_codes_overlapping():
    pool = [coupon("A", [leg("ENG", pick="1"), leg("X")]),
            coupon("B", [leg("ENG", pick="1"), leg("Y")])]  # same game+option
    r = analyze(pool)
    assert r["distinct_codes"] == 0
    assert r["overlapping_codes"] == 2
    assert r["shared_legs"] == 1


def test_fully_distinct_code_survives_alongside_overlapping_ones():
    pool = [coupon("A", [leg("1"), leg("2")]),
            coupon("B", [leg("2"), leg("3")]),   # shares game 2 with A
            coupon("C", [leg("9"), leg("8")])]   # shares nothing
    r = analyze(pool)
    assert r["distinct_codes"] == 1              # only C
    assert [c["code"] for c in r["distinct"]] == ["C"]
    assert r["overlapping_codes"] == 2
