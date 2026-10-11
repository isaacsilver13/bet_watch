"""Fixture-only ASGI entry point for local and CI browser smoke tests."""

import os
from pathlib import Path

from app import config

os.environ.update(
    {
        "PROVIDER_MODE": "fixture",
        "ODDS_PROVIDER": "fixture",
        "KALSHI_ENABLED": "true",
        "PERSISTENCE_ENABLED": "false",
    }
)
os.environ.pop("THE_ODDS_API_KEY", None)
os.environ.pop("ADMIN_REFRESH_SECRET", None)
# Browser smoke tests must never inherit credentials from a developer's local .env.
config._DOTENV_PATH = Path(__file__).with_name("does-not-exist.env")

from app.main import app  # noqa: E402

__all__ = ["app"]
