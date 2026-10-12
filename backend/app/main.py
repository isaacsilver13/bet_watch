import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import error_log
from app.api.matchup import router as matchup_router
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
