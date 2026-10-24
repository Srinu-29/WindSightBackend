# Elexon wind integration

## Verified contract

Official documentation:
- https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream
- https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream
- https://developer.data.elexon.co.uk/
- https://bmrs.elexon.co.uk/wind-generation

Base URL: `https://data.elexon.co.uk/bmrs/api/v1`. Public API; no key.
Both stream endpoints return a **plain JSON array**, not a `data` envelope.

`GET /datasets/FUELHH/stream` accepts `settlementDateFrom`, `settlementDateTo` (yyyy-MM-dd), `settlementPeriod` (array), `fuelType` (array), or publication filters `publishDateTimeFrom`/`publishDateTimeTo`. Settlement and publication filters must not be mixed. The client uses settlement dates and `fuelType=WIND`. Upstream settlement date bounds are inclusive, verified by a January 31–February 1 request returning 96 records. The client converts its exclusive end date to an inclusive upstream date.

FUELHH records contain `dataset`, `startTime`, `publishTime`, `settlementDate`, `settlementPeriod`, `fuelType`, and `generation`. Generation is average **MW over the half hour**, not MWh. Settlement dates are GB market dates; use the returned startTime rather than calculating UTC timestamps from settlement periods. Period validation allows up to 50 for daylight-saving transitions.

`GET /datasets/WINDFOR/stream` accepts `publishDateTimeFrom` and `publishDateTimeTo`; omitting them retrieves the latest publication. The client always supplies both for historical retrieval. Records contain `dataset`, `startTime`, `publishTime`, and `generation` (MW). `startTime` is the forecast delivery time, while `publishTime` identifies its publication. The underlying series is hourly and can include delivery times before and after the publication date. All publication versions are preserved; no latest-only selection or interpolation is applied.

Timestamps observed in live responses use ISO 8601 UTC (`Z`). Pydantic rejects timezone-naive timestamps and normalizes valid offsets to UTC. The client rejects missing or invalid generation values rather than substituting zeros. Empty upstream arrays remain empty.

## Date ranges and limits

All WindSight client and inspection ranges are **[from, to)**: start inclusive, end exclusive. This differs from Elexon's inclusive settlement-date query. Publication responses are explicitly filtered to half-open windows to avoid overlapping boundary records. Only exact duplicates are removed; different publication times and revised values remain distinct.

Elexon's developer portal describes stream endpoints as having no data-return limits, and these two endpoint pages do not specify a maximum date span. The client nevertheless splits requests into seven-day windows to keep downloads bounded. Seven days is an application batching policy, not a claimed Elexon limit. WindSight limits each requested range to 366 days. No non-stream pagination or row cap is assumed.

Timeouts: 10 seconds to connect/acquire a connection; 45 seconds per read/write operation. At most three attempts per chunk for network errors, HTTP 429, and selected transient 5xx errors. Retry-After is honored; waits over 30 seconds return an error instead of retrying early. Invalid JSON, invalid records, and other HTTP errors fail the request without returning partial results.

## Temporary inspection endpoints

Enabled by default for development; set `ENABLE_ELEXON_INSPECTION=false` to remove these routes, then restart the backend. No frontend is required.

Actuals for January and February 1–3:

```text
GET /api/test/elexon/actual?settlementDateFrom=2026-01-01&settlementDateTo=2026-02-04&limit=5
```

Every forecast published in January, including February delivery:

```text
GET /api/test/elexon/forecast?publishDateTimeFrom=2026-01-01T00:00:00Z&publishDateTimeTo=2026-02-01T00:00:00Z&limit=5
```

Optional `deliveryFrom` and `deliveryTo` independently filter forecast delivery timestamps. For February delivery only, add `deliveryFrom=2026-02-01T00:00:00Z`. Do not set deliveryTo to February 1 if you want forecasts extending into February. These delivery filters are applied locally, as WINDFOR does not expose them upstream.

Inspection results include units, source, totalRecords, returnedRecords, truncated, and records. `limit` (1–10,000; default 100) limits the displayed sample only; it does not reduce upstream retrieval. Upstream failures return HTTP 502/503/504; invalid query ranges return 422. Use `/docs` to try the endpoints.

## Live verification — 25 October 2026 (Asia/Calcutta)

Both official endpoints returned HTTP 200 during direct probes. The implemented HTTPX client then successfully retrieved:

| Request | Normalized records | Delivery coverage (UTC) |
| --- | ---: | --- |
| FUELHH WIND settlement dates January 1–February 3 | 1,632 | January 1 00:00–February 3 23:30 |
| WINDFOR publications January 1–31 | 18,104 | December 31 2025 21:00–February 2 2026 21:00 |

The forecast result contained 544 records with February delivery dates. Actuals included 144 February records. These are observed counts, not permanent guarantees of upstream completeness. Forecast versions must be selected by the intended analysis cutoff before comparing them with actuals; no evaluation policy is implemented yet.

All 22 backend tests passed. Both inspection routes were also exercised through FastAPI's test client with real Elexon requests: actuals for January 31–February 1 returned HTTP 200 with 96 records; January 31 forecasts filtered to February delivery returned HTTP 200 with 368 records. The test dependencies emit one deprecation warning about Starlette's HTTPX test-client adapter; no test failed.

Example real actual: startTime `2026-01-01T00:00:00Z`, publishTime `2026-01-01T00:30:00Z`, generation **13,756 MW**.

Repeat the live check from the backend folder:

```powershell
.\.venv\Scripts\python.exe verify_elexon.py
```

Unit tests use explicitly synthetic HTTPX fixtures and never provide fallback production data. Live retrieval uses only Elexon responses. Contains BMRS data © Elexon Limited copyright and database right 2026; consult Elexon's current data licence for reuse.
