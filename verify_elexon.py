"""Opt-in live API verification: python verify_elexon.py. No mocked responses."""
import json
from datetime import date, datetime, timezone

from app.services.elexon_client import ElexonClient


def summary(rows):
    # Summarize rather than printing thousands of records. default=None handles
    # an empty result; model_dump(mode='json') makes dates JSON-compatible.
    # sum counts True as 1, giving the number of rows with February delivery.
    return {
        'count': len(rows),
        'firstDelivery': min((r.startTime for r in rows), default=None),
        'lastDelivery': max((r.startTime for r in rows), default=None),
        'februaryDeliveryRecords': sum(r.startTime.month == 2 for r in rows),
        'sample': rows[0].model_dump(mode='json') if rows else None,
    }


if __name__ == '__main__':
    # Python sets __name__ to '__main__' for a directly executed script. If another
    # file imports it, __name__ is the module name and this condition is false.
    # This block runs only when this file is executed directly, not imported.
    # Unlike unit tests, it uses real HTTP requests. No web server is necessary.
    # End dates are exclusive: February 4 includes actuals through February 3;
    # February 1 includes forecasts published throughout January.
    with ElexonClient() as client:
        actual = client.actual_wind(date(2026, 1, 1), date(2026, 2, 4))
        forecast = client.forecast_wind(datetime(2026, 1, 1, tzinfo=timezone.utc),
                                        datetime(2026, 2, 1, tzinfo=timezone.utc))
    report = {'verifiedAt': datetime.now(timezone.utc).isoformat(),
              'actual': summary(actual), 'forecast': summary(forecast)}
    print(json.dumps(report, indent=2, default=str))
    # Exit with an error if the requested historical data/February forecasts are
    # absent. This checks basic coverage, not the accuracy of each forecast value.
    if not actual or not forecast or not any(r.startTime.month == 2 for r in forecast):
        raise SystemExit('Historical coverage verification failed; inspect the report.')
