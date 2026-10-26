import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from dotenv import dotenv_values

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOTENV_PATH = _REPO_ROOT / ".env"
# The AIsaac push target, shared with the owner's other local push scripts; env/.env win.
_PUSH_CONFIG_PATH = Path.home() / ".claude" / "aisaac_push.json"
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


def _push_setting(name: str) -> Optional[str]:
    value = _setting(name, "").strip()
    if not value and _PUSH_CONFIG_PATH.exists():
        value = str(json.loads(_PUSH_CONFIG_PATH.read_text(encoding="utf-8")).get(name) or "")
    return value.strip() or None


_VALID_REGIONS = {"us", "us2", "uk", "eu", "au"}
_BOOKMAKER_KEY = re.compile(r"^[a-z0-9_]+$")
_DEFAULT_KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


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


def _parse_kalshi_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("KALSHI_BASE_URL must be an HTTPS URL without credentials or a query")
    return value


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
    kalshi_base_url: str = _DEFAULT_KALSHI_BASE_URL
    admin_refresh_secret: Optional[str] = None
    admin_refresh_budget: int = 4
    admin_refresh_budget_window_seconds: int = 86400
    raw_data_dir: str = "data/raw"
    nba_predictions_dir: str = "predictions"
    nba_prediction_ingest_secret: Optional[str] = None
    fractional_kelly: float = 0.30  # mirrors the nba repo's experiment.py
    max_kelly: float = 0.08
    model_pick_stake: float = 25.0  # reference stake for pricing per-bet fees; never reported
    aisaac_dashboard_url: Optional[str] = None
    aisaac_internal_secret: Optional[str] = None
    resend_api_key: Optional[str] = None
    nba_newsletter_from_email: Optional[str] = None
    nba_newsletter_to_email: Optional[str] = None
    owner_password_hash: Optional[str] = None
    owner_app_origin: Optional[str] = None


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
        kalshi_base_url=_parse_kalshi_base_url(
            _setting("KALSHI_BASE_URL", _DEFAULT_KALSHI_BASE_URL)
        ),
        admin_refresh_secret=_setting("ADMIN_REFRESH_SECRET", "") or None,
        admin_refresh_budget=int(_setting("ADMIN_REFRESH_BUDGET", "4")),
        admin_refresh_budget_window_seconds=int(
            _setting("ADMIN_REFRESH_BUDGET_WINDOW_SECONDS", "86400")
        ),
        raw_data_dir=_setting("RAW_DATA_DIR", "").strip() or str(_REPO_ROOT / "data" / "raw"),
        nba_predictions_dir=_setting("NBA_PREDICTIONS_DIR", "").strip()
        or str(_REPO_ROOT.parent / "nba_prediction" / "outputs" / "predictions"),
        nba_prediction_ingest_secret=_setting("NBA_PREDICTION_INGEST_SECRET", "") or None,
        fractional_kelly=float(_setting("FRACTIONAL_KELLY", "0.30")),
        max_kelly=float(_setting("MAX_KELLY", "0.08")),
        model_pick_stake=float(_setting("MODEL_PICK_STAKE", "25")),
        aisaac_dashboard_url=_push_setting("AISAAC_DASHBOARD_URL"),
        aisaac_internal_secret=_push_setting("AISAAC_INTERNAL_SECRET"),
        resend_api_key=_setting("RESEND_API_KEY", "") or None,
        nba_newsletter_from_email=_setting("NBA_NEWSLETTER_FROM_EMAIL", "") or None,
        nba_newsletter_to_email=_setting("NBA_NEWSLETTER_TO_EMAIL", "") or None,
        owner_password_hash=_setting("OWNER_PASSWORD_HASH", "") or None,
        owner_app_origin=_setting("OWNER_APP_ORIGIN", "").strip().rstrip("/") or None,
    )
