"""Elexon stream client. Public methods use half-open [from, to) ranges.

FUELHH is filtered by settlement date; WINDFOR by publication time, never
implicitly by delivery time. Stream responses are JSON arrays, not {data: ...}.
"""
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
from pydantic import ValidationError

from app.schemas.elexon import ActualWindRecord, ForecastWindRecord

BASE_URL = 'https://data.elexon.co.uk/bmrs/api/v1'
CHUNK = timedelta(days=7)  # Conservative batch size, not a documented stream limit.


class ElexonError(Exception):
    def __init__(self, message, *, status_code=502, upstream_status=None):
        super().__init__(message)
        self.status_code = status_code
        self.upstream_status = upstream_status


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timestamps must include a timezone (Z or an offset).')
    return value.astimezone(timezone.utc)


def validate_range(start, end):
    if start >= end:
        raise ValueError('The end must be after the start; the end is exclusive.')
    if end - start > timedelta(days=366):
        raise ValueError('Request at most 366 days at a time (application limit).')


def windows(start, end):
    validate_range(start, end)
    while start < end:
        stop = min(start + CHUNK, end)
        yield start, stop
        start = stop


class ElexonClient:
    def __init__(self, *, transport=None, sleep=time.sleep):
        self._http = httpx.Client(
            base_url=BASE_URL,
            timeout=httpx.Timeout(45.0, connect=10.0, pool=10.0),
            headers={'Accept': 'application/json', 'User-Agent': 'WindSight/0.1'},
            transport=transport,
        )
        self._sleep = sleep

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._http.close()

    def _request(self, dataset, params, model):
        for attempt in range(3):
            try:
                response = self._http.get(f'/datasets/{dataset}/stream', params=params)
            except httpx.RequestError as exc:
                if attempt < 2:
                    self._sleep(2 ** attempt)
                    continue
                timeout = isinstance(exc, httpx.TimeoutException)
                raise ElexonError(
                    'Elexon request timed out.' if timeout else 'Unable to connect to Elexon.',
                    status_code=504 if timeout else 502,
                ) from exc
            retryable = response.status_code == 429 or response.status_code in (500, 502, 503, 504)
            if retryable and attempt < 2:
                delay = float(2 ** attempt)
                retry_after = response.headers.get('Retry-After')
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(delay, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                if delay <= 30:
                    self._sleep(delay)
                    continue
                # Do not retry earlier than the upstream server permits.
            if response.is_error:
                raise ElexonError(
                    f'Elexon returned HTTP {response.status_code} for {dataset}.',
                    status_code=503 if retryable else 502,
                    upstream_status=response.status_code,
                )
            try:
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError('Expected a JSON array')
                return [model.model_validate(row) for row in payload]
            except (ValueError, ValidationError) as exc:
                raise ElexonError(f'Invalid Elexon {dataset} response; no partial data returned.') from exc
        raise AssertionError('Unreachable retry state')

    @staticmethod
    def _sorted_unique(records):
        # Preserve different forecasts/revisions for the same delivery time.
        unique = {record.model_dump_json(): record for record in records}
        return sorted(unique.values(), key=lambda r: (r.startTime, r.publishTime, r.generation))

    def actual_wind(self, start: date, end: date):
        records = []
        for lower, upper in windows(start, end):
            batch = self._request('FUELHH', {
                'settlementDateFrom': lower.isoformat(),
                'settlementDateTo': (upper - timedelta(days=1)).isoformat(),
                'fuelType': 'WIND',
            }, ActualWindRecord)
            records.extend(row for row in batch if lower <= row.settlementDate < upper)
        return self._sorted_unique(records)

    def forecast_wind(self, publish_from: datetime, publish_to: datetime,
                      *, delivery_from: datetime | None = None, delivery_to: datetime | None = None):
        start, end = utc(publish_from), utc(publish_to)
        delivery_from = utc(delivery_from) if delivery_from else None
        delivery_to = utc(delivery_to) if delivery_to else None
        if delivery_from and delivery_to:
            validate_range(delivery_from, delivery_to)
        records = []
        for lower, upper in windows(start, end):
            batch = self._request('WINDFOR', {
                'publishDateTimeFrom': lower.isoformat().replace('+00:00', 'Z'),
                'publishDateTimeTo': upper.isoformat().replace('+00:00', 'Z'),
            }, ForecastWindRecord)
            records.extend(row for row in batch if lower <= row.publishTime < upper
                           and (delivery_from is None or row.startTime >= delivery_from)
                           and (delivery_to is None or row.startTime < delivery_to))
        return self._sorted_unique(records)
