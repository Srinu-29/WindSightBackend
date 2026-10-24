# WindSightBackend

Initial FastAPI backend for WindSight, a UK electricity purchasing workspace. No authentication or database.

The backend now includes a validated HTTPX integration for Elexon actual wind generation and historical wind forecasts. See [Elexon integration and verified live results](docs/elexon.md) for query parameters, units, date semantics, retry behavior, and temporary inspection endpoints.

## Local development

Requires Python 3.11+. Run from this repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

For macOS/Linux use `.venv/bin/python` and `cp`.

`GET /api/health` returns `{"status":"ok","service":"windsight-backend"}`. Interactive API documentation is available at `/docs`.

`app/api` contains routes, `app/schemas` contains Pydantic models, and `app/services` is reserved for business logic and future HTTPX integrations. Configuration is documented in `.env.example`; defaults work without an `.env` file.

The sibling Vite frontend proxies `/api` to this server, so browser requests stay on the frontend origin. Use a reverse proxy for separately hosted builds or configure explicit CORS origins when needed.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Docker

```powershell
docker build -t windsight-backend .
docker run --rm -p 8000:8000 windsight-backend
```
