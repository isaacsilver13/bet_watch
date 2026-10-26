"""Read immutable NBA report snapshots for the private interactive view."""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.portfolio import allocate_shadow_profiles, candidates_from_report
from app.api.slate import get_session_factory
from app.config import load_settings
from app.reports.nba_digest import render_nba_digest
from app.storage.models import NbaReport

router = APIRouter(prefix="/api/v1/nba/reports")


async def _saved_report(
    slate_date: date, factory: async_sessionmaker[AsyncSession]
) -> dict:
    async with factory() as session:
        row = await session.get(NbaReport, slate_date)
    if row is None:
        raise HTTPException(404, detail="No saved NBA report for this date")
    return row.payload


@router.get("/{slate_date}")
async def saved_nba_report(
    slate_date: date,
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> dict:
    return await _saved_report(slate_date, factory)


@router.get("/{slate_date}/digest-preview", response_class=HTMLResponse)
async def saved_nba_digest_preview(
    slate_date: date,
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> HTMLResponse:
    report = await _saved_report(slate_date, factory)
    origin = load_settings().owner_app_origin
    report_url = f"{origin}/nba/report/{slate_date}" if origin else None
    try:
        digest = render_nba_digest(report, report_url)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error
    return HTMLResponse(digest.html, headers={"Cache-Control": "private, no-store"})


@router.get("/{slate_date}/paper-preview")
async def saved_nba_paper_preview(
    slate_date: date,
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> dict:
    report = await _saved_report(slate_date, factory)
    try:
        if report.get("slate_date") != slate_date.isoformat():
            raise ValueError("saved report date does not match its database key")
        candidates = candidates_from_report(report)
        allocations = allocate_shadow_profiles(candidates, slate_date)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error
    return {
        "slate_date": slate_date.isoformat(),
        "status": "preview_not_ledger",
        "candidate_count": len(candidates),
        "allocations": [asdict(allocation) for allocation in allocations],
    }
