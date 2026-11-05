"""End to end on the REAL 2026 nflverse files: schedule -> games, play-by-play -> half stats
(through the real parser and repository), then the real /context endpoint for week 9."""
import asyncio, sys, json
from datetime import datetime, timezone
from pathlib import Path
from app.api.slate import get_now, get_session_factory
from app.ingest.nflverse import parse_games_csv
from app.ingest.nflverse_pbp import parse_pbp_halves
from app.ingest.seed import seed_teams
from app.main import app
from app.storage.context_repository import ContextRepository
from app.storage.database import create_session_factory, create_schema
from app.storage.games_repository import GamesRepository
from sqlalchemy.ext.asyncio import create_async_engine
from tests.local_client import TestClient

# usage: python e2e_real_data.py <raw_dir with games.csv + pbp.csv> <scratch_dir for real.db and output>
RAW = Path(sys.argv[1])
OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
URL = f"sqlite+aiosqlite:///{(OUT / 'real.db').as_posix()}"
NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)

async def load():
    engine = create_async_engine(URL); await create_schema(engine)
    f = create_session_factory(engine); await seed_teams(f)
    recs = [r for r in parse_games_csv((RAW/"games.csv").read_text(encoding="utf-8")) if r.season == 2026]
    await GamesRepository(f).upsert_games("nfl", "nflverse", recs, NOW)
    with (RAW/"pbp.csv").open(encoding="utf-8", newline="") as h:
        halves = parse_pbp_halves(h)
    res = await ContextRepository(f).save_half_stats("nfl", halves, NOW)
    print("games loaded:", len(recs), "| half rows saved:", res.saved, "| unmatched:", res.unmatched[:3])
    await engine.dispose()

(OUT / "real.db").unlink(missing_ok=True)
asyncio.run(load())
factory = create_session_factory(create_async_engine(URL))
app.dependency_overrides[get_session_factory] = lambda: factory
app.dependency_overrides[get_now] = lambda: NOW
c = TestClient(app)
slate = c.get("/api/v1/slate", params={"season": 2026, "week": 9}).json()
print("week 9 games via slate:", len(slate["games"]))
out = {}
for g in slate["games"]:
    ctx = c.get(f"/api/v1/games/{g['id']}/context", params={"last_n": 3}); assert ctx.status_code == 200, ctx.text
    body = ctx.json(); out[f"{body['away']['team']['abbr']}@{body['home']['team']['abbr']}"] = body
json.dump(out, open(OUT / "week9_context.json", "w"))
b = out["JAX@BAL"]
for side in ("away", "home"):
    t = b[side]; fh = t["first_half"]; m = fh["metrics"]
    print(t["team"]["abbr"], "games", fh["games"], "| 1H margin", m["h1_margin_pg"], "| 2H", round(fh["second_half_margin_pg"],2),
          "| last3", round(fh["recent_margin_pg"],2), "| HT led", fh["halftime"]["led"], "| trailed", fh["halftime"]["trailed"],
          "| sf", fh["scored_first"], "| off epa", m["h1_off_epa_play"], "| def epa", m["h1_def_epa_play"])
print("data_as_of.first_half:", b["data_as_of"]["first_half"])
