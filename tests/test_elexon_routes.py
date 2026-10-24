from fastapi.testclient import TestClient

from app.main import app
from app.services.elexon_client import ElexonClient, ElexonError
from app.schemas.elexon import ForecastWindRecord


def test_forecast_inspection_metadata_and_limit(monkeypatch):
    row = ForecastWindRecord(dataset='WINDFOR', publishTime='2026-01-31T22:30:00Z',
                             startTime='2026-02-02T21:00:00Z', generation=123)
    monkeypatch.setattr(ElexonClient, 'forecast_wind', lambda *a, **kw: [row, row])
    with TestClient(app) as client:
        response = client.get('/api/test/elexon/forecast', params={
            'publishDateTimeFrom': '2026-01-01T00:00:00Z',
            'publishDateTimeTo': '2026-02-01T00:00:00Z', 'limit': 1})
    assert response.status_code == 200
    body = response.json()
    assert body['units'] == 'MW' and body['totalRecords'] == 2
    assert body['returnedRecords'] == 1 and body['truncated'] is True
    assert body['records'][0]['startTime'] == '2026-02-02T21:00:00Z'


def test_inspection_rejects_naive_datetime():
    with TestClient(app) as client:
        response = client.get('/api/test/elexon/forecast', params={
            'publishDateTimeFrom': '2026-01-01T00:00:00',
            'publishDateTimeTo': '2026-02-01T00:00:00Z'})
    assert response.status_code == 422


def test_inspection_maps_upstream_failure(monkeypatch):
    def fail(*args):
        raise ElexonError('Elexon request timed out.', status_code=504)
    monkeypatch.setattr(ElexonClient, 'actual_wind', fail)
    with TestClient(app) as client:
        response = client.get('/api/test/elexon/actual', params={
            'settlementDateFrom': '2026-01-01', 'settlementDateTo': '2026-01-02'})
    assert response.status_code == 504


def test_inspection_rejects_reversed_range():
    with TestClient(app) as client:
        response = client.get('/api/test/elexon/actual', params={
            'settlementDateFrom': '2026-01-02', 'settlementDateTo': '2026-01-01'})
    assert response.status_code == 422
