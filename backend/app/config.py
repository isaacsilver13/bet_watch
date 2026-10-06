import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

_DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://betting_aggregator:betting_aggregator@127.0.0.1:5433/"
    "betting_aggregator"
)


def _setting(name: str, default: str) -> str:
    process_value = os.getenv(name)
    if process_value is not None and process_value.strip():
        return process_value

    file_value = dotenv_values(_DOTENV_PATH).get(name)
    return file_value if file_value is not None else default


class ProviderMode(str, Enum):
    FIXTURE = "fixture"
    LIVE = "live"


@dataclass(frozen=True)
class Settings:
    app_env: str
    provider_mode: ProviderMode
    odds_provider: str
    the_odds_api_key: Optional[str]
    database_url: str
    persistence_enabled: bool
    observation_retention_days: int
    cache_ttl_seconds: int
    provider_min_quota_remaining: int
    provider_timeout_seconds: float
    provider_max_retries: int
    provider_backoff_seconds: float
    admin_refresh_secret: Optional[str] = None
    admin_refresh_budget: int = 4
    admin_refresh_budget_window_seconds: int = 86400


def load_settings() -> Settings:
    mode_value = _setting("PROVIDER_MODE", ProviderMode.FIXTURE.value).strip().lower()
    try:
        provider_mode = ProviderMode(mode_value)
    except ValueError as error:
        raise ValueError("PROVIDER_MODE must be 'fixture' or 'live'") from error

    persistence_value = _setting("PERSISTENCE_ENABLED", "false").strip().lower()
    if persistence_value not in {"true", "false"}:
        raise ValueError("PERSISTENCE_ENABLED must be 'true' or 'false'")

    return Settings(
        app_env=_setting("APP_ENV", "development").strip().lower(),
        provider_mode=provider_mode,
        odds_provider=_setting("ODDS_PROVIDER", "").strip().lower(),
        the_odds_api_key=_setting("THE_ODDS_API_KEY", "") or None,
        database_url=_setting("DATABASE_URL", _DEFAULT_DATABASE_URL),
        persistence_enabled=persistence_value == "true",
        observation_retention_days=int(_setting("OBSERVATION_RETENTION_DAYS", "90")),
        cache_ttl_seconds=int(_setting("CACHE_TTL_SECONDS", "86400")),
        provider_min_quota_remaining=int(_setting("PROVIDER_MIN_QUOTA_REMAINING", "5")),
        provider_timeout_seconds=float(_setting("PROVIDER_TIMEOUT_SECONDS", "15")),
        provider_max_retries=int(_setting("PROVIDER_MAX_RETRIES", "2")),
        provider_backoff_seconds=float(_setting("PROVIDER_BACKOFF_SECONDS", "0.5")),
        admin_refresh_secret=_setting("ADMIN_REFRESH_SECRET", "") or None,
        admin_refresh_budget=int(_setting("ADMIN_REFRESH_BUDGET", "4")),
        admin_refresh_budget_window_seconds=int(
            _setting("ADMIN_REFRESH_BUDGET_WINDOW_SECONDS", "86400")
        ),
    )