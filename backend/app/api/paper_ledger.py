"""Read-only status of the owner-only NBA paper portfolios."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.slate import get_session_factory
from app.storage.paper_ledger import paper_day, paper_status

router = APIRouter(prefix="/api/v1/nba/paper-ledger")


@router.get("/status")
async def status(
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> dict:
    return await paper_status(factory)


@router.get("/slates/{slate_date}")
async def slate_decisions(
    slate_date: date,
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> dict:
    day = await paper_day(factory, slate_date)
    if day is None:
        raise HTTPException(404, detail="No recorded paper decisions for this slate")
    return day
