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
from datetime import date, datetime, time, timedelta
# Sequence allows accepting lists, tuples, or any ordered collection.
from typing import Sequence

# Pydantic schema models for accuracy responses and chart data.
from app.schemas.accuracy import (
    AccuracyMetrics,
    ChartPeriodRecord,
    DailyAccuracyRecord,
    HistoricalAccuracyResponse,
    PeriodProfileRecord,
    SingleDateAccuracyResponse,
)
from app.schemas.forecast import ForecastReplayResponse, PeriodEvaluation
from app.services.elexon_client import ElexonClient
from app.services.forecast_selector import (
    DEFAULT_CUTOFF_TIME,
    generate_delivery_periods,
    get_decision_cutoff_utc,
    get_delivery_date,
    replay_purchasing_decision,
    select_forecasts,
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


# In-memory cache for historical accuracy evaluation to avoid repeated slow downloads.
_HISTORICAL_CACHE: dict[tuple[date, date, time], HistoricalAccuracyResponse] = {}


def evaluate_historical_accuracy(
    start_date: date = date(2026, 1, 1),
    end_date: date = date(2026, 1, 31),
    cutoff_time: time = DEFAULT_CUTOFF_TIME,
    client: ElexonClient | None = None,
    daily_replays: list[ForecastReplayResponse] | None = None,
    use_cache: bool = True,
) -> HistoricalAccuracyResponse:
    """Calculates aggregate and daily accuracy metrics across a historical date range.

    Defaults to January 2026 purchasing decisions (2026-01-01 to 2026-01-31).
    """
    # Check in-memory cache first to return cached multi-day analytics immediately.
    cache_key = (start_date, end_date, cutoff_time)
    if use_cache and daily_replays is None and cache_key in _HISTORICAL_CACHE:
        return _HISTORICAL_CACHE[cache_key]

    replays: list[ForecastReplayResponse] = []

    # If unit tests provide synthetic daily replays, use them directly without network calls.
    if daily_replays is not None:
        replays = daily_replays
    else:
        # Determine overall delivery date range: from day after start_date to day after end_date.
        first_delivery = get_delivery_date(start_date)
        last_delivery = get_delivery_date(end_date)
        # End date in ElexonClient is exclusive; add 1 day so last_delivery is included.
        end_actual_query = last_delivery + timedelta(days=1)

        # Publications needed: forecasts published up to the cutoff for the last decision date.
        last_cutoff_utc = get_decision_cutoff_utc(end_date, cutoff_time)
        first_publish_utc = get_decision_cutoff_utc(start_date, cutoff_time) - timedelta(days=7)
        last_publish_utc = last_cutoff_utc + timedelta(seconds=1)

        # Delivery time span needed across the entire month.
        first_delivery_periods = generate_delivery_periods(first_delivery)
        last_delivery_periods = generate_delivery_periods(last_delivery)
        overall_delivery_start_utc = first_delivery_periods[0][1]
        overall_delivery_end_utc = last_delivery_periods[-1][1] + timedelta(minutes=30)

        # Fetch bulk monthly data in 1 pass to avoid making 31 separate HTTP requests.
        def fetch_all(c: ElexonClient):
            all_actuals = c.actual_wind(first_delivery, end_actual_query)
            all_forecasts = c.forecast_wind(
                publish_from=first_publish_utc,
                publish_to=last_publish_utc,
                delivery_from=overall_delivery_start_utc,
                delivery_to=overall_delivery_end_utc,
            )
            return all_forecasts, all_actuals

        if client is not None:
            forecasts, actuals = fetch_all(client)
        else:
            with ElexonClient() as c:
                forecasts, actuals = fetch_all(c)

        # Run selection for each decision day in memory.
        current_date = start_date
        while current_date <= end_date:
            rep = select_forecasts(
                decision_date=current_date,
                cutoff_time=cutoff_time,
                forecasts=forecasts,
                actuals=actuals,
            )
            replays.append(rep)
            current_date += timedelta(days=1)

    # Aggregate metrics across all matched periods in all days.
    all_matched_pairs: list[tuple[float, float]] = []
    total_periods_all_days = 0
    daily_breakdown: list[DailyAccuracyRecord] = []

    # Map for settlement period profile: settlementPeriod (1-48/50) -> list of (forecast, actual) pairs.
    period_profiles_map: dict[int, list[tuple[float, float]]] = {}

    for replay in replays:
        day_pairs: list[tuple[float, float]] = []
        for period in replay.periods:
            total_periods_all_days += 1
            f = period.forecastGenerationMW
            a = period.actualGenerationMW
            if f is not None and a is not None:
                day_pairs.append((f, a))
                all_matched_pairs.append((f, a))
                period_profiles_map.setdefault(period.settlementPeriod, []).append((f, a))

        # Daily accuracy statistics for this specific calendar day.
        day_metrics = calculate_metrics(day_pairs, total_periods=len(replay.periods))
        daily_breakdown.append(
            DailyAccuracyRecord(
                decisionDate=replay.decisionDate,
                deliveryDate=replay.deliveryDate,
                matchedPeriods=day_metrics.matchedPeriods,
                totalPeriods=day_metrics.totalPeriods,
                maeMW=day_metrics.maeMW,
                rmseMW=day_metrics.rmseMW,
                biasMW=day_metrics.biasMW,
                maxAbsoluteErrorMW=day_metrics.maxAbsoluteErrorMW,
            )
        )

    # Compute overall aggregate accuracy metrics across the entire month.
    overall_metrics = calculate_metrics(all_matched_pairs, total_periods=total_periods_all_days)

    # Build period profile: reveals if forecast errors concentrate at specific times of day
    # (e.g. morning ramp-up vs evening peak).
    period_profile_records: list[PeriodProfileRecord] = []
    for sp in sorted(period_profiles_map.keys()):
        sp_pairs = period_profiles_map[sp]
        sp_f = [p[0] for p in sp_pairs]
        sp_a = [p[1] for p in sp_pairs]
        sp_errors = [p[0] - p[1] for p in sp_pairs]

        avg_f = round(sum(sp_f) / len(sp_f), 2) if sp_f else None
        avg_a = round(sum(sp_a) / len(sp_a), 2) if sp_a else None
        avg_mae = round(sum(abs(e) for e in sp_errors) / len(sp_errors), 2) if sp_errors else None
        avg_bias = round(sum(sp_errors) / len(sp_errors), 2) if sp_errors else None

        period_profile_records.append(
            PeriodProfileRecord(
                settlementPeriod=sp,
                sampleCount=len(sp_pairs),
                averageForecastMW=avg_f,
                averageActualMW=avg_a,
                averageMaeMW=avg_mae,
                averageBiasMW=avg_bias,
            )
        )

    result = HistoricalAccuracyResponse(
        decisionDateFrom=start_date,
        decisionDateTo=end_date,
        totalDecisionDays=len(replays),
        cutoffTime=cutoff_time,
        overallMetrics=overall_metrics,
        dailyBreakdown=daily_breakdown,
        periodProfile=period_profile_records,
    )

    # Store result in cache when evaluating live data.
    if use_cache and daily_replays is None:
        _HISTORICAL_CACHE[cache_key] = result

    return result
