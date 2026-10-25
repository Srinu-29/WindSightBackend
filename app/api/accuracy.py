"""API route for evaluating forecast accuracy for one decision date."""
# date and time standard library types for parsing and validating dates and times.
from datetime import date, time
# Annotated combines types with FastAPI Query parameter metadata.
from typing import Annotated

# FastAPI router and HTTP exception handling classes.
from fastapi import APIRouter, HTTPException, Query

# Pydantic response models describing JSON schema structures.
from app.schemas.accuracy import SingleDateAccuracyResponse
from app.services.accuracy_service import evaluate_single_date_accuracy
from app.services.elexon_client import ElexonError
from app.services.forecast_selector import DEFAULT_CUTOFF_TIME

# APIRouter groups endpoints; main.py attaches prefix='/api', exposing /api/accuracy.
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
