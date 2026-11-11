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
| 4 | Data acquisition & cleaning | Next |
| 5 | Database implementation | Planned |
| 6 | Weather data integration | Planned |
| 7 | AI/ML early-warning model | Planned |
| 8 | Flask REST API | Planned |
| 9 | Dashboard | Planned |
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

## Data sources

- **IDSP Weekly Outbreak Reports**, National Centre for Disease Control (NCDC): <https://ncdc.mohfw.gov.in/includes/WeeklyOutbreaks.php>
- **Open-Meteo** historical and forecast weather API (free for non-commercial use): <https://open-meteo.com/>

---

## Disclaimer

This is an academic prototype. Risk scores are decision support only and do not replace field surveillance or official public health alerts.
