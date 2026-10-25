"""Forecast accuracy analysis service for WindSight.

Calculates standard power engineering error metrics in Megawatts (MW):
- Signed error = forecast - actual
- Absolute error = |forecast - actual|
- MAE (Mean Absolute Error) = average absolute error
- RMSE (Root Mean Squared Error) = sqrt(mean squared error)
- Bias = average signed error
- Max Absolute Error = maximum observed absolute error

Design Policy on Percentage Accuracy:
Percentage metrics (such as MAPE - Mean Absolute Percentage Error) are intentionally
omitted. Wind generation frequently approaches zero MW (e.g. during calm weather), which
causes division-by-near-zero distortion, producing absurdly high percentage errors that
do not reflect true operational impact. Absolute metrics in MW provide reliable,
industry-standard evaluation.

Bias Interpretation:
- Positive Bias (forecast > actual): Over-forecasting. Forecast was higher than actual
  output, meaning purchasers may have over-hedged or contracted more power than delivered.
- Negative Bias (forecast < actual): Under-forecasting. Forecast was lower than actual
  output, meaning excess unexpected power was generated.
- Zero Bias: Unbiased. No directional slant on average.
"""
# math provides sqrt() for Root Mean Squared Error (RMSE) calculation.
import math
# date/datetime/time/timedelta for manipulating calendar days and half-hour settlement periods.
from datetime import date, time
# Sequence allows accepting lists, tuples, or any ordered collection.
from typing import Sequence

# Pydantic schema models for accuracy responses and chart data.
from app.schemas.accuracy import (
    AccuracyMetrics,
    ChartPeriodRecord,
    SingleDateAccuracyResponse,
)
from app.schemas.forecast import ForecastReplayResponse
from app.services.elexon_client import ElexonClient
from app.services.forecast_selector import (
    DEFAULT_CUTOFF_TIME,
    replay_purchasing_decision,
)


def explain_bias(bias_mw: float | None) -> tuple[str, str]:
    """Generates the directional classification and operational explanation for bias.

    Returns:
        (biasDirection, biasExplanation) tuple of human-readable text.
    """
    # If no data was available, return a neutral status indicating insufficient data.
    if bias_mw is None:
        return 'Insufficient data', 'No matched forecast and actual records were available to compute bias.'

    rounded_bias = round(bias_mw, 2)
    # Positive bias means forecast was consistently higher than actual generation.
    if rounded_bias > 0:
        return (
            'Over-forecasting',
            f'Forecasts averaged {abs(rounded_bias):.2f} MW above actual generation (positive bias). '
            'Power buyers relying on these forecasts may have contracted excess volume.',
        )
    # Negative bias means forecast was consistently lower than actual generation.
    if rounded_bias < 0:
        return (
            'Under-forecasting',
            f'Forecasts averaged {abs(rounded_bias):.2f} MW below actual generation (negative bias). '
            'Actual wind output exceeded expectations, which may lead to imbalance settlement surpluses.',
        )
    # Exactly zero net bias means positive and negative differences balanced out.
    return 'Unbiased', 'Forecasts and actual generation had zero net difference on average.'


def calculate_metrics(
    pairs: Sequence[tuple[float, float]],
    total_periods: int,
) -> AccuracyMetrics:
    """Calculates MAE, RMSE, Bias, and Max Absolute Error from (forecast, actual) pairs.

    Records with missing forecast or actual values must be filtered out before
    passing to this function.
    """
    matched = len(pairs)
    # Missing periods are those where either forecast or actual was unavailable.
    missing = max(0, total_periods - matched)

    # Avoid ZeroDivisionError: return null/None metrics when no matched periods exist.
    if matched == 0:
        direction, explanation = explain_bias(None)
        return AccuracyMetrics(
            matchedPeriods=0,
            totalPeriods=total_periods,
            missingPeriods=total_periods,
            maeMW=None,
            rmseMW=None,
            biasMW=None,
            maxAbsoluteErrorMW=None,
            biasDirection=direction,
            biasExplanation=explanation,
        )

    # Calculate signed errors: e_i = forecast - actual (positive = over-forecast, negative = under-forecast).
    signed_errors = [f - a for f, a in pairs]
    # Absolute errors: |e_i| (magnitude of error regardless of direction).
    absolute_errors = [abs(e) for e in signed_errors]
    # Squared errors: e_i^2 (used for RMSE calculation to penalize larger errors).
    squared_errors = [e ** 2 for e in signed_errors]

    # Mean Absolute Error (MAE): arithmetic average of absolute errors.
    mae = sum(absolute_errors) / matched
    # Root Mean Squared Error (RMSE): square root of the mean squared error.
    rmse = math.sqrt(sum(squared_errors) / matched)
    # Forecast Bias: arithmetic average of signed errors (indicates persistent over/under forecasting).
    bias = sum(signed_errors) / matched
    # Maximum observed absolute deviation in the sample.
    max_error = max(absolute_errors)

    # Get human-readable classification of the bias.
    direction, explanation = explain_bias(bias)

    # Round all final metrics to 2 decimal places in Megawatts (MW).
    return AccuracyMetrics(
        matchedPeriods=matched,
        totalPeriods=total_periods,
        missingPeriods=missing,
        maeMW=round(mae, 2),
        rmseMW=round(rmse, 2),
        biasMW=round(bias, 2),
        maxAbsoluteErrorMW=round(max_error, 2),
        biasDirection=direction,
        biasExplanation=explanation,
    )


def evaluate_single_date_accuracy(
    decision_date: date,
    cutoff_time: time = DEFAULT_CUTOFF_TIME,
    client: ElexonClient | None = None,
    replay: ForecastReplayResponse | None = None,
) -> SingleDateAccuracyResponse:
    """Calculates accuracy metrics and chart-ready half-hour data for a single decision date."""
    # If a pre-computed replay response was not provided, fetch and replay from Elexon.
    if replay is None:
        replay = replay_purchasing_decision(decision_date, cutoff_time, client=client)

    chart_data: list[ChartPeriodRecord] = []
    valid_pairs: list[tuple[float, float]] = []

    # Iterate over each delivery period (typically 48 half-hour slots).
    for period in replay.periods:
        f = period.forecastGenerationMW
        a = period.actualGenerationMW
        signed_err = None
        abs_err = None

        # Only compute errors if BOTH forecast and actual values are present.
        if f is not None and a is not None:
            signed_err = round(f - a, 2)
            abs_err = round(abs(signed_err), 2)
            valid_pairs.append((f, a))

        # Format record into chart-ready structure for frontend line/bar charts.
        chart_data.append(
            ChartPeriodRecord(
                startTime=period.startTime,
                settlementPeriod=period.settlementPeriod,
                forecastMW=f,
                actualMW=a,
                signedErrorMW=signed_err,
                absoluteErrorMW=abs_err,
                forecastPublishTime=period.forecastPublishTime,
            )
        )

    # Calculate overall summary metrics across the valid pairs.
    metrics = calculate_metrics(valid_pairs, total_periods=len(replay.periods))

    return SingleDateAccuracyResponse(
        decisionDate=replay.decisionDate,
        deliveryDate=replay.deliveryDate,
        cutoffTime=replay.cutoffTime,
        cutoffUtc=replay.cutoffUtc,
        timezone=replay.timezone,
        metrics=metrics,
        chartData=chart_data,
    )
