"""Accuracy API routes.

Provides endpoints for evaluating single-date and historical forecast accuracy.
"""
# date and time standard library types for parsing and validating dates and times.
from datetime import date, time
# Annotated combines types with FastAPI Query parameter metadata.
from typing import Annotated

# FastAPI router and HTTP exception handling classes.
from fastapi import APIRouter, HTTPException, Query

# Pydantic response models describing JSON schema structures.
from app.schemas.accuracy import HistoricalAccuracyResponse, SingleDateAccuracyResponse
from app.services.accuracy_service import (
    evaluate_historical_accuracy,
    evaluate_single_date_accuracy,
)
from app.services.elexon_client import ElexonError
from app.services.forecast_selector import DEFAULT_CUTOFF_TIME

# APIRouter groups endpoints; main.py attaches prefix='/api', exposing /api/accuracy and /api/historical-accuracy.
router = APIRouter(tags=['forecast accuracy'])


# GET /api/accuracy?decision_date=2026-01-10
@router.get('/accuracy', response_model=SingleDateAccuracyResponse)
def get_single_date_accuracy(
    # decision_date is required (e.g. 2026-01-10). FastAPI parses YYYY-MM-DD or returns HTTP 422.
    decision_date: Annotated[
        date,
        Query(
            description='Calendar date when the purchasing decision was made (YYYY-MM-DD)',
            examples=['2026-01-10'],
        ),
    ],
    # cutoff_time is optional. If not supplied, defaults to 14:00 UK local time.
    cutoff_time: Annotated[
        time | None,
        Query(
            description='Optional cutoff time in Europe/London (default 14:00)',
            examples=['14:00:00'],
        ),
    ] = None,
) -> SingleDateAccuracyResponse:
    """Calculates forecast accuracy metrics (MAE, RMSE, Bias, Max Error) for a single decision date.

    Returns summary metrics and chart-ready half-hourly series with signed and absolute errors.
    """
    effective_cutoff = cutoff_time if cutoff_time is not None else DEFAULT_CUTOFF_TIME
    try:
        # Run accuracy calculation for the specified date and cutoff.
        return evaluate_single_date_accuracy(
            decision_date=decision_date,
            cutoff_time=effective_cutoff,
        )
    except ValueError as exc:
        # Invalid arguments return 422 Unprocessable Entity.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ElexonError as exc:
        # Upstream Elexon connection or rate-limit errors mapped to appropriate 5xx HTTP codes.
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                'message': str(exc),
                'upstreamStatus': exc.upstream_status,
            },
        ) from exc


# GET /api/historical-accuracy?start_date=2026-01-01&end_date=2026-01-31
@router.get('/historical-accuracy', response_model=HistoricalAccuracyResponse)
def get_historical_accuracy_route(
    # Defaults to the full month of January 2026 purchasing decisions as required.
    start_date: Annotated[
        date,
        Query(
            description='Start of purchasing decision date range (default 2026-01-01)',
            examples=['2026-01-01'],
        ),
    ] = date(2026, 1, 1),
    end_date: Annotated[
        date,
        Query(
            description='End of purchasing decision date range (default 2026-01-31)',
            examples=['2026-01-31'],
        ),
    ] = date(2026, 1, 31),
    # Optional cutoff time for testing non-standard purchasing windows.
    cutoff_time: Annotated[
        time | None,
        Query(
            description='Optional cutoff time in Europe/London (default 14:00)',
            examples=['14:00:00'],
        ),
    ] = None,
) -> HistoricalAccuracyResponse:
    """Calculates historical forecast accuracy metrics across January 2026 purchasing decisions.

    Provides overall aggregate metrics, a daily breakdown, and a settlement period profile.
    """
    # Guard against inverted date intervals.
    if start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail='start_date must be on or before end_date.',
        )

    effective_cutoff = cutoff_time if cutoff_time is not None else DEFAULT_CUTOFF_TIME
    try:
        # Run historical aggregation across all days in the date range.
        return evaluate_historical_accuracy(
            start_date=start_date,
            end_date=end_date,
            cutoff_time=effective_cutoff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ElexonError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                'message': str(exc),
                'upstreamStatus': exc.upstream_status,
            },
        ) from exc
