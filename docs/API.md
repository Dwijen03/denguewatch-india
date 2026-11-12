# DengueWatch India: REST API

Base URL when running locally: `http://localhost:8050/api/v1`. Start the server with `python run.py` or `start_app.bat`.

## Conventions

- **District IDs.** Districts are identified by their **Census 2011 code**, for example Ernakulam = `595` and Pune = `521`. `GET /districts` lists every code.
- **Formats.** Responses are JSON, except the CSV export. Dates are `YYYY-MM-DD`.
- **Lists and paging.** List endpoints accept `limit` (default 500, maximum 5000) and `offset`. They return `count`, `total` (where useful) and `results`.
- **Errors.** Errors are JSON, for example `{"error": {"status": 400, "message": "'limit' must be a whole number"}}`. The status codes are 400 (bad parameter), 401 (admin token missing or wrong), 404 (not found) and 500.
- **Security.** All endpoints are read-only except `POST /admin/refresh`. Every query is parameterised, so URL values are never inserted into SQL text.

## Endpoints

| Method | Path | Purpose | Main parameters |
|---|---|---|---|
| GET | `/` | Self-documenting list of endpoints | – |
| GET | `/health` | Database status and data freshness | – |
| GET | `/states` | States/UTs with district and outbreak counts | – |
| GET | `/districts` | District list with coordinates | `state` |
| GET | `/districts/{code}` | One district: outbreak summary and current risk | – |
| GET | `/districts/{code}/timeline` | Weekly outbreaks, cases and weather | `from`, `to` (default: last 2 years) |
| GET | `/outbreaks` | Outbreak records | `state`, `district_code`, `year`, `from`, `to`, `limit`, `offset` |
| GET | `/outbreaks/summary` | Totals grouped by period or place | `group_by` = year, month, state or district; `state`, `year` |
| GET | `/weather/{code}` | Observed and forecast weather | `granularity` = daily or weekly; `from`, `to` |
| GET | `/risk/current` | Latest 4-week risk for every district | `state`, `level` = low, medium or high |
| GET | `/risk/{code}` | Risk history and explanation for one district | – |
| GET | `/model` | Active model and its evaluation on unseen years | – |
| GET | `/reports` | IDSP weekly reports loaded | `year`, `limit`, `offset` |
| GET | `/pipeline-runs` | Recent automated jobs and their status | `limit` |
| GET | `/signals` | Unverified news signals from the optional agent (Step 11) | `limit`, `offset` |
| GET | `/export/outbreaks.csv` | CSV download of outbreaks | `state`, `district_code`, `year` |
| POST | `/admin/refresh` | Start a background job | `job` = predict, weather or weather_recent; header `X-Admin-Token` |

## Examples

**High-risk districts in Kerala**, from `GET /risk/current?state=Kerala&level=high` (first result shown):

```json
{
  "forecast_window": {"from": "2026-11-09", "to": "2026-12-06"},
  "levels": {"high": 7},
  "count": 7,
  "results": [{
    "state_name": "Kerala", "district_name": "Kozhikode", "census_code": 591,
    "latitude": 11.4848, "longitude": 75.8344,
    "target_week_start": "2026-11-09", "target_week_end": "2026-12-06",
    "risk_score": 0.6714, "risk_level": "high",
    "top_factors": [
      {"label": "District outbreaks in the past year", "value": 7.0, "effect": 1.572, "feature": "hist_district_1y"},
      {"label": "Years with past outbreaks in district", "value": 10.0, "effect": 0.676, "feature": "hist_district_years"},
      {"label": "Location (latitude)", "value": 11.48, "effect": 0.437, "feature": "latitude"}
    ]
  }]
}
```

**One district**, from `GET /districts/595` (Ernakulam):

```json
{
  "census_code": 595, "district_name": "Ernakulam", "state_name": "Kerala",
  "latitude": 10.0577, "longitude": 76.4727,
  "summary": {"outbreaks": 79, "cases": 2408, "deaths": 6, "years_with_outbreaks": 13,
              "first_outbreak": "2010-03-14", "last_outbreak": "2026-09-11"},
  "current_risk": {"target_week_start": "2026-11-09", "risk_score": 0.633, "risk_level": "high", "top_factors": ["..."]}
}
```

**Outbreak records**, from `GET /outbreaks?district_code=595&limit=1`:

```json
{"count": 1, "total": 79, "limit": 1, "offset": 0,
 "results": [{"outbreak_id": 2220, "state_name": "Kerala", "district_name": "Ernakulam", "census_code": 595,
              "report_year": 2026, "report_week": 37, "start_date": "2026-09-11", "cases": 11, "deaths": 0,
              "current_status": "Under Surveillance", "idsp_unique_id": "KL/ERN/2026/37/1507"}]}
```

**Refresh risk scores.** Use the token from `.env`:

```bash
curl -X POST -H "X-Admin-Token: <ADMIN_TOKEN>" "http://localhost:8050/api/v1/admin/refresh?job=predict"
# -> 202 {"started": "predict", "check_progress": "/api/v1/pipeline-runs"}
```

## Performance

Measured on the full dataset: 2,224 outbreaks, 4.2 million daily weather rows and 640 × 12 predictions.

- Every endpoint answered in **under 0.1 s**.
- `/health` is the slowest at about 0.09 s.
- `/risk/current` for all 640 districts takes about 0.02 s.
