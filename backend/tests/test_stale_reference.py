from datetime import date, datetime, timedelta, timezone

import pytest
from app.domain.forecasts import Predictions
from app.reports.stale_reference import ReferenceLine, build_reference_preview

DAY = date(2026, 10, 23)
GENERATED = datetime(2026, 10, 23, 14, tzinfo=timezone.utc)
NOW = GENERATED + timedelta(hours=1)
TIP = GENERATED + timedelta(hours=10)


def _forecast() -> Predictions:
    return Predictions.model_validate({
        "slate_date": DAY, "model_version": "test-model",
        "generated_at": GENERATED, "data_through": DAY - timedelta(days=1),
        "games": [{"game_date": DAY, "home": "MIL", "away": "CHI",
                   "pred_margin": 4.0, "p_home_win": 0.6, "sigma": 10.0}],
    })


def _line(**changes) -> ReferenceLine:
    values = dict(
        home="MIL", away="CHI", tip=TIP, captured_at=GENERATED - timedelta(hours=8),
        spread_home=-3.0, spread_home_price=-110, spread_away_price=-110,
        ml_home=-110, ml_away=-110,
    )
    values.update(changes)
    return ReferenceLine(**values)


def test_reference_preview_labels_stale_prices_and_never_offers_paper_bets() -> None:
    report = build_reference_preview(_forecast(), {("MIL", "CHI"): _line()}, NOW)
    game = report["games"][0]
    assert report["prices_are_actionable"] is False
    assert game["status"] == "reference_only"
    assert game["capture_age_hours"] == 9
    assert game["eligible_for_paper"] is False
    assert game["book_attribution"] == "unverified"
    assert {quote["market"] for quote in game["quotes"]} == {"moneyline", "spread"}
    home_ml = next(q for q in game["quotes"] if q["market"] == "moneyline"
                   and q["side"] == "home")
    assert home_ml["gross_ev_at_reference"] == pytest.approx(0.1455, abs=0.0001)
    home_spread = next(q for q in game["quotes"] if q["market"] == "spread"
                       and q["side"] == "home")
    assert home_spread["model_push_prob"] > 0


def test_reference_preview_excludes_missing_future_and_past_tip_prices() -> None:
    forecast = _forecast()
    assert build_reference_preview(forecast, {}, NOW)["games"][0]["status"] == (
        "no_saved_reference"
    )
    future = _line(captured_at=NOW + timedelta(minutes=1))
    assert build_reference_preview(forecast, {("MIL", "CHI"): future}, NOW)["games"][0][
        "quotes"
    ] == []
    assert build_reference_preview(forecast, {("MIL", "CHI"): _line()}, TIP)["games"][0][
        "status"
    ] == "past_tip"


def test_reference_preview_refuses_unstamped_decision_time() -> None:
    with pytest.raises(ValueError, match="time zone"):
        build_reference_preview(_forecast(), {}, NOW.replace(tzinfo=None))
