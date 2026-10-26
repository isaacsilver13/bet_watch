import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import error_log
from app.api.bets import router as bets_router
from app.api.forecasts import router as forecasts_router
from app.api.internal import router as internal_router
from app.api.lines import router as lines_router
from app.api.matchup import router as matchup_router
from app.api.nba_reports import router as nba_reports_router
from app.api.ops import router as ops_router
from app.api.owner_auth import router as owner_auth_router
from app.api.paper_ledger import router as paper_ledger_router
from app.api.routes import router
from app.api.slate import router as slate_router

# Uvicorn only configures its own loggers, so without this the app's per-fetch
# INFO lines are dropped and /health/errors never sees a failure.
logging.getLogger("app").setLevel(logging.INFO)
if not logging.getLogger("app").handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.getLogger("app").addHandler(_handler)
error_log.install()

app = FastAPI(
    title="Betting Aggregator API",
    version="0.1.0",
    description="Canonical NFL and NBA odds comparison API.",
)
app.include_router(router)
app.include_router(slate_router)
app.include_router(matchup_router)
app.include_router(lines_router)
app.include_router(bets_router)
app.include_router(ops_router)
app.include_router(forecasts_router)
app.include_router(owner_auth_router)
app.include_router(nba_reports_router)
app.include_router(paper_ledger_router)
app.include_router(internal_router)

# Serves the built frontend from the same app/port as the API, so the two no
# longer need separate Fly apps. No-op locally, where this directory doesn't
# exist and the Vite dev server is used instead.
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST_DIR", "/app/frontend_dist")).resolve()
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (FRONTEND_DIST / full_path).resolve()
        is_within_dist = candidate == FRONTEND_DIST or FRONTEND_DIST in candidate.parents
        if full_path and is_within_dist and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")


@app.middleware("http")
async def private_application_boundary(request, call_next):
    from fastapi.responses import JSONResponse, RedirectResponse

    from app.api.owner_auth import _origin_allowed
    from app.api.private_access import may_access_private_data
    from app.config import load_settings

    public_health = {
        "/health", "/api/v1/health", "/api/v1/health/ready",
        "/api/v1/health/metrics", "/api/v1/status",
    }
    # /internal/* checks its own secret (app/api/internal.py); everything else stays private.
    internal = request.method == "POST" and request.url.path.startswith("/internal/")
    internal_forecast = (
        request.method == "POST" and request.url.path == "/api/v1/internal/nba-predictions"
    )
    auth_entry = request.url.path in {"/api/v1/auth/login", "/api/v1/auth/session"}
    settings = load_settings()
    if request.method == "GET" and request.url.path in {
        "/api/v1/status", "/api/v1/health/metrics"
    }:
        from app.api.owner_auth import has_owner_session

        request.state.owner_session = await has_owner_session(request, settings)
    login_asset = (bool(settings.owner_password_hash) and request.method == "GET"
                   and (request.url.path == "/login"
                        or request.url.path.startswith("/assets/")))
    if not internal and not internal_forecast and not (
        request.method == "GET" and request.url.path in public_health
    ) and not auth_entry and not login_asset:
        if not await may_access_private_data(request):
            if settings.owner_password_hash:
                if request.method == "GET" and not request.url.path.startswith("/api/"):
                    return RedirectResponse("/login", status_code=303)
                return JSONResponse(status_code=401, content={"detail": "Owner login required"})
            return JSONResponse(status_code=403, content={
                "detail": "Private local application. Use loopback or the owner token."
            })
        if (getattr(request.state, "owner_session", False)
                and request.method not in {"GET", "HEAD", "OPTIONS"}
                and not _origin_allowed(request, settings)):
            return JSONResponse(status_code=403, content={"detail": "Invalid request origin"})
    response = await call_next(request)
    if getattr(request.state, "owner_session", False) or (
        not internal_forecast and not auth_entry and not login_asset
        and request.url.path not in public_health
    ):
        response.headers["Cache-Control"] = "private, no-store"
    return response
