"""Checks the browser-access (CORS) rules configured on the FastAPI app."""

from fastapi.middleware.cors import CORSMiddleware

from app.main import app


def cors_options() -> dict:
    """Return the settings passed to FastAPI's CORS middleware."""
    middleware = next(
        item for item in app.user_middleware if item.cls is CORSMiddleware
    )
    return middleware.kwargs


def test_local_frontends_are_allowed():
    options = cors_options()

    assert 'http://localhost:5173' in options['allow_origins']
    assert 'http://localhost:4173' in options['allow_origins']
    assert 'http://localhost:8080' in options['allow_origins']


def test_cors_is_limited_to_read_only_requests_without_cookies():
    options = cors_options()

    assert options['allow_methods'] == ['GET']
    assert options['allow_credentials'] is False
    assert '*' not in options['allow_origins']
