"""Unit tests for the forecast accuracy analysis service."""
import math
from datetime import date, datetime, time, timezone

import pytest

from app.schemas.accuracy import SingleDateAccuracyResponse
from app.schemas.forecast import ForecastReplayResponse, PeriodEvaluation
from app.services.accuracy_service import (
    calculate_metrics,
    evaluate_historical_accuracy,
    evaluate_single_date_accuracy,
    explain_bias,
)


def utc_dt(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str.replace('Z', '+00:00'))


def test_hand_crafted_accuracy_calculations():
    """Verify MAE, RMSE, Bias, and Max Absolute Error against exact manual arithmetic."""
    # Pairs: (forecast, actual)
    # Pair 1: forecast 100, actual 80   -> signed error = +20, abs error = 20, sq = 400
    # Pair 2: forecast 200, actual 250  -> signed error = -50, abs error = 50, sq = 2500
    # Pair 3: forecast 300, actual 300  -> signed error = 0,   abs error = 0,  sq = 0
    pairs = [(100.0, 80.0), (200.0, 250.0), (300.0, 300.0)]
    metrics = calculate_metrics(pairs, total_periods=3)

    assert metrics.matchedPeriods == 3
    assert metrics.totalPeriods == 3
    assert metrics.missingPeriods == 0

    # MAE = (20 + 50 + 0) / 3 = 70 / 3 ≈ 23.33
    assert metrics.maeMW == 23.33

    # RMSE = sqrt((400 + 2500 + 0) / 3) = sqrt(2900 / 3) ≈ 31.09
    expected_rmse = round(math.sqrt(2900 / 3), 2)
    assert metrics.rmseMW == expected_rmse

    # Bias = (20 - 50 + 0) / 3 = -30 / 3 = -10.00 (under-forecasting)
    assert metrics.biasMW == -10.00
    assert metrics.biasDirection == 'Under-forecasting'
    assert 'below actual generation' in metrics.biasExplanation

    # Max error = 50.0
    assert metrics.maxAbsoluteErrorMW == 50.00
    assert metrics.units == 'MW'


def test_positive_bias_interpretation():
    """Verify positive bias correctly indicates over-forecasting."""
    pairs = [(5000.0, 4500.0), (6000.0, 5200.0)]  # +500, +800
    metrics = calculate_metrics(pairs, total_periods=2)

    assert metrics.biasMW == 650.0
    assert metrics.biasDirection == 'Over-forecasting'
    assert 'above actual generation' in metrics.biasExplanation


def test_zero_bias_interpretation():
    """Verify zero bias indicates unbiased forecasting."""
    pairs = [(1000.0, 900.0), (1000.0, 1100.0)]  # +100, -100
    metrics = calculate_metrics(pairs, total_periods=2)

    assert metrics.biasMW == 0.0
    assert metrics.biasDirection == 'Unbiased'


def test_missing_records_exclusion_and_reporting():
    """Verify missing periods are excluded from error calculations but counted."""
    # Total periods 4, but only 2 have matched pairs
    pairs = [(1000.0, 900.0), (2000.0, 1900.0)]
    metrics = calculate_metrics(pairs, total_periods=4)

    assert metrics.matchedPeriods == 2
    assert metrics.totalPeriods == 4
    assert metrics.missingPeriods == 2
    assert metrics.maeMW == 100.0
    assert metrics.biasMW == 100.0


def test_all_records_missing_returns_none_safely():
    """Verify empty/missing dataset does not divide by zero and returns None metrics."""
    metrics = calculate_metrics([], total_periods=48)

    assert metrics.matchedPeriods == 0
    assert metrics.totalPeriods == 48
    assert metrics.missingPeriods == 48
    assert metrics.maeMW is None
    assert metrics.rmseMW is None
    assert metrics.biasMW is None
    assert metrics.maxAbsoluteErrorMW is None
    assert metrics.biasDirection == 'Insufficient data'


def test_single_date_evaluation_chart_data():
    """Verify evaluate_single_date_accuracy generates chart-ready half-hour data."""
    replay = ForecastReplayResponse(
        decisionDate=date(2026, 1, 10),
        cutoffTime=time(14, 0),
        cutoffUtc=utc_dt('2026-01-10T14:00:00Z'),
        deliveryDate=date(2026, 1, 11),
        timezone='Europe/London',
        totalPeriods=2,
        periodsWithForecast=2,
        periodsWithActual=1,
        periods=[
            PeriodEvaluation(
                startTime=utc_dt('2026-01-11T00:00:00Z'),
                settlementPeriod=1,
                forecastGenerationMW=4000.0,
                forecastPublishTime=utc_dt('2026-01-10T13:30:00Z'),
                actualGenerationMW=3800.0,
                differenceMW=200.0,
            ),
            PeriodEvaluation(
                startTime=utc_dt('2026-01-11T00:30:00Z'),
                settlementPeriod=2,
                forecastGenerationMW=4100.0,
                forecastPublishTime=utc_dt('2026-01-10T13:30:00Z'),
                actualGenerationMW=None,  # Missing actual
                differenceMW=None,
            ),
        ],
    )

    result = evaluate_single_date_accuracy(
        decision_date=date(2026, 1, 10),
        cutoff_time=time(14, 0),
        replay=replay,
    )

    assert result.decisionDate == date(2026, 1, 10)
    assert result.deliveryDate == date(2026, 1, 11)
    assert len(result.chartData) == 2

    # First period: matched
    p1 = result.chartData[0]
    assert p1.forecastMW == 4000.0
    assert p1.actualMW == 3800.0
    assert p1.signedErrorMW == 200.0
    assert p1.absoluteErrorMW == 200.0

    # Second period: missing actual
    p2 = result.chartData[1]
    assert p2.forecastMW == 4100.0
    assert p2.actualMW is None
    assert p2.signedErrorMW is None
    assert p2.absoluteErrorMW is None

    # Overall metrics for this day: 1 matched, 1 missing
    assert result.metrics.matchedPeriods == 1
    assert result.metrics.missingPeriods == 1
    assert result.metrics.maeMW == 200.0
    assert result.metrics.biasMW == 200.0


def test_historical_accuracy_aggregation():
    """Verify evaluate_historical_accuracy aggregates multiple days and period profiles."""
    day1 = ForecastReplayResponse(
        decisionDate=date(2026, 1, 1),
        cutoffTime=time(14, 0),
        cutoffUtc=utc_dt('2026-01-01T14:00:00Z'),
        deliveryDate=date(2026, 1, 2),
        timezone='Europe/London',
        totalPeriods=2,
        periodsWithForecast=2,
        periodsWithActual=2,
        periods=[
            PeriodEvaluation(
                startTime=utc_dt('2026-01-02T00:00:00Z'),
                settlementPeriod=1,
                forecastGenerationMW=5000.0,
                actualGenerationMW=4800.0,
                differenceMW=200.0,
            ),
            PeriodEvaluation(
                startTime=utc_dt('2026-01-02T00:30:00Z'),
                settlementPeriod=2,
                forecastGenerationMW=6000.0,
                actualGenerationMW=6200.0,
                differenceMW=-200.0,
            ),
        ],
    )

    day2 = ForecastReplayResponse(
        decisionDate=date(2026, 1, 2),
        cutoffTime=time(14, 0),
        cutoffUtc=utc_dt('2026-01-02T14:00:00Z'),
        deliveryDate=date(2026, 1, 3),
        timezone='Europe/London',
        totalPeriods=2,
        periodsWithForecast=2,
        periodsWithActual=2,
        periods=[
            PeriodEvaluation(
                startTime=utc_dt('2026-01-03T00:00:00Z'),
                settlementPeriod=1,
                forecastGenerationMW=5200.0,
                actualGenerationMW=5100.0,
                differenceMW=100.0,
            ),
            PeriodEvaluation(
                startTime=utc_dt('2026-01-03T00:30:00Z'),
                settlementPeriod=2,
                forecastGenerationMW=5800.0,
                actualGenerationMW=5900.0,
                differenceMW=-100.0,
            ),
        ],
    )

    result = evaluate_historical_accuracy(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        cutoff_time=time(14, 0),
        daily_replays=[day1, day2],
        use_cache=False,
    )

    assert result.totalDecisionDays == 2
    assert len(result.dailyBreakdown) == 2

    # Overall: 4 pairs: +200, -200, +100, -100
    # MAE = (200 + 200 + 100 + 100) / 4 = 150.0
    # Bias = (200 - 200 + 100 - 100) / 4 = 0.0
    assert result.overallMetrics.matchedPeriods == 4
    assert result.overallMetrics.maeMW == 150.0
    assert result.overallMetrics.biasMW == 0.0
    assert result.overallMetrics.maxAbsoluteErrorMW == 200.0

    # Period profiles
    assert len(result.periodProfile) == 2
    sp1 = result.periodProfile[0]
    assert sp1.settlementPeriod == 1
    assert sp1.sampleCount == 2
    # sp1 forecasts: 5000, 5200 -> avg 5100; actuals: 4800, 5100 -> avg 4950; bias: +150
    assert sp1.averageForecastMW == 5100.0
    assert sp1.averageActualMW == 4950.0
    assert sp1.averageBiasMW == 150.0


def test_accuracy_endpoint_validation():
    """Verify endpoint input validation for /api/accuracy and /api/historical-accuracy."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        # Missing decision_date on /api/accuracy returns 422
        res1 = client.get('/api/accuracy')
        assert res1.status_code == 422

        # Invalid date format returns 422
        res2 = client.get('/api/accuracy?decision_date=not-a-date')
        assert res2.status_code == 422

        # Reversed dates on /api/historical-accuracy returns 422
        res3 = client.get('/api/historical-accuracy?start_date=2026-01-15&end_date=2026-01-10')
        assert res3.status_code == 422


def test_accuracy_endpoint_success(monkeypatch):
    """Verify GET /api/accuracy returns 200 and SingleDateAccuracyResponse structure."""
    from fastapi.testclient import TestClient
    from app.main import app
    import app.api.accuracy as api_accuracy
    from app.schemas.accuracy import AccuracyMetrics, SingleDateAccuracyResponse, ChartPeriodRecord

    fake_resp = SingleDateAccuracyResponse(
        decisionDate=date(2026, 1, 10),
        deliveryDate=date(2026, 1, 11),
        cutoffTime=time(14, 0),
        cutoffUtc=utc_dt('2026-01-10T14:00:00Z'),
        timezone='Europe/London',
        metrics=AccuracyMetrics(
            matchedPeriods=1,
            totalPeriods=1,
            missingPeriods=0,
            maeMW=120.0,
            rmseMW=120.0,
            biasMW=120.0,
            maxAbsoluteErrorMW=120.0,
            biasDirection='Over-forecasting',
            biasExplanation='Over-forecasting by 120 MW',
        ),
        chartData=[
            ChartPeriodRecord(
                startTime=utc_dt('2026-01-11T00:00:00Z'),
                settlementPeriod=1,
                forecastMW=5000.0,
                actualMW=4880.0,
                signedErrorMW=120.0,
                absoluteErrorMW=120.0,
            )
        ],
    )

    monkeypatch.setattr(
        api_accuracy,
        'evaluate_single_date_accuracy',
        lambda decision_date, cutoff_time: fake_resp,
    )

    with TestClient(app) as client:
        response = client.get('/api/accuracy?decision_date=2026-01-10')

    assert response.status_code == 200
    data = response.json()
    assert data['metrics']['maeMW'] == 120.0
    assert data['metrics']['biasDirection'] == 'Over-forecasting'
    assert len(data['chartData']) == 1
    assert data['chartData'][0]['signedErrorMW'] == 120.0

