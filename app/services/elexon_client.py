"""Elexon stream client. Public methods use half-open [from, to) ranges.

FUELHH is filtered by settlement date; WINDFOR by publication time, never
implicitly by delivery time. Stream responses are JSON arrays, not {data: ...}.
"""
import time
# "import" loads a module; "from ... import ..." selects names from a module.
# Triple-quoted text above is a docstring (documentation attached to this module).
# Python uses indentation to group statements, instead of JavaScript's { } blocks.
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
from pydantic import ValidationError

from app.schemas.elexon import ActualWindRecord, ForecastWindRecord

BASE_URL = 'https://data.elexon.co.uk/bmrs/api/v1'
CHUNK = timedelta(days=7)  # Conservative batch size, not a documented stream limit.


class ElexonError(Exception):
    # "class" defines a type of object. (Exception) means inherit from Python's
    # built-in error type, so this class can be raised and caught like other errors.
    # A custom error separates external-service failures from invalid user input.
    # status_code is the response WindSight should send; upstream_status is the
    # HTTP code received from Elexon, or None when no HTTP response arrived.
    def __init__(self, message, *, status_code=502, upstream_status=None):
        # super() accesses the parent class; initialize its error message first.
        super().__init__(message)
        self.status_code = status_code
        self.upstream_status = upstream_status


def utc(value: datetime) -> datetime:
    # "def" defines a function. : datetime describes the argument's type;
    # -> datetime describes its return type. Python alone does not enforce hints.
    # "is None" checks for the special missing-value object; "or" means either
    # condition is enough. "return" ends the function and gives its result back.
    # Type hints describe expected inputs; this function also checks at runtime.
    # Refuse ambiguous local times, then convert an aware time to the same instant
    # in UTC. Never attach a timezone by guessing where the caller lives.
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timestamps must include a timezone (Z or an offset).')
    return value.astimezone(timezone.utc)


def validate_range(start, end):
    # All our ranges include start and exclude end: [January 1, February 1)
    # means every January day. Reject empty/reversed or excessively large ranges.
    if start >= end:
        raise ValueError('The end must be after the start; the end is exclusive.')
    if end - start > timedelta(days=366):
        raise ValueError('Request at most 366 days at a time (application limit).')


def windows(start, end):
    # Generator: yield gives the calling for-loop one (start, stop) pair at a time.
    # min keeps the last request inside the requested end, even for a short week.
    validate_range(start, end)
    while start < end:
        stop = min(start + CHUNK, end)
        # Unlike return, yield pauses the function. The next iteration resumes
        # after this line. "start, stop" packages the two values into a tuple.
        yield start, stop
        start = stop


class ElexonClient:
    # HTTPX is the Python HTTP client, similar to Axios. This service is separate
    # from FastAPI and can also be used by verify_elexon.py without a web server.
    def __init__(self, *, transport=None, sleep=time.sleep):
        # __init__ is a constructor; self is similar to "this" in JavaScript.
        # The * requires named optional arguments. Tests inject a fake transport
        # and a no-wait sleep function; normal use contacts the real Elexon API.
        # One client reuses connections across chunks. Timeouts apply per network
        # operation, not to the total duration of a multi-request monthly download.
        self._http = httpx.Client(
            base_url=BASE_URL,
            timeout=httpx.Timeout(45.0, connect=10.0, pool=10.0),
            headers={'Accept': 'application/json', 'User-Agent': 'WindSight/0.1'},
            transport=transport,
        )
        self._sleep = sleep

    def __enter__(self):
        # Called by "with ElexonClient() as client"; the returned object is client.
        return self

    def __exit__(self, *_):
        # Called when leaving the with-block, including after exceptions.
        # *_ accepts unused exception details supplied by Python.
        self._http.close()

    def _request(self, dataset, params, model):
        # Internal helper (_ is a naming convention, not enforced privacy).
        # dataset chooses the URL, params become URL query parameters, and model
        # selects the Pydantic schema used to validate each returned record.
        # range(3) gives attempts 0, 1, 2: at most three tries for this chunk.
        for attempt in range(3):
            try:
                # try/except is Python's try/catch. "as exc" below names the
                # caught error. f'...{dataset}...' inserts a variable into a string,
                # like JavaScript template literals. params=params passes a named
                # argument: parameter name on the left, local value on the right.
                response = self._http.get(f'/datasets/{dataset}/stream', params=params)
            except httpx.RequestError as exc:
                # Network errors include timeouts and connection failures.
                # 2 ** attempt waits 1 second then 2 seconds before another try.
                # continue starts the next loop iteration; raise stops this call.
                if attempt < 2:
                    self._sleep(2 ** attempt)
                    continue
                # isinstance checks an object's type (including subclasses).
                # "504 if timeout else 502" is Python's conditional expression,
                # equivalent to timeout ? 504 : 502 in JavaScript.
                timeout = isinstance(exc, httpx.TimeoutException)
                raise ElexonError(
                    'Elexon request timed out.' if timeout else 'Unable to connect to Elexon.',
                    status_code=504 if timeout else 502,
                ) from exc
            # "in" checks membership in the tuple of codes. "==" compares values;
            # a single "=" assigns a value. "and" below requires both conditions.
            retryable = response.status_code == 429 or response.status_code in (500, 502, 503, 504)
            # 429 means too many requests; these 5xx codes may be temporary.
            # Other errors (for example 400) are returned without retrying.
            if retryable and attempt < 2:
                delay = float(2 ** attempt)
                retry_after = response.headers.get('Retry-After')
                if retry_after:
                    # Retry-After can be a number of seconds or an HTTP date.
                    # max prevents retrying sooner than either our normal delay
                    # or the server's requested wait. Bad headers keep the default.
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(delay, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            # A tuple after except catches any listed error type.
                            # pass means "do nothing here"; keep the existing delay.
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
                # JSON parsing produces Python lists/dictionaries. The stream API
                # returns [record, record], not {"data": [record, record]}.
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError('Expected a JSON array')
                # A list comprehension is like payload.map(validate) in JS.
                # Validate every row. One bad row fails the entire request so a
                # partial dataset cannot silently be presented as complete.
                return [model.model_validate(row) for row in payload]
            except (ValueError, ValidationError) as exc:
                raise ElexonError(f'Invalid Elexon {dataset} response; no partial data returned.') from exc
        raise AssertionError('Unreachable retry state')

    @staticmethod
    def _sorted_unique(records):
        # staticmethod needs no self because it only uses the provided records.
        # Each normalized record's JSON is a dictionary key: identical keys
        # collapse duplicates. Distinct publication times or values stay separate.
        # Preserve different forecasts/revisions for the same delivery time.
        # {key: value for ...} is a dictionary comprehension (build a lookup).
        # values() returns its stored records. lambda is an unnamed function;
        # the sorting key is a tuple: sort by startTime, then publishTime, then MW.
        unique = {record.model_dump_json(): record for record in records}
        return sorted(unique.values(), key=lambda r: (r.startTime, r.publishTime, r.generation))

    def actual_wind(self, start: date, end: date):
        # Request WIND actuals by settlement dates, never mix publication filters
        # into this query. Elexon includes settlementDateTo, while our end is
        # exclusive; subtract one day to translate between these conventions.
        records = []
        # Tuple unpacking: each window has two values, assigned to lower and upper.
        for lower, upper in windows(start, end):
            batch = self._request('FUELHH', {
                'settlementDateFrom': lower.isoformat(),
                'settlementDateTo': (upper - timedelta(days=1)).isoformat(),
                'fuelType': 'WIND',
            }, ActualWindRecord)
            # extend adds rows to the existing list (rather than nesting a list).
            # Recheck the requested interval locally before returning the records.
            # "lower <= value < upper" means both comparisons must be true.
            # "row for row in batch if ..." generates only the matching rows.
            records.extend(row for row in batch if lower <= row.settlementDate < upper)
        return self._sorted_unique(records)

    def forecast_wind(self, publish_from: datetime, publish_to: datetime,
                      *, delivery_from: datetime | None = None, delivery_to: datetime | None = None):
        # A forecast issued January 31 may predict February 2. Select publications
        # by publishTime and preserve those February startTime values by default.
        # None means "no delivery filter"; callers can request one independently.
        start, end = utc(publish_from), utc(publish_to)
        delivery_from = utc(delivery_from) if delivery_from else None
        delivery_to = utc(delivery_to) if delivery_to else None
        if delivery_from and delivery_to:
            validate_range(delivery_from, delivery_to)
        records = []
        for lower, upper in windows(start, end):
            # Z and +00:00 both mean UTC. Send explicit publication times so
            # Elexon returns historical versions rather than its latest forecast.
            batch = self._request('WINDFOR', {
                'publishDateTimeFrom': lower.isoformat().replace('+00:00', 'Z'),
                'publishDateTimeTo': upper.isoformat().replace('+00:00', 'Z'),
            }, ForecastWindRecord)
            # Use an exclusive upper boundary locally to avoid counting a record
            # twice when upstream responses include a shared chunk boundary.
            # Optional delivery filters use startTime and are applied here because
            # this Elexon endpoint does not accept delivery-range parameters.
            records.extend(row for row in batch if lower <= row.publishTime < upper
                           and (delivery_from is None or row.startTime >= delivery_from)
                           and (delivery_to is None or row.startTime < delivery_to))
        return self._sorted_unique(records)
