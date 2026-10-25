"""Tests for historical purchasing-decision replay and forecast selector."""
from datetime import date, datetime, time, timezone

import httpx
import pytest

from app.schemas.elexon import ActualWindRecord, ForecastWindRecord
from app.services.forecast_selector import (
    DEFAULT_CUTOFF_TIME,
    generate_delivery_periods,
    get_decision_cutoff_utc,
    get_delivery_date,
    select_forecasts,
)


def utc_dt(iso_str: str) -> datetime:
    """Helper to create UTC timezone-aware datetimes."""
    return datetime.fromisoformat(iso_str.replace('Z', '+00:00'))


def make_forecast(
    publish_time: str,
    start_time: str,
    generation: float = 1000.0,
) -> ForecastWindRecord:
    """Helper to create synthetic ForecastWindRecord instances."""
    return ForecastWindRecord(
        dataset='WINDFOR',
        publishTime=utc_dt(publish_time),
        startTime=utc_dt(start_time),
        generation=generation,
    )


def make_actual(
    settlement_date: str,
    period: int,
    start_time: str,
    generation: float = 950.0,
) -> ActualWindRecord:
    """Helper to create synthetic ActualWindRecord instances."""
    return ActualWindRecord(
        dataset='FUELHH',
        fuelType='WIND',
        settlementDate=date.fromisoformat(settlement_date),
        settlementPeriod=period,
        startTime=utc_dt(start_time),
        publishTime=utc_dt(start_time),
        generation=generation,
    )


def test_delivery_date_is_following_calendar_day():
    """Verify following delivery date calculation including month/year boundaries."""
    assert get_delivery_date(date(2026, 1, 10)) == date(2026, 1, 11)
    assert get_delivery_date(date(2025, 12, 31)) == date(2026, 1, 1)
    assert get_delivery_date(date(2028, 2, 28)) == date(2028, 2, 29)


def test_london_timezone_cutoff_conversion():
    """Verify Europe/London conversion to UTC during GMT (winter) and BST (summer)."""
    # Winter (GMT, UTC+0): 14:00 London == 14:00 UTC
    winter_cutoff = get_decision_cutoff_utc(date(2026, 1, 10), time(14, 0))
    assert winter_cutoff == datetime(2026, 1, 10, 14, 0, tzinfo=timezone.utc)

    # Summer (BST, UTC+1): 14:00 London == 13:00 UTC
    summer_cutoff = get_decision_cutoff_utc(date(2026, 7, 10), time(14, 0))
    assert summer_cutoff == datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc)


def test_delivery_periods_generation():
    """Verify that a standard delivery day generates 48 half-hour periods."""
    periods = generate_delivery_periods(date(2026, 1, 11))
    assert len(periods) == 48
    assert periods[0] == (1, datetime(2026, 1, 11, 0, 0, tzinfo=timezone.utc))
    assert periods[-1] == (48, datetime(2026, 1, 11, 23, 30, tzinfo=timezone.utc))


def test_future_forecasts_strictly_excluded():
    """CRITICAL BUSINESS RULE: Forecasts published AFTER the decision cutoff must be excluded."""
    decision_date = date(2026, 1, 10)
    cutoff = time(14, 0)  # Cutoff: 2026-01-10 14:00:00Z
    delivery_slot = '2026-01-11T09:00:00Z'

    forecasts = [
        # Eligible: published before cutoff
        make_forecast('2026-01-10T11:00:00Z', delivery_slot, generation=4000.0),
        # Eligible boundary: published exactly at cutoff
        make_forecast('2026-01-10T14:00:00Z', delivery_slot, generation=4500.0),
        # EXCLUDED (future): published 1 second after cutoff
        make_forecast('2026-01-10T14:00:01Z', delivery_slot, generation=5000.0),
        # EXCLUDED (future): published evening of decision date
        make_forecast('2026-01-10T18:00:00Z', delivery_slot, generation=5500.0),
        # EXCLUDED (future): published on delivery day
        make_forecast('2026-01-11T06:00:00Z', delivery_slot, generation=6000.0),
    ]

    actuals = [
        make_actual('2026-01-11', 19, delivery_slot, generation=4400.0),
    ]

    result = select_forecasts(decision_date, cutoff, forecasts=forecasts, actuals=actuals)

    # Find the period evaluation for 09:00 UTC (period 19)
    period_0900 = next(p for p in result.periods if p.startTime == utc_dt(delivery_slot))

    # Must select 4500.0 (published at 14:00:00Z).
    # Must NOT select 5000.0, 5500.0, or 6000.0.
    assert period_0900.forecastGenerationMW == 4500.0
    assert period_0900.forecastPublishTime == utc_dt('2026-01-10T14:00:00Z')
    assert period_0900.actualGenerationMW == 4400.0
    assert period_0900.differenceMW == 100.0  # 4500.0 - 4400.0


def test_selects_latest_eligible_forecast():
    """Verify that among eligible forecasts (publishTime <= cutoff), the latest one is selected."""
    decision_date = date(2026, 1, 10)
    cutoff = time(14, 0)
    delivery_slot = '2026-01-11T12:00:00Z'

    forecasts = [
        make_forecast('2026-01-09T20:00:00Z', delivery_slot, generation=3000.0),
        make_forecast('2026-01-10T08:00:00Z', delivery_slot, generation=3200.0),
        make_forecast('2026-01-10T13:30:00Z', delivery_slot, generation=3600.0),  # Latest eligible
    ]

    result = select_forecasts(decision_date, cutoff, forecasts=forecasts)
    period_1200 = next(p for p in result.periods if p.startTime == utc_dt(delivery_slot))

    assert period_1200.forecastGenerationMW == 3600.0
    assert period_1200.forecastPublishTime == utc_dt('2026-01-10T13:30:00Z')


def test_missing_forecast_and_actual_handled():
    """Verify that periods without eligible forecasts or actuals return None values safely."""
    decision_date = date(2026, 1, 10)
    cutoff = time(14, 0)
    delivery_slot = '2026-01-11T00:00:00Z'

    # Only a future forecast exists (should be excluded)
    forecasts = [
        make_forecast('2026-01-10T16:00:00Z', delivery_slot, generation=2000.0),
    ]
    # No actuals supplied

    result = select_forecasts(decision_date, cutoff, forecasts=forecasts, actuals=[])
    period_0000 = result.periods[0]

    assert period_0000.forecastGenerationMW is None
    assert period_0000.forecastPublishTime is None
    assert period_0000.actualGenerationMW is None
    assert period_0000.differenceMW is None
    assert result.periodsWithForecast == 0
    assert result.periodsWithActual == 0


def test_custom_cutoff_time_respected():
    """Verify that passing a custom cutoff time updates the decision boundary."""
    decision_date = date(2026, 1, 10)
    custom_cutoff = time(10, 0)  # 10:00 London time
    delivery_slot = '2026-01-11T05:00:00Z'

    forecasts = [
        make_forecast('2026-01-10T09:30:00Z', delivery_slot, generation=1500.0),  # Eligible (< 10:00)
        make_forecast('2026-01-10T11:00:00Z', delivery_slot, generation=1800.0),  # Excluded (> 10:00)
    ]

    result = select_forecasts(decision_date, custom_cutoff, forecasts=forecasts)
    period_0500 = next(p for p in result.periods if p.startTime == utc_dt(delivery_slot))

    assert period_0500.forecastGenerationMW == 1500.0
    assert period_0500.forecastPublishTime == utc_dt('2026-01-10T09:30:00Z')


def test_forecast_replay_endpoint_missing_param():
    """Verify that omitting required decision_date query parameter returns 422."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.get('/api/forecast')
    assert response.status_code == 422


def test_forecast_replay_endpoint_success(monkeypatch):
    """Verify GET /api/forecast?decision_date=2026-01-10 returns valid replay JSON."""
    from fastapi.testclient import TestClient
    from app.main import app
    import app.api.forecast as api_forecast
    from app.schemas.forecast import ForecastReplayResponse, PeriodEvaluation

    fake_response = ForecastReplayResponse(
        decisionDate=date(2026, 1, 10),
        cutoffTime=time(14, 0),
        cutoffUtc=utc_dt('2026-01-10T14:00:00Z'),
        deliveryDate=date(2026, 1, 11),
        timezone='Europe/London',
        totalPeriods=48,
        periodsWithForecast=1,
        periodsWithActual=1,
        periods=[
            PeriodEvaluation(
                startTime=utc_dt('2026-01-11T00:00:00Z'),
                settlementPeriod=1,
                forecastGenerationMW=5200.0,
                forecastPublishTime=utc_dt('2026-01-10T13:45:00Z'),
                actualGenerationMW=5100.0,
                differenceMW=100.0,
            )
        ],
    )

    monkeypatch.setattr(
        api_forecast,
        'replay_purchasing_decision',
        lambda decision_date, cutoff_time: fake_response,
    )

    with TestClient(app) as client:
        response = client.get('/api/forecast?decision_date=2026-01-10')

    assert response.status_code == 200
    body = response.json()
    assert body['decisionDate'] == '2026-01-10'
    assert body['deliveryDate'] == '2026-01-11'
    assert body['cutoffTime'] == '14:00:00'
    assert body['cutoffUtc'] == '2026-01-10T14:00:00Z'
    assert body['totalPeriods'] == 48
    assert len(body['periods']) == 1
    assert body['periods'][0]['forecastGenerationMW'] == 5200.0
    assert body['periods'][0]['forecastPublishTime'] == '2026-01-10T13:45:00Z'

