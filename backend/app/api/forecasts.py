"""Authenticated delivery of daily NBA model predictions."""

import hmac
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.slate import get_now, get_session_factory
from app.config import load_settings
from app.domain.forecasts import Predictions
from app.storage.forecast_repository import (
    ForecastConflict,
    get_forecast,
    slate_matchups,
    store_forecast,
    validate_forecast_for_slate,
)

router = APIRouter(prefix="/api/v1/internal")
MAX_PAYLOAD_BYTES = 64 * 1024


@router.post("/nba-predictions")
async def receive_nba_predictions(
    request: Request,
    response: Response,
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    now: datetime = Depends(get_now),
) -> dict:
    expected = load_settings().nba_prediction_ingest_secret
    supplied = request.headers.get("x-internal-secret") or ""
    if not expected:
        raise HTTPException(503, detail="NBA prediction intake is not configured")
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(403, detail="Invalid prediction intake credential")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_PAYLOAD_BYTES:
            raise HTTPException(413, detail="Prediction payload is too large")
    try:
        preds = Predictions.model_validate_json(body)
    except ValidationError as error:
        raise HTTPException(422, detail=error.errors(include_input=False)) from error
    existing = await get_forecast(factory, preds.slate_date)
    if existing is not None:
        if existing.model_dump(mode="json") != preds.model_dump(mode="json"):
            raise HTTPException(409, detail="forecast for this slate already differs")
        return {"slate_date": preds.slate_date.isoformat(), "stored": False}
    matchups = await slate_matchups(factory, preds.slate_date)
    if not matchups:
        raise HTTPException(503, detail="NBA slate is not loaded")
    try:
        validate_forecast_for_slate(preds, matchups, now)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error
    try:
        created = await store_forecast(factory, preds)
    except ForecastConflict as error:
        raise HTTPException(409, detail=str(error)) from error
    response.status_code = 201 if created else 200
    return {"slate_date": preds.slate_date.isoformat(), "stored": created}
