"""HTTP routes: read query parameters, call the service, return JSON.

This file is similar to an Express router/controller. It does not know how to
download Elexon data; that responsibility belongs to ElexonClient. Request flow:
URL -> validated arguments -> inspect() -> ElexonClient -> response model -> JSON.
"""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime

from app.schemas.elexon import InspectionResponse
from app.services.elexon_client import ElexonClient, ElexonError

# APIRouter groups related endpoints, like express.Router(). main.py adds /api,
# making the complete route prefix /api/test/elexon. Tags group routes in /docs.
router = APIRouter(prefix='/test/elexon', tags=['temporary Elexon inspection'])


def inspect(dataset, limit, fetch):
    # Shared controller logic for both routes. "fetch" is a function supplied
    # by the route, so this helper can run either actual or forecast retrieval.
    try:
        # "with" calls __enter__ before this block and __exit__ afterward.
        # This closes HTTP connections even if fetching or validation fails.
        with ElexonClient() as client:
            records = fetch(client)
    except ValueError as exc:
        # raise stops normal execution. FastAPI turns HTTPException into an
        # HTTP error response, like res.status(422).json(...) in Express.
        # 422 means the caller supplied invalid input, such as reversed dates.
        raise HTTPException(422, str(exc)) from exc
    except ElexonError as exc:
        # An upstream error means the external Elexon service failed, rather
        # than the caller's input. "from exc" keeps the original error for debugging.
        raise HTTPException(exc.status_code, {
            'message': str(exc), 'upstreamStatus': exc.upstream_status,
        }) from exc
    # Return a Pydantic object; FastAPI converts it to JSON automatically.
    # records[:limit] is a list slice (like records.slice(0, limit) in JS).
    # This limits the displayed sample AFTER fetching, not the upstream download.
    return InspectionResponse(dataset=dataset, totalRecords=len(records),
                              returnedRecords=min(limit, len(records)),
                              truncated=len(records) > limit, records=records[:limit])


# A decorator (@...) registers the function underneath as a GET handler.
# response_model checks and documents the outgoing JSON structure.
# These are ordinary "def" routes: FastAPI runs them in its worker thread pool,
# so the synchronous HTTPX calls do not run directly on the async event loop.
@router.get('/actual', response_model=InspectionResponse)
def actual(
    # Annotated combines a Python type with FastAPI instructions. "date" asks
    # FastAPI to parse YYYY-MM-DD. alias is the public URL parameter name;
    # settlement_date_from is the internal Python name. Missing/bad input gets 422.
    settlement_date_from: Annotated[date, Query(alias='settlementDateFrom')],
    settlement_date_to: Annotated[date, Query(alias='settlementDateTo', description='Exclusive end date')],
    # ge/le mean greater-than-or-equal / less-than-or-equal. Default: 100 records.
    limit: Annotated[int, Query(ge=1, le=10000)] = 100,
):
    # lambda is a short function, like client => client.actualWind(...) in JS.
    # Example: from=2026-01-01, to=2026-01-02 returns January 1 only.
    return inspect('FUELHH', limit, lambda client: client.actual_wind(settlement_date_from, settlement_date_to))


@router.get('/forecast', response_model=InspectionResponse)
def forecast(
    # AwareDatetime requires a timezone: use ...T00:00:00Z for UTC.
    # Publication time is WHEN a forecast was issued, not the time it predicts.
    publish_from: Annotated[AwareDatetime, Query(alias='publishDateTimeFrom')],
    publish_to: Annotated[AwareDatetime, Query(alias='publishDateTimeTo', description='Exclusive end timestamp')],
    # "| None" allows an omitted value (None is similar to null in JS).
    # Optional delivery filters select the times being predicted independently.
    delivery_from: Annotated[AwareDatetime | None, Query(alias='deliveryFrom')] = None,
    delivery_to: Annotated[AwareDatetime | None, Query(alias='deliveryTo')] = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 100,
):
    return inspect('WINDFOR', limit, lambda client: client.forecast_wind(
        publish_from, publish_to, delivery_from=delivery_from, delivery_to=delivery_to))
