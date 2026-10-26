"""Single-owner password login with revocable, database-backed browser sessions."""

import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, load_settings
from app.storage.database import create_engine, create_session_factory
from app.storage.games_repository import aware
from app.storage.models import OwnerSession
from app.throttle import SlidingWindowLimiter

router = APIRouter(prefix="/api/v1/auth")
COOKIE_NAME = "owner_session"
SESSION_LIFETIME = timedelta(hours=12)
PASSWORD_ITERATIONS = 600_000
_GLOBAL_LOGIN_LIMIT = SlidingWindowLimiter(20, 15 * 60)
_CLIENT_LOGIN_LIMITS: dict[str, SlidingWindowLimiter] = {}


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def hash_owner_password(password: str, salt: Optional[bytes] = None) -> str:
    """Generate an env-ready verifier; the password is never stored or logged."""
    if len(password) < 16 or len(password) > 1024:
        raise ValueError("owner password must be 16–1024 characters")
    salt = salt or secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_owner_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt64, expected64 = encoded.split("$")
        iterations = int(rounds)
        if algorithm != "pbkdf2_sha256" or iterations != PASSWORD_ITERATIONS:
            return False
        salt = base64.urlsafe_b64decode(salt64 + "=" * (-len(salt64) % 4))
        expected = base64.urlsafe_b64decode(expected64 + "=" * (-len(expected64) % 4))
        if len(salt) != 24 or len(expected) != 32 or len(password) > 1024:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, UnicodeError, binascii.Error):
        return False


@lru_cache(maxsize=4)
def _factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(create_engine(settings))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _credential_hash(encoded_password: str) -> str:
    return hashlib.sha256(encoded_password.encode("utf-8")).hexdigest()


def _origin_allowed(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin or request.headers.get("sec-fetch-site") == "cross-site":
        return False
    if settings.owner_app_origin and origin == settings.owner_app_origin:
        return True
    if settings.app_env == "production":
        return False
    return origin in {
        str(request.base_url).rstrip("/"),
        "http://localhost:5173", "http://127.0.0.1:5173",
    }


async def has_owner_session(request: Request, settings: Optional[Settings] = None) -> bool:
    active = settings or load_settings()
    token = request.cookies.get(COOKIE_NAME)
    if (not token or len(token) > 128 or not active.owner_password_hash
            or not active.persistence_enabled):
        return False
    async with _factory(active)() as session:
        row = await session.get(OwnerSession, _token_hash(token))
        return (row is not None and aware(row.expires_at) > datetime.now(timezone.utc)
                and hmac.compare_digest(row.credential_sha256,
                                        _credential_hash(active.owner_password_hash)))


@router.get("/session")
async def session_status(request: Request) -> dict:
    settings = load_settings()
    return {
        "configured": bool(settings.owner_password_hash and settings.persistence_enabled),
        "authenticated": await has_owner_session(request, settings),
    }


@router.post("/login")
async def login(data: LoginIn, request: Request, response: Response) -> dict:
    settings = load_settings()
    if not settings.owner_password_hash or not settings.persistence_enabled:
        raise HTTPException(503, detail="Owner login is not configured")
    if settings.app_env == "production" and (
        not settings.owner_app_origin or not settings.owner_app_origin.startswith("https://")
    ):
        raise HTTPException(503, detail="Owner origin must be configured for HTTPS")
    if not _origin_allowed(request, settings):
        raise HTTPException(403, detail="Invalid login origin")
    peer = request.client.host if request.client else "unknown"
    limiter = _CLIENT_LOGIN_LIMITS.setdefault(peer, SlidingWindowLimiter(5, 15 * 60))
    if not _GLOBAL_LOGIN_LIMIT.allow() or not limiter.allow():
        raise HTTPException(429, detail="Too many login attempts")
    if not verify_owner_password(data.password, settings.owner_password_hash):
        raise HTTPException(401, detail="Invalid credentials")
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    async with _factory(settings)() as session, session.begin():
        await session.execute(delete(OwnerSession).where(OwnerSession.expires_at <= now))
        session.add(OwnerSession(token_sha256=_token_hash(token),
                                 credential_sha256=_credential_hash(settings.owner_password_hash),
                                 created_at=now, expires_at=now + SESSION_LIFETIME))
    response.set_cookie(COOKIE_NAME, token, httponly=True,
                        secure=settings.owner_app_origin is not None
                        and settings.owner_app_origin.startswith("https://"),
                        samesite="strict", path="/", max_age=int(SESSION_LIFETIME.total_seconds()))
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    settings = load_settings()
    if not _origin_allowed(request, settings):
        raise HTTPException(403, detail="Invalid logout origin")
    token = request.cookies.get(COOKIE_NAME)
    if token and settings.persistence_enabled:
        async with _factory(settings)() as session, session.begin():
            await session.execute(delete(OwnerSession).where(
                OwnerSession.token_sha256 == _token_hash(token)
            ))
    response.delete_cookie(COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}


if __name__ == "__main__":
    from getpass import getpass

    first = getpass("New owner password (at least 16 characters): ")
    second = getpass("Repeat owner password: ")
    if first != second:
        raise SystemExit("Passwords did not match")
    print(hash_owner_password(first))
