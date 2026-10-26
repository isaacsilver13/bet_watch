import pytest
from app.reports.nba_digest import render_nba_digest

REPORT = {
    "kind": "nba_daily_report", "slate_date": "2026-10-22",
    "odds_as_of": "2026-10-22T15:00:00+00:00",
    "games": [{"away": "PHI", "home": "CLE", "p_home_win": 0.64,
               "pred_margin": 4.2, "market_home_win_prob": 0.58}],
    "picks": [{"tier": "targeted", "away": "PHI", "home": "CLE", "team": "CLE",
               "book": "betmgm", "price_american": -110,
               "fanduel_price_american": -115, "ev": 0.05},
              {"tier": "watchlist", "away": "PHI", "home": "CLE", "team": "PHI",
               "book": "betmgm", "price_american": 110, "ev": 0.10}],
}


def test_digest_renders_saved_values_and_only_targeted_picks() -> None:
    digest = render_nba_digest(REPORT, "https://private.example/nba/report/2026-10-22")
    assert digest.subject == "NBA outlook — 2026-10-22"
    assert "CLE" in digest.html and "64.0%" in digest.html and "+4.2" in digest.html
    assert "58.0%" in digest.html and "5.0%" in digest.html
    assert "FanDuel -115" in digest.text
    assert "Selected price" in digest.html
    assert "Best price" not in digest.html
    assert "10.0%" not in digest.html and "10.0%" not in digest.text
    assert "width:64px" in digest.html
    assert "https://private.example/nba/report/2026-10-22" in digest.text


def test_digest_has_no_picks_state_and_escapes_dynamic_content() -> None:
    report = {**REPORT, "games": [{**REPORT["games"][0], "home": "<CLE>"}], "picks": []}
    digest = render_nba_digest(report)
    assert "No value picks cleared" in digest.html and "No value picks cleared" in digest.text
    assert "&lt;CLE&gt;" in digest.html and "<CLE>" not in digest.html


def test_digest_rejects_no_game_days_and_unsafe_links() -> None:
    with pytest.raises(ValueError):
        render_nba_digest({**REPORT, "games": []})
    with pytest.raises(ValueError):
        render_nba_digest(REPORT, "javascript:alert(1)")
    with pytest.raises(ValueError):
        render_nba_digest({key: value for key, value in REPORT.items() if key != "picks"})
    with pytest.raises(ValueError):
        render_nba_digest({**REPORT, "picks": None})
