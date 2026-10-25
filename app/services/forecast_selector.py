"""Historical purchasing-decision replay service for WindSight.

Business Rule:
A market participant making an electricity purchasing decision can only use forecasts
published at or before that decision cutoff time. Any forecast published afterward
represents future information that was not available at the decision point and must
be strictly excluded (preventing lookahead bias).

Assumed Cutoff Policy:
Day-ahead electricity wholesale purchasing in the UK market typically concludes in
the early afternoon (e.g., day-ahead auctions close around 11:00-12:00, with bilateral
trading wrapping up thereafter). Since the assignment specification does not specify an
explicit cutoff time, 14:00 UK local time (Europe/London) is documented and used as
the standard default cutoff. All purchasing decisions evaluate forecasts against this deadline.
"""
# date and datetime manage calendar dates and timestamps; time represents clock times (e.g. 14:00).
# timedelta represents time durations (e.g. +1 day or +30 minutes).
# timezone.utc is Python's UTC timezone constant.
from datetime import date, datetime, time, timedelta, timezone
# ZoneInfo uses the standard IANA time zone database (tzdata) to correctly calculate daylight saving time.
from zoneinfo import ZoneInfo

# Import Pydantic models from our schemas directory.
from app.schemas.elexon import ActualWindRecord, ForecastWindRecord
from app.schemas.forecast import ForecastReplayResponse, PeriodEvaluation
from app.services.elexon_client import ElexonClient

# Standard UK market timezone.
# Handles Greenwich Mean Time (GMT, UTC+0) in winter and British Summer Time (BST, UTC+1) in summer.
LONDON_TZ = ZoneInfo('Europe/London')

# Documented default assumed decision cutoff: 14:00 UK local time.
# In winter (GMT), 14:00 London corresponds to 14:00 UTC.
# In summer (BST), 14:00 London corresponds to 13:00 UTC.
DEFAULT_CUTOFF_TIME = time(14, 0)


def get_decision_cutoff_utc(decision_date: date, cutoff_time: time = DEFAULT_CUTOFF_TIME) -> datetime:
    """Converts a local UK decision date and cutoff time into a normalized UTC timestamp."""
    # datetime.combine merges a date (YYYY-MM-DD) and a time (HH:MM:SS) into a single datetime object.
    # tzinfo=LONDON_TZ attaches the Europe/London timezone to ensure daylight-saving awareness.
    local_cutoff = datetime.combine(decision_date, cutoff_time, tzinfo=LONDON_TZ)
    # astimezone(timezone.utc) converts the timestamp to UTC while preserving the exact instant in time.
    return local_cutoff.astimezone(timezone.utc)


def get_delivery_date(decision_date: date) -> date:
    """Calculates the following delivery date: decision_date + 1 calendar day."""
    # Day-ahead contracts target power delivery on the calendar day following the decision.
    # timedelta(days=1) correctly advances across calendar months and leap years.
    return decision_date + timedelta(days=1)


def generate_delivery_periods(delivery_date: date) -> list[tuple[int, datetime]]:
    """Generates the half-hourly settlement periods for the target delivery date in UTC.

    In the GB electricity market, the settlement day runs from 00:00 to 24:00 local time
    (Europe/London). Standard days contain 48 half-hour periods; clock-change days contain
    46 (spring) or 50 (autumn).
    """
    # Start at 00:00:00 local London time on the delivery date.
    start_local = datetime.combine(delivery_date, time(0, 0), tzinfo=LONDON_TZ)
    # End at 00:00:00 local London time on the subsequent day (exclusive upper bound).
    end_local = datetime.combine(delivery_date + timedelta(days=1), time(0, 0), tzinfo=LONDON_TZ)

    # Convert the local boundaries into UTC for consistent database and API comparisons.
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    # Build the list of half-hour periods. Each item is a (period_number, start_time_utc) tuple.
    periods: list[tuple[int, datetime]] = []
    current = start_utc
    period_number = 1
    # Loop advances in 30-minute steps until reaching the day's end boundary.
    while current < end_utc:
        periods.append((period_number, current))
        current += timedelta(minutes=30)
        period_number += 1

    return periods


def select_forecasts(
    decision_date: date,
    cutoff_time: time = DEFAULT_CUTOFF_TIME,
    forecasts: list[ForecastWindRecord] | None = None,
    actuals: list[ActualWindRecord] | None = None,
) -> ForecastReplayResponse:
    """Core selection algorithm: selects the latest forecast published at or before cutoff.

    Guarantees:
    - Never uses a forecast published after the decision cutoff (strictly enforced).
    - Selects the most recent forecast (latest publishTime) for each delivery period.
    - Matches actual generation by delivery startTime.
    - Handles missing forecasts and missing actuals gracefully by returning None.
    - Preserves forecast publication timestamps for transparency.
    """
    # Calculate the normalized UTC decision cutoff timestamp.
    cutoff_utc = get_decision_cutoff_utc(decision_date, cutoff_time)
    # Target delivery date is automatically the following day.
    delivery_date = get_delivery_date(decision_date)
    # Generate the half-hour delivery periods that make up this delivery date.
    expected_periods = generate_delivery_periods(delivery_date)

    # Default None to an empty list to prevent TypeError when iterating.
    forecast_list = forecasts or []
    actual_list = actuals or []

    # Step 1: Filter forecasts to strictly enforce publishTime <= cutoff_utc (Rule 5 & Rule 6).
    # Group eligible candidate forecasts by their target delivery startTime.
    # A dictionary mapping delivery startTime (datetime) -> list of eligible ForecastWindRecords.
    eligible_by_start: dict[datetime, list[ForecastWindRecord]] = {}
    for f in forecast_list:
        # Strictly exclude any forecast published AFTER the cutoff deadline.
        # This prevents lookahead bias (data leakage) in historical backtesting.
        if f.publishTime <= cutoff_utc:
            # setdefault initializes an empty list for a new startTime if it does not yet exist.
            eligible_by_start.setdefault(f.startTime, []).append(f)

    # Step 2: For each delivery period, select the latest eligible forecast (max publishTime).
    selected_forecasts: dict[datetime, ForecastWindRecord] = {}
    for start_time, candidates in eligible_by_start.items():
        # max with key=lambda selects the record with the most recent publication timestamp.
        # This represents the freshest information available to the buyer at the decision cutoff.
        selected_forecasts[start_time] = max(candidates, key=lambda rec: rec.publishTime)

    # Step 3: Index actual generation records by startTime for fast O(1) dictionary lookups.
    actuals_by_start: dict[datetime, ActualWindRecord] = {
        a.startTime: a for a in actual_list
    }

    # Step 4: Iterate through all expected 48 half-hour settlement periods and match records.
    period_evaluations: list[PeriodEvaluation] = []
    count_forecasts = 0
    count_actuals = 0

    for period_num, period_start_utc in expected_periods:
        # Retrieve the selected forecast (or None if no forecast was published before cutoff).
        forecast_rec = selected_forecasts.get(period_start_utc)
        # Retrieve the matched actual generation (or None if missing / not yet published).
        actual_rec = actuals_by_start.get(period_start_utc)

        # Extract generation values in MW. If missing, keep None (do not substitute zeros!).
        forecast_mw = forecast_rec.generation if forecast_rec else None
        forecast_pub = forecast_rec.publishTime if forecast_rec else None
        actual_mw = actual_rec.generation if actual_rec else None

        # Count successful matches for reporting.
        if forecast_mw is not None:
            count_forecasts += 1
        if actual_mw is not None:
            count_actuals += 1

        # Calculate difference (forecast - actual) only if both records are present.
        difference = None
        if forecast_mw is not None and actual_mw is not None:
            difference = round(forecast_mw - actual_mw, 2)

        # Append period evaluation preserving publication time for auditability.
        period_evaluations.append(
            PeriodEvaluation(
                startTime=period_start_utc,
                settlementPeriod=period_num,
                forecastGenerationMW=forecast_mw,
                forecastPublishTime=forecast_pub,
                actualGenerationMW=actual_mw,
                differenceMW=difference,
            )
        )

    # Return structured Pydantic response containing metadata and period-by-period results.
    return ForecastReplayResponse(
        decisionDate=decision_date,
        cutoffTime=cutoff_time,
        cutoffUtc=cutoff_utc,
        deliveryDate=delivery_date,
        timezone='Europe/London',
        totalPeriods=len(expected_periods),
        periodsWithForecast=count_forecasts,
        periodsWithActual=count_actuals,
        periods=period_evaluations,
    )


def replay_purchasing_decision(
    decision_date: date,
    cutoff_time: time = DEFAULT_CUTOFF_TIME,
    client: ElexonClient | None = None,
) -> ForecastReplayResponse:
    """Fetches data from Elexon and replays the purchasing decision.

    Can accept an existing ElexonClient (e.g. in tests) or instantiate one automatically.
    """
    # Compute the cutoff instant and target delivery date boundaries.
    cutoff_utc = get_decision_cutoff_utc(decision_date, cutoff_time)
    delivery_date = get_delivery_date(decision_date)
    periods = generate_delivery_periods(delivery_date)

    # Delivery boundaries: start of first period to end of last period (30 minutes after start).
    delivery_start_utc = periods[0][1]
    delivery_end_utc = periods[-1][1] + timedelta(minutes=30)

    # Query window for forecast publications:
    # Elexon WINDFOR forecasts targeting delivery are issued in the days leading up to the cutoff.
    # Searching 7 days before cutoff easily covers all published day-ahead forecast runs.
    publish_from_utc = cutoff_utc - timedelta(days=7)
    # Add a 1-second buffer to Elexon's exclusive publication upper bound
    # so a forecast published exactly at cutoff_utc is retrieved from the stream.
    publish_to_utc = cutoff_utc + timedelta(seconds=1)

    # Internal helper to perform HTTP requests using the provided client instance.
    def fetch_data(c: ElexonClient):
        # Fetch FUELHH actual generation for the delivery date (settlement date).
        # Upper date bound is exclusive in ElexonClient, so +1 day queries exactly delivery_date.
        actuals = c.actual_wind(delivery_date, delivery_date + timedelta(days=1))
        # Fetch WINDFOR forecasts targeting the delivery date published up to the cutoff.
        forecasts = c.forecast_wind(
            publish_from=publish_from_utc,
            publish_to=publish_to_utc,
            delivery_from=delivery_start_utc,
            delivery_to=delivery_end_utc,
        )
        return forecasts, actuals

    # Reuse an existing client if supplied (e.g. for mocked unit testing),
    # otherwise use a context manager to ensure HTTP connections close automatically.
    if client is not None:
        forecasts, actuals = fetch_data(client)
    else:
        with ElexonClient() as c:
            forecasts, actuals = fetch_data(c)

    # Pass the retrieved records into the pure selection algorithm.
    return select_forecasts(
        decision_date=decision_date,
        cutoff_time=cutoff_time,
        forecasts=forecasts,
        actuals=actuals,
    )
