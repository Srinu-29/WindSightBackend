"""Opt-in live API verification: python verify_elexon.py. No mocked responses."""
import json
from datetime import date, datetime, timezone

from app.services.elexon_client import ElexonClient


def summary(rows):
    return {
        'count': len(rows),
        'firstDelivery': min((r.startTime for r in rows), default=None),
        'lastDelivery': max((r.startTime for r in rows), default=None),
        'februaryDeliveryRecords': sum(r.startTime.month == 2 for r in rows),
        'sample': rows[0].model_dump(mode='json') if rows else None,
    }


if __name__ == '__main__':
    with ElexonClient() as client:
        actual = client.actual_wind(date(2026, 1, 1), date(2026, 2, 4))
        forecast = client.forecast_wind(datetime(2026, 1, 1, tzinfo=timezone.utc),
                                        datetime(2026, 2, 1, tzinfo=timezone.utc))
    report = {'verifiedAt': datetime.now(timezone.utc).isoformat(),
              'actual': summary(actual), 'forecast': summary(forecast)}
    print(json.dumps(report, indent=2, default=str))
    if not actual or not forecast or not any(r.startTime.month == 2 for r in forecast):
        raise SystemExit('Historical coverage verification failed; inspect the report.')
