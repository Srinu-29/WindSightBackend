"""Temporary inspection routes; disable with ENABLE_ELEXON_INSPECTION=false."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime

from app.schemas.elexon import InspectionResponse
from app.services.elexon_client import ElexonClient, ElexonError

router = APIRouter(prefix='/test/elexon', tags=['temporary Elexon inspection'])


def inspect(dataset, limit, fetch):
    try:
        with ElexonClient() as client:
            records = fetch(client)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ElexonError as exc:
        raise HTTPException(exc.status_code, {
            'message': str(exc), 'upstreamStatus': exc.upstream_status,
        }) from exc
    return InspectionResponse(dataset=dataset, totalRecords=len(records),
                              returnedRecords=min(limit, len(records)),
                              truncated=len(records) > limit, records=records[:limit])


@router.get('/actual', response_model=InspectionResponse)
def actual(
    settlement_date_from: Annotated[date, Query(alias='settlementDateFrom')],
    settlement_date_to: Annotated[date, Query(alias='settlementDateTo', description='Exclusive end date')],
    limit: Annotated[int, Query(ge=1, le=10000)] = 100,
):
    return inspect('FUELHH', limit, lambda client: client.actual_wind(settlement_date_from, settlement_date_to))


@router.get('/forecast', response_model=InspectionResponse)
def forecast(
    publish_from: Annotated[AwareDatetime, Query(alias='publishDateTimeFrom')],
    publish_to: Annotated[AwareDatetime, Query(alias='publishDateTimeTo', description='Exclusive end timestamp')],
    delivery_from: Annotated[AwareDatetime | None, Query(alias='deliveryFrom')] = None,
    delivery_to: Annotated[AwareDatetime | None, Query(alias='deliveryTo')] = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 100,
):
    return inspect('WINDFOR', limit, lambda client: client.forecast_wind(
        publish_from, publish_to, delivery_from=delivery_from, delivery_to=delivery_to))
