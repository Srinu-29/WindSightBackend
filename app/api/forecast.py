"""Forecast replay API endpoint.

Exposes historical purchasing decision replay for wind generation forecasts and actuals.
"""
# date and time are standard Python types used by FastAPI to parse URL query parameters.
from datetime import date, time
# Annotated attaches metadata (such as Query parameter descriptions and examples) to types.
from typing import Annotated

# APIRouter groups endpoints (like express.Router()). HTTPException returns custom HTTP error status codes.
from fastapi import APIRouter, HTTPException, Query

from app.schemas.forecast import ForecastReplayResponse
from app.services.elexon_client import ElexonError
from app.services.forecast_selector import DEFAULT_CUTOFF_TIME, replay_purchasing_decision

# APIRouter prefix='/forecast': when main.py mounts this with prefix='/api', the route becomes GET /api/forecast.
router = APIRouter(prefix='/forecast', tags=['forecast replay'])


# @router.get registers this function for GET /api/forecast.
# response_model enforces and serializes the outgoing JSON response to match ForecastReplayResponse.
@router.get('', response_model=ForecastReplayResponse)
def get_forecast_replay(
    # Annotated[date, Query(...)]: FastAPI validates and parses the YYYY-MM-DD string into a datetime.date object.
    # If the user supplies an invalid date format or omits this required parameter, FastAPI returns HTTP 422.
    decision_date: Annotated[
        date,
        Query(
            description='Calendar date when the purchasing decision was made (YYYY-MM-DD)',
            examples=['2026-01-10'],
        ),
    ],
    # cutoff_time is optional. If omitted, it defaults to None and we apply DEFAULT_CUTOFF_TIME (14:00 UK local time).
    cutoff_time: Annotated[
        time | None,
        Query(
            description='Optional cutoff time in Europe/London (HH:MM or HH:MM:SS, default 14:00)',
            examples=['14:00:00'],
        ),
    ] = None,
) -> ForecastReplayResponse:
    """Replays an electricity purchasing decision for the following delivery day.

    Selects the most recent wind forecast published at or before the decision cutoff
    for each half-hour delivery period and compares with actual generation.
    """
    # Use the user-provided cutoff if specified; otherwise fallback to the assumed 14:00 default.
    effective_cutoff = cutoff_time if cutoff_time is not None else DEFAULT_CUTOFF_TIME

    try:
        # Call the core service to retrieve Elexon data and select eligible forecasts.
        return replay_purchasing_decision(
            decision_date=decision_date,
            cutoff_time=effective_cutoff,
        )
    except ValueError as exc:
        # 422 Unprocessable Entity: Raised if input arguments are logically invalid.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ElexonError as exc:
        # Maps upstream Elexon failures (such as 502 Bad Gateway or 504 Gateway Timeout) to the client.
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                'message': str(exc),
                'upstreamStatus': exc.upstream_status,
            },
        ) from exc
