"""Central configuration, read from environment variables (and the .env file)."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Folders
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
SAMPLE_DIR = DATA_DIR / "sample"
MODELS_DIR = PROJECT_ROOT / "models"

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Security
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# Data sources
IDSP_ARCHIVE_URL = os.getenv(
    "IDSP_ARCHIVE_URL", "https://ncdc.mohfw.gov.in/includes/WeeklyOutbreaks.php"
)
OPEN_METEO_ARCHIVE_URL = os.getenv(
    "OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive"
)
OPEN_METEO_FORECAST_URL = os.getenv(
    "OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast"
)

# Web server
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
PORT = int(os.getenv("PORT", "8050"))

# Modelling constants (see documentation, Section 6.7)
PREDICTION_HORIZON_WEEKS = 4
REPORTING_LAG_WEEKS = 8
