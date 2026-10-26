import asyncio
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.config import load_settings
from app.domain.forecasts import Predictions
from app.reports.forecast_artifact import PRIVATE_DATA_REPO, download_forecast, import_forecast
from app.storage import trial_branch
from app.storage.forecast_repository import ForecastConflict
from sqlalchemy.engine import make_url


def _payload(day: str) -> dict:
    return {
        "slate_date": day, "model_version": "test", "generated_at": f"{day}T14:00:00Z",
        "data_through": "2026-10-22",
        "games": [{"game_date": day, "home": "MIL", "away": "CHI",
                   "pred_margin": 4.0, "p_home_win": 0.6, "sigma": 11.0}],
    }


def test_downloads_only_the_named_private_release_asset(monkeypatch) -> None:
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        path = Path(argv[-1]) / "predictions-2026-10-23.json"
        path.write_text(json.dumps(_payload("2026-10-23")), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("app.reports.forecast_artifact.subprocess.run", fake_run)
    forecast = download_forecast(date(2026, 10, 23))
    assert forecast.slate_date == date(2026, 10, 23)
    assert seen[0][3:6] == ["state-2026-10-23", "-R", PRIVATE_DATA_REPO]
    assert seen[0][7] == "predictions-2026-10-23.json"


def test_rejects_a_forecast_from_another_slate(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        path = Path(argv[-1]) / "predictions-2026-10-23.json"
        path.write_text(json.dumps(_payload("2026-10-24")), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("app.reports.forecast_artifact.subprocess.run", fake_run)
    with pytest.raises(ValueError, match="different slate date"):
        download_forecast(date(2026, 10, 23))


def test_trial_database_target_refuses_an_unexpected_source_host(monkeypatch) -> None:
    settings = replace(load_settings(), database_url="postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setattr(trial_branch, "load_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="host changed"):
        trial_branch.trial_settings()


def test_trial_database_target_changes_only_the_host(monkeypatch) -> None:
    source = (
        "postgresql+asyncpg://u:p@"
        f"{trial_branch.PRODUCTION_HOST}/db?ssl=require"
    )
    monkeypatch.setattr(
        trial_branch, "load_settings",
        lambda: replace(load_settings(), database_url=source),
    )
    target = make_url(trial_branch.trial_settings().database_url)
    assert target.host == trial_branch.TRIAL_HOST
    assert (target.username, target.password, target.database) == ("u", "p", "db")


def test_identical_reimport_after_tip_is_safe_but_conflict_is_rejected(
    monkeypatch,
) -> None:
    class Engine:
        async def dispose(self):
            pass

    async def scenario() -> None:
        forecast = Predictions.model_validate(_payload("2026-10-23"))
        factory = object()

        async def existing(factory, day):
            return forecast

        async def store(factory, incoming):
            if incoming.model_version != forecast.model_version:
                raise ForecastConflict("forecast for this slate already differs")
            return False

        monkeypatch.setattr("app.reports.forecast_artifact.download_forecast", lambda day: forecast)
        monkeypatch.setattr("app.reports.forecast_artifact.trial_settings", lambda: None)
        monkeypatch.setattr("app.reports.forecast_artifact.get_forecast", existing)
        monkeypatch.setattr("app.reports.forecast_artifact.store_forecast", store)
        monkeypatch.setattr(
            "app.reports.forecast_artifact.create_engine", lambda settings: Engine()
        )
        monkeypatch.setattr(
            "app.reports.forecast_artifact.create_session_factory", lambda engine: factory
        )
        monkeypatch.setattr(
            "app.reports.forecast_artifact.slate_matchups",
            lambda factory, day: pytest.fail("replay must not require a pre-tip slate"),
        )
        after_tip = datetime(2026, 10, 24, tzinfo=timezone.utc)
        assert await import_forecast(date(2026, 10, 23), after_tip) is False
        changed = Predictions.model_validate(
            {**_payload("2026-10-23"), "model_version": "changed"}
        )
        monkeypatch.setattr(
            "app.reports.forecast_artifact.download_forecast", lambda day: changed
        )
        with pytest.raises(ForecastConflict, match="already differs"):
            await import_forecast(date(2026, 10, 23), after_tip)

    asyncio.run(scenario())
