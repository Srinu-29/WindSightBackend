import httpx
from fastapi.testclient import TestClient
from app.main import app


def test_health_contract():
    with TestClient(app) as client:
        response = client.get('/api/health')
    assert response.status_code == httpx.codes.OK
    assert response.json() == {'status': 'ok', 'service': 'windsight-backend'}


def test_health_is_documented():
    with TestClient(app) as client:
        schema = client.get('/openapi.json').json()
    assert '/api/health' in schema['paths']
