# DengueWatch India

**A weather-driven dengue early warning system for Indian districts.**

India publishes dengue outbreak information every week through the Integrated Disease Surveillance Programme (IDSP), but only as PDF reports that appear about two months after the week they describe. DengueWatch India turns these reports into a queryable PostgreSQL database, links every district to its historical and forecast weather, and uses machine learning to estimate which districts are likely to report a dengue outbreak in the next four weeks. Results are served through a Flask REST API and an interactive Dash dashboard that update automatically.

> Minor Project: Python + Data Management & Engineering, Mahindra University
> Author: Dwijen Mehta (SE26MBDS001)

---

## Project status

| Step | Phase | Status |
|---|---|---|
| 0 | Topic selection & feasibility | Done |
| 1 | Problem definition | Done |
| 2 | Requirements & system design | Done |
| 3 | Project setup | Done |
| 4 | Data acquisition & cleaning | Done |
| 5 | Database implementation | Done |
| 6 | Weather data integration | Done |
| 7 | AI/ML early-warning model | Done |
| 8 | Flask REST API | Done |
| 9 | Dashboard | Next |
| 10 | Automation (scheduled updates) | Planned |
| 11 | AI agent (optional) | Planned |
| 12 | Testing, evaluation & final documentation | Planned |
| 13 | Presentation & demo | Planned |

---

## Architecture

![System architecture](docs/diagrams/architecture.png)

| Layer | What it does |
|---|---|
| Data sources | IDSP weekly outbreak PDFs (NCDC), Open-Meteo weather API, district master list |
| Ingestion (Python) | Downloads and parses reports, cleans data, matches district names, fetches weather |
| Storage | PostgreSQL: outbreaks, reports, districts, weather, model versions, predictions |
| ML (Python) | Lagged-weather features, LightGBM model, time-based validation, 4-week risk scores |
| Application | One Flask server: REST API at `/api/v1`, Dash dashboard at `/dashboard` |
| Automation | APScheduler: weekly report ingestion, daily weather + risk refresh |

The database design is in [`docs/diagrams/er_diagram.png`](docs/diagrams/er_diagram.png).

---

## Tech stack

Python 3.11+ · PostgreSQL · SQLAlchemy · Flask · Dash / Plotly · pandas · pdfplumber · RapidFuzz · scikit-learn · LightGBM · APScheduler · pytest

---

## Repository structure

```
denguewatch-india/
├── denguewatch/            # Main Python package
│   ├── config.py           # Settings read from .env
│   ├── app.py              # Flask app factory (API + dashboard)
│   ├── ingestion/          # Report download, PDF parsing, cleaning, weather fetch
│   ├── db/                 # Database connection and queries
│   ├── ml/                 # Features, training, validation, risk scoring
│   ├── api/                # REST API endpoints
│   ├── dashboard/          # Dash pages
│   └── scheduler/          # Automated jobs
├── database/               # SQL schema and query scripts
├── data/
│   ├── reference/          # Census 2011 district list + name aliases
│   ├── raw/                # Downloaded PDFs (not committed)
│   ├── processed/          # Intermediate files (not committed)
│   └── sample/             # Small sample data (committed)
├── models/                 # Trained model files (not committed)
├── notebooks/              # Exploration and analysis notebooks
├── scripts/                # Utility scripts, e.g. check_setup.py
├── tests/                  # pytest tests
├── docs/diagrams/          # Architecture and ER diagrams
├── run.py                  # Starts the web app
├── requirements.txt
└── .env.example            # Template for your local settings
```

---

## Setup (Windows)

### Quick setup (recommended)

1. Install PostgreSQL and either Anaconda or Python 3.11+ (see prerequisites below).
2. Double-click **`setup_windows.bat`**. It finds Python/conda and PostgreSQL, creates the environment, installs packages, creates the database and `.env`, and runs the setup check.
3. Double-click **`start_app.bat`** to start the app; your browser opens <http://localhost:8050>.

If the database password ever gets out of sync, run **`scripts\fix_database.bat`**.

### Manual setup

#### 1. Prerequisites

- **Python 3.11 or newer.** Anaconda/Miniconda is fine.
- **PostgreSQL 16 or newer** (developed on PostgreSQL 18). Download the Windows installer from <https://www.postgresql.org/download/windows/>. During installation, keep port `5432` and remember the password you set for the `postgres` user.
- **Git.** Download from <https://git-scm.com/download/win>.

#### 2. Get the code

```bash
git clone https://github.com/Dwijen03/denguewatch-india.git
cd denguewatch-india
```

#### 3. Create a Python environment and install packages

With Anaconda (Anaconda Prompt or VS Code terminal):

```bash
conda create -n denguewatch python=3.11 -y
conda activate denguewatch
pip install -r requirements.txt
```

#### 4. Create the database

Open **SQL Shell (psql)** from the Start menu, log in as `postgres`, and run (choose your own password):

```sql
CREATE USER denguewatch WITH PASSWORD 'your_password_here';
CREATE DATABASE denguewatch OWNER denguewatch;
```

#### 5. Configure

Copy `.env.example` to `.env` and put your password into `DATABASE_URL`:

```
DATABASE_URL=postgresql+psycopg://denguewatch:your_password_here@localhost:5432/denguewatch
```

#### 6. Check everything works

```bash
python scripts/check_setup.py
pytest
python run.py
```

Then open <http://localhost:8050>. The health check is at <http://localhost:8050/api/v1/health>.

---

## Data pipeline (Step 4)

```bash
python scripts/run_ingestion.py            # or double-click run_data_pipeline.bat
python scripts/run_ingestion.py --no-download   # re-parse PDFs already downloaded
```

1. **Download**: finds every weekly report on the NCDC Weekly Outbreaks page and downloads new ones to `data/raw/`.
2. **Parse**: reads the outbreak tables in each PDF with pdfplumber and keeps dengue rows. It handles the three layouts used since 2009, merged state/district cells, and `cases / deaths` written in one cell.
3. **Clean**: standardises states, dates (`13.08.10`, `14-07- 2026`, ...), numbers and status, and flags suspected or mixed outbreaks (e.g. "Dengue & Chikungunya").
4. **Match districts** to the 640 Census 2011 districts (`data/reference/districts.csv`), in this order: exact name, then curated aliases for renamed or newer districts (`data/reference/district_aliases.csv`, 317 entries), then fuzzy match, then the IDSP unique ID.
5. **De-duplicate**: an outbreak listed again in later weeks is kept once (district + start date).
6. **Validate**: rows with missing cases, invalid dates or unmatched districts get `needs_review = True` and a `review_reasons` explanation.

**Result (run of 11 Nov 2026):** 891 reports parsed (2009 to week 37 of 2026), giving **2,224 dengue outbreaks**, about **1.2 lakh cases**, across **353 districts**. 98.6% of outbreaks were matched to a district, 9 are state-wide, and 64 rows (2.9%) are flagged for review. The full table is in [`data/sample/dengue_outbreaks.csv`](data/sample/dengue_outbreaks.csv).

| Output (data/processed/) | Contents |
|---|---|
| `reports_index.csv` | Every report: year, week, URL, file fingerprint, dengue rows found |
| `dengue_rows_raw.csv` | Rows exactly as read from the PDFs |
| `dengue_rows_clean.csv` | Cleaned and matched rows with flags |
| `dengue_outbreaks.csv` | **One row per outbreak** (main output) |
| `unmatched_districts.csv` | District names still needing an alias |

---

## Database (Step 5)

```bash
python scripts/setup_database.py           # or double-click setup_database.bat
python scripts/setup_database.py --reset   # drop and rebuild all tables
```

- **`database/schema.sql`** defines 11 tables with primary and foreign keys, UNIQUE and CHECK rules, 6 indexes and 3 views (`v_outbreaks`, `v_district_year`, `v_weather_weekly`). The design is in [`docs/diagrams/er_diagram.png`](docs/diagrams/er_diagram.png).
- **`database/queries.sql`** holds 10 named analytical queries, using window functions (LAG, RANK), FILTER, percentiles and date-range joins. Run them from Python:

  ```python
  from denguewatch.db.queries import run_query
  run_query("monthly_seasonality", state="Kerala")
  run_query("outbreaks_by_district_year", district="Pune")
  ```

- **Loading is re-runnable.** Upserts on natural keys mean new reports are added without creating duplicates, and every load is logged in `pipeline_runs`.
- **Current contents:** 36 states, 640 districts, 317 aliases, 891 reports, 2,224 outbreaks.

---

## Weather (Step 6)

```bash
python scripts/update_weather.py --check      # test that both weather services answer
python scripts/update_weather.py --limit 5    # trial run on 5 districts
python scripts/update_weather.py              # full run (or double-click update_weather.bat)
python scripts/update_weather.py --no-history # quick daily refresh: recent days + forecast
```

| Source | Used for | Why |
|---|---|---|
| [NASA POWER](https://power.larc.nasa.gov) | Daily history 2009 to a few days ago, for each of the 640 district centroids | One request returns the whole history for a point, about 640 requests in total |
| [Open-Meteo](https://open-meteo.com) | Last 10 days and the 16-day forecast | Many districts per request. Its free limits (10,000 calls a day, 2 weeks of data = 1 call) rule it out for the full history |

**Variables:** precipitation (mm), mean, minimum and maximum temperature (°C), and relative humidity (%), stored daily in `weather_daily`.

**How the data is kept consistent:**

- Each day keeps its most reliable value, in this order: NASA POWER, then Open-Meteo observed, then forecast.
- The job can be resumed: it only downloads days that are still missing.
- The first full run takes about 30–60 minutes and stores about 4.2 million rows (≈0.5 GB). After that, each run takes about a minute.
- `v_weather_weekly` gives weekly totals, and the query `rainfall_before_outbreaks` links weather to outbreaks.

---

## Early-warning model (Step 7)

```bash
python scripts/train_model.py                  # or double-click train_model.bat
python scripts/train_model.py --predict-only   # refresh risk scores with the active model
```

**Question the model answers:** will this district report a dengue outbreak in the next 4 weeks?

**Inputs:** 23 features per district-week:

- weather over the last 1–16 weeks: rainfall, minimum, mean and maximum temperature, humidity
- season
- location
- outbreak history, counting only outbreaks that would already have been published, since reports appear about 8 weeks late

**Model:** LightGBM, tested on years it never saw (2023 – Jul 2026):

| Model | PR AUC | vs chance |
|---|---|---|
| **DengueWatch (LightGBM, all features)** | **0.176** | **10.4×** |
| Logistic regression | 0.165 | 9.8× |
| LightGBM without weather | 0.138 | 8.2× |
| Seasonal history rule | 0.118 | 7.0× |
| LightGBM, weather only | 0.053 | 3.1× |

**Alert performance:**

- **High alerts** cover 6% of district-weeks and catch 44.9% of outbreaks (7.4× chance).
- **Medium + high alerts** catch 71.7% of outbreaks.
- **Lead time:** 78% of outbreaks were flagged at least 2 weeks before they started.

**What matters most:** the minimum temperature over the last 4 weeks is the strongest signal. Weather carries 56% of the model's influence. Each stored prediction includes its top 3 reasons (SHAP).

Charts are in [`docs/figures/`](docs/figures/). Trained models are saved to `models/` and registered in `model_versions`, and scores are written to `predictions`.

---

## REST API (Step 8)

Start the server with `python run.py` (or `start_app.bat`), then open <http://localhost:8050/api/v1/>. There are 17 endpoints under `/api/v1`. The full reference with examples is in **[docs/API.md](docs/API.md)**.

| Example | What you get |
|---|---|
| [`/api/v1/health`](http://localhost:8050/api/v1/health) | Database status and the latest report and weather dates |
| [`/api/v1/risk/current?level=high`](http://localhost:8050/api/v1/risk/current?level=high) | High-risk districts for the next 4 weeks, with reasons |
| [`/api/v1/districts/595`](http://localhost:8050/api/v1/districts/595) | Ernakulam: outbreak history and current risk |
| [`/api/v1/outbreaks/summary?group_by=month&state=Kerala`](http://localhost:8050/api/v1/outbreaks/summary?group_by=month&state=Kerala) | Kerala outbreaks by month |
| [`/api/v1/districts/521/timeline`](http://localhost:8050/api/v1/districts/521/timeline) | Pune: weekly outbreaks and weather |
| [`/api/v1/export/outbreaks.csv?state=Kerala`](http://localhost:8050/api/v1/export/outbreaks.csv?state=Kerala) | CSV download |

**Behaviour:** all parameters are validated, errors come back as JSON, every query is parameterised, and each response arrives in under 0.1 s.

---

## Data sources

- **IDSP Weekly Outbreak Reports**, National Centre for Disease Control (NCDC): <https://ncdc.mohfw.gov.in/includes/WeeklyOutbreaks.php>
- **NASA POWER** daily point API (NASA Langley Research Center), weather history: <https://power.larc.nasa.gov/>
- **Open-Meteo** forecast API (free for non-commercial use, CC BY 4.0): <https://open-meteo.com/>
- **District boundaries**: DataMeet, *India - District Map* (Census 2011), CC BY 2.5 India: <https://github.com/datameet/maps>. Used to build `data/reference/districts.csv` with district centroids.

---

## Disclaimer

This is an academic prototype. Risk scores are decision support only and do not replace field surveillance or official public health alerts.
