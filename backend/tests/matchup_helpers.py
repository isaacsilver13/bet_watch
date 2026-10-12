import json
from pathlib import Path

from app.storage.database import create_schema, create_session_factory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(relative_path: str):
    return json.loads((FIXTURES / relative_path).read_text(encoding="utf-8"))


def db_url(tmp_path: Path) -> str:
    # A file DB (not :memory:) so a second engine, e.g. one used by TestClient, sees the same data.
    return f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"


async def new_session_factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url(tmp_path))
    await create_schema(engine)
    return create_session_factory(engine)
