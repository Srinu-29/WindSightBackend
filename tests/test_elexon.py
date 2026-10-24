"""Synthetic fixtures are only used in isolated tests, never as live API data."""
from datetime import date, datetime, timezone

import httpx
import pytest

from app.services.elexon_client import ElexonClient, ElexonError


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def forecast(**updates):
    return dict(dataset='WINDFOR', publishTime='2026-01-31T22:30:00Z',
                startTime='2026-02-02T21:00:00Z', generation=1234) | updates


def test_forecast_keeps_february_delivery_and_all_versions():
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
    calls = []
    row = forecast(publishTime='2026-01-08T00:00:00Z')
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[row])
    with ElexonClient(transport=httpx.MockTransport(handler)) as client:
        rows = client.forecast_wind(stamp('2026-01-01T00:00:00Z'), stamp('2026-01-10T00:00:00Z'))
    assert len(calls) == 2
    assert len(rows) == 1


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
    calls, sleeps = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After': '2'}) if len(calls) < 3 else httpx.Response(200, json=[])
    with ElexonClient(transport=httpx.MockTransport(handler), sleep=sleeps.append) as client:
        client.actual_wind(date(2026, 1, 1), date(2026, 1, 2))
    assert len(calls) == 3 and sleeps == [2, 2]


@pytest.mark.parametrize('status, expected', [(400, 502), (429, 503), (503, 503)])
def test_http_errors(status, expected):
    with ElexonClient(transport=httpx.MockTransport(lambda _: httpx.Response(status)), sleep=lambda _: None) as client:
        with pytest.raises(ElexonError) as error:
            client.actual_wind(date(2026, 1, 1), date(2026, 1, 2))
    assert error.value.status_code == expected
    assert error.value.upstream_status == status


def test_timeout_is_bounded():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('test timeout', request=request)
    with ElexonClient(transport=httpx.MockTransport(handler), sleep=lambda _: None) as client:
        with pytest.raises(ElexonError) as error:
            client.actual_wind(date(2026, 1, 1), date(2026, 1, 2))
    assert error.value.status_code == 504 and len(calls) == 3


def test_validation_before_network_and_delivery_filter():
    with ElexonClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[forecast()]))) as client:
        with pytest.raises(ValueError, match='timezone'):
            client.forecast_wind(datetime(2026, 1, 1), stamp('2026-02-01T00:00:00Z'))
        with pytest.raises(ValueError, match='end'):
            client.actual_wind(date(2026, 1, 1), date(2026, 1, 1))
        rows = client.forecast_wind(stamp('2026-01-31T00:00:00Z'), stamp('2026-02-01T00:00:00Z'),
                                    delivery_to=stamp('2026-02-01T00:00:00Z'))
        assert rows == []


def test_actual_response_validation_and_offset_normalization():
    row = dict(dataset='FUELHH', fuelType='WIND', settlementDate='2026-01-01', settlementPeriod=1,
               startTime='2026-01-01T01:00:00+01:00', publishTime='2026-01-01T00:30:00Z', generation=123)
    with ElexonClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[row]))) as client:
        result = client.actual_wind(date(2026, 1, 1), date(2026, 1, 2))
        assert result[0].startTime == stamp('2026-01-01T00:00:00Z')
        row['fuelType'] = 'GAS'
        with pytest.raises(ElexonError):
            client.actual_wind(date(2026, 1, 1), date(2026, 1, 2))
