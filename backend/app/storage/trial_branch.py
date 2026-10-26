"""Explicit isolated database target for the NBA paper-trial tools."""

from dataclasses import replace

from sqlalchemy.engine import make_url

from app.config import Settings, load_settings

TRIAL_HOST = "ep-crimson-darkness-aya3ajck.c-5.us-east-2.aws.neon.tech"
PRODUCTION_HOST = "ep-green-math-ay0yul5q-pooler.c-5.us-east-2.aws.neon.tech"


def trial_settings() -> Settings:
    """Reuse the local credential in memory, but refuse an unexpected source URL."""
    settings = load_settings()
    url = make_url(settings.database_url)
    if url.host != PRODUCTION_HOST:
        raise RuntimeError("The configured database host changed; verify the trial branch target")
    return replace(
        settings,
        database_url=url.set(host=TRIAL_HOST).render_as_string(hide_password=False),
    )
