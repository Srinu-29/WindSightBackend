# TestClient sends requests through FastAPI inside the test process. No running
# Uvicorn server is required. pytest discovers these test_ functions automatically.
import httpx
from fastapi.testclient import TestClient
from app.main import app


def test_health_contract():
    # Verify both success status and the exact JSON expected by the frontend.
    with TestClient(app) as client:
        response = client.get('/api/health')
    assert response.status_code == httpx.codes.OK
    assert response.json() == {'status': 'ok', 'service': 'windsight-backend'}


def test_health_is_documented():
    # /openapi.json is FastAPI's machine-readable API description, used by /docs.
    with TestClient(app) as client:
        schema = client.get('/openapi.json').json()
    assert '/api/health' in schema['paths']
