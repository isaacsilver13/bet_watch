from datetime import date

import pytest
from app.analytics.portfolio import (
    PaperCandidate,
    PaperPolicy,
    allocate_day,
    allocate_shadow_profiles,
    candidates_from_report,
)

ON = date(2026, 10, 22)


def candidate(name: str, game: int, win: float = 0.65, book: str = "betmgm") -> PaperCandidate:
    return PaperCandidate(name, game, book, -110, win, 1 - win, "forecast-v1", f"quote-{name}")


def test_all_and_top_n_rank_deterministically_and_keep_independent_bankrolls() -> None:
    candidates = [candidate("b", 2, 0.65), candidate("a", 1, 0.67), candidate("c", 3, 0.64)]
    profiles = allocate_shadow_profiles(candidates, ON)
    assert len(profiles) == 11
    assert all(profile.opening_bankroll == 1000 for profile in profiles)
    assert [bet.candidate_id for bet in profiles[0].bets] == ["a", "b", "c"]
    assert [bet.candidate_id for bet in profiles[1].bets] == ["a"]
    assert [bet.candidate_id for bet in profiles[2].bets] == ["a", "b"]
    assert profiles[0].policy_version == "paper-v1"
    assert allocate_day(list(reversed(candidates)), ON, 1000) == profiles[0]


def test_quarter_kelly_and_simultaneous_exposure_caps() -> None:
    policy = PaperPolicy(max_bet_fraction=0.03, max_game_fraction=0.05, max_day_fraction=0.08)
    bets = allocate_day([candidate("a", 1, 0.8), candidate("b", 1, 0.79),
                         candidate("c", 2, 0.78), candidate("d", 3, 0.77)], ON, 1000, policy)
    assert [(bet.candidate_id, bet.stake) for bet in bets.bets] == [
        ("a", 30), ("b", 20), ("c", 30)]
    assert ("d", "below_minimum_or_cap") in bets.excluded
    assert sum(bet.stake for bet in bets.bets) == 80


def test_fee_is_rechecked_at_actual_stake_and_can_exclude_a_bet() -> None:
    # The $25 reference quote qualifies after the FanDuel waiver, but a small
    # quarter-Kelly stake would incur a $0.50 fee and fall below the 3% bar.
    pick = candidate("fd", 1, 0.55, "fanduel")
    allocation = allocate_day([pick], ON, 1000)
    assert allocation.bets == ()
    assert allocation.excluded == (("fd", "below_actual_ev"),)


def test_top_n_fills_from_next_eligible_candidate_and_no_fee_on_push_is_assumed() -> None:
    candidates = [candidate("fd", 1, 0.55, "fanduel"), candidate("clean", 2, 0.55)]
    picked = allocate_day(candidates, ON, 1000, top_n=1)
    assert [bet.candidate_id for bet in picked.bets] == ["clean"]
    assert ("fd", "below_actual_ev") in picked.excluded
    push = PaperCandidate("push", 3, "betmgm", 110, 0.51, 0.39,
                          "forecast-v1", "quote-push")
    bet = allocate_day([push], ON, 1000).bets[0]
    assert bet.net_ev == pytest.approx(0.51 * 1.1 - 0.39)


def test_invalid_inputs_and_duplicate_identity_are_rejected() -> None:
    with pytest.raises(ValueError):
        PaperCandidate("bad", 1, "betmgm", 0, 0.6, 0.4, "forecast-v1", "quote-bad")
    with pytest.raises(ValueError):
        PaperCandidate("bad", 1, "betmgm", -110, 0.7, 0.4, "forecast-v1", "quote-bad")
    with pytest.raises(ValueError):
        allocate_day([candidate("x", 1), candidate("x", 2)], ON, 1000)
    with pytest.raises(ValueError):
        allocate_day([], ON, 0)
    with pytest.raises(ValueError):
        PaperPolicy(max_game_fraction=0.02)


def test_fixed_fee_counts_against_every_exposure_cap() -> None:
    policy = PaperPolicy(max_bet_fraction=0.1, max_game_fraction=0.1,
                         max_day_fraction=0.1, min_stake=0.1)
    high_edge = PaperCandidate("fd", 1, "fanduel", 300, 0.9, 0.1,
                               "forecast-v1", "quote-fd")
    bet = allocate_day([high_edge], ON, 10, policy).bets[0]
    assert bet.stake == 0.5
    assert bet.fee == 0.5
    assert bet.stake + bet.fee == pytest.approx(10 * policy.max_day_fraction)


def test_only_current_validated_moneylines_and_mapped_books_enter_paper_profiles() -> None:
    with pytest.raises(ValueError):
        PaperCandidate("offshore", 1, "Bovada", 110, 0.65, 0.35,
                       "forecast-v1", "quote-offshore")
    with pytest.raises(ValueError):
        PaperCandidate("watchlist", 1, "betmgm", 110, 0.65, 0.35,
                       "forecast-v1", "quote-watchlist", market="spread", tier="watchlist")


def test_allocation_records_full_policy_for_replay() -> None:
    default = allocate_day([candidate("a", 1)], ON, 1000)
    changed = allocate_day([candidate("a", 1)], ON, 1000,
                           PaperPolicy(max_bet_fraction=0.02))
    assert default.policy != changed.policy
    assert default.policy.max_bet_fraction == 0.03
    assert changed.policy.max_bet_fraction == 0.02


def test_saved_report_requires_exact_quote_and_forecast_provenance() -> None:
    report = {
        "kind": "nba_daily_report", "slate_date": "2026-10-22",
        "forecast_sha256": "a" * 64,
        "picks": [{"tier": "targeted", "market": "moneyline", "game_id": 1,
                   "book": "FanDuel", "price_american": -110,
                   "paper_win_prob": 0.65, "quote_observation_id": 42}],
    }
    candidates = candidates_from_report(report)
    assert len(candidates) == 1
    assert candidates[0].book == "fanduel"
    assert candidates[0].quote_id == "42"
    assert allocate_shadow_profiles(candidates, ON)[0].bets
    with pytest.raises(ValueError, match="provenance"):
        candidates_from_report({**report, "forecast_sha256": None})
    with pytest.raises(ValueError, match="provenance"):
        candidates_from_report({**report, "picks": [
            {**report["picks"][0], "quote_observation_id": None}
        ]})
