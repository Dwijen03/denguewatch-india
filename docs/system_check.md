# DengueWatch India - system check

Run 2026-11-12 22:55 on Windows 10, Python 3.11.18, DengueWatch 1.0.0.

**50 of 51 checks passed.**


## Environment

| Check | Result | Detail |
|---|---|---|
| Python version | PASS | 3.11.18 |
| All required packages installed | PASS | 20 pinned in requirements-lock.txt |

## Database

| Check | Result | Detail |
|---|---|---|
| All tables present | PASS | 12 tables |
| states has rows | PASS | 36 |
| districts has rows | PASS | 640 |
| reports has rows | PASS | 891 |
| outbreaks has rows | PASS | 2,224 |
| weather_daily has rows | PASS | 4,185,600 |
| predictions has rows | PASS | 7,680 |
| model_versions has rows | PASS | 1 |
| Weather is recent (observed within 7 days) | PASS | observed to 2026-11-11, forecast to 2026-11-27 |
| Latest IDSP report loaded | PASS | week ending 2026-09-13 |

## Data quality

| Check | Result | Detail |
|---|---|---|
| No negative cases or deaths | PASS | 0 violations |
| Deaths never exceed cases | PASS | 0 violations |
| No duplicate outbreaks | PASS | 0 violations |
| Every report week has 7 days | PASS | 0 violations |
| Weather values in physical range | PASS | 0 violations |
| Risk scores between 0 and 1 | PASS | 0 violations |
| Start date not after reporting date (source typos <= 0.5%) | PASS | 5 rows (0.22%) have a reporting date before the start date in the PDF |
| Outbreaks matched to a district | PASS | 98.6% (2,192 of 2,224); 64 flagged for review |

## REST API

| Check | Result | Detail |
|---|---|---|
| GET /api/v1/ | PASS | 200, 0.31 s |
| GET /api/v1/health | PASS | 200, 0.01 s |
| GET /api/v1/states | PASS | 200, 0.01 s |
| GET /api/v1/districts | PASS | 200, 0.01 s |
| GET /api/v1/districts/595 | PASS | 200, 0.02 s |
| GET /api/v1/districts/595/timeline | PASS | 200, 0.02 s |
| GET /api/v1/outbreaks?limit=500 | PASS | 200, 0.02 s |
| GET /api/v1/outbreaks/summary?by=state | PASS | 200, 0.00 s |
| GET /api/v1/weather/595 | PASS | 200, 0.01 s |
| GET /api/v1/risk/current | PASS | 200, 0.04 s |
| GET /api/v1/risk/595 | PASS | 200, 0.01 s |
| GET /api/v1/model | PASS | 200, 0.00 s |
| GET /api/v1/reports | PASS | 200, 0.01 s |
| GET /api/v1/pipeline-runs | PASS | 200, 0.00 s |
| GET /api/v1/signals | PASS | 200, 0.00 s |
| GET /api/v1/schedule | PASS | 200, 0.00 s |
| GET /api/v1/export/outbreaks.csv | PASS | 200, 0.02 s |
| Admin endpoint refuses requests without token (NFR-5) | PASS | 401 |
| SQL injection attempt is harmless (NFR-5) | PASS | 200, 0 rows |

## Dashboard

| Check | Result | Detail |
|---|---|---|
| GET /dashboard/ | PASS | 200, 0.01 s |
| GET /dashboard/_dash-layout | PASS | 200, 0.11 s |
| All 5 pages present | PASS | Risk this week, District explorer, Compare districts, Model & data, Ask AI |

## Model

| Check | Result | Detail |
|---|---|---|
| Active model beats chance on unseen years | PASS | PR AUC 0.176 vs chance 0.017 (10.4x) |

## Automation

| Check | Result | Detail |
|---|---|---|
| daily_update ran recently | PASS | last success 2026-11-12T11:43+05:30 |
| weekly_reports ran recently | PASS | last success 2026-11-12T11:29+05:30 |
| monthly_retrain ran recently | PASS | last success 2026-11-12T12:15+05:30 |
| news_signals ran recently | **FAIL** | last success None |

## AI agents

| Check | Result | Detail |
|---|---|---|
| AI key configured | PASS | Gemini, Groq |
| Questions answered and logged | PASS | answered 39, failed 11, refused 12 |
| News agent has read articles | PASS | 4 relevant of 59 articles |

## Tests

| Check | Result | Detail |
|---|---|---|
| All automated tests pass | PASS | 134 passed, 15 warnings in 9.91s |
