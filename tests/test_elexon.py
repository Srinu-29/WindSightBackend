"""Service tests: controlled inputs check behavior without contacting Elexon.

Pytest discovers functions named test_*. An assert fails a test if its condition
is false. MockTransport replaces network calls with our handler function; these
synthetic records are never used by the application as a fallback data source.
"""
from datetime import date, datetime, timezone

import httpx
import pytest

from app.services.elexon_client import ElexonClient, ElexonError


def stamp(value):
    # Convert a test's readable UTC string into a timezone-aware Python datetime.
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def forecast(**updates):
    # In a function declaration ** collects named arguments, while * collects
    # positional arguments. This differs from ** between numbers (exponentiation).
    # **updates collects named arguments into a dictionary. The | merge lets a
    # test replace selected default fields without repeating the whole fixture.
    return dict(dataset='WINDFOR', publishTime='2026-01-31T22:30:00Z',
                startTime='2026-02-02T21:00:00Z', generation=1234) | updates


def test_forecast_keeps_february_delivery_and_all_versions():
    # Duplicate identical rows collapse; another publication survives. The row
    # exactly at the exclusive February 1 publication boundary is excluded.
    def handler(request):
        assert request.url.path == '/bmrs/api/v1/datasets/WINDFOR/stream'
        assert 'startTime' not in request.url.params
        return httpx.Response(200, json=[forecast(), forecast(),
            forecast(publishTime='2026-01-31T20:30:00Z'),
            forecast(publishTime='2026-02-01T00:00:00Z')])
    with ElexonClient(transport=httpx.MockTransport(handler)) as client:
        rows = client.forecast_wind(stamp('2026-01-31T00:00:00Z'), stamp('2026-02-01T00:00:00Z'))
    assert len(rows) == 2
    assert all(r.startTime.month == 2 and r.startTime.tzinfo == timezone.utc for r in rows)


def test_actual_date_chunking_and_wind_query():
    # Capture outgoing query parameters to verify the date chunks and WIND filter.
    requests = []
    def handler(request):
        requests.append(dict(request.url.params))
        return httpx.Response(200, json=[])
    with ElexonClient(transport=httpx.MockTransport(handler)) as client:
        assert client.actual_wind(date(2026, 1, 1), date(2026, 2, 4)) == []
    assert len(requests) == 5
    assert requests[0] == {'settlementDateFrom': '2026-01-01', 'settlementDateTo': '2026-01-07', 'fuelType': 'WIND'}
    assert requests[-1]['settlementDateTo'] == '2026-02-03'
    assert all('publishDateTimeFrom' not in r for r in requests)


def test_publication_chunk_boundary_once():
    # Simulate upstream returning the same boundary record in adjacent chunks.
    calls = []
    row = forecast(publishTime='2026-01-08T00:00:00Z')
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[row])
    with ElexonClient(transport=httpx.MockTransport(handler)) as client:
        rows = client.forecast_wind(stamp('2026-01-01T00:00:00Z'), stamp('2026-01-10T00:00:00Z'))
    assert len(calls) == 2
    assert len(rows) == 1


# parametrize runs the test once per bad payload. pytest.raises below means
# "this operation must raise this error"; returning successfully would fail.
@pytest.mark.parametrize('payload', [
    {'data': [forecast()]}, [forecast(startTime='2026-02-02T21:00:00')],
    [forecast(generation=None)], [forecast(generation='123')],
    [forecast(dataset='WRONG')], [forecast(generation=True)],
])
def test_invalid_upstream_response_fails_closed(payload):
    with ElexonClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        with pytest.raises(ElexonError, match='Invalid Elexon'):
            client.forecast_wind(stamp('2026-01-31T00:00:00Z'), stamp('2026-02-01T00:00:00Z'))


def test_retries_rate_limit_and_preserves_retry_after():
    # Return two rate limits, then success. Recording sleeps avoids real waits
    # while still checking that the client respects the two-second Retry-After.
    calls, sleeps = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After': '2'}) if len(calls) < 3 else httpx.Response(200, json=[])
    with ElexonClient(transport=httpx.MockTransport(handler), sleep=sleeps.append) as client:
        client.actual_wind(date(2026, 1, 1), date(2026, 1, 2))
    assert len(calls) == 3 and sleeps == [2, 2]


@pytest.mark.parametrize('status, expected', [(400, 502), (429, 503), (503, 503)])
def test_http_errors(status, expected):
    # "lambda _: None" accepts one argument but intentionally ignores it. The
    # underscore is a conventional name, not special discard syntax in Python.
    # Check the error code our backend will send and preserve Elexon's own code.
    with ElexonClient(transport=httpx.MockTransport(lambda _: httpx.Response(status)), sleep=lambda _: None) as client:
        with pytest.raises(ElexonError) as error:
            client.actual_wind(date(2026, 1, 1), date(2026, 1, 2))
    assert error.value.status_code == expected
    assert error.value.upstream_status == status


def test_timeout_is_bounded():
    # A permanently slow service must fail after three attempts, not loop forever.
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('test timeout', request=request)
    with ElexonClient(transport=httpx.MockTransport(handler), sleep=lambda _: None) as client:
        with pytest.raises(ElexonError) as error:
            client.actual_wind(date(2026, 1, 1), date(2026, 1, 2))
    assert error.value.status_code == 504 and len(calls) == 3


def test_validation_before_network_and_delivery_filter():
    # Reject ambiguous dates/ranges and check the independent delivery-time filter.
    with ElexonClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[forecast()]))) as client:
        with pytest.raises(ValueError, match='timezone'):
            client.forecast_wind(datetime(2026, 1, 1), stamp('2026-02-01T00:00:00Z'))
        with pytest.raises(ValueError, match='end'):
            client.actual_wind(date(2026, 1, 1), date(2026, 1, 1))
        rows = client.forecast_wind(stamp('2026-01-31T00:00:00Z'), stamp('2026-02-01T00:00:00Z'),
                                    delivery_to=stamp('2026-02-01T00:00:00Z'))
        assert rows == []


def test_actual_response_validation_and_offset_normalization():
    # Different timezone representations of the same instant normalize to UTC.
    # Returning GAS when WIND was requested is a data-contract failure.
    row = dict(dataset='FUELHH', fuelType='WIND', settlementDate='2026-01-01', settlementPeriod=1,
               startTime='2026-01-01T01:00:00+01:00', publishTime='2026-01-01T00:30:00Z', generation=123)
    with ElexonClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[row]))) as client:
        result = client.actual_wind(date(2026, 1, 1), date(2026, 1, 2))
        assert result[0].startTime == stamp('2026-01-01T00:00:00Z')
        row['fuelType'] = 'GAS'
        with pytest.raises(ElexonError):
            client.actual_wind(date(2026, 1, 1), date(2026, 1, 2))
