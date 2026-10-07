import os
import re
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


_VALID_REGIONS = {"us", "us2", "uk", "eu", "au"}
_BOOKMAKER_KEY = re.compile(r"^[a-z0-9_]+$")


def _parse_regions(raw: str) -> str:
    regions = [r.strip().lower() for r in raw.split(",") if r.strip()]
    if not regions or not set(regions) <= _VALID_REGIONS:
        raise ValueError(f"ODDS_REGIONS must be a comma list of {sorted(_VALID_REGIONS)}")
    return ",".join(dict.fromkeys(regions))


def _parse_bookmakers(raw: str) -> tuple:
    keys = tuple(dict.fromkeys(k.strip().lower() for k in raw.split(",") if k.strip()))
    if not all(_BOOKMAKER_KEY.match(k) for k in keys):
        raise ValueError("ODDS_BOOKMAKERS must be comma-separated provider keys like 'draftkings'")
    return keys


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
    odds_regions: str = "us"
    odds_bookmakers: tuple = ()  # non-empty allowlist wins over odds_regions
    kalshi_enabled: bool = False
    kalshi_cache_ttl_seconds: int = 300
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
        odds_regions=_parse_regions(_setting("ODDS_REGIONS", "us")),
        odds_bookmakers=_parse_bookmakers(_setting("ODDS_BOOKMAKERS", "")),
        kalshi_enabled=_setting("KALSHI_ENABLED", "false").strip().lower() == "true",
        kalshi_cache_ttl_seconds=int(_setting("KALSHI_CACHE_TTL_SECONDS", "300")),
        admin_refresh_secret=_setting("ADMIN_REFRESH_SECRET", "") or None,
        admin_refresh_budget=int(_setting("ADMIN_REFRESH_BUDGET", "4")),
        admin_refresh_budget_window_seconds=int(
            _setting("ADMIN_REFRESH_BUDGET_WINDOW_SECONDS", "86400")
        ),
    )